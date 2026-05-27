-- Migration 009: Add super_admin role
-- Adds 'super_admin' to the role enum in lis_users.
-- super_admin users have full access and cannot be deleted.

ALTER TABLE lis_users DROP CONSTRAINT IF EXISTS lis_users_role_check;
ALTER TABLE lis_users ADD CONSTRAINT lis_users_role_check
    CHECK (role IN ('super_admin', 'admin', 'recepcion', 'laboratorista', 'bioquimico', 'contador'));
