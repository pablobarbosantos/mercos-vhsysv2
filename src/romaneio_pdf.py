"""Geração de PDF para romaneio de entrega."""

from fpdf import FPDF
from datetime import datetime


def _brl(v) -> str:
    try:
        return f"R$ {float(v or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "R$ 0,00"


def _fmt_data(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%d/%m/%Y")
    except Exception:
        return iso or ""


class _PDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 14)
        self.cell(0, 8, "ROMANEIO DE ENTREGA", align="C", new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 9)
        self.set_text_color(120, 120, 120)
        self.cell(0, 5, f"Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')}", align="C", new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)
        self.ln(2)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(160, 160, 160)
        self.cell(0, 6, f"Página {self.page_no()} / {{nb}}", align="C")
        self.set_text_color(0, 0, 0)


def gerar_pdf(rom: dict) -> bytes:
    pdf = _PDF(orientation="P", unit="mm", format="A4")
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_margins(15, 10, 15)

    # ── Cabeçalho do romaneio ────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(0, 7, "", fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.set_y(pdf.get_y() - 7)

    col_w = 90
    pdf.cell(col_w, 7, f"Romaneio Nº: {rom['id']}", fill=True)
    pdf.cell(col_w, 7, f"Data: {_fmt_data(rom.get('data') or rom.get('criado_em', ''))}", fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.cell(col_w, 7, f"Motorista: {rom.get('motorista') or '-'}", fill=True)
    pdf.cell(col_w, 7, f"Veiculo: {rom.get('veiculo') or '-'}", fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    # ── Sumário ──────────────────────────────────────────────────────────────
    paradas = rom.get("paradas", [])
    total_valor = sum(float(p.get("valor") or 0) for p in paradas)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, f"Total de paradas: {len(paradas)}   |   Valor total: {_brl(total_valor)}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    # ── Tabela de paradas ────────────────────────────────────────────────────
    header_cols = [("Nº", 10), ("Pedido", 18), ("Cliente", 65), ("Cidade", 30), ("Valor", 22), ("NF-e", 10), ("OK", 10)]
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_fill_color(30, 80, 180)
    pdf.set_text_color(255, 255, 255)
    for label, w in header_cols:
        pdf.cell(w, 6, label, fill=True, border=0)
    pdf.ln()

    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 8)
    fill = False
    for i, p in enumerate(sorted(paradas, key=lambda x: x.get("ordem", 0)), start=1):
        pdf.set_fill_color(245, 248, 255) if fill else pdf.set_fill_color(255, 255, 255)
        nfe = "Sim" if p.get("precisa_nfe") else "Não"
        cli = (p.get("cliente") or "-")[:32]
        cidade = (p.get("cidade") or "-")[:18]
        valores = [
            (str(i),               10),
            (str(p.get("numero") or p.get("mercos_id") or ""), 18),
            (cli,                  65),
            (cidade,               30),
            (_brl(p.get("valor")), 22),
            (nfe,                  10),
            ("",                   10),
        ]
        for txt, w in valores:
            pdf.cell(w, 6, txt, fill=True, border=0)
        pdf.ln()
        fill = not fill

    # ── Rodapé com linha de assinatura ───────────────────────────────────────
    pdf.ln(8)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, "Ciente da entrega:", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(8)
    pdf.cell(80, 0.3, "", border="T")
    pdf.cell(10)
    pdf.cell(80, 0.3, "", border="T")
    pdf.ln(4)
    pdf.set_font("Helvetica", "I", 7)
    pdf.cell(80, 4, "Motorista", align="C")
    pdf.cell(10)
    pdf.cell(80, 4, "Conferente", align="C")

    return bytes(pdf.output())
