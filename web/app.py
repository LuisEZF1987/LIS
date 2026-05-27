#!/usr/bin/env python3
"""
Dimed-LIS Web Dashboard.
Flask application serving the laboratory system UI, authentication,
patient API, and reverse proxy to LIS/Billing microservices.
"""

import functools
import json as _json
import logging
import os
import time
from datetime import datetime, timedelta, timezone, date
from decimal import Decimal
from pathlib import Path

import bcrypt
import jwt
import psycopg2
import psycopg2.extras
import psycopg2.pool
import requests as _req
from flask import (
    Flask, g, jsonify, redirect, render_template, request, url_for,
)
from flask.json.provider import DefaultJSONProvider

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = Flask(__name__, static_folder="static", template_folder="templates")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [WEB] %(levelname)s %(message)s",
)
log = logging.getLogger("lis-web")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

JWT_SECRET = os.environ["JWT_SECRET"]
JWT_EXPIRY_HOURS = int(os.environ.get("JWT_EXPIRY_HOURS", "8"))
COOKIE_NAME = "lis_token"

DB_HOST = os.environ.get("DB_HOST", "postgres")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "dimed_lis")
DB_USER = os.environ.get("PG_USER", "dimed")
DB_PASS = os.environ.get("PG_PASSWORD", "")

LIS_URL = os.environ.get("LIS_URL", "http://lis:9008")
BILLING_URL = os.environ.get("BILLING_URL", "http://billing:9009")
WEB_PORT = int(os.environ.get("WEB_PORT", "9000"))
IS_DEBUG = os.environ.get("FLASK_DEBUG", "false").lower() in ("true", "1")

LICENSE_KEY = os.environ.get("LICENSE_KEY", "")
LICENSE_SERVER = os.environ.get("LICENSE_SERVER", "")
_LICENSE_CACHE_FILE = "/tmp/lis_license.json"
_LICENSE_CACHE_TTL = 86400  # 24h

# ---------------------------------------------------------------------------
# Custom JSON provider (handle dates, Decimal from PostgreSQL)
# ---------------------------------------------------------------------------


class _JSONProvider(DefaultJSONProvider):
    def default(self, o):
        if isinstance(o, (date, datetime)):
            return o.isoformat()
        if isinstance(o, timedelta):
            return str(o)
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)


app.json_provider_class = _JSONProvider
app.json = _JSONProvider(app)

# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# License enforcement
# ---------------------------------------------------------------------------


def _check_license() -> str:
    """Returns 'full', 'readonly', or 'blocked'. Defaults to 'full' if unconfigured."""
    if not LICENSE_KEY or not LICENSE_SERVER:
        return "full"
    # Check cache
    try:
        cached = _json.loads(Path(_LICENSE_CACHE_FILE).read_text())
        if time.time() - cached.get("ts", 0) < _LICENSE_CACHE_TTL:
            return cached.get("mode", "full")
    except Exception:
        pass
    # Fetch from server
    try:
        r = _req.get(
            f"{LICENSE_SERVER}/check",
            params={"key": LICENSE_KEY},
            timeout=5,
        )
        mode = r.json().get("mode", "full") if r.status_code == 200 else "full"
        Path(_LICENSE_CACHE_FILE).write_text(
            _json.dumps({"mode": mode, "ts": time.time()})
        )
        return mode
    except Exception:
        # Can't reach server — use stale cache, else default to full (grace)
        try:
            return _json.loads(Path(_LICENSE_CACHE_FILE).read_text()).get("mode", "full")
        except Exception:
            return "full"


@app.before_request
def _enforce_license():
    mode = _check_license()
    g.license_mode = mode
    if mode == "blocked" and request.path.startswith("/app/"):
        return redirect("/license-expired")


@app.context_processor
def _inject_license():
    return {"license_mode": g.get("license_mode", "full")}


