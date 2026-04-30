"""Inbox warmup engine.

Automates reputation building for new Gmail accounts by:
1. Sending natural conversations between benny <-> george
2. Sending warm emails to known contacts who will reply
3. Varying content, timing, and subjects to look human
4. Gradually ramping volume over 6 weeks

All emails are plain text, unique, and conversationally phrased.
"""
import random
import asyncio
from datetime import datetime, timezone
from .gmail import send_email, send_with_delay, ACCOUNTS

# ─── KNOWN CONTACTS (will receive warm emails) ───────────────────────
WARM_CONTACTS = [
    {"name": "Jack", "email": "pokeremail1440@gmail.com"},
    {"name": "Jack", "email": "jgsicklaxer@gmail.com"},
    {"name": "Jack", "email": "jgewirz@gmail.com"},
    {"name": "Jack", "email": "Jack@optaimum.com"},
    {"name": "Rionel", "email": "rionel@optaimum.com"},
]

# ─── INTER-ACCOUNT CONVERSATION TEMPLATES ─────────────────────────────
# These simulate natural back-and-forth between benny and george

BENNY_TO_GEORGE = [
    {
        "subject": "Quick question about the CatchFlow dashboard",
        "body": "Hey George,\n\nDid you get a chance to look at the new pricing page mockup? I think we should add the species filter before we push it live.\n\nAlso, remind me to send the weekly supplier report tomorrow morning.\n\nThanks,\nBenny",
    },
    {
        "subject": "Supplier meeting notes",
        "body": "George,\n\nJust wrapped up the call with the Tampa distributor. Key takeaways:\n\n- They want daily price updates instead of weekly\n- Interested in the premium tier once we launch it\n- Asked about API access for their ordering system\n\nLet's discuss tomorrow.\n\nBenny",
    },
    {
        "subject": "Re: Q2 outreach numbers",
        "body": "George,\n\nNumbers are looking better this week. Reply rate is climbing and we haven't had any deliverability issues.\n\nI think we should start testing the follow-up sequence next week. What do you think about adding a case study reference?\n\nBenny",
    },
    {
        "subject": "Lunch tomorrow?",
        "body": "Hey George,\n\nFree for lunch tomorrow around noon? Want to go over the new lead scoring model and talk through the Apalachicola campaign results.\n\nThere's a new seafood place on 5th that seems appropriate given what we do.\n\nBenny",
    },
    {
        "subject": "Forwarding you the investor deck",
        "body": "George,\n\nAttaching the updated deck with the new metrics slide. Key changes:\n\n- Added the 600 lead pipeline number\n- Updated the TAM calculation for Florida market\n- Included the testimonial from the Miami supplier\n\nLet me know if anything needs tweaking before Thursday.\n\nBenny",
    },
    {
        "subject": "New feature idea",
        "body": "George,\n\nWas thinking about this on my drive home - what if we added a price alert feature? Suppliers could set thresholds and get notified when market prices cross them.\n\nSeems like it would be a strong retention hook. Most of our competitors don't have anything like it.\n\nThoughts?\n\nBenny",
    },
    {
        "subject": "Client feedback from yesterday",
        "body": "Hey George,\n\nGot some interesting feedback from the Carrabelle supplier. They said the main reason they signed up was the competitive pricing data, not the buyer network.\n\nThat changes our messaging a bit. We should probably lead with market intelligence in the next batch.\n\nBenny",
    },
]

