#!/usr/bin/env bash
# CloudWatch Monitor - inicializador para Linux e macOS.
# Verifica/instala: Python 3.10+, ambiente virtual + dependências (requirements.txt), AWS CLI (opcional);
# roda o assistente de configuração na 1ª vez e abre o painel.
# Uso: ./iniciar.sh [--port 8765] [--no-browser]
set -uo pipefail
cd "$(dirname "$0")"

OS="$(uname -s)"
INTERACTIVE=0; [ -t 0 ] && INTERACTIVE=1
ok()   { printf '      \033[32mOK\033[0m - %s\n' "$*"; }
warn() { printf '      \033[33m%s\033[0m\n' "$*"; }
die()  { printf '\n\033[31m[ERRO]\033[0m %s\n' "$*" >&2; exit 1; }
confirm() {  # confirm "pergunta" -> 0 se sim (só pergunta com console interativo)
    [ "$INTERACTIVE" = 1 ] || return 1
    read -r -p "      $1 (s/N) " ans; case "$ans" in [sSyY]*) return 0 ;; *) return 1 ;; esac
}
SUDO=""; [ "$(id -u)" != 0 ] && command -v sudo >/dev/null 2>&1 && SUDO="sudo"

pkg_install() {  # instala pacotes com o gerenciador da distribuição
    if command -v apt-get >/dev/null 2>&1; then $SUDO apt-get update && $SUDO apt-get install -y "$@"
    elif command -v dnf >/dev/null 2>&1; then $SUDO dnf install -y "$@"
    elif command -v yum >/dev/null 2>&1; then $SUDO yum install -y "$@"
    elif command -v pacman >/dev/null 2>&1; then $SUDO pacman -S --noconfirm "$@"
    elif command -v zypper >/dev/null 2>&1; then $SUDO zypper install -y "$@"
    else return 1; fi
}

echo
echo "  =============================================="
echo "    CloudWatch Monitor - verificando requisitos"
echo "  =============================================="
echo

# ------------------------------------------------------------------ 1. Python
echo "[1/4] Python 3.10 ou mais novo"
find_python() {
    PY=""
    for cand in python3.13 python3.12 python3.11 python3.10 python3 python; do
        if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
            PY="$(command -v "$cand")"; return 0
        fi
    done
    return 1
}
if ! find_python; then
    warn "Não encontrado."
    if [ "$OS" = "Darwin" ]; then
        if command -v brew >/dev/null 2>&1; then
            confirm "Instalar o Python 3.12 com Homebrew (brew install python@3.12)?" && brew install python@3.12
        else
            warn "Instale o Homebrew (https://brew.sh) ou o Python em https://www.python.org/downloads/macos/"
        fi
    else
        if command -v apt-get >/dev/null 2>&1; then PKGS="python3 python3-venv python3-pip"
        elif command -v pacman >/dev/null 2>&1; then PKGS="python python-pip"
        else PKGS="python3 python3-pip"; fi
        confirm "Instalar o Python pelo gerenciador de pacotes ($PKGS)?" && pkg_install $PKGS
    fi
    find_python || die "Python 3.10+ não encontrado. Instale (veja o README, seção Pré-requisitos) e rode de novo."
fi
ok "Python $("$PY" -c 'import sys; print(sys.version.split()[0])')  ($PY)"

# ------------------------------------------------------------------ 2. Dependências (em .venv)
echo "[2/4] Dependências (requirements.txt)"
if [ ! -x .venv/bin/python ]; then
    echo "      Criando o ambiente virtual .venv ..."
    if ! "$PY" -m venv .venv 2>/dev/null; then
        rm -rf .venv
        warn "Falta o módulo venv do Python."
        if command -v apt-get >/dev/null 2>&1; then
            VER="$("$PY" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
            confirm "Instalar python3-venv (python$VER-venv)?" && pkg_install "python$VER-venv" python3-venv
        fi
        "$PY" -m venv .venv || die "Não consegui criar o ambiente virtual. No Debian/Ubuntu: sudo apt install python3-venv"
    fi
fi
VPY=".venv/bin/python"
"$VPY" -m pip --version >/dev/null 2>&1 || "$VPY" -m ensurepip --upgrade >/dev/null 2>&1
if ! "$VPY" -c "import boto3" 2>/dev/null; then
    echo "      Instalando dependências..."
    "$VPY" -m pip install --disable-pip-version-check --quiet --upgrade pip >/dev/null 2>&1
    "$VPY" -m pip install --disable-pip-version-check -r requirements.txt || die "Falha ao instalar o requirements.txt (verifique internet/proxy)."
fi
"$VPY" -m pip install --disable-pip-version-check --quiet -r requirements.txt >/dev/null 2>&1
ok "boto3 $("$VPY" -c 'import boto3; print(boto3.__version__)')"

# ------------------------------------------------------------------ 3. AWS CLI (opcional)
echo "[3/4] AWS CLI (opcional - só é necessário para login SSO)"
if command -v aws >/dev/null 2>&1; then
    ok "$(aws --version 2>&1 | awk '{print $1}')"
else
    warn "Não instalado. Sem problema: o assistente cadastra a chave de acesso sem ele."
    if [ ! -f .env ]; then
        if [ "$OS" = "Darwin" ] && command -v brew >/dev/null 2>&1; then
            confirm "Instalar o AWS CLI mesmo assim (recomendado se a empresa usa SSO)?" && brew install awscli
        elif [ "$OS" = "Linux" ] && command -v curl >/dev/null 2>&1 && command -v unzip >/dev/null 2>&1; then
            if confirm "Instalar o AWS CLI v2 oficial (recomendado se a empresa usa SSO; pede sudo)?"; then
                ARCH="$(uname -m)"; [ "$ARCH" = "aarch64" ] || ARCH="x86_64"
                TMP="$(mktemp -d)"
                curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-${ARCH}.zip" -o "$TMP/awscliv2.zip" \
                    && unzip -q "$TMP/awscliv2.zip" -d "$TMP" && $SUDO "$TMP/aws/install" --update
                rm -rf "$TMP"
            fi
        fi
    fi
fi

# ------------------------------------------------------------------ 4. Configuração
echo "[4/4] Configuração"
if [ -f .env ]; then
    ok "usando .env  (para mudar: $VPY configurar.py)"
else
    echo "      Primeira execução: vamos configurar."
    "$VPY" configurar.py --if-needed || die "Configuração cancelada. Rode ./iniciar.sh de novo quando quiser."
fi

echo
echo "Iniciando o painel... (Ctrl+C para parar)"
echo
# MONITOR_LAUNCHER=1: o painel pode pedir para ser reiniciado (código 3) quando o código muda
export MONITOR_LAUNCHER=1
while :; do
    "$VPY" app.py "$@" && exit 0
    code=$?
    [ "$code" -eq 3 ] || exit "$code"
    echo
    echo "Código atualizado — reiniciando o painel..."
    echo
    export MONITOR_RELOADED=1
done
