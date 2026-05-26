#!/usr/bin/env python3
"""
Dimed LIS — Standalone Laboratory Information System.
Analitos, Muestras, Resultados, Instrumentos, QC, Validacion doble.

Runs independently on port 9008. Shares the gnuhealth PostgreSQL database
with HIS (Option A — shared DB, API boundary). Can run on the same server
or a different server.

Endpoints: /api/erp/lis/* (same paths as the former blueprint for compat)
"""
import io
import json
import os
import logging
import functools
from datetime import datetime, timezone, timedelta, date
from decimal import Decimal

import psycopg2
import psycopg2.extras
import psycopg2.pool
import jwt
import requests as _req
from flask import Flask, request, jsonify, make_response

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [LIS] %(levelname)s %(message)s")
log = logging.getLogger("lis")

app = Flask(__name__)
LIS_PORT = int(os.environ.get("LIS_PORT", "9008"))

JWT_SECRET = os.environ["JWT_SECRET"]
DB_HOST = os.environ.get("DB_HOST", "postgres")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
GH_DB_NAME = os.environ.get("GH_DB_NAME", "gnuhealth")
DB_USER = os.environ.get("PG_USER", "healthit")
DB_PASS = os.environ.get("PG_PASSWORD", "")
NOTIFY_URL = os.environ.get("NOTIFY_URL", "http://notification-service:9004")

_pool_gh = None

def _get_pool_gh():
    global _pool_gh
    if _pool_gh is None:
        _pool_gh = psycopg2.pool.ThreadedConnectionPool(
            minconn=1, maxconn=5,
            host=DB_HOST, port=DB_PORT, dbname=GH_DB_NAME,
            user=DB_USER, password=DB_PASS,
        )
    return _pool_gh

def get_db():
    return _get_pool_gh().getconn()

def put_db(conn):
    try: _get_pool_gh().putconn(conn)
    except Exception: pass

def decode_token(token):
    return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])

def require_auth(allowed_roles=None):
    def decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                return jsonify({"error": "Token no proporcionado"}), 401
            token = auth_header[7:]
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

erp_lis_bp = app  # alias so @erp_lis_bp.route(...) decorators still work

ADMIN_ROLES = ["super_admin", "admin_sede", "admin"]
LAB_ROLES = ADMIN_ROLES + ["laboratorista", "bioquimico"]
LAB_MED_ROLES = LAB_ROLES + ["medico", "director_medico"]
READ_ROLES = LAB_MED_ROLES + ["enfermeria", "recepcion", "contador"]


def _dec(row):
    if not row:
        return row
    out = dict(row)
    for k, v in out.items():
        if isinstance(v, Decimal):
            out[k] = float(v)
        elif isinstance(v, (date, datetime)):
            out[k] = v.isoformat()
    return out


def _generate_sample_code(cur, branch_code):
    prefix = f"LAB-{branch_code or 'GEN'}-{datetime.now().strftime('%Y%m%d')}"
    cur.execute(
        "SELECT COUNT(*) AS c FROM his_lab_samples WHERE sample_code LIKE %s",
        (f"{prefix}-%",)
    )
    seq = (cur.fetchone()["c"] or 0) + 1
    return f"{prefix}-{seq:04d}"


def _flag_result(numeric_value, analyte_id, cur, gender="all", age=None):
    """Auto-flag result against reference ranges."""
    if numeric_value is None:
        return "normal", ""

    sql = """
        SELECT range_low, range_high, critical_low, critical_high, unit
        FROM his_lab_reference_ranges
        WHERE analyte_id = %s AND (gender = %s OR gender = 'all')
    """
    params = [analyte_id, gender]
    if age is not None:
        sql += " AND age_min <= %s AND age_max >= %s"
        params.extend([age, age])
    sql += " ORDER BY CASE WHEN gender = %s THEN 0 ELSE 1 END LIMIT 1"
    params.append(gender)
    cur.execute(sql, params)
    ref = cur.fetchone()
    if not ref:
        return "normal", ""

    v = float(numeric_value)
    crit_low = float(ref["critical_low"]) if ref["critical_low"] is not None else None
    crit_high = float(ref["critical_high"]) if ref["critical_high"] is not None else None
    low = float(ref["range_low"]) if ref["range_low"] is not None else None
    high = float(ref["range_high"]) if ref["range_high"] is not None else None

    range_text = ""
    if low is not None and high is not None:
        range_text = f"{low} - {high}"
        if ref.get("unit"):
            range_text += f" {ref['unit']}"

    if crit_low is not None and v < crit_low:
        return "critical_low", range_text
    if crit_high is not None and v > crit_high:
        return "critical_high", range_text
    if low is not None and v < low:
        return "low", range_text
    if high is not None and v > high:
        return "high", range_text
    return "normal", range_text


# ===================================================================
# ANALYTES
# ===================================================================

@erp_lis_bp.route("/api/erp/lis/analytes", methods=["GET"])
@require_auth(allowed_roles=READ_ROLES)
def list_analytes():
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            category = request.args.get("category")
            search = request.args.get("q")
            sql = "SELECT * FROM his_lab_analytes WHERE is_active = TRUE"
            params = []
            if category:
                sql += " AND category = %s"
                params.append(category)
            if search:
                sql += " AND (name ILIKE %s OR code ILIKE %s)"
                params.extend([f"%{search}%"] * 2)
            sql += " ORDER BY category, name"
            cur.execute(sql, params)
            return jsonify([_dec(r) for r in cur.fetchall()])
    finally:
        put_db(conn)


