from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .config import ROOT, db_path, load_config
from . import db
from .models import JobStatus
from .pipeline import run_daily, run_discover, run_packets, run_rescore, run_score
from .followup import process_due_followups

app = typer.Typer(
    help="Job hunt agent — look → apply → mark. Metrics-first.",
    no_args_is_help=True,
)
legacy_app = typer.Typer(help="Legacy writer/outreach commands (soft-deprecated).")
app.add_typer(legacy_app, name="legacy")
console = Console()


@app.command()
def init() -> None:
    """Initialize DB and copy .env if missing."""
    db.init_db()
    env_path = ROOT / ".env"
    example = ROOT / ".env.example"
    if not env_path.exists() and example.exists():
        env_path.write_text(example.read_text())
        console.print(
            "[yellow]Created .env — add SMTP for inbox; ANTHROPIC_API_KEY for hunt prep[/]"
        )
    console.print(f"[green]DB ready:[/] {db_path()}")


@app.command("daily")
def daily(
    no_llm: bool = typer.Option(False, help="Ignored — daily no longer generates LLM packets"),
    skip_inbox: bool = typer.Option(False, help="Skip Gmail already-applied scan"),
) -> None:
    """Look+apply loop: inbox → discover → score → board (no email packets)."""
    cfg = load_config()
    db.init_db()
    run_daily(cfg, use_llm=not no_llm, skip_inbox=skip_inbox)


@app.command()
def metrics(
    days: int = typer.Option(30, help="Lookback window"),
    baseline: bool = typer.Option(False, "--baseline", help="Write/overwrite output/metrics/baseline.json"),
) -> None:
    """Funnel KPIs — precision, applies/week, outcomes, prep lift."""
    from .metrics import print_metrics, write_baseline, compute_metrics

    db.init_db()
    m = print_metrics(since_days=days, save_baseline=baseline)
    if baseline:
        path = write_baseline(m)
        console.print(f"[green]Baseline saved[/] {path}")


@app.command()
def outcome(
    job_id: str,
    result: str = typer.Argument(..., help="interview | rejected | ghost"),
) -> None:
    """Log application outcome for metrics (closes the funnel)."""
    db.init_db()
    result = result.strip().lower()
    if result not in ("interview", "rejected", "ghost"):
        console.print("[red]result must be interview | rejected | ghost[/]")
        raise typer.Exit(1)
    job = db.get_job(job_id)
    if not job:
        console.print("[red]Job not found[/]")
        raise typer.Exit(1)
    db.set_outcome(job_id, result)
    console.print(f"[green]Outcome[/] {job['company']} → {result}")


@app.command()
def prep(
    job_id: str,
    no_llm: bool = typer.Option(False, help="Template-only prep (no API)"),
) -> None:
    """On-demand materials: research + draft + reviewer → output/prep/."""
    from .prep import run_prep

    db.init_db()
    path = run_prep(job_id, use_llm=not no_llm)
    console.print(f"Open [bold]{path / 'PREP.md'}[/]")


@app.command()
def discover() -> None:
    cfg = load_config()
    db.init_db()
    run_discover(cfg)


@app.command()
def score() -> None:
    """Score newly discovered jobs only."""
    cfg = load_config()
    db.init_db()
    run_score(cfg)


@app.command()
def rescore() -> None:
    """Re-score all open jobs so filter changes rewrite the board (fixes stale junk)."""
    cfg = load_config()
    db.init_db()
    run_rescore(cfg)
    review = db.queue_for_review(limit=20, one_per_company=True)
    console.print(f"Review now: [green]{len(review)}[/] companies")
    for r in review[:10]:
        console.print(
            f"  {r.get('score', 0):.2f} [{r.get('track') or 'core'}] "
            f"{r['company']} — {r['title'][:55]}"
        )


@legacy_app.command("packets")
def packets(
    limit: int = typer.Option(70, help="Max packets to generate"),
    no_llm: bool = typer.Option(False),
) -> None:
    """[legacy] Generate LLM application packets."""
    cfg = load_config()
    db.init_db()
    run_packets(cfg, limit=limit, use_llm=not no_llm)


