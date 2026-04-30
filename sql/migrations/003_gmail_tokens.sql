-- Store Gmail OAuth tokens in the database (survives container rebuilds)
CREATE TABLE IF NOT EXISTS gmail_tokens (
    account TEXT PRIMARY KEY,
    tokens_json JSONB NOT NULL,
    verified_email TEXT DEFAULT '',
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