@erp_lis_bp.route("/api/erp/lis/analytes", methods=["POST"])
@require_auth(allowed_roles=LAB_ROLES)
def create_analyte():
    data = request.get_json(silent=True) or {}
    required = ["code", "name", "category"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Campos requeridos: {', '.join(missing)}"}), 400
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO his_lab_analytes
                (code, name, category, unit, decimal_places, method, sample_type)
                VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *
            """, (
                data["code"], data["name"], data["category"],
                data.get("unit"), data.get("decimal_places", 2),
                data.get("method"), data.get("sample_type", "sangre"),
            ))
            analyte = _dec(cur.fetchone())
        conn.commit()
        return jsonify(analyte), 201
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        return jsonify({"error": f"Codigo {data['code']} ya existe"}), 409
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        put_db(conn)


@erp_lis_bp.route("/api/erp/lis/analytes/<int:analyte_id>", methods=["PUT"])
@require_auth(allowed_roles=LAB_ROLES)
def update_analyte(analyte_id):
    data = request.get_json(silent=True) or {}
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id FROM his_lab_analytes WHERE id = %s", (analyte_id,))
            if not cur.fetchone():
                return jsonify({"error": "Analito no encontrado"}), 404
            fields, params = [], []
            for col in ["name", "category", "unit", "decimal_places", "method", "sample_type", "is_active"]:
                if col in data:
                    fields.append(f"{col} = %s")
                    params.append(data[col])
            if not fields:
                return jsonify({"error": "Sin campos"}), 400
            fields.append("updated_at = NOW()")
            params.append(analyte_id)
            cur.execute(f"UPDATE his_lab_analytes SET {', '.join(fields)} WHERE id = %s RETURNING *", params)
            result = _dec(cur.fetchone())
        conn.commit()
        return jsonify(result)
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        put_db(conn)


# ===================================================================
# REFERENCE RANGES
# ===================================================================

@erp_lis_bp.route("/api/erp/lis/reference-ranges", methods=["GET"])
@require_auth(allowed_roles=READ_ROLES)
def list_reference_ranges():
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            analyte_id = request.args.get("analyte_id")
            sql = """
                SELECT rr.*, a.code AS analyte_code, a.name AS analyte_name
                FROM his_lab_reference_ranges rr
                JOIN his_lab_analytes a ON a.id = rr.analyte_id
                WHERE 1=1
            """
            params = []
            if analyte_id:
                sql += " AND rr.analyte_id = %s"
                params.append(int(analyte_id))
            sql += " ORDER BY a.name, rr.gender, rr.age_min"
            cur.execute(sql, params)
            return jsonify([_dec(r) for r in cur.fetchall()])
    finally:
        put_db(conn)


@erp_lis_bp.route("/api/erp/lis/reference-ranges", methods=["POST"])
@require_auth(allowed_roles=LAB_ROLES)
def create_reference_range():
    data = request.get_json(silent=True) or {}
    if not data.get("analyte_id"):
        return jsonify({"error": "analyte_id requerido"}), 400
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO his_lab_reference_ranges
                (analyte_id, gender, age_min, age_max, range_low, range_high,
                 critical_low, critical_high, unit, notes)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *
            """, (
                data["analyte_id"], data.get("gender", "all"),
                data.get("age_min", 0), data.get("age_max", 999),
                data.get("range_low"), data.get("range_high"),
                data.get("critical_low"), data.get("critical_high"),
                data.get("unit"), data.get("notes"),
            ))
            rr = _dec(cur.fetchone())
        conn.commit()
        return jsonify(rr), 201
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        put_db(conn)


# ===================================================================
# SAMPLES
# ===================================================================

@erp_lis_bp.route("/api/erp/lis/samples", methods=["POST"])
@require_auth(allowed_roles=LAB_ROLES + ["recepcion", "enfermeria"])
def create_sample():
    cu = request.current_user
    data = request.get_json(silent=True) or {}
    if not data.get("patient_document"):
        return jsonify({"error": "patient_document requerido"}), 400
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            sample_code = _generate_sample_code(cur, data.get("branch_code"))
            cur.execute("""
                INSERT INTO his_lab_samples
                (sample_code, patient_document, patient_name, encounter_id,
                 service_order_id, sample_type, collected_by, branch_code, notes)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *
            """, (
                sample_code, data["patient_document"], data.get("patient_name"),
                data.get("encounter_id"), data.get("service_order_id"),
                data.get("sample_type", "sangre"), cu.get("user_id"),
                data.get("branch_code"), data.get("notes"),
            ))
            sample = _dec(cur.fetchone())

            # Auto-create pending results for requested analytes
            analyte_ids = data.get("analyte_ids", [])
            results_created = 0
            for aid in analyte_ids:
                cur.execute("""
                    INSERT INTO his_lab_results (sample_id, analyte_id, unit)
                    SELECT %s, id, unit FROM his_lab_analytes WHERE id = %s
                """, (sample["id"], aid))
                results_created += cur.rowcount

            sample["results_created"] = results_created
        conn.commit()
        log.info("Muestra creada: %s paciente:%s", sample_code, data["patient_document"])
        return jsonify(sample), 201
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        put_db(conn)


