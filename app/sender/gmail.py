import json
import base64
import random
import asyncio
import logging
import httpx
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr, formatdate, make_msgid
from app.shared.config import get_settings
from app.shared.db import db

log = logging.getLogger(__name__)

SCOPES = (
    "https://www.googleapis.com/auth/gmail.send "
    "https://www.googleapis.com/auth/gmail.readonly "
    "https://www.googleapis.com/auth/webmasters.readonly "
    "https://www.googleapis.com/auth/analytics.readonly"
)

# ─── REGISTERED ACCOUNTS ───────────────────────────────────────────
ACCOUNTS = {
    "benny": {
        "display_name": "Benny Torso",
        "email": "benny@optaimum.com",
    },
    "george": {
        "display_name": "George",
        "email": "george@optaimum.com",
    },
}

# ─── ANTI-SPAM: Send pacing ────────────────────────────────────────
MIN_DELAY_SECONDS = 45
MAX_DELAY_SECONDS = 120
MAX_PER_HOUR = 15
MAX_PER_DAY = 40


# ─── TOKEN STORAGE (database-backed, survives deploys) ─────────────

async def _load_tokens(account: str) -> dict | None:
    """Load tokens from database."""
    async with db() as conn:
        row = await conn.fetchrow(
            "SELECT tokens_json FROM gmail_tokens WHERE account = $1", account
        )
        if row:
            return json.loads(row["tokens_json"]) if isinstance(row["tokens_json"], str) else dict(row["tokens_json"])
    return None


async def _save_tokens(account: str, tokens: dict):
    """Save tokens to database (upsert)."""
    verified_email = tokens.get("verified_email", "")
    async with db() as conn:
        await conn.execute("""
            INSERT INTO gmail_tokens (account, tokens_json, verified_email, updated_at)
            VALUES ($1, $2::jsonb, $3, NOW())
            ON CONFLICT (account) DO UPDATE
            SET tokens_json = $2::jsonb, verified_email = $3, updated_at = NOW()
        """, account, json.dumps(tokens), verified_email)


def get_auth_url(account: str = "benny") -> str:
    """Generate OAuth consent URL. Use login_hint to target the right Google account."""
    s = get_settings()
    acct = ACCOUNTS.get(account)
    if not acct:
        raise ValueError(f"Unknown account: {account}")
    params = {
        "client_id": s.google_client_id,
        "redirect_uri": s.google_redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "login_hint": acct["email"],
        "state": account,  # Pass account name through OAuth flow
    }
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"https://accounts.google.com/o/oauth2/auth?{qs}"


async def exchange_code(code: str, account: str = "benny") -> dict:
    """Exchange authorization code for tokens and store in the database."""
    s = get_settings()
    async with httpx.AsyncClient() as client:
        resp = await client.post("https://oauth2.googleapis.com/token", data={
            "code": code,
            "client_id": s.google_client_id,
            "client_secret": s.google_client_secret,
            "redirect_uri": s.google_redirect_uri,
            "grant_type": "authorization_code",
        })
        resp.raise_for_status()
        tokens = resp.json()
        tokens["account"] = account

        # Verify we got the right account
        email = await _get_profile_email(tokens["access_token"])
        tokens["verified_email"] = email

        await _save_tokens(account, tokens)
        return tokens


