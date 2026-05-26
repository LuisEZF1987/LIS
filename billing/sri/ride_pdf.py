#!/usr/bin/env python3
"""
SRI Ecuador — RIDE PDF Generator.

RIDE = Representacion Impresa del Documento Electronico.

Generates a PDF for SRI-authorized electronic invoices and credit notes,
including the clave de acceso as a Code128 barcode.
"""
import io
import logging
from datetime import datetime

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, mm
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image,
)
from reportlab.graphics.barcode.code128 import Code128

log = logging.getLogger("sri.ride_pdf")


def generate_ride(invoice_data: dict, sri_data: dict, config: dict) -> bytes:
    """
    Generate RIDE PDF for an SRI-authorized document.

    Parameters:
        invoice_data: Invoice dict with keys:
            invoice_number, invoice_type, patient_document, patient_name,
            patient_address, patient_email, patient_phone,
            subtotal_0, subtotal_iva, iva_amount, total,
            created_at, notes, lines (list of line dicts)
        sri_data: SRI document dict with keys:
            clave_acceso, numero_autorizacion, fecha_autorizacion,
            estado_autorizacion
        config: Company config dict with keys:
            razon_social, nombre_comercial, ruc, direccion_matriz,
            obligado_contabilidad, ambiente

    Returns:
        PDF bytes
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=0.5 * inch,
        rightMargin=0.5 * inch,
        topMargin=0.4 * inch,
        bottomMargin=0.4 * inch,
    )

    styles = getSampleStyleSheet()
    elements = []

    # Styles
    style_title = ParagraphStyle(
        "ride_title", parent=styles["Title"],
        fontSize=14, spaceAfter=2, spaceBefore=0,
    )
    style_subtitle = ParagraphStyle(
        "ride_subtitle", parent=styles["Normal"],
        fontSize=10, spaceAfter=2,
    )
    style_normal = ParagraphStyle(
        "ride_normal", parent=styles["Normal"],
        fontSize=8, spaceAfter=1,
    )
    style_bold = ParagraphStyle(
        "ride_bold", parent=styles["Normal"],
        fontSize=8, spaceAfter=1, fontName="Helvetica-Bold",
    )
    style_center = ParagraphStyle(
        "ride_center", parent=styles["Normal"],
        fontSize=8, alignment=TA_CENTER,
    )
    style_right = ParagraphStyle(
        "ride_right", parent=styles["Normal"],
        fontSize=8, alignment=TA_RIGHT,
    )
    style_small = ParagraphStyle(
        "ride_small", parent=styles["Normal"],
        fontSize=7, textColor=colors.HexColor("#4b5563"),
    )

    # ===================================================================
    # Header: Two-column layout (Company info | SRI document info)
    # ===================================================================
    razon_social = config.get("razon_social", "")
    nombre_comercial = config.get("nombre_comercial", "")
    ruc = config.get("ruc", "")
    direccion = config.get("direccion_matriz", "")
    obligado = config.get("obligado_contabilidad", "SI")

    # Left column: company info
    left_info = f"""<b>{razon_social}</b><br/>"""
    if nombre_comercial and nombre_comercial != razon_social:
        left_info += f"{nombre_comercial}<br/>"
    left_info += f"""<b>RUC:</b> {ruc}<br/>
<b>Dir. Matriz:</b> {direccion}<br/>
<b>Obligado a llevar contabilidad:</b> {obligado}"""

    # Right column: SRI document info
    inv_type = invoice_data.get("invoice_type", "out")
    doc_label = "NOTA DE CREDITO" if inv_type == "credit_note" else "FACTURA"
    inv_number = invoice_data.get("invoice_number", "")

    clave_acceso = sri_data.get("clave_acceso", "")
    num_autorizacion = sri_data.get("numero_autorizacion", "")
    fecha_autorizacion = sri_data.get("fecha_autorizacion", "")
    ambiente_str = "PRODUCCION" if str(config.get("ambiente", "1")) == "2" else "PRUEBAS"

    # Display SRI-format number (remove FAC- prefix if present)
    sri_number = inv_number
    if sri_number.startswith("FAC-"):
        sri_number = sri_number[4:]

    right_info = f"""<b>{doc_label}</b><br/>
