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
    add("a2", "Figma", "Forward Deployed Engineer", 0.95, "adjacent")
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
        jobs = fetch_ashby("linear")
    assert len(jobs) == 1
    assert jobs[0].source == JobSource.ASHBY
    assert "agent" in jobs[0].description
