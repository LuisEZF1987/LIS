-- Migration 007: SRI Ecuador Electronic Invoicing + Migration Tracking
-- Target DB: dimed_lis
-- Purpose: Tables for SRI e-invoicing (facturacion electronica Ecuador)

-- Configuracion SRI por institucion
CREATE TABLE IF NOT EXISTS sri_config (
    id SERIAL PRIMARY KEY,
    ruc VARCHAR(13) NOT NULL,
    razon_social VARCHAR(300) NOT NULL,
    nombre_comercial VARCHAR(300),
    direccion_matriz VARCHAR(500),
    obligado_contabilidad BOOLEAN DEFAULT FALSE,
    contribuyente_especial VARCHAR(20),
    ambiente SMALLINT NOT NULL DEFAULT 1
        CHECK (ambiente IN (1, 2)),                 -- 1=pruebas, 2=produccion
    tipo_emision SMALLINT NOT NULL DEFAULT 1,       -- 1=normal
    cert_path VARCHAR(500),
    cert_password_hash VARCHAR(200),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Puntos de emision (establecimiento + punto)
CREATE TABLE IF NOT EXISTS sri_puntos_emision (
    id SERIAL PRIMARY KEY,
    establecimiento VARCHAR(3) NOT NULL,
    punto_emision VARCHAR(3) NOT NULL,
    branch_id INTEGER,
    is_active BOOLEAN DEFAULT TRUE,
    UNIQUE(establecimiento, punto_emision)
);

-- Secuenciales por punto de emision y tipo de comprobante
CREATE TABLE IF NOT EXISTS sri_secuenciales (
    id SERIAL PRIMARY KEY,
    punto_emision_id INTEGER NOT NULL REFERENCES sri_puntos_emision(id),
    tipo_comprobante VARCHAR(2) NOT NULL
        CHECK (tipo_comprobante IN ('01', '04', '07')),  -- 01=factura, 04=nota credito, 07=retencion
    secuencial INTEGER NOT NULL DEFAULT 0,
    UNIQUE(punto_emision_id, tipo_comprobante)
);

-- Comprobantes electronicos emitidos
CREATE TABLE IF NOT EXISTS sri_comprobantes (
    id SERIAL PRIMARY KEY,
    invoice_id INTEGER REFERENCES lis_invoices(id),
    tipo_comprobante VARCHAR(2) NOT NULL,
    clave_acceso VARCHAR(49) UNIQUE,
    numero_autorizacion VARCHAR(49),
    fecha_emision DATE NOT NULL,
    fecha_autorizacion TIMESTAMPTZ,
    estado VARCHAR(20) NOT NULL DEFAULT 'generado'
        CHECK (estado IN ('generado','firmado','enviado','autorizado','rechazado','anulado')),
    xml_generado TEXT,
    xml_firmado TEXT,
    xml_autorizacion TEXT,
    mensajes_sri JSONB,
    intentos_envio INTEGER DEFAULT 0,
    ultimo_envio TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_sri_comprobantes_clave ON sri_comprobantes(clave_acceso);
CREATE INDEX IF NOT EXISTS idx_sri_comprobantes_estado ON sri_comprobantes(estado);
CREATE INDEX IF NOT EXISTS idx_sri_comprobantes_invoice ON sri_comprobantes(invoice_id);
CREATE INDEX IF NOT EXISTS idx_sri_comprobantes_fecha ON sri_comprobantes(fecha_emision);

-- ============================================================
-- Migration tracking table
-- ============================================================
CREATE TABLE IF NOT EXISTS lis_migrations (
    id SERIAL PRIMARY KEY,
    filename VARCHAR(200) UNIQUE NOT NULL,
    applied_at TIMESTAMPTZ DEFAULT NOW()
);
