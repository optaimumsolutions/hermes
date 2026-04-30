"""Slack reporting system with rich Block Kit formatting.

Two channels, seven report types:
  #optaimum-dashboard  - executive overview (daily/weekly digests)
  #claw-company-agent  - real-time campaign ops (sends, replies, alerts)
"""
import logging
import httpx
from datetime import datetime, timezone
from .config import get_settings
from .db import db

log = logging.getLogger(__name__)

# Channel routing
DASHBOARD_CHANNEL = "C0AN7QGQH54"   # #optaimum-dashboard
OPS_CHANNEL = "C0ANA4MDKC2"          # #claw-company-agent


async def _post_slack(channel: str, text: str, blocks: list = None):
    """Post to a specific Slack channel."""
    s = get_settings()
    if not s.slack_bot_token:
        return
    payload = {"channel": channel, "text": text}
    if blocks:
        payload["blocks"] = blocks
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://slack.com/api/chat.postMessage",
                headers={
                    "Authorization": f"Bearer {s.slack_bot_token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                json=payload,
            )
            data = resp.json()
            if not data.get("ok"):
                log.warning(f"Slack post to {channel} failed: {data.get('error')}")
    except Exception as e:
        log.warning(f"Slack post failed: {e}")


def _header(text: str) -> dict:
    return {"type": "header", "text": {"type": "plain_text", "text": text[:150]}}


def _divider() -> dict:
    return {"type": "divider"}


def _fields(pairs: list[tuple[str, str]]) -> dict:
    return {
        "type": "section",
        "fields": [{"type": "mrkdwn", "text": f"*{k}*\n{v}"} for k, v in pairs],
    }


