"""Pre-send deliverability guard.

Checks every email before it leaves:
1. Spam phrase scan (words/patterns that trigger filters)
2. Bounce-risk scoring against verification cache
3. Sending velocity check (hourly + daily caps)
4. Content quality checks (length, personalization ratio)
"""
import re
import httpx
from datetime import datetime, timezone
from app.shared.db import db
from app.shared.config import get_settings

# Phrases that raise spam scores across major providers
SPAM_TRIGGERS = [
    # Urgency/pressure
    r"\bact now\b", r"\blimited time\b", r"\bdon'?t miss\b", r"\bhurry\b",
    r"\bexpires?\b", r"\burgent\b", r"\bimmediately\b",
    # Money/free
    r"\bfree(?:\s+(?:trial|gift|offer))?\b", r"\bno cost\b", r"\b100%\s+free\b",
    r"\bdiscount\b", r"\bspecial offer\b", r"\bexclusive deal\b",
    # Spammy sales
    r"\bbuy now\b", r"\border now\b", r"\bclick here\b", r"\bclick below\b",
    r"\bunsubscribe\b.*\bbody\b",  # unsubscribe in body text (header is fine)
    # ALL CAPS words (5+ chars, excludes normal acronyms)
    r"(?<!\w)[A-Z]{5,}(?!\w)",
    # Excessive punctuation
    r"[!]{2,}", r"[?]{2,}", r"\$\$",
]
# Compile most patterns case-insensitive, but ALL CAPS detector must be case-sensitive
SPAM_PATTERNS = []
for p in SPAM_TRIGGERS:
    if "[A-Z]" in p:
        SPAM_PATTERNS.append(re.compile(p))  # case-sensitive for caps detection
    else:
        SPAM_PATTERNS.append(re.compile(p, re.IGNORECASE))

# Minimum content quality thresholds
MIN_BODY_LENGTH = 80
MAX_BODY_LENGTH = 2000
MIN_SUBJECT_LENGTH = 10
MAX_SUBJECT_LENGTH = 120


class DeliverabilityResult:
    def __init__(self):
        self.passed = True
        self.warnings: list[str] = []
        self.blocks: list[str] = []
        self.spam_score = 0.0  # 0-10, higher = more likely spam

    def warn(self, msg: str):
        self.warnings.append(msg)

    def block(self, msg: str):
        self.blocks.append(msg)
        self.passed = False

    def to_dict(self):
        return {
            "passed": self.passed,
            "spam_score": round(self.spam_score, 1),
            "warnings": self.warnings,
            "blocks": self.blocks,
        }


def scan_spam_phrases(subject: str, body: str) -> tuple[list[str], float]:
    """Scan subject + body for spam trigger phrases. Returns (matches, score_delta)."""
    text = f"{subject} {body}"
    matches = []
    for pattern in SPAM_PATTERNS:
        if pattern.search(text):
            # Store the pattern description, not every fragment
            matches.append(pattern.pattern)
    # Each unique pattern match adds 0.5 to spam score, capped at 5
    score = min(len(matches) * 0.5, 5.0)
    return matches, score


def check_content_quality(subject: str, body: str) -> tuple[list[str], float]:
    """Check email content meets quality thresholds."""
    issues = []
    score = 0.0

    if len(body) < MIN_BODY_LENGTH:
        issues.append(f"Body too short ({len(body)} chars, min {MIN_BODY_LENGTH})")
        score += 1.0
    if len(body) > MAX_BODY_LENGTH:
        issues.append(f"Body too long ({len(body)} chars, max {MAX_BODY_LENGTH})")
        score += 0.5
    if len(subject) < MIN_SUBJECT_LENGTH:
        issues.append(f"Subject too short ({len(subject)} chars)")
        score += 1.0
    if len(subject) > MAX_SUBJECT_LENGTH:
        issues.append(f"Subject too long ({len(subject)} chars)")
        score += 0.5

    # Check for personalization (should have at least one proper noun or company name)
    # Heuristic: if body starts with "Hi " or "Hey " followed by a capitalized word
    if not re.match(r"^(Hi|Hey|Hello|Dear)\s+[A-Z]", body):
        issues.append("Missing personal greeting")
        score += 0.5

    # Check link density (too many links = spam signal)
    links = re.findall(r"https?://", body)
    if len(links) > 2:
        issues.append(f"Too many links ({len(links)}, keep to 1-2)")
        score += len(links) * 0.3

    return issues, score


async def check_velocity(account: str) -> tuple[bool, dict]:
    """Check sending velocity against hourly and daily caps."""
    from .gmail import MAX_PER_HOUR, MAX_PER_DAY

    async with db() as conn:
        hourly = await conn.fetchval("""
            SELECT COUNT(*) FROM outreach_emails
            WHERE sent_at > NOW() - INTERVAL '1 hour' AND status = 'sent'
        """)
        daily = await conn.fetchval("""
            SELECT COUNT(*) FROM outreach_emails
            WHERE sent_at > NOW() - INTERVAL '24 hours' AND status = 'sent'
        """)

    stats = {
        "hourly_sent": hourly or 0,
        "hourly_limit": MAX_PER_HOUR,
        "hourly_remaining": MAX_PER_HOUR - (hourly or 0),
        "daily_sent": daily or 0,
        "daily_limit": MAX_PER_DAY,
        "daily_remaining": MAX_PER_DAY - (daily or 0),
    }
    ok = stats["hourly_remaining"] > 0 and stats["daily_remaining"] > 0
    return ok, stats


