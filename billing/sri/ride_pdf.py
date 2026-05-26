"""
RIDE PDF Generator — Representacion Impresa del Documento Electronico.
Generates the official printed format required by SRI Ecuador.
"""
import io
import logging
from datetime import datetime

log = logging.getLogger("sri.ride")


def generate_ride(invoice_data: dict, sri_data: dict, config: dict) -> bytes:
    """Generate RIDE PDF for an authorized electronic invoice.

    Args:
        invoice_data: Invoice details (number, patient, lines, totals)
        sri_data: SRI authorization data (clave_acceso, autorizacion, fecha)
        config: Institution config (ruc, razon_social, direccion, etc.)

    Returns:
        PDF bytes
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=15 * mm, bottomMargin=15 * mm)
    styles = getSampleStyleSheet()
    story = []

    # Styles
    title_style = ParagraphStyle("Title", parent=styles["Heading1"], fontSize=12,
                                  spaceAfter=2 * mm, textColor=colors.HexColor("#1e3a5f"))
    normal = ParagraphStyle("Normal", parent=styles["Normal"], fontSize=8, leading=10)
    bold = ParagraphStyle("Bold", parent=styles["Normal"], fontSize=8, leading=10,
                           fontName="Helvetica-Bold")
    small = ParagraphStyle("Small", parent=styles["Normal"], fontSize=7, leading=9,
                            textColor=colors.grey)

    # --- Header: two columns ---
    ruc = config.get("ruc", "")
    razon_social = config.get("razon_social", "")
    nombre_comercial = config.get("nombre_comercial", razon_social)
    direccion = config.get("direccion_matriz", "")
    clave_acceso = sri_data.get("clave_acceso", "")
    num_autorizacion = sri_data.get("numero_autorizacion", clave_acceso)
    fecha_autorizacion = sri_data.get("fecha_autorizacion", "")
    invoice_number = invoice_data.get("invoice_number", "")

    # Left column: institution info
    left_info = f"""<b>{razon_social}</b><br/>
    {nombre_comercial}<br/>
    Dir: {direccion}<br/>
    RUC: {ruc}<br/>
    Obligado a llevar contabilidad: {config.get('obligado_contabilidad', 'NO')}"""

    # Right column: invoice info
    right_info = f"""<b>FACTURA</b><br/>
    No. {invoice_number}<br/>
    Ambiente: {'PRODUCCION' if config.get('ambiente', 1) == 2 else 'PRUEBAS'}<br/>
    Emision: NORMAL<br/>
    Clave de Acceso:"""

    header_data = [[Paragraph(left_info, normal), Paragraph(right_info, normal)]]
    header_table = Table(header_data, colWidths=[90 * mm, 90 * mm])
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 2 * mm))

    # Barcode (clave de acceso)
    if clave_acceso:
        try:
            import barcode
            from barcode.writer import ImageWriter
            code128 = barcode.get("code128", clave_acceso, writer=ImageWriter())
            barcode_buf = io.BytesIO()
            code128.write(barcode_buf, options={"write_text": False, "module_height": 8})
            barcode_buf.seek(0)
            from reportlab.platypus import Image
            story.append(Image(barcode_buf, width=170 * mm, height=12 * mm))
        except ImportError:
            story.append(Paragraph(f"Clave de Acceso: {clave_acceso}", small))
        story.append(Paragraph(clave_acceso, small))
        story.append(Spacer(1, 2 * mm))

    # Authorization
    if num_autorizacion:
        auth_info = f"No. Autorizacion: {num_autorizacion}    Fecha: {fecha_autorizacion}"
        story.append(Paragraph(auth_info, small))
        story.append(Spacer(1, 3 * mm))

    # --- Patient info ---
    patient_doc = invoice_data.get("patient_document", "")
    patient_name = invoice_data.get("patient_name", "")
    fecha_emision = invoice_data.get("fecha_emision", datetime.now().strftime("%d/%m/%Y"))

    patient_data = [
        [Paragraph(f"<b>Razon Social / Nombres:</b> {patient_name}", normal),
         Paragraph(f"<b>RUC/CI:</b> {patient_doc}", normal)],
        [Paragraph(f"<b>Fecha Emision:</b> {fecha_emision}", normal),
         Paragraph(f"<b>Guia Remision:</b> ", normal)],
    ]
    patient_table = Table(patient_data, colWidths=[120 * mm, 60 * mm])
    patient_table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(patient_table)
    story.append(Spacer(1, 4 * mm))

    # --- Detail lines ---
    lines = invoice_data.get("lines", [])
    detail_header = ["Cod.", "Descripcion", "Cant.", "P. Unit.", "Desc.", "Total"]
    detail_data = [detail_header]
    for line in lines:
        detail_data.append([
            line.get("code", ""),
            line.get("description", ""),
            str(line.get("quantity", 1)),
            f"${line.get('unit_price', 0):.2f}",
            f"${line.get('discount', 0):.2f}",
            f"${line.get('line_total', 0):.2f}",
        ])

    detail_table = Table(detail_data, colWidths=[25 * mm, 75 * mm, 15 * mm, 22 * mm, 18 * mm, 25 * mm])
    detail_style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8edf2")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]
    # Alternate row colors
    for i in range(1, len(detail_data)):
        if i % 2 == 0:
            detail_style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#f5f7fa")))
    detail_table.setStyle(TableStyle(detail_style))
    story.append(detail_table)
    story.append(Spacer(1, 4 * mm))

    # --- Totals ---
    subtotal_0 = invoice_data.get("subtotal", 0)
    subtotal_iva = invoice_data.get("subtotal_iva", 0)
    iva = invoice_data.get("tax_amount", 0)
    total = invoice_data.get("total", 0)

    totals_data = [
        ["SUBTOTAL 0%", f"${subtotal_0:.2f}"],
        ["SUBTOTAL 15%", f"${subtotal_iva:.2f}"],
        ["IVA 15%", f"${iva:.2f}"],
        ["TOTAL", f"${total:.2f}"],
    ]
    totals_table = Table(totals_data, colWidths=[40 * mm, 30 * mm])
    totals_table.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))

    # Align totals to the right
    wrapper_data = [["", totals_table]]
    wrapper = Table(wrapper_data, colWidths=[110 * mm, 70 * mm])
    wrapper.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(wrapper)

    # --- Payment method ---
    payment_method = invoice_data.get("payment_method", "")
    if payment_method:
        story.append(Spacer(1, 3 * mm))
        pm_label = {"efectivo": "Efectivo", "tarjeta_debito": "Tarjeta Debito",
                     "tarjeta_credito": "Tarjeta Credito", "transferencia": "Transferencia"
                    }.get(payment_method, payment_method)
        story.append(Paragraph(f"<b>Forma de Pago:</b> {pm_label} — ${total:.2f}", normal))

    # Build PDF
    doc.build(story)
    return buf.getvalue()