@app.command()
def review(
    limit: int = typer.Option(30, help="How many companies to show (one best role each)"),
    track: Optional[str] = typer.Option(None, help="Filter: core | adjacent"),
    all_roles: bool = typer.Option(False, "--all-roles", help="Show every role (no company dedupe)"),
) -> None:
    """Batch review queue — one best role per company by default."""
    db.init_db()
    rows = db.queue_for_review(limit=limit, track=track, one_per_company=not all_roles)
    if not rows:
        console.print("[yellow]Queue empty. Run: hunt daily[/]")
        raise typer.Exit()

    table = Table(title=f"Review queue ({len(rows)} companies)" if not all_roles else f"Review queue ({len(rows)} roles)")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Score", justify="right")
    table.add_column("Track")
    table.add_column("Company")
    table.add_column("Title")
    table.add_column("Location")
    table.add_column("+", justify="right", style="dim")
    for r in rows:
        t = r.get("track") or "core"
        siblings = int(r.get("sibling_roles") or 0)
        table.add_row(
            r["id"],
            f"{r.get('score', 0):.2f}",
            "[cyan]core[/]" if t == "core" else "[magenta]adj[/]",
            r["company"][:20],
            r["title"][:40],
            (r.get("location") or "")[:22],
            f"+{siblings}" if siblings else "",
        )
    console.print(table)
    if not all_roles:
        console.print("[dim]+N = other scored/ready roles at same company (hidden to avoid over-indexing)[/]")
        console.print("See all roles: [bold]hunt review --all-roles[/]")
    console.print("\nApply on ATS, then: [bold]hunt approve <id> --applied[/]")
    console.print("Or prep materials: [bold]hunt prep <id>[/]")
    console.print("Skip: [bold]hunt skip <id>[/] · Funnel: [bold]hunt metrics[/]")


@app.command()
def suggest(
    limit: int = typer.Option(20, help="How many suggestions to show"),
    min_score: float = typer.Option(0.0, help="Minimum match score"),
) -> None:
    """Adjacent roles worth considering — strong non-PM fits at good companies."""
    from .match.company import TIER_LABELS
    from .match.roles import family_by_key

    db.init_db()
    rows = db.suggestions(limit=limit, min_score=min_score)
    if not rows:
        console.print("[yellow]No adjacent suggestions yet. Run: hunt daily[/]")
        raise typer.Exit()

    table = Table(title=f"Suggested adjacent roles ({len(rows)})")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Score", justify="right")
    table.add_column("Company")
    table.add_column("Tier")
    table.add_column("Title")
    table.add_column("Role family")
    for r in rows:
        table.add_row(
            r["id"],
            f"{r.get('score', 0):.2f}",
            r["company"][:18],
            TIER_LABELS.get(r.get("company_tier") or "unknown", "?"),
            r["title"][:34],
            (family_by_key(r.get("role_family") or "") or type("x", (), {"label": "-"})).label[:26],
        )
    console.print(table)

    seen: set = set()
    console.print("\n[bold]Why these fit your profile[/]")
    for r in rows:
        key = r.get("role_family") or ""
        if key in seen:
            continue
        seen.add(key)
        fam = family_by_key(key)
        if fam:
            console.print(f"  [magenta]{fam.label}[/] — {fam.why}")

    console.print("\nGenerate materials for these: [bold]hunt packets[/]")
    console.print("Review just these: [bold]hunt review --track adjacent[/]")


@legacy_app.command("contacts")
def contacts(job_id: str) -> None:
    """Build an outreach target sheet — who to contact + LinkedIn search URLs + email guesses."""
    from .contacts import find_contacts
    from .send import active_provider

    db.init_db()
    row = db.get_job(job_id)
    if not row:
        console.print("[red]Job not found[/]")
        raise typer.Exit(1)
    job = db.job_from_row(row)
    sheet = find_contacts(job)

    console.print(f"[bold]{job.company}[/] — {job.title}")
    console.print(f"Domain: {sheet['domain'] or '?'}  ", end="")
    if sheet.get("hunter_pattern"):
        console.print(f"Email pattern: [green]{sheet['hunter_pattern']}[/]")
    else:
        console.print("")
    if active_provider() == "none":
        console.print("[yellow]No APOLLO_API_KEY set — showing LinkedIn search URLs + pattern guesses only.[/]")

    for block in sheet["personas"]:
        console.print(f"\n[bold cyan]#{block['priority']} {block['label']}[/]  ({block['channel']})")
        console.print(f"  {block['note']}")
        console.print(f"  LinkedIn search: {block['linkedin_search']}")
        console.print(f"  Company people:  {block['linkedin_company_people']}")
        if block["people"]:
            for p in block["people"]:
                email = p.get("email") or (p.get("email_guesses") or ["?"])[0]
                console.print(f"    • {p['name']} — {p['title']}  <{email}>  {p.get('linkedin','')}")
        elif block.get("email_guesses"):
            console.print(f"    email guesses: {', '.join(block['email_guesses'])}")
    console.print("\nDraft is in the packet: [bold]hunt show " + job_id + "[/]")
    console.print("Send email (dry-run): [bold]hunt send " + job_id + " --to someone@company.com[/]")