@erp_lis_bp.route("/api/erp/lis/samples", methods=["GET"])
@require_auth(allowed_roles=READ_ROLES)
def list_samples():
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            patient_doc = request.args.get("patient_document")
            status = request.args.get("status")
            branch = request.args.get("branch_code")
            date_from = request.args.get("from")
            date_to = request.args.get("to")
            sql = """
                SELECT s.*,
                       COUNT(r.id) AS total_results,
                       COUNT(CASE WHEN r.status = 'final' THEN 1 END) AS final_results
                FROM his_lab_samples s
                LEFT JOIN his_lab_results r ON r.sample_id = s.id
                WHERE 1=1
            """
            params = []
            if patient_doc:
                sql += " AND s.patient_document = %s"
                params.append(patient_doc)
            if status:
                sql += " AND s.status = %s"
                params.append(status)
            if branch:
                sql += " AND s.branch_code = %s"
                params.append(branch)
            if date_from:
                sql += " AND s.collection_date >= %s"
                params.append(date_from)
            if date_to:
                sql += " AND s.collection_date <= %s::date + 1"
                params.append(date_to)
            encounter_id = request.args.get("encounter_id")
            if encounter_id:
                sql += " AND s.encounter_id = %s"
                params.append(int(encounter_id))
            sql += " GROUP BY s.id ORDER BY s.collection_date DESC LIMIT 500"
            cur.execute(sql, params)
            return jsonify([_dec(r) for r in cur.fetchall()])
    finally:
        put_db(conn)


@erp_lis_bp.route("/api/erp/lis/samples/<int:sample_id>", methods=["GET"])
@require_auth(allowed_roles=READ_ROLES)
def get_sample(sample_id):
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM his_lab_samples WHERE id = %s", (sample_id,))
            sample = cur.fetchone()
            if not sample:
                return jsonify({"error": "Muestra no encontrada"}), 404
            sample = _dec(sample)

            cur.execute("""
                SELECT r.*, a.code AS analyte_code, a.name AS analyte_name,
                       a.category, i.name AS instrument_name
                FROM his_lab_results r
                JOIN his_lab_analytes a ON a.id = r.analyte_id
                LEFT JOIN his_lab_instruments i ON i.id = r.instrument_id
                WHERE r.sample_id = %s
                ORDER BY a.category, a.name
            """, (sample_id,))
            sample["results"] = [_dec(r) for r in cur.fetchall()]
            return jsonify(sample)
    finally:
        put_db(conn)


@erp_lis_bp.route("/api/erp/lis/samples/<int:sample_id>/status", methods=["PUT"])
@require_auth(allowed_roles=LAB_ROLES)
def update_sample_status(sample_id):
    data = request.get_json(silent=True) or {}
    new_status = data.get("status")
    if new_status not in ("received", "in_process", "completed", "rejected"):
        return jsonify({"error": "status invalido"}), 400
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cu = request.current_user
            updates = ["status = %s", "updated_at = NOW()"]
            params = [new_status]
            if new_status == "received":
                updates.extend(["received_date = NOW()", "received_by = %s"])
                params.append(cu.get("user_id"))
            if new_status == "rejected":
                updates.append("rejection_reason = %s")
                params.append(data.get("rejection_reason", ""))
            params.append(sample_id)
            cur.execute(
                f"UPDATE his_lab_samples SET {', '.join(updates)} WHERE id = %s RETURNING *",
                params
            )
            result = cur.fetchone()
            if not result:
                return jsonify({"error": "Muestra no encontrada"}), 404
            result = _dec(result)
        conn.commit()
        return jsonify(result)
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        put_db(conn)


# ===================================================================
# RESULTS
# ===================================================================

