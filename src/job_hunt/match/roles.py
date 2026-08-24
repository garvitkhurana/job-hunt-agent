"""Role taxonomy: core PM targets plus adjacent roles that fit a PM/engineer hybrid.

Garvit's profile is unusual — Product Lead *and* Engineer II, ships RAG/LLM systems
himself, capital-markets domain depth, Columbia MSBA. That makes several non-PM
titles genuinely strong fits, so we track them separately instead of discarding them.
"""

from __future__ import annotations

import re
from typing import Dict, List, NamedTuple, Optional


class RoleFamily(NamedTuple):
    key: str
    label: str
    track: str  # "core" | "adjacent"
    patterns: List[str]
    weight: float  # ceiling multiplier applied to role fit
    why: str  # explanation surfaced to the user


CORE_FAMILIES: List[RoleFamily] = [
    RoleFamily(
        key="founding_pm",
        label="Founding Product Manager",
        track="core",
        patterns=[r"\bfounding\s+product\b", r"\bfounding\s+pm\b", r"\bfirst\s+product\s+manager\b"],
        weight=1.0,
        why="Founding PM — owns discovery→launch solo, matches your 0→1 RAG platform build.",
    ),
    RoleFamily(
        key="product_lead",
        label="Product Lead",
        track="core",
        patterns=[r"\bproduct\s+lead\b"],
        weight=1.0,
        why="Product Lead — direct match to your current BlackRock title.",
    ),
    RoleFamily(
        key="product_leadership",
        label="Product Leadership (Head/Director/VP)",
        track="core",
        patterns=[
            r"\bhead\s+of\s+product\b",
            r"\bdirector\s+of\s+product\b",
            r"\bproduct\s+director\b",
            r"\bvp\s+of\s+product\b",
            r"\bvp,?\s+product\b",
        ],
        weight=0.65,
        why="Director/VP/Head of Product — stretch vs Product Lead / Eng II unless early-stage founding Head of Product.",
    ),
    RoleFamily(
        key="senior_pm",
        label="Senior / Group PM",
        track="core",
        patterns=[
            r"\bsenior\s+product\s+manager\b",
            r"\bsr\.?\s+product\s+manager\b",
            r"\bgroup\s+product\s+manager\b",
        ],
        weight=1.0,
        why="Senior PM — direct match to your current title band.",
    ),
    RoleFamily(
        key="staff_pm",
        label="Staff / Principal PM (stretch)",
        track="core",
        patterns=[
            r"\bstaff\s+product\s+manager\b",
            r"\bprincipal\s+product\s+manager\b",
        ],
        weight=0.7,
        why="Staff/Principal PM — one level above Product Lead / Eng II; usually a stretch at large cos.",
    ),
    RoleFamily(
        key="ai_pm",
        label="AI / ML Product Manager",
        track="core",
        patterns=[
            r"\bai\s+product\s+manager\b",
            r"\bml\s+product\s+manager\b",
            r"\bproduct\s+manager,?\s+ai\b",
            r"\bproduct\s+manager,?\s+ml\b",
            r"\bgenai\s+product\b",
            r"\bllm\s+product\b",
        ],
        weight=1.0,
        why="AI PM — you own an LLM/RAG product roadmap today.",
    ),
    RoleFamily(
        key="technical_pm",
        label="Technical / Platform PM",
        track="core",
        patterns=[
            r"\btechnical\s+product\s+manager\b",
            r"\bplatform\s+product\s+manager\b",
            r"\bproduct\s+manager,?\s+platform\b",
            r"\bapi\s+product\s+manager\b",
            r"\bdata\s+product\s+manager\b",
        ],
        weight=0.98,
        why="Technical PM — you write Python/SQL and partner directly with DS/eng.",
    ),
    RoleFamily(
        key="generic_pm",
        label="Product Manager",
        track="core",
        patterns=[r"\bproduct\s+manager\b", r"\bproduct\s+owner\b"],
        weight=0.85,
        why="PM role — check level; may be below your current scope.",
    ),
    RoleFamily(
        key="founding_generalist",
        label="Founding team / Co-founder",
        track="core",
        patterns=[r"\bco-?founder\b", r"\bfounding\s+team\b", r"\bfounder\s+in\s+residence\b", r"\beir\b"],
        weight=1.0,
        why="Founding seat — matches your stated interest in founding roles.",
    ),
]