async def check_bounce_rate() -> tuple[float, int]:
    """Calculate recent bounce rate. Returns (rate, total_bounces)."""
    async with db() as conn:
        row = await conn.fetchrow("""
            SELECT
                COUNT(*) FILTER (WHERE status = 'bounced') as bounces,
                COUNT(*) as total
            FROM outreach_emails
            WHERE sent_at > NOW() - INTERVAL '7 days'
        """)
    if not row or row["total"] == 0:
        return 0.0, 0
    rate = row["bounces"] / row["total"]
    return rate, row["bounces"]


async def verify_email_external(email: str) -> dict:
    """Verify an email address using NeverBounce or fallback to basic checks.

    Returns: {"status": "valid"|"invalid"|"risky"|"unknown", "cached": bool}
    """
    # Check cache first
    async with db() as conn:
        cached = await conn.fetchrow("""
            SELECT status, checked_at FROM email_verification_cache
            WHERE email = $1 AND checked_at > NOW() - INTERVAL '30 days'
        """, email)
        if cached:
            return {"status": cached["status"], "cached": True}

    s = get_settings()
    status = "unknown"

    # Try NeverBounce if configured
    if s.neverbounce_api_key:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://api.neverbounce.com/v4/single/check",
                    params={"key": s.neverbounce_api_key, "email": email},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    nb_result = data.get("result", "unknown")
                    status_map = {
                        "valid": "valid",
                        "invalid": "invalid",
                        "disposable": "invalid",
                        "catchall": "risky",
                        "unknown": "unknown",
                    }
                    status = status_map.get(nb_result, "unknown")
        except Exception:
            pass

    # Fallback: basic domain validation
    if status == "unknown":
        domain = email.split("@")[-1] if "@" in email else ""
        if not domain or "." not in domain:
            status = "invalid"
        elif domain in ("gmail.com", "yahoo.com", "hotmail.com", "outlook.com"):
            status = "risky"  # Free email = less likely a business contact
        else:
            status = "valid"  # Custom domain = probably legit business

    # Cache the result
    async with db() as conn:
        await conn.execute("""
            INSERT INTO email_verification_cache (email, status, checked_at)
            VALUES ($1, $2, NOW())
            ON CONFLICT (email) DO UPDATE SET status = $2, checked_at = NOW()
        """, email, status)

    return {"status": status, "cached": False}


async def pre_send_check(to: str, subject: str, body: str, account: str) -> DeliverabilityResult:
    """Run all deliverability checks before sending an email.

    This is the main entry point. Call this before every send.
    Returns a DeliverabilityResult with pass/fail and details.
    """
    result = DeliverabilityResult()

    # 1. Spam phrase scan
    spam_matches, spam_score = scan_spam_phrases(subject, body)
    result.spam_score += spam_score
    if spam_matches:
        result.warn(f"Spam trigger phrases found: {spam_matches[:5]}")
    if spam_score >= 3.0:
        result.block(f"Spam score too high ({spam_score}). Rewrite the email.")

    # 2. Content quality
    quality_issues, quality_score = check_content_quality(subject, body)
    result.spam_score += quality_score
    for issue in quality_issues:
        result.warn(issue)

    # 3. Velocity check
    velocity_ok, velocity_stats = await check_velocity(account)
    if not velocity_ok:
        if velocity_stats["hourly_remaining"] <= 0:
            result.block(f"Hourly send limit reached ({velocity_stats['hourly_sent']}/{velocity_stats['hourly_limit']})")
        if velocity_stats["daily_remaining"] <= 0:
            result.block(f"Daily send limit reached ({velocity_stats['daily_sent']}/{velocity_stats['daily_limit']})")

    # 4. Bounce rate check
    bounce_rate, total_bounces = await check_bounce_rate()
    if bounce_rate > 0.05:
        result.block(f"Bounce rate {bounce_rate:.1%} exceeds 5% threshold ({total_bounces} bounces in 7 days). Pause and verify remaining leads.")
    elif bounce_rate > 0.02:
        result.warn(f"Bounce rate {bounce_rate:.1%} approaching danger zone (>2%)")

    # 5. Email verification
    verification = await verify_email_external(to)
    if verification["status"] == "invalid":
        result.block(f"Email '{to}' failed verification (status: invalid)")
    elif verification["status"] == "risky":
        result.warn(f"Email '{to}' is risky (free email provider or catchall)")
        result.spam_score += 0.5

    # Final spam score threshold
    if result.spam_score >= 5.0:
        result.block(f"Combined spam score {result.spam_score:.1f}/10 too high")

    return result
