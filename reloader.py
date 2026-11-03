"""Reinício automático quando o código muda (MONITOR_AUTO_RELOAD=1, o padrão).

- .py ou .env alterado → o servidor sai com o código RESTART_CODE e o iniciar.bat/iniciar.sh o sobe de novo
  (espera terminar uma coleta em andamento, para não cortar a gravação no meio).
- arquivo da pasta web/ alterado → só muda `web_version`; o painel percebe e recarrega a página.

Só reinicia sozinho quando foi aberto pelo iniciar.bat/iniciar.sh (que avisam por MONITOR_LAUNCHER=1):
rodando `python app.py` direto, não há quem o suba de novo, então apenas avisa no console.
"""
import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger("reloader")

RESTART_CODE = 3
POLL_S = 1.5
MAX_WAIT_COLLECT_S = 120
ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"


def _snapshot(files) -> dict:
    out = {}
    for f in files:
        try:
            out[f] = f.stat().st_mtime_ns
        except OSError:
            pass
    return out


def _code_files():
    return [*ROOT.glob("*.py"), ROOT / ".env"]


def _web_files():
    return [f for f in WEB.rglob("*") if f.is_file() and "vendor" not in f.parts]


class Reloader:
    def __init__(self, collector=None):
        self.collector = collector
        self.can_restart = os.environ.get("MONITOR_LAUNCHER") == "1"
        self.web_version = self._web_version()
        self._warned = False

    def _web_version(self) -> int:
        snap = _snapshot(_web_files())
        return max(snap.values(), default=0) + len(snap)  # + quantidade: pega arquivo apagado/criado

    def start(self):
        threading.Thread(target=self._loop, name="reloader", daemon=True).start()
        if self.can_restart:
            log.info("reinício automático ligado: ao alterar o código, o painel reinicia sozinho")

    def _loop(self):
        code = _snapshot(_code_files())
        while True:
            time.sleep(POLL_S)
            self.web_version = self._web_version()
            now = _snapshot(_code_files())
            if now == code:
                continue
            changed = sorted({f.name for f in set(now) | set(code) if now.get(f) != code.get(f)})
            if not self.can_restart:
                if not self._warned:
                    log.warning("código alterado (%s): reinicie o painel para usar a versão nova", ", ".join(changed))
                    self._warned = True
                code = now
                continue
            time.sleep(POLL_S)  # o editor pode estar gravando vários arquivos em sequência
            self._wait_collect()
            log.info("código alterado (%s): reiniciando o painel...", ", ".join(changed))
            logging.shutdown()
            os._exit(RESTART_CODE)

    def _wait_collect(self):
        deadline = time.time() + MAX_WAIT_COLLECT_S
        while self.collector is not None and time.time() < deadline and (
                self.collector.state.get("running") or self.collector.backfill_state.get("running")):
            time.sleep(1)
