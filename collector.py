"""Coleta periódica: Logs Insights (erros) + alarmes do CloudWatch -> SQLite."""
import hashlib
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import boto3
from botocore.config import Config

import blacklist
import db
import fingerprint as fp
from config import (AWS_PROFILE, AWS_REGION, INSIGHTS_MAX_GROUPS, INSIGHTS_MAX_ROWS,
                    INSIGHTS_WORKERS, WINDOW_OVERLAP_MS)

log = logging.getLogger("collector")

GROUPS_REFRESH_MS = 60 * 60 * 1000
MAX_MESSAGE_CHARS = 64_000
QUERY_TIMEOUT_S = 180
# Recuperação de lacunas: roda em blocos, em segundo plano, separada da coleta ao vivo.
BACKFILL_CHUNK_MS = 6 * 3_600_000
# Quando há lacuna grande, a coleta ao vivo cobre só os últimos minutos; o resto vira recuperação.
LIVE_WINDOW_ON_GAP_MS = 10 * 60 * 1000
SEARCH_MAX_RESULTS = 300
SEARCH_MESSAGE_CHARS = 4000   # linha inteira continua disponível em "Ver contexto"


class Collector:
    def __init__(self):
        session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
        cfg = Config(retries={"max_attempts": 8, "mode": "adaptive"}, connect_timeout=10, read_timeout=60)
        self.logs = session.client("logs", config=cfg)
        self.cloudwatch = session.client("cloudwatch", config=cfg)
        self.account_id = None
        self._wake = threading.Event()
        self._run_now = False
        self._cycle_lock = threading.Lock()
        self._force_groups_refresh = False
        self._backfill_wake = threading.Event()
        self._backfill_lock = threading.Lock()
        self.state = {
            "running": False, "phase": "aguardando", "last_started": None, "last_finished": None,
            "last_status": None, "last_error": None, "next_run": None,
        }
        self.backfill_state = {"running": False, "error": None}

    # ------------------------------------------------------------------ agenda
    def start(self) -> None:
        threading.Thread(target=self._loop, name="collector", daemon=True).start()
        threading.Thread(target=self._backfill_loop, name="backfill", daemon=True).start()

    def trigger(self) -> None:
        """Roda um ciclo agora."""
        self._run_now = True
        self._wake.set()

    def reschedule(self) -> None:
        """Recalcula o próximo ciclo (ex.: intervalo mudou) sem rodar agora."""
        self._wake.set()

    def _interval_s(self) -> int:
        with db.connect() as conn:
            return max(1, int(db.get_settings(conn).get("interval_min", "5"))) * 60

    def _loop(self) -> None:
        while True:
            self.run_cycle()
            while True:
                next_at = (self.state["last_finished"] or time.time()) + self._interval_s()
                self.state["next_run"] = next_at
                remaining = next_at - time.time()
                if remaining <= 0:
                    break
                if self._wake.wait(remaining):
                    self._wake.clear()
                    if self._run_now:
                        self._run_now = False
                        break

    # ------------------------------------------------------------------ ciclo
    def run_cycle(self) -> dict:
        if not self._cycle_lock.acquire(blocking=False):
            return {"status": "busy"}
        conn = db.connect()
        started = db.now_ms()
        self.state.update(running=True, phase="iniciando", last_started=time.time(), last_error=None)
        run_id = conn.execute("INSERT INTO runs(kind, started_at, status) VALUES ('live', ?, 'running')",
                              (started,)).lastrowid
        conn.commit()
        result = {"status": "error"}
        try:
            settings = db.get_settings(conn)
            if self.account_id is None:
                self.account_id = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION) \
                    .client("sts").get_caller_identity()["Account"]

            self.state["phase"] = "listando log groups"
            self._refresh_groups(conn)

            end = db.now_ms()
            last_end = settings.get("last_window_end")
            if last_end:
                start = int(last_end) - WINDOW_OVERLAP_MS
                # Lacuna grande (monitor ficou fechado): vira recuperação em segundo plano
                # (automática ou perguntando ao usuário) e a coleta ao vivo segue só com o recente.
                if end - start > float(settings.get("auto_recover_hours", "24")) * 3_600_000:
                    cut = end - LIVE_WINDOW_ON_GAP_MS
                    self._register_gap(conn, start, cut, settings.get("gap_always_recover") == "1")
                    start = cut
            else:
                start = end - int(float(settings["first_lookback_hours"]) * 3_600_000)
            groups = self._active_groups(conn, settings, start, end)

            self.state["phase"] = f"consultando {len(groups)} log groups"
            query = build_query(settings)
            rows, errors = self._query_all(groups, start, end, query)

            self.state["phase"] = "gravando"
            stats = ingest(conn, rows, settings, fetch_block=self.fetch_block)

            self.state["phase"] = "alarmes"
            try:
                self._refresh_alarms(conn)
            except Exception as exc:  # alarmes não devem derrubar a coleta de logs
                errors.append(f"alarmes: {exc}")

            purge(conn, int(settings["retention_days"]))
            if not errors:
                db.set_setting(conn, "last_window_end", end)
            status = "ok" if not errors else ("partial" if rows or len(errors) < len(groups) else "error")
            err_txt = "; ".join(errors[:5]) or None
            conn.execute(
                "UPDATE runs SET finished_at=?, window_start=?, window_end=?, groups=?, rows_found=?, "
                "events_new=?, issues_new=?, status=?, error=? WHERE id=?",
                (db.now_ms(), start, end, len(groups), len(rows), stats["events_new"], stats["issues_new"],
                 status, err_txt, run_id))
            conn.commit()
            self.state.update(last_status=status, last_error=err_txt)
            result = {"status": status, **stats, "rows": len(rows), "groups": len(groups)}
            log.info("ciclo %s: %s linhas, %s eventos novos, %s issues novas",
                     status, len(rows), stats["events_new"], stats["issues_new"])
        except Exception as exc:
            log.exception("falha no ciclo de coleta")
            conn.execute("UPDATE runs SET finished_at=?, status='error', error=? WHERE id=?",
                         (db.now_ms(), str(exc)[:2000], run_id))
            conn.commit()
            self.state.update(last_status="error", last_error=str(exc))
        finally:
            conn.close()
            self.state.update(running=False, phase="aguardando", last_finished=time.time())
            self._cycle_lock.release()
        return result

    # ------------------------------------------------------------ recuperação
    @staticmethod
    def _extend_range(conn, prefix: str, start: int, end: int) -> None:
        s = db.get_settings(conn)
        cur_start, cur_end = s.get(f"{prefix}_start"), s.get(f"{prefix}_end")
        db.set_setting(conn, f"{prefix}_start", min(int(cur_start), start) if cur_start else start)
        db.set_setting(conn, f"{prefix}_end", max(int(cur_end), end) if cur_end else end)
        if prefix == "backfill_job":
            cursor = s.get("backfill_job_cursor")
            db.set_setting(conn, "backfill_job_cursor", min(int(cursor), start) if cursor else start)

    def _register_gap(self, conn, start: int, end: int, auto: bool) -> None:
        if auto:
            self._extend_range(conn, "backfill_job", start, end)
            self._backfill_wake.set()
            log.info("lacuna de %.1fh: recuperando automaticamente", (end - start) / 3_600_000)
        else:
            self._extend_range(conn, "backfill_pending", start, end)
            log.info("lacuna de %.1fh: aguardando decisão no painel", (end - start) / 3_600_000)
        conn.commit()

    def backfill_status(self, conn) -> dict:
        s = db.get_settings(conn)
        pending = job = None
        if s.get("backfill_pending_start"):
            ps, pe = int(s["backfill_pending_start"]), int(s["backfill_pending_end"])
            pending = {"start": ps, "end": pe, "hours": round((pe - ps) / 3_600_000, 1)}
        if s.get("backfill_job_start"):
            js, je, jc = (int(s["backfill_job_start"]), int(s["backfill_job_end"]),
                          int(s["backfill_job_cursor"]))
            job = {"start": js, "end": je, "cursor": jc,
                   "progress": round(100 * (jc - js) / max(1, je - js), 1), **self.backfill_state}
        return {"pending": pending, "job": job}

    def decide_backfill(self, conn, mode: str, hours: float | None = None, from_ms: int | None = None,
                        remember: bool = False) -> None:
        s = db.get_settings(conn)
        if not s.get("backfill_pending_start"):
            raise ValueError("Não há lacuna aguardando decisão")
        ps, pe = int(s["backfill_pending_start"]), int(s["backfill_pending_end"])
        if mode == "all":
            start = ps
        elif mode == "hours":
            if not hours or hours <= 0:
                raise ValueError("Informe quantas horas recuperar")
            start = max(ps, pe - int(hours * 3_600_000))
        elif mode == "from":
            if not from_ms:
                raise ValueError("Informe a data inicial")
            start = max(ps, int(from_ms))
        elif mode != "skip":
            raise ValueError("Opção inválida")
        if mode != "skip":
            if start >= pe:
                raise ValueError("A data escolhida é posterior ao fim da lacuna")
            self._extend_range(conn, "backfill_job", start, pe)
        for key in ("backfill_pending_start", "backfill_pending_end"):
            db.set_setting(conn, key, "")
        if remember:
            db.set_setting(conn, "gap_always_recover", "1")
        conn.commit()
        self._backfill_wake.set()

    def cancel_backfill(self, conn) -> None:
        # Sem lock: o bloco em andamento termina, mas não avança o cursor de um job cancelado.
        for key in ("backfill_job_start", "backfill_job_end", "backfill_job_cursor"):
            db.set_setting(conn, key, "")
        conn.commit()

    def _backfill_loop(self) -> None:
        while True:
            try:
                worked = self._backfill_step()
            except Exception as exc:
                log.exception("falha na recuperação")
                self.backfill_state.update(running=False, error=str(exc))
                worked = False
            if not worked:
                # sem trabalho (ou erro): espera ser acordado ou tenta de novo em 1 min
                self._backfill_wake.wait(60)
                self._backfill_wake.clear()

    def _backfill_step(self) -> bool:
        with self._backfill_lock, db.connect() as conn:
            s = db.get_settings(conn)
            if not s.get("backfill_job_start") or not s.get("last_window_end"):
                self.backfill_state["running"] = False
                return False
            end, cursor = int(s["backfill_job_end"]), int(s["backfill_job_cursor"])
            if cursor >= end:
                for key in ("backfill_job_start", "backfill_job_end", "backfill_job_cursor"):
                    db.set_setting(conn, key, "")
                conn.commit()
                self.backfill_state.update(running=False, error=None)
                log.info("recuperação concluída")
                return False

            self.backfill_state["running"] = True
            chunk_end = min(end, cursor + BACKFILL_CHUNK_MS)
            groups = self._active_groups(conn, s, cursor, chunk_end)
            started = db.now_ms()
            rows, errors = self._query_all(groups, cursor, chunk_end, build_query(s))
            stats = ingest(conn, rows, s, fetch_block=self.fetch_block, backfill=True)
            status = "ok" if not errors else "error"
            conn.execute(
                "INSERT INTO runs(kind, started_at, finished_at, window_start, window_end, groups, rows_found, "
                "events_new, issues_new, status, error) VALUES ('backfill',?,?,?,?,?,?,?,?,?,?)",
                (started, db.now_ms(), cursor, chunk_end, len(groups), len(rows), stats["events_new"],
                 stats["issues_new"], status, "; ".join(errors[:5]) or None))
            if errors:
                conn.commit()
                self.backfill_state["error"] = errors[0]
                return False  # tenta o mesmo bloco de novo em 1 min
            # o job pode ter sido cancelado/estendido enquanto consultava
            if db.get_settings(conn).get("backfill_job_start"):
                db.set_setting(conn, "backfill_job_cursor", chunk_end)
            conn.commit()
            self.backfill_state["error"] = None
            log.info("recuperação: bloco até %s, %s eventos novos", chunk_end, stats["events_new"])
            return True

    # ------------------------------------------------------------------- busca
    def search(self, conn, text: str, start: int, end: int, *, regex: bool = False, case: bool = False,
               word: bool = False, group_filter: str = "", limit: int = SEARCH_MAX_RESULTS) -> dict:
        """Busca livre em todos os log groups (não só erros) via Logs Insights."""
        pattern = text if regex else _escape_re2(text)
        if regex:
            re.compile(text)  # validação básica antes de gastar uma consulta
            pattern = re.sub(r"(?<!\\)/", r"\/", pattern)
        if word:
            pattern = rf"\b(?:{pattern})\b"
        if not case:
            pattern = "(?i)" + pattern
        # Na busca vale tudo (inclusive grupos excluídos da coleta); filtro opcional por trecho do nome
        groups = self._active_groups(
            conn, {"group_exclude_regex": "", "group_include_regex": re.escape(group_filter) if group_filter else ""},
            start, end)
        if not groups:
            return {"items": [], "groups": [], "total": 0, "groups_searched": 0, "errors": []}
        q_list = ("fields @timestamp, @message, @logStream, @log\n"
                  f"| filter @message like /{pattern}/\n| sort @timestamp desc\n| limit {limit}")
        q_count = f"filter @message like /{pattern}/\n| stats count(*) as n by @log"
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_list = pool.submit(self._query_all, groups, start, end, q_list)
            f_count = pool.submit(self._query_all, groups, start, end, q_count)
            rows, errors = f_list.result()
            counts, errors2 = f_count.result()

        items = []
        for r in rows:
            try:
                ts = _parse_ts(r["@timestamp"])
            except (KeyError, ValueError):
                continue
            group = (r.get("@log") or "").split(":", 1)[-1]
            items.append({"ts": ts, "log_group": group, "service": fp.service_of(group),
                          "log_stream": r.get("@logStream") or "", "message": (r.get("@message") or "")[:SEARCH_MESSAGE_CHARS]})
        items.sort(key=lambda i: -i["ts"])
        by_group = {}
        for r in counts:
            group = (r.get("@log") or "").split(":", 1)[-1]
            by_group[group] = by_group.get(group, 0) + int(float(r.get("n") or 0))
        group_list = sorted(({"log_group": g, "service": fp.service_of(g), "kind": fp.kind_of(g), "n": n}
                             for g, n in by_group.items()), key=lambda x: -x["n"])
        return {"items": items[:limit], "groups": group_list, "total": sum(by_group.values()),
                "groups_searched": len(groups), "errors": (errors + errors2)[:5]}

    # ------------------------------------------------------------ log groups
    def _refresh_groups(self, conn) -> None:
        last = conn.execute("SELECT MAX(updated_at) FROM log_groups").fetchone()[0]
        if last and db.now_ms() - last < GROUPS_REFRESH_MS and not self._force_groups_refresh:
            return
        now = db.now_ms()
        names = []
        for page in self.logs.get_paginator("describe_log_groups").paginate():
            for g in page["logGroups"]:
                names.append(g["logGroupName"])
                conn.execute(
                    "INSERT INTO log_groups(name, stored_bytes, retention_days, creation_time, updated_at) "
                    "VALUES (?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET stored_bytes=excluded.stored_bytes, "
                    "retention_days=excluded.retention_days, creation_time=excluded.creation_time, "
                    "updated_at=excluded.updated_at",
                    (g["logGroupName"], g.get("storedBytes", 0), g.get("retentionInDays"),
                     g.get("creationTime"), now))
        if names:
            conn.execute(f"DELETE FROM log_groups WHERE updated_at < ?", (now,))
        conn.commit()
        self._force_groups_refresh = False

    @staticmethod
    def _active_groups(conn, settings, start: int, end: int) -> list[str]:
        # O Insights recusa a consulta inteira se algum grupo foi criado depois do fim da janela
        # ou se a janela já saiu da retenção dele — esses ficam de fora.
        now = db.now_ms()
        names = [
            r["name"] for r in conn.execute(
                "SELECT name, creation_time, retention_days FROM log_groups ORDER BY name")
            if (r["creation_time"] or 0) <= end
            and (not r["retention_days"] or end >= now - r["retention_days"] * 86_400_000)
        ]
        exclude = settings.get("group_exclude_regex") or ""
        include = settings.get("group_include_regex") or ""
        if include:
            names = [n for n in names if re.search(include, n)]
        if exclude:
            names = [n for n in names if not re.search(exclude, n)]
        return names

    # --------------------------------------------------------------- insights
    def _query_all(self, groups, start, end, query):
        chunks = [groups[i:i + INSIGHTS_MAX_GROUPS] for i in range(0, len(groups), INSIGHTS_MAX_GROUPS)]
        rows, errors = [], []
        with ThreadPoolExecutor(max_workers=INSIGHTS_WORKERS) as pool:
            futures = {pool.submit(self._query_safe, chunk, start, end, query): chunk for chunk in chunks}
            for fut in as_completed(futures):
                try:
                    rows.extend(fut.result())
                except Exception as exc:
                    chunk = futures[fut]
                    if "ResourceNotFoundException" in str(exc):
                        self._force_groups_refresh = True
                    errors.append(f"{chunk[0]}…({len(chunk)} grupos): {exc}")
                    log.warning("consulta falhou para %s grupos: %s", len(chunk), exc)
        return rows, errors

    def _query_safe(self, groups, start, end, query) -> list[dict]:
        """Se o Insights recusar o lote por causa de algum grupo (criação/retenção), divide até isolá-lo."""
        try:
            return self._query_window(groups, start, end, query)
        except Exception as exc:
            text = str(exc)
            if "MalformedQueryException" not in text or not ("creation time" in text or "retention" in text):
                raise
            if len(groups) == 1:
                log.info("pulando %s: sem logs para esse período (criação/retenção)", groups[0])
                return []
            half = len(groups) // 2
            return (self._query_safe(groups[:half], start, end, query)
                    + self._query_safe(groups[half:], start, end, query))

    def _query_window(self, groups, start, end, query, depth=0) -> list[dict]:
        qid = self.logs.start_query(
            logGroupNames=groups, startTime=start // 1000, endTime=end // 1000 + 1,
            queryString=query, limit=INSIGHTS_MAX_ROWS)["queryId"]
        deadline = time.time() + QUERY_TIMEOUT_S
        delay = 1.0
        while True:
            time.sleep(delay)
            resp = self.logs.get_query_results(queryId=qid)
            if resp["status"] in ("Complete", "Failed", "Cancelled", "Timeout", "Unknown"):
                break
            if time.time() > deadline:
                self.logs.stop_query(queryId=qid)
                raise TimeoutError("consulta Logs Insights demorou demais")
            delay = min(delay * 1.5, 5)
        if resp["status"] != "Complete":
            raise RuntimeError(f"consulta terminou com status {resp['status']}")

        results = [{f["field"]: f.get("value", "") for f in row} for row in resp["results"]]
        # Bateu o limite de linhas: divide a janela ao meio para não perder eventos.
        if len(results) >= INSIGHTS_MAX_ROWS and end - start > 60_000 and depth < 8:
            mid = (start + end) // 2
            return (self._query_window(groups, start, mid, query, depth + 1)
                    + self._query_window(groups, mid, end, query, depth + 1))
        return results

    def fetch_block(self, group: str, stream: str, start_ts: int, end_ts: int) -> str:
        """Linhas originais, em ordem, de um trecho do stream (tracebacks quebrados em várias linhas)."""
        resp = self.logs.get_log_events(logGroupName=group, logStreamName=stream, startTime=start_ts,
                                        endTime=end_ts + 1, startFromHead=True, limit=500)
        return "\n".join(e["message"].rstrip() for e in resp["events"])

    # ----------------------------------------------------------------- alarmes
    def _refresh_alarms(self, conn) -> None:
        now = db.now_ms()
        current = {r["name"]: r["state"] for r in conn.execute("SELECT name, state FROM alarms")}
        seen = set()
        for page in self.cloudwatch.get_paginator("describe_alarms").paginate(
                AlarmTypes=["MetricAlarm", "CompositeAlarm"]):
            for a in page.get("MetricAlarms", []) + page.get("CompositeAlarms", []):
                name = a["AlarmName"]
                seen.add(name)
                state = a["StateValue"]
                updated = a.get("StateUpdatedTimestamp")
                updated_ms = int(updated.timestamp() * 1000) if updated else None
                if name in current and current[name] != state:
                    conn.execute(
                        "INSERT INTO alarm_history(name, old_state, new_state, reason, ts) VALUES (?,?,?,?,?)",
                        (name, current[name], state, a.get("StateReason"), updated_ms or now))
                conn.execute(
                    "INSERT INTO alarms(name, state, reason, namespace, metric, state_updated_at, seen_at) "
                    "VALUES (?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET state=excluded.state, "
                    "reason=excluded.reason, namespace=excluded.namespace, metric=excluded.metric, "
                    "state_updated_at=excluded.state_updated_at, seen_at=excluded.seen_at",
                    (name, state, a.get("StateReason"), a.get("Namespace", "composite"),
                     a.get("MetricName", ""), updated_ms, now))
        if seen:
            conn.execute(f"DELETE FROM alarms WHERE name NOT IN ({','.join('?' * len(seen))})", tuple(seen))
        conn.commit()


