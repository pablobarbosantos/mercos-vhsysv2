"""
consulta_vhsys — entry point.

Inicia o servidor FastAPI em background e abre janela nativa (pywebview).
Para rodar: python -m consulta_vhsys.main  (da raiz do projeto)
Para empacotar: pyinstaller consulta_vhsys/consulta_vhsys.spec
"""

import sys
import os
import time
import logging
import threading

_log_handlers = [logging.StreamHandler()]
if getattr(sys, "frozen", False):
    _log_file = os.path.join(os.path.dirname(sys.executable), "consulta_vhsys.log")
    _log_handlers.append(logging.FileHandler(_log_file, encoding="utf-8"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=_log_handlers,
)

# Garante raiz do projeto no sys.path (script E .exe)
if getattr(sys, "frozen", False):
    _ROOT = os.path.dirname(sys.executable)
else:
    _ROOT = os.path.join(os.path.dirname(__file__), "..")

if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

PORT = 8082


# ── Servidor FastAPI ───────────────────────────────────────────────────────────

def _start_server():
    try:
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w")
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w")
        import uvicorn
        from consulta_vhsys.server import app
        uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
    except Exception as e:
        logging.getLogger(__name__).error(f"[Servidor] falhou: {e}", exc_info=True)


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    _log = logging.getLogger(__name__)

    # Inicia servidor em background
    t = threading.Thread(target=_start_server, daemon=True)
    t.start()

    # Aguarda servidor subir
    import requests
    _log.info("[CONSULTA] Aguardando servidor na porta %d…", PORT)
    for _ in range(30):
        try:
            requests.get(f"http://127.0.0.1:{PORT}/", timeout=1)
            break
        except Exception:
            time.sleep(0.3)

    _log.info("[CONSULTA] Servidor pronto.")

    try:
        import webview

        class _Api:
            def __init__(self): self._win = None
            def minimize(self):
                if self._win: self._win.minimize()
            def close_window(self):
                if self._win: self._win.destroy()

        _api = _Api()
        window = webview.create_window(
            title="Consulta VHSys",
            url=f"http://127.0.0.1:{PORT}/",
            width=1000,
            height=680,
            min_size=(800, 560),
            fullscreen=False,
            confirm_close=False,
            background_color="#0f1117",
            js_api=_api,
        )
        _api._win = window

        def _on_closing():
            confirmado = window.create_confirmation_dialog(
                "Fechar Consulta VHSys",
                "Deseja sincronizar as alterações pendentes antes de fechar?",
            )
            if confirmado:
                from consulta_vhsys.services.sync_service import sincronizar_sujos
                r = sincronizar_sujos()
                _log.info("[CONSULTA] Sync final ao fechar: %s", r)
            return True  # permite fechar independente da escolha

        window.events.closing += _on_closing
        webview.start(debug=False)

    except ImportError:
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{PORT}/")
        _log.warning(
            "pywebview não encontrado. Abrindo no navegador. "
            "Instale com: pip install pywebview"
        )
        input("Consulta VHSys rodando. Pressione Enter para fechar…")


if __name__ == "__main__":
    main()