@erp_lis_bp.route("/api/erp/lis/results", methods=["POST"])
@require_auth(allowed_roles=LAB_ROLES)
def enter_results():
    data = request.get_json(silent=True) or {}
    results = data.get("results", [])
    if not results:
        return jsonify({"error": "results[] requerido"}), 400
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            entered = []
            for r in results:
                sample_id = r.get("sample_id")
                analyte_id = r.get("analyte_id")
                value = r.get("value", "")
                numeric_value = r.get("numeric_value")
                if numeric_value is not None:
                    numeric_value = float(numeric_value)

                # Auto-flag
                flag, range_text = _flag_result(numeric_value, analyte_id, cur,
                                                r.get("gender", "all"), r.get("age"))

                # Check if result already exists (update) or insert new
                cur.execute(
                    "SELECT id FROM his_lab_results WHERE sample_id = %s AND analyte_id = %s",
                    (sample_id, analyte_id)
                )
                existing = cur.fetchone()
                if existing:
                    cur.execute("""
                        UPDATE his_lab_results SET
                            value = %s, numeric_value = %s, unit = %s,
                            flag = %s, reference_range_text = %s,
                            instrument_id = %s, raw_value = %s,
                            updated_at = NOW()
                        WHERE id = %s RETURNING *
                    """, (
                        value, numeric_value, r.get("unit"),
                        flag, range_text, r.get("instrument_id"), r.get("raw_value"),
                        existing["id"],
                    ))
                else:
                    cur.execute("""
                        INSERT INTO his_lab_results
                        (sample_id, analyte_id, value, numeric_value, unit,
                         flag, reference_range_text, instrument_id, raw_value)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *
                    """, (
                        sample_id, analyte_id, value, numeric_value, r.get("unit"),
                        flag, range_text, r.get("instrument_id"), r.get("raw_value"),
                    ))
                entered.append(_dec(cur.fetchone()))

            # Update sample status to in_process
            if results:
                sample_ids = set(r.get("sample_id") for r in results if r.get("sample_id"))
                for sid in sample_ids:
                    cur.execute("""
                        UPDATE his_lab_samples SET status = 'in_process', updated_at = NOW()
                        WHERE id = %s AND status IN ('collected', 'received')
                    """, (sid,))

        conn.commit()
        return jsonify({"entered": len(entered), "results": entered}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        put_db(conn)


@erp_lis_bp.route("/api/erp/lis/results/<int:result_id>", methods=["PUT"])
@require_auth(allowed_roles=LAB_ROLES)
def correct_result(result_id):
    data = request.get_json(silent=True) or {}
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM his_lab_results WHERE id = %s", (result_id,))
            res = cur.fetchone()
            if not res:
                return jsonify({"error": "Resultado no encontrado"}), 404

            numeric_value = data.get("numeric_value")
            if numeric_value is not None:
                flag, range_text = _flag_result(float(numeric_value), res["analyte_id"], cur)
            else:
                flag = res["flag"]
                range_text = res["reference_range_text"]

            cur.execute("""
                UPDATE his_lab_results SET
                    value = COALESCE(%s, value),
                    numeric_value = COALESCE(%s, numeric_value),
                    flag = %s, reference_range_text = %s,
                    status = 'corrected', notes = %s, updated_at = NOW()
                WHERE id = %s RETURNING *
            """, (
                data.get("value"), numeric_value,
                flag, range_text, data.get("notes"),
                result_id,
            ))
            result = _dec(cur.fetchone())
        conn.commit()
        return jsonify(result)
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        put_db(conn)


@erp_lis_bp.route("/api/erp/lis/results/from-hl7", methods=["POST"])
@require_auth(allowed_roles=LAB_ROLES + ["super_admin"])
def receive_hl7_results():
    """Receive parsed HL7 ORU^R01 results from Mirth Connect."""
    data = request.get_json(silent=True) or {}
    sender_id = data.get("sender_id")
    results = data.get("results", [])
    if not results:
        return jsonify({"error": "results[] requerido"}), 400
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Resolve instrument
            instrument_id = None
            if sender_id:
                cur.execute(
                    "SELECT id FROM his_lab_instruments WHERE hl7_sender_id = %s AND is_active = TRUE",
                    (sender_id,)
                )
                inst = cur.fetchone()
                if inst:
                    instrument_id = inst["id"]

            processed = 0
            critical_alerts = []
            for r in results:
                sample_code = r.get("sample_code")
                analyte_code = r.get("analyte_code")
                if not sample_code or not analyte_code:
                    continue

                cur.execute("SELECT id FROM his_lab_samples WHERE sample_code = %s", (sample_code,))
                sample = cur.fetchone()
                if not sample:
                    continue

                cur.execute("SELECT id FROM his_lab_analytes WHERE code = %s", (analyte_code,))
                analyte = cur.fetchone()
                if not analyte:
                    continue

                numeric_value = r.get("numeric_value")
                if numeric_value is not None:
                    numeric_value = float(numeric_value)
                flag, range_text = _flag_result(numeric_value, analyte["id"], cur)

                cur.execute("""
                    INSERT INTO his_lab_results
                    (sample_id, analyte_id, value, numeric_value, unit,
                     flag, reference_range_text, instrument_id, raw_value)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING
                """, (
                    sample["id"], analyte["id"],
                    r.get("value", ""), numeric_value, r.get("unit"),
                    flag, range_text, instrument_id, r.get("raw_value"),
                ))
                processed += 1

                if flag in ("critical_low", "critical_high"):
                    critical_alerts.append({
                        "sample_code": sample_code,
                        "analyte_code": analyte_code,
                        "value": r.get("value"),
                        "flag": flag,
                    })

        conn.commit()
        log.info("HL7 resultados recibidos: %d de sender %s, %d criticos",
                 processed, sender_id, len(critical_alerts))
        return jsonify({
            "processed": processed,
            "critical_alerts": critical_alerts,
            "instrument_id": instrument_id,
        })
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        put_db(conn)


# ===================================================================
# VALIDATION
# ===================================================================

@erp_lis_bp.route("/api/erp/lis/results/<int:result_id>/validate-tech", methods=["POST"])
@require_auth(allowed_roles=LAB_ROLES)
def validate_tech(result_id):
    cu = request.current_user
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                UPDATE his_lab_results SET
                    status = 'preliminary',
                    tech_validated_by = %s, tech_validated_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s AND status IN ('pending', 'corrected') RETURNING *
            """, (cu.get("user_id"), result_id))
            result = cur.fetchone()
            if not result:
                return jsonify({"error": "Resultado no encontrado o ya validado"}), 404
        conn.commit()
        return jsonify(_dec(result))
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        put_db(conn)


def _notify_patient_lab_ready(sample, cur):
    """Notify patient that lab results are ready (all results final)."""
    try:
        patient_doc = sample.get("patient_document")
        if not patient_doc:
            return
        order_desc = sample.get("sample_type", "laboratorio")
        _req.post(f"{NOTIFY_URL}/api/notify/lab", json={
            "patient_document": patient_doc,
            "order_description": order_desc,
        }, timeout=5)
        log.info("Lab notification sent for patient %s, sample %s", patient_doc, sample.get("id"))
    except Exception:
        log.warning("Failed to send lab notification for sample %s", sample.get("id"))


@erp_lis_bp.route("/api/erp/lis/results/<int:result_id>/validate-med", methods=["POST"])
@require_auth(allowed_roles=LAB_MED_ROLES)
def validate_med(result_id):
    cu = request.current_user
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                UPDATE his_lab_results SET
                    status = 'final',
                    med_validated_by = %s, med_validated_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s AND status = 'preliminary' RETURNING *
            """, (cu.get("user_id"), result_id))
            result = cur.fetchone()
            if not result:
                return jsonify({"error": "Resultado no encontrado o no tiene validacion tecnica"}), 404
            result = _dec(result)

            # Check if all results for sample are final → mark sample completed
            cur.execute("""
                SELECT COUNT(*) AS total, COUNT(CASE WHEN status = 'final' THEN 1 END) AS finals
                FROM his_lab_results WHERE sample_id = %s
            """, (result["sample_id"],))
            counts = cur.fetchone()
            if counts["total"] == counts["finals"]:
                cur.execute(
                    "UPDATE his_lab_samples SET status = 'completed', updated_at = NOW() WHERE id = %s RETURNING *",
                    (result["sample_id"],)
                )
                sample = cur.fetchone()
                if sample:
                    conn.commit()
                    _notify_patient_lab_ready(_dec(sample), cur)
                    return jsonify(result)

        conn.commit()
        return jsonify(result)
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        put_db(conn)


