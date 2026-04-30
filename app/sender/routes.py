import json
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from app.shared.db import db, lead_db
from app.shared.config import get_settings
from app.shared.notify import send_telegram, notify, alert_hot_lead, campaign_report
from .personalize import personalize_with_llm
from .instantly import add_leads_to_campaign, get_campaign_summary
from .catchflow_sequences import build_sequence, build_sequence_sync, generate_batch_emails
from .gmail import send_email, send_with_delay, is_authenticated, MAX_PER_DAY, check_thread_replies, check_replies_for_leads
from .deliverability import pre_send_check

router = APIRouter(prefix="/sender", tags=["sender"])


class CreateCampaignRequest(BaseModel):
    name: str
    target_criteria: dict = {}
    instantly_campaign_id: str = ""


class SendBatchRequest(BaseModel):
    campaign_id: int
    instantly_campaign_id: str
    limit: int = 25


class LogReplyRequest(BaseModel):
    lead_email: str
    reply_text: str
    sentiment: str = "neutral"  # positive, neutral, negative


@router.post("/campaigns")
async def create_campaign(req: CreateCampaignRequest):
    """Create a new outreach campaign."""
    sequence = [
        {"step": 1, "delay_days": 0, "type": "initial"},
        {"step": 2, "delay_days": 3, "type": "follow_up"},
        {"step": 3, "delay_days": 7, "type": "breakup"},
    ]
    async with db() as conn:
        row = await conn.fetchrow("""
            INSERT INTO campaigns (name, target_criteria, sequence)
            VALUES ($1, $2::jsonb, $3::jsonb)
            RETURNING id, name, status
        """, req.name, str(req.target_criteria), str(sequence))
    return dict(row)


@router.post("/send-batch")
async def send_batch(req: SendBatchRequest):
    """Pull qualified leads, personalize emails, and queue for sending."""
    s = get_settings()
    async with db() as conn:
        # Get qualified leads not yet emailed
        leads = await conn.fetch("""
            SELECT l.id, l.company_name, l.domain, l.contact_name,
                   l.contact_title, l.email, l.score, l.signals, l.industry
            FROM leads l
            WHERE l.status = 'qualified' AND l.email_verified = TRUE
            AND NOT EXISTS (
                SELECT 1 FROM outreach_emails oe WHERE oe.lead_id = l.id
            )
            ORDER BY l.score DESC
            LIMIT $1
        """, min(req.limit, s.sender_daily_limit))

        if not leads:
            return {"status": "no_leads", "message": "No qualified leads ready for outreach"}

        instantly_leads = []
        emails_queued = 0

        for lead in leads:
            lead_dict = dict(lead)
            personalized = await personalize_with_llm(lead_dict, step=1)

            # Store the email record
            await conn.execute("""
                INSERT INTO outreach_emails (lead_id, campaign_id, step, subject, body, status)
                VALUES ($1, $2, 1, $3, $4, 'queued')
            """, lead["id"], req.campaign_id, personalized["subject"], personalized["body"])

            # Update lead status
            await conn.execute("""
                UPDATE leads SET status = 'sent', updated_at = NOW() WHERE id = $1
            """, lead["id"])

            # Log pipeline event
            await conn.execute("""
                INSERT INTO pipeline_events (lead_id, event_type, agent, metadata)
                VALUES ($1, 'emailed', 'sender', '{"step":1}'::jsonb)
            """, lead["id"])

            # Prepare for Instantly
            name_parts = (lead["contact_name"] or "").split(maxsplit=1)
            instantly_leads.append({
                "email": lead["email"],
                "first_name": name_parts[0] if name_parts else "",
                "last_name": name_parts[1] if len(name_parts) > 1 else "",
                "company_name": lead["company_name"] or "",
                "personalization": personalized["body"],
            })
            emails_queued += 1

        # Push to Instantly if configured
        instantly_result = {}
        if req.instantly_campaign_id and s.instantly_api_key:
            instantly_result = await add_leads_to_campaign(
                req.instantly_campaign_id, instantly_leads
            )

    await send_telegram(
        f"*Sender queued {emails_queued} emails*\n"
        f"Campaign ID: {req.campaign_id}\n"
        f"Top score: {leads[0]['score']}/10"
    )

    return {
        "queued": emails_queued,
        "campaign_id": req.campaign_id,
        "instantly": instantly_result,
    }


