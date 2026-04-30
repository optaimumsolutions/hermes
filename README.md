# Hermes Outreach System

Multi-agent outreach pipeline for CatchFlow seafood price intelligence. Scout discovers and qualifies leads, Sender personalizes and delivers emails via Gmail with AI-generated content.

## Architecture

```
Scout Agent          Sender Agent          Shared
-----------          ------------          ------
Hunter.io lookup     Gmail multi-account   Neon PostgreSQL (dual DB)
Upstream DB import   Claude Haiku AI       Telegram notifications
ICP lead scoring     3-step sequences      Slack reports
Email verification   Deliverability guard  Pipeline event tracking
```

## Agents

### Scout (`/scout`)
- `POST /scout/discover` - Find leads at a domain via Hunter.io
- `POST /scout/import/upstream` - Pull leads from upstream DB (supports `leads` and `cf_outreach_leads` tables)
- `POST /scout/add` - Manually add a lead
- `POST /scout/verify/{lead_id}` - Verify a lead's email
- `GET /scout/qualified` - Get leads ready for outreach
- `GET /scout/upstream/preview` - Preview upstream lead data
- `GET /scout/stats` - Pipeline statistics

### Sender (`/sender`)
- `POST /sender/catchflow/send` - Send personalized CatchFlow emails via Gmail
- `POST /sender/catchflow/preview` - Preview AI-personalized emails without sending
- `POST /sender/send-batch` - Send batch via Instantly.ai
- `POST /sender/campaigns` - Create a campaign
- `POST /sender/reply` - Log an email reply
- `POST /sender/check-replies` - Scan Gmail inbox for replies, auto-update leads
- `POST /sender/deliverability/check` - Pre-screen an email before sending
- `GET /sender/deliverability/status` - Bounce rate, velocity, verification cache
- `GET /sender/campaigns/{id}/stats` - Campaign performance
- `GET /sender/stats` - Overall sender stats

### Pipeline (`/pipeline`)
- `GET /pipeline/stats` - Full pipeline overview
- `POST /pipeline/daily-report` - Trigger daily report to Telegram + Slack

### Auth (`/auth`)
- `GET /auth/google/{account}` - OAuth consent screen (accounts: `benny`, `george`)
- `GET /auth/google/callback` - OAuth callback
- `GET /auth/accounts` - List all Gmail accounts and auth status

### Slack (`/slack`)
- `GET /slack/channels` - List channels the bot can see
- `POST /slack/test` - Send test message to configured channel

## Deliverability Guard

Every email passes through 5 checks before sending:

1. **Spam phrase scanner** - 17 trigger patterns (urgency, money, ALL CAPS, excessive punctuation)
2. **Content quality** - Body length, greeting, link density
3. **Velocity limiter** - 15/hour + 40/day caps enforced per-send
4. **Bounce rate monitor** - Blocks all sends if bounce rate >5%, warns >2%
5. **Email verification** - NeverBounce API + domain-based fallback with 30-day cache

## Anti-Spam Measures

- Plain text only (no HTML)
- `List-Unsubscribe` and `List-Unsubscribe-Post` headers
- Proper `From`, `Reply-To`, `Message-ID`, `Date` headers
- `In-Reply-To` / `References` for follow-up threading
- Human-like send pacing (45-120s random delay between emails)
- Exponential backoff on Gmail 429s (1s, 2s, 4s, 8s, 16s + jitter)
- AI-generated unique opening lines per lead (no two emails identical)

## CatchFlow Campaign

3-step email sequence for Florida seafood suppliers:

| Step | Day | Strategy |
|------|-----|----------|
| 1 | 0 | Value-first intro with AI-personalized opener, location-specific pain point |
| 2 | 3 | Social proof testimonials + seasonal urgency |
| 3 | 7 | Breakup - soft close with free profile offer |

All emails signed by Benny Torso, CatchFlow | Seafood Price Intelligence.

## Setup

### Environment Variables

```env
# Database (Neon PostgreSQL)
DATABASE_URL=postgresql://...
LEAD_DATABASE_URL=postgresql://...

# AI Personalization
ANTHROPIC_API_KEY=sk-ant-...

# Telegram
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...

# Slack
SLACK_BOT_TOKEN=xoxb-...
SLACK_CHANNEL_ID=C...
SLACK_APP_TOKEN=xapp-...

# Gmail OAuth
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REDIRECT_URI=https://your-domain.com/auth/google/callback

# Optional
HUNTER_API_KEY=
INSTANTLY_API_KEY=
NEVERBOUNCE_API_KEY=
SCOUT_BATCH_SIZE=25
SENDER_DAILY_LIMIT=50
LEAD_SCORE_THRESHOLD=6
```

### Local Development

```bash
pip install -r requirements.txt
cp .env.example .env  # fill in credentials
uvicorn app.main:app --reload --port 8000
```

### Docker

```bash
docker build -t outreach-system .
docker run -p 8000:8000 --env-file .env outreach-system
```

### Deploy to Render

Uses `render.yaml` blueprint. Connect the repo and set env vars in the Render dashboard.

### Gmail Authentication

After deploying, authenticate both Gmail accounts:

1. Visit `https://your-domain.com/auth/google/benny`
2. Visit `https://your-domain.com/auth/google/george`
3. Verify at `https://your-domain.com/auth/accounts`

## Cron Jobs

Recommended schedule (via Hermes or external scheduler):

| Job | Schedule | Endpoint |
|-----|----------|----------|
| Daily report | 9:00 AM | `POST /pipeline/daily-report` |
| Scout discovery | Every 4h | `POST /scout/import/upstream` |
| Send batch (step 1) | 10:00 AM | `POST /sender/catchflow/send?step=1&limit=10` |
| Send batch (step 2) | 2:00 PM | `POST /sender/catchflow/send?step=2&limit=10` |
| Reply monitor | Every 2h | `POST /sender/check-replies` |

## Database

Two PostgreSQL databases (Neon):

- **Outreach DB** (`DATABASE_URL`) - leads, campaigns, outreach_emails, pipeline_events, email_verification_cache, bounce_events
- **Upstream DB** (`LEAD_DATABASE_URL`) - cf_outreach_leads (600+ Florida seafood suppliers), leads (agency/SaaS)

Migrations run automatically on startup.
