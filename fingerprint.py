"""Classificação, título e agrupamento (fingerprint) de mensagens de erro."""
import hashlib
import json
import re

UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)

# Linhas com nível explícito baixo (INFO/DEBUG/TRACE) que só citam "error" no conteúdo.
_LOW_LEVEL = re.compile(
    r"^\s*\[?(INFO|DEBUG|TRACE)\]?[\s:\t]"
    r"|^[^\n]{0,80}?\t(INFO|DEBUG|TRACE)\t"
    r"|\"(level|severity|levelname)\"\s*:\s*\"(info|debug|trace)\""
    r"|\blevel=(info|debug|trace)\b"
    r"|^\S+\s+\S+\s+\[?(INFO|DEBUG|TRACE)\]?\s",
    re.I,
)

_KEYWORD = re.compile(
    r"error|exception|traceback|fatal|critical|unhandled|timed out|timeout|panic|"
    r"econnrefused|econnreset|status: 5\d\d", re.I)

# "ValueError: x", "botocore.exceptions.ClientError: x", "TypeError: x"
_EXC_LINE = re.compile(r"^[\w.]*(Error|Exception|Exit|Interrupt|Timeout|Warning)(:|$)")

# Prefixos comuns de linhas de Lambda (Python e Node): nível, timestamp ISO, request id.
_PREFIX_TOKEN = re.compile(
    r"^(\[?(ERROR|WARN|WARNING|CRITICAL|FATAL|INFO|DEBUG)\]?|undefined|"
    r"\d{4}-\d{2}-\d{2}[T ][\d:.,]+Z?|" + UUID_RE.pattern + r")$", re.I)

_NORMALIZERS = [
    (UUID_RE, "<uuid>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?"), "<ts>"),
    (re.compile(r"\b\d{1,3}(\.\d{1,3}){3}(:\d+)?\b"), "<ip>"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<email>"),
    (re.compile(r"\b(0x)?[0-9a-f]{8,}\b", re.I), "<hex>"),
    (re.compile(r"\?[^\s\"']+"), "?<qs>"),
    # strings longas ou com dígitos viram placeholder; curtas (ex.: KeyError: 'nome') são mantidas
    (re.compile(r"\"[^\"]{0,400}?\d[^\"]{0,400}?\"|\"[^\"]{31,}\""), "\"<s>\""),
    (re.compile(r"'[^']{0,400}?\d[^']{0,400}?'|'[^']{31,}'"), "'<s>'"),
    (re.compile(r"\d+"), "<n>"),
    (re.compile(r"\s+"), " "),
]

CATEGORIES = {
    "timeout": "Timeout",
    "http_5xx": "HTTP 5xx",
    "fatal": "Fatal / Crash",
    "exception": "Exceção",
    "error": "Erro",
    "warning": "Warning",  # aparece na lista de issues, mas não entra nas estatísticas de erro
}


# Sinais graves mesmo em linha INFO (ex.: pino/Fastify loga "request completed" com statusCode 500 em info)
_SEVERE_ANYWAY = re.compile(r"statuscode\W{0,3}5\d\d|status: 5\d\d|task timed out|status: timeout", re.I)


# Avisos (warnings): nível WARN explícito ou avisos de runtime. O Lambda marca como ERROR tudo que vai para o
# stderr, então "(node:2) MaxListenersExceededWarning" chega com nível ERROR, mas é só um aviso.
_WARNING = re.compile(
    r"^\s*\[?(WARN|WARNING)\]?[\s:\t]"
    r"|^[^\n]{0,80}?\t(WARN|WARNING)\t"
    r"|\"(level|severity|levelname)\"\s*:\s*\"(warn|warning)\""
    r"|\"level\"\s*:\s*40\b"                      # pino: 40 = warn
    r"|\blevel=(warn|warning)\b"
    r"|^\S+\s+\S+\s+\[?(WARN|WARNING)\]?\s"
    r"|\(node:\d+\)\s+(\[\w+\]\s+)?\w*Warning\b"    # avisos do processo Node
    r"|^[^\n]{0,160}?\b\w*(Deprecation|User|Runtime|Future|Resource|Experimental|MaxListenersExceeded)Warning\b",
    re.I,
)
# Mesmo marcado como aviso, isto é erro de verdade
_REAL_ERROR = re.compile(r"traceback \(most recent call last\)|unhandled|uncaught|\bfatal\b|task timed out|"
                         r"runtime\.exited|out of memory|outofmemory", re.I)


def is_warning(message: str) -> bool:
    return (bool(_WARNING.search(message[:300])) and not _SEVERE_ANYWAY.search(message)
            and not _REAL_ERROR.search(message))


def is_low_level(message: str) -> bool:
    return bool(_LOW_LEVEL.search(message[:300])) and not _SEVERE_ANYWAY.search(message)


def classify(message: str) -> str:
    if is_warning(message):
        return "warning"
    m = message.lower()
    if "task timed out" in m or "status: timeout" in m or "timed out" in m or "etimedout" in m \
            or "timeouterror" in m or "readtimeout" in m:
        return "timeout"
    if re.search(r"status: 5\d\d|statuscode\W{0,3}5\d\d|\bhttp/\d(\.\d)? 5\d\d\b|\b5\d\d internal", m):
        return "http_5xx"
    if re.search(r"\bfatal\b|\bcritical\b|panic:|out of memory|outofmemory|runtime\.exited|"
                 r"segmentation fault|killed", m):
        return "fatal"
    if "traceback" in m or "exception" in m or re.search(r"\w+error:", m):
        return "exception"
    return "error"


