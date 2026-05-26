#!/usr/bin/env python3
"""
Dimed LIS — Standalone Laboratory Billing Service.
Facturacion electronica (SRI Ecuador), Cuentas por Cobrar, Pagos y Reportes.

Simplified flow: order -> invoice -> payment (no encounters, prefacturas, doctor fees).
Single database (dimed_lis). Tables prefixed with lis_ (lab tables keep his_lab_*).

Port 9009 (BILLING_PORT env var).

Endpoints:
  POST   /api/billing/invoices                  - Crear factura
  GET    /api/billing/invoices                  - Listar facturas
  GET    /api/billing/invoices/<id>             - Detalle con lineas
  PUT    /api/billing/invoices/<id>             - Actualizar borrador
  POST   /api/billing/invoices/<id>/validate    - Asignar numero SRI
  POST   /api/billing/invoices/<id>/post        - Contabilizar
  POST   /api/billing/invoices/<id>/cancel      - Anular (nota de credito)
  POST   /api/billing/invoices/<id>/sri         - Enviar al SRI
  GET    /api/billing/invoices/<id>/pdf         - Generar RIDE o PDF simple
  GET    /api/billing/cxc                       - CxC con aging
  GET    /api/billing/cxc/summary               - Resumen CxC
  POST   /api/billing/payments/apply            - Aplicar pago
  GET    /api/billing/catalog                   - Catalogo de servicios
  PUT    /api/billing/catalog/<id>              - Actualizar precio
  GET    /api/billing/reports/daily-sales       - Ventas del dia
  GET    /api/billing/reports/revenue-by-category - Ingresos por categoria
  GET    /health                                - Health check
"""
import io
import os
import json
import logging
import functools
from datetime import datetime, timezone, timedelta, date
from decimal import Decimal

import psycopg2
import psycopg2.extras
import psycopg2.pool
import jwt
from flask import Flask, request, jsonify, make_response

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [BILLING] %(levelname)s %(message)s",
)
log = logging.getLogger("billing")

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = Flask(__name__)
BILLING_PORT = int(os.environ.get("BILLING_PORT", "9009"))

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
JWT_SECRET = os.environ["JWT_SECRET"]
DB_HOST = os.environ.get("DB_HOST", "postgres")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "dimed_lis")
DB_USER = os.environ.get("PG_USER", "dimed")
DB_PASS = os.environ.get("PG_PASSWORD", "")

# SRI configuration
SRI_AMBIENTE = os.environ.get("SRI_AMBIENTE", "1")  # 1=pruebas, 2=produccion
SRI_RUC = os.environ.get("SRI_RUC", "")
SRI_RAZON_SOCIAL = os.environ.get("SRI_RAZON_SOCIAL", "")
SRI_NOMBRE_COMERCIAL = os.environ.get("SRI_NOMBRE_COMERCIAL", "")
SRI_DIRECCION_MATRIZ = os.environ.get("SRI_DIRECCION_MATRIZ", "")
SRI_CERT_PATH = os.environ.get("SRI_CERT_PATH", "")
SRI_CERT_PASSWORD = os.environ.get("SRI_CERT_PASSWORD", "")
SRI_OBLIGADO_CONTABILIDAD = os.environ.get("SRI_OBLIGADO_CONTABILIDAD", "SI")

# ---------------------------------------------------------------------------
# Account codes (Plan de Cuentas simplificado)
# ---------------------------------------------------------------------------
ACCOUNT_CASH = "1.1.01"        # Caja / Efectivo
ACCOUNT_CARDS = "1.1.02"       # Tarjetas (debito + credito)
ACCOUNT_TRANSFER = "1.1.03"    # Transferencias bancarias
ACCOUNT_INSURANCE_AR = "1.1.04"  # CxC Aseguradoras
ACCOUNT_PATIENT_AR = "1.1.05"  # CxC Pacientes
ACCOUNT_LAB_REVENUE = "4.1.03" # Ingresos Laboratorio
ACCOUNT_IVA_PAYABLE = "2.1.04" # IVA por pagar

# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------
ADMIN_ROLES = ["admin"]
BILLING_ROLES = ["admin", "recepcion", "contador"]
READ_ROLES = ["admin", "recepcion", "contador", "laboratorista", "bioquimico"]
WRITE_ROLES = ["admin", "recepcion", "contador"]

# ---------------------------------------------------------------------------
# Connection pool — single database
# ---------------------------------------------------------------------------
_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=10,
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
        )
    return _pool


def get_db():
    """Obtain a connection from the pool."""
    return _get_pool().getconn()


def put_db(conn):
    """Return a connection to the pool."""
    try:
        _get_pool().putconn(conn)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
def decode_token(token):
    return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])


def require_auth(allowed_roles=None):
    """JWT authentication decorator with optional role restriction."""
    def decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            # Try Authorization header first, then cookie
            auth_header = request.headers.get("Authorization", "")
            token = None
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
            else:
                token = request.cookies.get("auth_token")
            if not token:
                return jsonify({"error": "Token no proporcionado"}), 401
            try:
                payload = decode_token(token)
            except jwt.ExpiredSignatureError:
                return jsonify({"error": "Token expirado"}), 401
            except jwt.InvalidTokenError:
                return jsonify({"error": "Token invalido"}), 401
            if allowed_roles and payload.get("role") not in allowed_roles:
                return jsonify({"error": "No tiene permisos para esta accion"}), 403
            request.current_user = payload
            return f(*args, **kwargs)
        return wrapper
    return decorator


def _user_branch_ids(cu):
    """Extract branch IDs from JWT payload."""
    return [b["id"] for b in cu.get("branches", [])]


def _user_branch_codes(cu):
    """Extract branch codes from JWT payload."""
    return [b["code"] for b in cu.get("branches", [])]


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------
def _dec(row):
    """Convert Decimal/date fields for JSON serialization."""
    if not row:
        return row
    out = dict(row)
    for k, v in out.items():
        if isinstance(v, Decimal):
            out[k] = float(v)
        elif isinstance(v, (date, datetime)):
            out[k] = v.isoformat()
    return out


# ---------------------------------------------------------------------------
# Tax & account helpers
# ---------------------------------------------------------------------------
def _tax_rate():
    """
    Medical laboratory services: 0% IVA in Ecuador.
    (Ley Organica de Regimen Tributario Interno, Art. 56 - servicios de salud)
    """
    return 0.0


def _payment_account(source):
    """Map payment source to cash/bank account code."""
    return {
        "efectivo": ACCOUNT_CASH,
        "tarjeta_debito": ACCOUNT_CARDS,
        "tarjeta_credito": ACCOUNT_CARDS,
        "transferencia": ACCOUNT_TRANSFER,
    }.get(source, ACCOUNT_CASH)


def _ar_account(party_type):
    """Map party type to accounts receivable code."""
    if party_type == "insurer":
        return ACCOUNT_INSURANCE_AR
    return ACCOUNT_PATIENT_AR


# ---------------------------------------------------------------------------
# SRI invoice number generation
# ---------------------------------------------------------------------------
def _generate_sri_number(cur, establecimiento, punto_emision):
    """
    Generate sequential SRI invoice number.
    Format: {establecimiento}-{punto_emision}-{secuencial:09d}
    e.g. 001-001-000000001
    """
    prefix = f"{establecimiento}-{punto_emision}"
    cur.execute(
        "SELECT MAX(secuencial) AS max_seq FROM lis_invoices "
        "WHERE establecimiento = %s AND punto_emision = %s "
        "AND status != 'draft'",
        (establecimiento, punto_emision),
    )
    row = cur.fetchone()
    next_seq = (row["max_seq"] or 0) + 1
    invoice_number = f"FAC-{prefix}-{next_seq:09d}"
    return invoice_number, next_seq


def _get_fiscal_period(cur, inv_date):
    """Find fiscal period for a date (returns None if table doesn't exist yet)."""
    try:
        cur.execute("SAVEPOINT _fp_check")
        cur.execute(
            "SELECT id FROM lis_fiscal_periods "
            "WHERE start_date <= %s AND end_date >= %s LIMIT 1",
            (inv_date, inv_date),
        )
        row = cur.fetchone()
        cur.execute("RELEASE SAVEPOINT _fp_check")
        return row["id"] if row else None
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT _fp_check")
        return None


def _get_branch_by_code(cur, code):
    """Look up branch by code."""
    cur.execute(
        "SELECT id, establecimiento, punto_emision FROM lis_branches WHERE code = %s",
        (code,),
    )
    return cur.fetchone()


