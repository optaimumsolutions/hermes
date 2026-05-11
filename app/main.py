import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Query
from fastapi.responses import RedirectResponse
from app.shared.db import get_pool, close_pool
from app.shared.notify import daily_report
from app.scout.routes import router as scout_router
from app.sender.routes import router as sender_router
from app.traffic.routes import router as traffic_router
from app.traffic.discovery import router as discovery_router
from app.sender.gmail import get_auth_url, exchange_code, is_authenticated, list_accounts
from app.scheduler import start_scheduler, stop_scheduler

# Configure logging so output reaches stdout (and Render's log collector)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await get_pool()
    # Run migrations on startup (skip if tables exist)
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name='pipeline_events')"
        )
        if not exists:
            with open("sql/migrations/001_leads_and_campaigns.sql") as f:
                await conn.execute(f.read())
        # Run deliverability migration (idempotent - uses IF NOT EXISTS)
        with open("sql/migrations/002_deliverability.sql") as f:
            await conn.execute(f.read())
        # Run gmail tokens migration (idempotent - uses IF NOT EXISTS)
        with open("sql/migrations/003_gmail_tokens.sql") as f:
            await conn.execute(f.read())
        # Run traffic monitor migration (idempotent - uses IF NOT EXISTS)
        with open("sql/migrations/004_traffic.sql") as f:
            await conn.execute(f.read())
    log.info("Database migrations complete")

    # Start the cron scheduler
    start_scheduler()
    log.info("Application startup complete")

    yield

    stop_scheduler()
    await close_pool()
    log.info("Application shutdown complete")


app = FastAPI(
    title="Outreach System",
    description="Multi-agent outreach: Scout (lead discovery) + Sender (email execution)",
    version="1.1.0",
    lifespan=lifespan,
)

app.include_router(scout_router)
app.include_router(sender_router)
app.include_router(traffic_router)
app.include_router(discovery_router)


@app.get("/slack/channels")
async def slack_channels():
    """List Slack channels the bot can see."""
    import httpx
    from app.shared.config import get_settings
    s = get_settings()
    if not s.slack_bot_token:
        return {"error": "SLACK_BOT_TOKEN not configured"}
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://slack.com/api/conversations.list",
            headers={"Authorization": f"Bearer {s.slack_bot_token}"},
            params={"types": "public_channel", "limit": 50},
        )
        data = resp.json()
    if not data.get("ok"):
        return {"error": data.get("error")}
    return {
        "channels": [
            {"id": ch["id"], "name": ch["name"], "is_member": ch.get("is_member", False)}
            for ch in data.get("channels", [])
        ]
    }


@app.post("/slack/test")
async def slack_test():
    """Send a test message to the configured Slack channel."""
    from app.shared.notify import send_slack
    await send_slack(
        "Outreach System connected. Hermes reporting for duty.",
        blocks=[
            {"type": "header", "text": {"type": "plain_text", "text": "Outreach System Online"}},
            {"type": "divider"},
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": "*Gmail*\nbenny + george authenticated"},
                {"type": "mrkdwn", "text": "*Deliverability*\nGuard active, bounce rate 0%"},
                {"type": "mrkdwn", "text": "*Pipeline*\n600+ seafood leads ready"},
                {"type": "mrkdwn", "text": "*Status*\nAll systems operational"},
            ]},
        ],
    )
    return {"status": "sent"}


@app.get("/health")
async def health():
    from app.shared.db import db
    from app.scheduler import scheduler
    async with db() as conn:
        result = await conn.fetchval("SELECT 1")
    gmail_status = await is_authenticated()
    jobs = [{"id": j.id, "next_run": str(j.next_run_time)} for j in scheduler.get_jobs()]
    return {
        "status": "ok",
        "db": result == 1,
        "gmail": gmail_status,
        "scheduler": {"running": scheduler.running, "jobs": len(jobs)},
    }


@app.get("/scheduler/jobs")
async def scheduler_jobs():
    """List all scheduled cron jobs and their next run times."""
    from app.scheduler import scheduler
    jobs = []
    for j in scheduler.get_jobs():
        jobs.append({
            "id": j.id,
            "name": j.name,
            "trigger": str(j.trigger),
            "next_run": str(j.next_run_time),
        })
    return {"running": scheduler.running, "jobs": jobs}


