"""Scan Gmail (IMAP) for job-application confirmation emails.

Looks for Greenhouse / Ashby / Lever / Workday / LinkedIn receipts and
"thank you for applying" style subjects, then extracts company (+ role when
possible) so the tracker can mark matches as already applied.
"""
from __future__ import annotations

import email
import imaplib
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.header import decode_header
from email.utils import parseaddr
from typing import Iterable, Optional

from rich.console import Console

from .. import db
from ..models import JobStatus

console = Console()

# ATS / career-site senders that almost always mean "you applied"
ATS_FROM_HINTS = (
    "greenhouse-mail.com",
    "greenhouse.io",
    "ashbyhq.com",
    "lever.co",
    "hire.lever.co",
    "myworkday.com",
    "myworkdayjobs.com",
    "workday.com",
    "smartrecruiters.com",
    "icims.com",
    "jobvite.com",
    "greenhouse.io",
    "notify.linkedin.com",
    "jobs-noreply@linkedin.com",
    "linkedin.com",
    "indeed.com",
    "wellfound.com",
    "angel.co",
    "rippling.com",
    "dover.io",
    "gem.com",
)

SUBJECT_PATTERNS = [
    re.compile(r"thank\s+you\s+for\s+applying(?:\s+to\s+(.+))?", re.I),
    re.compile(r"application\s+(?:received|submitted|confirmed)", re.I),
    re.compile(r"we\s+(?:have\s+)?received\s+your\s+application", re.I),
    re.compile(r"your\s+application\s+(?:to|for)\s+(.+)", re.I),
    re.compile(r"applied:\s*(.+)", re.I),
    re.compile(r"application\s+for\s+(.+?)\s+at\s+(.+)", re.I),
]

BODY_COMPANY_PATTERNS = [
    re.compile(r"thank\s+you\s+for\s+applying\s+to\s+([A-Z][\w\s.&'-]{1,60})", re.I),
    re.compile(r"application\s+(?:for|to)\s+(.{5,80}?)\s+at\s+([A-Z][\w\s.&'-]{1,60})", re.I),
    re.compile(r"applied\s+to\s+(?:the\s+)?(.{5,80}?)\s+(?:role\s+)?at\s+([A-Z][\w\s.&'-]{1,60})", re.I),
    re.compile(r"position:\s*(.+)", re.I),
    re.compile(r"role:\s*(.+)", re.I),
]

NOISE_COMPANY = {
    "linkedin",
    "indeed",
    "greenhouse",
    "ashby",
    "lever",
    "workday",
    "gmail",
    "google",
    "noreply",
    "no-reply",
    "careers",
    "jobs",
    "talent",
    "recruiting",
}


@dataclass
class InboxHit:
    message_id: str
    subject: str
    from_addr: str
    date: str
    company: str = ""
    title: str = ""
    confidence: float = 0.0
    raw_snippet: str = ""
    matched_job_ids: list[str] = field(default_factory=list)


