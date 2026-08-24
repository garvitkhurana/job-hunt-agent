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

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    job_id TEXT,
    payload_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(score DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_next_followup ON jobs(next_followup_at);
CREATE INDEX IF NOT EXISTS idx_applied_companies_name ON applied_companies(company);
CREATE INDEX IF NOT EXISTS idx_events_type_created ON events(event_type, created_at);
CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id);
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
    ("prepped", "INTEGER DEFAULT 0"),
    ("outcome", "TEXT DEFAULT ''"),
    ("outcome_at", "TEXT"),
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


# Statuses that scoring must never overwrite back to scored/packet_ready
_SCORE_PROTECTED = frozenset(
    {
        JobStatus.APPLIED.value,
        JobStatus.OUTREACH_SENT.value,
        JobStatus.FOLLOWUP_DUE.value,
        JobStatus.FOLLOWUP_SENT.value,
        JobStatus.REPLIED.value,
        JobStatus.INTERVIEW.value,
        JobStatus.REJECTED.value,
        JobStatus.SKIPPED.value,
        JobStatus.CLOSED.value,
        JobStatus.APPROVED.value,
    }
)


def update_score(job_id: str, score: float, breakdown: dict, status: JobStatus = JobStatus.SCORED) -> None:
    """Persist score. Zero → skipped. Never revive applied/skipped/rejected to scored."""
    now = datetime.utcnow().isoformat()
    if score <= 0:
        score = 0.0
        status = JobStatus.SKIPPED
    with connect() as conn:
        row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
        current = row["status"] if row else None
        if current in _SCORE_PROTECTED:
            # Keep terminal / skipped status; still refresh score metadata
            status_value = current
        else:
            status_value = status.value
        conn.execute(
            """
            UPDATE jobs SET score=?, score_json=?, status=?, updated_at=?,
                track=?, role_family=?, company_tier=?, company_score=?
            WHERE id=?
            """,
            (
                score,
                json.dumps(breakdown),
                status_value,
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
    track: str | None = "core",
    one_per_company: bool = True,
    prefer_core: bool = True,
) -> list[dict]:
    """Scored / ready roles for look+apply. Default: core PM, one best role per company."""
    from .config import load_config
    from .match.roles import is_hard_excluded

    cfg = load_config()
    min_core = cfg.filters.min_score
    min_adj = cfg.filters.min_adjacent_score
    floor = min(min_core, min_adj) if track != "core" else min_core

    clause = "AND track = ?" if track else ""
    params: list[Any] = [floor]
    if track:
        params.append(track)
    params.append(max(limit * 40, 500))
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM jobs
            WHERE status IN ('scored', 'packet_ready', 'queued')
              AND score >= ?
              {clause}
            ORDER BY score DESC, company_score DESC, updated_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()

    eligible: list[dict] = []
    for r in rows:
        row = dict(r)
        if is_company_already_applied(row["company"]):
            continue
        if is_hard_excluded(row.get("title") or ""):
            continue
        if (row.get("source") or "") == "yc":
            continue
        t = row.get("track") or "core"
        need = min_adj if t == "adjacent" else min_core
        if (row.get("score") or 0) < need:
            continue
        if track and t != track:
            continue
        eligible.append(row)

    sibling_counts: dict[str, int] = {}
    for r in eligible:
        key = _norm_company_key(r["company"])
        sibling_counts[key] = sibling_counts.get(key, 0) + 1

    if not one_per_company:
        out = []
        for r in eligible:
            r = dict(r)
            r["sibling_roles"] = max(0, sibling_counts.get(_norm_company_key(r["company"]), 1) - 1)
            out.append(r)
            if len(out) >= limit:
                break
        return out

    # Prefer core over adjacent when both exist at a company
    by_co: dict[str, list[dict]] = {}
    for r in eligible:
        key = _norm_company_key(r["company"])
        by_co.setdefault(key, []).append(r)

    picks: list[dict] = []
    for key, roles in by_co.items():
        if prefer_core and not track:
            cores = [x for x in roles if (x.get("track") or "core") == "core"]
            pool = cores or roles
        else:
            pool = roles
        best = max(pool, key=lambda x: (x.get("score") or 0, x.get("company_score") or 0))
        best = dict(best)
        best["sibling_roles"] = max(0, sibling_counts.get(key, 1) - 1)
        picks.append(best)

    picks.sort(key=lambda x: (-(x.get("score") or 0), -(x.get("company_score") or 0)))
    return picks[:limit]


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
    """Adjacent-track roles worth a look, best companies first (skip already-applied cos)."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM jobs
            WHERE track = 'adjacent'
              AND score >= ?
              AND status NOT IN ('skipped', 'rejected', 'closed', 'applied')
            ORDER BY company_score DESC, score DESC
            LIMIT ?
            """,
            (min_score, limit * 5),
        ).fetchall()
    out: list[dict] = []
    seen: set[str] = set()
    for r in rows:
        if is_company_already_applied(r["company"]):
            continue
        key = _norm_company_key(r["company"])
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(r))
        if len(out) >= limit:
            break
    return out


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


