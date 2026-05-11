"""Discovery endpoints for GSC and GA4 properties.

Lets the operator see which properties benny's OAuth has access to,
so we can configure the right property IDs.
"""
import logging
import httpx
from fastapi import APIRouter
from app.sender.gmail import refresh_access_token

log = logging.getLogger(__name__)

router = APIRouter(prefix="/discover", tags=["Discovery"])

OAUTH_ACCOUNT = "benny"


@router.get("/gsc-sites")
async def list_gsc_sites():
    """List all Search Console properties accessible via benny's OAuth."""
    try:
        token = await refresh_access_token(OAUTH_ACCOUNT)
    except Exception as e:
        return {"error": f"OAuth failed: {e}"}

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            "https://searchconsole.googleapis.com/webmasters/v3/sites",
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code != 200:
            return {"error": f"GSC API {resp.status_code}", "detail": resp.text[:300]}

        data = resp.json()
        sites = data.get("siteEntry", [])
        return {
            "count": len(sites),
            "sites": [
                {"url": s["siteUrl"], "permission": s["permissionLevel"]}
                for s in sites
            ],
        }


@router.get("/ga4-accounts")
async def list_ga4_accounts():
    """List all GA4 accounts and properties accessible via benny's OAuth."""
    try:
        token = await refresh_access_token(OAUTH_ACCOUNT)
    except Exception as e:
        return {"error": f"OAuth failed: {e}"}

    async with httpx.AsyncClient(timeout=30) as client:
        # List accounts
        resp = await client.get(
            "https://analyticsadmin.googleapis.com/v1beta/accountSummaries",
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code != 200:
            return {"error": f"GA4 Admin API {resp.status_code}", "detail": resp.text[:300]}

        data = resp.json()
        summaries = data.get("accountSummaries", [])
        results = []
        for acct in summaries:
            for prop in acct.get("propertySummaries", []):
                results.append({
                    "account": acct.get("displayName", ""),
                    "account_id": acct.get("account", "").split("/")[-1],
                    "property_name": prop.get("displayName", ""),
                    "property_id": prop.get("property", "").split("/")[-1],
                    "property_resource": prop.get("property", ""),
                })
        return {"count": len(results), "properties": results}