GEORGE_TO_BENNY = [
    {
        "subject": "Re: Quick question about the CatchFlow dashboard",
        "body": "Benny,\n\nYeah I saw the mockup. Species filter makes sense - I'd also add a location dropdown while we're at it.\n\nI'll prep the supplier report data tonight so you can just send it out in the morning.\n\nGeorge",
    },
    {
        "subject": "Re: Supplier meeting notes",
        "body": "Nice work on the Tampa call. Daily price updates shouldn't be hard to implement - we already have the data pipeline running hourly.\n\nAPI access is a bigger lift but could be a great premium feature. Let's scope it.\n\nGeorge",
    },
    {
        "subject": "Updated analytics for the week",
        "body": "Benny,\n\nPulled the numbers for this week:\n\n- 45 emails sent across both accounts\n- 3 replies (6.7% reply rate)\n- 0 bounces\n- 2 new qualified leads from organic\n\nPretty solid for week one. Let me know if you want me to run the detailed report.\n\nGeorge",
    },
    {
        "subject": "Re: Lunch tomorrow?",
        "body": "Noon works. The seafood place sounds perfect - nothing like eating fish while selling fish software.\n\nI'll bring my laptop so we can look at the scoring model together.\n\nGeorge",
    },
    {
        "subject": "Competitor alert",
        "body": "Benny,\n\nHeads up - saw that SeafoodSource just launched a pricing tool. Looks pretty basic compared to ours but worth keeping an eye on.\n\nTheir approach is more directory-style. No real-time data. I think we're still well differentiated.\n\nGeorge",
    },
    {
        "subject": "Re: New feature idea",
        "body": "Love the price alert concept. That's exactly the kind of sticky feature that keeps suppliers checking the platform daily.\n\nI can prototype something this weekend. Simple threshold + email notification to start, then SMS later.\n\nGeorge",
    },
    {
        "subject": "Server costs update",
        "body": "Benny,\n\nRan the numbers on our infrastructure costs:\n\n- Render hosting: $7/mo\n- Neon DB: free tier still\n- Anthropic API: ~$15/mo for personalization\n- Total: about $22/month\n\nPretty lean for what we're running. The AI personalization is the biggest line item but worth every penny based on reply rates.\n\nGeorge",
    },
]

# ─── WARM CONTACT TEMPLATES ──────────────────────────────────────────

WARM_TEMPLATES_BENNY = [
    {
        "subject": "Quick update on the seafood project",
        "body": "Hey {name},\n\nJust wanted to give you a quick update on CatchFlow. We've onboarded our first batch of Florida suppliers and the initial response has been really positive.\n\nThe AI-personalized outreach is working way better than the generic templates we were using before. Reply rates are up significantly.\n\nWould love to catch up sometime this week if you're free.\n\nBest,\nBenny Torso\nCatchFlow | Seafood Price Intelligence\nbenny@optaimum.com",
    },
    {
        "subject": "Interesting article about seafood pricing",
        "body": "Hey {name},\n\nCame across an article about how wholesale seafood pricing is still stuck in the dark ages - most suppliers literally email Excel spreadsheets to their buyers every day.\n\nThat's basically the exact problem we're solving with CatchFlow. Thought you'd find it interesting given our conversations.\n\nHow are things on your end?\n\nBenny",
    },
    {
        "subject": "Coffee this week?",
        "body": "Hey {name},\n\nBeen heads down building out the CatchFlow platform and could use a break. Free for coffee this week?\n\nWant to bounce some ideas off you about our go-to-market strategy for the Florida market. We've got 600+ suppliers in our pipeline and trying to figure out the best way to prioritize.\n\nLet me know what works.\n\nBenny",
    },
    {
        "subject": "Thanks for the intro last week",
        "body": "Hey {name},\n\nJust wanted to say thanks again for connecting me with your contact in the seafood industry. Had a great conversation with them about the market dynamics down in the Gulf.\n\nCatchFlow is really starting to take shape. We're seeing some promising early traction with the supplier outreach.\n\nAppreciate the support as always.\n\nBest,\nBenny",
    },
    {
        "subject": "Saw your post",
        "body": "Hey {name},\n\nSaw your recent post and it got me thinking about our approach at CatchFlow. The market intelligence angle is resonating with suppliers way more than we expected.\n\nTurns out knowing what competitors charge is more valuable to them than the buyer network itself. Interesting pivot in our positioning.\n\nHope all is well with you.\n\nBenny",
    },
]

WARM_TEMPLATES_GEORGE = [
    {
        "subject": "Platform update - thought you'd want to know",
        "body": "Hey {name},\n\nQuick update from the CatchFlow engineering side. Just shipped the deliverability system I was telling you about - it pre-screens every outreach email before it goes out.\n\nSpam phrase detection, bounce rate monitoring, the works. Pretty proud of how it turned out.\n\nHow are things going with you?\n\nGeorge\nOptaimum",
    },
    {
        "subject": "Need your opinion on something",
        "body": "Hey {name},\n\nWorking on a new feature for CatchFlow and wanted to get your take. We're thinking about adding price alerts - suppliers set a threshold and get notified when market prices cross it.\n\nDo you think that's the kind of thing that would drive daily engagement? Or is it more of a nice-to-have?\n\nAppreciate any thoughts.\n\nGeorge",
    },
    {
        "subject": "Interesting data point",
        "body": "Hey {name},\n\nPulled some numbers from our CatchFlow pipeline today. The Florida seafood market is $1.2B and growing. Most of it still runs on phone calls and handshakes.\n\nWe're basically building the infrastructure layer that doesn't exist yet. Wild that it's 2026 and this industry is still this manual.\n\nAnyway, thought you'd find that interesting.\n\nGeorge",
    },
    {
        "subject": "Weekend plans?",
        "body": "Hey {name},\n\nAny plans this weekend? I'm probably going to be heads down on the CatchFlow analytics dashboard but should be free Sunday.\n\nLet me know if you want to grab dinner or something.\n\nGeorge",
    },
]