# Common board slug / ATS name mismatches
_COMPANY_ALIASES = {
    "scaleai": "scale",
    "scale ai": "scale",
    "cohere talent": "cohere",
    "cohere": "cohere",
    "thinking machines lab": "thinking machines",
    "together ai": "together",
    "writer": "writer",
    "character ai": "character",
    "mistral ai": "mistral",
    "hugging face": "huggingface",
}


def _norm_company_key(company: str) -> str:
    s = (company or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\b(inc|llc|ltd|corp|co|technologies|technology|software|talent|hiring)\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Drop trailing generic tokens but keep meaningful short names
    s = re.sub(r"\b(labs?|ai)\b$", "", s).strip()
    s = re.sub(r"\s+", " ", s).strip()
    # Glue form for Scaleai-style single tokens
    glued = s.replace(" ", "")
    if s in _COMPANY_ALIASES:
        return _COMPANY_ALIASES[s]
    if glued in _COMPANY_ALIASES:
        return _COMPANY_ALIASES[glued]
    return s


def remember_applied_company(company: str, source: str = "inbox", title: str = "") -> None:
    if not company:
        return
    # Reject junk sentence-companies / visa spam from bad email parses
    if len(company) > 40 or re.search(
        r"\b(after reviewing|we'?ve determined|application to|green card|jinee|was received)\b",
        company,
        re.I,
    ):
        m = re.match(r"^([A-Z][\w.&'-]{1,30})", company.strip())
        company = m.group(1) if m and not re.search(r"green|jinee|was", m.group(1), re.I) else ""
    # Single junk tokens that are not employers
    if company and re.fullmatch(
        r"(?i)career|careers|wes|noreply|notifications?|team|hiring|jobs?",
        company.strip(),
    ):
        return
    if not company:
        return
    now = datetime.utcnow().isoformat()
    key = _norm_company_key(company)
    if not key or len(key) < 2:
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


_JUNK_APPLIED_NORMS = frozenset(
    {
        "career",
        "careers",
        "wes",
        "artificial",  # incomplete parse
        "angelo salerno",  # person, not employer
        "llamaindex was received",
        "physical intelligence we",
    }
)


def purge_junk_applied_companies() -> int:
    """Remove bad inbox parses that block discovery without being real employers."""
    removed = 0
    with connect() as conn:
        rows = conn.execute("SELECT company_norm, company FROM applied_companies").fetchall()
        for r in rows:
            norm = (r["company_norm"] or "").strip()
            name = (r["company"] or "").strip()
            junk = (
                norm in _JUNK_APPLIED_NORMS
                or name.lower() in _JUNK_APPLIED_NORMS
                or "was received" in name.lower()
                or re.fullmatch(r"(?i)career|careers|wes", name)
                or (len(name) > 40)
            )
            if junk:
                conn.execute("DELETE FROM applied_companies WHERE company_norm=?", (norm,))
                removed += 1
    return removed


def list_applied_companies(limit: int = 500) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM applied_companies ORDER BY last_seen_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def is_company_already_applied(company: str) -> bool:
    """True if inbox/manual list or any job at this company is already applied."""
    key = _norm_company_key(company)
    if not key:
        return False
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM applied_companies WHERE company_norm=?",
            (key,),
        ).fetchone()
        if row:
            return True
        # Fuzzy: Scaleai ↔ scale, etc.
        for r in conn.execute("SELECT company_norm FROM applied_companies").fetchall():
            ak = r["company_norm"] or ""
            if len(ak) < 4:
                continue
            if ak == key or ak in key or key in ak:
                return True
            if ak.replace(" ", "") == key.replace(" ", ""):
                return True
        # Any tracker job already applied / interviewed / rejected at this company
        for r in conn.execute(
            """
            SELECT DISTINCT company FROM jobs
            WHERE status IN ('applied', 'interview', 'rejected', 'outreach_sent', 'followup_sent')
            """
        ).fetchall():
            jk = _norm_company_key(r["company"])
            if not jk:
                continue
            if jk == key or (len(jk) >= 4 and (jk in key or key in jk)):
                return True
    return False