def _decode(value: Optional[str]) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    out: list[str] = []
    for chunk, enc in parts:
        if isinstance(chunk, bytes):
            out.append(chunk.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(chunk)
    return "".join(out).strip()


def _plain_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        chunks: list[str] = []
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain":
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                chunks.append(payload.decode(charset, errors="replace"))
            elif ctype == "text/html" and not chunks:
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                html = payload.decode(charset, errors="replace")
                text = re.sub(r"<[^>]+>", " ", html)
                chunks.append(re.sub(r"\s+", " ", text))
        return "\n".join(chunks)[:4000]
    payload = msg.get_payload(decode=True) or b""
    charset = msg.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")[:4000]


def _clean_company(name: str) -> str:
    name = re.sub(r"\s+", " ", name or "").strip(" \t\n\r\"'.,;:|!-")
    # Cut off if we accidentally grabbed a whole sentence
    for stopper in (
        ". We ",
        ". Your ",
        ". Thank",
        " — ",
        " - we ",
        " we appreciate",
        " hiring team",
    ):
        idx = name.lower().find(stopper.lower())
        if idx > 2:
            name = name[:idx]
    name = re.sub(r"[!]+$", "", name).strip()
    # Drop trailing fluff
    name = re.sub(
        r"\b(careers|recruiting|talent|team|hiring|inc|llc|ltd)\.?$",
        "",
        name,
        flags=re.I,
    ).strip(" -")
    # Reject role titles mistaken for companies
    if re.search(
        r"\b(product manager|forward.?deployed|engineer|director|internship)\b",
        name,
        re.I,
    ):
        return ""
    if len(name) < 2 or len(name) > 60:
        return ""
    if name.lower() in NOISE_COMPANY:
        return ""
    if name.lower().startswith("the ") and " " in name[4:]:
        # "the Product Manager" etc.
        return ""
    return name[:60]


def _extract_from_subject(subject: str) -> tuple[str, str]:
    """Return (company, title) guesses from subject."""
    s = subject.strip()
    # "Thank you for applying to Stripe" / "Thank you for applying to Stripe!"
    m = re.search(r"thank\s+you\s+for\s+applying(?:\s+to\s+(.+?))(?:\s*[!.]|$)", s, re.I)
    if m and m.group(1):
        return _clean_company(m.group(1)), ""
    # "Your application for Senior PM at Stripe"
    m = re.search(r"application\s+for\s+(.+?)\s+at\s+(.+?)(?:\s*[!.]|$)", s, re.I)
    if m:
        return _clean_company(m.group(2)), m.group(1).strip()[:120]
    # "Applied: Senior Product Manager @ Anthropic"
    m = re.search(r"applied:\s*(.+?)\s*[@|–—-]\s*(.+)$", s, re.I)
    if m:
        return _clean_company(m.group(2)), m.group(1).strip()[:120]
    # "Application received — Stripe" / "Application submitted to Mercury"
    m = re.search(r"application\s+(?:received|submitted)(?:\s+(?:—|-|to)\s+(.+))?$", s, re.I)
    if m and m.group(1):
        return _clean_company(m.group(1)), ""
    # "Stripe: Application received"
    m = re.search(r"^([A-Z][\w\s.&'-]{1,40}):\s*application", s, re.I)
    if m:
        return _clean_company(m.group(1)), ""
    # "Discord! Thanks for applying" style — company bang at start
    m = re.search(r"^([A-Z][\w.&'-]{1,40})!\s+", s)
    if m:
        return _clean_company(m.group(1)), ""
    return "", ""


def _extract_from_body(body: str) -> tuple[str, str]:
    # Prefer short "applying to X" before long sentence captures
    m = re.search(
        r"thank\s+you\s+for\s+applying\s+to\s+([A-Z][\w.&'-]*(?:\s+[A-Z][\w.&'-]*){0,4})",
        body,
    )
    if m:
        return _clean_company(m.group(1)), ""
    for pat in BODY_COMPANY_PATTERNS:
        m = pat.search(body)
        if not m:
            continue
        groups = [g for g in m.groups() if g]
        if len(groups) >= 2:
            return _clean_company(groups[-1]), groups[0].strip()[:120]
        if len(groups) == 1:
            g = groups[0].strip()
            if " at " in g.lower():
                left, right = re.split(r"\s+at\s+", g, maxsplit=1, flags=re.I)
                return _clean_company(right), left.strip()[:120]
            return _clean_company(g), ""
    return "", ""


def _from_looks_ats(from_addr: str, from_name: str) -> bool:
    blob = f"{from_addr} {from_name}".lower()
    return any(h in blob for h in ATS_FROM_HINTS)


def _subject_looks_apply(subject: str) -> bool:
    return any(p.search(subject) for p in SUBJECT_PATTERNS)


def parse_message(raw: bytes) -> Optional[InboxHit]:
    msg = email.message_from_bytes(raw)
    subject = _decode(msg.get("Subject"))
    from_name, from_addr = parseaddr(msg.get("From") or "")
    from_name = _decode(from_name)
    date = _decode(msg.get("Date"))
    mid = (msg.get("Message-ID") or "").strip() or f"{from_addr}|{subject}|{date}"

    ats = _from_looks_ats(from_addr, from_name)
    applyish = _subject_looks_apply(subject)
    if not ats and not applyish:
        return None

    body = _plain_body(msg)
    company, title = _extract_from_subject(subject)
    if not company:
        c2, t2 = _extract_from_body(body)
        company = company or c2
        title = title or t2
    if not company and from_name and not _from_looks_ats("", from_name):
        # Display name sometimes is the company
        company = _clean_company(from_name)

    conf = 0.4
    if ats:
        conf += 0.25
    if applyish:
        conf += 0.2
    if company:
        conf += 0.15
    if title:
        conf += 0.05

    return InboxHit(
        message_id=mid[:200],
        subject=subject[:300],
        from_addr=from_addr.lower(),
        date=date[:80],
        company=company,
        title=title,
        confidence=min(conf, 0.99),
        raw_snippet=(body[:280] if body else subject),
    )


def _imap_connect() -> imaplib.IMAP4_SSL:
    host = os.getenv("IMAP_HOST") or "imap.gmail.com"
    port = int(os.getenv("IMAP_PORT") or "993")
    user = os.getenv("IMAP_USER") or os.getenv("SMTP_USER") or ""
    password = os.getenv("IMAP_PASS") or os.getenv("SMTP_PASS") or ""
    if not user or not password or password.startswith("your-"):
        raise RuntimeError(
            "IMAP credentials missing. Add SMTP_USER + SMTP_PASS (Gmail app password) "
            "to .env — same credentials work for inbox scan. "
            "https://myaccount.google.com/apppasswords"
        )
    client = imaplib.IMAP4_SSL(host, port)
    client.login(user, password)
    return client


def _search_query(days: int) -> str:
    # Gmail raw search is more reliable than nested IMAP OR trees
    return (
        f'X-GM-RAW "newer_than:{days}d '
        f'(subject:\\"thank you for applying\\" OR '
        f'subject:\\"application received\\" OR '
        f'subject:\\"we received your application\\" OR '
        f'subject:\\"your application\\" OR '
        f'from:greenhouse OR from:ashbyhq OR from:lever.co OR '
        f'from:workday OR from:linkedin.com OR from:indeed.com OR from:wellfound)"'
    )


def fetch_application_emails(days: int = 180, limit: int = 400) -> list[InboxHit]:
    client = _imap_connect()
    hits: list[InboxHit] = []
    try:
        client.select("INBOX", readonly=True)
        typ, data = client.search(None, _search_query(days))
        if typ != "OK" or not data or not data[0]:
            # Fallback: simpler SINCE + subject filter client-side
            since = (datetime.utcnow() - timedelta(days=days)).strftime("%d-%b-%Y")
            typ, data = client.search(None, f'(SINCE {since})')
        if typ != "OK" or not data or not data[0]:
            return []
        ids = data[0].split()
        ids = list(reversed(ids))[: max(limit * 3, limit)]
        for eid in ids:
            typ, msg_data = client.fetch(eid, "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            if not isinstance(raw, (bytes, bytearray)):
                continue
            hit = parse_message(bytes(raw))
            if hit:
                hits.append(hit)
            if len(hits) >= limit:
                break
    finally:
        try:
            client.logout()
        except Exception:
            pass
    return hits


def _norm_company(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\b(inc|llc|ltd|corp|co|ai|labs?|technologies|technology|software)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def match_jobs_for_hit(hit: InboxHit) -> list[str]:
    if not hit.company:
        return []
    target = _norm_company(hit.company)
    if len(target) < 2:
        return []
    rows = db.list_jobs(limit=2000, order="updated_at DESC")
    matched: list[str] = []
    for r in rows:
        if r.get("status") in ("applied", "skipped", "rejected", "closed"):
            continue
        c = _norm_company(r.get("company") or "")
        if not c:
            continue
        if target == c or target in c or c in target:
            # Optional title soft-match boost; still mark company-level
            matched.append(r["id"])
    return matched


def apply_inbox_hits(
    hits: Iterable[InboxHit],
    *,
    mark_applied: bool = True,
    min_confidence: float = 0.55,
) -> dict:
    """Persist hits and optionally mark matching jobs as applied."""
    saved = 0
    marked = 0
    companies: set[str] = set()
    for hit in hits:
        if hit.confidence < min_confidence:
            continue
        job_ids = match_jobs_for_hit(hit)
        hit.matched_job_ids = job_ids
        db.upsert_inbox_hit(
            {
                "message_id": hit.message_id,
                "subject": hit.subject,
                "from_addr": hit.from_addr,
                "date": hit.date,
                "company": hit.company,
                "title": hit.title,
                "confidence": hit.confidence,
                "snippet": hit.raw_snippet,
                "matched_job_ids": job_ids,
            }
        )
        saved += 1
        if hit.company:
            companies.add(hit.company)
            db.remember_applied_company(hit.company, source="inbox", title=hit.title)
        if mark_applied and job_ids:
            for jid in job_ids:
                job = db.get_job(jid)
                if not job or job.get("status") == JobStatus.APPLIED.value:
                    continue
                db.mark_sent(
                    jid,
                    touch_type="application",
                    channel="inbox-detected",
                    content=f"Detected from email: {hit.subject}",
                    followup_n=0,
                )
                note = (job.get("notes") or "").strip()
                extra = f"inbox-applied: {hit.company}"
                if extra not in note:
                    db.set_status_note(jid, f"{note}; {extra}".strip("; "))
                marked += 1
    return {
        "hits_saved": saved,
        "jobs_marked": marked,
        "companies": sorted(companies, key=str.lower),
    }


def scan_inbox(
    days: int = 180,
    limit: int = 400,
    mark_applied: bool = True,
    dry_run: bool = False,
) -> dict:
    console.print(f"[bold]Scanning inbox[/] for application receipts (last {days} days)…")
    hits = fetch_application_emails(days=days, limit=limit)
    console.print(f"Parsed [cyan]{len(hits)}[/] likely application emails")
    if dry_run:
        for h in hits[:40]:
            console.print(
                f"  • {h.company or '?':<22} | {(h.title or '')[:40]:<40} | "
                f"conf={h.confidence:.2f} | {h.subject[:60]}"
            )
        if len(hits) > 40:
            console.print(f"  … and {len(hits) - 40} more")
        return {"hits": len(hits), "dry_run": True, "companies": sorted({h.company for h in hits if h.company})}
    return apply_inbox_hits(hits, mark_applied=mark_applied)