@router.post("/reply")
async def log_reply(req: LogReplyRequest):
    """Log an email reply and alert if positive."""
    async with db() as conn:
        lead = await conn.fetchrow(
            "SELECT id, company_name, contact_name, score FROM leads WHERE email = $1",
            req.lead_email,
        )
        if not lead:
            raise HTTPException(404, "Lead not found")

        await conn.execute("""
            UPDATE leads SET status = 'replied', updated_at = NOW() WHERE id = $1
        """, lead["id"])

        await conn.execute("""
            UPDATE outreach_emails SET status = 'replied', replied_at = NOW(),
                reply_text = $1 WHERE lead_id = $2 AND status = 'sent'
        """, req.reply_text, lead["id"])

        await conn.execute("""
            INSERT INTO pipeline_events (lead_id, event_type, agent, metadata)
            VALUES ($1, 'replied', 'sender', $2::jsonb)
        """, lead["id"], f'{{"sentiment":"{req.sentiment}"}}')

    if req.sentiment == "positive":
        await alert_hot_lead({
            **dict(lead),
            "email": req.lead_email,
            "reply_text": req.reply_text,
        })

    return {"status": "logged", "lead_id": lead["id"], "sentiment": req.sentiment}


@router.get("/campaigns/{campaign_id}/stats")
async def campaign_stats(campaign_id: int):
    """Get campaign performance stats."""
    async with db() as conn:
        stats = await conn.fetchrow("""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE status = 'sent') as sent,
                COUNT(*) FILTER (WHERE status = 'opened') as opened,
                COUNT(*) FILTER (WHERE status = 'replied') as replied,
                COUNT(*) FILTER (WHERE status = 'bounced') as bounced
            FROM outreach_emails WHERE campaign_id = $1
        """, campaign_id)
    return dict(stats)


@router.post("/catchflow/preview")
async def preview_catchflow_emails(
    limit: int = Query(5, le=20),
    status_filter: str = Query("needs_email", description="Lead status to target"),
):
    """Preview personalized CatchFlow emails for seafood leads WITHOUT sending."""
    async with lead_db() as upstream:
        leads = await upstream.fetch("""
            SELECT id, company, contact_name, email, species, location, source
            FROM cf_outreach_leads
            WHERE status = $1 AND email IS NOT NULL AND email != ''
            ORDER BY created_at ASC
            LIMIT $2
        """, status_filter, limit)

    previews = []
    for lead in leads:
        lead_dict = dict(lead)
        sequence = await build_sequence(lead_dict)
        previews.append({
            "lead": {
                "company": lead["company"],
                "contact": lead["contact_name"],
                "email": lead["email"],
                "location": lead["location"],
                "species": lead["species"],
            },
            "emails": sequence,
        })

    return {"count": len(previews), "previews": previews}


