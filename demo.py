"""Dados de demonstração: uma loja fictícia com erros realistas, para experimentar o painel sem AWS.

Uso:  python app.py --demo      (gera o banco de demonstração e abre o painel, sem acessar a AWS)
"""
import hashlib
import random
import sqlite3
from pathlib import Path

import db
import fingerprint as fp

HOUR = 3_600_000
DAY = 24 * HOUR

# (log group, mensagem, ocorrências/dia, status, pico nas últimas horas?)
ISSUES = [
    ("/aws/lambda/checkout-api",
     '[ERROR]\t2026-01-01T00:00:00.000Z\t{rid}\tTraceback (most recent call last):\n'
     '  File "/var/task/handler.py", line 88, in create_order\n    payment = gateway.charge(order.total, card_token)\n'
     '  File "/var/task/payments/gateway.py", line 41, in charge\n    raise PaymentGatewayError(resp.status_code, resp.text)\n'
     'payments.gateway.PaymentGatewayError: (502, "Bad Gateway from payment provider")',
     14, "open", True),
    ("/aws/lambda/checkout-api",
     "Task timed out after 30.03 seconds", 6, "open", True),
    ("/ecs/orders-service",
     '{"level":"error","time":1767225600000,"pid":1,"hostname":"a1b2c3d4e5f6","reqId":"req-{n}",'
     '"err":{"type":"SequelizeConnectionError","message":"connect ETIMEDOUT 10.20.1.15:5432",'
     '"stack":"SequelizeConnectionError: connect ETIMEDOUT\\n    at ConnectionManager.connect (/app/node_modules/sequelize/lib/dialects/postgres/connection-manager.js:184:24)"},'
     '"msg":"Failed to load order {n}"}',
     9, "open", True),
    ("/ecs/orders-service",
     '{"level":"error","time":1767225600000,"pid":1,"hostname":"a1b2c3d4e5f6","err":{"type":"NotFoundError",'
     '"message":"Order {n} not found","statusCode":404},"msg":"GET /v1/orders/{n} failed"}',
     22, "open", False),
    ("/ecs/auth-service",
     "FATAL ERROR: Reached heap limit Allocation failed - JavaScript heap out of memory", 1, "open", False),
    ("/aws/lambda/payments-worker",
     "[ERROR] KeyError: 'customer_id'\nTraceback (most recent call last):\n"
     '  File "/var/task/worker.py", line 57, in process_message\n    customer = body["customer_id"]\nKeyError: \'customer_id\'',
     4, "open", False),
    ("/aws/lambda/inventory-sync",
     "[ERROR] botocore.exceptions.ClientError: An error occurred (ProvisionedThroughputExceededException) "
     "when calling the PutItem operation: Rate of requests exceeds the allowed throughput",
     7, "open", False),
    ("/aws/lambda/notifications-sender",
     "[ERROR] smtplib.SMTPServerDisconnected: Connection unexpectedly closed", 3, "resolved", False),
    ("API-Gateway-Execution-Logs_a1b2c3/v1",
     "(3f2c9a1e-{rid4}) Method completed with status: 504", 5, "open", True),
    ("/aws/lambda/image-resizer",
     "[ERROR] PIL.UnidentifiedImageError: cannot identify image file '/tmp/upload-{n}.bin'", 2, "open", False),
    ("/aws/lambda/report-generator",
     "[ERROR] Runtime.ExitError: RequestId: {rid} Error: Runtime exited with error: signal: killed", 1, "open", False),
    ("/aws/rds/instance/shop-db/error",
     "2026-01-01 00:00:00 UTC::@:[4123]:ERROR:  deadlock detected", 2, "open", False),
    ("/aws/lambda/checkout-api",
     "(node:8) MaxListenersExceededWarning: Possible EventEmitter memory leak detected. 11 error listeners added",
     10, "open", False),
    ("/ecs/storefront-web",
     "Error: Failed to find Server Action \"x\". This request might be from an older or newer deployment.",
     3, "open", False),
]

ALARMS = [
    ("checkout-api-5xx-rate", "ALARM", "AWS/ApplicationELB", "HTTPCode_Target_5XX_Count",
     "Threshold Crossed: 1 datapoint [42.0] was greater than the threshold (10.0)."),
    ("orders-db-connections", "OK", "AWS/RDS", "DatabaseConnections", "Threshold Crossed: no datapoints breaching"),
    ("payments-dlq-messages", "OK", "AWS/SQS", "ApproximateNumberOfMessagesVisible", "OK"),
    ("storefront-latency-p95", "OK", "AWS/ApplicationELB", "TargetResponseTime", "OK"),
    ("auth-service-healthy-hosts", "OK", "AWS/ApplicationELB", "HealthyHostCount", "OK"),
    ("inventory-sync-errors", "INSUFFICIENT_DATA", "AWS/Lambda", "Errors", "Insufficient Data"),
]

