from __future__ import annotations

import re
from typing import List, Tuple

from ..config import AppConfig
from ..models import Job, ScoreBreakdown
from .company import company_tier
from .roles import classify, is_hard_excluded

SENIOR_SWEET_SPOT = [
    r"\bsenior\b",
    r"\bsr\.?\b",
    r"\bfounding\b",
    r"\blead\b",
    r"\bmanager\s+ii\b",
    r"\bproduct\s+manager\b",  # untitled PM still in-band
]

# Staff / Principal sits above Product Lead / Eng II at most tech cos
STAFF_STRETCH = [
    r"\bstaff\b",
    r"\bprincipal\b",
]

# Above Garvit's current band (Product Lead / Engineer II) — apply stretch penalty
STRETCH_SENIORITY = [
    r"\bdirector\b",
    r"\bvp\b",
    r"\bvice\s+president\b",
    r"\bchief\b",
    r"\bcpo\b",
    r"\bhead\s+of\b",
]

# Early-stage founding Head of Product can still be in-band; Director/VP at big cos is not.
# Do NOT match "Head of Product Operations/Marketing/etc."
STRETCH_OK_AT_EARLY = [
    r"\bfounding\s+(?:head\s+of\s+product|product\s+lead)\b",
    r"\bhead\s+of\s+product\b(?!\s+(?:operations|ops|marketing|design|analytics|engineering|growth))",
]

BLOCKED_GEOS = (
    "ireland",
    "dublin",
    "singapore",
    "india",
    "bangalore",
    "bengaluru",
    "hyderabad",
    "pune",
    "gurgaon",
    "sydney",
    "melbourne",
    "tokyo",
    "seoul",
    "berlin",
    "munich",
    "amsterdam",
    "paris",
    "madrid",
    "barcelona",
    "dubai",
    "tel aviv",
    "sao paulo",
    "mexico city",
    "bogota",
    "buenos aires",
    "warsaw",
    "krakow",
    "lisbon",
)