ADJACENT_FAMILIES: List[RoleFamily] = [
    RoleFamily(
        key="forward_deployed",
        label="Forward Deployed / Applied AI Engineer",
        track="adjacent",
        patterns=[
            r"\bforward\s+deployed\b",
            r"\bfde\b",
            r"\bapplied\s+ai\s+engineer\b",
            r"\bapplied\s+ai\b",
            r"\bapplied\s+ml\s+engineer\b",
            r"\bdeployment\s+strategist\b",
            # Not bare "field engineer" (too broad / non-AI)
            r"\bai\s+field\s+engineer\b",
            r"\bfield\s+ai\s+engineer\b",
        ],
        weight=0.95,
        why="Forward-deployed/applied AI — you build RAG systems AND run client discovery. Hottest hybrid role in AI startups and often a fast path to founding PM.",
    ),
    RoleFamily(
        key="ai_engineer",
        label="AI / LLM Engineer",
        track="adjacent",
        patterns=[
            r"\bai\s+engineer\b",
            r"\bllm\s+engineer\b",
            r"\bml\s+engineer\b",
            r"\bmachine\s+learning\s+engineer\b",
            r"\bgenai\s+engineer\b",
        ],
        weight=0.82,
        why="AI/LLM engineer — hands-on LangChain/Python/RAG at BlackRock; slightly less fit than forward-deployed roles that also need client discovery.",
    ),
    RoleFamily(
        key="ai_strategy",
        label="AI Strategy / Transformation",
        track="adjacent",
        patterns=[
            r"\bai\s+strategist\b",
            r"\bai\s+strategy\b",
            r"\bai\s+transformation\b",
            r"\bhead\s+of\s+ai\b",
            # Require product/strategy context — not "Sales … AI Adoption" / bare "AI Lead"
            r"\bai\s+product\s+lead\b",
            r"\bhead\s+of\s+ai\s+strategy\b",
            r"\bai\s+program\s+(?:manager|lead|director)\b",
        ],
        weight=0.72,
        why="AI strategy — you sit inside BlackRock's AI Accelerator driving enterprise AI adoption; rare, credible signal.",
    ),
    RoleFamily(
        key="solutions",
        label="Solutions Architect / Engineer",
        track="adjacent",
        patterns=[
            r"\bsolutions?\s+architect\b",
            r"\bsolutions?\s+engineer\b",
            r"\bsolutions?\s+consultant\b",
            r"\bsolutions?\s+lead\b",
            r"\bpartner\s+engineer\b",
            r"\bimplementation\s+lead\b",
        ],
        weight=0.7,
        why="Solutions — mirrors your BlackRock work translating client workflows into AI product, plus GTM enablement experience.",
    ),
    RoleFamily(
        key="founding_engineer",
        label="Founding Engineer",
        track="adjacent",
        patterns=[r"\bfounding\s+engineer\b", r"\bfounding\s+ai\s+engineer\b", r"\bfounding\s+full\s*stack\b"],
        weight=0.8,
        why="Founding engineer — viable given Engineer II title and hands-on LangChain/Python work, if you want to stay technical.",
    ),
    RoleFamily(
        key="chief_of_staff",
        label="Chief of Staff / BizOps / Strategy",
        track="adjacent",
        patterns=[
            r"\bchief\s+of\s+staff\b",
            r"\bbusiness\s+operations\b",
            r"\bbizops\b",
            # BizOps strategy&ops — not Sales Strategy & Operations
            r"(?<!sales\s)\bstrategy\s+(?:&|and)\s+operations\b",
            r"\bstrategic\s+initiatives\b",
            # Not bare "strategy lead" (catches sales/GTM junk)
            r"\bproduct\s+strategy\s+lead\b",
            r"\bcorporate\s+strategy\s+lead\b",
        ],
        weight=0.65,
        why="Chief of Staff / BizOps — Columbia MSBA plus cross-functional platform launches at a top financial institution reads strongly here.",
    ),
    RoleFamily(
        key="tpm",
        label="Technical Program Manager",
        track="adjacent",
        patterns=[r"\btechnical\s+program\s+manager\b", r"\bprogram\s+manager,?\s+(?:ai|ml|platform)\b", r"\btpm\b"],
        weight=0.75,
        why="TPM — you already run discovery→launch lifecycle and Jira-level execution across teams.",
    ),
    RoleFamily(
        key="growth",
        label="Growth Product / Growth Lead",
        track="adjacent",
        patterns=[r"\bgrowth\s+product\s+manager\b", r"\bgrowth\s+lead\b", r"\bhead\s+of\s+growth\b", r"\bgrowth\s+manager\b"],
        weight=0.8,
        why="Growth — you have direct growth-product startup experience (PadSquad) plus A/B testing and KPI work.",
    ),
    RoleFamily(
        key="fintech_specialist",
        label="Fintech / Capital Markets Specialist",
        track="adjacent",
        patterns=[
            r"\bcapital\s+markets\b",
            r"\bfixed\s+income\b",
            r"\bprivate\s+credit\b",
            r"\bprivate\s+markets\b",
            r"\basset\s+management\b",
            r"\bfintech\s+lead\b",
        ],
        weight=0.85,
        why="Domain specialist — private credit / fixed income / Aladdin experience is scarce and highly paid at fintech startups.",
    ),
    RoleFamily(
        key="data_analytics_lead",
        label="Data / Analytics Product Lead",
        track="adjacent",
        patterns=[
            r"\bhead\s+of\s+data\b",
            r"\banalytics\s+lead\b",
            r"\bdata\s+lead\b",
            r"\bdata\s+platform\s+lead\b",
            r"\banalytics\s+manager\b",
        ],
        weight=0.75,
        why="Data/analytics leadership — data governance platform build plus SQL/ETL/Tableau depth.",
    ),
]