# ===================================================================
# HEALTH CHECK
# ===================================================================
@app.route("/health", methods=["GET"])
def health():
    try:
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        finally:
            put_db(conn)
        return jsonify({"status": "healthy", "service": "billing", "port": BILLING_PORT}), 200
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 503


# ===================================================================
# POST /api/billing/invoices — Create invoice
# ===================================================================
@app.route("/api/billing/invoices", methods=["POST"])
@require_auth(allowed_roles=BILLING_ROLES)
def create_invoice():
    """
    Create a draft invoice from catalog items + patient info.
    Body: {
        patient_document, patient_name, patient_address, patient_email, patient_phone,
        branch_code, insurer_id, insurer_name, notes,
        lines: [{catalog_id, description, quantity, unit_price, discount_percent}]
    }
    """
    cu = request.current_user
    data = request.get_json(silent=True) or {}

    lines_data = data.get("lines", [])
    if not lines_data:
        return jsonify({"error": "Se requiere al menos una linea (lines)"}), 400

    patient_document = data.get("patient_document", "")
    patient_name = data.get("patient_name", "")
    if not patient_document:
        return jsonify({"error": "patient_document es requerido"}), 400

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Look up patient if name not provided
            if not patient_name:
                cur.execute(
                    "SELECT id, firstname, lastname FROM lis_patients "
                    "WHERE document_id = %s LIMIT 1",
                    (patient_document,),
                )
                p = cur.fetchone()
                if p:
                    patient_name = f"{p['firstname']} {p['lastname']}"

            # Resolve branch
            branch_code = data.get("branch_code", "")
            branch_id = None
            establecimiento = "001"
            punto_emision = "001"
            if branch_code:
                br = _get_branch_by_code(cur, branch_code)
                if br:
                    branch_id = br["id"]
                    establecimiento = br.get("establecimiento") or "001"
                    punto_emision = br.get("punto_emision") or "001"

            # Build invoice lines
            lines = []
            for ln in lines_data:
                qty = float(ln.get("quantity", 1))
                price = float(ln.get("unit_price", 0))
                disc = float(ln.get("discount_percent", 0))

                # If catalog_id provided, look up price from catalog
                catalog_id = ln.get("catalog_id")
                description = ln.get("description", "")
                if catalog_id and not price:
                    cur.execute(
                        "SELECT code, name, price, category FROM lis_service_catalog "
                        "WHERE id = %s AND is_active = TRUE",
                        (catalog_id,),
                    )
                    cat_item = cur.fetchone()
                    if cat_item:
                        price = float(cat_item["price"])
                        if not description:
                            description = f"{cat_item['code']} - {cat_item['name']}"

                line_total = round(qty * price * (1 - disc / 100), 2)
                tax_rate = _tax_rate()  # 0% for lab services
                tax_amount = round(line_total * tax_rate / 100, 2)

                lines.append({
                    "catalog_id": catalog_id,
                    "description": description,
                    "quantity": qty,
                    "unit_price": price,
                    "discount_percent": disc,
                    "line_total": line_total,
                    "tax_rate": tax_rate,
                    "tax_amount": tax_amount,
                })

            if not lines:
                return jsonify({"error": "No se generaron lineas de factura"}), 400

            subtotal = round(sum(l["line_total"] for l in lines), 2)
            tax_total = round(sum(l["tax_amount"] for l in lines), 2)
            total = round(subtotal + tax_total, 2)

            # Create invoice (draft, no SRI number yet)
            cur.execute(
                "INSERT INTO lis_invoices "
                "(branch_id, establecimiento, punto_emision, "
                "patient_document, patient_name, patient_address, "
                "patient_email, patient_phone, "
                "subtotal_0, subtotal_iva, iva_amount, total, "
                "insurer_id, insurer_name, notes, created_by) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                (
                    branch_id, establecimiento, punto_emision,
                    patient_document, patient_name,
                    data.get("patient_address", ""),
                    data.get("patient_email", ""),
                    data.get("patient_phone", ""),
                    subtotal, 0, 0, total,  # lab = 0% IVA -> subtotal_0 = subtotal
                    data.get("insurer_id"),
                    data.get("insurer_name"),
                    data.get("notes"),
                    cu.get("user_id"),
                ),
            )
            invoice = _dec(cur.fetchone())
            inv_id = invoice["id"]

            # Insert lines
            for ln in lines:
                cur.execute(
                    "INSERT INTO lis_invoice_lines "
                    "(invoice_id, catalog_id, description, "
                    "quantity, unit_price, discount_percent, "
                    "line_total, tax_rate, tax_amount) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        inv_id, ln.get("catalog_id"), ln["description"],
                        ln["quantity"], ln["unit_price"], ln["discount_percent"],
                        ln["line_total"], ln["tax_rate"], ln["tax_amount"],
                    ),
                )

            # Create accounts receivable record
            party_type = "patient"
            party_id = patient_document
            party_name = patient_name
            if data.get("insurer_id"):
                party_type = "insurer"
                party_id = str(data["insurer_id"])
                party_name = data.get("insurer_name", "Aseguradora")

            due_date = data.get(
                "due_date",
                (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d"),
            )
            cur.execute(
                "INSERT INTO lis_accounts_receivable "
                "(invoice_id, party_type, party_id, party_name, amount, due_date) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (inv_id, party_type, party_id, party_name, total, due_date),
            )

        conn.commit()
        invoice["lines"] = lines
        log.info("Factura creada ID:%s total=$%.2f", inv_id, total)
        return jsonify(invoice), 201

    except Exception as e:
        conn.rollback()
        log.exception("Error al crear factura")
        return jsonify({"error": str(e)}), 500
    finally:
        put_db(conn)


# ===================================================================
# GET /api/billing/invoices — List invoices
# ===================================================================
@app.route("/api/billing/invoices", methods=["GET"])
@require_auth(allowed_roles=READ_ROLES)
def list_invoices():
    cu = request.current_user
    status = request.args.get("status", "")
    patient_doc = request.args.get("patient_document", "")
    branch_id = request.args.get("branch_id", "")
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")
    limit = min(int(request.args.get("limit", "100")), 500)
    offset = int(request.args.get("offset", "0"))

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            conds, params = [], []

            if status:
                conds.append("i.status = %s")
                params.append(status)
            if patient_doc:
                conds.append("i.patient_document = %s")
                params.append(patient_doc)
            if branch_id:
                conds.append("i.branch_id = %s")
                params.append(branch_id)
            if date_from:
                conds.append("i.created_at >= %s")
                params.append(date_from)
            if date_to:
                conds.append("i.created_at <= %s::date + INTERVAL '1 day'")
                params.append(date_to)

            # Branch restriction for non-admin
            if cu.get("role") not in ADMIN_ROLES:
                bids = _user_branch_ids(cu)
                if bids:
                    conds.append("i.branch_id = ANY(%s)")
                    params.append(bids)

            where = "WHERE " + " AND ".join(conds) if conds else ""

            cur.execute(
                f"SELECT COUNT(*) AS total FROM lis_invoices i {where}", params
            )
            total = cur.fetchone()["total"]

            cur.execute(
                f"SELECT i.*, b.code AS branch_code, b.name AS branch_name "
                f"FROM lis_invoices i "
                f"LEFT JOIN lis_branches b ON b.id = i.branch_id "
                f"{where} ORDER BY i.created_at DESC LIMIT %s OFFSET %s",
                params + [limit, offset],
            )
            rows = [_dec(r) for r in cur.fetchall()]

        return jsonify({"invoices": rows, "total": total}), 200

    except Exception:
        log.exception("Error al listar facturas")
        return jsonify({"error": "Error interno del servidor"}), 500
    finally:
        put_db(conn)


# ===================================================================
# GET /api/billing/invoices/<id> — Detail with lines
# ===================================================================
@app.route("/api/billing/invoices/<int:inv_id>", methods=["GET"])
@require_auth(allowed_roles=READ_ROLES)
def get_invoice(inv_id):
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT i.*, b.code AS branch_code, b.name AS branch_name "
                "FROM lis_invoices i "
                "LEFT JOIN lis_branches b ON b.id = i.branch_id "
                "WHERE i.id = %s",
                (inv_id,),
            )
            inv = cur.fetchone()
            if not inv:
                return jsonify({"error": "Factura no encontrada"}), 404
            inv = _dec(inv)

            cur.execute(
                "SELECT * FROM lis_invoice_lines WHERE invoice_id = %s ORDER BY id",
                (inv_id,),
            )
            inv["lines"] = [_dec(r) for r in cur.fetchall()]

            cur.execute(
                "SELECT * FROM lis_accounts_receivable WHERE invoice_id = %s",
                (inv_id,),
            )
            inv["accounts_receivable"] = [_dec(r) for r in cur.fetchall()]

            # SRI info
            cur.execute(
                "SELECT * FROM lis_sri_documents WHERE invoice_id = %s "
                "ORDER BY created_at DESC LIMIT 1",
                (inv_id,),
            )
            sri_doc = cur.fetchone()
            if sri_doc:
                inv["sri"] = _dec(sri_doc)

        return jsonify(inv), 200

    except Exception:
        log.exception("Error al obtener factura")
        return jsonify({"error": "Error interno del servidor"}), 500
    finally:
        put_db(conn)


