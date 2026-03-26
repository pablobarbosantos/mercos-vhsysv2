"""
PDV — entry point.

Inicia o servidor FastAPI em background e abre a janela nativa (pywebview).
Para rodar: python -m pdv.main  (da raiz do projeto)
Para empacotar: pyinstaller pdv/pdv.spec
"""

import sys
import os
import time
import logging
import threading

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

# Garante que o diretório pai (raiz do projeto) esteja no sys.path quando
# rodando como script (python -m pdv.main) E como .exe (PyInstaller)
if getattr(sys, "frozen", False):
    _ROOT = os.path.dirname(sys.executable)
else:
    _ROOT = os.path.join(os.path.dirname(__file__), "..")

if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

PORT = 8080


def _start_server():
    import uvicorn
    from pdv.server import app
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


def main():
    # Inicia servidor em background
    t = threading.Thread(target=_start_server, daemon=True)
    t.start()

    # Aguarda servidor subir
    import requests
    for _ in range(20):
        try:
            requests.get(f"http://127.0.0.1:{PORT}/pdv/", timeout=1)
            break
        except Exception:
            time.sleep(0.3)

    # Sync inicial de produtos antes de abrir a janela
    _log = logging.getLogger(__name__)
    _log.info("[PDV] Sincronizando produtos do VHSys...")
    try:
        from pdv.vhsys import sincronizar_produtos
        r = sincronizar_produtos()
        if r["erro"]:
            _log.warning(f"[PDV] Sync falhou: {r['erro']} — abrindo com dados locais")
        else:
            _log.info(f"[PDV] {r['importados']} produtos prontos.")
    except Exception as e:
        _log.warning(f"[PDV] Sync erro: {e} — abrindo com dados locais")

    # Abre janela nativa
    try:
        import webview

        class _WindowApi:
            def __init__(self):
                self._win = None
            def minimize(self):
                if self._win:
                    self._win.minimize()
            def close_window(self):
                if self._win:
                    self._win.destroy()

        _api = _WindowApi()
        window = webview.create_window(
            title="PDV — Vendas Balcão",
            url=f"http://127.0.0.1:{PORT}/pdv/",
            fullscreen=True,
            confirm_close=True,
            background_color="#1a1d23",
            js_api=_api,
        )
        _api._win = window
        webview.start(debug=False)
    except ImportError:
        # pywebview não instalado — fallback para browser
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{PORT}/pdv/")
        logging.getLogger(__name__).warning(
            "pywebview não encontrado. Abrindo no navegador padrão. "
            "Instale com: pip install pywebview"
        )
        input("PDV rodando. Pressione Enter para fechar...")


if __name__ == "__main__":
    main()