@erp_lis_bp.route("/api/erp/lis/samples/<int:sample_id>/validate-all", methods=["POST"])
@require_auth(allowed_roles=LAB_MED_ROLES)
def validate_all_sample(sample_id):
    cu = request.current_user
    data = request.get_json(silent=True) or {}
    validation_type = data.get("type", "tech")  # "tech" or "med"
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if validation_type == "tech":
                cur.execute("""
                    UPDATE his_lab_results SET
                        status = 'preliminary',
                        tech_validated_by = %s, tech_validated_at = NOW(),
                        updated_at = NOW()
                    WHERE sample_id = %s AND status IN ('pending', 'corrected')
                """, (cu.get("user_id"), sample_id))
            else:
                cur.execute("""
                    UPDATE his_lab_results SET
                        status = 'final',
                        med_validated_by = %s, med_validated_at = NOW(),
                        updated_at = NOW()
                    WHERE sample_id = %s AND status = 'preliminary'
                """, (cu.get("user_id"), sample_id))
                # Mark sample completed
                cur.execute("""
                    UPDATE his_lab_samples SET status = 'completed', updated_at = NOW()
                    WHERE id = %s RETURNING *
                """, (sample_id,))
                sample = cur.fetchone()
            updated = cur.rowcount
        conn.commit()
        if validation_type == "med" and sample:
            _notify_patient_lab_ready(_dec(sample), None)
        return jsonify({"sample_id": sample_id, "validated": updated, "type": validation_type})
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        put_db(conn)


# ===================================================================
# PDF
# ===================================================================

@erp_lis_bp.route("/api/erp/lis/samples/<int:sample_id>/pdf", methods=["GET"])
@require_auth(allowed_roles=READ_ROLES)
def sample_pdf(sample_id):
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM his_lab_samples WHERE id = %s", (sample_id,))
            sample = cur.fetchone()
            if not sample:
                return jsonify({"error": "Muestra no encontrada"}), 404

            cur.execute("""
                SELECT r.*, a.code AS analyte_code, a.name AS analyte_name, a.category
                FROM his_lab_results r
                JOIN his_lab_analytes a ON a.id = r.analyte_id
                WHERE r.sample_id = %s AND r.status IN ('preliminary', 'final', 'corrected')
                ORDER BY a.category, a.name
            """, (sample_id,))
            results = [_dec(r) for r in cur.fetchall()]

        pdf_bytes = _generate_lab_report_pdf(sample, results)
        resp = make_response(pdf_bytes)
        resp.headers["Content-Type"] = "application/pdf"
        resp.headers["Content-Disposition"] = (
            f"inline; filename=lab_{sample['sample_code']}.pdf"
        )
        return resp
    finally:
        put_db(conn)


