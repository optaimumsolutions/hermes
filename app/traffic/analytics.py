"""Google Analytics 4 data connector.

Pulls daily metrics using the analytics.readonly scope via GA4 Data API.
Requires GA4 property IDs configured in settings.
"""
import logging
import httpx
from datetime import date, timedelta
from app.sender.gmail import _load_tokens, refresh_access_token
from app.shared.config import get_settings
from .trends import store_snapshot, DOMAINS

log = logging.getLogger(__name__)

GA4_API = "https://analyticsdata.googleapis.com/v1beta"
OAUTH_ACCOUNT = "benny"

# Map domains to GA4 property IDs (set via env vars)
def _get_property_ids() -> dict:
    s = get_settings()
    return {
        "optaimum.com": getattr(s, "ga4_property_optaimum", ""),
        "catchflow.org": getattr(s, "ga4_property_catchflow", ""),
    }


async def _get_token() -> str | None:
    try:
        return await refresh_access_token(OAUTH_ACCOUNT)
    except Exception as e:
        log.error(f"GA4 auth failed: {e}")
        return None


async def pull_daily(domain: str, query_date: date = None) -> dict | None:
    """Pull daily GA4 metrics for a domain.

    Returns: {sessions, users, engaged_sessions, conversions, top_sources, top_pages}
    """
    props = _get_property_ids()
    property_id = props.get(domain)
    if not property_id:
        log.info(f"No GA4 property ID configured for {domain}")
        return None

    token = await _get_token()
    if not token:
        return None

    if query_date is None:
        query_date = date.today() - timedelta(days=1)

    date_str = query_date.strftime("%Y%m%d")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # Core metrics
            resp = await client.post(
                f"{GA4_API}/properties/{property_id}:runReport",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "dateRanges": [{"startDate": date_str, "endDate": date_str}],
                    "metrics": [
                        {"name": "sessions"},
                        {"name": "totalUsers"},
                        {"name": "engagedSessions"},
                        {"name": "conversions"},
                    ],
                },
            )
            if resp.status_code != 200:
                log.warning(f"GA4 API error {resp.status_code} for {domain}: {resp.text[:200]}")
                return None

            data = resp.json()
            rows = data.get("rows", [])
            if not rows:
                log.info(f"GA4 returned no data for {domain} on {query_date}")
                return None

            values = rows[0].get("metricValues", [])
            metrics = {
                "sessions": int(values[0]["value"]) if len(values) > 0 else 0,
                "users": int(values[1]["value"]) if len(values) > 1 else 0,
                "engaged_sessions": int(values[2]["value"]) if len(values) > 2 else 0,
                "conversions": int(values[3]["value"]) if len(values) > 3 else 0,
            }

            # Top traffic sources
            resp_src = await client.post(
                f"{GA4_API}/properties/{property_id}:runReport",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "dateRanges": [{"startDate": date_str, "endDate": date_str}],
                    "dimensions": [{"name": "sessionDefaultChannelGroup"}],
                    "metrics": [{"name": "sessions"}],
                    "limit": "10",
                    "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
                },
            )
            if resp_src.status_code == 200:
                src_data = resp_src.json()
                metrics["top_sources"] = [
                    {"source": r["dimensionValues"][0]["value"],
                     "sessions": int(r["metricValues"][0]["value"])}
                    for r in src_data.get("rows", [])
                ]

            # Top landing pages
            resp_pg = await client.post(
                f"{GA4_API}/properties/{property_id}:runReport",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "dateRanges": [{"startDate": date_str, "endDate": date_str}],
                    "dimensions": [{"name": "landingPagePlusQueryString"}],
                    "metrics": [{"name": "sessions"}, {"name": "conversions"}],
                    "limit": "10",
                    "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
                },
            )
            if resp_pg.status_code == 200:
                pg_data = resp_pg.json()
                metrics["top_pages"] = [
                    {"page": r["dimensionValues"][0]["value"],
                     "sessions": int(r["metricValues"][0]["value"]),
                     "conversions": int(r["metricValues"][1]["value"])}
                    for r in pg_data.get("rows", [])
                ]

            await store_snapshot(domain, query_date, "ga4", metrics)
            return metrics

    except Exception as e:
        log.error(f"GA4 pull failed for {domain}: {e}")
        return None


async def pull_all_domains(query_date: date = None) -> dict:
    results = {}
    for domain in DOMAINS:
        results[domain] = await pull_daily(domain, query_date)
    return results
