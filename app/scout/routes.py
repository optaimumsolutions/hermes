from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from app.shared.db import db, lead_db
from app.shared.config import get_settings
from app.shared.notify import send_telegram
from .hunter import find_emails, verify_email
from .scorer import score_lead

router = APIRouter(prefix="/scout", tags=["scout"])


class DomainSearchRequest(BaseModel):
    domain: str
    company_name: str = ""
    industry: str = ""
    employee_count: int = 0


class ManualLeadRequest(BaseModel):
    company_name: str
    domain: str = ""
    contact_name: str = ""
    contact_title: str = ""
    email: str = ""
    industry: str = ""
    employee_count: int = 0
    signals: list[str] = []


@router.post("/discover")
async def discover_leads(req: DomainSearchRequest):
    """Discover leads at a domain via Hunter.io, score them, and store."""
    emails = await find_emails(req.domain)
    if not emails:
        return {"status": "no_results", "domain": req.domain}

    leads_created = []
    async with db() as conn:
        for contact in emails:
            lead_data = {
                "company_name": req.company_name or req.domain,
                "domain": req.domain,
                "contact_name": f"{contact['first_name']} {contact['last_name']}".strip(),
                "contact_title": contact["position"],
                "email": contact["email"],
                "email_verified": contact["confidence"] > 80,
                "industry": req.industry,
                "employee_count": req.employee_count,
                "source": "hunter",
            }
            score, signals = score_lead(lead_data)
            lead_data["score"] = score
            lead_data["signals"] = signals

            row = await conn.fetchrow("""
                INSERT INTO leads (company_name, domain, contact_name, contact_title,
                    email, email_verified, industry, employee_count, source, score, signals, status)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,
                    CASE WHEN $10 >= $12 THEN 'qualified' ELSE 'enriched' END)
                ON CONFLICT (email) DO UPDATE SET
                    score = EXCLUDED.score, signals = EXCLUDED.signals,
                    updated_at = NOW()
                RETURNING id, email, score, status
            """, lead_data["company_name"], lead_data["domain"],
                lead_data["contact_name"], lead_data["contact_title"],
                lead_data["email"], lead_data["email_verified"],
                lead_data["industry"], lead_data["employee_count"],
                lead_data["source"], score, str(signals),
                get_settings().lead_score_threshold)

            if row:
                leads_created.append(dict(row))
                await conn.execute("""
                    INSERT INTO pipeline_events (lead_id, event_type, agent, metadata)
                    VALUES ($1, 'discovered', 'scout', $2::jsonb)
                """, row["id"], f'{{"score":{score},"signals":{len(signals)}}}')

    qualified = [l for l in leads_created if l["status"] == "qualified"]
    if qualified:
        await send_telegram(
            f"*Scout found {len(qualified)} qualified leads* at {req.domain}\n"
            f"Total contacts: {len(leads_created)}"
        )

    return {"domain": req.domain, "total": len(leads_created), "qualified": len(qualified), "leads": leads_created}


@router.post("/add")
async def add_manual_lead(req: ManualLeadRequest):
    """Manually add a lead (from import, scraping, etc.)."""
    lead_data = req.model_dump()
    score, signals = score_lead(lead_data)

    async with db() as conn:
        row = await conn.fetchrow("""
            INSERT INTO leads (company_name, domain, contact_name, contact_title,
                email, industry, employee_count, source, score, signals, status)
            VALUES ($1,$2,$3,$4,$5,$6,$7,'manual',$8,$9::jsonb,
                CASE WHEN $8 >= $10 THEN 'qualified' ELSE 'new' END)
            ON CONFLICT (email) DO NOTHING
            RETURNING id, score, status
        """, req.company_name, req.domain, req.contact_name, req.contact_title,
            req.email, req.industry, req.employee_count, score, str(signals),
            get_settings().lead_score_threshold)

    if not row:
        raise HTTPException(409, "Lead already exists")
    return {"id": row["id"], "score": score, "status": row["status"], "signals": signals}


@router.post("/verify/{lead_id}")
async def verify_lead_email(lead_id: int):
    """Verify a lead's email via Hunter.io."""
    async with db() as conn:
        lead = await conn.fetchrow("SELECT email FROM leads WHERE id = $1", lead_id)
        if not lead:
            raise HTTPException(404, "Lead not found")

        result = await verify_email(lead["email"])
        await conn.execute("""
            UPDATE leads SET email_verified = $1, updated_at = NOW() WHERE id = $2
        """, result["deliverable"], lead_id)

    return {"lead_id": lead_id, "email": lead["email"], **result}


@router.get("/qualified")
async def get_qualified_leads(limit: int = 50):
    """Get leads ready for outreach (qualified, email verified, not yet sent)."""
    async with db() as conn:
        rows = await conn.fetch("""
            SELECT id, company_name, domain, contact_name, contact_title,
                   email, score, signals, industry, created_at
            FROM leads
            WHERE status = 'qualified' AND email_verified = TRUE
            ORDER BY score DESC, created_at ASC
            LIMIT $1
        """, limit)
    return [dict(r) for r in rows]


