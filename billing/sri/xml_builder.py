#!/usr/bin/env python3
"""
SRI Ecuador — XML Builder for Electronic Documents.

Builds SRI-compliant XML for:
  - Factura (tipo_comprobante = '01')
  - Nota de Credito (tipo_comprobante = '04')

Follows SRI schema v2.1.0.

Clave de acceso (49 digits):
  fecha(8) + tipo_comprobante(2) + ruc(13) + ambiente(1) + serie(6)
  + secuencial(9) + codigo_numerico(8) + tipo_emision(1) + digito_verificador(1)
"""
import random
import logging
from datetime import datetime

from lxml import etree

log = logging.getLogger("sri.xml_builder")


# ---------------------------------------------------------------------------
# Modulo 11 — SRI check digit algorithm
# ---------------------------------------------------------------------------
def _modulo11(clave48: str) -> int:
    """
    Compute the modulo 11 check digit for a 48-character string.

    Weights cycle 2,3,4,5,6,7 from right to left.
    Result = 11 - (sum % 11), with special cases:
      - if result == 11 -> 0
      - if result == 10 -> 1
    """
    if len(clave48) != 48:
        raise ValueError(f"clave48 must be exactly 48 chars, got {len(clave48)}")

    weights = [2, 3, 4, 5, 6, 7]
    total = 0
    for i, ch in enumerate(reversed(clave48)):
        total += int(ch) * weights[i % len(weights)]

    remainder = total % 11
    check = 11 - remainder

    if check == 11:
        return 0
    if check == 10:
        return 1
    return check


def build_clave_acceso(
    fecha: str,
    tipo_comprobante: str,
    ruc: str,
    ambiente: str,
    establecimiento: str,
    punto_emision: str,
    secuencial: str,
    codigo_numerico: str = "",
) -> str:
    """
    Build the 49-digit clave de acceso for SRI.

    Parameters:
        fecha: dd/mm/yyyy format (8 digits without separators internally)
        tipo_comprobante: '01' (factura), '04' (nota de credito), etc.
        ruc: 13-digit RUC
        ambiente: '1' (pruebas) or '2' (produccion)
        establecimiento: 3-digit establishment code (e.g. '001')
        punto_emision: 3-digit emission point (e.g. '001')
        secuencial: 9-digit sequential number (zero-padded)
        codigo_numerico: 8-digit random code (auto-generated if empty)

    Returns:
        49-character string (the clave de acceso)
    """
    # Parse and reformat date
    if "/" in fecha:
        parts = fecha.split("/")
        fecha_digits = f"{parts[0]:0>2}{parts[1]:0>2}{parts[2]:0>4}"
    elif "-" in fecha:
        parts = fecha.split("-")
        # Assume yyyy-mm-dd
        fecha_digits = f"{parts[2]:0>2}{parts[1]:0>2}{parts[0]:0>4}"
    else:
        fecha_digits = fecha[:8]

    tipo_emision = "1"  # Normal emission

    if not codigo_numerico:
        codigo_numerico = f"{random.randint(0, 99999999):08d}"

    # Pad fields
    ruc = ruc.ljust(13, "0")[:13]
    establecimiento = establecimiento.zfill(3)[:3]
    punto_emision = punto_emision.zfill(3)[:3]
    secuencial = secuencial.zfill(9)[:9]
    tipo_comprobante = tipo_comprobante.zfill(2)[:2]
    ambiente = ambiente[:1]

    serie = f"{establecimiento}{punto_emision}"

    clave48 = (
        f"{fecha_digits}"
        f"{tipo_comprobante}"
        f"{ruc}"
        f"{ambiente}"
        f"{serie}"
        f"{secuencial}"
        f"{codigo_numerico}"
        f"{tipo_emision}"
    )

    if len(clave48) != 48:
        raise ValueError(
            f"clave48 construction error: expected 48 chars, got {len(clave48)} "
            f"({clave48})"
        )

    digito = _modulo11(clave48)
    clave49 = f"{clave48}{digito}"

    log.debug("Clave de acceso generada: %s", clave49)
    return clave49