# ===================================================================
# PUT /api/billing/invoices/<id> — Update draft only
# ===================================================================
@app.route("/api/billing/invoices/<int:inv_id>", methods=["PUT"])
@require_auth(allowed_roles=WRITE_ROLES)
def update_invoice(inv_id):
    data = request.get_json(silent=True) or {}
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT status FROM lis_invoices WHERE id = %s", (inv_id,)
            )
            inv = cur.fetchone()
            if not inv:
                return jsonify({"error": "Factura no encontrada"}), 404
            if inv["status"] != "draft":
                return jsonify({
                    "error": "Solo se pueden editar facturas en borrador"
                }), 400

            # Update allowed fields
            updatable = {
                "notes": data.get("notes"),
                "patient_name": data.get("patient_name"),
                "patient_address": data.get("patient_address"),
                "patient_email": data.get("patient_email"),
                "patient_phone": data.get("patient_phone"),
                "insurer_id": data.get("insurer_id"),
                "insurer_name": data.get("insurer_name"),
            }
            sets = []
            vals = []
            for k, v in updatable.items():
                if v is not None:
                    sets.append(f"{k} = %s")
                    vals.append(v)

            if not sets:
                return jsonify({"error": "Ningun campo para actualizar"}), 400

            sets.append("updated_at = NOW()")
            vals.append(inv_id)

            cur.execute(
                f"UPDATE lis_invoices SET {', '.join(sets)} "
                f"WHERE id = %s RETURNING *",
                vals,
            )
            updated = _dec(cur.fetchone())

            # If lines provided, replace them
            new_lines = data.get("lines")
            if new_lines is not None:
                cur.execute(
                    "DELETE FROM lis_invoice_lines WHERE invoice_id = %s",
                    (inv_id,),
                )
                subtotal = 0
                tax_total = 0
                for ln in new_lines:
                    qty = float(ln.get("quantity", 1))
                    price = float(ln.get("unit_price", 0))
                    disc = float(ln.get("discount_percent", 0))
                    lt = round(qty * price * (1 - disc / 100), 2)
                    tr = _tax_rate()
                    ta = round(lt * tr / 100, 2)
                    subtotal += lt
                    tax_total += ta
                    cur.execute(
                        "INSERT INTO lis_invoice_lines "
                        "(invoice_id, catalog_id, description, quantity, "
                        "unit_price, discount_percent, line_total, tax_rate, tax_amount) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            inv_id, ln.get("catalog_id"), ln.get("description", ""),
                            qty, price, disc, lt, tr, ta,
                        ),
                    )
                total = round(subtotal + tax_total, 2)
                cur.execute(
                    "UPDATE lis_invoices SET subtotal_0 = %s, iva_amount = %s, "
                    "total = %s, updated_at = NOW() WHERE id = %s RETURNING *",
                    (subtotal, tax_total, total, inv_id),
                )
                updated = _dec(cur.fetchone())

                # Update A/R amount
                cur.execute(
                    "UPDATE lis_accounts_receivable SET amount = %s, "
                    "updated_at = NOW() WHERE invoice_id = %s AND status = 'open'",
                    (total, inv_id),
                )

        conn.commit()
        return jsonify(updated), 200

    except Exception:
        conn.rollback()
        log.exception("Error al actualizar factura")
        return jsonify({"error": "Error interno del servidor"}), 500
    finally:
        put_db(conn)


# ===================================================================
# POST /api/billing/invoices/<id>/validate — Assign SRI sequential number
# ===================================================================
@app.route("/api/billing/invoices/<int:inv_id>/validate", methods=["POST"])
@require_auth(allowed_roles=["admin", "contador"])
def validate_invoice(inv_id):
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM lis_invoices WHERE id = %s", (inv_id,))
            inv = cur.fetchone()
            if not inv:
                return jsonify({"error": "Factura no encontrada"}), 404
            if inv["status"] != "draft":
                return jsonify({
                    "error": f"Solo se pueden validar borradores (status: {inv['status']})"
                }), 400

            establecimiento = inv["establecimiento"] or "001"
            punto_emision = inv["punto_emision"] or "001"
            inv_number, secuencial = _generate_sri_number(
                cur, establecimiento, punto_emision
            )

            cur.execute(
                "UPDATE lis_invoices SET status = 'validated', "
                "invoice_number = %s, secuencial = %s, "
                "updated_at = NOW() WHERE id = %s RETURNING *",
                (inv_number, secuencial, inv_id),
            )
            updated = _dec(cur.fetchone())

        conn.commit()
        log.info("Factura validada: %s (seq %d)", inv_number, secuencial)
        return jsonify(updated), 200

    except Exception:
        conn.rollback()
        log.exception("Error al validar factura")
        return jsonify({"error": "Error interno del servidor"}), 500
    finally:
        put_db(conn)


# ===================================================================
# POST /api/billing/invoices/<id>/post — Post to journal
# ===================================================================
@app.route("/api/billing/invoices/<int:inv_id>/post", methods=["POST"])
@require_auth(allowed_roles=["admin", "contador"])
def post_invoice(inv_id):
    cu = request.current_user
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM lis_invoices WHERE id = %s", (inv_id,))
            inv = cur.fetchone()
            if not inv:
                return jsonify({"error": "Factura no encontrada"}), 404
            if inv["status"] != "validated":
                return jsonify({
                    "error": f"Solo se pueden contabilizar facturas validadas "
                             f"(status: {inv['status']})"
                }), 400

            entry_date = (
                inv["created_at"].date() if inv["created_at"] else date.today()
            )
            period_id = _get_fiscal_period(cur, entry_date)
            desc = f"Factura {inv['invoice_number']}"

            # Determine A/R account
            cur.execute(
                "SELECT party_type FROM lis_accounts_receivable "
                "WHERE invoice_id = %s LIMIT 1",
                (inv_id,),
            )
            ar = cur.fetchone()
            ar_acct = _ar_account(ar["party_type"]) if ar else ACCOUNT_PATIENT_AR

            total = float(inv["total"])

            # DR: Accounts Receivable
            cur.execute(
                "INSERT INTO lis_journal_entries "
                "(entry_date, invoice_id, description, account_code, "
                "debit, credit, branch_id, fiscal_period_id, created_by) "
                "VALUES (%s,%s,%s,%s,%s,0,%s,%s,%s)",
                (
                    entry_date, inv_id, desc, ar_acct, total,
                    inv["branch_id"], period_id, cu.get("user_id"),
                ),
            )

            # CR: Lab Revenue (all lines are lab services)
            subtotal = float(inv["subtotal_0"] or 0) + float(inv.get("subtotal_iva") or 0)
            if subtotal > 0:
                cur.execute(
                    "INSERT INTO lis_journal_entries "
                    "(entry_date, invoice_id, description, account_code, "
                    "debit, credit, branch_id, fiscal_period_id, created_by) "
                    "VALUES (%s,%s,%s,%s,0,%s,%s,%s,%s)",
                    (
                        entry_date, inv_id, desc, ACCOUNT_LAB_REVENUE, subtotal,
                        inv["branch_id"], period_id, cu.get("user_id"),
                    ),
                )

            # CR: IVA if applicable (unlikely for lab but be safe)
            iva = float(inv.get("iva_amount") or 0)
            if iva > 0:
                cur.execute(
                    "INSERT INTO lis_journal_entries "
                    "(entry_date, invoice_id, description, account_code, "
                    "debit, credit, branch_id, fiscal_period_id, created_by) "
                    "VALUES (%s,%s,%s,%s,0,%s,%s,%s,%s)",
                    (
                        entry_date, inv_id, desc, ACCOUNT_IVA_PAYABLE, iva,
                        inv["branch_id"], period_id, cu.get("user_id"),
                    ),
                )

            cur.execute(
                "UPDATE lis_invoices SET status = 'posted', updated_at = NOW() "
                "WHERE id = %s RETURNING *",
                (inv_id,),
            )
            updated = _dec(cur.fetchone())

        conn.commit()
        log.info("Factura contabilizada: %s", inv.get("invoice_number"))
        return jsonify(updated), 200

    except Exception:
        conn.rollback()
        log.exception("Error al contabilizar factura")
        return jsonify({"error": "Error interno del servidor"}), 500
    finally:
        put_db(conn)


