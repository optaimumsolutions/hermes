import httpx
from app.shared.config import get_settings

INSTANTLY_BASE = "https://api.instantly.ai/api/v1"


async def _request(method: str, path: str, **kwargs) -> dict:
    s = get_settings()
    if not s.instantly_api_key:
        return {"error": "No Instantly API key configured"}
    async with httpx.AsyncClient() as client:
        kwargs.setdefault("params", {})
        kwargs["params"]["api_key"] = s.instantly_api_key
        resp = await getattr(client, method)(f"{INSTANTLY_BASE}{path}", **kwargs)
        resp.raise_for_status()
        return resp.json()


async def list_campaigns() -> list[dict]:
    return await _request("get", "/campaign/list")


async def add_leads_to_campaign(campaign_id: str, leads: list[dict]) -> dict:
    """Add leads to an Instantly campaign.

    leads: list of {"email": str, "first_name": str, "last_name": str, "company_name": str, ...}
    """
    return await _request("post", "/lead/add", json={
        "campaign_id": campaign_id,
        "skip_if_in_workspace": True,
        "leads": leads,
    })


async def get_campaign_summary(campaign_id: str) -> dict:
    return await _request("get", "/analytics/campaign/summary", params={
        "campaign_id": campaign_id,
    })


async def get_lead_status(email: str) -> dict:
    return await _request("get", "/lead/get", params={"email": email})
