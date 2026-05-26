#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
MIGRATIONS_DIR="$PROJECT_DIR/migrations"

# Load env
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a; source "$PROJECT_DIR/.env"; set +a
fi

DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5434}"
DB_NAME="${DB_NAME:-dimed_lis}"
PG_USER="${PG_USER:-dimed}"

export PGPASSWORD="${PG_PASSWORD:-}"

PSQL="psql -h $DB_HOST -p $DB_PORT -U $PG_USER -d $DB_NAME -v ON_ERROR_STOP=1"

echo "Dimed-LIS — Ejecutando migraciones"
echo "Base de datos: $DB_NAME@$DB_HOST:$DB_PORT"
echo ""

# Create migration tracking table if not exists
$PSQL -c "
CREATE TABLE IF NOT EXISTS lis_migrations (
    id SERIAL PRIMARY KEY,
    filename VARCHAR(200) UNIQUE NOT NULL,
    applied_at TIMESTAMPTZ DEFAULT NOW()
);
" 2>/dev/null

applied=0
skipped=0

for sql_file in "$MIGRATIONS_DIR"/[0-9]*.sql; do
    [ -f "$sql_file" ] || continue
    fname=$(basename "$sql_file")

    already=$($PSQL -t -c "SELECT COUNT(*) FROM lis_migrations WHERE filename='$fname'" 2>/dev/null | tr -d ' ')
    if [ "$already" -gt 0 ]; then
        skipped=$((skipped + 1))
        continue
    fi

    echo "  Aplicando: $fname"
    $PSQL -f "$sql_file"
    $PSQL -c "INSERT INTO lis_migrations(filename) VALUES('$fname')"
    applied=$((applied + 1))
done

echo ""
echo "Migraciones completadas: $applied aplicadas, $skipped ya existentes."