@app.route("/license-expired")
def license_expired():
    return """<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
    <title>Licencia Vencida</title>
    <style>body{background:#020617;color:#e2e8f0;font-family:system-ui;
    display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
    .box{background:rgba(15,23,42,0.8);border:1px solid rgba(239,68,68,0.3);
    border-radius:1rem;padding:2.5rem;max-width:480px;text-align:center}
    h1{color:#f87171;margin-top:0}p{color:#94a3b8}
    a{color:#60a5fa;text-decoration:none}</style></head>
    <body><div class="box">
    <h1>&#128274; Licencia Vencida</h1>
    <p>Su licencia de <strong>Dimed-LIS</strong> ha vencido.</p>
    <p>Contacte a su proveedor para renovar y continuar usando el sistema.</p>
    <p style="margin-top:2rem;font-size:0.85rem">
      <a href="mailto:soporte@dimed.com.ec">soporte@dimed.com.ec</a>
    </p>
    </div></body></html>""", 403


@app.after_request
def _security_headers(response):
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if "text/html" in response.content_type:
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

# ---------------------------------------------------------------------------
# Database pool
# ---------------------------------------------------------------------------

_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2, maxconn=10,
            host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
            user=DB_USER, password=DB_PASS,
        )
    return _pool


def get_db():
    return _get_pool().getconn()


def put_db(conn):
    try:
        _get_pool().putconn(conn)
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------


def log_audit(user_id, action, entity=None, entity_id=None,
              details=None, ip=None):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO lis_audit_log (user_id, action, entity, entity_id, "
                "details, ip_address) VALUES (%s,%s,%s,%s,%s,%s)",
                (user_id, action, entity, str(entity_id) if entity_id else None,
                 psycopg2.extras.Json(details) if details else None, ip),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        log.warning("Failed to write audit log: %s / %s", action, entity)
    finally:
        put_db(conn)

# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------


def _create_token(user_row):
    now = datetime.now(timezone.utc)
    payload = {
        "user_id": user_row["id"],
        "username": user_row["username"],
        "email": user_row["email"],
        "full_name": user_row["full_name"],
        "role": user_row["role"],
        "branch_id": user_row["branch_id"],
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=JWT_EXPIRY_HOURS)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def _decode_token(token):
    return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])


