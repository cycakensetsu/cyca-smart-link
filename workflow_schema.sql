-- REVIEW BEFORE APPLYING. Never run automatically at application startup.
-- New private schema only. Does not modify existing Supabase storage buckets.
-- Connect as an authorized schema owner. Select an existing, dedicated runtime
-- DB role separately and grant only USAGE, SELECT, INSERT, UPDATE to that role.
-- Do not grant to PUBLIC, anon, authenticated, or expose via PostgREST schemas.
BEGIN;
CREATE SCHEMA IF NOT EXISTS cyca_workflow;
REVOKE ALL ON SCHEMA cyca_workflow FROM PUBLIC;

CREATE TABLE IF NOT EXISTS cyca_workflow.schema_version (
    version INTEGER PRIMARY KEY CHECK (version = 1)
);
INSERT INTO cyca_workflow.schema_version(version) VALUES (1) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS cyca_workflow.deliveries (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL, -- authenticated company scope, NOT employee ID
    request_key TEXT NOT NULL,
    payload TEXT NOT NULL CHECK (jsonb_typeof(payload::jsonb) = 'object'),
    pdf BYTEA NOT NULL,
    fingerprint TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('review','approved','sending','unknown','sent','cancelled')),
    actor TEXT, -- authenticated employee who approved
    approved_at TEXT,
    gmail_id TEXT,
    thread_id TEXT,
    error TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(owner, request_key),
    CHECK (payload::jsonb ?& ARRAY['recipient','document_id','pdf_sha256']),
    CHECK (length(payload::jsonb->>'recipient') > 0),
    CHECK (length(payload::jsonb->>'document_id') > 0),
    CHECK ((payload::jsonb->>'pdf_sha256') ~ '^[a-f0-9]{64}$')
);
-- A fresh request UUID must not bypass the business action identity.
CREATE UNIQUE INDEX IF NOT EXISTS delivery_active_document
 ON cyca_workflow.deliveries(owner, lower(payload::jsonb->>'recipient'), (payload::jsonb->>'document_id'))
 WHERE state <> 'cancelled';
CREATE UNIQUE INDEX IF NOT EXISTS delivery_active_pdf
 ON cyca_workflow.deliveries(owner, lower(payload::jsonb->>'recipient'), (payload::jsonb->>'pdf_sha256'))
 WHERE state <> 'cancelled';
CREATE INDEX IF NOT EXISTS delivery_history ON cyca_workflow.deliveries(owner, updated_at DESC);

CREATE TABLE IF NOT EXISTS cyca_workflow.invoices (
    owner TEXT NOT NULL, -- company scope
    number TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    payload TEXT NOT NULL CHECK (jsonb_typeof(payload::jsonb) = 'object'),
    pdf BYTEA NOT NULL,
    actor TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    PRIMARY KEY(owner, number)
);
CREATE INDEX IF NOT EXISTS invoice_fingerprint ON cyca_workflow.invoices(owner,fingerprint);
REVOKE ALL ON ALL TABLES IN SCHEMA cyca_workflow FROM PUBLIC;
-- Supabase browser roles may already exist; explicit grants must not survive.
DO $$
DECLARE browser_role TEXT;
BEGIN
    FOREACH browser_role IN ARRAY ARRAY['anon','authenticated'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = browser_role) THEN
            EXECUTE format('REVOKE ALL ON SCHEMA cyca_workflow FROM %I', browser_role);
            EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA cyca_workflow FROM %I', browser_role);
        END IF;
    END LOOP;
END $$;

-- No browser/PostgREST user may access these server-only records. Dedicated
-- backend connection role needs explicit privileges and a backend RLS policy.
ALTER TABLE cyca_workflow.deliveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE cyca_workflow.invoices ENABLE ROW LEVEL SECURITY;
ALTER TABLE cyca_workflow.schema_version ENABLE ROW LEVEL SECURITY;
-- Deliberately no permissive policy / runtime role / grant is created here.
-- A schema owner can run acceptance tests. Production role/policies must be
-- reviewed separately; do not use a broad postgres admin password in the app.
COMMIT;
