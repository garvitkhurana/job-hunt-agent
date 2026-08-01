from __future__ import annotations

from rich.console import Console

from .config import AppConfig, load_config
from . import db
from .discover import discover_all
from .match import score_job
from .models import JobStatus

console = Console()


def run_discover(cfg: AppConfig) -> list:
    boards_gh = list(dict.fromkeys(cfg.sources.greenhouse_boards + cfg.sources.extra_greenhouse))
    boards_ashby = list(dict.fromkeys(cfg.sources.ashby_boards + cfg.sources.extra_ashby))
    console.print(f"[bold]Discovering[/] from {len(boards_gh)} Greenhouse + {len(boards_ashby)} Ashby boards…")
    jobs = discover_all(
        greenhouse_boards=boards_gh,
        ashby_boards=boards_ashby,
        yc_enabled=cfg.sources.yc_enabled,
        max_total=cfg.daily.max_discover,
        include_adjacent=cfg.filters.include_adjacent_roles,
    )
    console.print(f"Found [cyan]{len(jobs)}[/] raw roles")
    for job in jobs:
        db.upsert_job(job, status=JobStatus.DISCOVERED)
    return jobs


def run_score(cfg: AppConfig, jobs: list | None = None) -> list[tuple]:
    if jobs is None:
        rows = db.list_jobs(status=JobStatus.DISCOVERED, limit=500)
        jobs = [db.job_from_row(r) for r in rows]
    scored = []
    for job in jobs:
        breakdown = score_job(job, cfg)
        db.upsert_job(job)
        db.update_score(job.id, breakdown.total, breakdown.model_dump(), status=JobStatus.SCORED)
        scored.append((job, breakdown))
    scored.sort(key=lambda x: x[1].total, reverse=True)
    above = [
        s
        for s in scored
        if s[1].total
        >= (cfg.filters.min_adjacent_score if s[1].track == "adjacent" else cfg.filters.min_score)
    ]
    core_n = sum(1 for s in above if s[1].track == "core")
    adj_n = len(above) - core_n
    console.print(
        f"Scored {len(scored)} · qualifying: [green]{len(above)}[/] "
        f"([cyan]{core_n}[/] core PM, [magenta]{adj_n}[/] adjacent)"
    )
    return above


def run_packets(cfg: AppConfig, limit: int | None = None, use_llm: bool = True) -> int:
    """Optional / legacy — daily path no longer generates packets."""
    from .generate import generate_packet
    from rich.progress import Progress

    limit = limit or (cfg.daily.app_target + cfg.daily.outreach_target)
    floor = min(cfg.filters.min_score, cfg.filters.min_adjacent_score)
    rows = db.list_jobs(status=JobStatus.SCORED, min_score=floor, limit=limit * 3)
    existing = {r["id"] for r in db.list_jobs(status=JobStatus.PACKET_READY, limit=500)}
    candidates = [
        r
        for r in rows
        if r["id"] not in existing and not db.is_company_already_applied(r["company"])
    ][:limit]
    if not candidates:
        console.print("[yellow]No new scored jobs needing packets.[/]")
        return 0
    made = 0
    with Progress() as progress:
        task = progress.add_task("Generating packets…", total=len(candidates))
        for row in candidates:
            job = db.job_from_row(row)
            packet = generate_packet(job, cfg, use_llm=use_llm)
            db.save_packet(
                job.id,
                {
                    "tailored_resume_md": packet.tailored_resume_md,
                    "cover_letter": packet.cover_letter,
                    "linkedin_note": packet.linkedin_note,
                    "email_subject": packet.email_subject,
                    "email_body": packet.email_body,
                    "founder_pitch": packet.founder_pitch,
                    "apply_checklist": packet.apply_checklist,
                },
            )
            made += 1
            progress.advance(task)
    console.print(f"Generated [green]{made}[/] application packets → data/packets/")
    return made


def run_inbox_scan(days: int = 180, mark_applied: bool = True) -> dict | None:
    """Scan Gmail for application + rejection emails. Soft-skip if IMAP not configured."""
    import os

    user = os.getenv("IMAP_USER") or os.getenv("SMTP_USER") or ""
    password = os.getenv("IMAP_PASS") or os.getenv("SMTP_PASS") or ""
    if not user or not password or password.startswith("your-"):
        console.print(
            "[dim]Inbox scan skipped — add SMTP_USER + SMTP_PASS (Gmail app password) to .env[/]"
        )
        return None
    from .inbox import scan_inbox

    result = scan_inbox(days=days, mark_applied=mark_applied, dry_run=False)
    companies = result.get("companies") or []
    synced = db.sync_applied_companies_from_jobs()
    parked = db.park_packets_at_applied_companies()
    console.print(
        f"Inbox: [cyan]{result.get('hits_saved', 0)}[/] emails · "
        f"applied jobs [green]{result.get('jobs_marked', 0)}[/] · "
        f"rejected [yellow]{result.get('jobs_rejected', 0)}[/] · "
        f"{len(companies)} companies blocked"
    )
    if synced or parked:
        console.print(f"[dim]Synced {synced} cos · parked {parked} stale packets[/]")
    return result


def run_daily(
    cfg: AppConfig | None = None,
    use_llm: bool = True,  # kept for CLI compat; ignored — no LLM in daily
    skip_inbox: bool = False,
) -> None:
    """Look+apply loop: inbox → discover → score → board (no LLM packets)."""
    _ = use_llm
    cfg = cfg or load_config()
    db.init_db()
    if not skip_inbox:
        run_inbox_scan()
    jobs = run_discover(cfg)
    above = run_score(cfg, jobs)
    skipped_applied = sum(1 for j, _ in above if db.is_company_already_applied(j.company))
    if skipped_applied:
        console.print(f"Skipped [yellow]{skipped_applied}[/] roles at already-applied companies")

    # One best role per company across core + adjacent
    review = db.queue_for_review(limit=cfg.daily.app_target + cfg.daily.adjacent_target, one_per_company=True)
    core_n = sum(1 for r in review if (r.get("track") or "core") == "core")
    adj_n = len(review) - core_n
    console.print(
        f"Review ready: [green]{len(review)}[/] companies "
        f"([cyan]{core_n}[/] core · [magenta]{adj_n}[/] adjacent) — 1 best role each"
    )
    console.print("Stats:", db.stats())
    console.print("Next: [bold]hunt ui[/] or [bold]hunt board[/] → apply → mark applied")
