import httpx
from app.shared.config import get_settings

HUNTER_BASE = "https://api.hunter.io/v2"


async def find_emails(domain: str, limit: int = 5) -> list[dict]:
    """Find emails at a domain using Hunter.io domain search."""
    s = get_settings()
    if not s.hunter_api_key:
        return []
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{HUNTER_BASE}/domain-search", params={
            "domain": domain,
            "api_key": s.hunter_api_key,
            "limit": limit,
        })
        resp.raise_for_status()
        data = resp.json().get("data", {})
        return [
            {
                "email": e["value"],
                "first_name": e.get("first_name", ""),
                "last_name": e.get("last_name", ""),
                "position": e.get("position", ""),
                "confidence": e.get("confidence", 0),
            }
            for e in data.get("emails", [])
        ]


async def verify_email(email: str) -> dict:
    """Verify an email address deliverability."""
    s = get_settings()
    if not s.hunter_api_key:
        return {"status": "unknown", "score": 0}
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{HUNTER_BASE}/email-verifier", params={
            "email": email,
            "api_key": s.hunter_api_key,
        })
        resp.raise_for_status()
        data = resp.json().get("data", {})
        return {
            "status": data.get("status", "unknown"),
            "score": data.get("score", 0),
            "deliverable": data.get("result") == "deliverable",
        }