def _get_current_user():
    """Extract user from cookie or Authorization header. Returns dict or None."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    if not token:
        return None
    try:
        return _decode_token(token)
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None

# ---------------------------------------------------------------------------
# Role-based access control
# ---------------------------------------------------------------------------

_SA = "super_admin"
_ROLE_ACCESS = {
    "dashboard":      [_SA, "admin", "recepcion", "laboratorista", "bioquimico", "contador"],
    "laboratorio":    [_SA, "admin", "laboratorista", "bioquimico"],
    "facturacion":    [_SA, "admin", "contador"],
    "caja":           [_SA, "admin", "recepcion", "contador"],
    "pacientes":      [_SA, "admin", "recepcion", "laboratorista"],
    "reportes":       [_SA, "admin", "contador"],
    "configuracion":  [_SA],
}

_ROLE_DEFAULT_PAGE = {
    "super_admin":   "/app/configuracion",
    "admin":         "/app/dashboard",
    "recepcion":     "/app/caja",
    "laboratorista": "/app/laboratorio",
    "bioquimico":    "/app/laboratorio",
    "contador":      "/app/facturacion",
}


def require_auth(page_name=None):
    """Decorator for routes requiring authentication and optional role check."""
    def decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            user = _get_current_user()
            if not user:
                if request.path.startswith("/api/"):
                    return jsonify({"error": "Token no proporcionado"}), 401
                return redirect("/login")
            if page_name and page_name in _ROLE_ACCESS:
                if user["role"] not in _ROLE_ACCESS[page_name]:
                    target = _ROLE_DEFAULT_PAGE.get(user["role"], "/app/dashboard")
                    return redirect(target)
            request.current_user = user
            return f(*args, **kwargs)
        return wrapper
    return decorator

# ═══════════════════════════════════════════════════════════════════════════
# AUTH ROUTES
# ═══════════════════════════════════════════════════════════════════════════


@app.route("/")
def index():
    user = _get_current_user()
    if user:
        return redirect(_ROLE_DEFAULT_PAGE.get(user["role"], "/app/dashboard"))
    return redirect("/login")


@app.route("/login")
def login_page():
    if _get_current_user():
        return redirect("/app/dashboard")
    return render_template("login.html")


@app.route("/api/auth/login", methods=["POST"])
def api_login():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")

    if not username or not password:
        return jsonify({"error": "Usuario y contrasena son requeridos"}), 400

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, username, email, password_hash, full_name, role, "
                "branch_id, is_active FROM lis_users "
                "WHERE (username = %s OR email = %s) AND is_active = TRUE",
                (username, username),
            )
            user = cur.fetchone()

        if not user:
            return jsonify({"error": "Credenciales invalidas"}), 401

        if not bcrypt.checkpw(password.encode("utf-8"),
                              user["password_hash"].encode("utf-8")):
            client_ip = request.headers.get(
                "X-Forwarded-For", request.remote_addr or "unknown"
            ).split(",")[0].strip()
            log_audit(None, "LOGIN_FAILED", "Auth", None,
                      {"username": username}, client_ip)
            return jsonify({"error": "Credenciales invalidas"}), 401

        # Update last_login
        with conn.cursor() as cur:
            cur.execute("UPDATE lis_users SET last_login = NOW() WHERE id = %s",
                        (user["id"],))
        conn.commit()

        token = _create_token(user)
        client_ip = request.headers.get(
            "X-Forwarded-For", request.remote_addr or "unknown"
        ).split(",")[0].strip()
        log_audit(user["id"], "LOGIN", "Auth", None, None, client_ip)

        resp = jsonify({
            "user": {
                "id": user["id"],
                "username": user["username"],
                "email": user["email"],
                "full_name": user["full_name"],
                "role": user["role"],
                "branch_id": user["branch_id"],
            },
            "redirect": _ROLE_DEFAULT_PAGE.get(user["role"], "/app/dashboard"),
        })
        resp.set_cookie(
            COOKIE_NAME, token,
            httponly=True, samesite="Strict", path="/",
            max_age=JWT_EXPIRY_HOURS * 3600,
            secure=os.getenv("FLASK_ENV") == "production",
        )
        return resp, 200

    except Exception:
        conn.rollback()
        log.exception("Error en login")
        return jsonify({"error": "Error interno del servidor"}), 500
    finally:
        put_db(conn)


@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    resp = jsonify({"message": "Sesion cerrada"})
    resp.set_cookie(COOKIE_NAME, "", expires=0, httponly=True,
                    samesite="Strict", path="/")
    return resp, 200


@app.route("/api/auth/me")
def api_me():
    user = _get_current_user()
    if not user:
        return jsonify({"error": "No autenticado"}), 401
    return jsonify({"user": user}), 200

# ═══════════════════════════════════════════════════════════════════════════
# APP PAGE ROUTES
# ═══════════════════════════════════════════════════════════════════════════


@app.route("/app/dashboard")
@require_auth("dashboard")
def page_dashboard():
    return render_template("dashboard.html", user=request.current_user, page="dashboard")


@app.route("/app/laboratorio")
@require_auth("laboratorio")
def page_laboratorio():
    return render_template("laboratorio.html", user=request.current_user, page="laboratorio")


@app.route("/app/facturacion")
@require_auth("facturacion")
def page_facturacion():
    return render_template("facturacion.html", user=request.current_user, page="facturacion")


@app.route("/app/caja")
@require_auth("caja")
def page_caja():
    return render_template("caja.html", user=request.current_user, page="caja")


@app.route("/app/pacientes")
@require_auth("pacientes")
def page_pacientes():
    return render_template("pacientes.html", user=request.current_user, page="pacientes")


@app.route("/app/reportes")
@require_auth("reportes")
def page_reportes():
    return render_template("reportes.html", user=request.current_user, page="reportes")


@app.route("/app/configuracion")
@require_auth("configuracion")
def page_configuracion():
    mode = _check_license()
    return render_template(
        "configuracion.html",
        user=request.current_user,
        page="configuracion",
        lic_mode=mode,
        lic_key=LICENSE_KEY[-8:] if LICENSE_KEY else "",
        lic_server=LICENSE_SERVER,
    )

# ═══════════════════════════════════════════════════════════════════════════
# PROXY TO MICROSERVICES
# ═══════════════════════════════════════════════════════════════════════════


def _proxy(target_base, path):
    """Forward the current Flask request to a backend microservice."""
    url = f"{target_base}/{path}"
    fwd_headers = {
        k: v for k, v in request.headers
        if k.lower() in ("content-type", "accept")
    }
    # Inject JWT from cookie as Authorization header
    token = request.cookies.get(COOKIE_NAME)
    auth_header = request.headers.get("Authorization")
    if auth_header:
        fwd_headers["Authorization"] = auth_header
    elif token:
        fwd_headers["Authorization"] = f"Bearer {token}"

    try:
        resp = _req.request(
            method=request.method,
            url=url,
            headers=fwd_headers,
            params=request.args,
            data=request.get_data(),
            timeout=30,
        )
        excluded = {"transfer-encoding", "content-encoding", "content-length"}
        headers = {k: v for k, v in resp.headers.items()
                   if k.lower() not in excluded}
        return (resp.content, resp.status_code, headers)
    except _req.ConnectionError:
        log.error("Proxy connection error: %s", url)
        return jsonify({"error": "Servicio no disponible"}), 502
    except _req.Timeout:
        return jsonify({"error": "Tiempo de espera agotado"}), 504


@app.route("/api/lis/<path:subpath>", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
@require_auth()
def proxy_lis(subpath):
    if request.method == "POST" and g.get("license_mode") == "readonly":
        return jsonify({"error": "Licencia vencida. Sistema en modo solo lectura."}), 403
    return _proxy(LIS_URL, f"api/erp/lis/{subpath}")


@app.route("/api/billing/<path:subpath>", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
@require_auth()
def proxy_billing(subpath):
    if request.method == "POST" and g.get("license_mode") == "readonly":
        return jsonify({"error": "Licencia vencida. Sistema en modo solo lectura."}), 403
    return _proxy(BILLING_URL, f"api/billing/{subpath}")

# ═══════════════════════════════════════════════════════════════════════════
# PATIENT API (direct DB access)
# ═══════════════════════════════════════════════════════════════════════════


def _row_to_dict(row):
    """Convert RealDictRow, normalising special types."""
    if not row:
        return row
    out = dict(row)
    for k, v in out.items():
        if isinstance(v, Decimal):
            out[k] = float(v)
        elif isinstance(v, (date, datetime)):
            out[k] = v.isoformat()
    return out


@app.route("/api/patients", methods=["GET"])
@require_auth()
def list_patients():
    q = request.args.get("q", "").strip()
    limit = min(int(request.args.get("limit", "50")), 200)
    offset = int(request.args.get("offset", "0"))

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if q:
                cur.execute(
                    "SELECT * FROM lis_patients "
                    "WHERE document_id ILIKE %s OR full_name ILIKE %s "
                    "ORDER BY full_name LIMIT %s OFFSET %s",
                    (f"%{q}%", f"%{q}%", limit, offset),
                )
            else:
                cur.execute(
                    "SELECT * FROM lis_patients ORDER BY created_at DESC "
                    "LIMIT %s OFFSET %s", (limit, offset),
                )
            rows = [_row_to_dict(r) for r in cur.fetchall()]

            # Count for pagination
            if q:
                cur.execute(
                    "SELECT COUNT(*) AS total FROM lis_patients "
                    "WHERE document_id ILIKE %s OR full_name ILIKE %s",
                    (f"%{q}%", f"%{q}%"),
                )
            else:
                cur.execute("SELECT COUNT(*) AS total FROM lis_patients")
            total = cur.fetchone()["total"]

        return jsonify({"patients": rows, "total": total}), 200
    finally:
        put_db(conn)


@app.route("/api/patients", methods=["POST"])
@require_auth()
def create_patient():
    if g.get("license_mode") == "readonly":
        return jsonify({"error": "Licencia vencida. Sistema en modo solo lectura."}), 403
    data = request.get_json(silent=True) or {}
    required = ("document_type", "document_id", "first_name", "last_name")
    missing = [f for f in required if not data.get(f, "").strip()]
    if missing:
        return jsonify({"error": f"Campos requeridos: {', '.join(missing)}"}), 400

    full_name = f"{data['first_name'].strip()} {data['last_name'].strip()}"
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id FROM lis_patients WHERE document_id = %s",
                        (data["document_id"].strip(),))
            if cur.fetchone():
                return jsonify({"error": "Ya existe un paciente con ese documento"}), 409

            cur.execute(
                "INSERT INTO lis_patients "
                "(document_type, document_id, first_name, last_name, full_name, "
                " birth_date, gender, email, phone, address, city, province, "
                " emergency_contact, emergency_phone, blood_type, notes, branch_code) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "RETURNING *",
                (
                    data["document_type"].strip(),
                    data["document_id"].strip(),
                    data["first_name"].strip(),
                    data["last_name"].strip(),
                    full_name,
                    data.get("birth_date") or None,
                    data.get("gender") or None,
                    data.get("email", "").strip() or None,
                    data.get("phone", "").strip() or None,
                    data.get("address", "").strip() or None,
                    data.get("city", "").strip() or None,
                    data.get("province", "").strip() or None,
                    data.get("emergency_contact", "").strip() or None,
                    data.get("emergency_phone", "").strip() or None,
                    data.get("blood_type", "").strip() or None,
                    data.get("notes", "").strip() or None,
                    data.get("branch_code", "").strip() or None,
                ),
            )
            patient = _row_to_dict(cur.fetchone())
        conn.commit()
        cu = request.current_user
        log_audit(cu.get("user_id") or cu.get("sub"), "CREATE_PATIENT", "lis_patients",
                  patient["id"], None, request.remote_addr)
        return jsonify({"patient": patient}), 201
    except Exception:
        conn.rollback()
        log.exception("Error creating patient")
        return jsonify({"error": "Error interno del servidor"}), 500
    finally:
        put_db(conn)


@app.route("/api/patients/<doc>", methods=["GET"])
@require_auth()
def get_patient(doc):
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM lis_patients WHERE document_id = %s", (doc,))
            row = cur.fetchone()
        if not row:
            return jsonify({"error": "Paciente no encontrado"}), 404
        return jsonify({"patient": _row_to_dict(row)}), 200
    finally:
        put_db(conn)


@app.route("/api/patients/<int:pid>", methods=["PUT"])
@require_auth()
def update_patient(pid):
    data = request.get_json(silent=True) or {}
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id FROM lis_patients WHERE id = %s", (pid,))
            if not cur.fetchone():
                return jsonify({"error": "Paciente no encontrado"}), 404

            allowed = (
                "document_type", "document_id", "first_name", "last_name",
                "birth_date", "gender", "email", "phone", "address", "city",
                "province", "emergency_contact", "emergency_phone",
                "blood_type", "notes", "branch_code", "is_active",
            )
            sets, vals = [], []
            for col in allowed:
                if col in data:
                    sets.append(f"{col} = %s")
                    vals.append(data[col] if data[col] != "" else None)

            # Recompute full_name if name parts changed
            if "first_name" in data or "last_name" in data:
                cur.execute("SELECT first_name, last_name FROM lis_patients WHERE id = %s", (pid,))
                existing = cur.fetchone()
                fn = data.get("first_name", existing["first_name"]).strip()
                ln = data.get("last_name", existing["last_name"]).strip()
                sets.append("full_name = %s")
                vals.append(f"{fn} {ln}")

            if not sets:
                return jsonify({"error": "No hay campos para actualizar"}), 400

            sets.append("updated_at = NOW()")
            vals.append(pid)
            cur.execute(
                f"UPDATE lis_patients SET {', '.join(sets)} WHERE id = %s RETURNING *",
                vals,
            )
            patient = _row_to_dict(cur.fetchone())
        conn.commit()
        cu = request.current_user
        log_audit(cu.get("user_id") or cu.get("sub"), "UPDATE_PATIENT", "lis_patients",
                  pid, None, request.remote_addr)
        return jsonify({"patient": patient}), 200
    except Exception:
        conn.rollback()
        log.exception("Error updating patient")
        return jsonify({"error": "Error interno del servidor"}), 500
    finally:
        put_db(conn)

# ═══════════════════════════════════════════════════════════════════════════
# DASHBOARD KPIs
# ═══════════════════════════════════════════════════════════════════════════


@app.route("/api/dashboard/kpis")
@require_auth()
def dashboard_kpis():
    conn = get_db()
    try:
        kpis = {}
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT COUNT(*) AS total FROM lis_patients WHERE is_active = TRUE")
            kpis["total_patients"] = cur.fetchone()["total"]

            cur.execute(
                "SELECT COUNT(*) AS total FROM his_lab_samples "
                "WHERE collection_date::date = CURRENT_DATE"
            )
            kpis["samples_today"] = cur.fetchone()["total"]

            cur.execute(
                "SELECT COUNT(*) AS total FROM his_lab_results WHERE status = 'pending'"
            )
            kpis["pending_results"] = cur.fetchone()["total"]

            cur.execute(
                "SELECT COUNT(*) AS total FROM his_lab_results "
                "WHERE status = 'final' AND updated_at::date = CURRENT_DATE"
            )
            kpis["validated_today"] = cur.fetchone()["total"]

            cur.execute(
                "SELECT COUNT(*) AS total FROM his_lab_results "
                "WHERE flag IN ('critical_low','critical_high') "
                "AND status IN ('preliminary','final') "
                "AND created_at::date = CURRENT_DATE"
            )
            kpis["critical_today"] = cur.fetchone()["total"]

            cur.execute(
                "SELECT status, COUNT(*) AS count FROM his_lab_samples "
                "WHERE collection_date::date = CURRENT_DATE "
                "GROUP BY status"
            )
            kpis["samples_by_status"] = {r["status"]: r["count"]
                                          for r in cur.fetchall()}

        return jsonify(kpis), 200
    except Exception:
        log.exception("Error fetching KPIs")
        return jsonify({"error": "Error obteniendo indicadores"}), 500
    finally:
        put_db(conn)

# ═══════════════════════════════════════════════════════════════════════════
# ADMIN: USER MANAGEMENT (super_admin only)
# ═══════════════════════════════════════════════════════════════════════════

_USER_COLS = "id, username, email, full_name, role, branch_id, is_active, last_login, created_at"
_ALL_ROLES = ["super_admin", "admin", "recepcion", "laboratorista", "bioquimico", "contador"]


@app.route("/api/admin/users", methods=["GET"])
@require_auth("configuracion")
def admin_list_users():
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"SELECT {_USER_COLS} FROM lis_users ORDER BY role, full_name")
            rows = [_row_to_dict(r) for r in cur.fetchall()]
        return jsonify({"users": rows}), 200
    finally:
        put_db(conn)


@app.route("/api/admin/users", methods=["POST"])
@require_auth("configuracion")
def admin_create_user():
    data = request.get_json(silent=True) or {}
    required = ("username", "email", "full_name", "role", "password")
    missing = [f for f in required if not data.get(f, "").strip()]
    if missing:
        return jsonify({"error": f"Campos requeridos: {', '.join(missing)}"}), 400
    if data["role"] not in _ALL_ROLES:
        return jsonify({"error": "Rol invalido"}), 400

    pw_hash = bcrypt.hashpw(data["password"].encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO lis_users (username, email, password_hash, full_name, role, branch_id) "
                "VALUES (%s,%s,%s,%s,%s,%s) RETURNING " + _USER_COLS,
                (
                    data["username"].strip(),
                    data["email"].strip().lower(),
                    pw_hash,
                    data["full_name"].strip(),
                    data["role"],
                    data.get("branch_id") or None,
                ),
            )
            user = _row_to_dict(cur.fetchone())
        conn.commit()
        cu = request.current_user
        log_audit(cu.get("user_id"), "CREATE_USER", "lis_users", user["id"],
                  {"role": user["role"]}, request.remote_addr)
        return jsonify({"user": user}), 201
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        return jsonify({"error": "Usuario o email ya existe"}), 409
    except Exception:
        conn.rollback()
        log.exception("Error creating user")
        return jsonify({"error": "Error interno del servidor"}), 500
    finally:
        put_db(conn)


@app.route("/api/admin/users/<int:uid>", methods=["PUT"])
@require_auth("configuracion")
def admin_update_user(uid):
    data = request.get_json(silent=True) or {}
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT role FROM lis_users WHERE id = %s", (uid,))
            row = cur.fetchone()
            if not row:
                return jsonify({"error": "Usuario no encontrado"}), 404

            sets, vals = [], []
            for col in ("username", "email", "full_name", "branch_id"):
                if col in data:
                    sets.append(f"{col} = %s")
                    vals.append(data[col] if data[col] != "" else None)
            if "role" in data:
                if data["role"] not in _ALL_ROLES:
                    return jsonify({"error": "Rol invalido"}), 400
                # Cannot change super_admin role via API (protect the role)
                if row["role"] == "super_admin" and data["role"] != "super_admin":
                    return jsonify({"error": "No se puede cambiar el rol de super_admin"}), 403
                sets.append("role = %s")
                vals.append(data["role"])
            if "is_active" in data:
                sets.append("is_active = %s")
                vals.append(bool(data["is_active"]))
            if data.get("password", "").strip():
                pw_hash = bcrypt.hashpw(
                    data["password"].encode("utf-8"), bcrypt.gensalt()
                ).decode("utf-8")
                sets.append("password_hash = %s")
                vals.append(pw_hash)
            if not sets:
                return jsonify({"error": "Sin cambios"}), 400

            sets.append("updated_at = NOW()")
            vals.append(uid)
            cur.execute(
                f"UPDATE lis_users SET {', '.join(sets)} WHERE id = %s RETURNING {_USER_COLS}",
                vals,
            )
            user = _row_to_dict(cur.fetchone())
        conn.commit()
        cu = request.current_user
        log_audit(cu.get("user_id"), "UPDATE_USER", "lis_users", uid, None, request.remote_addr)
        return jsonify({"user": user}), 200
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        return jsonify({"error": "Usuario o email ya existe"}), 409
    except Exception:
        conn.rollback()
        log.exception("Error updating user")
        return jsonify({"error": "Error interno del servidor"}), 500
    finally:
        put_db(conn)


@app.route("/api/admin/users/<int:uid>", methods=["DELETE"])
@require_auth("configuracion")
def admin_delete_user(uid):
    cu = request.current_user
    if cu.get("user_id") == uid:
        return jsonify({"error": "No puedes eliminar tu propia cuenta"}), 403
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT role, full_name FROM lis_users WHERE id = %s", (uid,))
            row = cur.fetchone()
            if not row:
                return jsonify({"error": "Usuario no encontrado"}), 404
            if row["role"] == "super_admin":
                return jsonify({"error": "Los usuarios super_admin no pueden ser eliminados"}), 403
            cur.execute("DELETE FROM lis_users WHERE id = %s", (uid,))
        conn.commit()
        log_audit(cu.get("user_id"), "DELETE_USER", "lis_users", uid,
                  {"full_name": row["full_name"]}, request.remote_addr)
        return jsonify({"status": "deleted"}), 200
    except Exception:
        conn.rollback()
        log.exception("Error deleting user")
        return jsonify({"error": "Error interno del servidor"}), 500
    finally:
        put_db(conn)


# ═══════════════════════════════════════════════════════════════════════════
# HEALTH CHECK
# ═══════════════════════════════════════════════════════════════════════════


@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200

# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    log.info("Dimed-LIS Web starting on port %d (debug=%s)", WEB_PORT, IS_DEBUG)
    app.run(host="0.0.0.0", port=WEB_PORT, debug=IS_DEBUG)