@legacy_app.command("send")
def send(
    job_id: str,
    to: str = typer.Option(..., "--to", help="Recipient email"),
    name: str = typer.Option("there", help="Recipient first name for {name}"),
    send: bool = typer.Option(False, "--send", help="Actually send (default is dry-run)"),
    no_attach: bool = typer.Option(False, help="Do not attach resume PDF"),
) -> None:
    """Send (or dry-run) the personalized email for a job. Logs + arms follow-up cadence."""
    from .send import send_email, active_provider

    cfg = load_config()
    db.init_db()
    packet = db.get_packet(job_id)
    job = db.get_job(job_id)
    if not packet or not job:
        console.print("[red]No packet — run hunt legacy packets first[/]")
        raise typer.Exit(1)
    subject = packet.get("email_subject") or f"{job['title']} — Garvit Khurana"
    body = (packet.get("email_body") or "").replace("{name}", name)

    result = send_email(to, subject, body, attach_resume=not no_attach, send=send)
    console.print(str(result))
    if send and result.ok:
        db.mark_sent(job_id, "email", "email", body, 0, cfg.followup.cadence_days)
        console.print(f"[green]Logged + follow-up armed[/] ({active_provider()})")
    elif result.dry_run:
        console.print("[yellow]Dry-run — add --send to actually deliver.[/] Provider: " + active_provider())


@legacy_app.command("send-batch")
def send_batch(
    limit: int = typer.Option(20, help="Max emails"),
    send: bool = typer.Option(False, "--send", help="Actually send"),
    track: Optional[str] = typer.Option(None, help="core | adjacent"),
) -> None:
    """Batch-send emails for queued packets that already have a recipient email in notes.

    For safety this only sends where you've recorded an email (hunt set-email),
    so you never blast a guessed address by accident.
    """
    from .send import send_email, active_provider

    cfg = load_config()
    db.init_db()
    targets = db.emailable(limit=limit * 3, track=track)
    if not targets:
        console.print("[yellow]No queued packets have a recorded email yet.[/]")
        console.print("Add one: [bold]hunt set-email <id> person@company.com[/] (get it via hunt contacts)")
        raise typer.Exit()
    sent = 0
    for r in targets[:limit]:
        to = (r["notes"].split("email:", 1)[1].strip().split()[0])
        subject = r.get("email_subject") or f"{r['title']} — Garvit Khurana"
        body = (r.get("email_body") or "").replace("{name}", "there")
        res = send_email(to, subject, body, attach_resume=True, send=send)
        console.print(f"{r['company'][:18]:18} {res}")
        if send and res.ok:
            db.mark_sent(r["id"], "email", "email", body, 0, cfg.followup.cadence_days)
            sent += 1
    console.print(f"\n{'Sent' if send else 'Dry-ran'} {sent or len(targets[:limit])} ({active_provider()})")


@legacy_app.command("set-email")
def set_email(job_id: str, email: str) -> None:
    """Record the recipient email for a job (used by send-batch)."""
    db.init_db()
    db.set_status_note(job_id, f"email: {email}")
    console.print(f"[green]Recorded[/] {email} for {job_id}")


@legacy_app.command("show")
def show(job_id: str) -> None:
    db.init_db()
    job = db.get_job(job_id)
    packet = db.get_packet(job_id)
    if not job:
        console.print("[red]Job not found[/]")
        raise typer.Exit(1)
    console.print(f"[bold]{job['company']}[/] — {job['title']}  (score={job.get('score')})")
    console.print(f"URL: {job.get('url')}")
    console.print(f"Location: {job.get('location')}  status={job.get('status')}")
    if (job.get("track") or "core") == "adjacent":
        from .match.roles import family_by_key

        fam = family_by_key(job.get("role_family") or "")
        console.print(f"[magenta]Adjacent suggestion[/] — {fam.label if fam else '?'}")
        if fam:
            console.print(f"  Why: {fam.why}")
    if packet:
        console.print("\n[bold cyan]LinkedIn[/]\n" + (packet.get("linkedin_note") or ""))
        console.print("\n[bold cyan]Email[/]\n" + (packet.get("email_subject") or ""))
        console.print(packet.get("email_body") or "")
        console.print("\n[bold cyan]Cover[/]\n" + (packet.get("cover_letter") or "")[:1200])
        console.print("\n[bold cyan]Founder pitch[/]\n" + (packet.get("founder_pitch") or ""))
        checklist = json.loads(packet.get("apply_checklist_json") or "[]")
        if checklist:
            console.print("\n[bold]Checklist[/]")
            for c in checklist:
                console.print(f"  • {c}")
    # Disk packet
    matches = list((ROOT / "data" / "packets").glob(f"{job_id}_*.md"))
    if matches:
        console.print(f"\nFull packet file: {matches[0]}")


