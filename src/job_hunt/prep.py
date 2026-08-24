"""On-demand application prep — drafter + reviewer. Not part of daily."""
from __future__ import annotations

import json
import re
from pathlib import Path

from rich.console import Console

from . import db
from .config import ROOT, AppConfig, load_config, load_master_resume, load_profile
from .llm import chat_json, has_llm_key
from .models import Job

console = Console()

DRAFT_SYSTEM = """You are Garvit Khurana's application writing agent.
Ground every claim in his real experience (BlackRock AI Accelerator RAG +40% bond TTM,
Private Governance Platform −80% manual, Columbia MSBA). Never invent employers or metrics.
Tone: confident, specific, peer-to-peer. Return ONLY valid JSON."""

REVIEW_SYSTEM = """You are a critical hiring-manager reviewer.
Flag generic phrasing, missing proof, weak company specificity, and invented claims.
Return ONLY valid JSON with keys: issues (string[]), revised (object matching draft keys you change)."""


def prep_dir(company: str) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-")[:40]
    d = ROOT / "output" / "prep" / slug
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_prep(job_id: str, cfg: AppConfig | None = None, use_llm: bool = True) -> Path:
    cfg = cfg or load_config()
    db.init_db()
    row = db.get_job(job_id)
    if not row:
        raise ValueError(f"Job not found: {job_id}")
    job = db.job_from_row(row)
    profile = load_profile()
    resume = load_master_resume()
    out = prep_dir(job.company)

    research = {
        "company": job.company,
        "title": job.title,
        "url": job.url,
        "location": job.location,
        "notes": f"Role at {job.company}. JD excerpt used for prep.",
    }
    (out / "research.json").write_text(json.dumps(research, indent=2))

    if not use_llm or not has_llm_key():
        draft = _fallback(job, profile)
        (out / "draft.json").write_text(json.dumps(draft, indent=2))
        _write_md(out, job, draft, reviewed=False)
        db.set_prepped(job_id, True)
        db.log_event("prep", job_id=job_id, payload={"llm": False, "path": str(out)})
        console.print(f"[yellow]Prep without LLM[/] → {out}")
        return out

    user = f"""Draft application materials for this role.

PROFILE:
{json.dumps(profile, indent=2)[:5000]}

RESUME (excerpt):
{resume[:5000]}

JOB:
company: {job.company}
title: {job.title}
location: {job.location}
url: {job.url}
description:
{(job.description or '')[:4500]}

Return JSON keys:
linkedin_note (<=280 chars),
cover_letter (<=220 words),
founder_pitch (2-4 sentences),
ninety_day_plan (3 bullets),
resume_emphasis (3-5 bullets to lead with — true facts only)
"""
    draft = chat_json(
        [{"role": "system", "content": DRAFT_SYSTEM}, {"role": "user", "content": user}],
        temperature=0.4,
    )
    (out / "draft.json").write_text(json.dumps(draft, indent=2))

    review_user = f"""Review this draft for {job.company} / {job.title}.

DRAFT:
{json.dumps(draft, indent=2)}

JD excerpt:
{(job.description or '')[:2500]}
"""
    try:
        critique = chat_json(
            [
                {"role": "system", "content": REVIEW_SYSTEM},
                {"role": "user", "content": review_user},
            ],
            temperature=0.2,
        )
    except Exception as e:  # noqa: BLE001
        critique = {"issues": [f"reviewer_failed: {e}"], "revised": {}}
    (out / "review.json").write_text(json.dumps(critique, indent=2))

    final = dict(draft)
    revised = critique.get("revised") or {}
    if isinstance(revised, dict):
        final.update({k: v for k, v in revised.items() if v})
    (out / "final.json").write_text(json.dumps(final, indent=2))
    _write_md(out, job, final, reviewed=True, issues=critique.get("issues") or [])

    db.set_prepped(job_id, True)
    db.log_event(
        "prep",
        job_id=job_id,
        payload={"llm": True, "path": str(out), "issues": len(critique.get("issues") or [])},
    )
    console.print(f"[green]Prep ready[/] → {out}")
    return out


def _fallback(job: Job, profile: dict) -> dict:
    return {
        "linkedin_note": (
            f"Hi — Product Lead on BlackRock's AI Accelerator (RAG, +40% bond TTM). "
            f"Curious about {job.title} at {job.company}."
        )[:280],
        "cover_letter": (
            f"I'm exploring {job.title} at {job.company}. At BlackRock I own AI product "
            f"strategy for a RAG security-creation platform (+40% TTM) and previously "
            f"shipped a Private Governance Platform (−80% manual). Happy to share more."
        ),
        "founder_pitch": (
            "Operator who ships AI into regulated workflows — discovery, roadmap, and hands-on "
            "Python/SQL/LangChain. Looking for senior/founding PM ownership."
        ),
        "ninety_day_plan": [
            "Map the workflow and failure modes with users in week 1–2",
            "Ship one measurable AI/workflow wedge by day 45",
            "Instrument adoption and cut a clear Q2 roadmap",
        ],
        "resume_emphasis": [
            "BlackRock AI Accelerator — RAG security creation +40% TTM",
            "Private credit expansion of same platform",
            "Private Governance Platform −80% manual / +20% accuracy",
        ],
    }


def _write_md(out: Path, job: Job, data: dict, reviewed: bool, issues: list | None = None) -> None:
    issues = issues or []
    body = f"""# Prep — {job.company} / {job.title}

**URL:** {job.url}
**Reviewed:** {reviewed}

## LinkedIn note
{data.get('linkedin_note') or ''}

## Cover letter
{data.get('cover_letter') or ''}

## Founder pitch
{data.get('founder_pitch') or ''}

## 90-day plan
{chr(10).join(f'- {x}' for x in (data.get('ninety_day_plan') or []))}

## Resume emphasis
{chr(10).join(f'- {x}' for x in (data.get('resume_emphasis') or []))}

## Reviewer issues
{chr(10).join(f'- {x}' for x in issues) or '- (none)'}
"""
    (out / "PREP.md").write_text(body)
