"""Alarmes na tela do monitor: regras configuráveis avaliadas localmente a cada poucos segundos.

Nada sai da máquina: o disparo vira um registro em alert_events, que o painel mostra como um aviso
no meio da tela (e, se o usuário quiser, notificação do sistema e som).
"""
import json
import logging
import re
import threading
import time

import db
import fingerprint as fp

log = logging.getLogger("alerts")

EVAL_INTERVAL_S = 30
# Desde quando o monitor está de pé e acordado. "Coleta parada" só conta a partir daqui: logo depois de abrir
# o monitor, ou de o computador voltar da suspensão, a última coleta é antiga sem que nada esteja errado.
_awake_since = int(time.time() * 1000)

KINDS = {
    "new_issue": "Issue nova",
    "regression": "Regressão (erro resolvido voltou)",
    "spike": "Pico de erros",
    "anomaly": "Anomalia (acima da média)",
    "match": "Erro com texto específico",
    "cw_alarm": "Alarme do CloudWatch disparou",
    "silence": "Silêncio (log group sem logs)",
    "stalled": "Coleta parada",
}

# Regras sugeridas na 1ª execução. As três primeiras já vêm ligadas (100% locais).
PRESETS = [
    ("Issue nova", "new_issue", 1, "warning", {}, 0),
    ("Regressão: erro resolvido voltou", "regression", 1, "critical", {}, 0),
    ("Alarme do CloudWatch disparou", "cw_alarm", 1, "critical", {"alarm": ""}, 0),
    ("Pico: 20 erros em 10 min num serviço", "spike", 0, "critical", {"threshold": 20, "window_min": 10}, 30),
    ("Anomalia: 3× a média das últimas 24h", "anomaly",
     0, "warning", {"factor": 3, "window_min": 15, "min_count": 10}, 60),
    ("Erro contendo um texto", "match", 0, "critical", {"text": "payment", "regex": False}, 15),
    ("Silêncio: log group sem logs há 60 min", "silence", 0, "warning", {"group": "", "minutes": 60}, 120),
    ("Coleta parada há 20 min", "stalled", 0, "critical", {"minutes": 20}, 60),
]


def ensure_presets(conn) -> None:
    if conn.execute("SELECT COUNT(*) FROM alert_rules").fetchone()[0]:
        return
    now = db.now_ms()
    for name, kind, enabled, severity, params, cooldown in PRESETS:
        conn.execute("INSERT INTO alert_rules(name, kind, enabled, severity, params, cooldown_min, created_at) "
                     "VALUES (?,?,?,?,?,?,?)", (name, kind, enabled, severity, json.dumps(params), cooldown, now))
    conn.commit()


def list_rules(conn) -> list[dict]:
    out = []
    for r in db.rows(conn.execute("SELECT * FROM alert_rules ORDER BY id")):
        r["params"] = json.loads(r["params"] or "{}")
        r["kind_label"] = KINDS.get(r["kind"], r["kind"])
        last = conn.execute("SELECT MAX(ts) FROM alert_events WHERE rule_id = ?", (r["id"],)).fetchone()[0]
        r["last_fired"] = last
        out.append(r)
    return out


def _clean_params(kind: str, params: dict) -> dict:
    def num(key, default, lo, hi, cast=float):
        try:
            value = cast(params.get(key, default))
        except (TypeError, ValueError):
            raise ValueError(f"'{key}' precisa ser um número")
        if not lo <= value <= hi:
            raise ValueError(f"'{key}' deve ficar entre {lo} e {hi}")
        return value

    out = {"service": str(params.get("service") or "").strip(), "category": str(params.get("category") or "").strip()}
    if kind == "spike":
        out.update(threshold=num("threshold", 20, 1, 1_000_000, int), window_min=num("window_min", 10, 1, 1440, int))
    elif kind == "anomaly":
        out.update(factor=num("factor", 3, 1.1, 1000), window_min=num("window_min", 15, 5, 1440, int),
                   min_count=num("min_count", 10, 1, 1_000_000, int))
    elif kind == "match":
        text = str(params.get("text") or "").strip()
        if len(text) < 2:
            raise ValueError("Informe o texto a procurar (mínimo 2 caracteres)")
        regex = bool(params.get("regex"))
        if regex:
            try:
                re.compile(text)
            except re.error as exc:
                raise ValueError(f"Regex inválida: {exc}")
        out.update(text=text, regex=regex)
    elif kind == "cw_alarm":
        out["alarm"] = str(params.get("alarm") or "").strip()
        out["include_autoscaling"] = bool(params.get("include_autoscaling"))
    elif kind == "silence":
        group = str(params.get("group") or "").strip()
        if not group:
            raise ValueError("Informe o log group a vigiar (nome exato, ex.: /aws/lambda/minha-funcao)")
        out.update(group=group, minutes=num("minutes", 60, 5, 10_080, int))
    elif kind == "stalled":
        out["minutes"] = num("minutes", 20, 5, 1440, int)
    return out


