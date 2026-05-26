"""Tests para el modulo SRI (facturacion electronica)."""
import pytest
from billing.sri.xml_builder import build_clave_acceso, _modulo11


class TestClaveAcceso:
    """Tests para generacion de clave de acceso SRI."""

    def test_clave_acceso_length(self):
        """Clave de acceso debe tener 49 digitos."""
        clave = build_clave_acceso(
            fecha="26/05/2026",
            tipo_comprobante="01",
            ruc="1791234567001",
            ambiente="1",
            establecimiento="001",
            punto_emision="001",
            secuencial="000000001",
            codigo_numerico="12345678",
        )
        assert len(clave) == 49
        assert clave.isdigit()

    def test_clave_acceso_structure(self):
        """Verificar estructura de la clave de acceso."""
        clave = build_clave_acceso(
            fecha="15/03/2026",
            tipo_comprobante="01",
            ruc="1791234567001",
            ambiente="1",
            establecimiento="001",
            punto_emision="001",
            secuencial="000000001",
            codigo_numerico="99999999",
        )
        # fecha(8) + tipo(2) + ruc(13) + ambiente(1) + serie(6) + seq(9) + cod(8) + emision(1) + check(1)
        assert clave[0:8] == "15032026"   # ddmmyyyy
        assert clave[8:10] == "01"        # tipo comprobante
        assert clave[10:23] == "1791234567001"  # RUC
        assert clave[23:24] == "1"        # ambiente
        assert clave[24:30] == "001001"   # serie
        assert clave[30:39] == "000000001"  # secuencial
        assert clave[39:47] == "99999999"  # codigo numerico
        assert clave[47:48] == "1"        # tipo emision

    def test_modulo11(self):
        """Verificar calculo de digito verificador modulo 11."""
        # Known test case
        result = _modulo11("123456789012345678901234567890123456789012345678")
        assert isinstance(result, int)
        assert 0 <= result <= 9

    def test_clave_acceso_nota_credito(self):
        """Clave de acceso para nota de credito (tipo 04)."""
        clave = build_clave_acceso(
            fecha="26/05/2026",
            tipo_comprobante="04",
            ruc="1791234567001",
            ambiente="2",
            establecimiento="001",
            punto_emision="001",
            secuencial="000000001",
            codigo_numerico="87654321",
        )
        assert len(clave) == 49
        assert clave[8:10] == "04"
        assert clave[23:24] == "2"


class TestATS:
    """Tests para generacion de ATS."""

    def test_ats_generation(self):
        """Generar ATS basico."""
        from billing.sri.ats import generate_ats

        config = {
            "ruc": "1791234567001",
            "razon_social": "Lab Demo S.A.",
            "establecimiento": "001",
        }
        ventas = [{
            "tipo_comprobante": "01",
            "tipo_id_comprador": "cedula",
            "id_comprador": "1712345678",
            "base_imponible_0": 50.00,
            "base_imponible_iva": 0,
            "monto_iva": 0,
            "numero_comprobante": "001-001-000000001",
            "forma_pago": "efectivo",
        }]

        xml = generate_ats(2026, 5, ventas, [], config)
        assert "1791234567001" in xml
        assert "<Anio>2026</Anio>" in xml
        assert "<Mes>05</Mes>" in xml


class TestRidePdf:
    """Tests para generacion de RIDE PDF."""

    def test_ride_generation(self):
        """Generar RIDE PDF basico."""
        from billing.sri.ride_pdf import generate_ride

        invoice_data = {
            "invoice_number": "001-001-000000001",
            "patient_document": "1712345678",
            "patient_name": "Maria Garcia",
            "subtotal": 50.00,
            "tax_amount": 0,
            "total": 50.00,
            "lines": [
                {"code": "LAB-HEM-001", "description": "Biometria hematica",
                 "quantity": 1, "unit_price": 12.00, "discount": 0, "line_total": 12.00},
            ],
        }
        sri_data = {
            "clave_acceso": "2605202601179123456700110010010000000011234567811",
            "numero_autorizacion": "2605202601179123456700110010010000000011234567811",
            "fecha_autorizacion": "26/05/2026 10:30:00",
        }
        config = {
            "ruc": "1791234567001",
            "razon_social": "Laboratorio Demo S.A.",
            "direccion_matriz": "Av. Principal 123, Quito",
            "ambiente": 1,
        }

        pdf_bytes = generate_ride(invoice_data, sri_data, config)
        assert isinstance(pdf_bytes, bytes)
        assert len(pdf_bytes) > 0
        assert pdf_bytes[:4] == b"%PDF"  # Valid PDF header