ALL_FAMILIES: List[RoleFamily] = CORE_FAMILIES + ADJACENT_FAMILIES

# Titles never worth surfacing regardless of company
HARD_EXCLUDE = [
    r"\bintern\b",
    r"\binternship\b",
    r"\bjunior\b",
    r"\bentry[\s-]?level\b",
    r"\bunpaid\b",
    r"\(explore\)",  # synthetic YC seed cards — not real postings
    r"\bnew\s+grad\b",
    r"\b20\d{2}\s*[-–]\s*product\b",  # "2026 - Product Manager" campus pipelines
    r"\baccount\s+executive\b",
    r"\baccount\s+manager\b",
    r"\bsales\s+(?:rep|representative|development)\b",
    r"\bsales\s+strategy\b",
    r"\bsales\s+operations?\b",
    r"\bsales\s+ops\b",
    r"\bsales\s+enablement\b",
    r"\bsales\s+engineer\b",
    r"\brevenue\s+ops\b",
    r"\brevenue\s+operations?\b",
    r"\bcustomer\s+success\b",
    r"\bsdr\b",
    r"\brecruit(?:er|ing)\b",
    r"\bcustome?r\s+support\b",
    r"\btechnical\s+support\b",
    r"\boffice\s+manager\b",
    r"\bexecutive\s+assistant\b",
    r"\bpaid\s+media\b",
    r"\bcontent\s+writer\b",
    r"\bproduct\s+designer\b",
    r"\bux\s+designer\b",
    r"\bproduct\s+marketing\b",
    r"\baccountant\b",
    r"\bpayroll\b",
    r"\bwarehouse\b",
    r"\bnurse\b",
]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def is_hard_excluded(title: str) -> bool:
    return any(re.search(p, _norm(title)) for p in HARD_EXCLUDE)


def classify(title: str) -> Optional[RoleFamily]:
    """Return the best-matching role family, preferring core and more specific matches."""
    t = _norm(title)
    if is_hard_excluded(t):
        return None
    for family in ALL_FAMILIES:
        if any(re.search(p, t) for p in family.patterns):
            return family
    return None


def family_by_key(key: str) -> Optional[RoleFamily]:
    for f in ALL_FAMILIES:
        if f.key == key:
            return f
    return None


ROLE_LABELS: Dict[str, str] = {f.key: f.label for f in ALL_FAMILIES}
