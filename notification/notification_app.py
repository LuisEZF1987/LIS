#!/usr/bin/env python3
"""
Dimed-LIS Notification Service.
Sends email + PDF attachment to patient when lab results are finalized.

Endpoints:
  POST /api/notify/lab  - Notify patient that results are ready
  GET  /health          - Health check
"""

import logging
import os
import smtplib
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import psycopg2
import psycopg2.extras
import requests as _req
from flask import Flask, jsonify, request

app = Flask(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [NOTIFY-LIS] %(levelname)s %(message)s",
)
log = logging.getLogger("lis-notify")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_USER", "noreply@lab.com")
SMTP_TLS = os.environ.get("SMTP_TLS", "true").lower() == "true"

LIS_URL = os.environ.get("LIS_URL", "http://lis:9008")
LIS_SERVICE_TOKEN = os.environ.get("LIS_SERVICE_TOKEN", "")
INSTITUTION = os.environ.get("INSTITUTION_NAME", "Laboratorio")
LOGO_PATH = os.environ.get("LOGO_PATH", "/certs/logo.png")
LISTEN_PORT = int(os.environ.get("NOTIFY_PORT", "9004"))

DB_HOST = os.environ.get("DB_HOST", "postgres")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "dimed_lis")
DB_USER = os.environ.get("PG_USER", "dimed")
DB_PASS = os.environ.get("PG_PASSWORD", "")

# ---------------------------------------------------------------------------
# Logo (CID-embedded in email)
# ---------------------------------------------------------------------------

_LOGO_BYTES = None
if LOGO_PATH and os.path.isfile(LOGO_PATH):
    with open(LOGO_PATH, "rb") as _f:
        _LOGO_BYTES = _f.read()
    log.info("Logo loaded from %s (%d bytes)", LOGO_PATH, len(_LOGO_BYTES))
else:
    log.warning("Logo not found at %s — emails will use text branding only", LOGO_PATH)


def _logo_html(width: int = 160) -> str:
    if _LOGO_BYTES:
        return (f'<img src="cid:dimedlogo" alt="{INSTITUTION}" '
                f'width="{width}" style="display:block;margin:0 auto;">')
    return f'<strong style="color:#60a5fa;font-size:1.4rem;">{INSTITUTION}</strong>'


# ---------------------------------------------------------------------------
# Email builder
# ---------------------------------------------------------------------------

def build_lab_email(patient_name: str, order_description: str) -> str:
    return f"""
    <div style="font-family:Inter,sans-serif;max-width:600px;margin:0 auto;
                background:#0f172a;color:#e2e8f0;padding:40px;border-radius:16px;">
        <div style="text-align:center;margin-bottom:30px;">
            {_logo_html(160)}
            <h1 style="margin:10px 0 0;font-size:24px;color:#fff;">{INSTITUTION}</h1>
        </div>
        <div style="background:rgba(16,185,129,0.1);border:1px solid rgba(16,185,129,0.2);
                    border-radius:12px;padding:30px;margin-bottom:20px;">
            <h2 style="color:#34d399;margin-top:0;">
                &#128300; Resultados de Laboratorio Disponibles
            </h2>
            <p>Estimado/a <strong>{patient_name}</strong>,</p>
            <p>Le informamos que los resultados de su orden de laboratorio
               <strong>{order_description}</strong> ya se encuentran disponibles.</p>
            <p style="margin-top:1.5rem;color:#94a3b8;font-size:0.9rem;">
                Los resultados se adjuntan en este correo en formato PDF.
            </p>
        </div>
        <p style="color:#94a3b8;font-size:13px;text-align:center;">
            {INSTITUTION} &middot; Soporte Tecnico Especializado
        </p>
    </div>
    """


# ---------------------------------------------------------------------------
# SMTP sender
# ---------------------------------------------------------------------------

def send_email(to_email: str, subject: str, html_body: str,
               pdf_bytes: bytes = None, pdf_filename: str = None) -> bool:
    if not SMTP_HOST or not to_email:
        log.warning("SMTP not configured or no recipient — skipping email to %s", to_email)
        return False

    msg = MIMEMultipart("related")
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    msg["Subject"] = subject

    alt_part = MIMEMultipart("alternative")
    alt_part.attach(MIMEText(html_body, "html", "utf-8"))
    msg.attach(alt_part)

    if _LOGO_BYTES:
        img = MIMEImage(_LOGO_BYTES, _subtype="png")
        img.add_header("Content-ID", "<dimedlogo>")
        img.add_header("Content-Disposition", "inline", filename="logo.png")
        msg.attach(img)

    if pdf_bytes and pdf_filename:
        attachment = MIMEApplication(pdf_bytes, _subtype="pdf")
        attachment.add_header(
            "Content-Disposition", "attachment", filename=pdf_filename
        )
        msg.attach(attachment)

    try:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        if SMTP_TLS:
            server.starttls()
        if SMTP_USER:
            server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()
        log.info("Email sent to %s: %s", to_email, subject)
        return True
    except Exception:
        log.exception("Failed to send email to %s", to_email)
        return False


# ---------------------------------------------------------------------------
# DB helper
# ---------------------------------------------------------------------------

def _get_patient_email(patient_document: str):
    """Returns (email, full_name) or (None, None) if not found."""
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
            user=DB_USER, password=DB_PASS,
        )
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT email, full_name FROM lis_patients WHERE document_id = %s",
                (patient_document,),
            )
            row = cur.fetchone()
        conn.close()
        if row:
            return row["email"], row["full_name"]
        return None, None
    except Exception:
        log.exception("DB error looking up patient %s", patient_document)
        return None, None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.route("/health")
def health():
    return jsonify({"status": "ok", "smtp": bool(SMTP_HOST)})


@app.route("/api/notify/lab", methods=["POST"])
def notify_lab():
    data = request.get_json(silent=True) or {}
    patient_doc = data.get("patient_document", "").strip()
    order_desc = data.get("order_description", "laboratorio")
    sample_id = data.get("sample_id")

    if not patient_doc:
        return jsonify({"error": "patient_document required"}), 400

    # Lookup patient email
    email, full_name = _get_patient_email(patient_doc)
    if not email:
        log.warning("No email for patient %s — skipping notification", patient_doc)
        return jsonify({"status": "skipped", "reason": "no email"}), 200

    # Fetch PDF from LIS if sample_id provided
    pdf_bytes = None
    pdf_filename = None
    if sample_id and LIS_SERVICE_TOKEN:
        try:
            r = _req.get(
                f"{LIS_URL}/api/erp/lis/samples/{sample_id}/pdf",
                headers={"Authorization": f"Bearer {LIS_SERVICE_TOKEN}"},
                timeout=15,
            )
            if r.status_code == 200 and r.content:
                pdf_bytes = r.content
                pdf_filename = f"resultados_{sample_id}.pdf"
                log.info("PDF fetched for sample %s (%d bytes)", sample_id, len(pdf_bytes))
            else:
                log.warning("PDF fetch failed for sample %s: HTTP %d",
                            sample_id, r.status_code)
        except Exception:
            log.warning("Failed to fetch PDF for sample %s", sample_id)

    html = build_lab_email(full_name or patient_doc, order_desc)
    subject = f"{INSTITUTION} — Sus resultados de laboratorio estan listos"
    send_email(email, subject, html, pdf_bytes, pdf_filename)

    return jsonify({"status": "ok"}), 200


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=LISTEN_PORT)
