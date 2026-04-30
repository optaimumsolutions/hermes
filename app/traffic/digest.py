"""Daily traffic digest formatter.

Produces the fixed-format digest for Slack (Block Kit) and Telegram (plaintext).
Format matches the SOUL.md spec: under 25 lines, both domains side by side,
movers only on real movement, watch items capped at 3.
"""
import logging
from datetime import date, timedelta
from .trends import (
    DOMAINS, get_snapshot, compute_delta, get_bot_hits,
    detect_movers, get_recent_alerts,
)

log = logging.getLogger(__name__)

# Known AI bots to track
AI_BOTS = ["GPTBot", "ClaudeBot", "PerplexityBot", "Google-Extended", "Bingbot", "Applebot"]


def _fmt_delta(delta: dict) -> str:
    """Format a metric with its 7d delta."""
    val = delta["current"]
    pct = delta["delta_7d_pct"]
    if pct is None:
        return str(val)
    sign = "+" if pct > 0 else ""
    return f"{val} ({sign}{pct}%)"


def _fmt_pos(delta: dict) -> str:
    """Format position (lower is better, so invert the sign for display)."""
    val = delta["current"]
    if val is None or val == 0:
        return "N/A"
    pct = delta["delta_7d_pct"]
    if pct is None:
        return f"{val:.1f}"
    # For position, negative delta = improvement
    sign = "+" if pct > 0 else ""
    return f"{val:.1f} ({sign}{pct}%)"


async def _domain_section(domain: str, report_date: date) -> dict:
    """Build metrics for a single domain."""
    section = {"domain": domain, "has_data": False}

    # GSC (2-day lag)
    gsc_date = report_date - timedelta(days=2)
    gsc = await get_snapshot(domain, gsc_date, "gsc")
    if gsc:
        section["has_data"] = True
        section["gsc"] = {
            "impressions": await compute_delta(domain, "gsc", "impressions", gsc_date),
            "clicks": await compute_delta(domain, "gsc", "clicks", gsc_date),
            "avg_position": await compute_delta(domain, "gsc", "avg_position", gsc_date),
            "ctr": gsc.get("ctr", 0),
        }
    else:
        section["gsc"] = None

    # GA4 (1-day lag)
    ga4_date = report_date - timedelta(days=1)
    ga4 = await get_snapshot(domain, ga4_date, "ga4")
    if ga4:
        section["has_data"] = True
        section["ga4"] = {
            "sessions": await compute_delta(domain, "ga4", "sessions", ga4_date),
            "conversions": await compute_delta(domain, "ga4", "conversions", ga4_date),
            "users": ga4.get("users", 0),
        }
    else:
        section["ga4"] = None

    # Cloudflare (real-time, use yesterday)
    cf_date = report_date - timedelta(days=1)
    cf = await get_snapshot(domain, cf_date, "cloudflare")
    if cf:
        section["has_data"] = True
        section["cf"] = {
            "requests": await compute_delta(domain, "cloudflare", "requests", cf_date),
            "unique_visitors": cf.get("unique_visitors", 0),
            "bot_pct": cf.get("bot_pct", 0),
        }
    else:
        section["cf"] = None

    # AI bots
    bot_data = await get_bot_hits(domain, report_date - timedelta(days=1))
    section["bots"] = {b["bot"]: b["hits"] for b in bot_data}

    return section


async def build_digest(report_date: date = None) -> dict:
    """Build the full daily digest.

    Returns structured data that can be formatted for Slack or Telegram.
    """
    if report_date is None:
        report_date = date.today()

    sections = {}
    for domain in DOMAINS:
        sections[domain] = await _domain_section(domain, report_date)

    movers = await detect_movers(report_date - timedelta(days=1))
    alerts = await get_recent_alerts(days=1)

    # Build watch items (max 3)
    watch = []
    for m in movers[:3]:
        direction = "up" if m["magnitude"] > 0 else "down"
        watch.append(
            f"{m['domain']} {m['source'].upper()} {m['metric']} {direction} "
            f"{abs(m['magnitude']):.0f}% ({m['current']} vs {m['prev']})"
        )

    return {
        "date": str(report_date),
        "domains": sections,
        "movers": movers,
        "watch": watch,
    }


