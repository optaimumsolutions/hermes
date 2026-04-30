-- Traffic monitoring tables for daily snapshots and trend computation

-- Daily traffic snapshots from each source
CREATE TABLE IF NOT EXISTS traffic_daily (
    id SERIAL PRIMARY KEY,
    domain TEXT NOT NULL,           -- 'optaimum.com' or 'catchflow.org'
    date DATE NOT NULL,
    source TEXT NOT NULL,           -- 'gsc', 'ga4', 'cloudflare'
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(domain, date, source)
);

-- AI bot crawl tracking (GEO leading indicator)
CREATE TABLE IF NOT EXISTS bot_hits_daily (
    id SERIAL PRIMARY KEY,
    domain TEXT NOT NULL,
    date DATE NOT NULL,
    bot_name TEXT NOT NULL,         -- 'GPTBot', 'ClaudeBot', 'PerplexityBot', etc.
    hit_count INTEGER DEFAULT 0,
    top_pages JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(domain, date, bot_name)
);

-- Traffic alerts log
CREATE TABLE IF NOT EXISTS traffic_alerts (
    id SERIAL PRIMARY KEY,
    domain TEXT NOT NULL,
    alert_type TEXT NOT NULL,       -- 'position_drop', 'traffic_spike', 'traffic_drop', 'bot_surge', 'source_divergence'
    severity TEXT DEFAULT 'info',   -- 'info', 'warning', 'critical'
    message TEXT NOT NULL,
    metrics JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_traffic_daily_domain_date ON traffic_daily(domain, date DESC);
CREATE INDEX IF NOT EXISTS idx_traffic_daily_source ON traffic_daily(source);
CREATE INDEX IF NOT EXISTS idx_bot_hits_domain_date ON bot_hits_daily(domain, date DESC);
CREATE INDEX IF NOT EXISTS idx_traffic_alerts_domain ON traffic_alerts(domain, created_at DESC);
