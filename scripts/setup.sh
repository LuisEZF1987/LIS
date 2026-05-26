#!/usr/bin/env bash
set -euo pipefail

echo "============================================"
echo "  Dimed-LIS - Asistente de Instalacion"
echo "============================================"
echo ""

if [ -f .env ]; then
    echo "Archivo .env ya existe."
    read -p "Desea sobreescribirlo? (y/N): " overwrite
    if [ "$overwrite" != "y" ]; then
        echo "Instalacion cancelada."
        exit 0
    fi
fi

cp .env.example .env

read -p "Nombre de la institucion: " INST_NAME
read -p "RUC: " RUC
read -p "Direccion: " ADDRESS
read -p "Telefono: " PHONE

# Generate secrets
JWT_SECRET=$(openssl rand -hex 32 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(32))")
PG_PASSWORD=$(openssl rand -hex 16 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(16))")

# SRI config
echo ""
echo "--- Configuracion SRI ---"
read -p "Ambiente SRI (1=Pruebas, 2=Produccion) [1]: " SRI_AMB
SRI_AMB=${SRI_AMB:-1}
read -p "Ruta al certificado .p12 (dejar vacio si no tiene): " CERT_PATH

# Write .env
sed -i "s|PG_PASSWORD=.*|PG_PASSWORD=${PG_PASSWORD}|" .env
sed -i "s|JWT_SECRET=.*|JWT_SECRET=${JWT_SECRET}|" .env
sed -i "s|SRI_AMBIENTE=.*|SRI_AMBIENTE=${SRI_AMB}|" .env
sed -i "s|SRI_RUC=.*|SRI_RUC=${RUC}|" .env
sed -i "s|SRI_RAZON_SOCIAL=.*|SRI_RAZON_SOCIAL=${INST_NAME}|" .env
sed -i "s|SRI_DIRECCION_MATRIZ=.*|SRI_DIRECCION_MATRIZ=${ADDRESS}|" .env

if [ -n "$CERT_PATH" ] && [ -f "$CERT_PATH" ]; then
    mkdir -p certs
    cp "$CERT_PATH" certs/firma.p12
    echo "Certificado copiado a certs/firma.p12"
fi

echo ""
echo "Configuracion guardada en .env"
echo ""
echo "Siguientes pasos:"
echo "  make up       # Iniciar servicios"
echo "  make migrate  # Crear tablas"
echo "  make seed     # Datos demo (opcional)"
echo ""
echo "Luego acceda a http://localhost:9000"