def format_telegram(digest: dict) -> str:
    """Format digest as plaintext for Telegram (matches SOUL.md spec)."""
    lines = [f"*Traffic Digest -- {digest['date']}*", ""]

    for domain in DOMAINS:
        s = digest["domains"].get(domain, {})
        lines.append(f"*{domain}*")

        if not s.get("has_data"):
            lines.append("  No data yet (insufficient history)")
            lines.append("")
            continue

        # Search
        if s.get("gsc"):
            g = s["gsc"]
            impr = _fmt_delta(g["impressions"])
            clicks = _fmt_delta(g["clicks"])
            pos = _fmt_pos(g["avg_position"])
            lines.append(f"  Search: {impr} impr, {clicks} clicks, pos {pos}")
        else:
            lines.append("  Search: no GSC data")

        # Users
        if s.get("ga4"):
            a = s["ga4"]
            sess = _fmt_delta(a["sessions"])
            conv = _fmt_delta(a["conversions"])
            lines.append(f"  Users: {sess} sessions, {conv} conversions")
        else:
            lines.append("  Users: no GA4 data")

        # Origin
        if s.get("cf"):
            c = s["cf"]
            reqs = _fmt_delta(c["requests"])
            bot = c["bot_pct"]
            lines.append(f"  Origin: {reqs} requests, {bot}% bot")
        else:
            lines.append("  Origin: no Cloudflare data")

        # AI bots
        bots = s.get("bots", {})
        if bots:
            bot_parts = [f"{name} {count}" for name, count in bots.items() if count > 0]
            if bot_parts:
                lines.append(f"  AI bots: {', '.join(bot_parts)}")
        lines.append("")

    # Movers
    if digest["movers"]:
        lines.append("*Movers* (>= +/-20% vs 7d avg or position drop >= 3):")
        for m in digest["movers"][:5]:
            direction = "UP" if m["magnitude"] > 0 else "DOWN"
            lines.append(
                f"  - {m['domain']} {m['source'].upper()} {m['metric']} "
                f"{direction} {abs(m['magnitude']):.0f}%"
            )
    else:
        lines.append("Movers: none today")

    lines.append("")

    # Watch
    if digest["watch"]:
        lines.append("*Watch:*")
        for w in digest["watch"][:3]:
            lines.append(f"  - {w}")
    else:
        lines.append("Watch: nothing flagged")

    return "\n".join(lines)


def format_slack_blocks(digest: dict) -> list:
    """Format digest as Slack Block Kit blocks."""
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"Traffic Digest - {digest['date']}"}},
        {"type": "divider"},
    ]

    for domain in DOMAINS:
        s = digest["domains"].get(domain, {})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{domain}*"},
        })

        if not s.get("has_data"):
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": "_No data yet (insufficient history)_"},
            })
            continue

        fields = []

        if s.get("gsc"):
            g = s["gsc"]
            fields.append({"type": "mrkdwn", "text": f"*Search*\n{_fmt_delta(g['impressions'])} impr\n{_fmt_delta(g['clicks'])} clicks\npos {_fmt_pos(g['avg_position'])}"})
        else:
            fields.append({"type": "mrkdwn", "text": "*Search*\nNo GSC data"})

        if s.get("ga4"):
            a = s["ga4"]
            fields.append({"type": "mrkdwn", "text": f"*Users*\n{_fmt_delta(a['sessions'])} sessions\n{_fmt_delta(a['conversions'])} conv"})
        else:
            fields.append({"type": "mrkdwn", "text": "*Users*\nNo GA4 data"})

        if s.get("cf"):
            c = s["cf"]
            fields.append({"type": "mrkdwn", "text": f"*Origin*\n{_fmt_delta(c['requests'])} requests\n{c['bot_pct']}% bot"})
        else:
            fields.append({"type": "mrkdwn", "text": "*Origin*\nNo CF data"})

        # AI bots
        bots = s.get("bots", {})
        bot_lines = [f"{name}: {count}" for name, count in bots.items() if count > 0]
        if bot_lines:
            fields.append({"type": "mrkdwn", "text": f"*AI Bots*\n{chr(10).join(bot_lines)}"})
        else:
            fields.append({"type": "mrkdwn", "text": "*AI Bots*\nNone detected"})

        blocks.append({"type": "section", "fields": fields})
        blocks.append({"type": "divider"})

    # Movers
    if digest["movers"]:
        mover_lines = []
        for m in digest["movers"][:5]:
            direction = ":arrow_up:" if m["magnitude"] > 0 else ":arrow_down:"
            mover_lines.append(
                f"{direction} {m['domain']} {m['source'].upper()} "
                f"*{m['metric']}* {abs(m['magnitude']):.0f}% "
                f"({m['current']} vs {m['prev']})"
            )
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*Movers*\n" + "\n".join(mover_lines)},
        })
    else:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*Movers:* none today"},
        })

    # Watch
    if digest["watch"]:
        watch_lines = [f"- {w}" for w in digest["watch"][:3]]
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*Watch*\n" + "\n".join(watch_lines)},
        })
    else:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*Watch:* nothing flagged"},
        })

    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": f"Generated for {digest['date']}"}],
    })

    return blocks