# ===================================================================
# POST /api/billing/invoices/<id>/cancel — Cancel (credit note)
# ===================================================================
@app.route("/api/billing/invoices/<int:inv_id>/cancel", methods=["POST"])
@require_auth(allowed_roles=["admin", "contador"])
def cancel_invoice(inv_id):
    cu = request.current_user
    data = request.get_json(silent=True) or {}
    reason = data.get("reason", "Anulacion")

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM lis_invoices WHERE id = %s", (inv_id,))
            inv = cur.fetchone()
            if not inv:
                return jsonify({"error": "Factura no encontrada"}), 404
            if inv["status"] not in ("validated", "posted", "sri_authorized"):
                return jsonify({
                    "error": "Solo se pueden anular facturas validadas, "
                             "contabilizadas o autorizadas por SRI"
                }), 400

            # Create credit note
            cn_number = (inv.get("invoice_number") or f"INV-{inv_id}") + "-NC"
            cur.execute(
                "INSERT INTO lis_invoices "
                "(invoice_number, branch_id, establecimiento, punto_emision, "
                "patient_document, patient_name, patient_address, "
                "invoice_type, subtotal_0, subtotal_iva, iva_amount, total, "
                "insurer_id, insurer_name, notes, status, created_by) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,'credit_note',%s,%s,%s,%s,%s,%s,%s,'posted',%s) "
                "RETURNING id",
                (
                    cn_number, inv["branch_id"],
                    inv["establecimiento"], inv["punto_emision"],
                    inv["patient_document"], inv["patient_name"],
                    inv.get("patient_address", ""),
                    -float(inv["subtotal_0"] or 0),
                    -float(inv.get("subtotal_iva") or 0),
                    -float(inv.get("iva_amount") or 0),
                    -float(inv["total"]),
                    inv.get("insurer_id"), inv.get("insurer_name"),
                    f"Nota de credito: {reason}. Ref: {inv.get('invoice_number')}",
                    cu.get("user_id"),
                ),
            )
            cn = cur.fetchone()
            cn_id = cn["id"]

            # Copy lines as negative
            cur.execute(
                "SELECT * FROM lis_invoice_lines WHERE invoice_id = %s", (inv_id,)
            )
            for ln in cur.fetchall():
                cur.execute(
                    "INSERT INTO lis_invoice_lines "
                    "(invoice_id, catalog_id, description, quantity, "
                    "unit_price, discount_percent, line_total, tax_rate, tax_amount) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        cn_id, ln["catalog_id"], ln["description"],
                        -float(ln["quantity"]), float(ln["unit_price"]),
                        float(ln["discount_percent"]),
                        -float(ln["line_total"]),
                        float(ln["tax_rate"]), -float(ln["tax_amount"]),
                    ),
                )

            # Cancel original
            cur.execute(
                "UPDATE lis_invoices SET status = 'cancelled', "
                "cancelled_reason = %s, updated_at = NOW() WHERE id = %s",
                (reason, inv_id),
            )

            # Close A/R
            cur.execute(
                "UPDATE lis_accounts_receivable SET status = 'written_off', "
                "updated_at = NOW() WHERE invoice_id = %s",
                (inv_id,),
            )

            # If was posted, reverse journal entries
            if inv["status"] in ("posted", "sri_authorized"):
                cur.execute(
                    "SELECT * FROM lis_journal_entries WHERE invoice_id = %s",
                    (inv_id,),
                )
                for je in cur.fetchall():
                    cur.execute(
                        "INSERT INTO lis_journal_entries "
                        "(entry_date, invoice_id, description, account_code, "
                        "debit, credit, branch_id, fiscal_period_id, created_by) "
                        "VALUES (CURRENT_DATE,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            cn_id, f"Reverso: {je['description']}",
                            je["account_code"],
                            float(je["credit"]), float(je["debit"]),
                            je["branch_id"], je["fiscal_period_id"],
                            cu.get("user_id"),
                        ),
                    )

        conn.commit()
        log.info(
            "Factura anulada: %s, NC: %s",
            inv.get("invoice_number"), cn_number,
        )
        return jsonify({
            "message": "Factura anulada",
            "credit_note_id": cn_id,
            "credit_note_number": cn_number,
        }), 200

    except Exception:
        conn.rollback()
        log.exception("Error al anular factura")
        return jsonify({"error": "Error interno del servidor"}), 500
    finally:
        put_db(conn)


# ===================================================================
# POST /api/billing/invoices/<id>/sri — Send to SRI
# ===================================================================
@app.route("/api/billing/invoices/<int:inv_id>/sri", methods=["POST"])
@require_auth(allowed_roles=["admin", "contador"])
def send_to_sri(inv_id):
    """Build XML, sign, send to SRI, poll authorization."""
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM lis_invoices WHERE id = %s", (inv_id,))
            inv = cur.fetchone()
            if not inv:
                return jsonify({"error": "Factura no encontrada"}), 404
            if inv["status"] not in ("validated", "posted"):
                return jsonify({
                    "error": "La factura debe estar validada o contabilizada "
                             "para enviar al SRI"
                }), 400
            if not SRI_RUC:
                return jsonify({"error": "SRI_RUC no configurado"}), 400

            cur.execute(
                "SELECT * FROM lis_invoice_lines WHERE invoice_id = %s ORDER BY id",
                (inv_id,),
            )
            lines = [_dec(r) for r in cur.fetchall()]

            _dec(inv)

        # Build SRI config
        sri_config = {
            "ambiente": SRI_AMBIENTE,
            "ruc": SRI_RUC,
            "razon_social": SRI_RAZON_SOCIAL,
            "nombre_comercial": SRI_NOMBRE_COMERCIAL,
            "direccion_matriz": SRI_DIRECCION_MATRIZ,
            "obligado_contabilidad": SRI_OBLIGADO_CONTABILIDAD,
        }

        # Determine document type based on invoice_type
        is_credit_note = inv.get("invoice_type") == "credit_note"

        try:
            from sri.xml_builder import build_factura_xml, build_nota_credito_xml
            from sri.signer import sign_xml
            from sri.ws_client import SriClient

            if is_credit_note:
                xml_str = build_nota_credito_xml(sri_config, inv, lines, {})
            else:
                xml_str = build_factura_xml(sri_config, inv, lines)

            # Sign if certificate configured
            xml_signed = xml_str
            if SRI_CERT_PATH and os.path.exists(SRI_CERT_PATH):
                xml_signed = sign_xml(xml_str, SRI_CERT_PATH, SRI_CERT_PASSWORD)

            # Send to SRI
            client = SriClient()
            ambiente = int(SRI_AMBIENTE)
            recv_result = client.enviar_comprobante(xml_signed, ambiente)

            estado = recv_result.get("estado", "")
            clave_acceso = recv_result.get("clave_acceso", "")

            # Try to get authorization
            auth_result = {}
            if estado == "RECIBIDA":
                import time
                time.sleep(2)  # Brief wait for SRI processing
                auth_result = client.autorizar_comprobante(clave_acceso, ambiente)

            # Store SRI document
            auth_estado = auth_result.get("estado", "")
            auth_numero = ""
            auth_fecha = None
            if auth_result.get("autorizaciones"):
                auth_info = auth_result["autorizaciones"][0]
                auth_estado = auth_info.get("estado", "")
                auth_numero = auth_info.get("numero_autorizacion", "")
                auth_fecha = auth_info.get("fecha_autorizacion")

            conn2 = get_db()
            try:
                with conn2.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur2:
                    cur2.execute(
                        "INSERT INTO lis_sri_documents "
                        "(invoice_id, tipo_comprobante, clave_acceso, "
                        "xml_generado, xml_firmado, estado_recepcion, "
                        "estado_autorizacion, numero_autorizacion, "
                        "fecha_autorizacion, ambiente) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                        (
                            inv_id,
                            "04" if is_credit_note else "01",
                            clave_acceso,
                            xml_str, xml_signed,
                            estado, auth_estado,
                            auth_numero, auth_fecha,
                            SRI_AMBIENTE,
                        ),
                    )
                    sri_doc = _dec(cur2.fetchone())

                    # Update invoice status if authorized
                    new_status = inv["status"]
                    if auth_estado == "AUTORIZADO":
                        new_status = "sri_authorized"
                    elif estado == "RECIBIDA":
                        new_status = "sri_received"

                    cur2.execute(
                        "UPDATE lis_invoices SET status = %s, "
                        "sri_clave_acceso = %s, updated_at = NOW() "
                        "WHERE id = %s",
                        (new_status, clave_acceso, inv_id),
                    )
                conn2.commit()
            finally:
                put_db(conn2)

            log.info(
                "SRI enviado factura %s: recepcion=%s, autorizacion=%s",
                inv.get("invoice_number"), estado, auth_estado,
            )
            return jsonify({
                "estado_recepcion": estado,
                "estado_autorizacion": auth_estado,
                "clave_acceso": clave_acceso,
                "numero_autorizacion": auth_numero,
                "sri_document": sri_doc,
            }), 200

        except ImportError as e:
            log.warning("Modulos SRI no disponibles: %s", e)
            return jsonify({
                "error": "Modulos SRI no instalados o no disponibles",
                "detail": str(e),
            }), 501
        except Exception as e:
            log.exception("Error al enviar al SRI")
            return jsonify({"error": f"Error SRI: {str(e)}"}), 500

    except Exception:
        log.exception("Error al preparar envio SRI")
        return jsonify({"error": "Error interno del servidor"}), 500
    finally:
        put_db(conn)