def save_rule(conn, body: dict) -> dict:
    kind = body.get("kind")
    if kind not in KINDS:
        raise ValueError("Tipo de alarme inválido")
    name = str(body.get("name") or "").strip() or KINDS[kind]
    severity = "warning" if body.get("severity") == "warning" else "critical"
    cooldown = int(body.get("cooldown_min") or 0)
    if not 0 <= cooldown <= 1440:
        raise ValueError("O intervalo mínimo entre avisos deve ficar entre 0 e 1440 min")
    params = json.dumps(_clean_params(kind, body.get("params") or {}))
    enabled = 1 if body.get("enabled", True) else 0
    if body.get("id"):
        conn.execute("UPDATE alert_rules SET name=?, kind=?, enabled=?, severity=?, params=?, cooldown_min=? WHERE id=?",
                     (name, kind, enabled, severity, params, cooldown, int(body["id"])))
        rule_id = int(body["id"])
    else:
        rule_id = conn.execute(
            "INSERT INTO alert_rules(name, kind, enabled, severity, params, cooldown_min, created_at) "
            "VALUES (?,?,?,?,?,?,?)", (name, kind, enabled, severity, params, cooldown, db.now_ms())).lastrowid
    conn.commit()
    return {"id": rule_id}


def set_enabled(conn, rule_id: int, enabled: bool) -> None:
    """Liga/desliga pela chave da lista. Ao ligar, confere se a regra está completa
    (ex.: o preset de silêncio vem sem log group e só pode ser ligado depois de configurado)."""
    rule = conn.execute("SELECT kind, params FROM alert_rules WHERE id = ?", (rule_id,)).fetchone()
    if rule is None:
        raise ValueError("Alarme não encontrado")
    if enabled:
        _clean_params(rule["kind"], json.loads(rule["params"] or "{}"))
    conn.execute("UPDATE alert_rules SET enabled = ? WHERE id = ?", (1 if enabled else 0, rule_id))
    conn.commit()


# ------------------------------------------------------------------ disparos
def _fire(conn, rule: dict, title: str, detail: str, key: str, fingerprint: str | None = None,
          service: str | None = None) -> bool:
    """Grava o disparo, respeitando o intervalo mínimo (cooldown) por regra + chave."""
    now = db.now_ms()
    cooldown_ms = max(int(rule["cooldown_min"]), 0) * 60_000
    last = conn.execute("SELECT MAX(ts) FROM alert_events WHERE rule_id = ? AND dedupe_key = ?",
                        (rule["id"], key)).fetchone()[0]
    if last and (cooldown_ms == 0 or now - last < cooldown_ms):
        return False  # cooldown 0 = dispara uma única vez por chave
    conn.execute("INSERT INTO alert_events(rule_id, rule_name, severity, title, detail, fingerprint, service, "
                 "dedupe_key, ts) VALUES (?,?,?,?,?,?,?,?,?)",
                 (rule["id"], rule["name"], rule["severity"], title, detail, fingerprint, service, key, now))
    log.info("ALARME [%s] %s", rule["name"], title)
    return True


def _event_filter(params: dict, alias: str = "e", errors_only: bool = True) -> tuple[str, list]:
    """Warnings não disparam alarmes de erro, a menos que a regra escolha a categoria Warning."""
    sql, args = "", []
    if errors_only and not params.get("category"):
        sql += f" AND {alias}.category != 'warning'"
    if params.get("service"):
        sql += f" AND {alias}.service LIKE ?"
        args.append(f"%{params['service']}%")
    if params.get("category"):
        sql += f" AND {alias}.category = ?"
        args.append(params["category"])
    return sql, args


def _not_ignored() -> str:
    return " AND e.fingerprint NOT IN (SELECT fingerprint FROM issues WHERE status = 'ignored')"


