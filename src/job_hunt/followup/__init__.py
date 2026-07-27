from __future__ import annotations

from typing import Any

from ..config import AppConfig, load_profile


FOLLOWUP_TEMPLATES = [
    """Hi {name},

Quick bump on my note about the {title} role at {company}. Still very interested — happy to share a 90-day plan or jump on a 15-min call this week.

Best,
Garvit""",
    """Hi {name},

Following up once more on {title} at {company}. I currently lead AI product at BlackRock's Accelerator (RAG platform, +40% bond TTM) and think there's a strong fit with what you're building.

If timing's off, no worries — appreciate you either way.

Garvit""",
    """Hi {name},

Last follow-up from me on {title}. I'll close the loop on my side — if useful later, I'm easy to find: linkedin.com/in/garvitkhurana.

Thanks,
Garvit""",
]


def draft_followup(row: dict[str, Any], cfg: AppConfig) -> dict[str, str]:
    n = int(row.get("followup_count") or 0)
    idx = min(n, len(FOLLOWUP_TEMPLATES) - 1)
    body = FOLLOWUP_TEMPLATES[idx].format(
        name="{name}",
        title=row.get("title") or "the role",
        company=row.get("company") or "your team",
    )
    linkedin = (
        f"Bumping my note on {row.get('title')} @ {row.get('company')} — "
        f"BlackRock AI Product Lead, open to a quick chat."
    )[: cfg.generation.linkedin_max_chars]
    return {
        "email_subject": f"Re: {row.get('email_subject') or row.get('title')}",
        "email_body": body,
        "linkedin_note": linkedin,
        "followup_n": str(n + 1),
    }


def process_due_followups(cfg: AppConfig, auto_mark: bool = False, limit: int = 50) -> list[dict]:
    from ..db import due_followups, mark_sent

    due = due_followups(limit=limit)
    out: list[dict] = []
    for row in due:
        draft = draft_followup(row, cfg)
        item = {**row, "followup_draft": draft}
        out.append(item)
        if auto_mark:
            mark_sent(
                row["id"],
                touch_type="followup",
                channel="email+linkedin",
                content=draft["email_body"],
                followup_n=int(draft["followup_n"]),
                cadence_days=cfg.followup.cadence_days,
            )
    return out
