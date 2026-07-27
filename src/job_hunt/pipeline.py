from __future__ import annotations

from rich.console import Console
from rich.progress import Progress

from .config import AppConfig, load_config
from . import db
from .discover import discover_all
from .match import score_job
from .generate import generate_packet
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
        # also re-score anything already scored below threshold? skip for now
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
        f"([cyan]{core_n}[/] core PM, [magenta]{adj_n}[/] adjacent suggestions)"
    )
    return above


def run_packets(cfg: AppConfig, limit: int | None = None, use_llm: bool = True) -> int:
    limit = limit or (cfg.daily.app_target + cfg.daily.outreach_target)
    floor = min(cfg.filters.min_score, cfg.filters.min_adjacent_score)
    rows = db.list_jobs(status=JobStatus.SCORED, min_score=floor, limit=limit * 3)
    # Prefer not regenerating existing packets; skip companies already applied (inbox)
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
    """Scan Gmail for application receipts. Soft-skip if IMAP not configured."""
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
    console.print(
        f"Inbox: [cyan]{result.get('hits_saved', 0)}[/] receipts · "
        f"[green]{result.get('jobs_marked', 0)}[/] jobs marked applied · "
        f"{len(companies)} companies remembered"
    )
    return result


def run_daily(
    cfg: AppConfig | None = None,
    use_llm: bool = True,
    skip_inbox: bool = False,
) -> None:
    cfg = cfg or load_config()
    db.init_db()
    # 1) Know where you've already applied BEFORE spending LLM on packets
    if not skip_inbox:
        run_inbox_scan()
    # 2) Discover → score
    jobs = run_discover(cfg)
    above = run_score(cfg, jobs)
    # 3) Build applying list: one best role per company (don't over-index Stripe×N)
    target = cfg.daily.app_target + cfg.daily.outreach_target

    def _dedupe_companies(pairs: list, n: int) -> list:
        seen: set[str] = set()
        out = []
        for job, breakdown in pairs:
            if db.is_company_already_applied(job.company):
                continue
            key = (job.company or "").lower().strip()
            if key in seen:
                continue
            seen.add(key)
            out.append(job)
            if len(out) >= n:
                break
        return out

    core = _dedupe_companies([(j, b) for j, b in above if b.track == "core"], target)
    adjacent = _dedupe_companies(
        [(j, b) for j, b in above if b.track == "adjacent"],
        cfg.daily.adjacent_target,
    )
    top_jobs = core + adjacent
    skipped_applied = sum(
        1 for j, _ in above if db.is_company_already_applied(j.company)
    )
    if skipped_applied:
        console.print(f"Skipped [yellow]{skipped_applied}[/] roles at already-applied companies")
    console.print(
        f"Packet targets: [cyan]{len(core)}[/] core + [magenta]{len(adjacent)}[/] adjacent "
        f"(1 best role / company)"
    )
    if top_jobs:
        made = 0
        with Progress() as progress:
            task = progress.add_task("Generating top packets…", total=len(top_jobs))
            for job in top_jobs:
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
        console.print(f"Daily run complete — [green]{made}[/] packets ready for batch review")
    else:
        console.print("[yellow]No jobs above threshold today. Add more boards in config.yaml.[/]")
    console.print("Stats:", db.stats())
    console.print("Next: [bold]hunt review[/] → apply/outreach → [bold]hunt approve <id> --applied[/]")
