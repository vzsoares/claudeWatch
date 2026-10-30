"""CloudWatch Monitor — painel local de erros do CloudWatch.

Uso:  python app.py [--port 8765] [--no-browser]
"""
import argparse
import json
import logging
import mimetypes
import re
import socket
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import alerts
import db
import fingerprint as fp
import prompt as prompt_builder
from collector import Collector
from config import APP_NAME, AWS_PROFILE, AWS_REGION, DB_PATH, HOST, INTERVAL_CHOICES, PORT, WEB_DIR

log = logging.getLogger("app")

RANGES = {  # período -> (duração ms, tamanho do bucket ms)
    "1h": (3_600_000, 2 * 60_000),
    "6h": (6 * 3_600_000, 10 * 60_000),
    "24h": (24 * 3_600_000, 30 * 60_000),
    "7d": (7 * 86_400_000, 3 * 3_600_000),
    "30d": (30 * 86_400_000, 12 * 3_600_000),
}
TOP_SERVICES = 5
EDITABLE_SETTINGS = {"interval_min", "first_lookback_hours", "auto_recover_hours", "gap_always_recover",
                     "retention_days", "ignore_low_level", "error_regex", "exclude_regex",
                     "group_exclude_regex", "group_include_regex"}

collector: Collector | None = None
DEMO = False
alert_loop: alerts.AlertLoop | None = None


# --------------------------------------------------------------------- consultas
def _range(qs) -> tuple[int, int, str]:
    key = qs.get("range", "24h")
    span, bucket = RANGES.get(key, RANGES["24h"])
    return db.now_ms() - span, bucket, key


def _filters(qs, alias="e") -> tuple[str, list]:
    sql, args = "", []
    if qs.get("service"):
        sql += f" AND {alias}.service = ?"
        args.append(qs["service"])
    if qs.get("category"):
        sql += f" AND {alias}.category = ?"
        args.append(qs["category"])
    return sql, args


# Alarmes que o auto scaling (Target Tracking) cria sozinho: ALARM neles é controle de capacidade, não problema.
AUTOSCALING_PREFIX = "TargetTracking-"


def api_alarms(conn) -> dict:
    items = db.rows(conn.execute(
        "SELECT * FROM alarms ORDER BY CASE WHEN name LIKE ? THEN 1 ELSE 0 END, "
        "CASE state WHEN 'ALARM' THEN 0 WHEN 'INSUFFICIENT_DATA' THEN 1 ELSE 2 END, name",
        (AUTOSCALING_PREFIX + "%",)))
    for a in items:
        a["autoscaling"] = a["name"].startswith(AUTOSCALING_PREFIX)
    return {"items": items, "region": AWS_REGION,
            "history": db.rows(conn.execute("SELECT * FROM alarm_history ORDER BY ts DESC LIMIT 30"))}


def api_summary(conn, qs):
    start, _, key = _range(qs)
    span = RANGES[key][0]
    fsql, fargs = _filters(qs)
    base = ("SELECT COUNT(*) FROM events e JOIN issues i ON i.fingerprint = e.fingerprint "
            "WHERE i.status != 'ignored' AND e.ts >= ? AND e.ts < ?" + fsql)
    now = db.now_ms()
    total = conn.execute(base, [start, now + 1] + fargs).fetchone()[0]
    previous = conn.execute(base, [start - span, start] + fargs).fetchone()[0]
    services = conn.execute(
        "SELECT COUNT(DISTINCT e.service) FROM events e JOIN issues i ON i.fingerprint = e.fingerprint "
        "WHERE i.status != 'ignored' AND e.ts >= ?" + fsql, [start] + fargs).fetchone()[0]
    isql, iargs = _filters(qs, alias="i")
    open_issues = conn.execute("SELECT COUNT(*) FROM issues i WHERE status = 'open'" + isql,
                               iargs).fetchone()[0]
    new_issues = conn.execute("SELECT COUNT(*) FROM issues i WHERE status != 'ignored' AND first_seen >= ?" + isql,
                              [start] + iargs).fetchone()[0]
    regressions = conn.execute("SELECT COUNT(*) FROM issues i WHERE status = 'open' AND regressed_at >= ?" + isql,
                               [start] + iargs).fetchone()[0]
    alarms = conn.execute("SELECT COUNT(*) FROM alarms WHERE state = 'ALARM' AND name NOT LIKE ?",
                          (AUTOSCALING_PREFIX + "%",)).fetchone()[0]
    return {"total": total, "previous": previous, "services": services, "open_issues": open_issues,
            "new_issues": new_issues, "regressions": regressions, "alarms_in_alarm": alarms}