# ===================================================================
# GET /api/billing/invoices/<id>/pdf — Generate RIDE or simple PDF
# ===================================================================
@app.route("/api/billing/invoices/<int:inv_id>/pdf", methods=["GET"])
@require_auth(allowed_roles=READ_ROLES)
def invoice_pdf(inv_id):
    """Generate RIDE PDF (if SRI authorized) or simple invoice PDF."""
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT i.*, b.name AS branch_name, b.address AS branch_address, "
                "b.phone AS branch_phone "
                "FROM lis_invoices i "
                "LEFT JOIN lis_branches b ON b.id = i.branch_id "
                "WHERE i.id = %s",
                (inv_id,),
            )
            inv = cur.fetchone()
            if not inv:
                return jsonify({"error": "Factura no encontrada"}), 404
            _dec(inv)

            cur.execute(
                "SELECT * FROM lis_invoice_lines WHERE invoice_id = %s ORDER BY id",
                (inv_id,),
            )
            lines = [_dec(r) for r in cur.fetchall()]

            # Check for SRI authorization
            sri_data = None
            try:
                cur.execute("SAVEPOINT _sri_check")
                cur.execute(
                    "SELECT * FROM sri_comprobantes WHERE invoice_id = %s "
                    "AND estado_autorizacion = 'AUTORIZADO' "
                    "ORDER BY created_at DESC LIMIT 1",
                    (inv_id,),
                )
                sri_row = cur.fetchone()
                cur.execute("RELEASE SAVEPOINT _sri_check")
                if sri_row:
                    sri_data = _dec(sri_row)
            except Exception:
                cur.execute("ROLLBACK TO SAVEPOINT _sri_check")

        # Try RIDE PDF first if SRI authorized
        if sri_data:
            try:
                from sri.ride_pdf import generate_ride
                config = {
                    "razon_social": SRI_RAZON_SOCIAL,
                    "nombre_comercial": SRI_NOMBRE_COMERCIAL,
                    "ruc": SRI_RUC,
                    "direccion_matriz": SRI_DIRECCION_MATRIZ,
                    "obligado_contabilidad": SRI_OBLIGADO_CONTABILIDAD,
                }
                pdf_bytes = generate_ride(inv, sri_data, config)
                response = make_response(pdf_bytes)
                response.headers["Content-Type"] = "application/pdf"
                fname = inv.get("invoice_number") or f"factura-{inv_id}"
                response.headers["Content-Disposition"] = (
                    f"inline; filename={fname}.pdf"
                )
                return response
            except ImportError:
                log.warning("ride_pdf module not available, falling back to simple PDF")

        # Simple PDF fallback
        pdf_bytes = _generate_simple_pdf(inv, lines)
        response = make_response(pdf_bytes)
        response.headers["Content-Type"] = "application/pdf"
        fname = inv.get("invoice_number") or f"factura-{inv_id}"
        response.headers["Content-Disposition"] = f"inline; filename={fname}.pdf"
        return response

    except Exception:
        log.exception("Error al generar PDF")
        return jsonify({"error": "Error interno del servidor"}), 500
    finally:
        put_db(conn)


