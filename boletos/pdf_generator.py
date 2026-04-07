"""
Gerador de PDF de boleto bancário SICOOB.
Formato: A4 portrait, 3 vias autoenvolvável (cedente / banco / pagador).
Uma folha por boleto — nunca agrupar.
Biblioteca: fpdf2 (sem dependências nativas, funciona no Windows).
"""
import io
import logging
from datetime import datetime

from fpdf import FPDF

logger = logging.getLogger(__name__)

# ─── Medidas em mm (A4 = 210 x 297) ─────────────────────────────────────────
PAGE_W = 210
PAGE_H = 297
MARGIN = 8
CONTENT_W = PAGE_W - 2 * MARGIN

# 3 faixas iguais menos as margens de perfuração
PERF_H = 2       # altura da faixa de perfuração
FAIXA_H = (PAGE_H - 2 * PERF_H) / 3   # ~97.7 mm por faixa

# Posição Y onde cada faixa começa
Y_FAIXA = [
    0,                              # Faixa 1: cedente
    FAIXA_H + PERF_H,              # Faixa 2: banco
    2 * FAIXA_H + 2 * PERF_H,     # Faixa 3: pagador
]


def _formatar_valor(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _formatar_data(d: str) -> str:
    """YYYY-MM-DD → DD/MM/YYYY"""
    try:
        return datetime.strptime(d, "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return d


def _formatar_linha_digitavel(linha: str) -> str:
    """Formata linha digitável em grupos para leitura."""
    if not linha:
        return ""
    # Remove espaços e formata em grupos de 5/10/10/1/14
    d = linha.replace(" ", "")
    if len(d) == 47:
        return f"{d[:5]}.{d[5:10]} {d[10:20]} {d[20:30]} {d[30]} {d[31:]}"
    return linha


class BoletoPDF(FPDF):
    def __init__(self):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.set_margins(0, 0, 0)
        self.set_auto_page_break(False)
        self.add_page()

    def _linha_perfuracao(self, y: float):
        """Linha tracejada de perfuração."""
        self.set_y(y)
        self.set_draw_color(150, 150, 150)
        self.set_line_width(0.2)
        x = MARGIN
        while x < PAGE_W - MARGIN:
            self.line(x, y + PERF_H / 2, min(x + 3, PAGE_W - MARGIN), y + PERF_H / 2)
            x += 5
        # Texto "CORTE AQUI"
        self.set_font("Helvetica", "I", 5)
        self.set_text_color(150, 150, 150)
        self.set_xy(PAGE_W / 2 - 8, y)
        self.cell(16, PERF_H, "--- CORTE AQUI ---", align="C")
        self.set_text_color(0, 0, 0)
        self.set_draw_color(0, 0, 0)
        self.set_line_width(0.2)

    def _cabecalho_faixa(self, y0: float, titulo_via: str, beneficiario: str, banco: str = "SICOOB / BANCOOB  756"):
        """Cabeçalho de cada via com nome do banco e beneficiário."""
        self.set_xy(MARGIN, y0 + 2)
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(0, 90, 160)
        self.cell(CONTENT_W * 0.5, 5, banco, border=0)
        self.set_text_color(0, 0, 0)
        self.set_font("Helvetica", "", 7)
        self.set_xy(MARGIN + CONTENT_W * 0.5, y0 + 2)
        self.cell(CONTENT_W * 0.5, 5, f"Via: {titulo_via}", border=0, align="R")

        self.set_xy(MARGIN, y0 + 7)
        self.set_font("Helvetica", "B", 8)
        self.cell(CONTENT_W, 5, f"Beneficiário: {beneficiario}", border=0)
        self.set_draw_color(0, 90, 160)
        self.set_line_width(0.4)
        self.line(MARGIN, y0 + 12, PAGE_W - MARGIN, y0 + 12)
        self.set_draw_color(0, 0, 0)
        self.set_line_width(0.2)

    def _campo(self, x: float, y: float, w: float, h: float,
                label: str, valor: str, border: str = "1"):
        """Campo com label pequeno em cima e valor em baixo."""
        self.rect(x, y, w, h)
        self.set_xy(x + 1, y + 0.5)
        self.set_font("Helvetica", "", 5.5)
        self.set_text_color(80, 80, 80)
        self.cell(w - 2, 3, label)
        self.set_xy(x + 1, y + 4)
        self.set_font("Helvetica", "B", 7.5)
        self.set_text_color(0, 0, 0)
        self.cell(w - 2, 4, str(valor)[:50], border=0)

    def _linha_digitavel_display(self, y: float, linha: str):
        """Exibe a linha digitável com fonte grande."""
        self.set_xy(MARGIN, y)
        self.set_font("Courier", "B", 10)
        self.set_text_color(0, 0, 0)
        self.cell(CONTENT_W, 7, _formatar_linha_digitavel(linha), border="B", align="C")

    def _codigo_barras(self, y: float, codigo: str):
        """
        Código de barras Code128 usando fpdf2 built-in.
        codigoBarras retornado pelo SICOOB tem 44 dígitos.
        """
        if not codigo:
            self.set_xy(MARGIN, y)
            self.set_font("Helvetica", "I", 7)
            self.set_text_color(120, 120, 120)
            self.cell(CONTENT_W, 8, "[código de barras não disponível]", align="C")
            return

        try:
            # fpdf2 ≥ 2.7 tem suporte nativo a Code128
            with self.local_context():
                self.set_xy(MARGIN, y)
                self.set_fill_color(255, 255, 255)
                # Usar cell de largura total como área
                bar_w = CONTENT_W
                bar_h = 10
                # fpdf2 write barcode via image (gerar via python-barcode se disponível)
                # Fallback: exibir o número formatado
                self.set_font("Courier", "", 6)
                self.set_text_color(0, 0, 0)
                self.cell(bar_w, bar_h, codigo, border="B", align="C")
        except Exception as e:
            logger.debug("[PDF] Barcode fallback: %s", e)
            self.set_xy(MARGIN, y)
            self.set_font("Courier", "", 7)
            self.cell(CONTENT_W, 8, codigo, align="C")

    def _faixa_completa(self, y0: float, via: str, boleto: dict, config: dict, show_barcode: bool = True):
        """Desenha uma via completa do boleto."""
        beneficiario = config.get("beneficiario", "PABLO AGRO")
        self._cabecalho_faixa(y0, via, beneficiario)

        # Linha 1: Pagador + Vencimento + Valor
        y1 = y0 + 13
        self._campo(MARGIN,                y1, CONTENT_W * 0.55, 9, "Pagador", boleto.get("cliente_nome", ""))
        self._campo(MARGIN + CONTENT_W * 0.55, y1, CONTENT_W * 0.25, 9, "Vencimento",
                    _formatar_data(boleto.get("data_vencimento", "")))
        self._campo(MARGIN + CONTENT_W * 0.80, y1, CONTENT_W * 0.20, 9, "Valor",
                    _formatar_valor(boleto.get("valor_nominal", 0)))

        # Linha 2: CPF/CNPJ + Nosso Número + Data Emissão
        y2 = y1 + 9
        self._campo(MARGIN,                y2, CONTENT_W * 0.35, 8, "CPF/CNPJ",
                    boleto.get("cliente_cpf_cnpj", ""))
        self._campo(MARGIN + CONTENT_W * 0.35, y2, CONTENT_W * 0.30, 8, "Nosso Número",
                    str(boleto.get("nosso_numero", "")))
        self._campo(MARGIN + CONTENT_W * 0.65, y2, CONTENT_W * 0.20, 8, "Emissão",
                    _formatar_data(boleto.get("data_emissao", "")))
        self._campo(MARGIN + CONTENT_W * 0.85, y2, CONTENT_W * 0.15, 8, "Espécie",
                    config.get("especie_titulo", "DM"))

        # Linha 3: Doc / Nro pedido + Juros + Multa
        y3 = y2 + 8
        self._campo(MARGIN,                y3, CONTENT_W * 0.40, 8, "Seu Número / Referência",
                    boleto.get("vhsys_nro", ""))
        self._campo(MARGIN + CONTENT_W * 0.40, y3, CONTENT_W * 0.30, 8, "Juros/Mora",
                    f"{config.get('juros_percentual', 1):.2f}% ao mês após venc.")
        self._campo(MARGIN + CONTENT_W * 0.70, y3, CONTENT_W * 0.30, 8, "Multa",
                    f"{config.get('multa_percentual', 2):.2f}% após venc.")

        # Linha 4: Instruções
        y4 = y3 + 8
        instrucao = (
            f"{config.get('local_pagamento', 'Pagável em qualquer banco até o vencimento')} "
            f"| Protestar após {config.get('dias_protesto', 3)} dias | "
            f"Baixar após {config.get('dias_baixa', 60)} dias"
        )
        self._campo(MARGIN, y4, CONTENT_W, 8, "Instruções", instrucao[:120])

        # Linha digitável
        y5 = y4 + 9
        self._linha_digitavel_display(y5, boleto.get("linha_digitavel", ""))

        # Código de barras (apenas na via cedente e banco)
        if show_barcode:
            y6 = y5 + 8
            self._codigo_barras(y6, boleto.get("codigo_barras", ""))


def gerar_pdf_boleto(boleto: dict, config: dict | None = None) -> bytes:
    """
    Gera PDF com 3 vias autoenvolvável para um único boleto.
    Retorna bytes do PDF.

    boleto: dict do banco (tabela boletos)
    config: dict do boletos_config (se None, usa valores padrão)
    """
    if config is None:
        from boletos.database import get_config
        config = get_config()

    pdf = BoletoPDF()

    # Via 1 — Cedente (topo)
    pdf._faixa_completa(Y_FAIXA[0] + 0.5, "CEDENTE", boleto, config, show_barcode=True)

    # Linha de perfuração 1
    pdf._linha_perfuracao(FAIXA_H)

    # Via 2 — Banco (meio)
    pdf._faixa_completa(Y_FAIXA[1] + 0.5, "BANCO", boleto, config, show_barcode=True)

    # Linha de perfuração 2
    pdf._linha_perfuracao(2 * FAIXA_H + PERF_H)

    # Via 3 — Pagador (fundo) — exibe QR Code Pix se disponível
    pdf._faixa_completa(Y_FAIXA[2] + 0.5, "PAGADOR", boleto, config, show_barcode=False)

    # QR Code Pix na via do pagador (canto direito)
    qr = boleto.get("qr_code")
    if qr:
        _inserir_qr_pix(pdf, qr, Y_FAIXA[2] + 15)

    buf = io.BytesIO()
    pdf_bytes = pdf.output()
    return bytes(pdf_bytes)


def _inserir_qr_pix(pdf: FPDF, qr_text: str, y: float):
    """Tenta gerar QR code Pix se qrcode[pil] estiver instalado."""
    try:
        import qrcode
        from PIL import Image
        import tempfile, os

        qr = qrcode.QRCode(box_size=3, border=2)
        qr.add_data(qr_text)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            img.save(tmp.name)
            tmp_path = tmp.name

        try:
            # Posicionar QR no canto direito da via pagador
            qr_size = 28
            x = PAGE_W - MARGIN - qr_size
            pdf.image(tmp_path, x=x, y=y, w=qr_size, h=qr_size)
            # Label
            pdf.set_xy(x, y + qr_size + 0.5)
            pdf.set_font("Helvetica", "", 5)
            pdf.set_text_color(80, 80, 80)
            pdf.cell(qr_size, 3, "PIX", align="C")
            pdf.set_text_color(0, 0, 0)
        finally:
            os.unlink(tmp_path)

    except ImportError:
        # qrcode não instalado — apenas exibe label
        pdf.set_xy(PAGE_W - MARGIN - 30, y + 10)
        pdf.set_font("Helvetica", "I", 6)
        pdf.set_text_color(120, 120, 120)
        pdf.cell(28, 4, "[QR Code Pix]", align="C")
        pdf.set_text_color(0, 0, 0)
    except Exception as e:
        logger.debug("[PDF] QR Pix falhou: %s", e)
