"""
CatchFlow Campaign Intelligence Database

All context the Hermes agent needs to generate highly personalized
outreach for the seafood industry. Benny from CatchFlow.
"""

# ─── COMPANY IDENTITY ──────────────────────────────────────────────
COMPANY = {
    "name": "CatchFlow",
    "tagline": "Seafood Price Intelligence Platform",
    "url": "https://catchflow.io",
    "sender_name": "Benny Torso",
    "sender_first": "Benny",
    "sender_title": "Partnerships",
    "sender_email": "benny@optaimum.com",
    "what_we_do": (
        "CatchFlow is a seafood price intelligence platform that gives "
        "suppliers real-time visibility into market pricing, connects them "
        "directly with qualified buyers, and automates the tedious process "
        "of sharing pricelists. Think of it as a digital marketplace where "
        "your prices are always in front of the right people."
    ),
}

# ─── VALUE PROPOSITIONS (ranked by impact) ──────────────────────────
VALUE_PROPS = [
    {
        "headline": "Get your prices in front of buyers who are actively searching",
        "detail": "CatchFlow's buyer dashboard shows live pricing from suppliers. "
                  "When a buyer in Miami needs Red Snapper, your price shows up first "
                  "if you're listed. No cold calls, no PDFs lost in email.",
        "proof": "Suppliers on CatchFlow see 3-5x more buyer inquiries in the first month.",
    },
    {
        "headline": "Stop emailing pricelists - automate it",
        "detail": "Upload your prices once and CatchFlow distributes them to your "
                  "buyer network automatically. Price changes update in real-time. "
                  "No more spreadsheets, no more 'send me your latest list' emails.",
        "proof": "Saves suppliers an average of 6 hours/week on price distribution.",
    },
    {
        "headline": "Market intelligence you can't get anywhere else",
        "detail": "See what competitors are pricing similar species at. Understand "
                  "market trends before they hit. Make pricing decisions based on "
                  "data, not gut feeling.",
        "proof": "Suppliers using CatchFlow pricing insights report 8-12% margin improvement.",
    },
    {
        "headline": "Free to list - you only pay when you grow",
        "detail": "Basic listing is completely free. Premium features (analytics, "
                  "priority placement, buyer alerts) are available as you scale.",
        "proof": "Zero risk to try. Most suppliers upgrade within 60 days.",
    },
]

# ─── INDUSTRY PAIN POINTS ──────────────────────────────────────────
PAIN_POINTS = {
    "price_distribution": {
        "pain": "emailing pricelists to buyers manually every day",
        "cost": "6-10 hours/week of admin time and outdated prices reaching buyers",
        "solution": "Automated price distribution - update once, buyers see it instantly",
    },
    "buyer_discovery": {
        "pain": "relying on the same buyer network with no way to find new accounts",
        "cost": "missed revenue from buyers who don't know you exist",
        "solution": "CatchFlow buyer dashboard puts your inventory in front of 500+ active buyers",
    },
    "price_transparency": {
        "pain": "pricing blind - no visibility into what competitors charge",
        "cost": "leaving money on the table or losing deals to underpricing",
        "solution": "Market pricing intelligence shows real-time competitive landscape",
    },
    "seasonal_volatility": {
        "pain": "dealing with wild price swings from seasons, weather, and supply",
        "cost": "buyers getting frustrated by stale quotes",
        "solution": "Real-time pricing keeps your offers current and competitive",
    },
    "relationship_scaling": {
        "pain": "running on personal relationships that don't scale",
        "cost": "growth limited by how many calls and emails you can send per day",
        "solution": "Digital presence works 24/7 - buyers find you while you sleep",
    },
}

# ─── FLORIDA SEAFOOD MARKET CONTEXT ────────────────────────────────
MARKET_INTEL = {
    "florida_market_size": "$1.2B+ annual commercial fishing and wholesale",
    "top_ports": [
        "Miami (largest import hub, 60%+ of US seafood imports)",
        "Tampa/St. Pete (Gulf coast hub)",
        "Jacksonville (Atlantic coast distribution)",
        "Panama City/Apalachicola (Gulf shrimp, oysters)",
        "Key West (lobster, stone crab, yellowtail)",
        "Fort Lauderdale (import/export corridor)",
    ],
    "seasonal_peaks": {
        "stone_crab": "October 15 - May 1 (Florida season)",
        "shrimp": "Year-round, peaks June-November (Gulf)",
        "lobster": "August - March (spiny lobster season)",
        "grouper": "Year-round, closures Jan-Apr (some species)",
        "snapper": "Year-round, red snapper limited season June-July",
        "oysters": "September - April (peak quality months)",
    },
    "trends": [
        "Restaurants demanding traceable, sustainable sourcing",
        "Direct-to-consumer seafood boxes growing 25% YoY",
        "Import costs rising — domestic suppliers have pricing advantage",
        "Buyers consolidating — fewer, larger accounts want digital ordering",
        "Farm-raised vs wild-caught premiums widening",
    ],
}

