-- Migration: Add Trustlink Extraction Audit Tables
-- Date: 2026-03-20
-- Purpose: Track all extraction runs and step-level metrics for Trustlink account extraction

-- Table: trustlink_runs
-- Stores metadata for each extraction run (scheduled or manual)
CREATE TABLE IF NOT EXISTS trustlink_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_date DATE UNIQUE NOT NULL,
    run_type TEXT NOT NULL CHECK (run_type IN ('scheduled', 'manual')),
    triggered_by TEXT,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'success', 'failed', 'duplicate')),
    
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    
    file_path TEXT,
    file_hash TEXT,
    integrity_report_path TEXT,
    
    total_rows INT DEFAULT 0,
    idc_rows INT DEFAULT 0,
    digipay_rows INT DEFAULT 0,
    
    extract_duration_ms INT DEFAULT 0,
    transform_duration_ms INT DEFAULT 0,
    validation_duration_ms INT DEFAULT 0,
    total_duration_ms INT DEFAULT 0,
    
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create index for faster date lookups (critical for daily checks)
CREATE INDEX IF NOT EXISTS idx_trustlink_runs_run_date ON trustlink_runs(run_date);
CREATE INDEX IF NOT EXISTS idx_trustlink_runs_status ON trustlink_runs(status);

-- Table: trustlink_steps
-- Stores granular metrics for each processing step within a run
CREATE TABLE IF NOT EXISTS trustlink_steps (
    id SERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES trustlink_runs(id) ON DELETE CASCADE,
    
    step_name TEXT NOT NULL CHECK (step_name IN (
        'IDC_EXTRACTION',
        'DIGIPAY_EXTRACTION',
        'TRANSFORMATION',
        'VALIDATION',
        'FILE_SAVE'
    )),
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    
    row_count INT DEFAULT 0,
    duration_ms INT DEFAULT 0,
    metadata JSONB DEFAULT '{}'::jsonb,
    
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create composite index for efficient run+step queries
CREATE INDEX IF NOT EXISTS idx_trustlink_steps_run_id ON trustlink_steps(run_id);
CREATE INDEX IF NOT EXISTS idx_trustlink_steps_status ON trustlink_steps(status);

-- Add comments for documentation
COMMENT ON TABLE trustlink_runs IS 'Tracks all Trustlink extraction runs for audit and monitoring';
COMMENT ON COLUMN trustlink_runs.run_type IS 'scheduled (automated daily) or manual (user-triggered)';
COMMENT ON COLUMN trustlink_runs.status IS 'Current state of extraction run';
COMMENT ON TABLE trustlink_steps IS 'Granular step-level metrics for each extraction run';
COMMENT ON COLUMN trustlink_steps.metadata IS 'JSON field for step-specific metrics (validation issues, sample data, etc.)';