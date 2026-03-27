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

_log_handlers = [logging.StreamHandler()]
if getattr(sys, "frozen", False):
    _log_file = os.path.join(os.path.dirname(sys.executable), "pdv.log")
    _log_handlers.append(logging.FileHandler(_log_file, encoding="utf-8"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=_log_handlers,
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

# Caminho da logo para splash (funciona em script e em .exe)
def _logo_path():
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(__file__)
    return os.path.join(base, "assets", "logo.png")


# ── Splash screen (Tkinter) ───────────────────────────────────────────────────

def _mostrar_splash():
    """Exibe janela de splash centralizada enquanto o sistema carrega."""
    try:
        import tkinter as tk
        from PIL import Image, ImageTk  # Pillow

        root = tk.Tk()
        root.overrideredirect(True)          # sem barra de título
        root.configure(bg="#0d1117")
        root.attributes("-topmost", True)

        img_path = _logo_path()
        if os.path.exists(img_path):
            img = Image.open(img_path)
            # Redimensiona mantendo proporção — máx 480x480
            img.thumbnail((480, 480), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            lbl_img = tk.Label(root, image=photo, bg="#0d1117")
            lbl_img.image = photo
            lbl_img.pack(padx=40, pady=(40, 8))

        lbl = tk.Label(root, text="Carregando PDV…",
                       font=("Segoe UI", 13), fg="#8b9cb3", bg="#0d1117")
        lbl.pack(pady=(0, 36))

        # Centraliza na tela
        root.update_idletasks()
        w, h = root.winfo_width(), root.winfo_height()
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"+{(sw-w)//2}+{(sh-h)//2}")

        return root
    except Exception as e:
        logging.getLogger(__name__).warning(f"[Splash] {e}")
        return None


def _fechar_splash(root):
    try:
        if root:
            root.destroy()
    except Exception:
        pass


# ── Servidor FastAPI ──────────────────────────────────────────────────────────

def _start_server():
    try:
        # console=False no exe torna sys.stdout/stderr None;
        # uvicorn chama isatty() e crasha — redireciona para devnull
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w")
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w")
        import uvicorn
        from pdv.server import app
        uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
    except Exception as e:
        logging.getLogger(__name__).error(f"[Servidor] falhou ao iniciar: {e}", exc_info=True)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    _log = logging.getLogger(__name__)

    # Mostra splash
    splash = _mostrar_splash()

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
        if splash:
            splash.update()

    # Sync inicial de produtos
    _log.info("[PDV] Sincronizando produtos do VHSys...")
    try:
        from pdv.vhsys import sincronizar_produtos
        if splash:
            splash.update()
        r = sincronizar_produtos()
        if r["erro"]:
            _log.warning(f"[PDV] Sync falhou: {r['erro']} — abrindo com dados locais")
        else:
            _log.info(f"[PDV] {r['importados']} produtos prontos.")
    except Exception as e:
        _log.warning(f"[PDV] Sync erro: {e} — abrindo com dados locais")

    # Fecha splash e abre janela principal
    _fechar_splash(splash)

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
            confirm_close=False,
            background_color="#1a1d23",
            js_api=_api,
        )
        _api._win = window
        webview.start(debug=False)
    except ImportError:
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{PORT}/pdv/")
        _log.warning(
            "pywebview não encontrado. Abrindo no navegador padrão. "
            "Instale com: pip install pywebview"
        )
        input("PDV rodando. Pressione Enter para fechar...")


if __name__ == "__main__":
    main()