def _generate_lab_report_pdf(sample, results):
    """Generate a professional lab results PDF with logo, colors, and flags."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image,
    )

    PRIMARY = colors.HexColor("#0f172a")
    ACCENT = colors.HexColor("#1e40af")
    LIGHT_BG = colors.HexColor("#f8fafc")
    BORDER = colors.HexColor("#cbd5e1")
    DARK_TEXT = colors.HexColor("#1e293b")
    MUTED = colors.HexColor("#64748b")
    FLAG_HIGH = colors.HexColor("#dc2626")
    FLAG_LOW = colors.HexColor("#2563eb")
    FLAG_CRIT = colors.HexColor("#991b1b")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        topMargin=0.4 * inch, bottomMargin=0.5 * inch,
    )
    styles = getSampleStyleSheet()

    s_cell = ParagraphStyle("cell", parent=styles["Normal"], fontSize=8)
    s_cell_bold = ParagraphStyle("cell_bold", parent=styles["Normal"],
        fontSize=8, fontName="Helvetica-Bold")
    s_right = ParagraphStyle("right", parent=styles["Normal"],
        fontSize=8, alignment=TA_RIGHT)
    s_footer = ParagraphStyle("footer", parent=styles["Normal"],
        fontSize=7, textColor=MUTED, alignment=TA_CENTER)

    elements = []
    inst_name = os.environ.get("INSTITUTION_NAME", "Laboratorio")
    inst_ruc = os.environ.get("INSTITUTION_RUC", "")
    inst_addr = os.environ.get("INSTITUTION_ADDRESS", "")
    inst_phone = os.environ.get("INSTITUTION_PHONE", "")
    logo_path = os.environ.get("LOGO_PATH", "/certs/logo.png")
    has_logo = os.path.isfile(logo_path)

    # ===== HEADER =====
    info_parts = [f"<b>{inst_name}</b>"]
    if inst_ruc:
        info_parts.append(f"<b>RUC:</b> {inst_ruc}")
    if inst_addr:
        info_parts.append(inst_addr)
    if inst_phone:
        info_parts.append(f"Tel: {inst_phone}")
    info_html = "<br/>".join(info_parts)

    title_html = (
        "<b><font size='12' color='#1e40af'>INFORME DE RESULTADOS</font></b>"
        "<br/><font size='8' color='#64748b'>Laboratorio Clinico</font>"
    )

    if has_logo:
        logo_img = Image(logo_path, width=1.1 * inch, height=0.7 * inch)
        logo_img.hAlign = "LEFT"
        left_cell = Table(
            [[logo_img, Paragraph(info_html, ParagraphStyle("lh", parent=s_cell,
                fontSize=8, leading=12))]],
            colWidths=[1.2 * inch, 2.4 * inch],
        )
        left_cell.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]))
    else:
        left_cell = Paragraph(info_html, ParagraphStyle("lh", parent=s_cell,
            fontSize=8, leading=12))

    header_data = [[
        left_cell,
        Paragraph(title_html, ParagraphStyle("rh", parent=s_cell,
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

    # ===== PATIENT / SAMPLE INFO =====
    collection_date = str(sample.get("collection_date") or "")[:10]
    status_labels = {
        "registered": "Registrada", "received": "Recibida",
        "in_progress": "En Proceso", "completed": "Completada",
        "validated": "Validada",
    }
    status_display = status_labels.get(sample.get("status", ""), sample.get("status", ""))

    patient_rows = [
        [Paragraph("<b>Paciente:</b>", s_cell),
         Paragraph(sample.get("patient_name", ""), s_cell),
         Paragraph("<b>Identificacion:</b>", s_cell),
         Paragraph(sample.get("patient_document", ""), s_cell)],
        [Paragraph("<b>Muestra:</b>", s_cell),
         Paragraph(sample.get("sample_code", ""), s_cell),
         Paragraph("<b>Tipo:</b>", s_cell),
         Paragraph(sample.get("sample_type", ""), s_cell)],
        [Paragraph("<b>Fecha Recoleccion:</b>", s_cell),
         Paragraph(collection_date, s_cell),
         Paragraph("<b>Estado:</b>", s_cell),
         Paragraph(status_display, s_cell)],
    ]
    patient_table = Table(patient_rows,
        colWidths=[1.2 * inch, 2.5 * inch, 1.0 * inch, 2.3 * inch])
    patient_table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e2e8f0")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BG),
    ]))
    elements.append(patient_table)
    elements.append(Spacer(1, 12))

    # ===== RESULTS TABLE (grouped by category) =====
    current_cat = None
    detail_header = [
        Paragraph("<b>Analito</b>", s_cell_bold),
        Paragraph("<b>Resultado</b>", s_cell_bold),
        Paragraph("<b>Unidad</b>", s_cell_bold),
        Paragraph("<b>Rango Referencia</b>", s_cell_bold),
        Paragraph("<b>Flag</b>", s_cell_bold),
    ]
    detail_data = [detail_header]

    for r in results:
        cat = r.get("category", "General")
        if cat != current_cat:
            current_cat = cat
            detail_data.append([
                Paragraph(f"<b>{cat.upper()}</b>", ParagraphStyle("cat", parent=s_cell,
                    fontSize=8, fontName="Helvetica-Bold", textColor=ACCENT)),
                "", "", "", "",
            ])

        flag = r.get("flag", "normal")
        val_str = str(r.get("value") or "")
        if flag == "high":
            val_para = Paragraph(f"<b><font color='#dc2626'>{val_str}</font></b>", s_cell)
            flag_para = Paragraph("<font color='#dc2626'><b>ALTO</b></font>", s_cell)
        elif flag == "low":
            val_para = Paragraph(f"<b><font color='#2563eb'>{val_str}</font></b>", s_cell)
            flag_para = Paragraph("<font color='#2563eb'><b>BAJO</b></font>", s_cell)
        elif flag in ("critical_high", "critical_low", "critical"):
            label = "CRITICO ALTO" if flag == "critical_high" else (
                "CRITICO BAJO" if flag == "critical_low" else "CRITICO")
            val_para = Paragraph(f"<b><font color='#991b1b'>{val_str}</font></b>", s_cell)
            flag_para = Paragraph(f"<font color='#991b1b'><b>{label}</b></font>", s_cell)
        elif flag == "abnormal":
            val_para = Paragraph(f"<b><font color='#d97706'>{val_str}</font></b>", s_cell)
            flag_para = Paragraph("<font color='#d97706'><b>ANORMAL</b></font>", s_cell)
        else:
            val_para = Paragraph(val_str, s_cell)
            flag_para = Paragraph("", s_cell)

        detail_data.append([
            Paragraph(r.get("analyte_name", ""), s_cell),
            val_para,
            Paragraph(str(r.get("unit") or ""), s_cell),
            Paragraph(str(r.get("reference_range_text") or ""), s_cell),
            flag_para,
        ])

    detail_table = Table(detail_data,
        colWidths=[2.2 * inch, 1.2 * inch, 0.8 * inch, 1.8 * inch, 1.0 * inch])
    detail_style = [
        ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    for i in range(1, len(detail_data)):
        row = detail_data[i]
        if isinstance(row[1], str) and row[1] == "":
            detail_style.append(("SPAN", (0, i), (-1, i)))
            detail_style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#eff6ff")))
        elif i % 2 == 0:
            detail_style.append(("BACKGROUND", (0, i), (-1, i), LIGHT_BG))

    detail_table.setStyle(TableStyle(detail_style))
    elements.append(detail_table)
    elements.append(Spacer(1, 15))

    # ===== SIGNATURES =====
    sig_data = [[
        Paragraph("____________________________<br/><b>Tecnologo Medico</b>",
            ParagraphStyle("sig", parent=s_cell, fontSize=8, alignment=TA_CENTER)),
        Paragraph("____________________________<br/><b>Bioquimico Responsable</b>",
            ParagraphStyle("sig2", parent=s_cell, fontSize=8, alignment=TA_CENTER)),
    ]]
    sig_table = Table(sig_data, colWidths=[3.5 * inch, 3.5 * inch])
    sig_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
        ("TOPPADDING", (0, 0), (-1, -1), 20),
    ]))
    elements.append(sig_table)

    # ===== FOOTER =====
    elements.append(Spacer(1, 15))
    elements.append(Paragraph(
        "Documento generado electronicamente — Dimed-LIS | "
        "Los valores de referencia pueden variar segun edad, sexo y condicion del paciente.",
        s_footer))

    doc.build(elements)
    buf.seek(0)
    return buf.read()


# ===================================================================
# WORKLIST & INSTRUMENTS
# ===================================================================

@erp_lis_bp.route("/api/erp/lis/worklist", methods=["GET"])
@require_auth(allowed_roles=LAB_ROLES)
def worklist():
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            branch = request.args.get("branch_code")
            instrument_id = request.args.get("instrument_id")
            sql = """
                SELECT s.id, s.sample_code, s.patient_document, s.patient_name,
                       s.sample_type, s.status, s.collection_date, s.branch_code,
                       COUNT(r.id) AS pending_results
                FROM his_lab_samples s
                LEFT JOIN his_lab_results r ON r.sample_id = s.id AND r.status = 'pending'
                WHERE s.status IN ('received', 'in_process')
            """
            params = []
            if branch:
                sql += " AND s.branch_code = %s"
                params.append(branch)
            sql += " GROUP BY s.id HAVING COUNT(r.id) > 0 ORDER BY s.collection_date ASC LIMIT 200"
            cur.execute(sql, params)
            return jsonify([_dec(r) for r in cur.fetchall()])
    finally:
        put_db(conn)


@erp_lis_bp.route("/api/erp/lis/instruments", methods=["GET"])
@require_auth(allowed_roles=LAB_ROLES)
def list_instruments():
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM his_lab_instruments WHERE is_active = TRUE ORDER BY name")
            return jsonify([_dec(r) for r in cur.fetchall()])
    finally:
        put_db(conn)


@erp_lis_bp.route("/api/erp/lis/instruments", methods=["POST"])
@require_auth(allowed_roles=ADMIN_ROLES + ["laboratorista"])
def create_instrument():
    data = request.get_json(silent=True) or {}
    required = ["code", "name"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Campos requeridos: {', '.join(missing)}"}), 400
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO his_lab_instruments
                (code, name, manufacturer, model, serial_number,
                 hl7_sender_id, hl7_sender_facility, connection_type,
                 host, port, branch_code)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *
            """, (
                data["code"], data["name"], data.get("manufacturer"),
                data.get("model"), data.get("serial_number"),
                data.get("hl7_sender_id"), data.get("hl7_sender_facility"),
                data.get("connection_type", "manual"),
                data.get("host"), data.get("port"), data.get("branch_code"),
            ))
            inst = _dec(cur.fetchone())
        conn.commit()
        return jsonify(inst), 201
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        return jsonify({"error": f"Codigo {data['code']} ya existe"}), 409
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        put_db(conn)


