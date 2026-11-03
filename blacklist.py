"""Lista de bloqueio: erros que são falso alarme.

Diferente de "Ignorar" (que só esconde a issue dos gráficos), um erro bloqueado some de verdade:
as ocorrências já guardadas são apagadas e as próximas são descartadas na coleta — não aparecem,
não contam como erro e não disparam alarme, até a entrada ser removida da lista.

Dois tipos de entrada:
  issue  bloqueia uma issue exata (pelo fingerprint)
  text   bloqueia qualquer erro cuja mensagem contenha o texto (pega variações do mesmo falso alarme),
         opcionalmente só em serviços cujo nome contenha `service`
"""
import db

MIN_TEXT = 3
MIN_NOTE = 10
SAMPLE_CHARS = 2000


def _check_note(note: str) -> str:
    """A justificativa é obrigatória: quem olhar a lista depois precisa saber por que o erro foi bloqueado."""
    note = (note or "").strip()
    if len(note) < MIN_NOTE:
        raise ValueError(f"Escreva a justificativa do bloqueio (mínimo {MIN_NOTE} caracteres)")
    return note


def list_entries(conn) -> list[dict]:
    return db.rows(conn.execute("SELECT * FROM blacklist ORDER BY created_at DESC"))


def add_issue(conn, fingerprint: str, note: str) -> dict:
    note = _check_note(note)
    issue = conn.execute("SELECT title, service FROM issues WHERE fingerprint = ?", (fingerprint,)).fetchone()
    if issue is None:
        raise ValueError("Issue não encontrada")
    if conn.execute("SELECT 1 FROM blacklist WHERE kind = 'issue' AND value = ?", (fingerprint,)).fetchone():
        raise ValueError("Essa issue já está na lista de bloqueio")
    sample = conn.execute("SELECT message FROM events WHERE fingerprint = ? ORDER BY ts DESC LIMIT 1",
                          (fingerprint,)).fetchone()
    entry_id = _insert(conn, "issue", fingerprint, "", issue["title"], sample[0] if sample else "", note)
    removed = _drop(conn, [r[0] for r in conn.execute("SELECT id FROM events WHERE fingerprint = ?", (fingerprint,))])
    conn.execute("DELETE FROM issues WHERE fingerprint = ?", (fingerprint,))
    _ack_alerts(conn, [fingerprint])
    conn.commit()
    return {"id": entry_id, "removed": removed}


def add_text(conn, text: str, service: str, note: str) -> dict:
    note = _check_note(note)
    text, service = (text or "").strip(), (service or "").strip()
    if len(text) < MIN_TEXT:
        raise ValueError(f"Informe o texto a bloquear (mínimo {MIN_TEXT} caracteres)")
    if conn.execute("SELECT 1 FROM blacklist WHERE kind = 'text' AND value = ? AND service = ?",
                    (text, service)).fetchone():
        raise ValueError("Esse texto já está na lista de bloqueio")
    m = Matcher.single("text", text, service)
    ids, sample = [], ""
    for e in conn.execute("SELECT id, fingerprint, service, message FROM events ORDER BY ts DESC"):
        if m.match_text(e["service"], e["message"]):
            ids.append(e["id"])
            sample = sample or e["message"]
    entry_id = _insert(conn, "text", text, service, text, sample, note)
    removed = _drop(conn, ids)
    conn.commit()
    return {"id": entry_id, "removed": removed}


def remove(conn, entry_id: int) -> None:
    conn.execute("DELETE FROM blacklist WHERE id = ?", (entry_id,))
    conn.commit()


def _insert(conn, kind, value, service, label, sample, note) -> int:
    return conn.execute(
        "INSERT INTO blacklist(kind, value, service, label, sample, note, created_at) VALUES (?,?,?,?,?,?,?)",
        (kind, value, service, label[:300], (sample or "")[:SAMPLE_CHARS], note[:500],
         db.now_ms())).lastrowid


def _drop(conn, event_ids: list[int]) -> int:
    """Apaga as ocorrências e acerta as issues afetadas (some a issue que ficar sem nenhuma)."""
    if not event_ids:
        return 0
    removed: dict[str, int] = {}
    for i in range(0, len(event_ids), 500):
        chunk = event_ids[i:i + 500]
        marks = ",".join("?" * len(chunk))
        for fp_, n in conn.execute(f"SELECT fingerprint, COUNT(*) FROM events WHERE id IN ({marks}) "
                                   "GROUP BY fingerprint", chunk):
            removed[fp_] = removed.get(fp_, 0) + n
        conn.execute(f"DELETE FROM events WHERE id IN ({marks})", chunk)
    gone = []
    for fp_, n in removed.items():
        left = conn.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM events WHERE fingerprint = ?", (fp_,)).fetchone()
        if left[0] == 0:
            conn.execute("DELETE FROM issues WHERE fingerprint = ?", (fp_,))
            gone.append(fp_)
        else:
            conn.execute("UPDATE issues SET count = MAX(count - ?, ?), first_seen = ?, last_seen = ? "
                         "WHERE fingerprint = ?", (n, left[0], left[1], left[2], fp_))
    _ack_alerts(conn, gone)
    return sum(removed.values())


def _ack_alerts(conn, fingerprints: list[str]) -> None:
    """Avisos na tela de uma issue bloqueada deixam de fazer sentido: marca como reconhecidos."""
    for f in fingerprints:
        conn.execute("UPDATE alert_events SET acknowledged_at = ? WHERE fingerprint = ? AND acknowledged_at IS NULL",
                     (db.now_ms(), f))


class Matcher:
    """Usado pela coleta: decide se um erro novo está bloqueado e conta quantos foram descartados."""

    def __init__(self, entries: list[dict]):
        self.issues = {e["value"]: e for e in entries if e["kind"] == "issue"}
        self.texts = [(e, e["value"].casefold(), e["service"].casefold()) for e in entries if e["kind"] == "text"]
        self.hits: dict[int, list[int]] = {}

    @classmethod
    def load(cls, conn) -> "Matcher":
        return cls(list_entries(conn))

    @classmethod
    def single(cls, kind: str, value: str, service: str = "") -> "Matcher":
        return cls([{"id": 0, "kind": kind, "value": value, "service": service, "last_hit_ts": None}])

    def match_text(self, service: str, message: str):
        low, svc = message.casefold(), service.casefold()
        for entry, text, only in self.texts:
            if text in low and (not only or only in svc):
                return entry
        return None

    def blocked(self, fingerprint: str, service: str, message: str, ts: int) -> bool:
        entry = self.issues.get(fingerprint) or self.match_text(service, message)
        if entry is None:
            return False
        # a coleta relê um pedaço da janela anterior: só conta o que é mais novo que o último descartado
        if ts > (entry["last_hit_ts"] or 0):
            hit = self.hits.setdefault(entry["id"], [0, 0])
            hit[0] += 1
            hit[1] = max(hit[1], ts)
        return True

    def save(self, conn) -> None:
        for entry_id, (n, ts) in self.hits.items():
            conn.execute("UPDATE blacklist SET hits = hits + ?, last_hit_ts = MAX(COALESCE(last_hit_ts, 0), ?) "
                         "WHERE id = ?", (n, ts, entry_id))
        self.hits.clear()
