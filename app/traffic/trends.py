"""Trend computation engine for traffic data.

Stores daily snapshots and computes rolling baselines for delta reporting.
All data lives in Neon PostgreSQL — no BigQuery needed.
"""
import json
import logging
from datetime import date, timedelta
from app.shared.db import db

log = logging.getLogger(__name__)

DOMAINS = ["optaimum.com", "catchflow.org"]
SOURCES = ["gsc", "ga4", "cloudflare"]


async def store_snapshot(domain: str, snapshot_date: date, source: str, metrics: dict):
    """Upsert a daily traffic snapshot."""
    async with db() as conn:
        await conn.execute("""
            INSERT INTO traffic_daily (domain, date, source, metrics)
            VALUES ($1, $2, $3, $4::jsonb)
            ON CONFLICT (domain, date, source) DO UPDATE
            SET metrics = $4::jsonb, created_at = NOW()
        """, domain, snapshot_date, source, json.dumps(metrics))


async def store_bot_hits(domain: str, hit_date: date, bot_name: str,
                         hit_count: int, top_pages: list = None):
    """Upsert bot hit data for a day."""
    async with db() as conn:
        await conn.execute("""
            INSERT INTO bot_hits_daily (domain, date, bot_name, hit_count, top_pages)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            ON CONFLICT (domain, date, bot_name) DO UPDATE
            SET hit_count = $4, top_pages = $5::jsonb, created_at = NOW()
        """, domain, hit_date, bot_name, hit_count, json.dumps(top_pages or []))


async def store_alert(domain: str, alert_type: str, severity: str,
                      message: str, metrics: dict = None):
    """Log a traffic alert."""
    async with db() as conn:
        await conn.execute("""
            INSERT INTO traffic_alerts (domain, alert_type, severity, message, metrics)
            VALUES ($1, $2, $3, $4, $5::jsonb)
        """, domain, alert_type, severity, message, json.dumps(metrics or {}))


async def get_snapshot(domain: str, snapshot_date: date, source: str) -> dict | None:
    """Get a single day's snapshot."""
    async with db() as conn:
        row = await conn.fetchrow("""
            SELECT metrics FROM traffic_daily
            WHERE domain = $1 AND date = $2 AND source = $3
        """, domain, snapshot_date, source)
        if row:
            m = row["metrics"]
            return json.loads(m) if isinstance(m, str) else dict(m)
    return None


async def get_range(domain: str, source: str, start: date, end: date) -> list[dict]:
    """Get snapshots for a date range."""
    async with db() as conn:
        rows = await conn.fetch("""
            SELECT date, metrics FROM traffic_daily
            WHERE domain = $1 AND source = $2 AND date BETWEEN $3 AND $4
            ORDER BY date ASC
        """, domain, source, start, end)
        results = []
        for row in rows:
            m = row["metrics"]
            entry = json.loads(m) if isinstance(m, str) else dict(m)
            entry["date"] = str(row["date"])
            results.append(entry)
        return results


async def get_rolling_average(domain: str, source: str, metric_key: str,
                              end_date: date, days: int = 7) -> float | None:
    """Compute rolling average of a specific metric over N days."""
    start = end_date - timedelta(days=days)
    snapshots = await get_range(domain, source, start, end_date)
    if not snapshots:
        return None
    values = [s.get(metric_key, 0) for s in snapshots if metric_key in s]
    if not values:
        return None
    return sum(values) / len(values)


async def compute_delta(domain: str, source: str, metric_key: str,
                        current_date: date) -> dict:
    """Compute deltas for a metric: vs 7d ago and vs 28d rolling average.

    Returns: {
        "current": <value>,
        "prev_7d": <value>,
        "delta_7d_pct": <float or None>,
        "avg_28d": <float or None>,
        "delta_28d_pct": <float or None>,
    }
    """
    current = await get_snapshot(domain, current_date, source)
    current_val = current.get(metric_key, 0) if current else 0

    # 7-day comparison
    prev_date = current_date - timedelta(days=7)
    prev = await get_snapshot(domain, prev_date, source)
    prev_val = prev.get(metric_key, 0) if prev else None

    delta_7d = None
    if prev_val and prev_val > 0:
        delta_7d = ((current_val - prev_val) / prev_val) * 100

    # 28-day rolling average
    avg_28d = await get_rolling_average(domain, source, metric_key, current_date, 28)
    delta_28d = None
    if avg_28d and avg_28d > 0:
        delta_28d = ((current_val - avg_28d) / avg_28d) * 100

    return {
        "current": current_val,
        "prev_7d": prev_val,
        "delta_7d_pct": round(delta_7d, 1) if delta_7d is not None else None,
        "avg_28d": round(avg_28d, 1) if avg_28d is not None else None,
        "delta_28d_pct": round(delta_28d, 1) if delta_28d is not None else None,
    }


