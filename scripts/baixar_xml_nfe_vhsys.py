"""
VERSÃO FINAL (ANTI-CLOUDFLARE REAL)
- Usa Chrome REAL com perfil persistente
- Login manual apenas 1x
- Não usa storage_state
- Estável contra Cloudflare
"""

import time
import logging
from pathlib import Path
from playwright.sync_api import sync_playwright

# CONFIG
DOWNLOAD_PATH = Path(__file__).parent / "nfe_emitidas"
USER_DATA_DIR = "C:/chrome-profile-vhsys"  # pasta do perfil
HEADLESS = False

BASE_URL = "https://app.vhsys.com.br"
LISTA_URL = f"{BASE_URL}/index.php?Secao=Vendas.Emitir&Modulo=Vendas"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def abrir_browser(p):
    browser = p.chromium.launch_persistent_context(
        user_data_dir=USER_DATA_DIR,
        headless=HEADLESS,
        args=[
            "--start-maximized",
            "--disable-blink-features=AutomationControlled"
        ]
    )

    page = browser.pages[0] if browser.pages else browser.new_page()
    return browser, page


def ir_para_lista(page):
    page.goto(LISTA_URL)
    page.wait_for_selector("table tbody tr")


def baixar_xml(page, linha, numero, pasta):
    try:
        linha.locator("td:last-child button, td:last-child a").first.click()

        page.locator("text=Abrir Opções").click()

        modal = page.locator(".modal:visible").first
        modal.wait_for(state="visible")

        with page.expect_download() as dl:
            modal.locator("text=Baixar XML").click()

        download = dl.value
        caminho = pasta / f"nfe_{numero}.xml"
        download.save_as(caminho)

        log.info(f"✓ {numero}")

        page.keyboard.press("Escape")
        return True

    except Exception as e:
        log.warning(f"✗ {numero} -> {e}")
        page.keyboard.press("Escape")
        return False


def main():
    DOWNLOAD_PATH.mkdir(exist_ok=True)

    with sync_playwright() as p:
        browser, page = abrir_browser(p)

        print("\n👉 Se for a PRIMEIRA vez:")
        print("1. Faça login manual no VHSYS")
        print("2. Resolva o Cloudflare")
        print("3. Depois volte aqui e aperte ENTER")
        input("\nPressione ENTER para continuar...")

        ir_para_lista(page)

        pagina = 1
        total = 0

        while True:
            log.info(f"Página {pagina}")

            linhas = page.locator("table tbody tr")
            count = linhas.count()

            for i in range(count):
                linha = linhas.nth(i)
                numero = linha.locator("td").nth(0).inner_text().strip()

                baixar_xml(page, linha, numero, DOWNLOAD_PATH)
                total += 1

                page.wait_for_timeout(800)

            next_btn = page.locator("li.next:not(.disabled) a")
            if next_btn.is_visible():
                next_btn.click()
                page.wait_for_selector("table tbody tr")
                pagina += 1
            else:
                break

        browser.close()

    log.info(f"Finalizado. Total: {total}")


if __name__ == "__main__":
    main()
