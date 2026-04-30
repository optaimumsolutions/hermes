-- Email verification cache (avoid re-verifying the same address)
CREATE TABLE IF NOT EXISTS email_verification_cache (
    email TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'unknown',  -- valid, invalid, risky, unknown
    checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Bounce event log (track every bounce for rate calculations)
CREATE TABLE IF NOT EXISTS bounce_events (
    id SERIAL PRIMARY KEY,
    email TEXT NOT NULL,
    lead_id INT REFERENCES leads(id),
    reason TEXT DEFAULT '',
    bounce_type TEXT DEFAULT 'hard',  -- hard, soft
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_bounce_events_created ON bounce_events(created_at);
CREATE INDEX IF NOT EXISTS idx_bounce_events_email ON bounce_events(email);

-- Add bounce tracking columns to outreach_emails if not present
DO $$ BEGIN
    ALTER TABLE outreach_emails ADD COLUMN IF NOT EXISTS bounced_at TIMESTAMPTZ;
    ALTER TABLE outreach_emails ADD COLUMN IF NOT EXISTS bounce_reason TEXT DEFAULT '';
    ALTER TABLE outreach_emails ADD COLUMN IF NOT EXISTS gmail_msg_id TEXT DEFAULT '';
    ALTER TABLE outreach_emails ADD COLUMN IF NOT EXISTS gmail_thread_id TEXT DEFAULT '';
    ALTER TABLE outreach_emails ADD COLUMN IF NOT EXISTS deliverability_score FLOAT DEFAULT 0;
EXCEPTION WHEN others THEN NULL;
END $$;