@app.get("/auth/accounts")
async def auth_accounts():
    """List all Gmail accounts and their authentication status."""
    accounts = list_accounts()
    for acct in accounts:
        acct["authenticated"] = await is_authenticated(acct["account"])
    return {"accounts": accounts}


@app.get("/auth/google/callback")
async def google_callback(code: str = Query(...), state: str = Query("benny")):
    """Handle OAuth callback and store tokens under the correct account."""
    tokens = await exchange_code(code, account=state)
    return {
        "status": "authenticated",
        "account": state,
        "email": tokens.get("verified_email", ""),
        "has_refresh_token": "refresh_token" in tokens,
        "message": f"Gmail account '{state}' connected.",
    }


@app.get("/auth/google/{account}")
async def google_auth(account: str):
    """Redirect to Google OAuth consent screen for a specific account.

    Available accounts: benny, george
    """
    return RedirectResponse(get_auth_url(account))


@app.get("/pipeline/stats")
async def pipeline_stats():
    """Full pipeline overview combining Scout + Sender stats."""
    from app.shared.db import db
    async with db() as conn:
        lead_stats = await conn.fetchrow("""
            SELECT
                COUNT(*) as total_leads,
                COUNT(*) FILTER (WHERE status = 'new') as new,
                COUNT(*) FILTER (WHERE status = 'qualified') as qualified,
                COUNT(*) FILTER (WHERE status = 'sent') as sent,
                COUNT(*) FILTER (WHERE status = 'replied') as replied,
                COUNT(*) FILTER (WHERE status = 'converted') as converted,
                ROUND(AVG(score)::numeric, 1) as avg_score
            FROM leads
        """)
        email_stats = await conn.fetchrow("""
            SELECT
                COUNT(*) as total_emails,
                COUNT(*) FILTER (WHERE status = 'replied') as replies,
                CASE WHEN COUNT(*) > 0
                    THEN ROUND(100.0 * COUNT(*) FILTER (WHERE status = 'replied') / COUNT(*), 1)
                    ELSE 0 END as reply_rate
            FROM outreach_emails
        """)
        recent_events = await conn.fetch("""
            SELECT event_type, agent, COUNT(*) as count
            FROM pipeline_events
            WHERE created_at > NOW() - INTERVAL '24 hours'
            GROUP BY event_type, agent
            ORDER BY count DESC
        """)
    return {
        "leads": dict(lead_stats),
        "emails": dict(email_stats),
        "last_24h": [dict(e) for e in recent_events],
    }


@app.post("/reports/daily")
async def slack_daily_reports():
    """Trigger daily reports to Slack (pipeline digest + deliverability)."""
    from app.shared.slack_reports import report_daily_digest, report_daily_deliverability
    digest = await report_daily_digest()
    deliverability = await report_daily_deliverability()
    return {"digest": digest, "deliverability": deliverability}


@app.post("/reports/weekly")
async def slack_weekly_report():
    """Trigger weekly performance summary to Slack."""
    from app.shared.slack_reports import report_weekly_summary
    result = await report_weekly_summary()
    return result


@app.post("/pipeline/daily-report")
async def trigger_daily_report():
    """Trigger a daily report to Telegram."""
    from app.shared.db import db
    async with db() as conn:
        stats = await conn.fetchrow("""
            SELECT
                COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours') as discovered,
                COUNT(*) FILTER (WHERE status = 'qualified') as qualified,
                COUNT(*) FILTER (WHERE status = 'replied') as replied,
                COUNT(*) FILTER (WHERE status = 'converted') as converted,
                COUNT(*) FILTER (WHERE status IN ('new','enriched','qualified','sent')) as total_active
            FROM leads
        """)
        email_stats = await conn.fetchrow("""
            SELECT
                COUNT(*) FILTER (WHERE sent_at > NOW() - INTERVAL '24 hours') as sent,
                COUNT(*) FILTER (WHERE opened_at > NOW() - INTERVAL '24 hours') as opened,
                COUNT(*) FILTER (WHERE replied_at > NOW() - INTERVAL '24 hours') as replied
            FROM outreach_emails
        """)
    report = {**dict(stats), **dict(email_stats)}
    await daily_report(report)
    return report
