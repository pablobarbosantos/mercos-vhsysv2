#!/usr/bin/env python3
"""Abre o painel Admin em janela própria (sem navegador)."""
import sys
import time

import requests
import webview

SERVER_URL = "http://localhost:8000"
ADMIN_URL = f"{SERVER_URL}/admin"


def aguardar_servidor(timeout=30):
    for _ in range(timeout):
        try:
            if requests.get(SERVER_URL, timeout=2).status_code < 500:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


if __name__ == "__main__":
    if not aguardar_servidor():
        print("Servidor não respondeu em 30s. Verifique se o serviço mercos-main está rodando:")
        print("  systemctl status mercos-main")
        sys.exit(1)

    webview.create_window(
        "Admin — Pablo Agro",
        ADMIN_URL,
        width=1400,
        height=900,
        min_size=(1024, 600),
    )
    webview.start()