def sync_applied_companies_from_jobs() -> int:
    """Ensure every company with an applied job is on the applied_companies list."""
    n = 0
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT company, title FROM jobs
            WHERE status IN ('applied', 'interview', 'rejected')
            """
        ).fetchall()
    for r in rows:
        before = is_company_already_applied(r["company"])
        remember_applied_company(r["company"], source="tracker", title=r["title"] or "")
        if not before:
            n += 1
    return n


def park_packets_at_applied_companies() -> int:
    """Move packet_ready/queued roles at already-applied companies out of the review queue."""
    now = datetime.utcnow().isoformat()
    parked = 0
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, company FROM jobs
            WHERE status IN ('packet_ready', 'queued')
            """
        ).fetchall()
        for r in rows:
            if is_company_already_applied(r["company"]):
                conn.execute(
                    "UPDATE jobs SET status=?, updated_at=?, notes=COALESCE(notes,'') || ? WHERE id=?",
                    (
                        JobStatus.SKIPPED.value,
                        now,
                        " | parked: already applied at company",
                        r["id"],
                    ),
                )
                parked += 1
    return parked


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


def log_event(event_type: str, job_id: str | None = None, payload: dict | None = None) -> None:
    now = datetime.utcnow().isoformat()
    with connect() as conn:
        conn.execute(
            "INSERT INTO events (event_type, job_id, payload_json, created_at) VALUES (?,?,?,?)",
            (event_type, job_id, json.dumps(payload or {}), now),
        )


def list_events(since: str | None = None, limit: int = 10_000) -> list[dict]:
    with connect() as conn:
        if since:
            rows = conn.execute(
                """
                SELECT * FROM events
                WHERE created_at >= ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (since, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM events ORDER BY created_at ASC LIMIT ?",
                (limit,),
            ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["payload"] = json.loads(d.pop("payload_json") or "{}")
        except json.JSONDecodeError:
            d["payload"] = {}
        out.append(d)
    return out


def set_outcome(job_id: str, outcome: str) -> None:
    """outcome: interview | rejected | ghost"""
    now = datetime.utcnow().isoformat()
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET outcome=?, outcome_at=?, updated_at=? WHERE id=?",
            (outcome, now, now, job_id),
        )
    log_event(f"outcome_{outcome}", job_id=job_id, payload={"outcome": outcome})


def set_prepped(job_id: str, prepped: bool = True) -> None:
    now = datetime.utcnow().isoformat()
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET prepped=?, updated_at=? WHERE id=?",
            (1 if prepped else 0, now, job_id),
        )


def hours_since_created(job_id: str) -> float | None:
    job = get_job(job_id)
    if not job:
        return None
    created = job.get("created_at")
    if not created:
        return None
    try:
        c = datetime.fromisoformat(created.replace("Z", ""))
    except ValueError:
        return None
    return round((datetime.utcnow() - c).total_seconds() / 3600.0, 2)


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