async def _get_profile_email(access_token: str) -> str:
    """Fetch the Gmail address for a token."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/profile",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code == 200:
            return resp.json().get("emailAddress", "")
    return ""


async def refresh_access_token(account: str) -> str:
    """Refresh the access token for a specific account."""
    tokens = await _load_tokens(account)
    if not tokens:
        raise RuntimeError(f"Account '{account}' not authenticated. Visit /auth/google/{account}")
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise RuntimeError(f"No refresh token for '{account}'. Re-authenticate at /auth/google/{account}")

    s = get_settings()
    async with httpx.AsyncClient() as client:
        resp = await client.post("https://oauth2.googleapis.com/token", data={
            "refresh_token": refresh_token,
            "client_id": s.google_client_id,
            "client_secret": s.google_client_secret,
            "grant_type": "refresh_token",
        })
        resp.raise_for_status()
        new_tokens = resp.json()
        tokens["access_token"] = new_tokens["access_token"]
        await _save_tokens(account, tokens)
        return tokens["access_token"]


async def get_access_token(account: str) -> str:
    """Get a valid access token for a specific account."""
    tokens = await _load_tokens(account)
    if not tokens:
        raise RuntimeError(f"Account '{account}' not authenticated. Visit /auth/google/{account}")
    return await refresh_access_token(account)


async def send_email(to: str, subject: str, body: str,
                     account: str = "benny", reply_to_msg_id: str = "") -> dict:
    """Send a plain-text email via Gmail API from a specific account."""
    acct = ACCOUNTS[account]
    access_token = await get_access_token(account)

    msg = MIMEMultipart("alternative")
    msg["From"] = formataddr((acct["display_name"], acct["email"]))
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="optaimum.com")
    msg["Reply-To"] = formataddr((acct["display_name"], acct["email"]))

    # Anti-spam headers
    msg["List-Unsubscribe"] = f"<mailto:{acct['email']}?subject=unsubscribe>"
    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    # Threading for follow-ups
    if reply_to_msg_id:
        msg["In-Reply-To"] = reply_to_msg_id
        msg["References"] = reply_to_msg_id

    text_part = MIMEText(body, "plain", "utf-8")
    msg.attach(text_part)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    # Exponential backoff: 1s, 2s, 4s, 8s, 16s + jitter
    max_retries = 5
    for attempt in range(max_retries):
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                headers={"Authorization": f"Bearer {access_token}"},
                json={"raw": raw},
            )
            if resp.status_code == 429:
                if attempt < max_retries - 1:
                    delay = (2 ** attempt) + random.uniform(0, 1)
                    log.warning(f"Gmail 429 rate limit, retry {attempt + 1}/{max_retries} in {delay:.1f}s")
                    await asyncio.sleep(delay)
                    continue
                else:
                    log.error("Gmail 429 rate limit exceeded after all retries")
                    raise RuntimeError(f"Gmail rate limited after {max_retries} retries. Back off.")
            resp.raise_for_status()
            result = resp.json()
            return {
                "id": result.get("id", ""),
                "threadId": result.get("threadId", ""),
                "account": account,
                "from": f"{acct['display_name']} <{acct['email']}>",
            }


async def send_with_delay():
    """Random human-like delay between sends."""
    delay = random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS)
    await asyncio.sleep(delay)


async def is_authenticated(account: str = None) -> bool | dict:
    """Check auth status. If account is None, returns status for all accounts."""
    if account:
        tokens = await _load_tokens(account)
        if not tokens:
            return False
        try:
            await get_access_token(account)
            return True
        except Exception:
            return False

    # Return status for all accounts
    status = {}
    for name in ACCOUNTS:
        tokens = await _load_tokens(name)
        if not tokens:
            status[name] = False
            continue
        try:
            await get_access_token(name)
            status[name] = True
        except Exception:
            status[name] = False
    return status


async def check_thread_replies(thread_id: str, account: str = "benny") -> dict:
    """Check if a Gmail thread has replies from the recipient."""
    if not thread_id:
        return {"has_reply": False, "reply_count": 0, "latest_snippet": ""}

    try:
        access_token = await get_access_token(account)
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/threads/{thread_id}",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"format": "metadata", "metadataHeaders": ["From", "Subject"]},
            )
            if resp.status_code != 200:
                return {"has_reply": False, "reply_count": 0, "latest_snippet": ""}

            thread = resp.json()
            messages = thread.get("messages", [])
            acct = ACCOUNTS[account]

            replies = []
            for msg in messages:
                headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
                from_addr = headers.get("From", "")
                if acct["email"] not in from_addr:
                    replies.append(msg)

            return {
                "has_reply": len(replies) > 0,
                "reply_count": len(replies),
                "latest_snippet": replies[-1].get("snippet", "") if replies else "",
            }
    except Exception as e:
        log.warning(f"Failed to check thread {thread_id}: {e}")
        return {"has_reply": False, "reply_count": 0, "latest_snippet": ""}


async def check_replies_for_leads(account: str = "benny") -> list[dict]:
    """Scan recent sent emails for replies. Returns leads that replied."""
    try:
        access_token = await get_access_token(account)
        acct = ACCOUNTS[account]
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                headers={"Authorization": f"Bearer {access_token}"},
                params={
                    "q": f"in:inbox is:unread -from:{acct['email']}",
                    "maxResults": 50,
                },
            )
            if resp.status_code != 200:
                return []

            messages = resp.json().get("messages", [])
            replies = []
            for msg_ref in messages[:20]:
                msg_resp = await client.get(
                    f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_ref['id']}",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params={"format": "metadata", "metadataHeaders": ["From", "Subject", "In-Reply-To"]},
                )
                if msg_resp.status_code != 200:
                    continue
                msg = msg_resp.json()
                headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
                if headers.get("In-Reply-To"):
                    replies.append({
                        "from": headers.get("From", ""),
                        "subject": headers.get("Subject", ""),
                        "snippet": msg.get("snippet", ""),
                        "thread_id": msg.get("threadId", ""),
                        "gmail_id": msg.get("id", ""),
                    })
            return replies
    except Exception as e:
        log.warning(f"Failed to check replies: {e}")
        return []


def list_accounts() -> list[dict]:
    """List all registered accounts (auth status checked async separately)."""
    result = []
    for name, acct in ACCOUNTS.items():
        result.append({
            "account": name,
            "display_name": acct["display_name"],
            "email": acct["email"],
        })
    return result
