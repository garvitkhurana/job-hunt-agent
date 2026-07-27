"""Company quality signal.

Adjacent (non-PM) roles are only worth Garvit's time at genuinely good companies,
so we gate them on a company tier score rather than surfacing every title.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

# Tier 1 — top-tier AI labs / category leaders / hypergrowth with strong brand equity
TIER_1 = {
    "anthropic",
    "openai",
    "cursor",
    "anysphere",
    "stripe",
    "databricks",
    "figma",
    "notion",
    "linear",
    "ramp",
    "vercel",
    "perplexity",
    "scale ai",
    "scaleai",
    "harvey",
    "sierra",
    "elevenlabs",
    "mercury",
    "plaid",
    "brex",
    "rippling",
    "coinbase",
    "hugging face",
    "huggingface",
    "runway",
    "runwayml",
}

# Tier 2 — strong, well-funded, credible but a notch below category-defining
TIER_2 = {
    "airtable",
    "asana",
    "retool",
    "coda",
    "dropbox",
    "gusto",
    "clerk",
    "adept",
    "modal",
    "replit",
    "supabase",
    "deel",
    "airwallex",
    "checkr",
    "chime",
    "affirm",
    "marqeta",
    "alloy",
    "unit",
    "addepar",
    "carta",
    "ampla",
    "arcesium",
    "clearwater",
    "enfusion",
    "edra",
}

# Signals inside job descriptions that suggest a quality, well-capitalized startup
GOOD_SIGNALS = [
    (r"\bseries\s+[b-e]\b", 0.20, "Series B+ funding"),
    (r"\bseries\s+a\b", 0.15, "Series A"),
    (r"\bseed\b", 0.08, "Seed stage"),
    (r"\b(?:a16z|andreessen|sequoia|benchmark|greylock|accel|founders fund|index ventures|lightspeed|thrive|kleiner|khosla|general catalyst|insight partners|icon\w*|y combinator|ycombinator)\b", 0.22, "Top-tier investors"),
    (r"\bunicorn\b", 0.15, "Unicorn"),
    (r"\bprofitable\b", 0.10, "Profitable"),
    (r"\bfortune\s+500\b", 0.08, "Enterprise traction"),
    (r"\barr\b|\bannual recurring revenue\b", 0.10, "Disclosed ARR"),
    (r"\bbacked by\b", 0.10, "Named backers"),
]

BAD_SIGNALS = [
    (r"\bstaffing\s+agency\b", -0.4, "Staffing agency"),
    (r"\brecruiting\s+firm\b", -0.4, "Recruiting firm"),
    (r"\bconsultancy\b|\bconsulting\s+services\b", -0.15, "Body-shop consulting"),
    (r"\bcommission\s+only\b", -0.5, "Commission only"),
    (r"\bunpaid\b", -0.6, "Unpaid"),
]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).strip()


def company_tier(company: str, description: str = "", is_yc: bool = False) -> Tuple[float, str, List[str]]:
    """Return (score 0-1, tier label, reasons)."""
    c = _norm(company)
    reasons: List[str] = []

    if c in TIER_1 or any(t in c for t in TIER_1):
        return 1.0, "tier1", ["Category-leading company"]
    if c in TIER_2 or any(t in c for t in TIER_2):
        return 0.8, "tier2", ["Strong well-funded company"]

    score = 0.45
    if is_yc:
        score += 0.15
        reasons.append("YC-backed")

    desc = (description or "").lower()
    for pattern, delta, label in GOOD_SIGNALS:
        if re.search(pattern, desc):
            score += delta
            reasons.append(label)
    for pattern, delta, label in BAD_SIGNALS:
        if re.search(pattern, desc):
            score += delta
            reasons.append(label)

    score = max(0.0, min(1.0, score))
    if score >= 0.75:
        tier = "tier2"
    elif score >= 0.55:
        tier = "tier3"
    else:
        tier = "unknown"
    if not reasons:
        reasons.append("Unverified company quality")
    return round(score, 3), tier, reasons


TIER_LABELS: Dict[str, str] = {
    "tier1": "Top-tier",
    "tier2": "Strong",
    "tier3": "Decent",
    "unknown": "Unverified",
}