def evaluate(conn, collector=None, since: int | None = None) -> int:
    """Avalia as regras ligadas. `since`: só considera novidades depois deste instante (ms)."""
    now = db.now_ms()
    s = db.get_settings(conn)
    since = since if since is not None else int(s.get("alerts_watermark") or now)
    fired = 0
    for rule in db.rows(conn.execute("SELECT * FROM alert_rules WHERE enabled = 1")):
        p = json.loads(rule["params"] or "{}")
        try:
            fired += _evaluate_rule(conn, collector, rule, p, since, now)
        except Exception as exc:  # uma regra com problema não derruba as outras
            log.warning("falha ao avaliar o alarme '%s': %s", rule["name"], exc)
    db.set_setting(conn, "alerts_watermark", now)
    conn.commit()
    return fired


def _evaluate_rule(conn, collector, rule, p, since, now) -> int:
    kind, fired = rule["kind"], 0
    fsql, fargs = _event_filter(p, "i")

    if kind in ("new_issue", "regression"):
        col = "created_at" if kind == "new_issue" else "regressed_at"
        rows = conn.execute(f"SELECT * FROM issues i WHERE {col} > ? AND status = 'open'{fsql} ORDER BY {col}",
                            [since] + fargs).fetchall()
        for it in rows[:20]:
            label = "Issue nova" if kind == "new_issue" else "Regressão — o erro voltou"
            fired += _fire(conn, rule, f"{label} em {it['service']}", it["title"],
                           f"{it['fingerprint']}:{it[col]}", it["fingerprint"], it["service"])

    elif kind == "spike":
        start = now - int(p.get("window_min", 10)) * 60_000
        esql, eargs = _event_filter(p)
        for r in conn.execute("SELECT e.service, COUNT(*) AS n, MAX(e.fingerprint) AS fpx FROM events e WHERE e.ts >= ?"
                              + esql + _not_ignored() + " GROUP BY e.service HAVING n >= ? ORDER BY n DESC",
                              [start] + eargs + [int(p.get("threshold", 20))]):
            fired += _fire(conn, rule, f"Pico de erros em {r['service']}",
                           f"{r['n']} erros nos últimos {p.get('window_min')} min (limite: {p.get('threshold')})",
                           f"spike:{r['service']}", None, r["service"])

    elif kind == "anomaly":
        window = int(p.get("window_min", 15)) * 60_000
        esql, eargs = _event_filter(p)
        current = {r["service"]: r["n"] for r in conn.execute(
            "SELECT e.service, COUNT(*) AS n FROM events e WHERE e.ts >= ?" + esql + _not_ignored() + " GROUP BY e.service",
            [now - window] + eargs)}
        buckets = (86_400_000 - window) / window
        for service, n in current.items():
            if n < int(p.get("min_count", 10)):
                continue
            base_total = conn.execute(
                "SELECT COUNT(*) FROM events e WHERE e.service = ? AND e.ts >= ? AND e.ts < ?" + esql + _not_ignored(),
                [service, now - 86_400_000, now - window] + eargs).fetchone()[0]
            baseline = base_total / buckets if buckets > 0 else 0
            if n >= float(p.get("factor", 3)) * max(baseline, 1):
                times = f"{n / max(baseline, 1):.1f}×" if baseline >= 1 else "muito"
                fired += _fire(conn, rule, f"Erros {times} acima do normal em {service}",
                               f"{n} erros nos últimos {p.get('window_min')} min; a média é {baseline:.1f} por período",
                               f"anomaly:{service}", None, service)

    elif kind == "match":
        rx = re.compile(p["text"] if p.get("regex") else re.escape(p["text"]), re.IGNORECASE)
        esql, eargs = _event_filter(p, errors_only=False)
        for e in conn.execute("SELECT e.* FROM events e WHERE e.collected_at > ?" + esql + _not_ignored()
                              + " ORDER BY e.ts DESC LIMIT 2000", [since] + eargs):
            if rx.search(e["message"]):
                title = conn.execute("SELECT title FROM issues WHERE fingerprint = ?", (e["fingerprint"],)).fetchone()
                fired += _fire(conn, rule, f"Erro com “{p['text']}” em {e['service']}",
                               title[0] if title else e["message"][:300], f"match:{e['fingerprint']}",
                               e["fingerprint"], e["service"])
                break  # um aviso por avaliação basta; o cooldown evita repetição

    elif kind == "cw_alarm":
        for a in conn.execute("SELECT * FROM alarms WHERE state = 'ALARM'"):
            if p.get("alarm") and p["alarm"].lower() not in a["name"].lower():
                continue
            # alarmes do auto scaling ficam em ALARM o tempo todo (é como ele decide escalar): não é problema
            if a["name"].startswith("TargetTracking-") and not p.get("include_autoscaling"):
                continue
            fired += _fire(conn, rule, f"Alarme do CloudWatch: {a['name']}",
                           f"{a['namespace']} · {a['metric']} — {a['reason'] or ''}"[:500],
                           f"cw:{a['name']}:{a['state_updated_at']}")

    elif kind == "stalled":
        # com coleta a cada 30/60 min, ficar 20 min sem coletar é normal: exige pelo menos 3 intervalos
        interval = int(db.get_settings(conn).get("interval_min", "5") or 5)
        limit = max(int(p.get("minutes", 20)), 3 * interval) * 60_000
        last_ok = conn.execute("SELECT MAX(finished_at) FROM runs WHERE kind = 'live' AND status IN ('ok','partial')"
                               ).fetchone()[0]
        since_ok = max(last_ok or 0, _awake_since)
        if collector is not None and now - since_ok > limit:
            err = conn.execute("SELECT error FROM runs WHERE kind = 'live' AND status = 'error' AND error IS NOT NULL "
                               "ORDER BY id DESC LIMIT 1").fetchone()
            ago = f"há {round((now - last_ok) / 60_000)} min" if last_ok else "desde que o monitor abriu"
            fired += _fire(conn, rule, "A coleta está parada",
                           f"Nenhuma coleta bem-sucedida {ago}. "
                           + (f"Último erro: {err[0][:300]}" if err else "Verifique a internet e as credenciais da AWS."),
                           f"stalled:{last_ok}")

    elif kind == "silence" and collector is not None and p.get("group"):
        streams = collector.logs.describe_log_streams(logGroupName=p["group"], orderBy="LastEventTime",
                                                      descending=True, limit=1).get("logStreams", [])
        last_event = streams[0].get("lastEventTimestamp") if streams else None
        if not last_event or now - last_event > int(p.get("minutes", 60)) * 60_000:
            ago = f"há {round((now - last_event) / 60_000)} min" if last_event else "nenhum log encontrado"
            fired += _fire(conn, rule, f"Silêncio em {fp.service_of(p['group'])}",
                           f"O log group {p['group']} não recebe logs ({ago}). O serviço pode ter parado.",
                           f"silence:{p['group']}:{last_event}", None, fp.service_of(p["group"]))
    return fired