@router.post("/catchflow/send")
async def send_catchflow_batch(
    limit: int = Query(10, le=50),
    step: int = Query(1, ge=1, le=3, description="Which sequence step to send"),
    account: str = Query("benny", description="Gmail account to send from: 'benny' or 'george'"),
    dry_run: bool = Query(False, description="If true, queue but don't send via Gmail"),
):
    """Send personalized CatchFlow emails to seafood leads via Gmail.

    Pulls leads from upstream DB, generates personalized sequences,
    sends step N via Gmail, and tracks everything.
    """
    if not dry_run and not await is_authenticated(account):
        raise HTTPException(400, f"Gmail account '{account}' not authenticated. Visit /auth/google/{account}")

    s = get_settings()
    actual_limit = min(limit, s.sender_daily_limit)

    if step == 1:
        # Step 1: Pull fresh leads from upstream that haven't been emailed
        async with lead_db() as upstream:
            leads = await upstream.fetch("""
                SELECT id, company, contact_name, email, species, location, source
                FROM cf_outreach_leads
                WHERE status = 'needs_email' AND email IS NOT NULL AND email != ''
                ORDER BY created_at ASC
                LIMIT $1
            """, actual_limit)
    else:
        # Steps 2-3: Pull leads we've already emailed at the previous step
        async with db() as conn:
            rows = await conn.fetch("""
                SELECT l.id as local_id, l.company_name as company, l.contact_name,
                       l.email, l.industry, l.domain as location
                FROM leads l
                JOIN outreach_emails oe ON oe.lead_id = l.id
                WHERE oe.step = $1 AND oe.status = 'sent'
                AND l.status = 'sent'
                AND oe.sent_at < NOW() - INTERVAL '1 day' * $2
                AND NOT EXISTS (
                    SELECT 1 FROM outreach_emails oe2
                    WHERE oe2.lead_id = l.id AND oe2.step = $3
                )
                ORDER BY l.score DESC
                LIMIT $4
            """, step - 1, 3 if step == 2 else 7, step, actual_limit)
            leads = rows

    if not leads:
        return {"status": "no_leads", "step": step, "message": f"No leads ready for step {step}"}

    # Anti-spam: enforce daily send cap
    async with db() as conn:
        sent_today = await conn.fetchval("""
            SELECT COUNT(*) FROM outreach_emails
            WHERE sent_at > NOW() - INTERVAL '24 hours' AND status = 'sent'
        """)
    remaining_today = MAX_PER_DAY - (sent_today or 0)
    if remaining_today <= 0 and not dry_run:
        return {"status": "daily_limit", "message": f"Already sent {sent_today} today. Cap is {MAX_PER_DAY}."}
    actual_leads = leads[:min(len(leads), remaining_today)]

    sent_count = 0
    failed_count = 0
    results = []

    for i, lead in enumerate(actual_leads):
        lead_dict = dict(lead)

        # AI-personalized sequence (unique opener per lead)
        sequence = await build_sequence(lead_dict)
        email_data = sequence[step - 1]

        # Import lead to local DB if step 1
        local_lead_id = None
        if step == 1:
            async with db() as conn:
                row = await conn.fetchrow("""
                    INSERT INTO leads (company_name, domain, contact_name, email,
                        industry, source, score, signals, status, email_verified)
                    VALUES ($1, $2, $3, $4, 'seafood', 'catchflow', 5, '[]'::jsonb, 'sent', TRUE)
                    ON CONFLICT (email) DO UPDATE SET status = 'sent', updated_at = NOW()
                    RETURNING id
                """, lead_dict.get("company", ""), lead_dict.get("location", ""),
                    lead_dict.get("contact_name", ""), lead_dict["email"])
                local_lead_id = row["id"]
        else:
            local_lead_id = lead_dict.get("local_id", lead_dict.get("id"))

        # Pre-send deliverability check
        check = await pre_send_check(
            to=lead_dict["email"],
            subject=email_data["subject"],
            body=email_data["body"],
            account=account,
        )
        if not check.passed and not dry_run:
            failed_count += 1
            results.append({
                "email": lead_dict["email"],
                "company": lead_dict.get("company", lead_dict.get("company_name")),
                "step": step,
                "subject": email_data["subject"],
                "sent": False,
                "blocked_reason": check.blocks,
                "spam_score": check.spam_score,
            })
            continue

        # Send via Gmail (or dry run)
        gmail_result = {}
        if not dry_run:
            try:
                # For follow-ups, check if they already replied (skip if so)
                reply_to = ""
                if step > 1:
                    async with db() as conn:
                        prev = await conn.fetchrow("""
                            SELECT gmail_thread_id, gmail_msg_id FROM outreach_emails
                            WHERE lead_id = $1 AND step = $2 AND status = 'sent'
                            ORDER BY sent_at DESC LIMIT 1
                        """, local_lead_id, step - 1)
                        if prev and prev["gmail_thread_id"]:
                            thread_status = await check_thread_replies(prev["gmail_thread_id"], account)
                            if thread_status["has_reply"]:
                                # They replied - skip this follow-up, mark as replied
                                await conn.execute("""
                                    UPDATE leads SET status = 'replied', updated_at = NOW() WHERE id = $1
                                """, local_lead_id)
                                results.append({
                                    "email": lead_dict["email"],
                                    "company": lead_dict.get("company", lead_dict.get("company_name")),
                                    "step": step,
                                    "sent": False,
                                    "skipped_reason": "recipient_already_replied",
                                    "snippet": thread_status["latest_snippet"][:100],
                                })
                                continue
                            reply_to = prev["gmail_msg_id"] or ""

                gmail_result = await send_email(
                    to=lead_dict["email"],
                    subject=email_data["subject"],
                    body=email_data["body"],
                    account=account,
                    reply_to_msg_id=reply_to,
                )
                sent_count += 1
            except Exception as e:
                failed_count += 1
                gmail_result = {"error": str(e)}
        else:
            sent_count += 1

        # Track in local DB
        async with db() as conn:
            await conn.execute("""
                INSERT INTO outreach_emails (lead_id, step, subject, body, status, sent_at,
                    personalization)
                VALUES ($1, $2, $3, $4, $5, NOW(), $6::jsonb)
            """, local_lead_id, step, email_data["subject"], email_data["body"],
                "sent" if not gmail_result.get("error") else "failed",
                json.dumps({"gmail_id": gmail_result.get("id", ""),
                            "thread_id": gmail_result.get("threadId", "")}))

            await conn.execute("""
                INSERT INTO pipeline_events (lead_id, event_type, agent, metadata)
                VALUES ($1, 'emailed', 'sender', $2::jsonb)
            """, local_lead_id, json.dumps({
                "step": step,
                "gmail_id": gmail_result.get("id", ""),
                "from": "Benny Torso <benny@optaimum.com>",
            }))

        # Update upstream status if step 1
        if step == 1:
            try:
                async with lead_db() as upstream:
                    await upstream.execute("""
                        UPDATE cf_outreach_leads SET status = 'contacted',
                            contacted_at = NOW(), updated_at = NOW()
                        WHERE id = $1
                    """, lead_dict["id"])
            except Exception:
                pass

        results.append({
            "email": lead_dict["email"],
            "company": lead_dict.get("company", lead_dict.get("company_name")),
            "step": step,
            "subject": email_data["subject"],
            "sent": not gmail_result.get("error"),
        })

        # Anti-spam: human-like delay between sends (45-120s)
        if not dry_run and i < len(actual_leads) - 1:
            await send_with_delay()

    from .gmail import ACCOUNTS
    acct = ACCOUNTS.get(account, {})
    from_addr = f"{acct.get('display_name', account)} <{acct.get('email', '')}>"
    await campaign_report(step, sent_count, failed_count, account, from_addr)

    return {
        "step": step,
        "account": account,
        "from": f"{acct.get('display_name', account)} <{acct.get('email', '')}>",
        "sent": sent_count,
        "failed": failed_count,
        "dry_run": dry_run,
        "daily_sent": (sent_today or 0) + sent_count,
        "daily_remaining": remaining_today - sent_count,
        "results": results,
    }