# ---------------------------------------------------------------------------
# Factura XML (tipo_comprobante = '01')
# ---------------------------------------------------------------------------
def build_factura_xml(config: dict, invoice: dict, lines: list) -> str:
    """
    Build SRI factura XML.

    Parameters:
        config: {ambiente, ruc, razon_social, nombre_comercial,
                 direccion_matriz, obligado_contabilidad}
        invoice: invoice row dict (patient_document, patient_name,
                 patient_address, establecimiento, punto_emision,
                 secuencial, subtotal_0, subtotal_iva, iva_amount, total,
                 created_at, etc.)
        lines: list of invoice line dicts

    Returns:
        XML string
    """
    # Format date
    inv_date = invoice.get("created_at", "")
    if isinstance(inv_date, str):
        fecha_emision = inv_date[:10]
        # Convert to dd/mm/yyyy
        if "-" in fecha_emision:
            parts = fecha_emision.split("-")
            fecha_emision = f"{parts[2]}/{parts[1]}/{parts[0]}"
    elif hasattr(inv_date, "strftime"):
        fecha_emision = inv_date.strftime("%d/%m/%Y")
    else:
        fecha_emision = datetime.now().strftime("%d/%m/%Y")

    establecimiento = str(invoice.get("establecimiento", "001")).zfill(3)
    punto_emision = str(invoice.get("punto_emision", "001")).zfill(3)
    secuencial_raw = str(invoice.get("secuencial", 1))
    secuencial = secuencial_raw.zfill(9)
    ambiente = str(config.get("ambiente", "1"))
    ruc = config.get("ruc", "")
    codigo_numerico = f"{random.randint(0, 99999999):08d}"

    clave_acceso = build_clave_acceso(
        fecha=fecha_emision,
        tipo_comprobante="01",
        ruc=ruc,
        ambiente=ambiente,
        establecimiento=establecimiento,
        punto_emision=punto_emision,
        secuencial=secuencial,
        codigo_numerico=codigo_numerico,
    )

    # Build XML
    root = etree.Element("factura", id="comprobante", version="2.1.0")

    # --- infoTributaria ---
    info_trib = etree.SubElement(root, "infoTributaria")
    _add_text(info_trib, "ambiente", ambiente)
    _add_text(info_trib, "tipoEmision", "1")
    _add_text(info_trib, "razonSocial", config.get("razon_social", ""))
    if config.get("nombre_comercial"):
        _add_text(info_trib, "nombreComercial", config["nombre_comercial"])
    _add_text(info_trib, "ruc", ruc)
    _add_text(info_trib, "claveAcceso", clave_acceso)
    _add_text(info_trib, "codDoc", "01")
    _add_text(info_trib, "estab", establecimiento)
    _add_text(info_trib, "ptoEmi", punto_emision)
    _add_text(info_trib, "secuencial", secuencial)
    _add_text(info_trib, "dirMatriz", config.get("direccion_matriz", ""))

    # --- infoFactura ---
    info_fac = etree.SubElement(root, "infoFactura")
    _add_text(info_fac, "fechaEmision", fecha_emision)
    _add_text(info_fac, "dirEstablecimiento", config.get("direccion_matriz", ""))
    if config.get("obligado_contabilidad"):
        _add_text(info_fac, "obligadoContabilidad", config["obligado_contabilidad"])

    # Buyer identification
    patient_doc = invoice.get("patient_document", "")
    tipo_id = _tipo_identificacion(patient_doc)
    _add_text(info_fac, "tipoIdentificacionComprador", tipo_id)
    _add_text(info_fac, "razonSocialComprador",
              invoice.get("patient_name", "CONSUMIDOR FINAL"))
    _add_text(info_fac, "identificacionComprador",
              patient_doc or "9999999999999")
    if invoice.get("patient_address"):
        _add_text(info_fac, "direccionComprador", invoice["patient_address"])

    # Totals
    subtotal_0 = float(invoice.get("subtotal_0", 0))
    subtotal_iva = float(invoice.get("subtotal_iva", 0))
    iva_amount = float(invoice.get("iva_amount", 0))
    total = float(invoice.get("total", 0))

    _add_text(info_fac, "totalSinImpuestos", f"{subtotal_0 + subtotal_iva:.2f}")
    _add_text(info_fac, "totalDescuento", "0.00")

    # totalConImpuestos
    total_impuestos = etree.SubElement(info_fac, "totalConImpuestos")

    # IVA 0%
    if subtotal_0 > 0:
        ti = etree.SubElement(total_impuestos, "totalImpuesto")
        _add_text(ti, "codigo", "2")       # IVA
        _add_text(ti, "codigoPorcentaje", "0")  # 0%
        _add_text(ti, "baseImponible", f"{subtotal_0:.2f}")
        _add_text(ti, "valor", "0.00")

    # IVA 15% (if any)
    if subtotal_iva > 0:
        ti = etree.SubElement(total_impuestos, "totalImpuesto")
        _add_text(ti, "codigo", "2")
        _add_text(ti, "codigoPorcentaje", "4")  # 15%
        _add_text(ti, "baseImponible", f"{subtotal_iva:.2f}")
        _add_text(ti, "valor", f"{iva_amount:.2f}")

    _add_text(info_fac, "propina", "0.00")
    _add_text(info_fac, "importeTotal", f"{total:.2f}")
    _add_text(info_fac, "moneda", "DOLAR")

    # pagos
    pagos = etree.SubElement(info_fac, "pagos")
    pago = etree.SubElement(pagos, "pago")
    _add_text(pago, "formaPago", "01")  # Sin utilizacion del sistema financiero
    _add_text(pago, "total", f"{total:.2f}")

    # --- detalles ---
    detalles = etree.SubElement(root, "detalles")
    for ln in lines:
        detalle = etree.SubElement(detalles, "detalle")
        _add_text(detalle, "codigoPrincipal", str(ln.get("catalog_id", "001")))
        _add_text(detalle, "descripcion", ln.get("description", "Servicio")[:300])
        _add_text(detalle, "cantidad", f"{abs(float(ln.get('quantity', 1))):.2f}")
        _add_text(detalle, "precioUnitario", f"{float(ln.get('unit_price', 0)):.2f}")
        _add_text(detalle, "descuento", "0.00")
        line_total = abs(float(ln.get("line_total", 0)))
        _add_text(detalle, "precioTotalSinImpuesto", f"{line_total:.2f}")

        impuestos = etree.SubElement(detalle, "impuestos")
        imp = etree.SubElement(impuestos, "impuesto")
        _add_text(imp, "codigo", "2")  # IVA
        tax_rate = float(ln.get("tax_rate", 0))
        if tax_rate > 0:
            _add_text(imp, "codigoPorcentaje", "4")  # 15%
            _add_text(imp, "tarifa", f"{tax_rate:.0f}")
        else:
            _add_text(imp, "codigoPorcentaje", "0")  # 0%
            _add_text(imp, "tarifa", "0")
        _add_text(imp, "baseImponible", f"{line_total:.2f}")
        _add_text(imp, "valor", f"{abs(float(ln.get('tax_amount', 0))):.2f}")

    xml_str = etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", pretty_print=True,
    ).decode("utf-8")

    return xml_str


