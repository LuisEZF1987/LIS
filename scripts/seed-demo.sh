#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

if [ -f "$PROJECT_DIR/.env" ]; then
    set -a; source "$PROJECT_DIR/.env"; set +a
fi

DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_NAME="${DB_NAME:-dimed_lis}"
PG_USER="${PG_USER:-dimed}"

export PGPASSWORD="${PG_PASSWORD:-}"

PSQL="psql -h $DB_HOST -p $DB_PORT -U $PG_USER -d $DB_NAME -v ON_ERROR_STOP=1"

echo "Dimed-LIS — Cargando datos demo"
echo ""

# Admin user (password: Admin2026!)
ADMIN_HASH='$2b$12$LJ3m4ys6Gx5YPL3VXBhBNOQzWQDZMZh0YP5XKxBqzXfV0V3HQJXCy'

$PSQL <<SQL
-- Configuracion de institucion
INSERT INTO lis_config (institution_name, ruc, address, phone, email)
VALUES ('Laboratorio Demo', '1791234567001', 'Av. Principal 123, Quito', '02-2345678', 'info@labdemo.ec')
ON CONFLICT DO NOTHING;

-- Sede principal
INSERT INTO lis_branches (name, code, address, phone, ruc)
VALUES ('Sede Principal', 'MAIN', 'Av. Principal 123, Quito', '02-2345678', '1791234567001')
ON CONFLICT (code) DO NOTHING;

-- Usuario admin
INSERT INTO lis_users (username, email, password_hash, full_name, role, branch_id, is_active)
VALUES ('admin', 'admin@labdemo.ec', '${ADMIN_HASH}', 'Administrador', 'admin',
        (SELECT id FROM lis_branches WHERE code='MAIN'), TRUE)
ON CONFLICT (username) DO NOTHING;

-- Pacientes demo
INSERT INTO lis_patients (document_type, document_id, first_name, last_name, full_name, birth_date, gender, email, phone, city, province)
VALUES
    ('cedula', '1712345678', 'Maria', 'Garcia Lopez', 'Maria Garcia Lopez', '1985-03-15', 'F', 'maria@email.com', '0991234567', 'Quito', 'Pichincha'),
    ('cedula', '0912345678', 'Carlos', 'Rodriguez Perez', 'Carlos Rodriguez Perez', '1978-07-22', 'M', 'carlos@email.com', '0987654321', 'Guayaquil', 'Guayas'),
    ('cedula', '0112345678', 'Ana', 'Martinez Ruiz', 'Ana Martinez Ruiz', '1990-11-08', 'F', 'ana@email.com', '0976543210', 'Cuenca', 'Azuay')
ON CONFLICT (document_id) DO NOTHING;

-- SRI config demo (ambiente pruebas)
INSERT INTO sri_config (ruc, razon_social, nombre_comercial, direccion_matriz, ambiente, tipo_emision)
VALUES ('1791234567001', 'Laboratorio Demo S.A.', 'Lab Demo', 'Av. Principal 123, Quito', 1, 1)
ON CONFLICT DO NOTHING;

-- Punto de emision
INSERT INTO sri_puntos_emision (establecimiento, punto_emision)
VALUES ('001', '001')
ON CONFLICT DO NOTHING;

-- Secuenciales iniciales
INSERT INTO sri_secuenciales (punto_emision_id, tipo_comprobante, secuencial)
VALUES
    ((SELECT id FROM sri_puntos_emision LIMIT 1), '01', 0),
    ((SELECT id FROM sri_puntos_emision LIMIT 1), '04', 0)
ON CONFLICT DO NOTHING;
SQL

echo "Datos demo cargados."
echo ""
echo "Credenciales:"
echo "  Usuario: admin"
echo "  Password: Admin2026!"
