from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Iterator

from .config import db_path
from .models import Job, JobStatus, JobSource


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    location TEXT DEFAULT '',
    url TEXT DEFAULT '',
    description TEXT DEFAULT '',
    department TEXT DEFAULT '',
    remote INTEGER DEFAULT 0,
    posted_at TEXT,
    raw_json TEXT DEFAULT '{}',
    score REAL DEFAULT 0,
    score_json TEXT DEFAULT '{}',
    status TEXT DEFAULT 'discovered',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_touch_at TEXT,
    followup_count INTEGER DEFAULT 0,
    next_followup_at TEXT,
    notes TEXT DEFAULT '',
    track TEXT DEFAULT 'core',
    role_family TEXT DEFAULT '',
    company_tier TEXT DEFAULT 'unknown',
    company_score REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS packets (
    job_id TEXT PRIMARY KEY,
    tailored_resume_md TEXT,
    cover_letter TEXT,
    linkedin_note TEXT,
    email_subject TEXT,
    email_body TEXT,
    founder_pitch TEXT,
    apply_checklist_json TEXT DEFAULT '[]',
    created_at TEXT NOT NULL,
    FOREIGN KEY(job_id) REFERENCES jobs(id)
);

CREATE TABLE IF NOT EXISTS touches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    touch_type TEXT NOT NULL,
    channel TEXT NOT NULL,
    content TEXT,
    sent_at TEXT,
    followup_n INTEGER DEFAULT 0,
    FOREIGN KEY(job_id) REFERENCES jobs(id)
);

