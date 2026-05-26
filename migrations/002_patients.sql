-- Migration 002: Patients table
-- Target DB: dimed_lis
-- Purpose: Patient registry for the laboratory system

CREATE TABLE IF NOT EXISTS lis_patients (
    id SERIAL PRIMARY KEY,
    document_type VARCHAR(20) NOT NULL
        CHECK (document_type IN ('cedula', 'ruc', 'pasaporte')),
    document_id VARCHAR(50) UNIQUE NOT NULL,
    first_name VARCHAR(200) NOT NULL,
    last_name VARCHAR(200) NOT NULL,
    full_name VARCHAR(400) NOT NULL,
    birth_date DATE,
    gender VARCHAR(5)
        CHECK (gender IN ('M', 'F')),
    email VARCHAR(200),
    phone VARCHAR(50),
    address VARCHAR(500),
    city VARCHAR(200),
    province VARCHAR(200),
    emergency_contact VARCHAR(400),
    emergency_phone VARCHAR(50),
    blood_type VARCHAR(10),
    notes TEXT,
    branch_code VARCHAR(20),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_lis_patients_document ON lis_patients(document_id);
CREATE INDEX IF NOT EXISTS idx_lis_patients_name ON lis_patients(full_name);
CREATE INDEX IF NOT EXISTS idx_lis_patients_branch ON lis_patients(branch_code);