<b>No.</b> {sri_number}<br/>
<b>Ambiente:</b> {ambiente_str}<br/>
<b>Emision:</b> NORMAL<br/>
<b>Clave de Acceso:</b>"""

    header_data = [[Paragraph(left_info, style_normal), Paragraph(right_info, style_normal)]]
    header_table = Table(header_data, colWidths=[3.5 * inch, 3.5 * inch])
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("BOX", (0, 0), (0, 0), 0.5, colors.HexColor("#94a3b8")),
        ("BOX", (1, 0), (1, 0), 0.5, colors.HexColor("#94a3b8")),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 6))

    # ===================================================================
    # Clave de acceso barcode
    # ===================================================================
    if clave_acceso:
        elements.append(
            Paragraph("<b>CLAVE DE ACCESO</b>", style_center)
        )
        try:
            barcode = Code128(clave_acceso, barWidth=0.8, barHeight=30)
            bc_table = Table(
                [[barcode]],
                colWidths=[7 * inch],
            )
            bc_table.setStyle(TableStyle([
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ]))
            elements.append(bc_table)
        except Exception as e:
            log.warning("Failed to generate barcode: %s", e)

        elements.append(
            Paragraph(f"<font size='6'>{clave_acceso}</font>", style_center)
        )
        elements.append(Spacer(1, 4))

    # Authorization info
    if num_autorizacion:
        elements.append(
            Paragraph(
                f"<b>No. Autorizacion:</b> {num_autorizacion}"
                f"    <b>Fecha:</b> {fecha_autorizacion}",
                style_small,
            )
        )
    elements.append(Spacer(1, 8))

    # ===================================================================
    # Buyer (patient) information
    # ===================================================================
    fecha_emision = invoice_data.get("created_at", "")
    if isinstance(fecha_emision, str):
        fecha_emision = fecha_emision[:10]

    patient_doc = invoice_data.get("patient_document", "")
    patient_name = invoice_data.get("patient_name", "CONSUMIDOR FINAL")
    patient_addr = invoice_data.get("patient_address", "")

    buyer_data = [
        [
            Paragraph(f"<b>Razon Social / Nombres:</b> {patient_name}", style_normal),
            Paragraph(f"<b>Fecha Emision:</b> {fecha_emision}", style_normal),
        ],
        [
            Paragraph(f"<b>Identificacion:</b> {patient_doc}", style_normal),
            Paragraph("", style_normal),
        ],
    ]
    if patient_addr:
        buyer_data.append([
            Paragraph(f"<b>Direccion:</b> {patient_addr}", style_normal),
            Paragraph("", style_normal),
        ])

    buyer_table = Table(buyer_data, colWidths=[4.5 * inch, 2.5 * inch])
    buyer_table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#94a3b8")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e2e8f0")),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(buyer_table)
    elements.append(Spacer(1, 8))

    # ===================================================================
    # Detail lines table
    # ===================================================================
    lines = invoice_data.get("lines", [])

    detail_header = [
        Paragraph("<b>Cod.</b>", style_bold),
        Paragraph("<b>Descripcion</b>", style_bold),
        Paragraph("<b>Cant.</b>", style_bold),
        Paragraph("<b>P. Unit.</b>", style_bold),
        Paragraph("<b>Desc.</b>", style_bold),
        Paragraph("<b>P. Total</b>", style_bold),
    ]
    detail_data = [detail_header]

    for ln in lines:
        qty = abs(float(ln.get("quantity", 1)))
        unit_price = float(ln.get("unit_price", 0))
        discount = float(ln.get("discount_percent", 0))
        line_total = abs(float(ln.get("line_total", 0)))
        discount_amt = round(qty * unit_price * discount / 100, 2)

        detail_data.append([
            Paragraph(str(ln.get("catalog_id") or ln.get("code", "-")), style_normal),
            Paragraph((ln.get("description", "") or "")[:80], style_normal),
            Paragraph(f"{qty:.2f}", style_right),
            Paragraph(f"${unit_price:.2f}", style_right),
            Paragraph(f"${discount_amt:.2f}", style_right),
            Paragraph(f"${line_total:.2f}", style_right),
        ])

    detail_table = Table(
        detail_data,
        colWidths=[0.6 * inch, 3.4 * inch, 0.6 * inch, 0.8 * inch, 0.7 * inch, 0.9 * inch],
    )
    detail_style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]
    # Alternate row colors
    for i in range(1, len(detail_data)):
        if i % 2 == 0:
            detail_style.append(
                ("BACKGROUND", (0, i), (-1, i), colors.HexColor("#f8fafc"))
            )
    detail_table.setStyle(TableStyle(detail_style))
    elements.append(detail_table)
    elements.append(Spacer(1, 8))

    # ===================================================================
    # Totals section (SRI format)
    # ===================================================================
    subtotal_0 = abs(float(invoice_data.get("subtotal_0", 0)))
    subtotal_iva = abs(float(invoice_data.get("subtotal_iva", 0)))
    iva_amount = abs(float(invoice_data.get("iva_amount", 0)))
    total = abs(float(invoice_data.get("total", 0)))

    totals_data = [
        ["SUBTOTAL 0%", f"${subtotal_0:.2f}"],
        ["SUBTOTAL IVA%", f"${subtotal_iva:.2f}"],
        ["SUBTOTAL SIN IMPUESTOS", f"${subtotal_0 + subtotal_iva:.2f}"],
        ["IVA", f"${iva_amount:.2f}"],
        ["VALOR TOTAL", f"${total:.2f}"],
    ]

    totals_inner = Table(totals_data, colWidths=[2.2 * inch, 1 * inch])
    totals_inner.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#94a3b8")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e2e8f0")),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTSIZE", (0, -1), (-1, -1), 10),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f1f5f9")),
    ]))

    totals_wrapper = Table(
        [["", totals_inner]],
        colWidths=[3.8 * inch, 3.2 * inch],
    )
    totals_wrapper.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    elements.append(totals_wrapper)
    elements.append(Spacer(1, 8))

    # ===================================================================
    # Payment method
    # ===================================================================
    elements.append(Paragraph("<b>Forma de Pago:</b>", style_bold))
    payment_table = Table(
        [
            [
                Paragraph("<b>Forma de Pago</b>", style_bold),
                Paragraph("<b>Valor</b>", style_bold),
            ],
            [
                Paragraph("Sin utilizacion del sistema financiero", style_normal),
                Paragraph(f"${total:.2f}", style_right),
            ],
        ],
        colWidths=[5.5 * inch, 1.5 * inch],
    )
    payment_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#334155")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    elements.append(payment_table)
    elements.append(Spacer(1, 8))

    # ===================================================================
    # Additional info / Notes
    # ===================================================================
    notes = invoice_data.get("notes", "")
    if notes:
        elements.append(Paragraph("<b>Informacion Adicional:</b>", style_bold))
        elements.append(Paragraph(notes, style_normal))
        elements.append(Spacer(1, 4))

    # ===================================================================
    # Footer
    # ===================================================================
    elements.append(Spacer(1, 12))
    elements.append(
        Paragraph(
            "Documento generado electronicamente - RIDE",
            ParagraphStyle(
                "footer", parent=style_center,
                fontSize=7, textColor=colors.HexColor("#6b7280"),
            ),
        )
    )

    # Build PDF
    doc.build(elements)
    buf.seek(0)
    return buf.read()