def _text(msg: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": msg}}


def _context(msg: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": msg}]}


def _delta(current: int, previous: int) -> str:
    """Format a delta indicator."""
    if previous == 0:
        return f"{current}"
    diff = current - previous
    if diff > 0:
        return f"{current} (+{diff})"
    elif diff < 0:
        return f"{current} ({diff})"
    return f"{current} (=)"


def _health_emoji(status: str) -> str:
    if status == "good":
        return "Large green circle"
    elif status == "warning":
        return "Warning"
    return "Red circle"


# ─── REPORT 1: Campaign Send Report (real-time → #claw-company-agent) ────

async def report_campaign_send(step: int, sent: int, failed: int, blocked: int,
                                account: str, from_addr: str, results: list):
    """Posted after every /catchflow/send batch."""
    status = "Completed" if failed == 0 and blocked == 0 else "Completed with issues"

    blocks = [
        _header(f"Campaign Send - Step {step}"),
        _fields([
            ("Status", status),
            ("From", from_addr),
            ("Sent", str(sent)),
            ("Failed", str(failed)),
        ]),
    ]

    if blocked > 0:
        blocks.append(_text(f"*{blocked} emails blocked by deliverability guard*"))

    # Show top 5 results
    if results:
        lines = []
        for r in results[:5]:
            if r.get("sent"):
                lines.append(f"Sent to {r.get('company', 'Unknown')} ({r.get('email', '')})")
            elif r.get("blocked_reason"):
                lines.append(f"Blocked: {r.get('company', 'Unknown')} - {r['blocked_reason'][0]}")
            elif r.get("skipped_reason"):
                lines.append(f"Skipped: {r.get('company', 'Unknown')} - already replied")
        if lines:
            blocks.append(_text("\n".join(lines)))

    blocks.append(_context(f"Account: {account} | {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"))

    await _post_slack(OPS_CHANNEL, f"Campaign Step {step}: {sent} sent, {failed} failed", blocks)


# ─── REPORT 2: Hot Lead Alert (real-time → both channels) ────────────────

async def report_hot_lead(lead: dict):
    """Instant alert when a positive reply is detected."""
    blocks = [
        _header("HOT LEAD - Positive Reply"),
        _fields([
            ("Contact", f"{lead.get('contact_name', 'Unknown')} at {lead.get('company_name', 'Unknown')}"),
            ("Email", lead.get("email", "N/A")),
            ("Score", f"{lead.get('score', 0)}/10"),
            ("Reply", lead.get("reply_text", "")[:300]),
        ]),
        _text("*Respond ASAP - this lead is warm.*"),
        _context(datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')),
    ]

    text = f"HOT LEAD: {lead.get('contact_name', 'Unknown')} at {lead.get('company_name', 'Unknown')} replied positively"
    await _post_slack(OPS_CHANNEL, text, blocks)
    await _post_slack(DASHBOARD_CHANNEL, text, blocks)


# ─── REPORT 3: Deliverability Warning (real-time → #claw-company-agent) ──

async def report_deliverability_warning(bounce_rate: float, total_bounces: int,
                                         hourly_sent: int, daily_sent: int):
    """Fires when bounce rate >2% or approaching velocity limits."""
    warnings = []
    if bounce_rate > 0.05:
        warnings.append(f"CRITICAL: Bounce rate {bounce_rate:.1%} exceeds 5%. ALL SENDS PAUSED.")
    elif bounce_rate > 0.02:
        warnings.append(f"WARNING: Bounce rate {bounce_rate:.1%} approaching danger zone (>2%)")

    if daily_sent >= 35:
        warnings.append(f"Daily send volume high: {daily_sent}/40")
    if hourly_sent >= 12:
        warnings.append(f"Hourly send volume high: {hourly_sent}/15")

    if not warnings:
        return

    blocks = [
        _header("Deliverability Alert"),
        _text("\n".join(f"- {w}" for w in warnings)),
        _fields([
            ("Bounce Rate", f"{bounce_rate:.2%}"),
            ("Bounces (7d)", str(total_bounces)),
            ("Hourly", f"{hourly_sent}/15"),
            ("Daily", f"{daily_sent}/40"),
        ]),
        _context(datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')),
    ]

    await _post_slack(OPS_CHANNEL, f"Deliverability Alert: {warnings[0]}", blocks)


# ─── REPORT 4: Reply Monitor Digest (real-time → #claw-company-agent) ────

async def report_reply_monitor(checked: int, matched: int, new_replies: list):
    """Posted after every /check-replies run."""
    if matched == 0 and checked == 0:
        return  # Don't spam with empty scans

    blocks = [
        _header("Reply Monitor Scan"),
        _fields([
            ("Inbox Messages Checked", str(checked)),
            ("New Replies Matched", str(matched)),
        ]),
    ]

    if new_replies:
        lines = [f"- *{r['company']}* ({r['contact']}): {r['snippet'][:100]}" for r in new_replies[:5]]
        blocks.append(_text("\n".join(lines)))

    blocks.append(_context(datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')))

    await _post_slack(OPS_CHANNEL, f"Reply Monitor: {matched} new replies from {checked} checked", blocks)


# ─── REPORT 5: Daily Pipeline Digest (scheduled → #optaimum-dashboard) ───

async def report_daily_digest():
    """Full daily pipeline overview with deltas vs yesterday."""
    async with db() as conn:
        # Today's stats
        today = await conn.fetchrow("""
            SELECT
                COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours') as discovered_today,
                COUNT(*) FILTER (WHERE status = 'qualified') as qualified,
                COUNT(*) FILTER (WHERE status = 'sent') as sent,
                COUNT(*) FILTER (WHERE status = 'replied') as replied,
                COUNT(*) FILTER (WHERE status = 'converted') as converted,
                COUNT(*) FILTER (WHERE status IN ('new','enriched','qualified','sent')) as active,
                COUNT(*) as total
            FROM leads
        """)
        # Yesterday's snapshot for deltas
        yesterday = await conn.fetchrow("""
            SELECT
                COUNT(*) FILTER (WHERE created_at BETWEEN NOW() - INTERVAL '48 hours' AND NOW() - INTERVAL '24 hours') as discovered_yesterday
            FROM leads
        """)
        # Email stats today
        emails = await conn.fetchrow("""
            SELECT
                COUNT(*) FILTER (WHERE sent_at > NOW() - INTERVAL '24 hours') as sent_today,
                COUNT(*) FILTER (WHERE replied_at > NOW() - INTERVAL '24 hours') as replied_today,
                COUNT(*) FILTER (WHERE status = 'bounced' AND sent_at > NOW() - INTERVAL '24 hours') as bounced_today
            FROM outreach_emails
        """)
        # Email stats yesterday for delta
        emails_yesterday = await conn.fetchrow("""
            SELECT
                COUNT(*) FILTER (WHERE sent_at BETWEEN NOW() - INTERVAL '48 hours' AND NOW() - INTERVAL '24 hours') as sent_yesterday,
                COUNT(*) FILTER (WHERE replied_at BETWEEN NOW() - INTERVAL '48 hours' AND NOW() - INTERVAL '24 hours') as replied_yesterday
            FROM outreach_emails
        """)
        # Deliverability
        bounce_row = await conn.fetchrow("""
            SELECT
                COUNT(*) FILTER (WHERE status = 'bounced') as bounces,
                COUNT(*) as total
            FROM outreach_emails WHERE sent_at > NOW() - INTERVAL '7 days'
        """)

    bounce_rate = (bounce_row["bounces"] / bounce_row["total"]) if bounce_row["total"] > 0 else 0
    bounce_health = "good" if bounce_rate < 0.02 else "warning" if bounce_rate < 0.05 else "critical"

    blocks = [
        _header("Daily Pipeline Digest"),
        _divider(),
        _fields([
            ("Discovered (24h)", _delta(today["discovered_today"], yesterday["discovered_yesterday"])),
            ("Total Active Leads", str(today["active"])),
            ("Qualified", str(today["qualified"])),
            ("Total Pipeline", str(today["total"])),
        ]),
        _divider(),
        _fields([
            ("Emails Sent (24h)", _delta(emails["sent_today"], emails_yesterday["sent_yesterday"])),
            ("Replies (24h)", _delta(emails["replied_today"], emails_yesterday["replied_yesterday"])),
            ("Bounced (24h)", str(emails["bounced_today"])),
            ("Reply Rate", f"{(emails['replied_today'] / emails['sent_today'] * 100):.1f}%" if emails["sent_today"] > 0 else "N/A"),
        ]),
        _divider(),
        _fields([
            ("Replied (all time)", str(today["replied"])),
            ("Converted (all time)", str(today["converted"])),
            ("Bounce Rate (7d)", f"{bounce_rate:.2%}"),
            ("Deliverability", f"{bounce_health.upper()}"),
        ]),
        _context(f"Report generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"),
    ]

    text = (
        f"Daily Digest: {emails['sent_today']} sent, {emails['replied_today']} replies, "
        f"{today['discovered_today']} discovered, bounce rate {bounce_rate:.2%}"
    )
    await _post_slack(DASHBOARD_CHANNEL, text, blocks)
    return {"sent_today": emails["sent_today"], "replied_today": emails["replied_today"],
            "discovered_today": today["discovered_today"], "bounce_rate": bounce_rate}


# ─── REPORT 6: Daily Deliverability Report (scheduled → #claw-company-agent)

async def report_daily_deliverability():
    """Detailed deliverability health check."""
    async with db() as conn:
        bounce_stats = await conn.fetchrow("""
            SELECT
                COUNT(*) FILTER (WHERE status = 'bounced') as bounces,
                COUNT(*) FILTER (WHERE status = 'sent') as sent,
                COUNT(*) as total
            FROM outreach_emails WHERE sent_at > NOW() - INTERVAL '7 days'
        """)
        verification = await conn.fetchrow("""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE status = 'valid') as valid,
                COUNT(*) FILTER (WHERE status = 'invalid') as invalid,
                COUNT(*) FILTER (WHERE status = 'risky') as risky
            FROM email_verification_cache
        """)
        velocity = await conn.fetchrow("""
            SELECT
                COUNT(*) FILTER (WHERE sent_at > NOW() - INTERVAL '1 hour') as hourly,
                COUNT(*) FILTER (WHERE sent_at > NOW() - INTERVAL '24 hours') as daily
            FROM outreach_emails WHERE status = 'sent'
        """)

    bounce_rate = (bounce_stats["bounces"] / bounce_stats["total"]) if bounce_stats["total"] > 0 else 0
    health = "good" if bounce_rate < 0.02 else "warning" if bounce_rate < 0.05 else "critical"

    blocks = [
        _header("Deliverability Health Report"),
        _divider(),
        _fields([
            ("Bounce Rate (7d)", f"{bounce_rate:.2%}"),
            ("Health Status", health.upper()),
            ("Total Bounces", str(bounce_stats["bounces"])),
            ("Emails Sent (7d)", str(bounce_stats["total"])),
        ]),
        _divider(),
        _fields([
            ("Verified Emails", str(verification["total"])),
            ("Valid", str(verification["valid"])),
            ("Invalid (blocked)", str(verification["invalid"])),
            ("Risky", str(verification["risky"])),
        ]),
        _divider(),
        _fields([
            ("Hourly Velocity", f"{velocity['hourly']}/15"),
            ("Daily Velocity", f"{velocity['daily']}/40"),
            ("Gmail Benny", "Authenticated"),
            ("Gmail George", "Authenticated"),
        ]),
        _context(f"Report generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"),
    ]

    await _post_slack(OPS_CHANNEL, f"Deliverability: {health.upper()} | Bounce rate {bounce_rate:.2%}", blocks)
    return {"bounce_rate": bounce_rate, "health": health}


# ─── REPORT 7: Weekly Performance Summary (scheduled → #optaimum-dashboard)

async def report_weekly_summary():
    """Weekly conversion funnel and performance trends."""
    async with db() as conn:
        # This week
        this_week = await conn.fetchrow("""
            SELECT
                COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '7 days') as discovered,
                COUNT(*) FILTER (WHERE status = 'replied') as total_replied,
                COUNT(*) FILTER (WHERE status = 'converted') as total_converted
            FROM leads
        """)
        this_week_emails = await conn.fetchrow("""
            SELECT
                COUNT(*) FILTER (WHERE sent_at > NOW() - INTERVAL '7 days') as sent,
                COUNT(*) FILTER (WHERE replied_at > NOW() - INTERVAL '7 days') as replied,
                COUNT(*) FILTER (WHERE status = 'bounced' AND sent_at > NOW() - INTERVAL '7 days') as bounced
            FROM outreach_emails
        """)
        # Last week for comparison
        last_week_emails = await conn.fetchrow("""
            SELECT
                COUNT(*) FILTER (WHERE sent_at BETWEEN NOW() - INTERVAL '14 days' AND NOW() - INTERVAL '7 days') as sent,
                COUNT(*) FILTER (WHERE replied_at BETWEEN NOW() - INTERVAL '14 days' AND NOW() - INTERVAL '7 days') as replied
            FROM outreach_emails
        """)
        # Top performing leads (replied)
        top_leads = await conn.fetch("""
            SELECT company_name, contact_name, score
            FROM leads WHERE status = 'replied'
            ORDER BY score DESC LIMIT 5
        """)

    reply_rate = (this_week_emails["replied"] / this_week_emails["sent"] * 100) if this_week_emails["sent"] > 0 else 0
    last_reply_rate = (last_week_emails["replied"] / last_week_emails["sent"] * 100) if last_week_emails["sent"] > 0 else 0

    blocks = [
        _header("Weekly Performance Summary"),
        _divider(),
        _text("*Conversion Funnel (7 days)*"),
        _fields([
            ("Discovered", str(this_week["discovered"])),
            ("Emails Sent", _delta(this_week_emails["sent"], last_week_emails["sent"])),
            ("Replies", _delta(this_week_emails["replied"], last_week_emails["replied"])),
            ("Bounced", str(this_week_emails["bounced"])),
        ]),
        _divider(),
        _fields([
            ("Reply Rate", f"{reply_rate:.1f}%"),
            ("vs Last Week", f"{last_reply_rate:.1f}%"),
            ("All-Time Replied", str(this_week["total_replied"])),
            ("All-Time Converted", str(this_week["total_converted"])),
        ]),
    ]

    if top_leads:
        lines = [f"- {r['company_name']} ({r['contact_name']}) - score {r['score']}/10" for r in top_leads]
        blocks.append(_divider())
        blocks.append(_text("*Top Leads (Replied)*\n" + "\n".join(lines)))

    blocks.append(_context(f"Week ending {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"))

    await _post_slack(DASHBOARD_CHANNEL, f"Weekly: {this_week_emails['sent']} sent, {this_week_emails['replied']} replies, {reply_rate:.1f}% rate", blocks)
    return {"sent": this_week_emails["sent"], "replied": this_week_emails["replied"], "reply_rate": reply_rate}
