"""Lead scoring based on OptAImum ICP criteria."""


def score_lead(lead: dict) -> tuple[int, list[str]]:
    """Score a lead 0-10 and return signal reasons.

    ICP: SMBs (10-200 employees), SaaS/agencies/professional services,
    decision-makers (founders, VPs, directors, heads of).
    """
    score = 0
    signals = []

    # Company size (sweet spot: 10-200)
    emp = lead.get("employee_count", 0)
    if 10 <= emp <= 200:
        score += 3
        signals.append(f"SMB sweet spot ({emp} employees)")
    elif 5 <= emp < 10:
        score += 1
        signals.append("Small team - potential fit")
    elif 200 < emp <= 500:
        score += 1
        signals.append("Mid-market - may have budget")

    # Industry match
    industry = (lead.get("industry") or "").lower()
    high_fit = ["saas", "software", "agency", "marketing", "consulting", "professional services"]
    med_fit = ["technology", "sales", "e-commerce", "fintech", "real estate"]
    if any(i in industry for i in high_fit):
        score += 2
        signals.append(f"High-fit industry: {industry}")
    elif any(i in industry for i in med_fit):
        score += 1
        signals.append(f"Medium-fit industry: {industry}")

    # Title/role match
    title = (lead.get("contact_title") or "").lower()
    founder_titles = ["founder", "ceo", "co-founder", "owner"]
    exec_titles = ["vp", "vice president", "director", "head of", "cto", "cmo", "cro"]
    if any(t in title for t in founder_titles):
        score += 3
        signals.append(f"Decision-maker: {lead.get('contact_title')}")
    elif any(t in title for t in exec_titles):
        score += 2
        signals.append(f"Executive: {lead.get('contact_title')}")

    # Email verified
    if lead.get("email_verified"):
        score += 1
        signals.append("Email verified")

    # Buying signals from metadata
    for signal in lead.get("signals", []):
        sig = signal.lower() if isinstance(signal, str) else ""
        if "hiring" in sig:
            score += 1
            signals.append("Hiring signal detected")
        if "funding" in sig:
            score += 1
            signals.append("Recent funding")

    return min(score, 10), signals
