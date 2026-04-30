import json
import base64
import random
import asyncio
import logging
import httpx
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from app.shared.config import get_settings

log = logging.getLogger(__name__)

TOKENS_DIR = Path("gmail_tokens")
TOKENS_DIR.mkdir(exist_ok=True)

SCOPES = "https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/gmail.readonly"

# ─── REGISTERED ACCOUNTS ───────────────────────────────────────────
ACCOUNTS = {
    "benny": {
        "display_name": "Benny Torso",
        "email": "benny@optaimum.com",
        "token_file": "benny.json",
    },
    "george": {
        "display_name": "George",
        "email": "george@optaimum.com",
        "token_file": "george.json",
    },
}

# ─── ANTI-SPAM: Send pacing ────────────────────────────────────────
MIN_DELAY_SECONDS = 45
MAX_DELAY_SECONDS = 120
MAX_PER_HOUR = 15
MAX_PER_DAY = 40


def _token_path(account: str) -> Path:
    acct = ACCOUNTS.get(account)
    if not acct:
        raise ValueError(f"Unknown account: {account}. Available: {list(ACCOUNTS.keys())}")
    return TOKENS_DIR / acct["token_file"]


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
    """Exchange authorization code for tokens and store under the account name."""
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
        _token_path(account).write_text(json.dumps(tokens))

        # Verify we got the right account
        email = await _get_profile_email(tokens["access_token"])
        tokens["verified_email"] = email
        _token_path(account).write_text(json.dumps(tokens))
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
    path = _token_path(account)
    if not path.exists():
        raise RuntimeError(f"Account '{account}' not authenticated. Visit /auth/google/{account}")
    tokens = json.loads(path.read_text())
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
        path.write_text(json.dumps(tokens))
        return tokens["access_token"]


async def get_access_token(account: str) -> str:
    """Get a valid access token for a specific account."""
    path = _token_path(account)
    if not path.exists():
        raise RuntimeError(f"Account '{account}' not authenticated. Visit /auth/google/{account}")
    return await refresh_access_token(account)


async def send_email(to: str, subject: str, body: str,
                     account: str = "benny", reply_to_msg_id: str = "") -> dict:
    """Send a plain-text email via Gmail API from a specific account.

    Anti-spam measures:
    - Proper From with display name
    - Plain text only (no HTML = lower spam score)
    - Proper Message-ID, Date, and Reply-To headers
    - List-Unsubscribe header
    - Threading via In-Reply-To / References for follow-ups
    """
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
        path = _token_path(account)
        if not path.exists():
            return False
        try:
            await get_access_token(account)
            return True
        except Exception:
            return False

    # Return status for all accounts
    status = {}
    for name in ACCOUNTS:
        path = _token_path(name)
        if not path.exists():
            status[name] = False
            continue
        try:
            await get_access_token(name)
            status[name] = True
        except Exception:
            status[name] = False
    return status


async def check_thread_replies(thread_id: str, account: str = "benny") -> dict:
    """Check if a Gmail thread has replies from the recipient.

    Returns: {"has_reply": bool, "reply_count": int, "latest_snippet": str}
    """
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

            # Count messages NOT from us (i.e. replies from recipient)
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
    """Scan recent sent emails for replies. Returns leads that replied.

    Checks Gmail inbox for threads where we sent outreach and got a response.
    """
    try:
        access_token = await get_access_token(account)
        acct = ACCOUNTS[account]
        async with httpx.AsyncClient() as client:
            # Search for replies to our outreach (messages in inbox that are replies)
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
            for msg_ref in messages[:20]:  # Cap at 20 to avoid rate limits
                msg_resp = await client.get(
                    f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_ref['id']}",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params={"format": "metadata", "metadataHeaders": ["From", "Subject", "In-Reply-To"]},
                )
                if msg_resp.status_code != 200:
                    continue
                msg = msg_resp.json()
                headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
                # Only count if it's a reply to something (has In-Reply-To header)
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
    """List all registered accounts and their auth status."""
    result = []
    for name, acct in ACCOUNTS.items():
        path = _token_path(name)
        authenticated = path.exists()
        verified_email = ""
        if authenticated:
            try:
                tokens = json.loads(path.read_text())
                verified_email = tokens.get("verified_email", "")
            except Exception:
                pass
        result.append({
            "account": name,
            "display_name": acct["display_name"],
            "email": acct["email"],
            "authenticated": authenticated,
            "verified_email": verified_email,
        })
    return result