# ─── WARMUP ENGINE ────────────────────────────────────────────────────

async def run_inter_account_warmup(count: int = 3) -> list[dict]:
    """Send conversational emails between benny and george.

    Randomly picks templates and direction. Should run 3-5x/day.
    """
    results = []
    directions = []

    for _ in range(count):
        # Alternate directions with some randomness
        if random.random() < 0.5:
            templates = BENNY_TO_GEORGE
            sender = "benny"
            recipient = ACCOUNTS["george"]["email"]
        else:
            templates = GEORGE_TO_BENNY
            sender = "george"
            recipient = ACCOUNTS["benny"]["email"]
        directions.append(sender)

        template = random.choice(templates)
        try:
            result = await send_email(
                to=recipient,
                subject=template["subject"],
                body=template["body"],
                account=sender,
            )
            results.append({
                "from": sender,
                "to": recipient,
                "subject": template["subject"],
                "sent": True,
                "gmail_id": result.get("id", ""),
            })
        except Exception as e:
            results.append({
                "from": sender,
                "to": recipient,
                "subject": template["subject"],
                "sent": False,
                "error": str(e),
            })

        # Human-like delay between inter-account emails (30-90s)
        if _ < count - 1:
            await asyncio.sleep(random.uniform(30, 90))

    return results


async def run_warm_contact_emails(account: str = "benny", count: int = 2) -> list[dict]:
    """Send warm emails to known contacts from a specific account.

    These are real people who should reply, building sender reputation.
    """
    templates = WARM_TEMPLATES_BENNY if account == "benny" else WARM_TEMPLATES_GEORGE
    contacts = random.sample(WARM_CONTACTS, min(count, len(WARM_CONTACTS)))
    results = []

    for contact in contacts:
        template = random.choice(templates)
        subject = template["subject"]
        body = template["body"].replace("{name}", contact["name"])

        try:
            result = await send_email(
                to=contact["email"],
                subject=subject,
                body=body,
                account=account,
            )
            results.append({
                "to": contact["email"],
                "name": contact["name"],
                "subject": subject,
                "sent": True,
                "gmail_id": result.get("id", ""),
            })
        except Exception as e:
            results.append({
                "to": contact["email"],
                "name": contact["name"],
                "subject": subject,
                "sent": False,
                "error": str(e),
            })

        # Delay between warm emails (60-180s - more human-like for personal emails)
        await asyncio.sleep(random.uniform(60, 180))

    return results


async def run_full_warmup(week: int = 1) -> dict:
    """Run a full warmup session based on the current week.

    Week 1-2: Inter-account only + warm contacts (no cold outreach)
    Week 3: Low volume warm + start cold
    Week 4+: Ramp up
    """
    results = {
        "week": week,
        "inter_account": [],
        "warm_benny": [],
        "warm_george": [],
    }

    if week <= 2:
        # Heavy warmup phase
        results["inter_account"] = await run_inter_account_warmup(count=4)
        results["warm_benny"] = await run_warm_contact_emails("benny", count=3)
        await asyncio.sleep(random.uniform(120, 300))
        results["warm_george"] = await run_warm_contact_emails("george", count=2)
    elif week <= 4:
        # Lighter warmup, cold outreach starting separately
        results["inter_account"] = await run_inter_account_warmup(count=2)
        results["warm_benny"] = await run_warm_contact_emails("benny", count=2)
        results["warm_george"] = await run_warm_contact_emails("george", count=1)
    else:
        # Maintenance mode - just keep accounts looking active
        results["inter_account"] = await run_inter_account_warmup(count=1)
        results["warm_benny"] = await run_warm_contact_emails("benny", count=1)

    total_sent = (
        sum(1 for r in results["inter_account"] if r["sent"]) +
        sum(1 for r in results["warm_benny"] if r["sent"]) +
        sum(1 for r in results["warm_george"] if r["sent"])
    )
    results["total_sent"] = total_sent

    return results
