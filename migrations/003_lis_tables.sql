-- Migration 003: LIS core tables - Analytes, Reference Ranges, Instruments, Samples, Results, QC
-- Target DB: dimed_lis
-- Purpose: Laboratory Information System core workflow tables
-- Note: his_ prefix kept for compatibility with lis_app.py

-- Catalogo de analitos/pruebas
CREATE TABLE IF NOT EXISTS his_lab_analytes (
    id SERIAL PRIMARY KEY,
    code VARCHAR(50) UNIQUE NOT NULL,
    name VARCHAR(300) NOT NULL,
    category VARCHAR(50) NOT NULL
        CHECK (category IN (
            'hematologia','quimica','coagulacion','hormonas',
            'inmunologia','microbiologia','urinalisis','marcadores',
            'gases','otro'
        )),
    unit VARCHAR(50),
    decimal_places INTEGER DEFAULT 2,
    method VARCHAR(200),
    sample_type VARCHAR(30) DEFAULT 'sangre'
        CHECK (sample_type IN ('sangre','orina','heces','liquido','tejido','otro')),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS his_lab_reference_ranges (
    id SERIAL PRIMARY KEY,
    analyte_id INTEGER NOT NULL REFERENCES his_lab_analytes(id) ON DELETE CASCADE,
    gender VARCHAR(5) DEFAULT 'all'
        CHECK (gender IN ('M','F','all')),
    age_min INTEGER DEFAULT 0,
    age_max INTEGER DEFAULT 999,
    range_low NUMERIC(12,4),
    range_high NUMERIC(12,4),
    critical_low NUMERIC(12,4),
    critical_high NUMERIC(12,4),
    unit VARCHAR(50),
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS his_lab_instruments (
    id SERIAL PRIMARY KEY,
    code VARCHAR(50) UNIQUE NOT NULL,
    name VARCHAR(200) NOT NULL,
    manufacturer VARCHAR(200),
    model VARCHAR(200),
    serial_number VARCHAR(100),
    hl7_sender_id VARCHAR(100),
    hl7_sender_facility VARCHAR(100),
    connection_type VARCHAR(20) DEFAULT 'manual'
        CHECK (connection_type IN ('hl7_mllp','hl7_file','serial','manual')),
    host VARCHAR(200),
    port INTEGER,
    is_active BOOLEAN DEFAULT TRUE,
    branch_code VARCHAR(20),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS his_lab_samples (
    id SERIAL PRIMARY KEY,
    sample_code VARCHAR(50) UNIQUE NOT NULL,
    patient_document VARCHAR(50) NOT NULL,
    patient_name VARCHAR(400),
    encounter_id INTEGER,
    service_order_id VARCHAR(50),
    sample_type VARCHAR(30) DEFAULT 'sangre'
        CHECK (sample_type IN ('sangre','orina','heces','liquido','tejido','otro')),
    collection_date TIMESTAMPTZ DEFAULT NOW(),
    collected_by INTEGER,
    received_date TIMESTAMPTZ,
    received_by INTEGER,
    status VARCHAR(20) NOT NULL DEFAULT 'collected'
        CHECK (status IN ('collected','received','in_process','completed','rejected')),
    rejection_reason TEXT,
    branch_code VARCHAR(20),
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS his_lab_results (
    id SERIAL PRIMARY KEY,
    sample_id INTEGER NOT NULL REFERENCES his_lab_samples(id) ON DELETE CASCADE,
    analyte_id INTEGER NOT NULL REFERENCES his_lab_analytes(id),
    value VARCHAR(500),
    numeric_value NUMERIC(12,4),
    unit VARCHAR(50),
    flag VARCHAR(20) DEFAULT 'normal'
        CHECK (flag IN ('normal','low','high','critical_low','critical_high','abnormal')),
    reference_range_text VARCHAR(200),
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','preliminary','final','corrected','cancelled')),
    instrument_id INTEGER REFERENCES his_lab_instruments(id),
    raw_value VARCHAR(500),
    tech_validated_by INTEGER,
    tech_validated_at TIMESTAMPTZ,
    med_validated_by INTEGER,
    med_validated_at TIMESTAMPTZ,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS his_lab_qc (
    id SERIAL PRIMARY KEY,
    instrument_id INTEGER NOT NULL REFERENCES his_lab_instruments(id),
    analyte_id INTEGER NOT NULL REFERENCES his_lab_analytes(id),
    qc_date DATE NOT NULL DEFAULT CURRENT_DATE,
    level VARCHAR(20) NOT NULL
        CHECK (level IN ('low','normal','high')),
    expected_value NUMERIC(12,4) NOT NULL,
    actual_value NUMERIC(12,4) NOT NULL,
    sd NUMERIC(12,4),
    cv_percent NUMERIC(8,4),
    status VARCHAR(20) DEFAULT 'accepted'
        CHECK (status IN ('accepted','rejected','warning')),
    lot_number VARCHAR(100),
    notes TEXT,
    created_by INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_analytes_category ON his_lab_analytes(category);
CREATE INDEX IF NOT EXISTS idx_analytes_code ON his_lab_analytes(code);
CREATE INDEX IF NOT EXISTS idx_ref_ranges_analyte ON his_lab_reference_ranges(analyte_id);
CREATE INDEX IF NOT EXISTS idx_instruments_code ON his_lab_instruments(code);
CREATE INDEX IF NOT EXISTS idx_instruments_hl7 ON his_lab_instruments(hl7_sender_id);
CREATE INDEX IF NOT EXISTS idx_samples_patient ON his_lab_samples(patient_document);
CREATE INDEX IF NOT EXISTS idx_samples_status ON his_lab_samples(status);
CREATE INDEX IF NOT EXISTS idx_samples_code ON his_lab_samples(sample_code);
CREATE INDEX IF NOT EXISTS idx_samples_branch ON his_lab_samples(branch_code);
CREATE INDEX IF NOT EXISTS idx_samples_date ON his_lab_samples(collection_date);
CREATE INDEX IF NOT EXISTS idx_results_sample ON his_lab_results(sample_id);
CREATE INDEX IF NOT EXISTS idx_results_analyte ON his_lab_results(analyte_id);
CREATE INDEX IF NOT EXISTS idx_results_status ON his_lab_results(status);
CREATE INDEX IF NOT EXISTS idx_results_flag ON his_lab_results(flag);
CREATE INDEX IF NOT EXISTS idx_qc_instrument ON his_lab_qc(instrument_id);
CREATE INDEX IF NOT EXISTS idx_qc_analyte ON his_lab_qc(analyte_id);
CREATE INDEX IF NOT EXISTS idx_qc_date ON his_lab_qc(qc_date);