def api_timeline(conn, qs):
    start, bucket, _ = _range(qs)
    fsql, fargs = _filters(qs)
    rows = conn.execute(
        "SELECT (e.ts / ?) * ? AS b, e.service, COUNT(*) AS n FROM events e "
        "JOIN issues i ON i.fingerprint = e.fingerprint WHERE i.status != 'ignored' AND e.ts >= ?" + fsql +
        " GROUP BY b, e.service", [bucket, bucket, start] + fargs).fetchall()
    first = (start // bucket) * bucket
    labels = list(range(first, db.now_ms() + 1, bucket))
    index = {b: i for i, b in enumerate(labels)}
    totals: dict[str, int] = {}
    for r in rows:
        totals[r["service"]] = totals.get(r["service"], 0) + r["n"]
    top = [s for s, _ in sorted(totals.items(), key=lambda kv: -kv[1])[:TOP_SERVICES]]
    series = {s: [0] * len(labels) for s in top}
    other = [0] * len(labels)
    for r in rows:
        i = index.get(r["b"])
        if i is None:
            continue
        (series[r["service"]] if r["service"] in series else other)[i] += r["n"]
    out = [{"name": s, "data": series[s], "total": totals[s]} for s in top]
    if any(other):
        out.append({"name": "Outros", "data": other, "total": sum(other)})
    return {"labels": labels, "bucket_ms": bucket, "series": out}


def api_breakdown(conn, qs):
    # Cada gráfico ignora o próprio filtro: continua mostrando todas as barras, com a selecionada em destaque.
    start, _, _ = _range(qs)
    base = ("FROM events e JOIN issues i ON i.fingerprint = e.fingerprint "
            "WHERE i.status != 'ignored' AND e.ts >= ?")
    svc_sql, svc_args = _filters({"category": qs.get("category")})
    cat_sql, cat_args = _filters({"service": qs.get("service")})
    services = db.rows(conn.execute(
        f"SELECT e.service AS name, COUNT(*) AS n {base}{svc_sql} GROUP BY e.service ORDER BY n DESC LIMIT 12",
        [start] + svc_args))
    if qs.get("service") and not any(s["name"] == qs["service"] for s in services):
        services.append({"name": qs["service"], "n": 0})
    categories = db.rows(conn.execute(
        f"SELECT e.category AS name, COUNT(*) AS n {base}{cat_sql} GROUP BY e.category ORDER BY n DESC",
        [start] + cat_args))
    for c in categories:
        c["label"] = fp.CATEGORIES.get(c["name"], c["name"])
    return {"services": services, "categories": categories}


def api_issues(conn, qs):
    start, _, _ = _range(qs)
    status = qs.get("status", "open")
    fsql, fargs = _filters(qs)
    sql = ("SELECT i.*, COUNT(e.id) AS range_count, MAX(e.ts) AS range_last FROM issues i "
           "JOIN events e ON e.fingerprint = i.fingerprint AND e.ts >= ? WHERE 1=1" + fsql)
    args = [start] + fargs
    if status != "all":
        sql += " AND i.status = ?"
        args.append(status)
    if qs.get("q"):
        sql += " AND (i.title LIKE ? OR i.service LIKE ? OR i.log_group LIKE ?)"
        like = f"%{qs['q']}%"
        args += [like, like, like]
    order = "range_count DESC" if qs.get("sort") == "count" else "range_last DESC"
    sql += f" GROUP BY i.fingerprint ORDER BY {order} LIMIT 300"
    items = db.rows(conn.execute(sql, args))
    for it in items:
        it["category_label"] = fp.CATEGORIES.get(it["category"], it["category"])
    return {"items": items}


REPORT_MAX_ISSUES = 15
REPORT_MESSAGE_CHARS = 4_000


def api_report(conn, qs):
    """Issues mais frequentes do período (com os filtros da tela) + última mensagem de cada, mascaradas."""
    limit = min(int(qs.get("limit", REPORT_MAX_ISSUES)), 50)
    items = api_issues(conn, {**qs, "sort": "count"})["items"][:limit]
    for it in items:
        ev = conn.execute("SELECT message, ts FROM events WHERE fingerprint = ? ORDER BY ts DESC LIMIT 1",
                          (it["fingerprint"],)).fetchone()
        it["title"] = prompt_builder.mask(it["title"])
        it["message"] = prompt_builder.mask(ev["message"][:REPORT_MESSAGE_CHARS]) if ev else ""
        it["message_ts"] = ev["ts"] if ev else None
    alarms = db.rows(conn.execute(
        "SELECT name, state, namespace, metric, state_updated_at, reason FROM alarms WHERE state != 'OK' "
        f"AND name NOT LIKE '{AUTOSCALING_PREFIX}%' "
        "ORDER BY state, name"))
    total_alarms = conn.execute("SELECT COUNT(*) FROM alarms").fetchone()[0]
    return {"issues": items, "alarms": alarms, "total_alarms": total_alarms,
            "account": collector.account_id if collector else None, "region": AWS_REGION,
            "generated_at": db.now_ms()}


SEARCH_RANGES = {"15m": 15 * 60_000, "1h": 3_600_000, "6h": 6 * 3_600_000, "24h": 86_400_000,
                 "3d": 3 * 86_400_000, "7d": 7 * 86_400_000, "30d": 30 * 86_400_000}


def api_search(conn, body):
    """scope="errors" (padrão): texto completo dos erros já coletados, no banco local — instantâneo e sem custo.
    scope="all": todos os logs na AWS via Logs Insights (pode gerar custo)."""
    text = (body.get("text") or "").strip()
    if len(text) < 2:
        raise ValueError("Digite pelo menos 2 caracteres para buscar")
    end = int(body["end"]) if body.get("end") else db.now_ms()
    if body.get("start"):
        start = int(body["start"])
    else:
        start = end - SEARCH_RANGES.get(body.get("range", "24h"), SEARCH_RANGES["24h"])
    if start >= end:
        raise ValueError("O início precisa ser antes do fim")
    t0 = time.time()
    if body.get("scope") != "all" or collector is None:  # modo demonstração não acessa a AWS
        result = local_search(conn, text, start, end, body)
    else:
        try:
            result = collector.search(conn, text, start, end, regex=bool(body.get("regex")),
                                      case=bool(body.get("case")), word=bool(body.get("word")),
                                      group_filter=(body.get("group") or "").strip())
        except re.error as exc:
            raise ValueError(f"Regex inválida: {exc}")
        result["scope"] = "all"
    result.update(start=start, end=end, elapsed_s=round(time.time() - t0, 1))
    return result


LOCAL_SEARCH_MAX_ITEMS = 300


def local_search(conn, text, start, end, body):
    """Busca no texto completo dos erros guardados no SQLite (não acessa a AWS)."""
    flags = 0 if body.get("case") else re.IGNORECASE
    src = text if body.get("regex") else re.escape(text)
    if body.get("word"):
        src = r"\b(?:" + src + r")\b"
    try:
        rx = re.compile(src, flags)
    except re.error as exc:
        raise ValueError(f"Regex inválida: {exc}")
    sql = ("SELECT e.ts, e.log_group, e.service, e.log_stream, e.message, e.fingerprint, i.title AS issue_title, "
           "i.status AS issue_status FROM events e JOIN issues i ON i.fingerprint = e.fingerprint "
           "WHERE e.ts BETWEEN ? AND ?")
    args = [start, end]
    # Pré-filtro no SQLite (LIKE já ignora maiúsculas em ASCII); a regex confirma depois
    if not body.get("regex") and text.isascii():
        like = "%" + text.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
        sql += " AND (e.message LIKE ? ESCAPE '!' OR e.log_group LIKE ? ESCAPE '!')"
        args += [like, like]
    group_filter = (body.get("group") or "").strip().lower()
    items, by_group = [], {}
    for r in conn.execute(sql + " ORDER BY e.ts DESC", args):
        if group_filter and group_filter not in r["log_group"].lower():
            continue
        # procura no texto do erro e no nome do serviço/log group (ex.: "rpa" acha os erros das Lambdas rpa-*)
        if rx.search(r["message"]) or rx.search(r["log_group"]):
            by_group[r["log_group"]] = by_group.get(r["log_group"], 0) + 1
            if len(items) < LOCAL_SEARCH_MAX_ITEMS:
                items.append(dict(r))
    groups = sorted(({"log_group": g, "service": fp.service_of(g), "kind": fp.kind_of(g), "n": n}
                     for g, n in by_group.items()), key=lambda x: -x["n"])
    searched = conn.execute("SELECT COUNT(DISTINCT log_group) FROM events WHERE ts BETWEEN ? AND ?",
                            (start, end)).fetchone()[0]
    return {"items": items, "groups": groups, "total": sum(by_group.values()), "groups_searched": searched,
            "errors": [], "scope": "errors"}


def api_alerts_post(conn, path, body):
    if path == "/api/alerts/rules":
        result = alerts.save_rule(conn, body)
    elif m := re.fullmatch(r"/api/alerts/rules/(\d+)/delete", path):
        conn.execute("DELETE FROM alert_rules WHERE id = ?", (int(m.group(1)),))
        conn.commit()
        result = {"ok": True}
    elif m := re.fullmatch(r"/api/alerts/rules/(\d+)/toggle", path):
        conn.execute("UPDATE alert_rules SET enabled = ? WHERE id = ?", (1 if body.get("enabled") else 0, int(m.group(1))))
        conn.commit()
        result = {"ok": True}
    elif path == "/api/alerts/test":
        alerts.test_alert(conn, int(body["rule_id"]) if body.get("rule_id") else None)
        result = {"ok": True}
    elif m := re.fullmatch(r"/api/alerts/(\d+)/ack", path):
        conn.execute("UPDATE alert_events SET acknowledged_at = ? WHERE id = ? AND acknowledged_at IS NULL",
                     (db.now_ms(), int(m.group(1))))
        conn.commit()
        result = {"ok": True}
    elif path == "/api/alerts/ack-all":
        conn.execute("UPDATE alert_events SET acknowledged_at = ? WHERE acknowledged_at IS NULL", (db.now_ms(),))
        conn.commit()
        result = {"ok": True}
    elif path == "/api/alerts/settings":
        for key in ALERT_SETTINGS:
            if key in body:
                db.set_setting(conn, key, "1" if body[key] else "0")
        conn.commit()
        result = {"ok": True}
    else:
        raise KeyError("rota de alarmes não encontrada")
    if alert_loop:
        alert_loop.trigger()  # reavalia na hora (ex.: regra recém-ligada)
    return result


def api_issues_new(conn, qs):
    since = int(qs.get("since", "0"))
    return {"items": db.rows(conn.execute(
        "SELECT fingerprint, title, service, category, created_at, regressed_at FROM issues "
        "WHERE status = 'open' AND (created_at > ? OR regressed_at > ?) ORDER BY created_at DESC LIMIT 20",
        (since, since)))}


def api_issue(conn, fingerprint, qs):
    issue = conn.execute("SELECT * FROM issues WHERE fingerprint = ?", (fingerprint,)).fetchone()
    if issue is None:
        return None
    issue = dict(issue)
    issue["category_label"] = fp.CATEGORIES.get(issue["category"], issue["category"])
    issue["kind"] = fp.kind_of(issue["log_group"])
    start, bucket, _ = _range(qs)
    rows = conn.execute("SELECT (ts / ?) * ? AS b, COUNT(*) AS n FROM events WHERE fingerprint = ? AND ts >= ? "
                        "GROUP BY b", (bucket, bucket, fingerprint, start)).fetchall()
    first = (start // bucket) * bucket
    labels = list(range(first, db.now_ms() + 1, bucket))
    counts = {r["b"]: r["n"] for r in rows}
    events = db.rows(conn.execute(
        "SELECT id, ts, log_stream, substr(message, 1, 400) AS preview, length(message) AS size "
        "FROM events WHERE fingerprint = ? ORDER BY ts DESC LIMIT 100", (fingerprint,)))
    return {"issue": issue, "events": events,
            "timeline": {"labels": labels, "data": [counts.get(b, 0) for b in labels]}}


def api_event(conn, event_id):
    row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    return dict(row) if row else None


ALERT_SETTINGS = {"alert_notify": "1", "alert_sound": "0"}


def alerts_summary(conn) -> dict:
    """Alertas não reconhecidos das últimas 24h (o painel mostra o mais recente no meio da tela)."""
    active = db.rows(conn.execute(
        "SELECT e.*, r.kind AS rule_kind FROM alert_events e LEFT JOIN alert_rules r ON r.id = e.rule_id "
        "WHERE e.acknowledged_at IS NULL AND e.ts >= ? ORDER BY e.ts DESC LIMIT 20",
        (db.now_ms() - 86_400_000,)))
    s = db.get_settings(conn)
    return {"active": active, "notify": s.get("alert_notify", "1") == "1", "sound": s.get("alert_sound", "0") == "1"}


def api_alerts(conn) -> dict:
    alerts.ensure_presets(conn)
    history = db.rows(conn.execute("SELECT * FROM alert_events ORDER BY ts DESC LIMIT 50"))
    groups = [r[0] for r in conn.execute("SELECT name FROM log_groups ORDER BY name")]
    return {"rules": alerts.list_rules(conn), "history": history, "kinds": alerts.KINDS,
            "categories": fp.CATEGORIES, "log_groups": groups, **alerts_summary(conn)}


def api_status(conn):
    last_run = conn.execute("SELECT * FROM runs WHERE kind = 'live' AND status != 'running' "
                            "ORDER BY id DESC LIMIT 1").fetchone()
    s = db.get_settings(conn)
    return {
        "collector": collector.state if collector else None,
        "last_run": dict(last_run) if last_run else None,
        "interval_min": int(s["interval_min"]),
        "interval_choices": INTERVAL_CHOICES,
        "profile": AWS_PROFILE or "padrão", "region": AWS_REGION, "app_name": APP_NAME, "account": collector.account_id if collector else None,
        "db_path": str(DB_PATH),
        "groups_monitored": conn.execute("SELECT COUNT(*) FROM log_groups").fetchone()[0],
        "backfill": collector.backfill_status(conn) if collector else None,
        "demo": DEMO,
        "alerts": alerts_summary(conn),
        "last_window_end": int(s["last_window_end"]) if s.get("last_window_end") else None,
    }


def api_settings_save(conn, body):
    changes = {k: v for k, v in body.items() if k in EDITABLE_SETTINGS}
    if "interval_min" in changes:
        n = int(changes["interval_min"])
        if not 1 <= n <= 240:
            raise ValueError("Intervalo deve ficar entre 1 e 240 minutos")
        changes["interval_min"] = n
    for key in ("first_lookback_hours", "auto_recover_hours", "retention_days"):
        if key in changes and float(changes[key]) <= 0:
            raise ValueError(f"{key} deve ser maior que zero")
    for key in ("error_regex", "exclude_regex", "group_exclude_regex", "group_include_regex"):
        if changes.get(key):
            try:
                re.compile(changes[key])
            except re.error as exc:
                raise ValueError(f"Regex inválida em {key}: {exc}")
    if not changes.get("error_regex", "x"):
        raise ValueError("A regex de erro não pode ficar vazia")
    for k, v in changes.items():
        db.set_setting(conn, k, v)
    conn.commit()
    if collector and "interval_min" in changes:
        collector.reschedule()
    return db.get_settings(conn)


# ------------------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "CloudWatchMonitor/1.0"

    def log_message(self, fmt, *args):  # silencioso: o painel faz polling a cada poucos segundos
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        # Envia em blocos de 64 KB: no Windows, um único write grande depois de uma requisição demorada
        # chegava cortado no navegador (JSON incompleto na busca). Em blocos, chega inteiro.
        view = memoryview(data)
        sent = 0
        while sent < len(data):
            sent += self.connection.send(view[sent:sent + 65536])

    def finish(self):
        # Fechamento ordenado: no Windows, fechar o socket direto pode virar RST e descartar o fim de
        # respostas grandes (o navegador recebia JSON cortado). Avisa o fim do envio e espera o cliente fechar.
        super().finish()
        try:
            self.connection.shutdown(socket.SHUT_WR)
            self.connection.settimeout(5)
            while self.connection.recv(65536):
                pass
        except OSError:
            pass

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def _cross_site(self) -> bool:
        """Proteção contra CSRF: qualquer site aberto no navegador consegue mandar requisições para
        127.0.0.1. Só aceitamos as que vêm do próprio painel."""
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            return True
        origin = self.headers.get("Origin")
        if origin is None:  # navegadores sempre mandam Origin em POST; sem ele é curl/script local
            return False
        port = self.server.server_address[1]
        return origin not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}")

    def _reject_csrf(self, method: str) -> bool:
        if self._cross_site():
            log.warning("bloqueada requisição de outro site: %s %s (Origin=%s)",
                        method, self.path, self.headers.get("Origin"))
            self._send(403, {"error": "Requisição de outro site bloqueada."})
            return True
        # Exigir JSON força o navegador a pedir permissão (preflight) para requisições de outro site,
        # e o painel nunca concede: um formulário ou fetch "simples" de fora não chega a executar.
        if method == "POST" and self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            self._send(415, {"error": "Content-Type deve ser application/json."})
            return True
        return False

    def _static(self, path):
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        file = (WEB_DIR / rel).resolve()
        if WEB_DIR.resolve() not in file.parents or not file.is_file():
            return self._send(404, {"error": "não encontrado"})
        ctype = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self._send(200, file.read_bytes(), ctype)

    def do_GET(self):
        url = urlparse(self.path)
        qs = {k: v[0] for k, v in parse_qs(url.query).items()}
        path = url.path
        if not path.startswith("/api/"):
            return self._static(path)
        if self._reject_csrf("GET"):
            return
        try:
            with db.connect() as conn:
                if path == "/api/status":
                    return self._send(200, api_status(conn))
                if path == "/api/summary":
                    return self._send(200, api_summary(conn, qs))
                if path == "/api/timeline":
                    return self._send(200, api_timeline(conn, qs))
                if path == "/api/breakdown":
                    return self._send(200, api_breakdown(conn, qs))
                if path == "/api/issues":
                    return self._send(200, api_issues(conn, qs))
                if path == "/api/issues/new":
                    return self._send(200, api_issues_new(conn, qs))
                if path == "/api/report":
                    return self._send(200, api_report(conn, qs))
                if path == "/api/alarms":
                    return self._send(200, api_alarms(conn))
                if path == "/api/alerts":
                    return self._send(200, api_alerts(conn))
                if path == "/api/runs":
                    return self._send(200, {"items": db.rows(conn.execute(
                        "SELECT * FROM runs ORDER BY id DESC LIMIT 50"))})
                if path == "/api/settings":
                    return self._send(200, db.get_settings(conn))
                m = re.fullmatch(r"/api/issue/([0-9a-f]{16})", path)
                if m:
                    data = api_issue(conn, m.group(1), qs)
                    return self._send(200 if data else 404, data or {"error": "issue não encontrada"})
                m = re.fullmatch(r"/api/issue/([0-9a-f]{16})/prompt", path)
                if m:
                    text = prompt_builder.build(
                        conn, collector.logs if collector else None, collector.account_id if collector else None, m.group(1),
                        int(qs["event_id"]) if qs.get("event_id") else None, qs.get("mask", "1") == "1")
                    return self._send(200, {"prompt": text})
                m = re.fullmatch(r"/api/event/(\d+)", path)
                if m:
                    data = api_event(conn, int(m.group(1)))
                    return self._send(200 if data else 404, data or {"error": "evento não encontrado"})
            self._send(404, {"error": "rota não encontrada"})
        except KeyError as exc:
            self._send(404, {"error": str(exc)})
        except Exception as exc:
            log.exception("erro na API %s", path)
            self._send(500, {"error": str(exc)})

    def do_POST(self):
        path = urlparse(self.path).path
        if self._reject_csrf("POST"):
            return
        try:
            if path == "/api/collect":
                if collector is None:
                    return self._send(409, {"error": "Modo demonstração: não há coleta na AWS."})
                collector.trigger()
                return self._send(202, {"ok": True})
            with db.connect() as conn:
                if path == "/api/settings":
                    return self._send(200, api_settings_save(conn, self._body()))
                if path == "/api/backfill":
                    b = self._body()
                    collector.decide_backfill(
                        conn, b.get("mode"), float(b["hours"]) if b.get("hours") else None,
                        int(b["from_ms"]) if b.get("from_ms") else None, bool(b.get("remember")))
                    return self._send(200, collector.backfill_status(conn))
                if path == "/api/search":
                    return self._send(200, api_search(conn, self._body()))
                if path.startswith("/api/alerts"):
                    return self._send(200, api_alerts_post(conn, path, self._body()))
                if collector is None and path.startswith(("/api/search/", "/api/backfill")):
                    b = self._body()
                    if path == "/api/search/prompt":
                        return self._send(200, {"prompt": prompt_builder.build_for_line(
                            None, None, b["log_group"], b["log_stream"], int(b["ts"]), b.get("message", ""),
                            b.get("text", ""), bool(b.get("mask", True)))})
                    return self._send(200, {"context": "", "description": "modo demonstração: sem acesso à AWS"})
                if path in ("/api/search/context", "/api/search/prompt"):
                    b = self._body()
                    group, stream, ts, msg = b["log_group"], b["log_stream"], int(b["ts"]), b.get("message", "")
                    if path.endswith("context"):
                        ctx, desc = prompt_builder.fetch_context(collector.logs, group, stream, ts, msg)
                        return self._send(200, {"context": ctx, "description": desc})
                    return self._send(200, {"prompt": prompt_builder.build_for_line(
                        collector.logs, collector.account_id, group, stream, ts, msg,
                        b.get("text", ""), bool(b.get("mask", True)))})
                if path == "/api/backfill/cancel":
                    collector.cancel_backfill(conn)
                    return self._send(200, collector.backfill_status(conn))
                m = re.fullmatch(r"/api/issue/([0-9a-f]{16})/status", path)
                if m:
                    status = self._body().get("status")
                    if status not in ("open", "resolved", "ignored"):
                        return self._send(400, {"error": "status inválido"})
                    conn.execute(
                        "UPDATE issues SET status = ?, resolved_at = CASE WHEN ? = 'resolved' THEN ? "
                        "ELSE resolved_at END, regressed_at = CASE WHEN ? = 'open' THEN regressed_at "
                        "ELSE NULL END WHERE fingerprint = ?",
                        (status, status, db.now_ms(), status, m.group(1)))
                    conn.commit()
                    return self._send(200, {"ok": True})
            self._send(404, {"error": "rota não encontrada"})
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
        except Exception as exc:
            log.exception("erro na API %s", path)
            self._send(500, {"error": str(exc)})


def check_aws() -> bool:
    """Testa credenciais e permissões antes de subir, com mensagens que dizem o que fazer."""
    import boto3
    from botocore.exceptions import (ClientError, NoCredentialsError, ProfileNotFound,
                                     SSLError, TokenRetrievalError, UnauthorizedSSOTokenError)
    who = f"profile '{AWS_PROFILE}'" if AWS_PROFILE else "credenciais padrão da AWS"
    try:
        session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
        ident = session.client("sts").get_caller_identity()
        session.client("logs").describe_log_groups(limit=1)
        log.info("AWS ok: conta %s, %s, região %s", ident["Account"], who, AWS_REGION)
        return True
    except ProfileNotFound:
        hint = f"O profile '{AWS_PROFILE}' não existe nesta máquina. Rode: python configurar.py"
    except (NoCredentialsError,):
        hint = "Nenhuma credencial AWS encontrada. Rode: python configurar.py"
    except (TokenRetrievalError, UnauthorizedSSOTokenError):
        hint = f"A sessão SSO expirou. Rode: aws sso login{' --profile ' + AWS_PROFILE if AWS_PROFILE else ''}"
    except SSLError:
        hint = ("Erro de certificado SSL (antivírus ou proxy inspecionando HTTPS). "
                "Veja 'Erro de certificado SSL' no README.")
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("AccessDenied", "AccessDeniedException", "UnauthorizedOperation"):
            hint = f"Sem permissão ({code}). Peça as permissões da seção 'Permissões na AWS' do README."
        elif code in ("ExpiredToken", "ExpiredTokenException", "InvalidClientTokenId"):
            hint = "Credencial expirada ou inválida. Refaça 'aws configure' ou 'aws sso login'."
        else:
            hint = f"{code}: {exc}"
    except Exception as exc:  # rede, região inválida etc.
        hint = f"{type(exc).__name__}: {exc}"
    log.error("Não consegui acessar a AWS com %s (região %s).\n    → %s\n    O painel abre mesmo assim; "
              "a coleta tenta de novo a cada intervalo.", who, AWS_REGION, hint)
    return False


def main():
    parser = argparse.ArgumentParser(description=f"{APP_NAME} — erros do CloudWatch")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--check", action="store_true", help="só testa o acesso à AWS e sai")
    parser.add_argument("--demo", action="store_true", help="abre com dados fictícios, sem acessar a AWS")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%d/%m %H:%M:%S")
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.ERROR)

    global collector, DEMO, DB_PATH, alert_loop
    if args.demo:
        import demo
        DEMO = True
        DB_PATH = demo.build(DB_PATH.parent / "demo.db")
        db.DB_PATH = DB_PATH
        log.info("Modo demonstração: dados fictícios em %s (nada é lido da AWS)", DB_PATH)
    else:
        ok = check_aws()
        if args.check:
            raise SystemExit(0 if ok else 1)
        db.init()
        collector = Collector()
        collector.start()
    alert_loop = alerts.AlertLoop(collector)
    alert_loop.start()

    server = ThreadingHTTPServer((HOST, args.port), Handler)
    url = f"http://{HOST}:{args.port}/"
    log.info("%s em %s  (profile %s, região %s, banco %s)", APP_NAME, url, AWS_PROFILE or "padrão", AWS_REGION, DB_PATH)
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("encerrando")


if __name__ == "__main__":
    main()
