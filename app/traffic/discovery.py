"""Discovery endpoints for GSC and GA4 properties.

Lets the operator see which properties are accessible via OAuth,
trying both gmail accounts (benny and george).
"""
import logging
import httpx
from fastapi import APIRouter, Query
from app.sender.gmail import refresh_access_token

log = logging.getLogger(__name__)

router = APIRouter(prefix="/discover", tags=["Discovery"])


@router.get("/gsc-sites")
async def list_gsc_sites(account: str = Query("benny", description="OAuth account: benny or george")):
    """List all Search Console properties accessible via OAuth."""
    try:
        token = await refresh_access_token(account)
    except Exception as e:
        return {"error": f"OAuth failed for {account}: {e}"}

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            "https://searchconsole.googleapis.com/webmasters/v3/sites",
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code != 200:
            return {"error": f"GSC API {resp.status_code}", "account": account, "detail": resp.text[:500]}

        data = resp.json()
        sites = data.get("siteEntry", [])
        return {
            "account": account,
            "count": len(sites),
            "sites": [
                {"url": s["siteUrl"], "permission": s["permissionLevel"]}
                for s in sites
            ],
        }


@router.get("/ga4-accounts")
async def list_ga4_accounts(account: str = Query("benny", description="OAuth account: benny or george")):
    """List all GA4 accounts and properties accessible via OAuth."""
    try:
        token = await refresh_access_token(account)
    except Exception as e:
        return {"error": f"OAuth failed for {account}: {e}"}

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            "https://analyticsadmin.googleapis.com/v1beta/accountSummaries",
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code != 200:
            return {"error": f"GA4 Admin API {resp.status_code}", "account": account, "detail": resp.text[:500]}

        data = resp.json()
        summaries = data.get("accountSummaries", [])
        results = []
        for acct in summaries:
            for prop in acct.get("propertySummaries", []):
                results.append({
                    "account_name": acct.get("displayName", ""),
                    "account_id": acct.get("account", "").split("/")[-1],
                    "property_name": prop.get("displayName", ""),
                    "property_id": prop.get("property", "").split("/")[-1],
                })
        return {"account": account, "count": len(results), "properties": results}
