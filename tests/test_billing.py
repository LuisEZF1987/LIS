"""Tests basicos para el modulo de facturacion."""
import pytest
import json


def test_health_endpoint(client):
    """Health check responde correctamente."""
    resp = client.get("/health")
    assert resp.status_code == 200


def test_catalog_list(client, auth_headers):
    """Listar catalogo de servicios."""
    resp = client.get("/api/billing/catalog", headers=auth_headers)
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "items" in data
    assert len(data["items"]) > 0


def test_invoice_creation(client, auth_headers):
    """Crear factura borrador."""
    resp = client.post("/api/billing/invoices", json={
        "patient_document": "1712345678",
        "patient_name": "Maria Garcia",
        "lines": [
            {"catalog_id": 1, "description": "Biometria hematica", "quantity": 1, "unit_price": 12.00},
            {"catalog_id": 7, "description": "Glucosa", "quantity": 1, "unit_price": 5.00},
        ]
    }, headers=auth_headers)
    assert resp.status_code == 201
    data = json.loads(resp.data)
    assert data["status"] == "draft"
    assert data["total"] == 17.00


def test_invoice_validation(client, auth_headers):
    """Validar factura asigna numero secuencial."""
    # Create
    resp = client.post("/api/billing/invoices", json={
        "patient_document": "0912345678",
        "patient_name": "Carlos Rodriguez",
        "lines": [
            {"description": "TSH", "quantity": 1, "unit_price": 15.00},
        ]
    }, headers=auth_headers)
    invoice_id = json.loads(resp.data)["id"]

    # Validate
    resp = client.post(f"/api/billing/invoices/{invoice_id}/validate",
                       headers=auth_headers)
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["invoice_number"] is not None
    assert data["status"] == "validated"


def test_payment_application(client, auth_headers):
    """Aplicar pago a factura."""
    # Create and validate invoice
    resp = client.post("/api/billing/invoices", json={
        "patient_document": "0112345678",
        "patient_name": "Ana Martinez",
        "lines": [{"description": "Glucosa", "quantity": 1, "unit_price": 5.00}]
    }, headers=auth_headers)
    invoice_id = json.loads(resp.data)["id"]

    client.post(f"/api/billing/invoices/{invoice_id}/validate", headers=auth_headers)
    client.post(f"/api/billing/invoices/{invoice_id}/post", headers=auth_headers)

    # Apply payment
    resp = client.post("/api/billing/payments/apply", json={
        "invoice_id": invoice_id,
        "amount": 5.00,
        "payment_source": "efectivo",
    }, headers=auth_headers)
    assert resp.status_code == 200


# --- Fixtures ---
@pytest.fixture
def client():
    pytest.skip("Requires database connection")


@pytest.fixture
def auth_headers():
    pytest.skip("Requires JWT secret and user setup")