@router.get("/stats")
async def sender_stats():
    """Get overall Sender stats."""
    async with db() as conn:
        stats = await conn.fetchrow("""
            SELECT
                COUNT(*) as total_emails,
                COUNT(*) FILTER (WHERE status = 'sent') as sent,
                COUNT(*) FILTER (WHERE status = 'opened') as opened,
                COUNT(*) FILTER (WHERE status = 'replied') as replied,
                COUNT(*) FILTER (WHERE status = 'bounced') as bounced,
                COUNT(DISTINCT lead_id) as unique_leads,
                COUNT(DISTINCT campaign_id) as campaigns
            FROM outreach_emails
        """)
    return dict(stats)


@router.post("/check-replies")
async def monitor_replies(
    account: str = Query("benny", description="Gmail account to check"),
):
    """Scan Gmail inbox for replies to outreach emails and update lead status.

    This should run on a cron (every 2 hours). It:
    1. Reads inbox for replies to our threads
    2. Matches replies to leads in the DB
    3. Updates lead status to 'replied'
    4. Alerts on positive signals
    """
    if not await is_authenticated(account):
        raise HTTPException(400, f"Gmail account '{account}' not authenticated")

    replies = await check_replies_for_leads(account)
    matched = 0
    new_replies = []

    async with db() as conn:
        for reply in replies:
            # Extract sender email from "Name <email>" format
            from_str = reply["from"]
            email_match = from_str.split("<")[-1].rstrip(">") if "<" in from_str else from_str
            email_match = email_match.strip()

            lead = await conn.fetchrow(
                "SELECT id, company_name, contact_name, score FROM leads WHERE email = $1 AND status != 'replied'",
                email_match,
            )
            if not lead:
                continue

            matched += 1
            await conn.execute("""
                UPDATE leads SET status = 'replied', updated_at = NOW() WHERE id = $1
            """, lead["id"])

            await conn.execute("""
                UPDATE outreach_emails SET status = 'replied', replied_at = NOW()
                WHERE lead_id = $1 AND status = 'sent'
            """, lead["id"])

            await conn.execute("""
                INSERT INTO pipeline_events (lead_id, event_type, agent, metadata)
                VALUES ($1, 'replied', 'sender', $2::jsonb)
            """, lead["id"], json.dumps({
                "source": "gmail_monitor",
                "snippet": reply["snippet"][:200],
            }))

            new_replies.append({
                "lead_id": lead["id"],
                "company": lead["company_name"],
                "contact": lead["contact_name"],
                "snippet": reply["snippet"][:200],
            })

    if new_replies:
        names = ", ".join(r["company"] for r in new_replies[:5])
        await notify(
            f"*Reply Monitor found {matched} new replies*\n"
            f"Companies: {names}"
        )

    return {"checked": len(replies), "matched": matched, "new_replies": new_replies}