_TRACE_CTX = re.compile(r"^\[dd\.trace_id=\S+ dd\.span_id=\S+\]\s*")
_UNDEFINED_REQ = re.compile(r"^undefined\s+(?=(ERROR|WARN|INFO)\b)", re.I)

# Linhas que são pedaços de um traceback logado linha a linha.
_FRAGMENT = re.compile(
    r"Traceback \(most recent call last\)|^\s*File \"|^\s+at \S|^\s*raise\b|exception occurred|"
    r"direct cause of the following|^\(Background on this error|^\s*\^+\s*$|^[\w.]+(Error|Exception)\b",
    re.M)
TRACEBACK_START = re.compile(r"Traceback \(most recent call last\)|During handling of the above|direct cause of the following")


def is_fragment(message: str) -> bool:
    return bool(_FRAGMENT.search(_TRACE_CTX.sub("", message.strip())))


def _strip_prefix(line: str) -> str:
    line = _UNDEFINED_REQ.sub("", _TRACE_CTX.sub("", line.strip()))
    parts = re.split(r"\t+", line.strip())
    while len(parts) > 1 and _PREFIX_TOKEN.match(parts[0].strip()):
        parts.pop(0)
    out = "\t".join(parts).strip()
    out = re.sub(r"^\[?(ERROR|CRITICAL|FATAL)\]?[:\s]+", "", out, flags=re.I)
    return out


def _shorten(text: str, size: int = 240) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= size else text[: size - 1] + "…"


def _json_title(message: str) -> str | None:
    """Logs estruturados (pino/Fastify, structlog…): título = tipo + mensagem do erro, não o JSON bruto."""
    text = message.strip()
    if not text.startswith("{"):
        return None
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    err = obj.get("err") or obj.get("error") or obj.get("exception")
    if isinstance(err, dict) and (err.get("message") or err.get("type") or err.get("name")):
        etype = err.get("type") or err.get("name") or "Error"
        return f"{etype}: {err.get('message', '')}".strip()
    msg = obj.get("msg") or obj.get("message") or obj.get("errorMessage")
    if isinstance(err, str) and err:
        return f"{msg}: {err}" if msg and msg != err else err
    return str(msg) if msg else None


def extract_title(message: str) -> str:
    lines = [ln for ln in (l.strip() for l in message.strip().splitlines()) if ln]
    if not lines:
        return "(mensagem vazia)"

    jt = _json_title(message)
    if jt:
        return _shorten(jt)

    # Traceback Python: a última linha "XxxError: ..." diz o que aconteceu.
    if any("Traceback (most recent call last)" in ln for ln in lines):
        for ln in reversed(lines):
            ln2 = _strip_prefix(ln)
            if _EXC_LINE.match(ln2):
                return _shorten(ln2)

    # Erro estruturado de Lambda: {"errorType": "...", "errorMessage": "..."}
    m = re.search(r'"errorType"\s*:\s*"([^"]+)"', message)
    m2 = re.search(r'"errorMessage"\s*:\s*"((?:[^"\\]|\\.)*)"', message)
    if m or m2:
        etype = m.group(1) if m else "Error"
        emsg = m2.group(1) if m2 else ""
        return _shorten(f"{etype}: {emsg}" if emsg else etype)

    for ln in lines:
        if _KEYWORD.search(ln):
            return _shorten(_strip_prefix(ln))
    return _shorten(_strip_prefix(lines[0]))


def normalize(title: str) -> str:
    out = title
    for pattern, repl in _NORMALIZERS:
        out = pattern.sub(repl, out)
    return out.strip().lower()


def fingerprint(service: str, category: str, title: str) -> str:
    raw = f"{service}|{category}|{normalize(title)}"
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16]


def service_of(log_group: str) -> str:
    if log_group.startswith("/aws/lambda/"):
        return log_group[len("/aws/lambda/"):]
    if log_group.startswith("/ecs/"):
        return "ecs:" + log_group[len("/ecs/"):]
    if log_group.startswith("API-Gateway-Execution-Logs_"):
        return "apigw:" + log_group[len("API-Gateway-Execution-Logs_"):]
    m = re.match(r"/aws/rds/instance/([^/]+)/(\w+)", log_group)
    if m:
        return f"rds:{m.group(1)}"
    return log_group.lstrip("/")


def kind_of(log_group: str) -> str:
    if log_group.startswith("/aws/lambda/"):
        return "AWS Lambda"
    if log_group.startswith("/ecs/"):
        return "ECS (container)"
    if log_group.startswith("API-Gateway-Execution-Logs_"):
        return "API Gateway"
    if log_group.startswith("/aws/rds/"):
        return "RDS"
    if log_group.startswith("/aws-glue/"):
        return "AWS Glue"
    if "/states/" in log_group:
        return "Step Functions"
    return "CloudWatch Logs"
