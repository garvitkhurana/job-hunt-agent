from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, List

from openai import OpenAI

from ..config import AppConfig, env, load_master_resume, load_profile, packets_dir, resumes_dir
from ..models import ApplicationPacket, Job


SYSTEM = """You are Garvit Khurana's job-search writing agent.
Write crisp, specific, high-signal copy. No fluff, no buzzword salad, no fake enthusiasm.
Always ground claims in his real experience (BlackRock AI Accelerator, RAG platform +40% TTM,
private markets governance platform, Columbia MSBA). Never invent employers, metrics, or titles.
Tone: confident, founder-friendly, peer-to-peer.
Return ONLY valid JSON matching the requested schema."""

# NVIDIA NIM + OpenRouter both speak the OpenAI Chat Completions wire format —
# same HTTP shape, different hosts/keys/models. Convenience, not a hard requirement.
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_NVIDIA_MODEL = "nvidia/llama-3.3-nemotron-super-49b-v1"
DEFAULT_OPENROUTER_MODEL = "nvidia/llama-3.3-nemotron-super-49b-v1:free"


@dataclass
class LLMProvider:
    name: str
    client: OpenAI
    model: str


def _providers() -> List[LLMProvider]:
    """Build provider chain: NVIDIA → OpenRouter → OpenAI (whichever keys are set)."""
    chain: List[LLMProvider] = []
    timeout = float(env("LLM_TIMEOUT_SEC", "90") or "90")

    nvidia = env("NVIDIA_API_KEY")
    if nvidia:
        chain.append(
            LLMProvider(
                name="nvidia",
                client=OpenAI(
                    api_key=nvidia,
                    base_url=env("NVIDIA_BASE_URL", NVIDIA_BASE_URL) or NVIDIA_BASE_URL,
                    timeout=timeout,
                ),
                model=env("NVIDIA_MODEL", DEFAULT_NVIDIA_MODEL) or DEFAULT_NVIDIA_MODEL,
            )
        )

    openrouter = env("OPENROUTER_API_KEY")
    if openrouter:
        chain.append(
            LLMProvider(
                name="openrouter",
                client=OpenAI(
                    api_key=openrouter,
                    base_url=env("OPENROUTER_BASE_URL", OPENROUTER_BASE_URL) or OPENROUTER_BASE_URL,
                    timeout=timeout,
                    default_headers={
                        "HTTP-Referer": env(
                            "OPENROUTER_REFERER",
                            "https://github.com/garvitkhurana/job-hunt-agent",
                        )
                        or "https://github.com/garvitkhurana/job-hunt-agent",
                        "X-Title": "job-hunt-agent",
                    },
                ),
                model=env("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL) or DEFAULT_OPENROUTER_MODEL,
            )
        )

    openai_key = env("OPENAI_API_KEY")
    if openai_key:
        chain.append(
            LLMProvider(
                name="openai",
                client=OpenAI(api_key=openai_key, timeout=timeout),
                model=env("OPENAI_MODEL", "gpt-4.1-mini") or "gpt-4.1-mini",
            )
        )

    return chain


def _has_llm_key() -> bool:
    return bool(_providers())


def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        return json.loads(m.group(0))
    raise json.JSONDecodeError("no json object found", text, 0)


def _chat_once(provider: LLMProvider, messages: list, temperature: float) -> dict:
    kwargs = {
        "model": provider.model,
        "temperature": temperature,
        "messages": messages,
    }
    try:
        resp = provider.client.chat.completions.create(
            **kwargs, response_format={"type": "json_object"}
        )
        return _extract_json(resp.choices[0].message.content or "{}")
    except Exception:
        resp = provider.client.chat.completions.create(**kwargs)
        return _extract_json(resp.choices[0].message.content or "{}")


def _chat_json(messages: list, temperature: float = 0.4) -> dict:
    """Try each configured provider until one succeeds (more free quota via failover)."""
    providers = _providers()
    if not providers:
        raise RuntimeError(
            "No LLM key set. Add NVIDIA_API_KEY (build.nvidia.com) and/or "
            "OPENROUTER_API_KEY (openrouter.ai) to .env"
        )
    errors: List[str] = []
    for p in providers:
        try:
            return _chat_once(p, messages, temperature)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{p.name}/{p.model}: {e}")
            continue
    raise RuntimeError("All LLM providers failed — " + " | ".join(errors))


def _fallback_packet(job: Job, profile: dict[str, Any], resume: str, cfg: AppConfig) -> ApplicationPacket:
    from ..match.roles import classify

    company = job.company
    title = job.title
    family = classify(job.title)
    is_adjacent = bool(family and family.track == "adjacent")
    hook = profile.get("pitch_angles", {}).get("outreach_hook", "").strip()
    linkedin = (
        f"Hi — Product Lead on BlackRock's AI Accelerator (RAG platform, +40% bond TTM). "
        f"Curious about {title} at {company}. Open to a quick chat?"
    )[: cfg.generation.linkedin_max_chars]
    sched = cfg.candidate.resolved_scheduling_link()
    sched_line = f"\nGrab a time that works: {sched}\n" if sched else ""
    email_subject = f"{title} at {company} — Garvit Khurana"
    email_body = f"""Hi {{name}},

I'm Garvit — Product Lead / Engineer II on BlackRock's AI Accelerator. I own the roadmap for a RAG-based security creation tool that cut time-to-market for new public bonds by 40% and now supports private credit workflows.

I'm exploring senior / founding PM roles and {company}'s {title} stood out. Happy to share a short note on how I'd approach the first 90 days.
{sched_line}
Resume attached. LinkedIn: {profile.get('linkedin') or 'linkedin.com/in/garvitkhurana'}

Best,
Garvit
+1 (646) 763-3302
"""
    if is_adjacent:
        closing = (
            "I'm looking for a role where product judgment and hands-on AI engineering both "
            "matter — this looks like exactly that kind of seat."
        )
    else:
        closing = (
            "I'm looking for a senior or founding PM seat where I can own discovery, roadmap, "
            "and AI product depth end-to-end."
        )

    cover = f"""Dear {company} hiring team,

I'm a Product Lead / Engineer II on BlackRock's AI Accelerator applying for {title}. I currently own product strategy for an LLM/RAG platform used across capital-markets workflows — improving time-to-market for new public bonds by 40% and expanding into private credit. I build as well as define: Python, SQL, and LangChain are day-to-day tools for me.

Previously I launched BlackRock's Private Governance Platform (−80% manual intervention) and have startup growth-product experience. {closing}

I'd welcome the chance to discuss how I can contribute at {company}.

Sincerely,
Garvit Khurana
"""
    # Light keyword injection into summary line
    tailored = resume
    if job.description:
        kws = []
        for kw in ("AI", "LLM", "RAG", "platform", "fintech", "B2B", "founding", "senior"):
            if kw.lower() in (job.title + job.description).lower():
                kws.append(kw)
        if kws:
            tailored = resume.replace(
                "Seeking Senior / Founding Product roles at high-growth startups.",
                f"Seeking Senior / Founding Product roles at high-growth startups. Relevant focus: {', '.join(kws[:5])}.",
            )
    return ApplicationPacket(
        job_id=job.id,
        tailored_resume_md=tailored,
        cover_letter=cover.strip(),
        linkedin_note=linkedin,
        email_subject=email_subject,
        email_body=email_body.strip(),
        founder_pitch=hook or email_body,
        apply_checklist=[
            f"Open apply URL: {job.url}",
            "Paste tailored resume (export PDF from markdown if needed)",
            "Paste cover letter into portal / attach",
            "Send LinkedIn note to hiring manager / founder",
            "Send personalized email if address known",
            "Mark as applied in CLI: hunt approve <job_id> --applied",
        ],
    )


def generate_packet(job: Job, cfg: AppConfig, use_llm: bool = True) -> ApplicationPacket:
    from ..match.roles import classify

    profile = load_profile()
    resume = load_master_resume()
    family = classify(job.title)

    if not use_llm or not _has_llm_key():
        return _fallback_packet(job, profile, resume, cfg)

    schema_hint = {
        "tailored_resume_md": "full markdown resume, lightly tailored (keep truth intact)",
        "cover_letter": "<=220 words",
        "linkedin_note": "<=280 chars connection/InMail note",
        "email_subject": "short subject",
        "email_body": "<=160 words, use {name} placeholder",
        "founder_pitch": "2-4 sentence founding-PM pitch specific to this company",
        "apply_checklist": ["step", "..."],
    }
    user = f"""Create an application packet for this role.

CANDIDATE PROFILE JSON:
{json.dumps(profile, indent=2)[:6000]}

MASTER RESUME (markdown):
{resume[:7000]}

JOB:
company: {job.company}
title: {job.title}
location: {job.location}
url: {job.url}
description:
{job.description[:5000]}

ROLE CLASSIFICATION: {family.label if family else 'Product role'} ({family.track if family else 'core'} track)
{f"POSITIONING ANGLE: {family.why}" if family else ""}

CONSTRAINTS:
- tone: {cfg.generation.tone}
- linkedin_note max {cfg.generation.linkedin_max_chars} chars
- email_body max ~{cfg.generation.email_max_words} words
- cover_letter max ~{cfg.generation.cover_max_words} words
- tailor resume by reordering emphasis / summary keywords only — do NOT invent experience
- if this looks founding/early, lean into founding pitch
- if this is an ADJACENT (non-PM) role, lead with the hybrid product+engineering
  angle and the specific transferable proof points; do not pretend it's a PM job
{f"- include this scheduling link naturally near the CTA in email_body: {cfg.candidate.resolved_scheduling_link()}" if cfg.candidate.resolved_scheduling_link() else "- do not fabricate a scheduling link"}

Return JSON keys exactly: {list(schema_hint.keys())}
"""
    try:
        data = _chat_json(
            [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0.4,
        )
        packet = ApplicationPacket(
            job_id=job.id,
            tailored_resume_md=data.get("tailored_resume_md") or resume,
            cover_letter=data.get("cover_letter") or "",
            linkedin_note=(data.get("linkedin_note") or "")[: cfg.generation.linkedin_max_chars],
            email_subject=data.get("email_subject") or f"{job.title} — Garvit Khurana",
            email_body=data.get("email_body") or "",
            founder_pitch=data.get("founder_pitch") or "",
            apply_checklist=list(data.get("apply_checklist") or []),
        )
    except Exception:
        packet = _fallback_packet(job, profile, resume, cfg)

    # Persist artifacts to disk for easy copy/paste
    slug = re.sub(r"[^a-z0-9]+", "-", f"{job.company}-{job.title}".lower()).strip("-")[:60]
    packet_path = packets_dir() / f"{job.id}_{slug}.md"
    resume_path = resumes_dir() / f"{job.id}_{slug}.md"
    resume_path.write_text(packet.tailored_resume_md)
    packet_path.write_text(
        f"""# Packet — {job.company} / {job.title}

**URL:** {job.url}
**Location:** {job.location}
**Job ID:** {job.id}

## LinkedIn note
{packet.linkedin_note}

## Email subject
{packet.email_subject}

## Email body
{packet.email_body}

## Founder pitch
{packet.founder_pitch}

## Cover letter
{packet.cover_letter}

## Apply checklist
{chr(10).join(f'- {c}' for c in packet.apply_checklist)}

## Tailored resume
See: `{resume_path}`
"""
    )
    return packet
