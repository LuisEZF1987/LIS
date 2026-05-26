"""Tests basicos para el modulo LIS."""
import pytest
import json


def test_health_endpoint(client):
    """Health check responde correctamente."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "ok"


def test_analytes_list_requires_auth(client):
    """Listar analitos requiere autenticacion."""
    resp = client.get("/api/erp/lis/analytes")
    assert resp.status_code == 401


def test_analytes_crud(client, auth_headers):
    """Crear, listar y actualizar analito."""
    # Create
    resp = client.post("/api/erp/lis/analytes", json={
        "code": "TEST-001",
        "name": "Prueba Test",
        "category": "quimica",
        "unit": "mg/dL",
        "sample_type": "sangre",
    }, headers=auth_headers)
    assert resp.status_code == 201
    data = json.loads(resp.data)
    analyte_id = data["id"]

    # List
    resp = client.get("/api/erp/lis/analytes", headers=auth_headers)
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert any(a["code"] == "TEST-001" for a in data["items"])

    # Update
    resp = client.put(f"/api/erp/lis/analytes/{analyte_id}", json={
        "name": "Prueba Test Actualizada",
    }, headers=auth_headers)
    assert resp.status_code == 200


def test_sample_creation(client, auth_headers):
    """Crear muestra de laboratorio."""
    resp = client.post("/api/erp/lis/samples", json={
        "patient_document": "1712345678",
        "patient_name": "Maria Garcia",
        "sample_type": "sangre",
    }, headers=auth_headers)
    assert resp.status_code == 201
    data = json.loads(resp.data)
    assert "sample_code" in data


def test_result_validation_workflow(client, auth_headers):
    """Flujo de validacion tecnica y medica de resultados."""
    # Create sample
    resp = client.post("/api/erp/lis/samples", json={
        "patient_document": "0912345678",
        "patient_name": "Carlos Rodriguez",
        "sample_type": "sangre",
    }, headers=auth_headers)
    sample_id = json.loads(resp.data)["id"]

    # Create analyte for result
    resp = client.post("/api/erp/lis/analytes", json={
        "code": "TEST-VAL-001",
        "name": "Glucosa Validacion",
        "category": "quimica",
        "unit": "mg/dL",
    }, headers=auth_headers)
    analyte_id = json.loads(resp.data)["id"]

    # Enter result
    resp = client.post("/api/erp/lis/results", json={
        "sample_id": sample_id,
        "analyte_id": analyte_id,
        "value": "95.5",
        "numeric_value": 95.5,
    }, headers=auth_headers)
    assert resp.status_code == 201
    result_id = json.loads(resp.data)["id"]

    # Technical validation
    resp = client.post(f"/api/erp/lis/results/{result_id}/validate-tech",
                       headers=auth_headers)
    assert resp.status_code == 200

    # Medical validation
    resp = client.post(f"/api/erp/lis/results/{result_id}/validate-med",
                       headers=auth_headers)
    assert resp.status_code == 200


# --- Fixtures ---
@pytest.fixture
def client():
    """Flask test client — requires LIS app to be importable."""
    pytest.skip("Requires database connection")


@pytest.fixture
def auth_headers():
    """JWT auth headers for testing."""
    pytest.skip("Requires JWT secret and user setup")
