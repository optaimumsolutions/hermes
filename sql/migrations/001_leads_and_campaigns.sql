-- Multi-agent outreach system schema

-- Leads discovered by Scout
CREATE TABLE IF NOT EXISTS leads (
    id SERIAL PRIMARY KEY,
    company_name TEXT NOT NULL,
    domain TEXT,
    contact_name TEXT,
    contact_title TEXT,
    email TEXT,
    email_verified BOOLEAN DEFAULT FALSE,
    source TEXT NOT NULL,  -- 'linkedin', 'google_maps', 'manual', 'import'
    industry TEXT,
    employee_count INTEGER,
    score INTEGER DEFAULT 0 CHECK (score BETWEEN 0 AND 10),
    signals JSONB DEFAULT '[]'::jsonb,
    status TEXT DEFAULT 'new' CHECK (status IN ('new','enriched','qualified','sent','replied','converted','disqualified')),
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(email)
);

-- Outreach campaigns managed by Sender
CREATE TABLE IF NOT EXISTS campaigns (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT DEFAULT 'draft' CHECK (status IN ('draft','active','paused','completed')),
    target_criteria JSONB DEFAULT '{}'::jsonb,
    sequence JSONB DEFAULT '[]'::jsonb,  -- array of {step, delay_days, subject, body_template}
    stats JSONB DEFAULT '{"sent":0,"opened":0,"replied":0,"bounced":0}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Individual emails sent
CREATE TABLE IF NOT EXISTS outreach_emails (
    id SERIAL PRIMARY KEY,
    lead_id INTEGER NOT NULL REFERENCES leads(id),
    campaign_id INTEGER REFERENCES campaigns(id),
    step INTEGER DEFAULT 1,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    personalization JSONB DEFAULT '{}'::jsonb,
    status TEXT DEFAULT 'queued' CHECK (status IN ('queued','sent','opened','replied','bounced','failed')),
    sent_at TIMESTAMPTZ,
    opened_at TIMESTAMPTZ,
    replied_at TIMESTAMPTZ,
    reply_text TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Pipeline events for tracking full journey
CREATE TABLE IF NOT EXISTS pipeline_events (
    id SERIAL PRIMARY KEY,
    lead_id INTEGER NOT NULL REFERENCES leads(id),
    event_type TEXT NOT NULL,  -- 'discovered','enriched','scored','emailed','opened','replied','meeting_booked','converted'
    agent TEXT NOT NULL,       -- 'scout' or 'sender'
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_score ON leads(score DESC);
CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(email);
CREATE INDEX IF NOT EXISTS idx_outreach_lead ON outreach_emails(lead_id);
CREATE INDEX IF NOT EXISTS idx_outreach_status ON outreach_emails(status);
CREATE INDEX IF NOT EXISTS idx_pipeline_lead ON pipeline_events(lead_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_type ON pipeline_events(event_type);
