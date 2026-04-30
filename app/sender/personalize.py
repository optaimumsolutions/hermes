import httpx
from app.shared.config import get_settings

TEMPLATES = {
    1: {
        "subject": "{company} + AI automation?",
        "body": (
            "Hey {first_name},\n\n"
            "Saw {company} is {signal_hook} — nice.\n\n"
            "Quick question: how many hours/week does your team spend on "
            "{pain_point}?\n\n"
            "We built an AI system that handles that end-to-end for companies "
            "your size. One client cut their {pain_point} time by 70% in the "
            "first month.\n\n"
            "Worth a 10-minute look?\n\n"
            "Best,\nJack"
        ),
    },
    2: {
        "subject": "Re: {company} + AI automation?",
        "body": (
            "Hey {first_name}, bumping this up.\n\n"
            "Just shipped a case study where a {industry} company similar to "
            "{company} automated their entire {pain_point} pipeline.\n\n"
            "Result: 3x output, zero additional headcount.\n\n"
            "Want me to send it over?\n\n"
            "Jack"
        ),
    },
    3: {
        "subject": "Last one - {first_name}",
        "body": (
            "Hey {first_name},\n\n"
            "Totally get it if timing's off. Just wanted to leave this here:\n\n"
            "We help {industry} companies automate {pain_point} using AI agents "
            "that run 24/7. If that ever becomes a priority, I'm an email away.\n\n"
            "No hard feelings either way.\n\n"
            "Jack"
        ),
    },
}

PAIN_POINTS = {
    "saas": "lead generation and outreach",
    "agency": "client prospecting and follow-ups",
    "consulting": "business development and pipeline management",
    "marketing": "campaign management and reporting",
    "default": "manual, repetitive workflows",
}


def get_pain_point(industry: str) -> str:
    industry_lower = (industry or "").lower()
    for key, pain in PAIN_POINTS.items():
        if key in industry_lower:
            return pain
    return PAIN_POINTS["default"]


def build_signal_hook(signals: list) -> str:
    if not signals:
        return "growing fast"
    first = signals[0] if isinstance(signals[0], str) else str(signals[0])
    return first.lower()


async def personalize_with_llm(lead: dict, step: int) -> dict[str, str]:
    """Use Claude to generate a personalized first line. Falls back to template."""
    s = get_settings()
    template = TEMPLATES.get(step, TEMPLATES[1])

    first_name = (lead.get("contact_name") or "there").split()[0]
    company = lead.get("company_name", "your company")
    industry = lead.get("industry", "")
    signals = lead.get("signals", [])

    context = {
        "first_name": first_name,
        "company": company,
        "industry": industry or "tech",
        "signal_hook": build_signal_hook(signals),
        "pain_point": get_pain_point(industry),
    }

    subject = template["subject"].format(**context)
    body = template["body"].format(**context)

    # If Anthropic key available, enhance the first line
    if s.anthropic_api_key and step == 1:
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
                        "max_tokens": 100,
                        "messages": [{"role": "user", "content": (
                            f"Write a personalized cold email opening line (1 sentence) for:\n"
                            f"- Contact: {first_name}, {lead.get('contact_title', '')} at {company}\n"
                            f"- Industry: {industry}\n"
                            f"- Signals: {', '.join(str(s) for s in signals[:3])}\n"
                            f"Be specific, confident, not sycophantic. Lead with value."
                        )}],
                    },
                    timeout=10,
                )
                if resp.status_code == 200:
                    first_line = resp.json()["content"][0]["text"].strip()
                    body = f"Hey {first_name},\n\n{first_line}\n\n" + "\n".join(body.split("\n")[2:])
        except Exception:
            pass  # Fall back to template

    return {"subject": subject, "body": body}