CREATE TABLE IF NOT EXISTS inbox_hits (
    message_id TEXT PRIMARY KEY,
    subject TEXT,
    from_addr TEXT,
    date TEXT,
    company TEXT,
    title TEXT,
    confidence REAL DEFAULT 0,
    snippet TEXT,
    matched_job_ids_json TEXT DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applied_companies (
    company_norm TEXT PRIMARY KEY,
    company TEXT NOT NULL,
    title TEXT DEFAULT '',
    source TEXT DEFAULT 'inbox',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(score DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_next_followup ON jobs(next_followup_at);
CREATE INDEX IF NOT EXISTS idx_applied_companies_name ON applied_companies(company);
"""


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


MIGRATIONS = [
    ("track", "TEXT DEFAULT 'core'"),
    ("role_family", "TEXT DEFAULT ''"),
    ("company_tier", "TEXT DEFAULT 'unknown'"),
    ("company_score", "REAL DEFAULT 0"),
]


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        existing = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        for column, ddl in MIGRATIONS:
            if column not in existing:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} {ddl}")


def upsert_job(job: Job, score: float = 0.0, score_json: dict | None = None, status: JobStatus = JobStatus.DISCOVERED) -> None:
    now = datetime.utcnow().isoformat()
    with connect() as conn:
        existing = conn.execute("SELECT id FROM jobs WHERE id = ?", (job.id,)).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE jobs SET company=?, title=?, location=?, url=?, description=?,
                department=?, remote=?, posted_at=?, raw_json=?, updated_at=?
                WHERE id=?
                """,
                (
                    job.company,
                    job.title,
                    job.location,
                    job.url,
                    job.description,
                    job.department,
                    int(job.remote),
                    job.posted_at,
                    json.dumps(job.raw),
                    now,
                    job.id,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO jobs (
                    id, source, company, title, location, url, description, department,
                    remote, posted_at, raw_json, score, score_json, status, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    job.id,
                    job.source.value,
                    job.company,
                    job.title,
                    job.location,
                    job.url,
                    job.description,
                    job.department,
                    int(job.remote),
                    job.posted_at,
                    json.dumps(job.raw),
                    score,
                    json.dumps(score_json or {}),
                    status.value,
                    now,
                    now,
                ),
            )


def update_score(job_id: str, score: float, breakdown: dict, status: JobStatus = JobStatus.SCORED) -> None:
    now = datetime.utcnow().isoformat()
    with connect() as conn:
        conn.execute(
            """
            UPDATE jobs SET score=?, score_json=?, status=?, updated_at=?,
                track=?, role_family=?, company_tier=?, company_score=?
            WHERE id=?
            """,
            (
                score,
                json.dumps(breakdown),
                status.value,
                now,
                breakdown.get("track", "core"),
                breakdown.get("role_family", ""),
                breakdown.get("company_tier", "unknown"),
                breakdown.get("company_score", 0.0),
                job_id,
            ),
        )


def set_status(job_id: str, status: JobStatus, **extra: Any) -> None:
    now = datetime.utcnow().isoformat()
    fields = ["status=?", "updated_at=?"]
    values: list[Any] = [status.value, now]
    for k, v in extra.items():
        fields.append(f"{k}=?")
        values.append(v)
    values.append(job_id)
    with connect() as conn:
        conn.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id=?", values)


def set_status_note(job_id: str, note: str) -> None:
    now = datetime.utcnow().isoformat()
    with connect() as conn:
        conn.execute("UPDATE jobs SET notes=?, updated_at=? WHERE id=?", (note, now, job_id))


def save_packet(job_id: str, packet: dict[str, Any]) -> None:
    now = datetime.utcnow().isoformat()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO packets (
                job_id, tailored_resume_md, cover_letter, linkedin_note,
                email_subject, email_body, founder_pitch, apply_checklist_json, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(job_id) DO UPDATE SET
                tailored_resume_md=excluded.tailored_resume_md,
                cover_letter=excluded.cover_letter,
                linkedin_note=excluded.linkedin_note,
                email_subject=excluded.email_subject,
                email_body=excluded.email_body,
                founder_pitch=excluded.founder_pitch,
                apply_checklist_json=excluded.apply_checklist_json,
                created_at=excluded.created_at
            """,
            (
                job_id,
                packet.get("tailored_resume_md", ""),
                packet.get("cover_letter", ""),
                packet.get("linkedin_note", ""),
                packet.get("email_subject", ""),
                packet.get("email_body", ""),
                packet.get("founder_pitch", ""),
                json.dumps(packet.get("apply_checklist", [])),
                now,
            ),
        )
    set_status(job_id, JobStatus.PACKET_READY)


def get_job(job_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None


def get_packet(job_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM packets WHERE id=?" if False else "SELECT * FROM packets WHERE job_id=?", (job_id,)).fetchone()
        return dict(row) if row else None


def list_jobs(
    status: JobStatus | None = None,
    min_score: float | None = None,
    limit: int = 50,
    order: str = "score DESC",
    track: str | None = None,
) -> list[dict]:
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        clauses.append("status=?")
        params.append(status.value)
    if min_score is not None:
        clauses.append("score>=?")
        params.append(min_score)
    if track:
        clauses.append("track=?")
        params.append(track)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT * FROM jobs {where} ORDER BY {order} LIMIT ?"
    params.append(limit)
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def queue_for_review(
    limit: int = 50,
    track: str | None = None,
    one_per_company: bool = True,
) -> list[dict]:
    """Jobs with packets ready. By default one best role per company (avoid Stripe×21 spam)."""
    clause = "AND j.track = ?" if track else ""
    params: list[Any] = [track] if track else []
    # Over-fetch so we can still fill `limit` after company dedupe + applied filter
    params.append(max(limit * 20, 200))
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT j.*, p.linkedin_note, p.email_subject, p.email_body, p.cover_letter
            FROM jobs j
            JOIN packets p ON p.job_id = j.id
            WHERE j.status IN ('packet_ready', 'queued') {clause}
            ORDER BY j.score DESC, j.company_score DESC, j.updated_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()

        # Sibling counts among packet_ready/queued (same company)
        sibling_counts: dict[str, int] = {}
        for r in rows:
            key = _norm_company_key(r["company"])
            sibling_counts[key] = sibling_counts.get(key, 0) + 1

        out: list[dict] = []
        seen_companies: set[str] = set()
        for r in rows:
            if is_company_already_applied(r["company"]):
                continue
            key = _norm_company_key(r["company"])
            if one_per_company and key in seen_companies:
                continue
            if one_per_company:
                seen_companies.add(key)
            row = dict(r)
            row["sibling_roles"] = max(0, sibling_counts.get(key, 1) - 1)
            out.append(row)
            if len(out) >= limit:
                break
        return out


def company_sibling_roles(company: str, exclude_job_id: str | None = None, limit: int = 20) -> list[dict]:
    """Other open roles at the same company (for review context)."""
    key = _norm_company_key(company)
    if not key:
        return []
    out: list[dict] = []
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, company, title, location, score, track, status, url
            FROM jobs
            WHERE status IN ('packet_ready', 'queued', 'scored')
            ORDER BY score DESC
            LIMIT 800
            """,
        ).fetchall()
    for r in rows:
        if _norm_company_key(r["company"]) != key:
            continue
        if exclude_job_id and r["id"] == exclude_job_id:
            continue
        out.append(dict(r))
        if len(out) >= limit:
            break
    return out


def emailable(limit: int = 60, track: str | None = None) -> list[dict]:
    """Packets that have a recorded recipient email in notes and aren't sent yet."""
    clause = "AND j.track = ?" if track else ""
    params: list[Any] = [track] if track else []
    params.append(limit)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT j.*, p.email_subject, p.email_body
            FROM jobs j JOIN packets p ON p.job_id = j.id
            WHERE j.notes LIKE 'email:%'
              AND j.status IN ('packet_ready', 'queued') {clause}
            ORDER BY j.score DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [dict(r) for r in rows]


def suggestions(limit: int = 25, min_score: float = 0.0) -> list[dict]:
    """Adjacent-track roles worth a look, best companies first."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM jobs
            WHERE track = 'adjacent'
              AND score >= ?
              AND status NOT IN ('skipped', 'rejected', 'closed')
            ORDER BY company_score DESC, score DESC
            LIMIT ?
            """,
            (min_score, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def mark_sent(job_id: str, touch_type: str, channel: str, content: str, followup_n: int = 0, cadence_days: list[int] | None = None) -> None:
    now = datetime.utcnow()
    cadence = cadence_days or [3, 7, 14]
    next_days = cadence[min(followup_n, len(cadence) - 1)]
    next_at = (now + timedelta(days=next_days)).isoformat()
    status = JobStatus.FOLLOWUP_SENT if followup_n > 0 else (
        JobStatus.APPLIED if touch_type == "application" else JobStatus.OUTREACH_SENT
    )
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO touches (job_id, touch_type, channel, content, sent_at, followup_n)
            VALUES (?,?,?,?,?,?)
            """,
            (job_id, touch_type, channel, content, now.isoformat(), followup_n),
        )
        conn.execute(
            """
            UPDATE jobs SET status=?, last_touch_at=?, followup_count=?, next_followup_at=?, updated_at=?
            WHERE id=?
            """,
            (status.value, now.isoformat(), followup_n, next_at, now.isoformat(), job_id),
        )


def due_followups(limit: int = 100) -> list[dict]:
    now = datetime.utcnow().isoformat()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT j.*, p.email_subject, p.email_body, p.linkedin_note, p.founder_pitch
            FROM jobs j
            LEFT JOIN packets p ON p.job_id = j.id
            WHERE j.next_followup_at IS NOT NULL
              AND j.next_followup_at <= ?
              AND j.status IN ('applied', 'outreach_sent', 'followup_sent', 'followup_due')
              AND j.followup_count < 3
            ORDER BY j.next_followup_at ASC
            LIMIT ?
            """,
            (now, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def stats() -> dict[str, int]:
    with connect() as conn:
        rows = conn.execute("SELECT status, COUNT(*) as c FROM jobs GROUP BY status").fetchall()
        return {r["status"]: r["c"] for r in rows}


def _norm_company_key(company: str) -> str:
    s = (company or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\b(inc|llc|ltd|corp|co|ai|labs?|technologies|technology|software)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def remember_applied_company(company: str, source: str = "inbox", title: str = "") -> None:
    if not company:
        return
    now = datetime.utcnow().isoformat()
    key = _norm_company_key(company)
    if not key:
        return
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO applied_companies (company_norm, company, title, source, first_seen_at, last_seen_at)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(company_norm) DO UPDATE SET
                last_seen_at=excluded.last_seen_at,
                title=CASE WHEN excluded.title != '' THEN excluded.title ELSE applied_companies.title END,
                company=excluded.company
            """,
            (key, company.strip(), title or "", source, now, now),
        )


def list_applied_companies(limit: int = 500) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM applied_companies ORDER BY last_seen_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def is_company_already_applied(company: str) -> bool:
    key = _norm_company_key(company)
    if not key:
        return False
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM applied_companies WHERE company_norm=?",
            (key,),
        ).fetchone()
        return bool(row)


def upsert_inbox_hit(hit: dict[str, Any]) -> None:
    now = datetime.utcnow().isoformat()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO inbox_hits (
                message_id, subject, from_addr, date, company, title,
                confidence, snippet, matched_job_ids_json, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(message_id) DO UPDATE SET
                company=excluded.company,
                title=excluded.title,
                confidence=excluded.confidence,
                snippet=excluded.snippet,
                matched_job_ids_json=excluded.matched_job_ids_json
            """,
            (
                hit["message_id"],
                hit.get("subject", ""),
                hit.get("from_addr", ""),
                hit.get("date", ""),
                hit.get("company", ""),
                hit.get("title", ""),
                hit.get("confidence", 0),
                hit.get("snippet", ""),
                json.dumps(hit.get("matched_job_ids") or []),
                now,
            ),
        )


def list_inbox_hits(limit: int = 100) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM inbox_hits ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def job_from_row(row: dict) -> Job:
    return Job(
        id=row["id"],
        source=JobSource(row["source"]),
        company=row["company"],
        title=row["title"],
        location=row.get("location") or "",
        url=row.get("url") or "",
        description=row.get("description") or "",
        department=row.get("department") or "",
        remote=bool(row.get("remote")),
        posted_at=row.get("posted_at"),
        raw=json.loads(row.get("raw_json") or "{}"),
    )
