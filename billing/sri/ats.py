#!/usr/bin/env python3
"""
SRI Ecuador — ATS (Anexo Transaccional Simplificado) XML Generator.

Monthly report of sales and purchases required by SRI Ecuador.
"""
import logging
from lxml import etree

log = logging.getLogger("sri.ats")

# Tipo de identificacion SRI
TIPO_ID = {
    "ruc": "04",
    "cedula": "05",
    "pasaporte": "06",
    "consumidor_final": "07",
}


def generate_ats(
    year: int,
    month: int,
    invoices: list,
    purchases: list,
    config: dict,
) -> str:
    """
    Generate ATS XML for a given month.

    Parameters:
        year: Fiscal year (e.g. 2026)
        month: Month number (1-12)
        invoices: List of sale dicts with:
            - tipo_comprobante: '01' factura, '04' nota credito
            - tipo_id_comprador: 'cedula', 'ruc', 'pasaporte', 'consumidor_final'
            - id_comprador: document number
            - base_imponible_0: amount at 0% IVA
            - base_imponible_iva: amount at IVA rate
            - monto_iva: IVA amount
            - numero_comprobante: invoice number (001-001-000000001)
            - fecha_emision: 'DD/MM/YYYY'
            - forma_pago: 'efectivo', 'tarjeta_debito', etc.
        purchases: List of purchase dicts with:
            - cod_sustento: sustento code
            - tipo_id_proveedor: 'ruc', etc.
            - id_proveedor: document number
            - tipo_comprobante: '01' factura, etc.
            - fecha_registro: 'DD/MM/YYYY'
            - establecimiento, punto_emision, secuencial
            - base_imponible_0, base_imponible_iva, monto_iva
            - retenciones: list of {codigo, base, porcentaje, valor}
            - forma_pago: payment method
        config: Institution config dict with:
            - ruc, razon_social, establecimiento,
              num_establecimientos

    Returns:
        ATS XML string
    """
    root = etree.Element("iva")

    # ===================================================================
    # Header: Informante identification
    # ===================================================================
    _sub(root, "TipoIDInformante", "R")  # R = RUC
    _sub(root, "IdInformante", config.get("ruc", ""))
    _sub(root, "razonSocial", config.get("razon_social", ""))
    _sub(root, "Anio", str(year))
    _sub(root, "Mes", f"{month:02d}")

    num_establ = config.get("num_establecimientos", 1)
    _sub(root, "numEstabRuc", f"{num_establ:03d}")

    # ===================================================================
    # ventasEstab: Sales totals per establishment
    # ===================================================================
    total_ventas = sum(
        float(v.get("base_imponible_0", 0)) + float(v.get("base_imponible_iva", 0))
        for v in invoices
    )
    ventas_estab = etree.SubElement(root, "ventasEstab")
    _sub(ventas_estab, "codEstab", config.get("establecimiento", "001"))
    _sub(ventas_estab, "ventasEstab", f"{total_ventas:.2f}")
    _sub(ventas_estab, "ivaComp", "0.00")

    # ===================================================================
    # detalleVentas: Individual sale records
    # ===================================================================
    for v in invoices:
        det = etree.SubElement(root, "detalleVentas")

        tipo_id = TIPO_ID.get(v.get("tipo_id_comprador", "cedula"), "05")
        _sub(det, "tpIdCliente", tipo_id)
        _sub(det, "idCliente", v.get("id_comprador", "9999999999999"))

        partes_comprob = v.get("partes_relacionadas", "NO")
        _sub(det, "parteRelVtas", partes_comprob)

        _sub(det, "tipoComprobante", v.get("tipo_comprobante", "01"))
        _sub(det, "tipoEmision", "E")  # E = Electronica
        _sub(det, "numeroComprobantes", "1")

        base_0 = float(v.get("base_imponible_0", 0))
        base_iva = float(v.get("base_imponible_iva", 0))
        monto_iva = float(v.get("monto_iva", 0))

        _sub(det, "baseNoGraIva", "0.00")
        _sub(det, "baseImponible", f"{base_0:.2f}")
        _sub(det, "baseImpGrav", f"{base_iva:.2f}")
        _sub(det, "montoIva", f"{monto_iva:.2f}")
        _sub(det, "montoIce", "0.00")
        _sub(det, "valorRetIva", "0.00")
        _sub(det, "valorRetRenta", "0.00")

        # Formas de pago
        formas = etree.SubElement(det, "formasDePago")
        fp = etree.SubElement(formas, "formaPago")
        _sub(fp, "formaPago", _forma_pago_sri(v.get("forma_pago", "efectivo")))
        total_line = base_0 + base_iva + monto_iva
        _sub(fp, "total", f"{total_line:.2f}")

    # ===================================================================
    # detalleCompras: Individual purchase records
    # ===================================================================
    for c in purchases:
        det = etree.SubElement(root, "detalleCompras")

        _sub(det, "codSustento", c.get("cod_sustento", "01"))
        tipo_id_prov = TIPO_ID.get(c.get("tipo_id_proveedor", "ruc"), "04")
        _sub(det, "tpIdProv", tipo_id_prov)
        _sub(det, "idProv", c.get("id_proveedor", ""))
        _sub(det, "tipoComprobante", c.get("tipo_comprobante", "01"))
        _sub(det, "parteRel", c.get("parte_relacionada", "NO"))
        _sub(det, "fechaRegistro", c.get("fecha_registro", ""))
        _sub(det, "establecimiento", c.get("establecimiento", "001"))
        _sub(det, "puntoEmision", c.get("punto_emision", "001"))
        _sub(det, "secuencial", c.get("secuencial", "000000001"))

        base_0 = float(c.get("base_imponible_0", 0))
        base_iva = float(c.get("base_imponible_iva", 0))
        monto_iva = float(c.get("monto_iva", 0))

        _sub(det, "baseNoGraIva", "0.00")
        _sub(det, "baseImponible", f"{base_0:.2f}")
        _sub(det, "baseImpGrav", f"{base_iva:.2f}")
        _sub(det, "baseImpExe", "0.00")
        _sub(det, "montoIva", f"{monto_iva:.2f}")
        _sub(det, "montoIce", "0.00")

        # Retenciones en la fuente (Income tax withholding)
        if c.get("retenciones"):
            for ret in c["retenciones"]:
                air = etree.SubElement(det, "air")
                _sub(air, "codRetAir", ret.get("codigo", ""))
                _sub(air, "baseImpAir", f"{float(ret.get('base', 0)):.2f}")
                _sub(air, "porcentajeAir", f"{float(ret.get('porcentaje', 0)):.2f}")
                _sub(air, "valRetAir", f"{float(ret.get('valor', 0)):.2f}")

        # Pago exterior
        pago_ext = etree.SubElement(det, "pagoExterior")
        _sub(pago_ext, "pagoLocExt", "01")  # 01 = local
        _sub(pago_ext, "paisEfecPago", "NA")
        _sub(pago_ext, "aplicConvDobTrib", "NA")
        _sub(pago_ext, "pagExtSujRetNorLeg", "NA")

        # Formas de pago
        formas = etree.SubElement(det, "formasDePago")
        fp = etree.SubElement(formas, "formaPago")
        _sub(
            fp, "formaPago",
            _forma_pago_sri(c.get("forma_pago", "transferencia")),
        )
        total_line = base_0 + base_iva + monto_iva
        _sub(fp, "total", f"{total_line:.2f}")

    # Serialize
    xml_str = etree.tostring(
        root, encoding="unicode", xml_declaration=True, pretty_print=True,
    )

    log.info("ATS generado: %d ventas, %d compras, periodo %04d-%02d",
             len(invoices), len(purchases), year, month)
    return xml_str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _sub(parent, tag: str, text: str):
    """Create a sub-element with text content."""
    el = etree.SubElement(parent, tag)
    el.text = str(text)
    return el


def _forma_pago_sri(method: str) -> str:
    """Map payment method to SRI forma de pago code."""
    mapping = {
        "efectivo": "01",
        "cheque": "02",
        "tarjeta_debito": "16",
        "tarjeta_credito": "19",
        "transferencia": "20",
        "otros": "15",
    }
    return mapping.get(method, "01")