EXTRA_GROUPS = ["/aws/lambda/cart-api", "/aws/lambda/search-indexer", "/aws/lambda/coupon-validator",
                "/ecs/admin-panel", "/aws/lambda/webhooks-receiver", "/aws/lambda/email-templates"]


def _fill(template: str, rng: random.Random) -> str:
    return (template.replace("{rid}", "%08x-%04x-4%03x-a%03x-%012x" % (
        rng.getrandbits(32), rng.getrandbits(16), rng.getrandbits(12), rng.getrandbits(12), rng.getrandbits(48)))
        .replace("{rid4}", "%04x" % rng.getrandbits(16))
        .replace("{n}", str(rng.randint(1000, 99999))))


def build(path: Path, seed: int = 7) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    db.DB_PATH = path
    db.init()
    rng = random.Random(seed)
    now = db.now_ms()
    conn = db.connect()

    groups = sorted({g for g, *_ in ISSUES} | set(EXTRA_GROUPS))
    for g in groups:
        conn.execute("INSERT INTO log_groups(name, stored_bytes, retention_days, creation_time, updated_at) "
                     "VALUES (?,?,?,?,?)", (g, rng.randint(10**5, 10**8), 30, now - 400 * DAY, now))

    for idx, (group, template, per_day, status, spike) in enumerate(ISSUES):
        service = fp.service_of(group)
        stream = f"2026/01/01/[$LATEST]{hashlib.md5(group.encode()).hexdigest()}" if "/lambda/" in group \
            else f"{service.split(':')[-1]}/app/{hashlib.md5(group.encode()).hexdigest()[:24]}"
        times = []
        for day in range(7):
            count = max(0, int(rng.gauss(per_day, per_day * 0.3)))
            times += [now - day * DAY - rng.randint(0, DAY) for _ in range(count)]
        if spike:  # pico recente: deixa o gráfico de tempo interessante
            times += [now - rng.randint(0, 3 * HOUR) for _ in range(per_day * 3)]
        times = sorted(t for t in times if t < now)
        if not times:
            continue
        sample = _fill(template, rng)
        category = fp.classify(sample)
        title = fp.extract_title(sample)
        fprint = fp.fingerprint(service, category, title)
        for t in times:
            msg = _fill(template, rng)
            key = hashlib.sha1(f"{group}|{stream}|{t}|{msg}".encode()).hexdigest()
            conn.execute("INSERT OR IGNORE INTO events(event_key, fingerprint, ts, service, log_group, log_stream, "
                         "category, message, collected_at) VALUES (?,?,?,?,?,?,?,?,?)",
                         (key, fprint, t, service, group, stream, category, msg, t))
        regressed = now - 2 * HOUR if idx == 1 else None   # um timeout que "voltou" depois de resolvido
        conn.execute("INSERT OR REPLACE INTO issues(fingerprint, title, category, service, log_group, first_seen, "
                     "last_seen, count, status, created_at, resolved_at, regressed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (fprint, title, category, service, group, times[0], times[-1], len(times), status,
                      times[0], now - 3 * DAY if status == "resolved" else None, regressed))

    for name, state, ns, metric, reason in ALARMS:
        conn.execute("INSERT INTO alarms(name, state, reason, namespace, metric, state_updated_at, seen_at) "
                     "VALUES (?,?,?,?,?,?,?)", (name, state, reason, ns, metric,
                                                 now - (25 * 60_000 if state == "ALARM" else 2 * DAY), now))
    for i in range(12):
        start = now - i * 5 * 60_000
        conn.execute("INSERT INTO runs(kind, started_at, finished_at, window_start, window_end, groups, rows_found, "
                     "events_new, issues_new, status) VALUES ('live',?,?,?,?,?,?,?,?,'ok')",
                     (start - 6_000, start, start - 8 * 60_000, start, len(groups), rng.randint(0, 30),
                      rng.randint(0, 20), 0))
    db.set_setting(conn, "last_window_end", now)
    conn.commit()
    conn.close()
    return path
