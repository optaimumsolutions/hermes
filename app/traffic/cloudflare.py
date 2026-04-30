"""Cloudflare Web Analytics connector.

Pulls daily traffic data and AI bot detection via the Cloudflare GraphQL API.
Requires a CF API token scoped to Analytics:Read.
"""
import logging
import httpx
from datetime import date, timedelta
from app.shared.config import get_settings
from .trends import store_snapshot, store_bot_hits, DOMAINS

log = logging.getLogger(__name__)

CF_GRAPHQL = "https://api.cloudflare.com/client/v4/graphql"

# Known AI crawler user-agent signatures
AI_BOT_SIGNATURES = {
    "GPTBot": "GPTBot",
    "ClaudeBot": "ClaudeBot",
    "PerplexityBot": "PerplexityBot",
    "Google-Extended": "Google-Extended",
    "Bingbot": "bingbot",
    "Applebot": "Applebot",
}


def _get_config() -> dict:
    """Get CF credentials from settings."""
    s = get_settings()
    return {
        "token": getattr(s, "cloudflare_api_token", ""),
        "zones": {
            "optaimum.com": getattr(s, "cloudflare_zone_optaimum", ""),
            "catchflow.org": getattr(s, "cloudflare_zone_catchflow", ""),
        },
    }


async def pull_daily(domain: str, query_date: date = None) -> dict | None:
    """Pull daily Cloudflare analytics for a domain.

    Returns: {requests, unique_visitors, bot_pct, top_countries, top_user_agents}
    """
    config = _get_config()
    if not config["token"]:
        log.info("No Cloudflare API token configured")
        return None

    zone_id = config["zones"].get(domain)
    if not zone_id:
        log.info(f"No Cloudflare zone ID configured for {domain}")
        return None

    if query_date is None:
        query_date = date.today() - timedelta(days=1)

    date_str = query_date.isoformat()
    next_date = (query_date + timedelta(days=1)).isoformat()

    query = """
    query ($zoneTag: String!, $date: String!, $nextDate: String!) {
        viewer {
            zones(filter: {zoneTag: $zoneTag}) {
                httpRequests1dGroups(
                    filter: {date_geq: $date, date_lt: $nextDate}
                    limit: 1
                ) {
                    sum {
                        requests
                        threats
                        pageViews
                        browserMap { pageViews uaBrowserFamily }
                        countryMap { requests clientCountryName }
                    }
                    uniq { uniques }
                }
            }
        }
    }
    """

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                CF_GRAPHQL,
                headers={
                    "Authorization": f"Bearer {config['token']}",
                    "Content-Type": "application/json",
                },
                json={
                    "query": query,
                    "variables": {
                        "zoneTag": zone_id,
                        "date": date_str,
                        "nextDate": next_date,
                    },
                },
            )
            if resp.status_code != 200:
                log.warning(f"CF API error {resp.status_code} for {domain}: {resp.text[:200]}")
                return None

            data = resp.json()
            errors = data.get("errors")
            if errors:
                log.warning(f"CF GraphQL errors for {domain}: {errors}")
                return None

            zones = data.get("data", {}).get("viewer", {}).get("zones", [])
            if not zones or not zones[0].get("httpRequests1dGroups"):
                log.info(f"CF returned no data for {domain} on {date_str}")
                return None

            group = zones[0]["httpRequests1dGroups"][0]
            totals = group.get("sum", {})
            uniq = group.get("uniq", {})

            total_requests = totals.get("requests", 0)
            threats = totals.get("threats", 0)
            bot_pct = round((threats / total_requests * 100), 1) if total_requests > 0 else 0

            metrics = {
                "requests": total_requests,
                "unique_visitors": uniq.get("uniques", 0),
                "page_views": totals.get("pageViews", 0),
                "threats": threats,
                "bot_pct": bot_pct,
            }

            # Top countries
            country_map = totals.get("countryMap", [])
            metrics["top_countries"] = sorted(
                [{"country": c["clientCountryName"], "requests": c["requests"]}
                 for c in country_map],
                key=lambda x: x["requests"], reverse=True,
            )[:10]

            # Top user agents (for bot detection)
            browser_map = totals.get("browserMap", [])
            metrics["top_user_agents"] = sorted(
                [{"agent": b["uaBrowserFamily"], "views": b["pageViews"]}
                 for b in browser_map],
                key=lambda x: x["views"], reverse=True,
            )[:10]

            await store_snapshot(domain, query_date, "cloudflare", metrics)

            # Detect AI bot hits from user agents
            for bot_name, signature in AI_BOT_SIGNATURES.items():
                for ua in browser_map:
                    if signature.lower() in ua.get("uaBrowserFamily", "").lower():
                        await store_bot_hits(
                            domain, query_date, bot_name,
                            ua.get("pageViews", 0),
                        )

            return metrics

    except Exception as e:
        log.error(f"CF pull failed for {domain}: {e}")
        return None


async def pull_all_domains(query_date: date = None) -> dict:
    results = {}
    for domain in DOMAINS:
        results[domain] = await pull_daily(domain, query_date)
    return results