@router.post("/import/upstream")
async def import_from_upstream(
    source: str = Query("leads", description="Table to import from: 'leads' or 'cf_outreach_leads'"),
    limit: int = Query(50, le=200),
    status_filter: str = Query("new", description="Only import leads with this status"),
):
    """Pull leads from the upstream lead database into the outreach pipeline."""
    imported = 0
    skipped = 0

    async with lead_db() as upstream:
        if source == "cf_outreach_leads":
            rows = await upstream.fetch("""
                SELECT company, contact_name, email, location, source, status
                FROM cf_outreach_leads
                WHERE status = $1 AND email IS NOT NULL AND email != ''
                ORDER BY created_at DESC
                LIMIT $2
            """, status_filter, limit)

            async with db() as conn:
                for r in rows:
                    lead_data = {
                        "company_name": r["company"],
                        "contact_name": r["contact_name"] or "",
                        "contact_title": "",
                        "email": r["email"],
                        "industry": "seafood",
                        "employee_count": 0,
                        "source": f"upstream:{source}",
                    }
                    score, signals = score_lead(lead_data)
                    result = await conn.fetchrow("""
                        INSERT INTO leads (company_name, domain, contact_name, contact_title,
                            email, industry, employee_count, source, score, signals, status)
                        VALUES ($1, $2, $3, '', $4, 'seafood', 0, $5, $6, $7::jsonb, 'qualified')
                        ON CONFLICT (email) DO NOTHING
                        RETURNING id
                    """, r["company"], (r["email"].split("@")[1] if "@" in r["email"] else ""),
                        r["contact_name"] or "", r["email"],
                        f"upstream:{source}", score, str(signals))
                    if result:
                        imported += 1
                        await conn.execute("""
                            INSERT INTO pipeline_events (lead_id, event_type, agent, metadata)
                            VALUES ($1, 'imported', 'scout', $2::jsonb)
                        """, result["id"], f'{{"source":"{source}","upstream_status":"{r["status"]}"}}')
                    else:
                        skipped += 1

        elif source == "leads":
            rows = await upstream.fetch("""
                SELECT name, email, company, status, score, source
                FROM leads
                WHERE status = $1 AND email IS NOT NULL AND email != ''
                ORDER BY score DESC NULLS LAST
                LIMIT $2
            """, status_filter, limit)

            async with db() as conn:
                for r in rows:
                    name_parts = (r["name"] or "").strip()
                    company = (r["company"] or "").split("|")[0].strip()
                    lead_data = {
                        "company_name": company,
                        "contact_name": name_parts,
                        "contact_title": "",
                        "email": r["email"],
                        "industry": "agency",
                        "employee_count": 0,
                        "source": f"upstream:{source}",
                    }
                    score, signals = score_lead(lead_data)
                    # Preserve upstream score if higher
                    if r["score"] and r["score"] > score:
                        score = min(r["score"] // 10, 10)
                    result = await conn.fetchrow("""
                        INSERT INTO leads (company_name, domain, contact_name, contact_title,
                            email, industry, employee_count, source, score, signals, status)
                        VALUES ($1, $2, $3, '', $4, 'agency', 0, $5, $6, $7::jsonb, 'qualified')
                        ON CONFLICT (email) DO NOTHING
                        RETURNING id
                    """, company, (r["email"].split("@")[1] if "@" in r["email"] else ""),
                        name_parts, r["email"],
                        f"upstream:{source}", score, str(signals))
                    if result:
                        imported += 1
                        await conn.execute("""
                            INSERT INTO pipeline_events (lead_id, event_type, agent, metadata)
                            VALUES ($1, 'imported', 'scout', $2::jsonb)
                        """, result["id"], f'{{"source":"{source}","upstream_score":{r["score"] or 0}}}')
                    else:
                        skipped += 1
        else:
            raise HTTPException(400, f"Unknown source table: {source}")

    if imported > 0:
        await send_telegram(
            f"*Scout imported {imported} leads* from upstream `{source}`\n"
            f"Skipped {skipped} duplicates"
        )

    return {"imported": imported, "skipped": skipped, "source": source}


@router.get("/upstream/preview")
async def preview_upstream(
    source: str = Query("leads", description="'leads' or 'cf_outreach_leads'"),
    limit: int = Query(10, le=50),
):
    """Preview what's available in the upstream lead database."""
    async with lead_db() as conn:
        if source == "cf_outreach_leads":
            rows = await conn.fetch("""
                SELECT company, contact_name, email, status, source, location
                FROM cf_outreach_leads WHERE email IS NOT NULL
                ORDER BY created_at DESC LIMIT $1
            """, limit)
            stats = await conn.fetchrow("""
                SELECT COUNT(*) as total,
                    COUNT(*) FILTER (WHERE status = 'new') as new,
                    COUNT(*) FILTER (WHERE status = 'needs_email') as needs_email,
                    COUNT(*) FILTER (WHERE status = 'contacted') as contacted
                FROM cf_outreach_leads
            """)
        elif source == "leads":
            rows = await conn.fetch("""
                SELECT name, email, company, status, score, source
                FROM leads WHERE email IS NOT NULL
                ORDER BY score DESC NULLS LAST LIMIT $1
            """, limit)
            stats = await conn.fetchrow("""
                SELECT COUNT(*) as total,
                    COUNT(*) FILTER (WHERE status = 'new') as new,
                    COUNT(*) FILTER (WHERE status = 'contacted') as contacted
                FROM leads
            """)
        else:
            raise HTTPException(400, f"Unknown source: {source}")

    return {"source": source, "stats": dict(stats), "sample": [dict(r) for r in rows]}


@router.get("/stats")
async def scout_stats():
    """Get Scout pipeline stats."""
    async with db() as conn:
        stats = await conn.fetchrow("""
            SELECT
                COUNT(*) FILTER (WHERE status = 'new') as new,
                COUNT(*) FILTER (WHERE status = 'enriched') as enriched,
                COUNT(*) FILTER (WHERE status = 'qualified') as qualified,
                COUNT(*) FILTER (WHERE status = 'sent') as sent,
                COUNT(*) FILTER (WHERE status = 'replied') as replied,
                COUNT(*) FILTER (WHERE status = 'converted') as converted,
                COUNT(*) FILTER (WHERE status = 'disqualified') as disqualified,
                COUNT(*) as total,
                AVG(score) as avg_score
            FROM leads
        """)
    return dict(stats)
