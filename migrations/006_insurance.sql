-- Migration 006: Insurance - Insurers, Plans, Patient Policies, Preauthorizations, Claims, Tariffs
-- Target DB: dimed_lis
-- Purpose: Standalone insurance management for LIS (adapted from HIS 025 + 030, single DB)

-- Aseguradoras
CREATE TABLE IF NOT EXISTS lis_insurers (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL UNIQUE,
    ruc VARCHAR(13),
    contact_phone VARCHAR(50),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Seed: common Ecuadorian insurers
INSERT INTO lis_insurers (name) VALUES
    ('Seguros Sucre'),
    ('Equinoccial'),
    ('Humana'),
    ('BMI'),
    ('Saludsa'),
    ('Ecuasanitas'),
    ('Panamericana'),
    ('Latina Seguros'),
    ('AIG'),
    ('Chubb'),
    ('IESS'),
    ('ISSFA'),
    ('ISSPOL')
ON CONFLICT (name) DO NOTHING;

-- Planes de seguro por aseguradora
CREATE TABLE IF NOT EXISTS lis_insurance_plans (
    id SERIAL PRIMARY KEY,
    insurer_id INTEGER NOT NULL REFERENCES lis_insurers(id),
    plan_name VARCHAR(200) NOT NULL,
    plan_code VARCHAR(50),
    coverage_percent NUMERIC(5,2) NOT NULL DEFAULT 80
        CHECK (coverage_percent >= 0 AND coverage_percent <= 100),
    copay_amount NUMERIC(12,2) DEFAULT 0,
    deductible NUMERIC(12,2) DEFAULT 0,
    max_annual NUMERIC(12,2) DEFAULT 0,
    requires_preauth BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Polizas de pacientes
CREATE TABLE IF NOT EXISTS lis_patient_policies (
    id SERIAL PRIMARY KEY,
    patient_document VARCHAR(50) NOT NULL,
    patient_name VARCHAR(400),
    insurer_id INTEGER NOT NULL REFERENCES lis_insurers(id),
    plan_id INTEGER REFERENCES lis_insurance_plans(id),
    policy_number VARCHAR(100),
    member_id VARCHAR(100),
    holder_name VARCHAR(400),
    relationship VARCHAR(30) DEFAULT 'titular'
        CHECK (relationship IN ('titular','conyuge','hijo','dependiente','otro')),
    effective_date DATE,
    expiry_date DATE,
    status VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active','suspended','expired','cancelled')),
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Pre-autorizaciones
CREATE TABLE IF NOT EXISTS lis_preauthorizations (
    id SERIAL PRIMARY KEY,
    auth_number VARCHAR(100),
    patient_document VARCHAR(50) NOT NULL,
    policy_id INTEGER REFERENCES lis_patient_policies(id),
    insurer_id INTEGER NOT NULL REFERENCES lis_insurers(id),
    service_type VARCHAR(50),
    service_description VARCHAR(500),
    requested_amount NUMERIC(12,2) DEFAULT 0,
    approved_amount NUMERIC(12,2) DEFAULT 0,
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','approved','denied','expired')),
    denied_reason TEXT,
    requested_by INTEGER,
    reviewed_by INTEGER,
    requested_at TIMESTAMPTZ DEFAULT NOW(),
    reviewed_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Reclamos a aseguradoras
CREATE TABLE IF NOT EXISTS lis_insurance_claims (
    id SERIAL PRIMARY KEY,
    claim_number VARCHAR(100) UNIQUE,
    invoice_id INTEGER REFERENCES lis_invoices(id),
    policy_id INTEGER REFERENCES lis_patient_policies(id),
    insurer_id INTEGER NOT NULL REFERENCES lis_insurers(id),
    patient_document VARCHAR(50),
    patient_name VARCHAR(400),
    total_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    covered_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    copay_amount NUMERIC(12,2) DEFAULT 0,
    deductible_applied NUMERIC(12,2) DEFAULT 0,
    status VARCHAR(20) NOT NULL DEFAULT 'draft'
        CHECK (status IN (
            'draft','submitted','under_review','approved',
            'partial','denied','paid','appealed'
        )),
    submitted_at TIMESTAMPTZ,
    settlement_date DATE,
    settlement_amount NUMERIC(12,2) DEFAULT 0,
    settlement_reference VARCHAR(200),
    denied_reason TEXT,
    notes TEXT,
    created_by INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Tarifarios por aseguradora (precios especiales por plan/aseguradora)
CREATE TABLE IF NOT EXISTS lis_insurer_tariffs (
    id SERIAL PRIMARY KEY,
    insurer_id INTEGER NOT NULL REFERENCES lis_insurers(id),
    plan_id INTEGER REFERENCES lis_insurance_plans(id),
    catalog_id INTEGER,
    service_code VARCHAR(50),
    service_name VARCHAR(200),
    tariff_price NUMERIC(12,2) NOT NULL DEFAULT 0,
    coverage_percent NUMERIC(5,2) DEFAULT 80,
    requires_preauth BOOLEAN DEFAULT FALSE,
    effective_date DATE DEFAULT CURRENT_DATE,
    expiry_date DATE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_lis_plans_insurer ON lis_insurance_plans(insurer_id);
CREATE INDEX IF NOT EXISTS idx_lis_policies_doc ON lis_patient_policies(patient_document);
CREATE INDEX IF NOT EXISTS idx_lis_policies_insurer ON lis_patient_policies(insurer_id);
CREATE INDEX IF NOT EXISTS idx_lis_policies_status ON lis_patient_policies(status);
CREATE INDEX IF NOT EXISTS idx_lis_preauth_patient ON lis_preauthorizations(patient_document);
CREATE INDEX IF NOT EXISTS idx_lis_preauth_policy ON lis_preauthorizations(policy_id);
CREATE INDEX IF NOT EXISTS idx_lis_preauth_status ON lis_preauthorizations(status);
CREATE INDEX IF NOT EXISTS idx_lis_claims_insurer ON lis_insurance_claims(insurer_id);
CREATE INDEX IF NOT EXISTS idx_lis_claims_invoice ON lis_insurance_claims(invoice_id);
CREATE INDEX IF NOT EXISTS idx_lis_claims_status ON lis_insurance_claims(status);
CREATE INDEX IF NOT EXISTS idx_lis_claims_patient ON lis_insurance_claims(patient_document);
CREATE INDEX IF NOT EXISTS idx_lis_tariffs_insurer ON lis_insurer_tariffs(insurer_id);
CREATE INDEX IF NOT EXISTS idx_lis_tariffs_catalog ON lis_insurer_tariffs(catalog_id);
CREATE INDEX IF NOT EXISTS idx_lis_tariffs_code ON lis_insurer_tariffs(service_code);