@router.post("/deliverability/check")
async def check_deliverability(
    email: str = Query(..., description="Email address to check"),
    subject: str = Query("Test subject line", description="Subject to scan"),
    body: str = Query("Test body content", description="Body to scan"),
    account: str = Query("benny"),
):
    """Run a deliverability check without sending. Use to pre-screen emails."""
    result = await pre_send_check(email, subject, body, account)
    return result.to_dict()


@router.get("/deliverability/status")
async def deliverability_status():
    """Get current deliverability health metrics."""
    from .deliverability import check_bounce_rate, check_velocity
    bounce_rate, total_bounces = await check_bounce_rate()
    _, velocity = await check_velocity("benny")

    async with db() as conn:
        verification_stats = await conn.fetchrow("""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE status = 'valid') as valid,
                COUNT(*) FILTER (WHERE status = 'invalid') as invalid,
                COUNT(*) FILTER (WHERE status = 'risky') as risky
            FROM email_verification_cache
        """) or {"total": 0, "valid": 0, "invalid": 0, "risky": 0}

    return {
        "bounce_rate": round(bounce_rate, 4),
        "total_bounces_7d": total_bounces,
        "bounce_health": "good" if bounce_rate < 0.02 else "warning" if bounce_rate < 0.05 else "critical",
        "velocity": velocity,
        "verification_cache": dict(verification_stats) if verification_stats else {},
    }
