-- Supabase SQL Migration
-- Creates a standalone users table with manual authentication fields

-- ==============================================================================
-- FOR EXISTING DATABASES (Run this in Supabase SQL Editor if getting PGRST204):
-- ==============================================================================
ALTER TABLE users ADD COLUMN IF NOT EXISTS joining_date TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS intern_id TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS phone TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token_expires_at TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS deactivation_reason TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS session_token TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS college_name TEXT;

-- Reload PostgREST schema cache immediately
NOTIFY pgrst, 'reload schema';


-- ==============================================================================
-- FOR NEW SETUP (Drop and recreate):
-- ==============================================================================
DROP TABLE IF EXISTS profiles CASCADE;
DROP TABLE IF EXISTS users CASCADE;

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'manager', 'intern')),
    department TEXT,
    manager_id UUID REFERENCES users(id) ON DELETE SET NULL,
    internship_duration_months INTEGER,
    joining_date TEXT,
    leave_allotted_days INTEGER,
    status TEXT NOT NULL DEFAULT 'active',
    intern_id TEXT,
    phone TEXT,
    reset_token TEXT,
    reset_token_expires_at TEXT,
    deactivation_reason TEXT,
    session_token TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

-- Note: We intentionally DO NOT enable Row Level Security (RLS) on the users table.
-- Security is managed exclusively by the Flask backend which acts as the 'admin' 
-- holding the service role key with full access.