@app.command()
def approve(
    job_id: str,
    applied: bool = typer.Option(False, help="Mark job application submitted"),
    outreach: bool = typer.Option(False, help="Mark LinkedIn/email outreach sent"),
) -> None:
    """Mark a packet as sent — starts follow-up cadence (day 3 / 7 / 14)."""
    cfg = load_config()
    db.init_db()
    job = db.get_job(job_id)
    packet = db.get_packet(job_id)
    if not job:
        console.print("[red]Not found[/]")
        raise typer.Exit(1)
    if not applied and not outreach:
        outreach = True
    if applied:
        hours = db.hours_since_created(job_id)
        db.mark_sent(
            job_id,
            touch_type="application",
            channel="portal",
            content=(packet or {}).get("cover_letter") or "",
            followup_n=0,
            cadence_days=cfg.followup.cadence_days,
        )
        db.remember_applied_company(job["company"], source="manual", title=job.get("title") or "")
        parked = db.park_packets_at_applied_companies()
        db.log_event(
            "applied",
            job_id=job_id,
            payload={
                "company": job["company"],
                "hours_to_applied": hours,
                "prepped": int(job.get("prepped") or 0),
            },
        )
        console.print(f"[green]Marked APPLIED[/] {job['company']} — follow-ups scheduled")
        if parked:
            console.print(f"[dim]Parked {parked} other roles at already-applied companies[/]")
    if outreach:
        db.mark_sent(
            job_id,
            touch_type="linkedin" if not applied else "email",
            channel="linkedin+email",
            content=(packet or {}).get("email_body") or (packet or {}).get("linkedin_note") or "",
            followup_n=0,
            cadence_days=cfg.followup.cadence_days,
        )
        console.print(f"[green]Marked OUTREACH SENT[/] {job['company']} — follow-ups scheduled")


@legacy_app.command("approve-all")
def approve_all(
    outreach: bool = typer.Option(True, help="Mark all queued as outreach sent"),
    applied: bool = typer.Option(False, help="Mark all queued as applied"),
    limit: int = typer.Option(50),
) -> None:
    """Batch-approve the review queue — 'let it fly' after you've skimmed."""
    cfg = load_config()
    db.init_db()
    rows = db.queue_for_review(limit=limit)
    if not rows:
        console.print("[yellow]Nothing to approve[/]")
        raise typer.Exit()
    for r in rows:
        if applied:
            db.mark_sent(r["id"], "application", "portal", r.get("cover_letter") or "", 0, cfg.followup.cadence_days)
        if outreach:
            db.mark_sent(r["id"], "linkedin", "linkedin+email", r.get("email_body") or "", 0, cfg.followup.cadence_days)
    console.print(f"[green]Approved {len(rows)}[/] — follow-up cadence armed")


@app.command()
def skip(job_id: str) -> None:
    db.init_db()
    job = db.get_job(job_id)
    db.set_status(job_id, JobStatus.SKIPPED)
    db.log_event(
        "skipped",
        job_id=job_id,
        payload={"company": (job or {}).get("company"), "title": (job or {}).get("title")},
    )
    console.print(f"Skipped {job_id}")


@legacy_app.command("followups")
def followups(
    mark: bool = typer.Option(False, "--mark", help="Mark drafts as sent after printing"),
    limit: int = typer.Option(50),
) -> None:
    """[legacy] Due follow-up drafts."""
    cfg = load_config()
    db.init_db()
    items = process_due_followups(cfg, auto_mark=mark, limit=limit)
    if not items:
        console.print("[green]No follow-ups due. Nice.[/]")
        raise typer.Exit()
    for item in items:
        d = item["followup_draft"]
        console.print(f"\n[bold]{item['company']}[/] — {item['title']} (#{d['followup_n']})")
        console.print(f"  LinkedIn: {d['linkedin_note']}")
        console.print(f"  Email: {d['email_subject']}")
        console.print(d["email_body"])
    if mark:
        console.print(f"\n[green]Marked {len(items)} follow-ups sent[/]")
    else:
        console.print("\nRe-run with [bold]--mark[/] after you send them.")


