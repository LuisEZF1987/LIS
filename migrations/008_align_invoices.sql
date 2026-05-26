-- Migration 008: Align lis_invoices with billing_app.py columns
-- Adds columns expected by the billing microservice

-- Columns used by billing_app.py but missing from 005
ALTER TABLE lis_invoices ADD COLUMN IF NOT EXISTS branch_id INTEGER;
ALTER TABLE lis_invoices ADD COLUMN IF NOT EXISTS establecimiento VARCHAR(10) DEFAULT '001';
ALTER TABLE lis_invoices ADD COLUMN IF NOT EXISTS punto_emision VARCHAR(10) DEFAULT '001';
ALTER TABLE lis_invoices ADD COLUMN IF NOT EXISTS patient_address VARCHAR(500);
ALTER TABLE lis_invoices ADD COLUMN IF NOT EXISTS patient_email VARCHAR(200);
ALTER TABLE lis_invoices ADD COLUMN IF NOT EXISTS patient_phone VARCHAR(50);
ALTER TABLE lis_invoices ADD COLUMN IF NOT EXISTS subtotal_0 NUMERIC(12,2) DEFAULT 0;
ALTER TABLE lis_invoices ADD COLUMN IF NOT EXISTS subtotal_iva NUMERIC(12,2) DEFAULT 0;
ALTER TABLE lis_invoices ADD COLUMN IF NOT EXISTS iva_amount NUMERIC(12,2) DEFAULT 0;
ALTER TABLE lis_invoices ADD COLUMN IF NOT EXISTS insurer_name VARCHAR(300);
ALTER TABLE lis_invoices ADD COLUMN IF NOT EXISTS secuencial INTEGER;
ALTER TABLE lis_invoices ADD COLUMN IF NOT EXISTS sri_clave_acceso VARCHAR(60);
ALTER TABLE lis_invoices ADD COLUMN IF NOT EXISTS sri_estado VARCHAR(30);
ALTER TABLE lis_invoices ADD COLUMN IF NOT EXISTS sri_fecha_autorizacion TIMESTAMPTZ;
ALTER TABLE lis_invoices ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ;
ALTER TABLE lis_invoices ADD COLUMN IF NOT EXISTS credit_note_id INTEGER REFERENCES lis_invoices(id);

-- payment_source on payment_applications needs wider values
ALTER TABLE lis_payment_applications DROP CONSTRAINT IF EXISTS lis_payment_applications_payment_source_check;
ALTER TABLE lis_payment_applications ADD CONSTRAINT lis_payment_applications_payment_source_check
    CHECK (payment_source IN ('cash','pos','transfer','credit_note','efectivo','tarjeta_debito','tarjeta_credito','transferencia'));

-- journal_entries also needs branch_id
ALTER TABLE lis_journal_entries ADD COLUMN IF NOT EXISTS branch_id INTEGER;

-- branches need SRI emission point columns
ALTER TABLE lis_branches ADD COLUMN IF NOT EXISTS establecimiento VARCHAR(10) DEFAULT '001';
ALTER TABLE lis_branches ADD COLUMN IF NOT EXISTS punto_emision VARCHAR(10) DEFAULT '001';

-- Index for SRI lookups
CREATE INDEX IF NOT EXISTS idx_lis_invoices_sri ON lis_invoices(sri_clave_acceso);
CREATE INDEX IF NOT EXISTS idx_lis_invoices_secuencial ON lis_invoices(secuencial);
