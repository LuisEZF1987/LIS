-- Migration 005: Invoicing, Accounts Receivable, Journal Entries, Payment Applications
-- Target DB: dimed_lis
-- Purpose: Standalone invoicing for LIS (adapted from HIS 029, single DB)

-- Facturas
CREATE TABLE IF NOT EXISTS lis_invoices (
    id SERIAL PRIMARY KEY,
    invoice_number VARCHAR(50) UNIQUE,
    branch_code VARCHAR(20),
    patient_id VARCHAR(50),
    patient_document VARCHAR(50),
    patient_name VARCHAR(400),
    invoice_type VARCHAR(20) NOT NULL DEFAULT 'out'
        CHECK (invoice_type IN ('out', 'in', 'credit_note')),
    subtotal NUMERIC(12,2) DEFAULT 0,
    tax_amount NUMERIC(12,2) DEFAULT 0,
    total NUMERIC(12,2) DEFAULT 0,
    currency VARCHAR(10) DEFAULT 'USD',
    status VARCHAR(20) NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'validated', 'posted', 'paid', 'cancelled')),
    insurer_id INTEGER,
    notes TEXT,
    created_by INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Lineas de factura
CREATE TABLE IF NOT EXISTS lis_invoice_lines (
    id SERIAL PRIMARY KEY,
    invoice_id INTEGER NOT NULL REFERENCES lis_invoices(id) ON DELETE CASCADE,
    service_order_id VARCHAR(50),
    catalog_id INTEGER,
    description VARCHAR(500) NOT NULL,
    quantity NUMERIC(10,2) NOT NULL DEFAULT 1,
    unit_price NUMERIC(12,2) NOT NULL DEFAULT 0,
    discount_percent NUMERIC(5,2) DEFAULT 0,
    line_total NUMERIC(12,2) NOT NULL DEFAULT 0,
    tax_rate NUMERIC(5,2) DEFAULT 0,
    tax_amount NUMERIC(12,2) DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Cuentas por cobrar
CREATE TABLE IF NOT EXISTS lis_accounts_receivable (
    id SERIAL PRIMARY KEY,
    invoice_id INTEGER NOT NULL REFERENCES lis_invoices(id),
    party_type VARCHAR(20) NOT NULL
        CHECK (party_type IN ('patient', 'insurer')),
    party_id VARCHAR(50) NOT NULL,
    party_name VARCHAR(400),
    amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    paid_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    balance NUMERIC(12,2) GENERATED ALWAYS AS (amount - paid_amount) STORED,
    status VARCHAR(20) NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'partial', 'paid', 'written_off')),
    due_date DATE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Asientos contables (libro diario)
CREATE TABLE IF NOT EXISTS lis_journal_entries (
    id SERIAL PRIMARY KEY,
    entry_date DATE NOT NULL,
    invoice_id INTEGER REFERENCES lis_invoices(id),
    description VARCHAR(500),
    account_code VARCHAR(20) NOT NULL,
    debit NUMERIC(12,2) DEFAULT 0,
    credit NUMERIC(12,2) DEFAULT 0,
    branch_code VARCHAR(20),
    fiscal_period_id INTEGER,
    created_by INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Aplicaciones de pago
CREATE TABLE IF NOT EXISTS lis_payment_applications (
    id SERIAL PRIMARY KEY,
    receivable_id INTEGER NOT NULL REFERENCES lis_accounts_receivable(id),
    amount NUMERIC(12,2) NOT NULL,
    payment_source VARCHAR(30)
        CHECK (payment_source IN ('cash', 'pos', 'transfer', 'credit_note')),
    payment_reference VARCHAR(200),
    applied_by INTEGER,
    applied_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_lis_invoices_branch ON lis_invoices(branch_code);
CREATE INDEX IF NOT EXISTS idx_lis_invoices_patient ON lis_invoices(patient_document);
CREATE INDEX IF NOT EXISTS idx_lis_invoices_status ON lis_invoices(status);
CREATE INDEX IF NOT EXISTS idx_lis_invoices_created ON lis_invoices(created_at);
CREATE INDEX IF NOT EXISTS idx_lis_inv_lines_inv ON lis_invoice_lines(invoice_id);
CREATE INDEX IF NOT EXISTS idx_lis_ar_invoice ON lis_accounts_receivable(invoice_id);
CREATE INDEX IF NOT EXISTS idx_lis_ar_party ON lis_accounts_receivable(party_type, party_id);
CREATE INDEX IF NOT EXISTS idx_lis_ar_status ON lis_accounts_receivable(status);
CREATE INDEX IF NOT EXISTS idx_lis_journal_date ON lis_journal_entries(entry_date);
CREATE INDEX IF NOT EXISTS idx_lis_journal_account ON lis_journal_entries(account_code);
CREATE INDEX IF NOT EXISTS idx_lis_journal_invoice ON lis_journal_entries(invoice_id);
CREATE INDEX IF NOT EXISTS idx_lis_journal_period ON lis_journal_entries(fiscal_period_id);
CREATE INDEX IF NOT EXISTS idx_lis_payment_app_recv ON lis_payment_applications(receivable_id);