@app.command()
def inbox(
    days: int = typer.Option(180, help="How far back to scan"),
    limit: int = typer.Option(400, help="Max emails to fetch"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Parse only; don't write DB"),
    no_mark: bool = typer.Option(False, "--no-mark", help="Save hits but don't mark jobs applied"),
) -> None:
    """Scan Gmail for application receipts and mark already-applied companies."""
    from .inbox import scan_inbox

    db.init_db()
    try:
        result = scan_inbox(
            days=days,
            limit=limit,
            mark_applied=not no_mark,
            dry_run=dry_run,
        )
    except RuntimeError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1)

    if result.get("dry_run"):
        console.print(f"\nCompanies detected (dry-run): {', '.join(result.get('companies') or []) or '(none)'}")
        return

    companies = result.get("companies") or []
    console.print(
        f"[green]Saved {result.get('hits_saved', 0)} hits[/] · "
        f"applied [cyan]{result.get('jobs_marked', 0)}[/] · "
        f"rejected [yellow]{result.get('jobs_rejected', 0)}[/] · "
        f"parked {result.get('parked', 0)}"
    )
    if companies:
        console.print("Companies (applied or out of consideration):")
        for c in companies:
            console.print(f"  • {c}")
    rejected = result.get("rejected_companies") or []
    if rejected:
        console.print("Rejected / out of consideration:")
        for c in rejected:
            console.print(f"  • {c}")
    console.print("List later: [bold]hunt applied[/]")


@app.command()
def applied(limit: int = typer.Option(100)) -> None:
    """Show companies detected as already applied (inbox + manual)."""
    db.init_db()
    rows = db.list_applied_companies(limit=limit)
    if not rows:
        console.print("[yellow]None yet. Run: hunt inbox[/]")
        raise typer.Exit()
    table = Table(title=f"Already applied ({len(rows)})")
    table.add_column("Company")
    table.add_column("Role hint")
    table.add_column("Source")
    table.add_column("Last seen")
    for r in rows:
        table.add_row(
            r["company"],
            (r.get("title") or "")[:40],
            r.get("source") or "",
            (r.get("last_seen_at") or "")[:19],
        )
    console.print(table)


@app.command()
def board() -> None:
    """One-pager: applied / inbox cos / review / follow-ups (terminal)."""
    from .ui import print_board

    print_board()


@app.command()
def ui(
    port: int = typer.Option(8765, help="Local port"),
    no_browser: bool = typer.Option(False, help="Don't open browser"),
) -> None:
    """Local web dashboard — review, applied, suggestions, mark applied/skip."""
    from .ui import serve

    serve(port=port, open_browser=not no_browser)


@app.command()
def status() -> None:
    db.init_db()
    s = db.stats()
    table = Table(title="Pipeline status")
    table.add_column("Status")
    table.add_column("Count", justify="right")
    for k, v in sorted(s.items(), key=lambda x: -x[1]):
        table.add_row(k, str(v))
    if not s:
        table.add_row("(empty)", "0")
    console.print(table)
    due = db.due_followups(limit=1000)
    console.print(f"Follow-ups due now: [cyan]{len(due)}[/]")


@app.command("add-job")
def add_job(
    company: str = typer.Option(...),
    title: str = typer.Option(...),
    url: str = typer.Option(""),
    location: str = typer.Option(""),
    description: str = typer.Option(""),
) -> None:
    """Manually add a role (LinkedIn/Wellfound paste)."""
    from .models import Job, JobSource
    import hashlib

    jid = hashlib.sha1(f"manual|{company}|{title}|{url}".encode()).hexdigest()[:16]
    job = Job(
        id=jid,
        source=JobSource.MANUAL,
        company=company,
        title=title,
        url=url,
        location=location,
        description=description,
        remote="remote" in location.lower(),
    )
    db.init_db()
    db.upsert_job(job)
    cfg = load_config()
    from .match import score_job

    b = score_job(job, cfg)
    db.update_score(job.id, b.total, b.model_dump())
    console.print(f"Added {jid} score={b.total:.2f} — run [bold]hunt prep {jid}[/] for materials")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
