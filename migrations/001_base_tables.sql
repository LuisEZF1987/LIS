-- Migration 001: Base tables - Config, Branches, Users, Audit
-- Target DB: dimed_lis
-- Purpose: Foundation tables for the Dimed-LIS standalone system

-- Configuracion general de la institucion
CREATE TABLE IF NOT EXISTS lis_config (
    id SERIAL PRIMARY KEY,
    institution_name VARCHAR(300) NOT NULL,
    ruc VARCHAR(13),
    address VARCHAR(500),
    phone VARCHAR(50),
    email VARCHAR(200),
    logo_path VARCHAR(500),
    currency VARCHAR(10) DEFAULT 'USD',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Sedes / sucursales
CREATE TABLE IF NOT EXISTS lis_branches (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    code VARCHAR(20) UNIQUE NOT NULL,
    address VARCHAR(500),
    phone VARCHAR(50),
    ruc VARCHAR(13),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Usuarios del sistema
CREATE TABLE IF NOT EXISTS lis_users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(100) UNIQUE NOT NULL,
    email VARCHAR(200) UNIQUE NOT NULL,
    password_hash VARCHAR(300) NOT NULL,
    full_name VARCHAR(400) NOT NULL,
    role VARCHAR(30) NOT NULL
        CHECK (role IN ('admin', 'recepcion', 'laboratorista', 'bioquimico', 'contador')),
    branch_id INTEGER REFERENCES lis_branches(id),
    is_active BOOLEAN DEFAULT TRUE,
    last_login TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Log de auditoria
CREATE TABLE IF NOT EXISTS lis_audit_log (
    id SERIAL PRIMARY KEY,
    user_id INTEGER,
    action VARCHAR(100) NOT NULL,
    entity VARCHAR(100),
    entity_id VARCHAR(100),
    details JSONB,
    ip_address VARCHAR(45),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_lis_users_username ON lis_users(username);
CREATE INDEX IF NOT EXISTS idx_lis_users_email ON lis_users(email);
CREATE INDEX IF NOT EXISTS idx_lis_users_role ON lis_users(role);
CREATE INDEX IF NOT EXISTS idx_lis_audit_user ON lis_audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_lis_audit_entity ON lis_audit_log(entity);
CREATE INDEX IF NOT EXISTS idx_lis_audit_created ON lis_audit_log(created_at);