# ---------------------------------------------------------------------- helpers
def _escape_re2(text: str) -> str:
    """Escapa texto literal para regex RE2 dentro de /.../ do Logs Insights."""
    return re.sub(r"([\\.^$|?*+()\[\]{}/])", r"\\\1", text)


def build_query(settings: dict) -> str:
    err = settings["error_regex"]
    excl = settings.get("exclude_regex") or ""
    q = "fields @timestamp, @message, @logStream, @log\n"
    q += f"| filter @message like /{err}/\n"
    if excl:
        q += f"| filter @message not like /{excl}/\n"
    q += f"| sort @timestamp asc\n| limit {INSIGHTS_MAX_ROWS}"
    return q


def _parse_ts(value: str) -> int:
    # Logs Insights devolve "2026-10-30 12:34:56.789" em UTC
    dt = datetime.strptime(value[:23], "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


MERGE_GAP_MS = 1500


def _to_blocks(rows: list[dict], ignore_low: bool) -> list[dict]:
    """Normaliza as linhas do Insights e junta tracebacks logados linha a linha num único evento."""
    parsed = []
    for r in rows:
        message = (r.get("@message") or "").rstrip()
        if not message or (ignore_low and fp.is_low_level(message)):
            continue
        try:
            ts = _parse_ts(r["@timestamp"])
        except (KeyError, ValueError):
            continue
        parsed.append({"group": (r.get("@log") or "").split(":", 1)[-1],
                       "stream": r.get("@logStream") or "", "ts": ts, "message": message})
    parsed.sort(key=lambda e: (e["group"], e["stream"], e["ts"]))

    blocks = []
    for e in parsed:
        prev = blocks[-1] if blocks else None
        if (prev and prev["group"] == e["group"] and prev["stream"] == e["stream"]
                and e["ts"] - prev["last_ts"] <= MERGE_GAP_MS and prev["lines"] < 300
                and (prev["in_traceback"] or fp.is_fragment(e["message"]))
                and (prev["in_traceback"] or fp.is_fragment(prev["message"]))):
            prev["message"] += "\n" + e["message"]
            prev["last_ts"] = e["ts"]
            prev["lines"] += 1
            prev["in_traceback"] = prev["in_traceback"] or bool(fp.TRACEBACK_START.search(e["message"]))
            continue
        blocks.append({**e, "last_ts": e["ts"], "lines": 1,
                       "in_traceback": bool(fp.TRACEBACK_START.search(e["message"]))})
    return blocks


MAX_BLOCK_FETCHES = 40


def ingest(conn, rows: list[dict], settings: dict, fetch_block=None, backfill: bool = False) -> dict:
    """backfill=True: issues criadas a partir de logs antigos não contam como "novas" para notificação."""
    ignore_low = settings.get("ignore_low_level", "1") == "1"
    now = db.now_ms()
    events_new = issues_new = fetches = 0
    blocked = blacklist.Matcher.load(conn)
    for b in _to_blocks(rows, ignore_low):
        group, stream, ts = b["group"], b["stream"], b["ts"]
        # Chave estável (independe da ordem das linhas): a mesma ocorrência na sobreposição é descartada.
        key = hashlib.sha1(f"{group}|{stream}|{ts}|{b['lines'] > 1}|{min(b['message'].splitlines())}"
                           .encode("utf-8", "replace")).hexdigest()
        if conn.execute("SELECT 1 FROM events WHERE event_key=?", (key,)).fetchone():
            continue
        message = b["message"]
        if b["lines"] > 1 and fetch_block and fetches < MAX_BLOCK_FETCHES:
            fetches += 1
            try:
                message = fetch_block(group, stream, ts, b["last_ts"]) or message
            except Exception as exc:
                log.warning("não consegui buscar o traceback completo em %s: %s", stream, exc)
        message = message[:MAX_MESSAGE_CHARS]
        service = fp.service_of(group)
        category = fp.classify(message)
        title = fp.extract_title(message)
        fprint = fp.fingerprint(service, category, title)
        if blocked.blocked(fprint, service, message, ts):
            continue  # falso alarme na lista de bloqueio: não guarda nem conta

        cur = conn.execute(
            "INSERT OR IGNORE INTO events(event_key, fingerprint, ts, service, log_group, log_stream, "
            "category, message, collected_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (key, fprint, ts, service, group, stream, category, message, now))
        if cur.rowcount == 0:
            continue
        events_new += 1

        # INSERT OR IGNORE: coleta ao vivo e recuperação podem gravar ao mesmo tempo
        created = conn.execute(
            "INSERT OR IGNORE INTO issues(fingerprint, title, category, service, log_group, first_seen, "
            "last_seen, count, status, created_at) VALUES (?,?,?,?,?,?,?,1,'open',?)",
            (fprint, title, category, service, group, ts, ts, ts if backfill else now)).rowcount
        if created:
            issues_new += 1
        else:
            issue = conn.execute("SELECT status, resolved_at FROM issues WHERE fingerprint=?", (fprint,)).fetchone()
            conn.execute(
                "UPDATE issues SET count=count+1, first_seen=MIN(first_seen, ?), last_seen=MAX(last_seen, ?) "
                "WHERE fingerprint=?", (ts, ts, fprint))
            # Voltou a acontecer depois de marcada como resolvida = regressão
            if issue["status"] == "resolved" and ts > (issue["resolved_at"] or 0):
                conn.execute("UPDATE issues SET status='open', regressed_at=? WHERE fingerprint=?",
                             (now, fprint))
    blocked.save(conn)
    conn.commit()
    return {"events_new": events_new, "issues_new": issues_new}


def purge(conn, retention_days: int) -> None:
    cutoff = db.now_ms() - retention_days * 86_400_000
    conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
    # Issues ignoradas ficam para sempre (para continuar ignorando)
    conn.execute("DELETE FROM issues WHERE last_seen < ? AND status != 'ignored'", (cutoff,))
    conn.execute("DELETE FROM runs WHERE started_at < ?", (cutoff,))
    conn.execute("DELETE FROM alarm_history WHERE ts < ?", (cutoff,))
    conn.commit()
