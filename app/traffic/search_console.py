import os
"""Google Search Console data connector.

Pulls daily metrics using the webmasters.readonly scope.
Requires OAuth tokens stored in gmail_tokens table (reuses the same Google OAuth app).
"""
import logging
import httpx
from datetime import date, timedelta
from app.sender.gmail import _load_tokens, refresh_access_token
from .trends import store_snapshot, store_alert, DOMAINS

log = logging.getLogger(__name__)

# GSC API base
GSC_API = "https://searchconsole.googleapis.com/webmasters/v3"

# Use benny's OAuth tokens (same Google account owns both properties)
OAUTH_ACCOUNT = os.environ.get("TRAFFIC_OAUTH_ACCOUNT", "benny")



async def _get_token() -> str | None:
    """Get a valid access token for GSC (reuses Gmail OAuth)."""
    try:
        return await refresh_access_token(OAUTH_ACCOUNT)
    except Exception as e:
        log.error(f"GSC auth failed: {e}")
        return None


async def pull_daily(domain: str, query_date: date = None) -> dict | None:
    """Pull daily GSC metrics for a domain.

    Returns: {impressions, clicks, ctr, avg_position, top_queries, top_pages}
    """
    token = await _get_token()
    if not token:
        return None

    if query_date is None:
        query_date = date.today() - timedelta(days=2)  # GSC has 2-day lag

    site_url = f"sc-domain:{domain}"
    date_str = query_date.isoformat()

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # Aggregate metrics
            resp = await client.post(
                f"{GSC_API}/sites/{site_url}/searchAnalytics/query",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "startDate": date_str,
                    "endDate": date_str,
                    "dimensions": [],
                    "rowLimit": 1,
                },
            )
            if resp.status_code == 403:
                log.warning(f"GSC access denied for {domain}. Verify property ownership.")
                return None
            if resp.status_code != 200:
                log.warning(f"GSC API error {resp.status_code} for {domain}: {resp.text[:200]}")
                return None

            data = resp.json()
            rows = data.get("rows", [])
            if not rows:
                log.info(f"GSC returned no data for {domain} on {date_str}")
                return None

            agg = rows[0]
            metrics = {
                "impressions": agg.get("impressions", 0),
                "clicks": agg.get("clicks", 0),
                "ctr": round(agg.get("ctr", 0) * 100, 2),
                "avg_position": round(agg.get("position", 0), 1),
            }

            # Top queries
            resp_q = await client.post(
                f"{GSC_API}/sites/{site_url}/searchAnalytics/query",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "startDate": date_str,
                    "endDate": date_str,
                    "dimensions": ["query"],
                    "rowLimit": 10,
                    "orderBy": "impressions",
                },
            )
            if resp_q.status_code == 200:
                q_data = resp_q.json()
                metrics["top_queries"] = [
                    {"query": r["keys"][0], "impressions": r["impressions"],
                     "clicks": r["clicks"], "position": round(r["position"], 1)}
                    for r in q_data.get("rows", [])
                ]

            # Top pages
            resp_p = await client.post(
                f"{GSC_API}/sites/{site_url}/searchAnalytics/query",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "startDate": date_str,
                    "endDate": date_str,
                    "dimensions": ["page"],
                    "rowLimit": 10,
                    "orderBy": "clicks",
                },
            )
            if resp_p.status_code == 200:
                p_data = resp_p.json()
                metrics["top_pages"] = [
                    {"page": r["keys"][0], "impressions": r["impressions"],
                     "clicks": r["clicks"], "position": round(r["position"], 1)}
                    for r in p_data.get("rows", [])
                ]

            # Store snapshot
            await store_snapshot(domain, query_date, "gsc", metrics)
            return metrics

    except Exception as e:
        log.error(f"GSC pull failed for {domain}: {e}")
        return None


async def pull_all_domains(query_date: date = None) -> dict:
    """Pull GSC data for all monitored domains."""
    results = {}
    for domain in DOMAINS:
        results[domain] = await pull_daily(domain, query_date)
    return results
