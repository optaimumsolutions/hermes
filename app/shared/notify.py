import logging
import httpx
from .config import get_settings

log = logging.getLogger(__name__)


# ─── TELEGRAM ──────────────────────────────────────────────────────

async def send_telegram(message: str):
    s = get_settings()
    if not s.telegram_bot_token or not s.telegram_chat_id:
        return
    url = f"https://api.telegram.org/bot{s.telegram_bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient() as client:
            await client.post(url, json={
                "chat_id": s.telegram_chat_id,
                "text": message,
                "parse_mode": "Markdown",
            })
    except Exception as e:
        log.warning(f"Telegram send failed: {e}")


# ─── SLACK ─────────────────────────────────────────────────────────

async def send_slack(message: str, blocks: list = None):
    """Post a message to the configured Slack channel.

    Supports both plain text and Block Kit formatted messages.
    """
    s = get_settings()
    if not s.slack_bot_token or not s.slack_channel_id:
        return
    payload = {
        "channel": s.slack_channel_id,
        "text": message,
    }
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
                log.warning(f"Slack send failed: {data.get('error')}")
    except Exception as e:
        log.warning(f"Slack send failed: {e}")


def _slack_report_blocks(title: str, fields: list[tuple[str, str]]) -> list:
    """Build Slack Block Kit blocks for a report."""
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": title}},
        {"type": "divider"},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*{label}*\n{value}"}
                for label, value in fields
            ],
        },
    ]
    return blocks


# ─── UNIFIED NOTIFICATIONS ────────────────────────────────────────

async def notify(message: str, slack_blocks: list = None):
    """Send to both Telegram and Slack."""
    await send_telegram(message)
    await send_slack(message, blocks=slack_blocks)


async def alert_hot_lead(lead: dict):
    msg = (
        f"*HOT LEAD REPLY*\n\n"
        f"*From:* {lead.get('contact_name', 'Unknown')} at {lead.get('company_name', 'Unknown')}\n"
        f"*Email:* {lead.get('email', 'N/A')}\n"
        f"*Score:* {lead.get('score', 0)}/10\n"
        f"*Reply:* {lead.get('reply_text', '')[:200]}\n\n"
        f"Respond ASAP."
    )
    blocks = _slack_report_blocks("HOT LEAD REPLY", [
        ("Contact", f"{lead.get('contact_name', 'Unknown')} at {lead.get('company_name', 'Unknown')}"),
        ("Email", lead.get("email", "N/A")),
        ("Score", f"{lead.get('score', 0)}/10"),
        ("Reply", lead.get("reply_text", "")[:200]),
    ])
    await notify(msg, slack_blocks=blocks)


async def daily_report(stats: dict):
    msg = (
        f"*Daily Outreach Report*\n\n"
        f"*Scout:* {stats.get('discovered', 0)} found, {stats.get('qualified', 0)} qualified\n"
        f"*Sender:* {stats.get('sent', 0)} sent, {stats.get('opened', 0)} opened, {stats.get('replied', 0)} replied\n"
        f"*Pipeline:* {stats.get('total_active', 0)} active leads\n"
        f"*Conversion:* {stats.get('converted', 0)} this week"
    )
    blocks = _slack_report_blocks("Daily Outreach Report", [
        ("Discovered", str(stats.get("discovered", 0))),
        ("Qualified", str(stats.get("qualified", 0))),
        ("Sent", str(stats.get("sent", 0))),
        ("Replied", str(stats.get("replied", 0))),
        ("Active Leads", str(stats.get("total_active", 0))),
        ("Converted", str(stats.get("converted", 0))),
    ])
    await notify(msg, slack_blocks=blocks)


async def campaign_report(step: int, sent: int, failed: int, account: str, from_addr: str):
    """Report a campaign batch completion to both channels."""
    msg = (
        f"*CatchFlow Campaign - Step {step}*\n"
        f"Sent: {sent} | Failed: {failed}\n"
        f"From: {from_addr}"
    )
    blocks = _slack_report_blocks(f"CatchFlow Campaign - Step {step}", [
        ("Sent", str(sent)),
        ("Failed", str(failed)),
        ("From", from_addr),
        ("Account", account),
    ])
    await notify(msg, slack_blocks=blocks)
