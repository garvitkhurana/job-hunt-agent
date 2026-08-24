"""Funnel metrics — measure look→apply→outcome so we can optimize against numbers."""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from . import db
from .config import ROOT
from .models import JobStatus

console = Console()


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", ""))
    except ValueError:
        return None


def compute_metrics(since_days: int = 30) -> dict[str, Any]:
    """Aggregate funnel KPIs from events + jobs tables."""
    db.init_db()
    since = (datetime.utcnow() - timedelta(days=since_days)).isoformat()
    half = (datetime.utcnow() - timedelta(days=max(1, since_days // 2))).isoformat()

    events = db.list_events(since=since, limit=50_000)
    by_type: dict[str, int] = {}
    for e in events:
        by_type[e["event_type"]] = by_type.get(e["event_type"], 0) + 1

    applies = by_type.get("applied", 0)
    skips = by_type.get("skipped", 0)
    denom = applies + skips
    precision = (applies / denom) if denom else None

    # Hours discover→applied from event payloads or job timestamps
    hours_list: list[float] = []
    for e in events:
        if e["event_type"] != "applied":
            continue
        payload = e.get("payload") or {}
        if "hours_to_applied" in payload:
            try:
                hours_list.append(float(payload["hours_to_applied"]))
                continue
            except (TypeError, ValueError):
                pass
        job = db.get_job(e["job_id"]) if e.get("job_id") else None
        if not job:
            continue
        created = _parse_ts(job.get("created_at"))
        applied_at = _parse_ts(e.get("created_at"))
        if created and applied_at and applied_at >= created:
            hours_list.append((applied_at - created).total_seconds() / 3600.0)

    median_hours = round(statistics.median(hours_list), 1) if hours_list else None

    # Outcomes on applied / outcome-tagged jobs
    applied_jobs = db.list_jobs(status=JobStatus.APPLIED, limit=2000, order="updated_at DESC")
    # Also pull jobs that have outcomes but may have left applied status
    extra = []
    with db.connect() as conn:
        extra = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM jobs WHERE outcome IN ('interview','rejected','ghost') LIMIT 2000"
            ).fetchall()
        ]
    seen_ids = {j["id"] for j in applied_jobs}
    for j in extra:
        if j["id"] not in seen_ids:
            applied_jobs.append(j)

    outcomes = {"interview": 0, "rejected": 0, "ghost": 0, "none": 0}
    interview_prepped = interview_unprepped = 0
    applied_prepped = applied_unprepped = 0
    for j in applied_jobs:
        oc = (j.get("outcome") or "").strip() or "none"
        prepped = int(j.get("prepped") or 0)
        if oc in outcomes:
            outcomes[oc] += 1
        else:
            outcomes["none"] += 1
        if prepped:
            applied_prepped += 1
            if oc == "interview":
                interview_prepped += 1
        else:
            applied_unprepped += 1
            if oc == "interview":
                interview_unprepped += 1

    # Prefer event counts for applies in window
    outcome_events = {
        "interview": by_type.get("outcome_interview", 0),
        "rejected": by_type.get("outcome_rejected", 0),
        "ghost": by_type.get("outcome_ghost", 0),
    }

    stats = db.stats()
    review_n = len(db.queue_for_review(limit=100, one_per_company=True))
    blocked = len(db.list_applied_companies(limit=5000))

    # Skip company frequency
    skip_cos: dict[str, int] = {}
    for e in events:
        if e["event_type"] != "skipped":
            continue
        co = (e.get("payload") or {}).get("company") or ""
        if not co and e.get("job_id"):
            job = db.get_job(e["job_id"])
            co = (job or {}).get("company") or ""
        if co:
            skip_cos[co] = skip_cos.get(co, 0) + 1
    top_skipped = sorted(skip_cos.items(), key=lambda x: -x[1])[:8]

    # WoW-ish: second half of window vs first half for applies
    applies_recent = sum(
        1 for e in events if e["event_type"] == "applied" and (e.get("created_at") or "") >= half
    )
    applies_older = applies - applies_recent

    interview_rate = None
    applied_with_outcome = (
        outcome_events["interview"] + outcome_events["rejected"] + outcome_events["ghost"]
    )
    if applied_with_outcome:
        interview_rate = round(outcome_events["interview"] / applied_with_outcome, 3)
    elif applies and (outcomes["interview"] + outcomes["rejected"] + outcomes["ghost"]):
        tot = outcomes["interview"] + outcomes["rejected"] + outcomes["ghost"]
        interview_rate = round(outcomes["interview"] / tot, 3)

    prep_lift = None
    if applied_prepped and applied_unprepped:
        r_p = interview_prepped / applied_prepped
        r_u = interview_unprepped / applied_unprepped
        prep_lift = round(r_p - r_u, 3)

    daily_runs = [e for e in events if e["event_type"] == "daily_run"]
    last_daily = daily_runs[-1]["payload"] if daily_runs else {}

    return {
        "since_days": since_days,
        "generated_at": datetime.utcnow().isoformat(),
        "pipeline": {
            "scored": stats.get("scored", 0),
            "applied": stats.get("applied", 0),
            "skipped": stats.get("skipped", 0),
            "rejected": stats.get("rejected", 0),
            "review_companies": review_n,
            "blocked_companies": blocked,
        },
        "funnel": {
            "daily_runs": by_type.get("daily_run", 0),
            "raw_roles_last_daily": last_daily.get("raw_roles"),
            "qualifying_last_daily": last_daily.get("qualifying"),
            "core_n_last_daily": last_daily.get("core_n"),
            "adj_n_last_daily": last_daily.get("adj_n"),
            "applies": applies,
            "skips": skips,
            "review_precision": round(precision, 3) if precision is not None else None,
            "applies_per_day": round(applies / max(since_days, 1), 2),
            "median_hours_discover_to_applied": median_hours,
            "applies_recent_half": applies_recent,
            "applies_older_half": applies_older,
        },
        "outcomes": {
            **outcome_events,
            "interview_rate": interview_rate,
            "applied_prepped": applied_prepped,
            "applied_unprepped": applied_unprepped,
            "interview_prepped": interview_prepped,
            "interview_unprepped": interview_unprepped,
            "prep_lift": prep_lift,
        },
        "top_skipped_companies": top_skipped,
        "event_counts": by_type,
    }


def metrics_dir() -> Path:
    d = ROOT / "output" / "metrics"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_baseline(metrics: dict[str, Any] | None = None) -> Path:
    m = metrics or compute_metrics(30)
    path = metrics_dir() / "baseline.json"
    path.write_text(json.dumps(m, indent=2))
    # also dated snapshot
    stamp = datetime.utcnow().strftime("%Y%m%d")
    (metrics_dir() / f"snapshot_{stamp}.json").write_text(json.dumps(m, indent=2))
    return path


def print_metrics(since_days: int = 30, save_baseline: bool = False) -> dict[str, Any]:
    m = compute_metrics(since_days)
    baseline_path = metrics_dir() / "baseline.json"
    if save_baseline or not baseline_path.exists():
        write_baseline(m)

    f = m["funnel"]
    p = m["pipeline"]
    o = m["outcomes"]

    console.print(
        f"[bold]Hunt metrics[/] — last {since_days}d · generated {m['generated_at'][:19]}"
    )
    table = Table(title="Funnel")
    table.add_column("KPI")
    table.add_column("Value", justify="right")
    rows = [
        ("Review companies (now)", str(p["review_companies"])),
        ("Blocked companies", str(p["blocked_companies"])),
        ("Applies (events)", str(f["applies"])),
        ("Skips (events)", str(f["skips"])),
        ("Review precision", "—" if f["review_precision"] is None else f"{f['review_precision']:.0%}"),
        ("Applies / day", str(f["applies_per_day"])),
        ("Median hrs discover→applied", "—" if f["median_hours_discover_to_applied"] is None else str(f["median_hours_discover_to_applied"])),
        ("Interview rate (logged)", "—" if o["interview_rate"] is None else f"{o['interview_rate']:.0%}"),
        ("Outcomes interview/reject/ghost", f"{o['interview']}/{o['rejected']}/{o['ghost']}"),
        ("Prep lift (pp)", "—" if o["prep_lift"] is None else str(o["prep_lift"])),
        ("Last daily raw→qualifying", f"{f.get('raw_roles_last_daily') or '—'} → {f.get('qualifying_last_daily') or '—'}"),
    ]
    for k, v in rows:
        table.add_row(k, v)
    console.print(table)

    if m["top_skipped_companies"]:
        sk = Table(title="Top skipped companies")
        sk.add_column("Company")
        sk.add_column("Skips", justify="right")
        for co, n in m["top_skipped_companies"]:
            sk.add_row(co, str(n))
        console.print(sk)

    base = metrics_dir() / "baseline.json"
    if base.exists() and not save_baseline:
        try:
            old = json.loads(base.read_text())
            old_p = (old.get("funnel") or {}).get("review_precision")
            new_p = f["review_precision"]
            if old_p is not None and new_p is not None:
                delta = new_p - old_p
                console.print(
                    f"vs baseline precision: {old_p:.0%} → {new_p:.0%} "
                    f"([{'green' if delta >= 0 else 'red'}]{delta:+.1%}[/])"
                )
        except (json.JSONDecodeError, TypeError):
            pass

    console.print(f"[dim]Snapshots: {metrics_dir()}[/]")
    return m