def _generate_simple_pdf(inv, lines):
    """Generate a professional invoice PDF styled for Ecuador labs."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch, mm
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image,
    )

    # Colors
    PRIMARY = colors.HexColor("#0f172a")
    ACCENT = colors.HexColor("#1e40af")
    LIGHT_BG = colors.HexColor("#f8fafc")
    BORDER = colors.HexColor("#cbd5e1")
    DARK_TEXT = colors.HexColor("#1e293b")
    MUTED = colors.HexColor("#64748b")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        topMargin=0.4 * inch, bottomMargin=0.5 * inch,
    )
    styles = getSampleStyleSheet()

    s_company = ParagraphStyle("company", parent=styles["Normal"],
        fontSize=13, fontName="Helvetica-Bold", textColor=PRIMARY,
        spaceAfter=1)
    s_company_detail = ParagraphStyle("company_detail", parent=styles["Normal"],
        fontSize=8, textColor=MUTED, spaceAfter=1)
    s_doc_title = ParagraphStyle("doc_title", parent=styles["Normal"],
        fontSize=12, fontName="Helvetica-Bold", textColor=ACCENT,
        alignment=TA_RIGHT, spaceAfter=2)
    s_doc_num = ParagraphStyle("doc_num", parent=styles["Normal"],
        fontSize=10, fontName="Helvetica-Bold", alignment=TA_RIGHT,
        spaceAfter=1)
    s_doc_detail = ParagraphStyle("doc_detail", parent=styles["Normal"],
        fontSize=8, textColor=MUTED, alignment=TA_RIGHT, spaceAfter=1)
    s_label = ParagraphStyle("label", parent=styles["Normal"],
        fontSize=8, fontName="Helvetica-Bold", textColor=MUTED)
    s_value = ParagraphStyle("value", parent=styles["Normal"],
        fontSize=9, textColor=DARK_TEXT)
    s_section = ParagraphStyle("section", parent=styles["Normal"],
        fontSize=9, fontName="Helvetica-Bold", textColor=ACCENT,
        spaceBefore=8, spaceAfter=4)
    s_footer = ParagraphStyle("footer", parent=styles["Normal"],
        fontSize=7, textColor=MUTED, alignment=TA_CENTER)
    s_right = ParagraphStyle("right", parent=styles["Normal"],
        fontSize=8, alignment=TA_RIGHT)
    s_cell = ParagraphStyle("cell", parent=styles["Normal"], fontSize=8)
    s_cell_bold = ParagraphStyle("cell_bold", parent=styles["Normal"],
        fontSize=8, fontName="Helvetica-Bold")
    s_total_label = ParagraphStyle("total_label", parent=styles["Normal"],
        fontSize=9, fontName="Helvetica-Bold", textColor=DARK_TEXT,
        alignment=TA_RIGHT)
    s_total_value = ParagraphStyle("total_value", parent=styles["Normal"],
        fontSize=9, alignment=TA_RIGHT)
    s_grand_total_label = ParagraphStyle("gt_label", parent=styles["Normal"],
        fontSize=11, fontName="Helvetica-Bold", textColor=PRIMARY,
        alignment=TA_RIGHT)
    s_grand_total = ParagraphStyle("gt_value", parent=styles["Normal"],
        fontSize=12, fontName="Helvetica-Bold", textColor=ACCENT,
        alignment=TA_RIGHT)

    elements = []

    company_name = SRI_RAZON_SOCIAL or "Laboratorio"
    company_ruc = SRI_RUC or ""
    company_addr = SRI_DIRECCION_MATRIZ or ""
    company_trade = SRI_NOMBRE_COMERCIAL or ""
    obligado = SRI_OBLIGADO_CONTABILIDAD or "SI"

    # Logo
    logo_path = os.environ.get("LOGO_PATH", "/certs/logo.png")
    has_logo = os.path.isfile(logo_path)

    inv_type = inv.get("invoice_type", "out")
    doc_label = "NOTA DE CREDITO" if inv_type == "credit_note" else "FACTURA"
    inv_number = inv.get("invoice_number") or "BORRADOR"
    sri_number = inv_number
    if sri_number.startswith("FAC-"):
        sri_number = sri_number[4:]

    fecha = str(inv.get("created_at", ""))[:10]
    ambiente = "PRODUCCION" if str(os.environ.get("SRI_AMBIENTE", "1")) == "2" else "PRUEBAS"
    estab = inv.get("establecimiento", "001")
    pto = inv.get("punto_emision", "001")

    # ===== HEADER: Logo + Company | Document =====
    company_parts = [f"<b>{company_name}</b>"]
    if company_trade and company_trade != company_name:
        company_parts.append(company_trade)
    if company_ruc:
        company_parts.append(f"<b>RUC:</b> {company_ruc}")
    if company_addr:
        company_parts.append(f"<b>Dir. Matriz:</b> {company_addr}")
    company_parts.append(f"<b>Obligado a llevar contabilidad:</b> {obligado}")
    company_parts.append(f"<b>Establecimiento:</b> {estab} | <b>Pto. Emision:</b> {pto}")
    company_html = "<br/>".join(company_parts)

    right_parts = [
        f"<b><font size='12' color='#1e40af'>{doc_label}</font></b>",
        f"<b>No.</b> {sri_number}",
        f"<b>Ambiente:</b> {ambiente}",
        f"<b>Emision:</b> NORMAL",
        f"<b>Fecha:</b> {fecha}",
    ]
    right_html = "<br/>".join(right_parts)

    if has_logo:
        logo_img = Image(logo_path, width=1.1 * inch, height=0.7 * inch)
        logo_img.hAlign = "LEFT"
        left_cell = Table(
            [[logo_img, Paragraph(company_html, ParagraphStyle("lh", parent=s_cell,
                fontSize=8, leading=12))]],
            colWidths=[1.2 * inch, 2.4 * inch],
        )
        left_cell.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]))
    else:
        left_cell = Paragraph(company_html, ParagraphStyle("lh", parent=s_cell,
            fontSize=8, leading=12))

    header_data = [[
        left_cell,
        Paragraph(right_html, ParagraphStyle("rh", parent=s_cell,
            fontSize=8, leading=12, alignment=TA_RIGHT)),
    ]]
    header_table = Table(header_data, colWidths=[3.7 * inch, 3.3 * inch])
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("BOX", (0, 0), (0, 0), 0.75, ACCENT),
        ("BOX", (1, 0), (1, 0), 0.75, ACCENT),
        ("BACKGROUND", (1, 0), (1, 0), colors.HexColor("#f0f4ff")),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 10))

    # ===== BUYER INFO =====
    pat_name = inv.get("patient_name", "CONSUMIDOR FINAL")
    pat_doc = inv.get("patient_document", "9999999999999")
    pat_addr = inv.get("patient_address", "")
    pat_email = inv.get("patient_email", "")
    pat_phone = inv.get("patient_phone", "")

    buyer_rows = [
        [Paragraph("<b>Razon Social / Nombres:</b>", s_cell),
         Paragraph(pat_name, s_value),
         Paragraph("<b>Identificacion:</b>", s_cell),
         Paragraph(pat_doc, s_value)],
    ]
    if pat_addr or pat_phone:
        buyer_rows.append([
            Paragraph("<b>Direccion:</b>", s_cell),
            Paragraph(pat_addr or "-", s_cell),
            Paragraph("<b>Telefono:</b>", s_cell),
            Paragraph(pat_phone or "-", s_cell),
        ])
    if pat_email:
        buyer_rows.append([
            Paragraph("<b>Email:</b>", s_cell),
            Paragraph(pat_email, s_cell),
            Paragraph("", s_cell),
            Paragraph("", s_cell),
        ])

    buyer_table = Table(buyer_rows,
        colWidths=[1.2 * inch, 2.5 * inch, 1 * inch, 2.3 * inch])
    buyer_table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e2e8f0")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BG),
    ]))
    elements.append(buyer_table)
    elements.append(Spacer(1, 10))

    # ===== DETAIL LINES =====
    detail_header = [
        Paragraph("<b>Cod.</b>", s_cell_bold),
        Paragraph("<b>Descripcion</b>", s_cell_bold),
        Paragraph("<b>Cant.</b>", s_cell_bold),
        Paragraph("<b>P. Unitario</b>", s_cell_bold),
        Paragraph("<b>Descuento</b>", s_cell_bold),
        Paragraph("<b>P. Total</b>", s_cell_bold),
    ]
    detail_data = [detail_header]

    for ln in lines:
        qty = float(ln.get("quantity", 1))
        up = float(ln.get("unit_price", 0))
        disc = float(ln.get("discount_percent", 0))
        lt = float(ln.get("line_total", 0))
        disc_amt = round(qty * up * disc / 100, 2)

        detail_data.append([
            Paragraph(str(ln.get("catalog_id") or ln.get("code", "-")), s_cell),
            Paragraph((ln.get("description") or "")[:70], s_cell),
            Paragraph(f"{qty:.2f}", s_right),
            Paragraph(f"${up:.2f}", s_right),
            Paragraph(f"${disc_amt:.2f}", s_right),
            Paragraph(f"${lt:.2f}", s_right),
        ])

    detail_table = Table(detail_data,
        colWidths=[0.6 * inch, 3.2 * inch, 0.55 * inch, 0.8 * inch, 0.75 * inch, 0.8 * inch])
    detail_style = [
        ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    for i in range(1, len(detail_data)):
        if i % 2 == 0:
            detail_style.append(
                ("BACKGROUND", (0, i), (-1, i), LIGHT_BG))
    detail_table.setStyle(TableStyle(detail_style))
    elements.append(detail_table)
    elements.append(Spacer(1, 8))

    # ===== TOTALS =====
    subtotal_0 = float(inv.get("subtotal_0") or inv.get("subtotal") or 0)
    subtotal_iva = float(inv.get("subtotal_iva", 0) or 0)
    iva_amount = float(inv.get("iva_amount", 0) or 0)
    total = float(inv.get("total", 0) or 0)

    totals_rows = [
        [Paragraph("SUBTOTAL 0%", s_total_label),
         Paragraph(f"${subtotal_0:.2f}", s_total_value)],
        [Paragraph("SUBTOTAL IVA%", s_total_label),
         Paragraph(f"${subtotal_iva:.2f}", s_total_value)],
        [Paragraph("SUBTOTAL SIN IMPUESTOS", s_total_label),
         Paragraph(f"${subtotal_0 + subtotal_iva:.2f}", s_total_value)],
        [Paragraph("IVA", s_total_label),
         Paragraph(f"${iva_amount:.2f}", s_total_value)],
        [Paragraph("VALOR TOTAL", s_grand_total_label),
         Paragraph(f"${total:.2f}", s_grand_total)],
    ]

    totals_inner = Table(totals_rows, colWidths=[2.2 * inch, 1 * inch])
    totals_inner.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e2e8f0")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#eff6ff")),
        ("LINEABOVE", (0, -1), (-1, -1), 1, ACCENT),
    ]))

    totals_wrapper = Table(
        [["", totals_inner]],
        colWidths=[3.8 * inch, 3.2 * inch])
    totals_wrapper.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    elements.append(totals_wrapper)
    elements.append(Spacer(1, 10))

    # ===== PAYMENT METHOD =====
    source_labels = {
        "efectivo": "Efectivo", "tarjeta_debito": "Tarjeta de Debito",
        "tarjeta_credito": "Tarjeta de Credito", "transferencia": "Transferencia Bancaria",
    }
    status = inv.get("status", "draft")
    if status == "paid":
        pay_label = "Sin utilizacion del sistema financiero"
    else:
        pay_label = "Pendiente de cobro"

    pay_data = [
        [Paragraph("<b>Forma de Pago</b>", s_cell_bold),
         Paragraph("<b>Valor</b>", ParagraphStyle("phb", parent=s_cell_bold, alignment=TA_RIGHT))],
        [Paragraph(pay_label, s_cell),
         Paragraph(f"${total:.2f}", s_right)],
    ]
    pay_table = Table(pay_data, colWidths=[5.5 * inch, 1.5 * inch])
    pay_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#334155")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(pay_table)

    # ===== NOTES =====
    if inv.get("notes"):
        elements.append(Spacer(1, 8))
        elements.append(Paragraph("<b>Informacion Adicional:</b>", s_cell_bold))
        elements.append(Paragraph(inv["notes"], s_cell))

    # ===== FOOTER =====
    elements.append(Spacer(1, 20))
    elements.append(Paragraph(
        "Documento generado electronicamente — Dimed-LIS",
        s_footer))

    doc.build(elements)
    buf.seek(0)
    return buf.read()


# ===================================================================
# GET /api/billing/cxc — Accounts receivable with aging
# ===================================================================
@app.route("/api/billing/cxc", methods=["GET"])
@require_auth(allowed_roles=BILLING_ROLES)
def list_cxc():
    cu = request.current_user
    status_filter = request.args.get("status", "")
    party_type = request.args.get("party_type", "")
    patient_doc = request.args.get("patient_document", "")

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            conds, params = [], []
            if status_filter:
                conds.append("ar.status = %s")
                params.append(status_filter)
            if party_type:
                conds.append("ar.party_type = %s")
                params.append(party_type)
            if patient_doc:
                conds.append("ar.party_id = %s")
                params.append(patient_doc)

            # Branch restriction
            if cu.get("role") not in ADMIN_ROLES:
                bids = _user_branch_ids(cu)
                if bids:
                    conds.append("i.branch_id = ANY(%s)")
                    params.append(bids)

            where = "WHERE " + " AND ".join(conds) if conds else ""

            cur.execute(
                f"SELECT ar.*, i.invoice_number, i.branch_id, "
                f"i.patient_name, i.patient_document, "
                f"CASE "
                f"  WHEN ar.due_date IS NULL THEN 'current' "
                f"  WHEN CURRENT_DATE - ar.due_date <= 0 THEN 'current' "
                f"  WHEN CURRENT_DATE - ar.due_date <= 30 THEN '1_30' "
                f"  WHEN CURRENT_DATE - ar.due_date <= 60 THEN '31_60' "
                f"  WHEN CURRENT_DATE - ar.due_date <= 90 THEN '61_90' "
                f"  ELSE '90_plus' END AS aging_bucket "
                f"FROM lis_accounts_receivable ar "
                f"JOIN lis_invoices i ON i.id = ar.invoice_id "
                f"{where} ORDER BY ar.due_date ASC",
                params,
            )
            rows = [_dec(r) for r in cur.fetchall()]

            # Aging summary
            aging = {
                "current": 0, "1_30": 0, "31_60": 0,
                "61_90": 0, "90_plus": 0,
            }
            for r in rows:
                bucket = r.get("aging_bucket", "current")
                balance = r.get("amount", 0) - r.get("paid_amount", 0)
                aging[bucket] = aging.get(bucket, 0) + balance

        return jsonify({
            "receivables": rows,
            "aging": aging,
            "total": len(rows),
        }), 200

    except Exception:
        log.exception("Error al listar CxC")
        return jsonify({"error": "Error interno del servidor"}), 500
    finally:
        put_db(conn)


# ===================================================================
# GET /api/billing/cxc/summary — Summary totals
# ===================================================================
@app.route("/api/billing/cxc/summary", methods=["GET"])
@require_auth(allowed_roles=BILLING_ROLES)
def cxc_summary():
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT party_type, party_name, "
                "COUNT(*) AS count, "
                "SUM(amount) AS total_amount, "
                "SUM(paid_amount) AS total_paid, "
                "SUM(amount - paid_amount) AS total_balance "
                "FROM lis_accounts_receivable "
                "WHERE status IN ('open', 'partial') "
                "GROUP BY party_type, party_name "
                "ORDER BY total_balance DESC"
            )
            rows = [_dec(r) for r in cur.fetchall()]

            # Grand totals
            cur.execute(
                "SELECT "
                "SUM(amount) AS total_amount, "
                "SUM(paid_amount) AS total_paid, "
                "SUM(amount - paid_amount) AS total_balance, "
                "COUNT(*) AS total_count "
                "FROM lis_accounts_receivable "
                "WHERE status IN ('open', 'partial')"
            )
            grand = _dec(cur.fetchone()) or {}

        return jsonify({
            "by_party": rows,
            "grand_total": grand,
        }), 200

    except Exception:
        log.exception("Error al obtener resumen CxC")
        return jsonify({"error": "Error interno del servidor"}), 500
    finally:
        put_db(conn)


# ===================================================================
# POST /api/billing/payments/apply — Apply payment
# ===================================================================
@app.route("/api/billing/payments/apply", methods=["POST"])
@require_auth(allowed_roles=BILLING_ROLES)
def apply_payment():
    """
    Apply a payment to an accounts receivable.
    Body: {receivable_id, amount, payment_source, payment_reference}
    payment_source: efectivo, tarjeta_debito, tarjeta_credito, transferencia
    """
    cu = request.current_user
    data = request.get_json(silent=True) or {}

    recv_id = data.get("receivable_id")
    invoice_id = data.get("invoice_id")
    amount = float(data.get("amount", 0))
    source = data.get("payment_source", "efectivo")
    reference = data.get("payment_reference", "")

    if not recv_id and not invoice_id:
        return jsonify({
            "error": "receivable_id o invoice_id son requeridos"
        }), 400
    if amount <= 0:
        return jsonify({"error": "amount debe ser > 0"}), 400

    valid_sources = ("efectivo", "tarjeta_debito", "tarjeta_credito", "transferencia")
    if source not in valid_sources:
        return jsonify({
            "error": f"payment_source invalido. Opciones: {', '.join(valid_sources)}"
        }), 400

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Resolve invoice_id to receivable if needed
            if not recv_id and invoice_id:
                cur.execute(
                    "SELECT id FROM lis_accounts_receivable "
                    "WHERE invoice_id = %s AND status != 'paid' "
                    "ORDER BY id LIMIT 1",
                    (invoice_id,),
                )
                row = cur.fetchone()
                if not row:
                    return jsonify({"error": "No hay cuenta por cobrar para esta factura"}), 404
                recv_id = row["id"]

            cur.execute(
                "SELECT ar.*, i.branch_id, i.id AS inv_id, i.invoice_number "
                "FROM lis_accounts_receivable ar "
                "JOIN lis_invoices i ON i.id = ar.invoice_id "
                "WHERE ar.id = %s",
                (recv_id,),
            )
            ar = cur.fetchone()
            if not ar:
                return jsonify({"error": "Cuenta por cobrar no encontrada"}), 404
            ar = _dec(ar)

            balance = ar["amount"] - ar["paid_amount"]
            if amount > balance + 0.01:
                return jsonify({
                    "error": f"Monto excede el saldo pendiente (${balance:.2f})"
                }), 400

            # Update A/R
            new_paid = ar["paid_amount"] + amount
            new_status = "paid" if new_paid >= ar["amount"] - 0.01 else "partial"

            cur.execute(
                "UPDATE lis_accounts_receivable SET paid_amount = %s, "
                "status = %s, updated_at = NOW() WHERE id = %s",
                (new_paid, new_status, recv_id),
            )

            # Record payment application
            cur.execute(
                "INSERT INTO lis_payment_applications "
                "(receivable_id, amount, payment_source, "
                "payment_reference, applied_by) "
                "VALUES (%s,%s,%s,%s,%s) RETURNING id",
                (
                    recv_id, amount, source,
                    reference, cu.get("user_id"),
                ),
            )
            app_id = cur.fetchone()["id"]

            # Journal entry: DR cash/bank, CR A/R
            cash_acct = _payment_account(source)
            ar_acct = _ar_account(ar.get("party_type", "patient"))
            period_id = _get_fiscal_period(cur, date.today())
            desc = f"Pago recibido ({source}): {ar.get('invoice_number', '')}"

            # DR: Cash/Bank
            cur.execute(
                "INSERT INTO lis_journal_entries "
                "(entry_date, invoice_id, description, account_code, "
                "debit, credit, branch_id, fiscal_period_id, created_by) "
                "VALUES (CURRENT_DATE,%s,%s,%s,%s,0,%s,%s,%s)",
                (
                    ar["inv_id"], desc, cash_acct, amount,
                    ar["branch_id"], period_id, cu.get("user_id"),
                ),
            )
            # CR: A/R
            cur.execute(
                "INSERT INTO lis_journal_entries "
                "(entry_date, invoice_id, description, account_code, "
                "debit, credit, branch_id, fiscal_period_id, created_by) "
                "VALUES (CURRENT_DATE,%s,%s,%s,0,%s,%s,%s,%s)",
                (
                    ar["inv_id"], desc, ar_acct, amount,
                    ar["branch_id"], period_id, cu.get("user_id"),
                ),
            )

            # If fully paid, update invoice status
            if new_status == "paid":
                cur.execute(
                    "SELECT COUNT(*) AS c FROM lis_accounts_receivable "
                    "WHERE invoice_id = %s AND status != 'paid'",
                    (ar["inv_id"],),
                )
                if cur.fetchone()["c"] == 0:
                    cur.execute(
                        "UPDATE lis_invoices SET status = 'paid', "
                        "updated_at = NOW() WHERE id = %s",
                        (ar["inv_id"],),
                    )

        conn.commit()
        log.info(
            "Pago aplicado: $%.2f a CxC #%s (%s)", amount, recv_id, source,
        )
        return jsonify({
            "application_id": app_id,
            "receivable_id": recv_id,
            "amount_applied": amount,
            "payment_source": source,
            "new_status": new_status,
            "remaining_balance": round(balance - amount, 2),
        }), 200

    except Exception as e:
        conn.rollback()
        log.exception("Error al aplicar pago")
        return jsonify({"error": str(e)}), 500
    finally:
        put_db(conn)


# ===================================================================
# GET /api/billing/catalog — Service catalog
# ===================================================================
@app.route("/api/billing/catalog", methods=["GET"])
@require_auth(allowed_roles=READ_ROLES)
def list_catalog():
    """List service catalog with optional search/filter."""
    search = request.args.get("q", "")
    category = request.args.get("category", "")
    active_only = request.args.get("active", "true").lower() == "true"

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            conds, params = [], []
            if active_only:
                conds.append("is_active = TRUE")
            if category:
                conds.append("category = %s")
                params.append(category)
            if search:
                conds.append("(name ILIKE %s OR code ILIKE %s)")
                params.extend([f"%{search}%", f"%{search}%"])

            where = "WHERE " + " AND ".join(conds) if conds else ""

            cur.execute(
                f"SELECT * FROM lis_service_catalog {where} "
                f"ORDER BY category, name",
                params,
            )
            rows = [_dec(r) for r in cur.fetchall()]

        return jsonify({"items": rows, "total": len(rows)}), 200

    except Exception:
        log.exception("Error al listar catalogo")
        return jsonify({"error": "Error interno del servidor"}), 500
    finally:
        put_db(conn)


# ===================================================================
# PUT /api/billing/catalog/<id> — Update price
# ===================================================================
@app.route("/api/billing/catalog/<int:item_id>", methods=["PUT"])
@require_auth(allowed_roles=["admin", "contador"])
def update_catalog_item(item_id):
    data = request.get_json(silent=True) or {}
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM lis_service_catalog WHERE id = %s", (item_id,)
            )
            item = cur.fetchone()
            if not item:
                return jsonify({"error": "Item no encontrado"}), 404

            updatable = {
                "price": data.get("price"),
                "name": data.get("name"),
                "category": data.get("category"),
                "is_active": data.get("is_active"),
            }
            sets = []
            vals = []
            for k, v in updatable.items():
                if v is not None:
                    sets.append(f"{k} = %s")
                    vals.append(v)

            if not sets:
                return jsonify({"error": "Ningun campo para actualizar"}), 400

            sets.append("updated_at = NOW()")
            vals.append(item_id)

            cur.execute(
                f"UPDATE lis_service_catalog SET {', '.join(sets)} "
                f"WHERE id = %s RETURNING *",
                vals,
            )
            updated = _dec(cur.fetchone())

        conn.commit()
        log.info("Catalogo actualizado: item #%s", item_id)
        return jsonify(updated), 200

    except Exception:
        conn.rollback()
        log.exception("Error al actualizar catalogo")
        return jsonify({"error": "Error interno del servidor"}), 500
    finally:
        put_db(conn)


# ===================================================================
# GET /api/billing/reports/daily-sales — Daily sales report
# ===================================================================
@app.route("/api/billing/reports/daily-sales", methods=["GET"])
@require_auth(allowed_roles=BILLING_ROLES)
def daily_sales():
    report_date = request.args.get("date", date.today().isoformat())
    branch_id = request.args.get("branch_id", "")

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            conds = ["i.created_at::date = %s"]
            params = [report_date]
            if branch_id:
                conds.append("i.branch_id = %s")
                params.append(branch_id)
            conds.append("i.status NOT IN ('draft', 'cancelled')")
            where = "WHERE " + " AND ".join(conds)

            # Summary
            cur.execute(
                f"SELECT "
                f"COUNT(*) AS invoice_count, "
                f"COALESCE(SUM(i.total), 0) AS total_sales, "
                f"COALESCE(SUM(i.subtotal_0), 0) AS subtotal_0, "
                f"COALESCE(SUM(i.iva_amount), 0) AS total_iva "
                f"FROM lis_invoices i {where}",
                params,
            )
            summary = _dec(cur.fetchone())

            # By payment source
            cur.execute(
                f"SELECT pa.payment_source, "
                f"COUNT(*) AS count, "
                f"COALESCE(SUM(pa.amount), 0) AS total "
                f"FROM lis_payment_applications pa "
                f"JOIN lis_accounts_receivable ar ON ar.id = pa.receivable_id "
                f"JOIN lis_invoices i ON i.id = ar.invoice_id "
                f"{where} AND pa.applied_at::date = %s "
                f"GROUP BY pa.payment_source ORDER BY total DESC",
                params + [report_date],
            )
            by_source = [_dec(r) for r in cur.fetchall()]

            # Individual invoices
            cur.execute(
                f"SELECT i.id, i.invoice_number, i.patient_name, "
                f"i.patient_document, i.total, i.status, "
                f"i.created_at "
                f"FROM lis_invoices i {where} "
                f"ORDER BY i.created_at",
                params,
            )
            invoices = [_dec(r) for r in cur.fetchall()]

        return jsonify({
            "date": report_date,
            "summary": summary,
            "by_payment_source": by_source,
            "invoices": invoices,
        }), 200

    except Exception:
        log.exception("Error en reporte de ventas diarias")
        return jsonify({"error": "Error interno del servidor"}), 500
    finally:
        put_db(conn)


# ===================================================================
# GET /api/billing/reports/revenue-by-category — Revenue by test category
# ===================================================================
@app.route("/api/billing/reports/revenue-by-category", methods=["GET"])
@require_auth(allowed_roles=BILLING_ROLES)
def revenue_by_category():
    date_from = request.args.get(
        "date_from", date.today().replace(day=1).isoformat()
    )
    date_to = request.args.get("date_to", date.today().isoformat())
    branch_id = request.args.get("branch_id", "")

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            conds = [
                "i.status IN ('posted', 'paid', 'sri_authorized')",
                "i.created_at >= %s",
                "i.created_at <= %s::date + INTERVAL '1 day'",
            ]
            params = [date_from, date_to]
            if branch_id:
                conds.append("i.branch_id = %s")
                params.append(branch_id)

            where = "WHERE " + " AND ".join(conds)

            # Revenue by catalog category
            cur.execute(
                f"SELECT "
                f"COALESCE(sc.category, 'Sin categoria') AS category, "
                f"COUNT(DISTINCT i.id) AS invoice_count, "
                f"SUM(il.quantity) AS total_tests, "
                f"SUM(il.line_total) AS revenue "
                f"FROM lis_invoice_lines il "
                f"JOIN lis_invoices i ON i.id = il.invoice_id "
                f"LEFT JOIN lis_service_catalog sc ON sc.id = il.catalog_id "
                f"{where} "
                f"GROUP BY sc.category "
                f"ORDER BY revenue DESC",
                params,
            )
            by_category = [_dec(r) for r in cur.fetchall()]

            total_revenue = sum(r.get("revenue", 0) for r in by_category)

            # Top services
            cur.execute(
                f"SELECT il.description, "
                f"SUM(il.quantity) AS total_count, "
                f"SUM(il.line_total) AS revenue "
                f"FROM lis_invoice_lines il "
                f"JOIN lis_invoices i ON i.id = il.invoice_id "
                f"{where} "
                f"GROUP BY il.description "
                f"ORDER BY revenue DESC LIMIT 20",
                params,
            )
            top_services = [_dec(r) for r in cur.fetchall()]

        return jsonify({
            "period": {"from": date_from, "to": date_to},
            "by_category": by_category,
            "top_services": top_services,
            "total_revenue": round(total_revenue, 2),
        }), 200

    except Exception:
        log.exception("Error en reporte de ingresos por categoria")
        return jsonify({"error": "Error interno del servidor"}), 500
    finally:
        put_db(conn)


# ===================================================================
# Startup
# ===================================================================
if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    log.info("Billing service starting on port %d (debug=%s)", BILLING_PORT, debug)
    app.run(host="0.0.0.0", port=BILLING_PORT, debug=debug)