# ---------------------------------------------------------------------------
# Nota de Credito XML (tipo_comprobante = '04')
# ---------------------------------------------------------------------------
def build_nota_credito_xml(
    config: dict,
    credit_note: dict,
    lines: list,
    original_invoice: dict,
) -> str:
    """
    Build SRI nota de credito XML.

    Parameters:
        config: SRI configuration dict
        credit_note: credit note invoice dict
        lines: list of credit note lines (quantities/amounts are negative)
        original_invoice: the original invoice being credited

    Returns:
        XML string
    """
    inv_date = credit_note.get("created_at", "")
    if isinstance(inv_date, str):
        fecha_emision = inv_date[:10]
        if "-" in fecha_emision:
            parts = fecha_emision.split("-")
            fecha_emision = f"{parts[2]}/{parts[1]}/{parts[0]}"
    elif hasattr(inv_date, "strftime"):
        fecha_emision = inv_date.strftime("%d/%m/%Y")
    else:
        fecha_emision = datetime.now().strftime("%d/%m/%Y")

    establecimiento = str(credit_note.get("establecimiento", "001")).zfill(3)
    punto_emision = str(credit_note.get("punto_emision", "001")).zfill(3)
    secuencial = str(credit_note.get("secuencial", 1)).zfill(9)
    ambiente = str(config.get("ambiente", "1"))
    ruc = config.get("ruc", "")
    codigo_numerico = f"{random.randint(0, 99999999):08d}"

    clave_acceso = build_clave_acceso(
        fecha=fecha_emision,
        tipo_comprobante="04",
        ruc=ruc,
        ambiente=ambiente,
        establecimiento=establecimiento,
        punto_emision=punto_emision,
        secuencial=secuencial,
        codigo_numerico=codigo_numerico,
    )

    root = etree.Element("notaCredito", id="comprobante", version="1.1.0")

    # --- infoTributaria ---
    info_trib = etree.SubElement(root, "infoTributaria")
    _add_text(info_trib, "ambiente", ambiente)
    _add_text(info_trib, "tipoEmision", "1")
    _add_text(info_trib, "razonSocial", config.get("razon_social", ""))
    if config.get("nombre_comercial"):
        _add_text(info_trib, "nombreComercial", config["nombre_comercial"])
    _add_text(info_trib, "ruc", ruc)
    _add_text(info_trib, "claveAcceso", clave_acceso)
    _add_text(info_trib, "codDoc", "04")
    _add_text(info_trib, "estab", establecimiento)
    _add_text(info_trib, "ptoEmi", punto_emision)
    _add_text(info_trib, "secuencial", secuencial)
    _add_text(info_trib, "dirMatriz", config.get("direccion_matriz", ""))

    # --- infoNotaCredito ---
    info_nc = etree.SubElement(root, "infoNotaCredito")
    _add_text(info_nc, "fechaEmision", fecha_emision)
    _add_text(info_nc, "dirEstablecimiento", config.get("direccion_matriz", ""))
    if config.get("obligado_contabilidad"):
        _add_text(info_nc, "obligadoContabilidad", config["obligado_contabilidad"])

    patient_doc = credit_note.get("patient_document", "")
    tipo_id = _tipo_identificacion(patient_doc)
    _add_text(info_nc, "tipoIdentificacionComprador", tipo_id)
    _add_text(info_nc, "razonSocialComprador",
              credit_note.get("patient_name", "CONSUMIDOR FINAL"))
    _add_text(info_nc, "identificacionComprador",
              patient_doc or "9999999999999")

    # Reference to original document
    _add_text(info_nc, "codDocModificado", "01")  # Factura
    orig_number = original_invoice.get("invoice_number", "")
    # Extract SRI number from FAC-XXX-YYY-ZZZZZZZZZ format
    if orig_number.startswith("FAC-"):
        sri_number = orig_number[4:]  # Remove FAC- prefix
    else:
        sri_number = orig_number
    _add_text(info_nc, "numDocModificado", sri_number)

    # Date of original document
    orig_date = original_invoice.get("created_at", fecha_emision)
    if isinstance(orig_date, str) and "-" in orig_date:
        parts = orig_date[:10].split("-")
        orig_date = f"{parts[2]}/{parts[1]}/{parts[0]}"
    elif hasattr(orig_date, "strftime"):
        orig_date = orig_date.strftime("%d/%m/%Y")
    _add_text(info_nc, "fechaEmisionDocSustento", str(orig_date)[:10])

    subtotal_0 = abs(float(credit_note.get("subtotal_0", 0)))
    subtotal_iva = abs(float(credit_note.get("subtotal_iva", 0)))
    iva_amount = abs(float(credit_note.get("iva_amount", 0)))
    total = abs(float(credit_note.get("total", 0)))

    _add_text(info_nc, "totalSinImpuestos", f"{subtotal_0 + subtotal_iva:.2f}")
    _add_text(info_nc, "valorModificacion", f"{total:.2f}")
    _add_text(info_nc, "moneda", "DOLAR")

    # totalConImpuestos
    total_impuestos = etree.SubElement(info_nc, "totalConImpuestos")
    if subtotal_0 > 0:
        ti = etree.SubElement(total_impuestos, "totalImpuesto")
        _add_text(ti, "codigo", "2")
        _add_text(ti, "codigoPorcentaje", "0")
        _add_text(ti, "baseImponible", f"{subtotal_0:.2f}")
        _add_text(ti, "valor", "0.00")

    if subtotal_iva > 0:
        ti = etree.SubElement(total_impuestos, "totalImpuesto")
        _add_text(ti, "codigo", "2")
        _add_text(ti, "codigoPorcentaje", "4")
        _add_text(ti, "baseImponible", f"{subtotal_iva:.2f}")
        _add_text(ti, "valor", f"{iva_amount:.2f}")

    _add_text(info_nc, "motivo", credit_note.get("notes", "Anulacion de factura"))

    # --- detalles ---
    detalles = etree.SubElement(root, "detalles")
    for ln in lines:
        detalle = etree.SubElement(detalles, "detalle")
        _add_text(detalle, "codigoInterno", str(ln.get("catalog_id", "001")))
        _add_text(detalle, "descripcion", ln.get("description", "Servicio")[:300])
        _add_text(detalle, "cantidad", f"{abs(float(ln.get('quantity', 1))):.2f}")
        _add_text(detalle, "precioUnitario", f"{float(ln.get('unit_price', 0)):.2f}")
        _add_text(detalle, "descuento", "0.00")
        line_total = abs(float(ln.get("line_total", 0)))
        _add_text(detalle, "precioTotalSinImpuesto", f"{line_total:.2f}")

        impuestos = etree.SubElement(detalle, "impuestos")
        imp = etree.SubElement(impuestos, "impuesto")
        _add_text(imp, "codigo", "2")
        tax_rate = float(ln.get("tax_rate", 0))
        if tax_rate > 0:
            _add_text(imp, "codigoPorcentaje", "4")
            _add_text(imp, "tarifa", f"{tax_rate:.0f}")
        else:
            _add_text(imp, "codigoPorcentaje", "0")
            _add_text(imp, "tarifa", "0")
        _add_text(imp, "baseImponible", f"{line_total:.2f}")
        _add_text(imp, "valor", f"{abs(float(ln.get('tax_amount', 0))):.2f}")

    xml_str = etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", pretty_print=True,
    ).decode("utf-8")

    return xml_str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _add_text(parent, tag, text):
    """Add a text subelement."""
    el = etree.SubElement(parent, tag)
    el.text = str(text) if text is not None else ""
    return el


def _tipo_identificacion(document_id: str) -> str:
    """
    Determine SRI tipo_identificacion from document ID.
    04 = RUC (13 digits)
    05 = Cedula (10 digits)
    06 = Pasaporte
    07 = Consumidor final
    """
    if not document_id or document_id == "9999999999999":
        return "07"
    doc = document_id.strip()
    if len(doc) == 13 and doc.isdigit():
        return "04"  # RUC
    if len(doc) == 10 and doc.isdigit():
        return "05"  # Cedula
    return "06"  # Pasaporte / otro
