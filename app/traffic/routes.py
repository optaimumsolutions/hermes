"""Traffic monitor API routes.

Endpoints for triggering data pulls, viewing digests, and checking status.
"""
import logging
from datetime import date, timedelta
from fastapi import APIRouter, Query
from .trends import (
    DOMAINS, get_snapshot, compute_delta, get_bot_hits,
    get_recent_alerts, detect_movers, store_snapshot,
)
from .digest import build_digest, format_telegram, format_slack_blocks
from .search_console import pull_all_domains as pull_gsc
from .analytics import pull_all_domains as pull_ga4
from .cloudflare import pull_all_domains as pull_cf
from app.shared.notify import send_telegram, send_slack
from app.shared.slack_reports import _post_slack, DASHBOARD_CHANNEL

log = logging.getLogger(__name__)

router = APIRouter(prefix="/traffic", tags=["Traffic Monitor"])


@router.post("/pull")
async def pull_all_sources(query_date: str = Query(None, description="YYYY-MM-DD, defaults to appropriate lag per source")):
    """Pull data from all configured sources (GSC, GA4, Cloudflare)."""
    d = date.fromisoformat(query_date) if query_date else None

    gsc = await pull_gsc(d)
    ga4 = await pull_ga4(d)
    cf = await pull_cf(d)

    return {
        "gsc": {domain: "ok" if data else "no data" for domain, data in gsc.items()},
        "ga4": {domain: "ok" if data else "no data" for domain, data in ga4.items()},
        "cloudflare": {domain: "ok" if data else "no data" for domain, data in cf.items()},
    }


@router.post("/digest")
async def trigger_digest(report_date: str = Query(None, description="YYYY-MM-DD, defaults to today"),
                         send: bool = Query(True, description="Send to Slack + Telegram")):
    """Build and optionally send the daily traffic digest."""
    d = date.fromisoformat(report_date) if report_date else date.today()

    digest = await build_digest(d)

    if send:
        # Telegram
        telegram_text = format_telegram(digest)
        await send_telegram(telegram_text)

        # Slack
        blocks = format_slack_blocks(digest)
        fallback = f"Traffic Digest - {digest['date']}"
        await _post_slack(DASHBOARD_CHANNEL, fallback, blocks)

    return digest


@router.post("/pull-and-digest")
async def pull_and_digest():
    """Full daily run: pull all sources then generate and send digest.

    This is what the cron job calls at 7am daily.
    """
    # Pull data
    gsc = await pull_gsc()
    ga4 = await pull_ga4()
    cf = await pull_cf()

    # Build and send digest
    digest = await build_digest()
    telegram_text = format_telegram(digest)
    await send_telegram(telegram_text)

    blocks = format_slack_blocks(digest)
    await _post_slack(DASHBOARD_CHANNEL, f"Traffic Digest - {digest['date']}", blocks)

    return {
        "pull_results": {
            "gsc": {d: "ok" if v else "no data" for d, v in gsc.items()},
            "ga4": {d: "ok" if v else "no data" for d, v in ga4.items()},
            "cloudflare": {d: "ok" if v else "no data" for d, v in cf.items()},
        },
        "digest": digest,
        "sent": True,
    }


@router.get("/status")
async def traffic_status():
    """Check what data we have for each domain and source."""
    today = date.today()
    status = {}

    for domain in DOMAINS:
        domain_status = {}
        for source, lag in [("gsc", 2), ("ga4", 1), ("cloudflare", 1)]:
            check_date = today - timedelta(days=lag)
            snapshot = await get_snapshot(domain, check_date, source)
            domain_status[source] = {
                "has_data": snapshot is not None,
                "date_checked": str(check_date),
            }
        # Bot data
        bots = await get_bot_hits(domain, today - timedelta(days=1))
        domain_status["ai_bots"] = bots

        status[domain] = domain_status

    # Recent alerts
    alerts = await get_recent_alerts(days=7)
    status["recent_alerts"] = len(alerts)

    return status


@router.get("/snapshot/{domain}/{source}")
async def get_domain_snapshot(domain: str, source: str,
                               query_date: str = Query(None)):
    """Get a specific snapshot for a domain and source."""
    d = date.fromisoformat(query_date) if query_date else date.today() - timedelta(days=1)
    snapshot = await get_snapshot(domain, d, source)
    if not snapshot:
        return {"domain": domain, "source": source, "date": str(d), "data": None}
    return {"domain": domain, "source": source, "date": str(d), "data": snapshot}


@router.get("/deltas/{domain}")
async def get_domain_deltas(domain: str,
                             query_date: str = Query(None)):
    """Get all deltas (7d + 28d) for a domain across all sources."""
    d = date.fromisoformat(query_date) if query_date else date.today() - timedelta(days=1)

    deltas = {}

    # GSC deltas
    gsc_date = d - timedelta(days=1)  # extra day for GSC lag
    for metric in ["impressions", "clicks", "avg_position", "ctr"]:
        deltas[f"gsc_{metric}"] = await compute_delta(domain, "gsc", metric, gsc_date)

    # GA4 deltas
    for metric in ["sessions", "users", "conversions"]:
        deltas[f"ga4_{metric}"] = await compute_delta(domain, "ga4", metric, d)

    # CF deltas
    for metric in ["requests", "unique_visitors"]:
        deltas[f"cf_{metric}"] = await compute_delta(domain, "cloudflare", metric, d)

    return {"domain": domain, "date": str(d), "deltas": deltas}


@router.get("/movers")
async def get_movers(query_date: str = Query(None)):
    """Detect significant movements across both domains."""
    d = date.fromisoformat(query_date) if query_date else date.today() - timedelta(days=1)
    movers = await detect_movers(d)
    return {"date": str(d), "movers": movers}


@router.get("/alerts")
async def get_alerts(domain: str = Query(None), days: int = Query(7)):
    """Get recent traffic alerts."""
    alerts = await get_recent_alerts(domain, days)
    return {"alerts": alerts, "count": len(alerts)}


@router.post("/ingest")
async def manual_ingest(domain: str, source: str, query_date: str,
                         metrics: dict):
    """Manually ingest a traffic snapshot (for testing or backfill)."""
    d = date.fromisoformat(query_date)
    await store_snapshot(domain, d, source, metrics)
    return {"status": "stored", "domain": domain, "source": source, "date": query_date}
