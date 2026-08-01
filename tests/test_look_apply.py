"""Parser + filter tests for look+apply flow."""
from __future__ import annotations

import pytest

from job_hunt.inbox import _detect_kind, _extract_from_subject
from job_hunt import db
from job_hunt.models import Job, JobSource, JobStatus


@pytest.mark.parametrize(
    "subject,expected_company",
    [
        ("Thank you for your application to Asana!", "Asana"),
        ("Garvit, Thank You for Applying to Brex!", "Brex"),
        ("Thanks for applying to Cohere!", "Cohere"),
        ("ElevenLabs | Application Received", "ElevenLabs"),
        ("Edra Update - AI Engineer (London)", "Edra"),
        ("Thank you for applying to Anthropic", "Anthropic"),
    ],
)
def test_extract_company_from_subject(subject, expected_company):
    company, _ = _extract_from_subject(subject)
    assert company == expected_company


def test_detect_kind_applied_vs_rejected():
    assert _detect_kind("Thank you for applying to Anthropic", "We received your application.") == "applied"
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
    assert figma["title"] == "PM Design"  # higher score wins
    assert figma["sibling_roles"] >= 1
