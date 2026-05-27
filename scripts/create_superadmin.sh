#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# Dimed-LIS — Crear usuario super_admin
# Uso: ./scripts/create_superadmin.sh
# ============================================================

CONTAINER="dimed-lis-web"

echo ""
echo "============================================"
echo "  Dimed-LIS — Crear Super Admin"
echo "============================================"
echo ""

# Verificar que el contenedor este corriendo
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
  echo "ERROR: El contenedor '${CONTAINER}' no esta corriendo."
  echo "       Ejecuta primero: docker compose up -d"
  exit 1
fi

# Pedir datos
read -rp "Nombre completo   [Super Admin]: " FULL_NAME
FULL_NAME="${FULL_NAME:-Super Admin}"

read -rp "Usuario           [superadmin]: " USERNAME
USERNAME="${USERNAME:-superadmin}"

read -rp "Email             [admin@lab.com]: " EMAIL
EMAIL="${EMAIL:-admin@lab.com}"

# Pedir password con confirmacion (sin eco)
while true; do
  read -rsp "Contrasena: " PASSWORD
  echo ""
  read -rsp "Confirmar contrasena: " PASSWORD2
  echo ""
  if [ "${PASSWORD}" = "${PASSWORD2}" ]; then
    break
  fi
  echo "Las contrasenas no coinciden. Intenta de nuevo."
done

if [ ${#PASSWORD} -lt 8 ]; then
  echo "ERROR: La contrasena debe tener al menos 8 caracteres."
  exit 1
fi

echo ""
echo "Creando usuario..."

docker exec "${CONTAINER}" python3 - <<PYEOF
import bcrypt, os, psycopg2, sys

full_name = """${FULL_NAME}"""
username  = """${USERNAME}"""
email     = """${EMAIL}"""
password  = """${PASSWORD}"""

h = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

try:
    conn = psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", "5432")),
        dbname=os.environ["DB_NAME"],
        user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"],
    )
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM lis_users WHERE username = %s OR email = %s",
            (username, email),
        )
        if cur.fetchone():
            print("ERROR: Ya existe un usuario con ese username o email.")
            sys.exit(1)
        cur.execute(
            """INSERT INTO lis_users (username, email, password_hash, full_name, role)
               VALUES (%s, %s, %s, %s, 'super_admin') RETURNING id""",
            (username, email, h, full_name),
        )
        uid = cur.fetchone()[0]
    conn.commit()
    conn.close()
    print(f"OK — super_admin creado (id={uid})")
except psycopg2.errors.UniqueViolation:
    print("ERROR: Ya existe un usuario con ese username o email.")
    sys.exit(1)
except Exception as e:
    print(f"ERROR: {e}")
    sys.exit(1)
PYEOF

echo ""
echo "Listo. Ingresa en http://localhost:9100"
echo "  Usuario:   ${USERNAME}"
echo "  Rol:       super_admin"
echo ""
