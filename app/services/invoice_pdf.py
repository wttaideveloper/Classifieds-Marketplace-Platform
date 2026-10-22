"""Draws an invoice, credit note or debit note as a PDF (reportlab).

The built-in PDF fonts only cover Latin-1, so amounts show the currency *code* (INR 1,180.00) and a character the font cannot
draw is shown as ?. Numbers are grouped the Indian way (12,34,567.89) for the en-IN locale and the usual way otherwise.
"""
import io
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from xml.sax.saxutils import escape

from app.utils.money import quantize, to_decimal

TITLES = {"invoice": "TAX INVOICE", "credit_note": "CREDIT NOTE", "debit_note": "DEBIT NOTE"}


def safe(text) -> str:
    """Text the built-in fonts can draw, escaped for the paragraph markup."""
    return escape(str(text if text is not None else "").encode("latin-1", "replace").decode("latin-1"))


def group_number(amount: Decimal, decimals: int, locale: str) -> str:
    text = f"{quantize(to_decimal(amount), decimals):,.{decimals}f}"
    if locale.lower().endswith("-in"):
        whole, _, frac = text.replace(",", "").partition(".")
        sign = "-" if whole.startswith("-") else ""
        whole = whole.lstrip("-")
        if len(whole) > 3:
            head, tail = whole[:-3], whole[-3:]
            parts = []
            while len(head) > 2:
                parts.insert(0, head[-2:])
                head = head[:-2]
            if head:
                parts.insert(0, head)
            whole = ",".join(parts + [tail])
        return f"{sign}{whole}" + (f".{frac}" if frac else "")
    return text


def render(invoice: dict, template: dict, decimals: int, base_decimals: int) -> bytes:
    """`invoice` and `template` are plain dicts (see invoice_service.pdf_context)."""
    locale = template.get("locale") or "en-IN"
    accent = colors.HexColor(template.get("accent_color") or "#1f3a5f")
    money = lambda value: f"{invoice['currency']} {group_number(value, decimals, locale)}"
    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=8.5, leading=11, textColor=colors.HexColor("#444444"))
    body = ParagraphStyle("body", parent=styles["Normal"], fontSize=9.5, leading=12.5)
    bold = ParagraphStyle("bold", parent=body, fontName="Helvetica-Bold")
    title = ParagraphStyle("title", parent=styles["Title"], fontSize=18, leading=22, alignment=2, textColor=accent)
    name_style = ParagraphStyle("name", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=14, leading=17, textColor=accent)

    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm, bottomMargin=16 * mm,
                            title=f"{TITLES.get(invoice['doc_type'], 'Invoice')} {invoice['number']}")
    story = []

    supplier = invoice.get("supplier") or {}
    left = [Paragraph(safe(template.get("display_name") or supplier.get("name") or "Seller"), name_style)]
    for line in (template.get("address") or supplier.get("address") or "").splitlines():
        left.append(Paragraph(safe(line), small))
    if supplier.get("tax_id"):
        left.append(Paragraph(safe(f"{supplier.get('tax_id_label') or 'Tax ID'}: {supplier['tax_id']}"), small))
    right = [Paragraph(TITLES.get(invoice["doc_type"], "INVOICE"), title),
             Paragraph(safe(f"No. {invoice['number']}"), ParagraphStyle("r1", parent=body, alignment=2, fontName="Helvetica-Bold")),
             Paragraph(safe(f"Date: {invoice['issued_at']:%d %b %Y}"), ParagraphStyle("r2", parent=body, alignment=2))]
    if invoice.get("parent_number"):
        right.append(Paragraph(safe(f"Against invoice {invoice['parent_number']}"), ParagraphStyle("r3", parent=small, alignment=2)))
    head = Table([[left, right]], colWidths=[100 * mm, 74 * mm])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [head, Spacer(1, 6 * mm)]

    billing = invoice.get("billing") or {}
    to = [Paragraph("BILLED TO", ParagraphStyle("lbl", parent=small, fontName="Helvetica-Bold", textColor=accent))]
    for value in (billing.get("name"), billing.get("address_line1"), billing.get("address_line2"),
                  ", ".join(x for x in (billing.get("city"), billing.get("state"), billing.get("postal_code")) if x), billing.get("country"), billing.get("email")):
        if value:
            to.append(Paragraph(safe(value), body))
    if billing.get("tax_id"):
        to.append(Paragraph(safe(f"Tax ID: {billing['tax_id']}"), small))
    story += [Table([[to]], colWidths=[174 * mm], style=[("LEFTPADDING", (0, 0), (-1, -1), 0)]), Spacer(1, 5 * mm)]

    rows = [["Description", "Qty", "Unit price", "Amount", "Tax", "Total"]]
    for line in invoice["lines"]:
        rows.append([Paragraph(safe(line["description"]), body), str(line.get("quantity", 1)), group_number(line["unit_price"], decimals, locale),
                     group_number(line["net"], decimals, locale), group_number(line["tax"], decimals, locale), group_number(line["gross"], decimals, locale)])
    table = Table(rows, colWidths=[64 * mm, 12 * mm, 26 * mm, 26 * mm, 22 * mm, 24 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), accent), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9), ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#cccccc")), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story += [table, Spacer(1, 4 * mm)]

    totals = [["Subtotal", money(invoice["subtotal"])]]
    for row in invoice.get("tax_breakdown") or []:
        label = f"{row['name']} @ {group_number(row['rate'], 2, locale).rstrip('0').rstrip('.')}%"
        totals.append([label, money(row["tax_amount"])])
    totals.append(["Tax total", money(invoice["tax_total"])])
    totals.append([("Credit total" if invoice["doc_type"] == "credit_note" else "Total"), money(invoice["total"])])
    t = Table(totals, colWidths=[44 * mm, 38 * mm], hAlign="RIGHT")
    t.setStyle(TableStyle([("ALIGN", (1, 0), (1, -1), "RIGHT"), ("FONTSIZE", (0, 0), (-1, -1), 9.5), ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                           ("LINEABOVE", (0, -1), (-1, -1), 0.8, accent), ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    story += [t, Spacer(1, 6 * mm)]

    notes = []
    if template.get("show_exchange_rate", True) and invoice["item_currency"] != invoice["currency"]:
        notes.append(f"Prices are in {invoice['item_currency']}. Charged in {invoice['currency']} at 1 {invoice['item_currency']} = "
                     f"{invoice['exchange_rate']:.6f} {invoice['currency']}.")
    if invoice["base_currency"] != invoice["currency"]:
        notes.append(f"Accounting value: {invoice['base_currency']} {group_number(invoice['base_total'], base_decimals, locale)} "
                     f"(1 {invoice['currency']} = {invoice['base_rate']:.6f} {invoice['base_currency']}).")
    if invoice.get("notes"):
        notes.append(invoice["notes"])
    if invoice.get("reason"):
        notes.append(f"Reason: {invoice['reason']}")
    for note in notes:
        story.append(Paragraph(safe(note), small))
    if template.get("terms"):
        story += [Spacer(1, 3 * mm), Paragraph(safe(template["terms"]), small)]
    if template.get("footer_text"):
        story += [Spacer(1, 4 * mm), Paragraph(safe(template["footer_text"]), ParagraphStyle("foot", parent=small, alignment=1))]
    doc.build(story)
    return out.getvalue()
