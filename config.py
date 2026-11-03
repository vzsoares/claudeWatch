"""Configuração fixa do monitor. O que pode mudar em tempo de execução fica na tabela settings.

Configuração por instalação: arquivo `.env` na pasta do projeto (veja `.env.example`).
Variáveis de ambiente de verdade têm prioridade sobre o `.env`.
"""
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


_load_env_file(BASE_DIR / ".env")


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or default


def _data_dir() -> Path:
    # Pasta de dados do usuário, fora do projeto: OneDrive/Dropbox/iCloud travam/corrompem SQLite em uso.
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "cloudwatch-monitor"


DB_PATH = Path(_env("MONITOR_DB") or _data_dir() / "monitor.db")

HOST = "127.0.0.1"
PORT = int(_env("MONITOR_PORT", "8765"))
# reinicia sozinho quando o código muda (só quando aberto pelo iniciar.bat/iniciar.sh)
AUTO_RELOAD = _env("MONITOR_AUTO_RELOAD", "1") == "1"

# Nome exibido no painel/relatório e o nome do sistema citado nos prompts para o Claude.
APP_NAME = _env("MONITOR_APP_NAME", "CloudWatch Monitor")
SYSTEM_NAME = _env("MONITOR_SYSTEM_NAME", "")

# Sem profile definido, o boto3 usa a cadeia padrão da AWS (AWS_PROFILE, profile "default",
# variáveis AWS_ACCESS_KEY_ID/…, SSO, role da máquina).
AWS_PROFILE = _env("MONITOR_AWS_PROFILE") or _env("AWS_PROFILE")


def _resolve_region() -> str:
    explicit = _env("MONITOR_AWS_REGION") or _env("AWS_REGION") or _env("AWS_DEFAULT_REGION")
    if explicit:
        return explicit
    try:  # região configurada no próprio profile (aws configure)
        import boto3
        return boto3.Session(profile_name=AWS_PROFILE).region_name or "us-east-1"
    except Exception:
        return "us-east-1"


AWS_REGION = _resolve_region()

# Certificado extra para antivírus/proxy que inspeciona HTTPS (ex.: Norton). Vale para boto3 e AWS CLI.
CA_BUNDLE = _env("MONITOR_CA_BUNDLE")
if CA_BUNDLE:
    os.environ.setdefault("AWS_CA_BUNDLE", CA_BUNDLE)

# Logs Insights aceita no máximo 50 log groups por consulta e devolve no máximo 10.000 linhas.
INSIGHTS_MAX_GROUPS = 50
INSIGHTS_MAX_ROWS = 10_000
INSIGHTS_WORKERS = 4
# Sobreposição entre janelas para pegar eventos ingeridos com atraso (duplicados são descartados).
WINDOW_OVERLAP_MS = 3 * 60 * 1000

INTERVAL_CHOICES = [1, 2, 5, 10, 15, 30, 60]

# Valores iniciais das configurações do painel (⚙). Só valem na criação do banco; depois, edite no painel.
DEFAULT_SETTINGS = {
    "interval_min": _env("MONITOR_INTERVAL_MIN", "5"),
    "first_lookback_hours": _env("MONITOR_FIRST_LOOKBACK_HOURS", "6"),
    # Lacunas (monitor fechado) até este tamanho são recuperadas sem perguntar.
    "auto_recover_hours": "24",
    # "1" = recupera qualquer lacuna inteira sem perguntar.
    "gap_always_recover": "0",
    "retention_days": _env("MONITOR_RETENTION_DAYS", "30"),
    "ignore_low_level": "1",
    # Regex no padrão do Logs Insights (RE2). Não use "/" sem escapar.
    "error_regex": (
        r"(?i)(error|exception|traceback|fatal|critical|unhandled|task timed out|"
        r"status: timeout|runtime\.exited|out of memory|outofmemory|panic:|"
        r"econnrefused|econnreset|etimedout|status: 5\d\d|statuscode\W{0,3}5\d\d)"
    ),
    "exclude_regex": (
        r"(?i)(\"?errors?\"?\s*[:=]\s*(null|none|false|0|\[\]|\{\}|\"\")(\s|,|\}|$)|"
        r"no errors?|0 errors?|without errors?|error_count\W{0,3}0\b|serverless-plugin-warmup|warmup)"
    ),
    "group_exclude_regex": _env("MONITOR_GROUP_EXCLUDE",
                                r"warmup-plugin|containerinsights|RDSOSMetrics|/performance$"),
    "group_include_regex": _env("MONITOR_GROUP_INCLUDE", ""),
}
