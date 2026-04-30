"""
CatchFlow Email Sequences - Personalized for seafood industry.

3-step sequence from Benny Torso at CatchFlow:
  Step 1 (Day 0): Value-first intro - specific to their species/location
  Step 2 (Day 3): Social proof + seasonal urgency
  Step 3 (Day 7): Breakup - soft close with free offer

Each email gets a unique AI-generated opening line via Claude Haiku
to maximize personalization and avoid spam pattern detection.
"""
import httpx
from app.shared.config import get_settings
from .catchflow_brain import (
    COMPANY, VALUE_PROPS, MARKET_INTEL,
    get_location_hook, get_species_hook, get_best_pain_point,
    get_seasonal_relevance,
)


async def build_sequence(lead: dict) -> list[dict]:
    """Build a 3-step personalized email sequence for a seafood lead."""
    first_name = _get_first_name(lead)
    company = lead.get("company", lead.get("company_name", "your company"))
    location = lead.get("location", "")
    species = lead.get("species") or []
    species_hook = get_species_hook(species, company)
    location_hook = get_location_hook(location)
    pain = get_best_pain_point(lead)
    city = _extract_city(location)

    # Generate unique personalized opener via Claude
    custom_opener = await _generate_opener(lead, first_name, company, city, species)

    return [
        _step_1(first_name, company, species_hook, custom_opener, pain, city),
        _step_2(first_name, company, species_hook, city),
        _step_3(first_name, company),
    ]


def build_sequence_sync(lead: dict) -> list[dict]:
    """Sync fallback using template-only personalization (no Claude call)."""
    first_name = _get_first_name(lead)
    company = lead.get("company", lead.get("company_name", "your company"))
    location = lead.get("location", "")
    species = lead.get("species") or []
    species_hook = get_species_hook(species, company)
    location_hook = get_location_hook(location)
    pain = get_best_pain_point(lead)
    city = _extract_city(location)

    return [
        _step_1(first_name, company, species_hook, location_hook, pain, city),
        _step_2(first_name, company, species_hook, city),
        _step_3(first_name, company),
    ]


def _get_first_name(lead: dict) -> str:
    name = lead.get("contact_name", "")
    if not name:
        return "there"
    return name.split()[0]


def _extract_city(location: str) -> str:
    if not location:
        return ""
    return location.split(",")[0].strip()


# ─── AI PERSONALIZATION ────────────────────────────────────────────

async def _generate_opener(lead: dict, first_name: str, company: str,
                           city: str, species: list) -> str:
    """Generate a unique, personalized opening line via Claude Haiku.

    This is the #1 anti-spam measure: every email has a unique first
    paragraph that references something specific about the lead.
    Spam filters flag identical content across emails.
    """
    s = get_settings()
    if not s.anthropic_api_key:
        return get_location_hook(lead.get("location", ""))

    species_str = ", ".join(s.title() for s in (species or [])[:3]) or "seafood"
    city_str = city or "Florida"

    prompt = f"""Write a 1-2 sentence personalized email opening for a cold outreach email.

Context:
- Recipient: {first_name}, runs {company} in {city_str}
- They supply: {species_str}
- We are CatchFlow, a seafood price intelligence platform
- Sender is Benny Torso from CatchFlow

Rules:
- Reference their specific location or market
- Sound like a real person, not a template
- No "I hope this finds you well" or generic openers
- Be conversational and specific
- Do NOT use emojis
- Keep it under 30 words
- Do NOT include any greeting like "Hi" or "Hey" - just the opening line itself"""

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": s.anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 80,
                    "temperature": 0.9,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=8,
            )
            if resp.status_code == 200:
                opener = resp.json()["content"][0]["text"].strip()
                # Strip quotes if Claude wraps it
                opener = opener.strip('"').strip("'")
                # Sanitize unicode dashes/quotes that break plain text encoding
                opener = opener.replace("\u2014", "-").replace("\u2013", "-")
                opener = opener.replace("\u2018", "'").replace("\u2019", "'")
                opener = opener.replace("\u201c", '"').replace("\u201d", '"')
                return opener
    except Exception:
        pass

    return get_location_hook(lead.get("location", ""))


# ─── STEP 1: Value-First Intro ─────────────────────────────────────

def _step_1(first_name, company, species_hook, opener, pain, city):
    subject = f"{company} - quick question about {species_hook.replace('your ', '')}"

    body = f"""Hi {first_name},

{opener}

I'm Benny Torso from CatchFlow - we built a seafood price intelligence platform specifically for suppliers like {company}.

Right now, most suppliers are still {pain['pain']}. That means {pain['cost']}. We fix that.

With CatchFlow you can:
- Get your prices in front of buyers who are actively searching
- Stop emailing pricelists - automate it
- Market intelligence you can't get anywhere else

It's free to list. {VALUE_PROPS[3]['proof']}

Would it make sense to get {company} set up this week? Takes about 5 minutes.

Best,
Benny Torso
CatchFlow | Seafood Price Intelligence
benny@optaimum.com"""
    return {"step": 1, "delay_days": 0, "subject": subject, "body": body}


# ─── STEP 2: Social Proof + Seasonal Urgency ───────────────────────

def _step_2(first_name, company, species_hook, city):
    seasonal = get_seasonal_relevance()

    subject = f"Re: {company} - quick question about {species_hook.replace('your ', '')}"

    body = f"""Hi {first_name},

Wanted to follow up quickly. {seasonal}

A few things suppliers tell us after joining CatchFlow:

  "We stopped emailing pricelists manually. Buyers just check our page." - Supplier in Miami
  "We picked up 3 new restaurant accounts in the first month." - Wholesale distributor, Tampa
  "Finally know what competitors are charging. Game changer for our margins." - {city or 'FL'} supplier

Getting {company} listed takes 5 minutes and costs nothing. {species_hook.capitalize()} would be visible to our growing buyer network immediately.

Want me to send you a quick setup link?

Best,
Benny Torso
CatchFlow"""
    return {"step": 2, "delay_days": 3, "subject": subject, "body": body}


# ─── STEP 3: Breakup ───────────────────────────────────────────────

def _step_3(first_name, company):
    subject = f"Closing the loop - {company}"

    body = f"""Hi {first_name},

I know you're busy running {company}, so I'll keep this short.

If getting your prices in front of more buyers isn't a priority right now, totally understand. I'll close this out.

But if timing was the only issue, here's what I'd suggest:

I'll create a free CatchFlow profile for {company} with your current inventory. You don't have to do anything. If buyers start reaching out, great. If not, no harm done.

Either way, I'm here if you ever want to chat seafood pricing.

Best,
Benny Torso
CatchFlow | Seafood Price Intelligence
benny@optaimum.com"""
    return {"step": 3, "delay_days": 7, "subject": subject, "body": body}


# ─── BATCH GENERATION ──────────────────────────────────────────────

async def generate_batch_emails(leads: list[dict]) -> list[dict]:
    """Generate step-1 emails for a batch of leads with AI personalization."""
    results = []
    for lead in leads:
        sequence = await build_sequence(lead)
        results.append({
            "lead_id": lead.get("id"),
            "email": lead.get("email"),
            "company": lead.get("company", lead.get("company_name")),
            "contact_name": lead.get("contact_name"),
            "sequence": sequence,
        })
    return results