async def get_bot_hits(domain: str, hit_date: date) -> list[dict]:
    """Get all bot hits for a domain on a specific date."""
    async with db() as conn:
        rows = await conn.fetch("""
            SELECT bot_name, hit_count, top_pages FROM bot_hits_daily
            WHERE domain = $1 AND date = $2
            ORDER BY hit_count DESC
        """, domain, hit_date)
        return [{"bot": r["bot_name"], "hits": r["hit_count"],
                 "pages": json.loads(r["top_pages"]) if isinstance(r["top_pages"], str) else r["top_pages"]}
                for r in rows]


async def get_recent_alerts(domain: str = None, days: int = 7) -> list[dict]:
    """Get recent alerts, optionally filtered by domain."""
    async with db() as conn:
        if domain:
            rows = await conn.fetch("""
                SELECT domain, alert_type, severity, message, metrics, created_at
                FROM traffic_alerts
                WHERE domain = $1 AND created_at > NOW() - make_interval(days => $2)
                ORDER BY created_at DESC LIMIT 20
            """, domain, days)
        else:
            rows = await conn.fetch("""
                SELECT domain, alert_type, severity, message, metrics, created_at
                FROM traffic_alerts
                WHERE created_at > NOW() - make_interval(days => $1)
                ORDER BY created_at DESC LIMIT 20
            """, days)
        return [dict(r) for r in rows]


async def detect_movers(current_date: date) -> list[dict]:
    """Detect significant movements across both domains.

    Fires on: >= +/-20% vs 7d average, or GSC position drop >= 3.
    """
    movers = []

    for domain in DOMAINS:
        # GSC movers
        gsc = await get_snapshot(domain, current_date, "gsc")
        if gsc:
            for key in ["impressions", "clicks"]:
                delta = await compute_delta(domain, "gsc", key, current_date)
                if delta["delta_7d_pct"] is not None and abs(delta["delta_7d_pct"]) >= 20:
                    direction = "up" if delta["delta_7d_pct"] > 0 else "down"
                    movers.append({
                        "domain": domain,
                        "source": "gsc",
                        "metric": key,
                        "direction": direction,
                        "magnitude": delta["delta_7d_pct"],
                        "current": delta["current"],
                        "prev": delta["prev_7d"],
                    })

            # Position drop check
            pos_delta = await compute_delta(domain, "gsc", "avg_position", current_date)
            if (pos_delta["current"] and pos_delta["prev_7d"] and
                    pos_delta["current"] - pos_delta["prev_7d"] >= 3):
                movers.append({
                    "domain": domain,
                    "source": "gsc",
                    "metric": "avg_position",
                    "direction": "down",
                    "magnitude": round(pos_delta["current"] - pos_delta["prev_7d"], 1),
                    "current": pos_delta["current"],
                    "prev": pos_delta["prev_7d"],
                })

        # GA4 movers
        ga4 = await get_snapshot(domain, current_date, "ga4")
        if ga4:
            for key in ["sessions", "users", "conversions"]:
                delta = await compute_delta(domain, "ga4", key, current_date)
                if delta["delta_7d_pct"] is not None and abs(delta["delta_7d_pct"]) >= 20:
                    direction = "up" if delta["delta_7d_pct"] > 0 else "down"
                    movers.append({
                        "domain": domain,
                        "source": "ga4",
                        "metric": key,
                        "direction": direction,
                        "magnitude": delta["delta_7d_pct"],
                        "current": delta["current"],
                        "prev": delta["prev_7d"],
                    })

        # Cloudflare movers
        cf = await get_snapshot(domain, current_date, "cloudflare")
        if cf:
            for key in ["requests", "unique_visitors"]:
                delta = await compute_delta(domain, "cloudflare", key, current_date)
                if delta["delta_7d_pct"] is not None and abs(delta["delta_7d_pct"]) >= 20:
                    direction = "up" if delta["delta_7d_pct"] > 0 else "down"
                    movers.append({
                        "domain": domain,
                        "source": "cloudflare",
                        "metric": key,
                        "direction": direction,
                        "magnitude": delta["delta_7d_pct"],
                        "current": delta["current"],
                        "prev": delta["prev_7d"],
                    })

    return movers
