"""APScheduler cron jobs for automated pipeline operations.

Wires up recurring tasks so the service operates autonomously:
- Reply monitoring (every 2h weekdays)
- Warmup sessions (daily morning)
- Daily reports to Slack + Telegram (6 PM)
- Deliverability health check (daily)
- Weekly summary (Friday)
- Traffic pull + digest (daily 7 AM)
"""
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

log = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="America/New_York")


# --- JOB FUNCTIONS ---

async def job_check_replies():
    """Scan Gmail for replies to outreach emails."""
    log.info("[cron] check-replies: starting")
    try:
        from app.sender.gmail import is_authenticated, check_replies_for_leads
        from app.shared.db import db
        from app.shared.notify import notify
        from app.shared.slack_reports import report_reply_monitor
        import json

        for account in ("benny", "george"):
            if not await is_authenticated(account):
                log.warning("[cron] check-replies: %s not authenticated, skipping", account)
                continue

            replies = await check_replies_for_leads(account)
            matched = 0
            new_replies = []

            async with db() as conn:
                for reply in replies:
                    from_str = reply["from"]
                    email_match = from_str.split("<")[-1].rstrip(">") if "<" in from_str else from_str
                    email_match = email_match.strip()

                    lead = await conn.fetchrow(
                        "SELECT id, company_name, contact_name, score FROM leads WHERE email = $1 AND status != 'replied'",
                        email_match,
                    )
                    if not lead:
                        continue

                    matched += 1
                    await conn.execute("UPDATE leads SET status = 'replied', updated_at = NOW() WHERE id = $1", lead["id"])
                    await conn.execute(
                        "UPDATE outreach_emails SET status = 'replied', replied_at = NOW() WHERE lead_id = $1 AND status = 'sent'",
                        lead["id"],
                    )
                    await conn.execute(
                        "INSERT INTO pipeline_events (lead_id, event_type, agent, metadata) VALUES ($1, 'replied', 'sender', $2::jsonb)",
                        lead["id"], json.dumps({"source": "gmail_monitor", "account": account, "snippet": reply["snippet"][:200]}),
                    )
                    new_replies.append({
                        "lead_id": lead["id"], "company": lead["company_name"],
                        "contact": lead["contact_name"], "snippet": reply["snippet"][:200],
                    })

            if new_replies:
                names = ", ".join(r["company"] for r in new_replies[:5])
                await notify(f"*Reply Monitor ({account}) found {matched} new replies*\nCompanies: {names}")

            await report_reply_monitor(len(replies), matched, new_replies)
            log.info("[cron] check-replies (%s): checked=%d, matched=%d", account, len(replies), matched)

    except Exception as e:
        log.error("[cron] check-replies failed: %s", e, exc_info=True)


async def job_warmup():
    """Run inbox warmup session (maintenance mode)."""
    log.info("[cron] warmup: starting")
    try:
        from app.sender.warmup import run_full_warmup
        from app.shared.notify import send_telegram
        # Week 5+ = maintenance: 1 inter-account + 1 warm contact
        result = await run_full_warmup(week=5)
        total = result.get("total_sent", 0)
        log.info("[cron] warmup: sent %d emails", total)
        if total > 0:
            await send_telegram(f"*Warmup Session (auto)*\nTotal sent: {total}")
    except Exception as e:
        log.error("[cron] warmup failed: %s", e, exc_info=True)


async def job_daily_reports():
    """Send daily pipeline digest + deliverability report to Slack."""
    log.info("[cron] daily-reports: starting")
    try:
        from app.shared.slack_reports import report_daily_digest, report_daily_deliverability
        digest = await report_daily_digest()
        await report_daily_deliverability()
        log.info("[cron] daily-reports: digest sent (sent_today=%s)", digest.get("sent_today", 0))

        # Also send Telegram daily report
        from app.shared.db import db
        from app.shared.notify import daily_report
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
        await daily_report({**dict(stats), **dict(email_stats)})
    except Exception as e:
        log.error("[cron] daily-reports failed: %s", e, exc_info=True)


async def job_weekly_summary():
    """Send weekly performance summary to Slack."""
    log.info("[cron] weekly-summary: starting")
    try:
        from app.shared.slack_reports import report_weekly_summary
        result = await report_weekly_summary()
        log.info("[cron] weekly-summary: sent (reply_rate=%.1f%%)", result.get("reply_rate", 0))
    except Exception as e:
        log.error("[cron] weekly-summary failed: %s", e, exc_info=True)


async def job_traffic_digest():
    """Pull traffic data from all sources and send digest."""
    log.info("[cron] traffic-digest: starting")
    try:
        from app.traffic.search_console import pull_all_domains as pull_gsc
        from app.traffic.analytics import pull_all_domains as pull_ga4
        from app.traffic.cloudflare import pull_all_domains as pull_cf
        from app.traffic.digest import build_digest, format_telegram, format_slack_blocks
        from app.shared.notify import send_telegram
        from app.shared.slack_reports import _post_slack, DASHBOARD_CHANNEL

        gsc = await pull_gsc()
        ga4 = await pull_ga4()
        cf = await pull_cf()

        digest = await build_digest()

        telegram_text = format_telegram(digest)
        await send_telegram(telegram_text)

        blocks = format_slack_blocks(digest)
        await _post_slack(DASHBOARD_CHANNEL, f"Traffic Digest - {digest['date']}", blocks)

        log.info("[cron] traffic-digest: sent for %s", digest.get("date", "unknown"))
    except Exception as e:
        log.error("[cron] traffic-digest failed: %s", e, exc_info=True)


# --- SCHEDULER SETUP ---

def start_scheduler():
    """Register all cron jobs and start the scheduler."""

    # Reply monitoring - every 2 hours on weekdays (8am-8pm ET)
    scheduler.add_job(job_check_replies, CronTrigger(hour="8,10,12,14,16,18,20", day_of_week="mon-fri"),
                      id="check-replies", name="Check Gmail Replies", replace_existing=True)

    # Warmup - daily at 9:30 AM ET (offset from other jobs)
    scheduler.add_job(job_warmup, CronTrigger(hour=9, minute=30, day_of_week="mon-fri"),
                      id="warmup", name="Inbox Warmup", replace_existing=True)

    # Daily reports - 6 PM ET weekdays
    scheduler.add_job(job_daily_reports, CronTrigger(hour=18, minute=0, day_of_week="mon-fri"),
                      id="daily-reports", name="Daily Pipeline and Deliverability Reports", replace_existing=True)

    # Weekly summary - Friday 5 PM ET
    scheduler.add_job(job_weekly_summary, CronTrigger(day_of_week="fri", hour=17, minute=0),
                      id="weekly-summary", name="Weekly Performance Summary", replace_existing=True)

    # Traffic digest - daily 7 AM ET
    scheduler.add_job(job_traffic_digest, CronTrigger(hour=7, minute=0),
                      id="traffic-digest", name="Traffic Pull and Digest", replace_existing=True)

    scheduler.start()
    log.info("[scheduler] Started with %d jobs", len(scheduler.get_jobs()))
    for job in scheduler.get_jobs():
        log.info("[scheduler]   %s: %s", job.id, job.trigger)


def stop_scheduler():
    """Gracefully shut down the scheduler."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        log.info("[scheduler] Stopped")