def test_alert(conn, rule_id: int | None) -> None:
    rule = conn.execute("SELECT * FROM alert_rules WHERE id = ?", (rule_id,)).fetchone() if rule_id else None
    name = rule["name"] if rule else "Teste"
    severity = rule["severity"] if rule else "critical"
    conn.execute("INSERT INTO alert_events(rule_id, rule_name, severity, title, detail, dedupe_key, ts) "
                 "VALUES (?,?,?,?,?,?,?)", (rule_id, name, severity, f"Teste do alarme “{name}”",
                                            "É assim que o aviso aparece quando a regra disparar.",
                                            f"test:{db.now_ms()}", db.now_ms()))
    conn.commit()


class AlertLoop:
    """Avalia as regras periodicamente, numa thread separada da coleta."""

    def __init__(self, collector=None):
        self.collector = collector
        self._wake = threading.Event()

    def start(self):
        with db.connect() as conn:
            ensure_presets(conn)
            # Só avisa sobre o que acontecer daqui para frente (não sobre o histórico)
            db.set_setting(conn, "alerts_watermark", db.now_ms())
            conn.commit()
        threading.Thread(target=self._loop, name="alerts", daemon=True).start()

    def trigger(self):
        self._wake.set()

    def _loop(self):
        global _awake_since
        last_tick = time.time()
        while True:
            self._wake.wait(EVAL_INTERVAL_S)
            self._wake.clear()
            # um "tique" que demorou muito mais que o normal = o computador estava suspenso/hibernando
            if time.time() - last_tick > EVAL_INTERVAL_S * 4:
                _awake_since = int(time.time() * 1000)
            last_tick = time.time()
            try:
                with db.connect() as conn:
                    evaluate(conn, self.collector)
            except Exception:
                log.exception("falha na avaliação dos alarmes")
