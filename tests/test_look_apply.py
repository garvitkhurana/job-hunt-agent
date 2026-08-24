"""Parser + filter + metrics tests for look→apply→measure loop."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from job_hunt.inbox import _detect_kind, _extract_from_subject
from job_hunt import db
from job_hunt.metrics import compute_metrics
from job_hunt.models import Job, JobSource, JobStatus
from job_hunt.discover import fetch_greenhouse, fetch_ashby


@pytest.mark.parametrize(
    "subject,expected_company",
    [
        ("Thank you for your application to Asana!", "Asana"),
        ("Garvit, Thank You for Applying to Brex!", "Brex"),
        ("Thanks for applying to Cohere!", "Cohere"),
        ("ElevenLabs | Application Received", "ElevenLabs"),
        ("Edra Update - AI Engineer (London)", "Edra"),
        ("Thank you for applying to Anthropic", "Anthropic"),
        ("Your application to LlamaIndex was received", "LlamaIndex"),
        ("Application to Modal was received", "Modal"),
    ],
)
def test_extract_company_from_subject(subject, expected_company):
    company, _ = _extract_from_subject(subject)
    assert company == expected_company


def test_detect_kind_applied_vs_rejected():
    assert _detect_kind("Thank you for applying to Anthropic", "We received your application.") == "applied"
    # Pure thank-you is applied, not reject
    assert _detect_kind("Thanks for applying to Stripe!", "We got it.") == "applied"
    assert (
        _detect_kind(
            "Edra Update - AI Engineer (London)",
            "After reviewing your application we've determined that we will not be moving forward.",
        )
        == "rejected"
    )
    assert (
        _detect_kind(
            "Update on your application to Figma",
            "Unfortunately we are not moving forward with your candidacy.",
        )
        == "rejected"
    )


def test_norm_aliases():
    assert db._norm_company_key("Scaleai") == db._norm_company_key("Scale AI")
    assert db._norm_company_key("Together AI") == "together"


def test_queue_one_per_company_and_applied_filter(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(db, "db_path", lambda: db_path)
    db.init_db()

    def add(jid, company, title, score, track="core"):
        job = Job(id=jid, source=JobSource.MANUAL, company=company, title=title, url="https://x")
        db.upsert_job(job, score=score, status=JobStatus.SCORED)
        db.update_score(
            jid,
            score,
            {"total": score, "track": track, "company_score": 1.0, "company_tier": "tier1"},
            status=JobStatus.SCORED,
        )
        with db.connect() as conn:
            conn.execute("UPDATE jobs SET track=? WHERE id=?", (track, jid))

    add("a1", "Figma", "PM Design", 1.0, "core")
    add("a2", "Figma", "Senior Product Manager, AI", 0.95, "core")
    add("b1", "Brex", "Senior PM AI", 0.99, "core")
    add("c1", "Anthropic", "PM", 1.0, "core")
    db.remember_applied_company("Anthropic", source="inbox")

    rows = db.queue_for_review(limit=20, one_per_company=True)
    companies = [r["company"] for r in rows]
    assert companies.count("Figma") == 1
    assert "Anthropic" not in companies
    assert "Brex" in companies
    figma = next(r for r in rows if r["company"] == "Figma")
    assert figma["title"] == "PM Design"
    assert figma["sibling_roles"] >= 1


def test_events_and_metrics(tmp_path, monkeypatch):
    db_path = tmp_path / "metrics.db"
    monkeypatch.setattr(db, "db_path", lambda: db_path)
    db.init_db()

    job = Job(id="m1", source=JobSource.MANUAL, company="Glean", title="Senior PM", url="https://x")
    db.upsert_job(job, score=0.9, status=JobStatus.SCORED)
    db.update_score("m1", 0.9, {"total": 0.9, "track": "core"}, status=JobStatus.SCORED)

    db.log_event("skipped", job_id="m1", payload={"company": "Glean"})
    db.log_event("applied", job_id="m1", payload={"company": "Glean", "hours_to_applied": 2.5})
    db.log_event("daily_run", payload={"raw_roles": 10, "qualifying": 4, "core_n": 3, "adj_n": 1})
    db.set_outcome("m1", "interview")

    events = db.list_events(limit=100)
    types = [e["event_type"] for e in events]
    assert "skipped" in types
    assert "applied" in types
    assert "outcome_interview" in types

    m = compute_metrics(since_days=30)
    assert m["funnel"]["applies"] >= 1
    assert m["funnel"]["skips"] >= 1
    assert m["funnel"]["review_precision"] is not None
    assert m["outcomes"]["interview"] >= 1


def test_fetch_greenhouse_mocked():
    payload = {
        "jobs": [
            {
                "id": 1,
                "title": "Senior Product Manager",
                "absolute_url": "https://boards.greenhouse.io/x/jobs/1",
                "content": "<p>AI platform PM</p>",
                "location": {"name": "New York, NY"},
                "offices": [{"name": "New York"}],
                "departments": [{"name": "Product"}],
                "updated_at": "2026-01-01",
            }
        ]
    }
    with patch("job_hunt.discover._get_json", return_value=payload):
        jobs = fetch_greenhouse("glean")
    assert len(jobs) == 1
    assert jobs[0].title == "Senior Product Manager"
    assert jobs[0].source == JobSource.GREENHOUSE
    assert "AI platform" in jobs[0].description


def test_fetch_ashby_mocked():
    payload = {
        "jobs": [
            {
                "title": "Product Manager, AI",
                "jobUrl": "https://jobs.ashbyhq.com/linear/abc",
                "descriptionPlain": "Build agent tooling",
                "location": "San Francisco",
                "isRemote": False,
                "publishedAt": "2026-01-02",
            }
        ]
    }
    with patch("job_hunt.discover._get_json", return_value=payload):
        from job_hunt.discover import fetch_ashby

        jobs = fetch_ashby("linear")
    assert len(jobs) == 1
    assert jobs[0].source == JobSource.ASHBY
    assert "agent" in jobs[0].description


def test_discover_skips_applied_and_round_robins():
    from job_hunt.discover import discover_all
    from job_hunt.models import Job, JobSource

    def fake_gh(board: str):
        # Each board returns more than max_total if taken greedily
        return [
            Job(
                id=f"gh-{board}-{i}",
                source=JobSource.GREENHOUSE,
                company=board.title(),
                title=f"Senior Product Manager {i}",
                url=f"https://x/{board}/{i}",
                location="New York",
            )
            for i in range(30)
        ]

    with patch("job_hunt.discover.fetch_greenhouse", side_effect=fake_gh):
        with patch("job_hunt.discover.fetch_ashby", return_value=[]):
            with patch("job_hunt.discover.fetch_yc_jobs", return_value=[]):
                jobs = discover_all(
                    greenhouse_boards=["stripe", "notion", "ramp"],
                    ashby_boards=[],
                    yc_enabled=False,
                    max_total=9,
                    per_board=30,
                    skip_company_keys={"stripe"},
                )
    cos = {j.company.lower() for j in jobs}
    assert "stripe" not in cos
    assert "notion" in cos and "ramp" in cos
    # Round-robin should include both unapplied boards, not 9 from Notion only
    assert sum(1 for j in jobs if j.company.lower() == "notion") <= 5
    assert sum(1 for j in jobs if j.company.lower() == "ramp") <= 5


def test_purge_junk_applied(tmp_path, monkeypatch):
    db_path = tmp_path / "junk.db"
    monkeypatch.setattr(db, "db_path", lambda: db_path)
    db.init_db()
    db.remember_applied_company("Stripe", source="inbox")
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO applied_companies (company_norm, company, title, source, first_seen_at, last_seen_at) "
            "VALUES ('career','career','','inbox','t','t')"
        )
        conn.execute(
            "INSERT INTO applied_companies (company_norm, company, title, source, first_seen_at, last_seen_at) "
            "VALUES ('llamaindex was received','LlamaIndex was received','','inbox','t','t')"
        )
    n = db.purge_junk_applied_companies()
    assert n >= 2
    assert db.is_company_already_applied("Stripe")
    assert not db.is_company_already_applied("career")


def test_japan_remote_and_sales_ops_excluded():
    from job_hunt.config import load_config
    from job_hunt.match import score_job
    from job_hunt.match.roles import is_hard_excluded

    cfg = load_config()
    assert is_hard_excluded("Senior Sales Strategy & Operations Manager - APJ")
    assert is_hard_excluded("Head of Global Sales Enablement and AI Adoption")
    assert is_hard_excluded("Sales Engineer, Enterprise")
    assert is_hard_excluded("Customer Success Manager")
    assert is_hard_excluded("Founding / Senior Product Manager (explore)")

    japan = Job(
        id="jp1",
        source=JobSource.MANUAL,
        company="Dropbox",
        title="Senior Product Manager",
        location="Remote - Japan",
        url="https://x",
        description="Product role",
        remote=True,
    )
    b = score_job(japan, cfg)
    assert b.total == 0.0
    assert any("excluded_geo" in r for r in b.reasons)

    apj = Job(
        id="apj1",
        source=JobSource.MANUAL,
        company="Dropbox",
        title="Senior Product Manager APJ",
        location="Remote",
        url="https://x",
        remote=True,
    )
    assert score_job(apj, cfg).total == 0.0

    anz = Job(
        id="anz1",
        source=JobSource.MANUAL,
        company="Stripe",
        title="Senior Product Manager - ANZ",
        location="Remote",
        url="https://x",
        remote=True,
    )
    assert score_job(anz, cfg).total == 0.0


def test_rescore_zeros_stale_hard_excluded(tmp_path, monkeypatch):
    from job_hunt.config import load_config
    from job_hunt.pipeline import run_rescore

    db_path = tmp_path / "rescore.db"
    monkeypatch.setattr(db, "db_path", lambda: db_path)
    db.init_db()

    junk = Job(
        id="junk1",
        source=JobSource.MANUAL,
        company="Dropbox",
        title="Senior Sales Strategy & Operations Manager - APJ (Remote)",
        location="Japan",
        url="https://x",
        remote=True,
    )
    good = Job(
        id="good1",
        source=JobSource.MANUAL,
        company="Notion",
        title="Senior Product Manager, AI",
        location="New York, NY",
        url="https://x",
        description="AI LLM platform product",
    )
    db.upsert_job(junk, score=0.789, status=JobStatus.SCORED)
    db.update_score(
        "junk1",
        0.789,
        {"total": 0.789, "track": "adjacent", "company_score": 1.0},
        status=JobStatus.SCORED,
    )
    # Force status back to scored in case update_score would skip (fresh insert path)
    with db.connect() as conn:
        conn.execute(
            "UPDATE jobs SET status='scored', score=0.789, track='adjacent' WHERE id='junk1'"
        )

    db.upsert_job(good, score=0.9, status=JobStatus.SCORED)
    with db.connect() as conn:
        conn.execute(
            "UPDATE jobs SET status='scored', score=0.9, track='core' WHERE id='good1'"
        )

    cfg = load_config()
    run_rescore(cfg)

    junk_row = db.get_job("junk1")
    assert junk_row is not None
    assert junk_row["score"] == 0.0
    assert junk_row["status"] == "skipped"

    review = db.queue_for_review(limit=20, one_per_company=True)
    assert not any(r["id"] == "junk1" for r in review)
    assert not any("Sales Strategy" in (r.get("title") or "") for r in review)


def test_update_score_does_not_revive_skipped(tmp_path, monkeypatch):
    db_path = tmp_path / "revive.db"
    monkeypatch.setattr(db, "db_path", lambda: db_path)
    db.init_db()
    job = Job(id="s1", source=JobSource.MANUAL, company="X", title="Senior PM", url="https://x")
    db.upsert_job(job, status=JobStatus.SKIPPED)
    db.set_status("s1", JobStatus.SKIPPED)
    db.update_score("s1", 0.95, {"total": 0.95, "track": "core"}, status=JobStatus.SCORED)
    row = db.get_job("s1")
    assert row["status"] == "skipped"


def test_core_preferred_over_higher_adjacent(tmp_path, monkeypatch):
    db_path = tmp_path / "corepref.db"
    monkeypatch.setattr(db, "db_path", lambda: db_path)
    db.init_db()

    def add(jid, company, title, score, track):
        job = Job(id=jid, source=JobSource.MANUAL, company=company, title=title, url="https://x")
        db.upsert_job(job, score=score, status=JobStatus.SCORED)
        with db.connect() as conn:
            conn.execute(
                "UPDATE jobs SET score=?, status='scored', track=? WHERE id=?",
                (score, track, jid),
            )

    add("c1", "Linear", "Senior Product Manager", 0.70, "core")
    add("a1", "Linear", "Forward Deployed Engineer", 0.95, "adjacent")
    # Explicit track=None: prefer core when both tracks present
    rows = db.queue_for_review(limit=10, track=None, one_per_company=True, prefer_core=True)
    linear = next(r for r in rows if r["company"] == "Linear")
    assert linear["title"] == "Senior Product Manager"
    assert linear["track"] == "core"