# ─── SPECIES CATEGORIES (from database) ────────────────────────────
SPECIES_BY_CATEGORY = {
    "Fish": [
        "Red Snapper", "Grouper", "Mahi Mahi", "Swordfish", "Yellowfin Tuna",
        "Salmon", "Chilean Sea Bass", "Hogfish", "Wahoo", "Yellowtail",
        "Branzino", "Cod", "Halibut", "Flounder", "Catfish", "Tilapia",
        "Snapper (Lane, Silk, Scarlet, B-Liner)", "King Mackerel",
    ],
    "Crustacean": [
        "Stone Crab", "King Crab", "Snow Crab", "Lobster", "Spiny Lobster",
        "Blue Swimming Crab", "Jonah Crab", "Crawfish",
    ],
    "Shellfish": [
        "Shrimp", "Oysters", "Clams", "Mussels", "Conch",
    ],
    "Mollusc": [
        "Scallops", "Sea Scallops", "Squid", "Octopus",
    ],
}

# ─── LOCATION-SPECIFIC HOOKS ───────────────────────────────────────
LOCATION_HOOKS = {
    "Miami": "As the #1 seafood import hub in the US, Miami suppliers need every edge to stand out. CatchFlow gives you that edge.",
    "Tampa": "Tampa's Gulf coast market is booming. The suppliers winning are the ones buyers can find fastest — that's what CatchFlow does.",
    "Jacksonville": "Jax is becoming a major distribution hub. CatchFlow connects you to buyers across the entire East Coast corridor.",
    "Panama City": "The Panhandle's shrimp and oyster suppliers are some of the best in the country. CatchFlow makes sure buyers know it.",
    "Apalachicola": "Apalachicola oysters are legendary. CatchFlow helps you command the premium pricing your product deserves.",
    "Key West": "Stone crab and lobster from the Keys sell themselves — if buyers can find you. CatchFlow puts you on their radar.",
    "Fort Lauderdale": "FLL's import/export corridor moves serious volume. CatchFlow's buyer network is where those deals start.",
    "Doral": "Doral's wholesale district is competitive. CatchFlow gives you visibility that cold calls can't match.",
    "Orlando": "Orlando's restaurant scene is exploding. CatchFlow connects you directly to the buyers feeding that demand.",
    "Pensacola": "Gulf Coast seafood from Pensacola is in high demand. CatchFlow makes sure your pricing reaches the right buyers.",
    "default": "Florida's seafood market is competitive. CatchFlow gives you the visibility and tools to win more business.",
}

# ─── PERSONALIZATION HELPERS ────────────────────────────────────────

def get_location_hook(location: str) -> str:
    """Get a location-specific opening hook."""
    if not location:
        return LOCATION_HOOKS["default"]
    for city, hook in LOCATION_HOOKS.items():
        if city.lower() in location.lower():
            return hook
    return LOCATION_HOOKS["default"]


def get_species_hook(species: list | None, company: str) -> str:
    """Generate a species-specific value mention."""
    if not species:
        return f"your seafood inventory"
    if len(species) == 1:
        return f"your {species[0].title()} supply"
    top_two = [s.title() for s in species[:2]]
    return f"your {top_two[0]} and {top_two[1]}"


def get_best_pain_point(lead: dict) -> dict:
    """Select the most relevant pain point based on lead data."""
    # Small operations → price distribution pain
    # Larger/multi-location → buyer discovery
    # Everyone → seasonal volatility if we're near a season change
    location = (lead.get("location") or "").lower()
    if "miami" in location or "doral" in location or "fort lauderdale" in location:
        return PAIN_POINTS["buyer_discovery"]  # Competitive markets
    if "apalachicola" in location or "panama city" in location or "key west" in location:
        return PAIN_POINTS["relationship_scaling"]  # Smaller ports
    return PAIN_POINTS["price_distribution"]  # Default — universal pain


def get_seasonal_relevance() -> str:
    """Get what's seasonally relevant right now (late April)."""
    return (
        "With stone crab season wrapping up May 1st and shrimp season "
        "ramping up, this is the perfect time to make sure buyers can "
        "find your updated pricing."
    )
