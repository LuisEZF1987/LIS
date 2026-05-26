"""
ATS Generator — Anexo Transaccional Simplificado for SRI Ecuador.
Monthly report of sales and purchases.
"""
import logging
from datetime import date
from lxml import etree

log = logging.getLogger("sri.ats")

# Tipo de identificacion SRI
TIPO_ID = {
    "ruc": "04",
    "cedula": "05",
    "pasaporte": "06",
    "consumidor_final": "07",
}


def generate_ats(year: int, month: int, ventas: list, compras: list, config: dict) -> str:
    """Generate ATS XML for a given month.

    Args:
        year: Fiscal year (e.g. 2026)
        month: Month number (1-12)
        ventas: List of sale dicts with:
            - tipo_comprobante: '01' factura, '04' nota credito
            - tipo_id_comprador: 'cedula', 'ruc', 'pasaporte', 'consumidor_final'
            - id_comprador: document number
            - base_imponible_0: amount at 0% IVA
            - base_imponible_iva: amount at IVA rate
            - monto_iva: IVA amount
            - numero_comprobante: invoice number (001-001-000000001)
            - fecha_emision: 'DD/MM/YYYY'
        compras: List of purchase dicts (similar structure)
        config: Institution config (ruc, razon_social, etc.)

    Returns:
        ATS XML string
    """
    root = etree.Element("iva")

    # Header
    _sub(root, "TipoIDInformante", "R")  # R = RUC
    _sub(root, "IdInformante", config.get("ruc", ""))
    _sub(root, "razonSocial", config.get("razon_social", ""))
    _sub(root, "Anio", str(year))
    _sub(root, "Mes", f"{month:02d}")

    # Number of establishments
    num_establ = config.get("num_establecimientos", 1)
    _sub(root, "numEstabRuc", f"{num_establ:03d}")

    # Sales totals per establishment
    total_ventas = sum(v.get("base_imponible_0", 0) + v.get("base_imponible_iva", 0) for v in ventas)
    ventas_estab = etree.SubElement(root, "ventasEstab")
    _sub(ventas_estab, "codEstab", config.get("establecimiento", "001"))
    _sub(ventas_estab, "ventasEstab", f"{total_ventas:.2f}")
    _sub(ventas_estab, "ivaComp", "0.00")

    # Detail ventas
    for v in ventas:
        det = etree.SubElement(root, "detalleVentas")
        tipo_id = TIPO_ID.get(v.get("tipo_id_comprador", "cedula"), "05")
        _sub(det, "tpIdCliente", tipo_id)
        _sub(det, "idCliente", v.get("id_comprador", "9999999999999"))
        _sub(det, "tipoComprobante", v.get("tipo_comprobante", "01"))

        # Parse invoice number to parts
        parts = v.get("numero_comprobante", "001-001-000000001").split("-")
        if len(parts) == 3:
            _sub(det, "tipoEmision", "E")
            _sub(det, "numeroComprobantes", "1")
            _sub(det, "baseNoGraIva", "0.00")
            _sub(det, "baseImponible", f"{v.get('base_imponible_0', 0):.2f}")
            _sub(det, "baseImpGrav", f"{v.get('base_imponible_iva', 0):.2f}")
            _sub(det, "montoIva", f"{v.get('monto_iva', 0):.2f}")
            _sub(det, "montoIce", "0.00")
            _sub(det, "valorRetIva", "0.00")
            _sub(det, "valorRetRenta", "0.00")

            # Forma de pago
            pago = etree.SubElement(det, "formasDePago")
            fp = etree.SubElement(pago, "formaPago")
            _sub(fp, "formaPago", _forma_pago_sri(v.get("forma_pago", "efectivo")))
            total_line = v.get("base_imponible_0", 0) + v.get("base_imponible_iva", 0) + v.get("monto_iva", 0)
            _sub(fp, "total", f"{total_line:.2f}")

    # Detail compras (purchases)
    for c in compras:
        det = etree.SubElement(root, "detalleCompras")
        _sub(det, "codSustento", c.get("cod_sustento", "01"))
        tipo_id = TIPO_ID.get(c.get("tipo_id_proveedor", "ruc"), "04")
        _sub(det, "tpIdProv", tipo_id)
        _sub(det, "idProv", c.get("id_proveedor", ""))
        _sub(det, "tipoComprobante", c.get("tipo_comprobante", "01"))
        _sub(det, "fechaRegistro", c.get("fecha_registro", ""))
        _sub(det, "establecimiento", c.get("establecimiento", "001"))
        _sub(det, "puntoEmision", c.get("punto_emision", "001"))
        _sub(det, "secuencial", c.get("secuencial", "000000001"))
        _sub(det, "baseNoGraIva", "0.00")
        _sub(det, "baseImponible", f"{c.get('base_imponible_0', 0):.2f}")
        _sub(det, "baseImpGrav", f"{c.get('base_imponible_iva', 0):.2f}")
        _sub(det, "montoIva", f"{c.get('monto_iva', 0):.2f}")
        _sub(det, "montoIce", "0.00")

        # Retenciones
        if c.get("retenciones"):
            for ret in c["retenciones"]:
                air = etree.SubElement(det, "air")
                _sub(air, "codRetAir", ret.get("codigo", ""))
                _sub(air, "baseImpAir", f"{ret.get('base', 0):.2f}")
                _sub(air, "porcentajeAir", f"{ret.get('porcentaje', 0):.2f}")
                _sub(air, "valRetAir", f"{ret.get('valor', 0):.2f}")

        # Forma de pago
        pago = etree.SubElement(det, "pagoExterior")
        _sub(pago, "pagoLocExt", "01")
        _sub(pago, "paisEfecPago", "NA")
        _sub(pago, "aplicConvDobworktrib", "NA")
        _sub(pago, "pagExtSujRetNorworktLeg", "NA")

        formas = etree.SubElement(det, "formasDePago")
        fp = etree.SubElement(formas, "formaPago")
        _sub(fp, "formaPago", _forma_pago_sri(c.get("forma_pago", "transferencia")))
        total_line = c.get("base_imponible_0", 0) + c.get("base_imponible_iva", 0) + c.get("monto_iva", 0)
        _sub(fp, "total", f"{total_line:.2f}")

    xml_str = etree.tostring(root, encoding="unicode", xml_declaration=True, pretty_print=True)
    return xml_str


def _sub(parent, tag, text):
    """Helper to create sub-element with text."""
    el = etree.SubElement(parent, tag)
    el.text = str(text)
    return el


def _forma_pago_sri(method: str) -> str:
    """Map payment method to SRI forma de pago code."""
    mapping = {
        "efectivo": "01",
        "tarjeta_debito": "16",
        "tarjeta_credito": "19",
        "transferencia": "20",
        "cheque": "02",
        "otros": "15",
    }
    return mapping.get(method, "01")