GEO_ALIASES = {
    "new york": ("new york", "nyc", "brooklyn", "manhattan", "us-nyc"),
    "nyc": ("new york", "nyc", "us-nyc"),
    "new york city": ("new york", "nyc"),
    "california": (
        "california",
        "calif",
        "san francisco",
        "us-sf",
        "bay area",
        "palo alto",
        "mountain view",
        "santa clara",
        "los angeles",
        "san jose",
        "sunnyvale",
        "menlo park",
        " ca,",
        "ca)",
    ),
    "san francisco": ("san francisco", "us-sf", "bay area", "palo alto", "sf"),
    "bay area": ("bay area", "san francisco", "palo alto", "mountain view"),
    "los angeles": ("los angeles", "santa monica"),
    "remote us": ("remote", "united states", "usa", "us -", "us-", "u.s."),
    "united states": ("united states", "usa", "us -", "us-", "u.s."),
    "london": ("london", "united kingdom", "england"),
    "uk": ("london", "united kingdom", " uk"),
    "canada": ("canada", "toronto", "vancouver", "montreal", "ontario", "ca-toronto"),
    "toronto": ("toronto", "ca-toronto"),
    "vancouver": ("vancouver",),
    "remote canada": ("canada", "toronto", "vancouver"),
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _any_match(patterns: List[str], text: str) -> bool:
    return any(re.search(p, text) for p in patterns)


def _location_fit(loc_blob: str, remote: bool, allowed: List[str]) -> Tuple[float, List[str]]:
    reasons: List[str] = []
    for allow in allowed:
        needles = GEO_ALIASES.get(allow.lower(), (allow.lower(),))
        if any(n and n in loc_blob for n in needles):
            return 1.0, [f"location:{allow}"]

    if any(b in loc_blob for b in BLOCKED_GEOS):
        return 0.0, ["excluded_geo"]

    if remote or "remote" in loc_blob or "anywhere" in loc_blob:
        reasons.append("remote_ok")
        return 0.8, reasons

    if not loc_blob.strip():
        return 0.5, ["location_unknown"]

    return 0.2, ["location_mismatch"]


def score_job(job: Job, cfg: AppConfig) -> ScoreBreakdown:
    title = _norm(job.title)
    loc = _norm(job.location)
    desc = _norm(job.description[:4000])
    blob = f"{title} {loc} {desc}"
    reasons: List[str] = []

    if is_hard_excluded(title):
        return ScoreBreakdown(total=0.0, reasons=["excluded:title"])

    for kw in cfg.filters.exclude_keywords:
        if kw.lower() in title:
            return ScoreBreakdown(total=0.0, reasons=[f"excluded:{kw}"])

    family = classify(job.title)
    is_yc = job.source.value == "yc"

    if family is None:
        if is_yc:
            role_fit, track, role_key = 0.6, "core", "yc_explore"
            reasons.append("yc_explore_seed")
        else:
            return ScoreBreakdown(total=0.0, reasons=["no_role_match"])
    else:
        role_fit = family.weight
        track = family.track
        role_key = family.key
        reasons.append(f"role:{family.key}")

    tier_score, tier, tier_reasons = company_tier(job.company, job.description, is_yc=is_yc)
    reasons.extend(f"company:{r}" for r in tier_reasons)

    # Adjacent roles only justify attention at good companies
    if track == "adjacent" and tier_score < cfg.filters.min_company_tier:
        return ScoreBreakdown(
            total=0.0,
            track=track,
            role_family=role_key,
            company_tier=tier,
            company_score=tier_score,
            reasons=reasons + ["adjacent_company_below_bar"],
        )

    seniority = 0.45
    stretch = False
    if _any_match(STRETCH_SENIORITY, title):
        # Director / VP / CPO / Head — usually a stretch from Product Lead / Eng II
        if "founding" in title or (tier_score < 0.85 and _any_match(STRETCH_OK_AT_EARLY, title)):
            seniority = 0.8
            reasons.append("early_leadership_ok")
        else:
            stretch = True
            seniority = 0.35
            reasons.append("stretch:director_or_vp")
    elif _any_match(STAFF_STRETCH, title):
        # Staff / Principal — one band above Product Lead / Eng II at most tech cos
        stretch = True
        seniority = 0.45
        reasons.append("stretch:staff_or_principal")
    elif _any_match(SENIOR_SWEET_SPOT, title):
        seniority = 0.95
        reasons.append("seniority_sweet_spot")
    elif "product manager" in title or "engineer" in title or "strategist" in title or "architect" in title:
        seniority = 0.7
        reasons.append("in_band_ic")

    # Pure Staff/Principal Software Engineer (not FDE / Applied AI) is off-target
    if (
        track == "adjacent"
        and _any_match(STAFF_STRETCH, title)
        and role_key not in ("forward_deployed", "ai_engineer", "solutions", "ai_strategy")
    ):
        return ScoreBreakdown(
            total=0.0,
            track=track,
            role_family=role_key,
            company_tier=tier,
            company_score=tier_score,
            reasons=reasons + ["excluded:staff_swe"],
        )

    hits = sum(1 for kw in cfg.candidate.target_keywords + cfg.filters.prefer_keywords if kw.lower() in blob)
    keyword_fit = min(1.0, 0.25 + hits / 5.0) if hits else 0.2
    if hits:
        reasons.append(f"keyword_hits:{hits}")

    # Geos are often encoded in the title, e.g. "Solutions Consultant (Singapore)"
    title_geo = next((g for g in BLOCKED_GEOS if g in title), None)
    if title_geo and not any(
        n and n in title
        for allow in cfg.candidate.locations
        for n in GEO_ALIASES.get(allow.lower(), (allow.lower(),))
    ):
        return ScoreBreakdown(
            total=0.0,
            track=track,
            role_family=role_key,
            company_tier=tier,
            company_score=tier_score,
            reasons=reasons + [f"excluded_geo_in_title:{title_geo}"],
        )

    loc_blob = f"{loc} {'remote' if job.remote else ''}"
    location_fit, loc_reasons = _location_fit(loc_blob, job.remote, cfg.candidate.locations)
    reasons.extend(loc_reasons)
    if location_fit == 0.0:
        return ScoreBreakdown(
            total=0.0,
            track=track,
            role_family=role_key,
            company_tier=tier,
            company_score=tier_score,
            reasons=reasons,
        )

    # Visa-friendly boost: London / Canada / UK outrank equivalent US roles
    priority_boost = 0.0
    visa_boost = getattr(cfg.filters, "visa_priority_boost", 0.08) or 0.08
    for p in cfg.candidate.priority_locations or []:
        needles = GEO_ALIASES.get(p.lower(), (p.lower(),))
        if any(n and (n in loc_blob or n in title) for n in needles):
            priority_boost = visa_boost
            reasons.append(f"visa_priority:{p}")
            break

    total = (
        0.34 * role_fit
        + 0.20 * seniority
        + 0.16 * keyword_fit
        + 0.13 * location_fit
        + 0.17 * tier_score
        + priority_boost
    )

    if "founding" in title:
        total = min(1.0, total + (getattr(cfg.filters, "founding_boost", 0.07) or 0.07))
        reasons.append("founding_boost")
    if re.search(r"\bai\b|\bllm\b|\bml\b|\bgenai\b", title):
        total = min(1.0, total + (getattr(cfg.filters, "ai_title_boost", 0.05) or 0.05))
        reasons.append("ai_title_boost")
    if track == "adjacent":
        total *= getattr(cfg.filters, "adjacent_track_mult", 0.92) or 0.92
        reasons.append("adjacent_track")
    if stretch:
        total *= getattr(cfg.filters, "stretch_penalty", 0.72) or 0.72
        reasons.append("stretch_penalty")
        stretch_bar = getattr(cfg.filters, "stretch_min_score", 0.78) or 0.78
        # Drop stretch roles that don't clear a higher bar
        if total < max(cfg.filters.min_score, stretch_bar):
            return ScoreBreakdown(
                total=round(total, 3),
                role_fit=round(role_fit, 3),
                location_fit=round(location_fit, 3),
                keyword_fit=round(keyword_fit, 3),
                seniority_fit=round(seniority, 3),
                company_score=tier_score,
                company_tier=tier,
                track=track,
                role_family=role_key,
                reasons=reasons + ["stretch_below_bar"],
            )

    return ScoreBreakdown(
        role_fit=round(role_fit, 3),
        location_fit=round(location_fit, 3),
        keyword_fit=round(keyword_fit, 3),
        seniority_fit=round(seniority, 3),
        company_score=tier_score,
        company_tier=tier,
        track=track,
        role_family=role_key,
        total=round(min(1.0, total), 3),
        reasons=reasons,
    )