# ===================================================================
# QC
# ===================================================================

@erp_lis_bp.route("/api/erp/lis/qc", methods=["POST"])
@require_auth(allowed_roles=LAB_ROLES)
def create_qc():
    cu = request.current_user
    data = request.get_json(silent=True) or {}
    required = ["instrument_id", "analyte_id", "level", "expected_value", "actual_value"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Campos requeridos: {', '.join(missing)}"}), 400

    expected = float(data["expected_value"])
    actual = float(data["actual_value"])
    sd = float(data.get("sd", 0))

    # Auto-calculate CV and status
    cv = round(abs(actual - expected) / expected * 100, 4) if expected != 0 else 0
    status = "accepted"
    if sd > 0:
        deviation = abs(actual - expected) / sd
        if deviation > 3:
            status = "rejected"
        elif deviation > 2:
            status = "warning"

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO his_lab_qc
                (instrument_id, analyte_id, qc_date, level,
                 expected_value, actual_value, sd, cv_percent,
                 status, lot_number, notes, created_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *
            """, (
                data["instrument_id"], data["analyte_id"],
                data.get("qc_date", date.today().isoformat()),
                data["level"], expected, actual, sd, cv,
                data.get("status", status),
                data.get("lot_number"), data.get("notes"),
                cu.get("user_id"),
            ))
            qc = _dec(cur.fetchone())
        conn.commit()
        return jsonify(qc), 201
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        put_db(conn)


@erp_lis_bp.route("/api/erp/lis/qc", methods=["GET"])
@require_auth(allowed_roles=LAB_ROLES)
def list_qc():
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            inst_id = request.args.get("instrument_id")
            analyte_id = request.args.get("analyte_id")
            sql = """
                SELECT qc.*, i.name AS instrument_name, a.name AS analyte_name
                FROM his_lab_qc qc
                JOIN his_lab_instruments i ON i.id = qc.instrument_id
                JOIN his_lab_analytes a ON a.id = qc.analyte_id
                WHERE 1=1
            """
            params = []
            if inst_id:
                sql += " AND qc.instrument_id = %s"
                params.append(int(inst_id))
            if analyte_id:
                sql += " AND qc.analyte_id = %s"
                params.append(int(analyte_id))
            sql += " ORDER BY qc.qc_date DESC, qc.created_at DESC LIMIT 200"
            cur.execute(sql, params)
            return jsonify([_dec(r) for r in cur.fetchall()])
    finally:
        put_db(conn)


@erp_lis_bp.route("/api/erp/lis/qc/levey-jennings/<int:instrument_id>/<int:analyte_id>", methods=["GET"])
@require_auth(allowed_roles=LAB_ROLES)
def levey_jennings(instrument_id, analyte_id):
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            days = int(request.args.get("days", 30))
            level = request.args.get("level", "normal")
            cur.execute("""
                SELECT qc_date, actual_value, expected_value, sd, cv_percent, status
                FROM his_lab_qc
                WHERE instrument_id = %s AND analyte_id = %s AND level = %s
                  AND qc_date >= CURRENT_DATE - %s
                ORDER BY qc_date ASC
            """, (instrument_id, analyte_id, level, days))
            points = [_dec(r) for r in cur.fetchall()]

            # Calculate mean and SD for Levey-Jennings
            if points:
                values = [p["actual_value"] for p in points]
                mean = sum(values) / len(values)
                variance = sum((v - mean) ** 2 for v in values) / len(values) if len(values) > 1 else 0
                sd_calc = variance ** 0.5
            else:
                mean = sd_calc = 0

            return jsonify({
                "instrument_id": instrument_id,
                "analyte_id": analyte_id,
                "level": level,
                "days": days,
                "points": points,
                "statistics": {
                    "mean": round(mean, 4),
                    "sd": round(sd_calc, 4),
                    "n": len(points),
                    "plus_1sd": round(mean + sd_calc, 4),
                    "minus_1sd": round(mean - sd_calc, 4),
                    "plus_2sd": round(mean + 2 * sd_calc, 4),
                    "minus_2sd": round(mean - 2 * sd_calc, 4),
                    "plus_3sd": round(mean + 3 * sd_calc, 4),
                    "minus_3sd": round(mean - 3 * sd_calc, 4),
                },
            })
    finally:
        put_db(conn)


# ===================================================================
# STATS & PENDING
# ===================================================================

@erp_lis_bp.route("/api/erp/lis/stats", methods=["GET"])
@require_auth(allowed_roles=LAB_ROLES + ["director_medico"])
def lis_stats():
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            days = int(request.args.get("days", 30))
            branch = request.args.get("branch_code")

            base_where = "WHERE s.collection_date >= CURRENT_DATE - %s"
            params = [days]
            if branch:
                base_where += " AND s.branch_code = %s"
                params.append(branch)

            # Samples per day
            cur.execute(f"""
                SELECT DATE(s.collection_date) AS day, COUNT(*) AS count
                FROM his_lab_samples s {base_where}
                GROUP BY DATE(s.collection_date) ORDER BY day
            """, params)
            samples_per_day = [_dec(r) for r in cur.fetchall()]

            # Status breakdown
            cur.execute(f"""
                SELECT s.status, COUNT(*) AS count
                FROM his_lab_samples s {base_where}
                GROUP BY s.status
            """, params)
            by_status = {r["status"]: r["count"] for r in cur.fetchall()}

            # Rejection rate
            total = sum(by_status.values())
            rejected = by_status.get("rejected", 0)
            rejection_rate = round(rejected / total * 100, 2) if total > 0 else 0

            # Critical results
            cur.execute(f"""
                SELECT COUNT(*) AS count FROM his_lab_results r
                JOIN his_lab_samples s ON s.id = r.sample_id
                {base_where} AND r.flag IN ('critical_low', 'critical_high')
            """, params)
            critical_count = cur.fetchone()["count"]

            # Turnaround time (collection to completed)
            cur.execute(f"""
                SELECT AVG(EXTRACT(EPOCH FROM (s.updated_at - s.collection_date)) / 3600) AS avg_tat_hours
                FROM his_lab_samples s {base_where} AND s.status = 'completed'
            """, params)
            tat = cur.fetchone()
            avg_tat = round(float(tat["avg_tat_hours"]), 2) if tat and tat["avg_tat_hours"] else 0

            return jsonify({
                "period_days": days,
                "total_samples": total,
                "by_status": by_status,
                "rejection_rate": rejection_rate,
                "critical_results": critical_count,
                "avg_turnaround_hours": avg_tat,
                "samples_per_day": samples_per_day,
            })
    finally:
        put_db(conn)


@erp_lis_bp.route("/api/erp/lis/pending-validation", methods=["GET"])
@require_auth(allowed_roles=LAB_MED_ROLES)
def pending_validation():
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            val_type = request.args.get("type", "tech")  # tech or med
            status_filter = "pending" if val_type == "tech" else "preliminary"
            cur.execute("""
                SELECT r.id, r.value, r.numeric_value, r.flag, r.status,
                       r.created_at, a.code AS analyte_code, a.name AS analyte_name,
                       s.sample_code, s.patient_document, s.patient_name, s.branch_code
                FROM his_lab_results r
                JOIN his_lab_analytes a ON a.id = r.analyte_id
                JOIN his_lab_samples s ON s.id = r.sample_id
                WHERE r.status = %s
                ORDER BY
                    CASE WHEN r.flag IN ('critical_low','critical_high') THEN 0 ELSE 1 END,
                    r.created_at ASC
                LIMIT 200
            """, (status_filter,))
            results = [_dec(r) for r in cur.fetchall()]
            return jsonify({
                "validation_type": val_type,
                "pending_count": len(results),
                "results": results,
            })
    finally:
        put_db(conn)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.route("/health", methods=["GET"])
def health():
    db_ok = False
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        put_db(conn)
        db_ok = True
    except Exception:
        pass
    return jsonify({
        "status": "ok" if db_ok else "degraded",
        "service": "lis",
        "database": "connected" if db_ok else "error",
    }), 200 if db_ok else 503


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    log.info("Dimed LIS starting on port %d", LIS_PORT)
    app.run(host="0.0.0.0", port=LIS_PORT, debug=False)
