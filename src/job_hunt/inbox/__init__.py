"""Scan Gmail (IMAP) for job-application + rejection emails.

Catches Greenhouse / Ashby / Lever / Workday / LinkedIn receipts,
"thank you for applying", and out-of-consideration / rejection updates.
Marks companies so review won't re-surface them.
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
    re.compile(r"thank\s+you\s+for\s+(?:your\s+)?apply", re.I),
    re.compile(r"thanks\s+for\s+(?:your\s+)?apply", re.I),
    re.compile(r"application\s+(?:received|submitted|confirmed|update)", re.I),
    re.compile(r"we\s+(?:have\s+)?received\s+your\s+application", re.I),
    re.compile(r"your\s+application\s+(?:to|for)\s+", re.I),
    re.compile(r"applied:\s*", re.I),
    re.compile(r"regarding\s+your\s+application", re.I),
    re.compile(r"update\s+on\s+your\s+application", re.I),
    re.compile(r"application\s+status", re.I),
    re.compile(r"not\s+moving\s+forward", re.I),
    re.compile(r"out\s+of\s+consideration", re.I),
    re.compile(r"decided\s+not\s+to\s+(?:move|proceed)", re.I),
    re.compile(r"other\s+candidates", re.I),
    re.compile(r"will\s+not\s+be\s+moving\s+forward", re.I),
    re.compile(r"unfortunately", re.I),
    re.compile(r"we(?:'ve| have) decided", re.I),
]

# Signals that this email means rejection / out of consideration (not just receipt)
REJECTION_SUBJECT = re.compile(
    r"(not\s+moving\s+forward|out\s+of\s+consideration|"
    r"other\s+candidates|decided\s+not\s+to|will\s+not\s+be\s+moving|"
    r"application\s+unsuccessful|we\s+regret\s+to\s+inform)",
    re.I,
)
REJECTION_BODY = re.compile(
    r"(not\s+(?:be\s+)?moving\s+forward|out\s+of\s+consideration|"
    r"decided\s+not\s+to\s+(?:move|proceed|advance)|"
    r"will\s+not\s+be\s+progressing|pursue\s+other\s+candidates|"
    r"after\s+careful\s+(?:review|consideration)|"
    r"after\s+reviewing\s+your\s+application.{0,80}(?:determined|decided|unfortunately)|"
    r"we(?:'ve| have)\s+determined\s+that|"
    r"not\s+selected\s+(?:to\s+)?(?:move|advance|proceed)|"
    r"position\s+has\s+been\s+filled|"
    r"we\s+will\s+not\s+be\s+proceeding|"
    r"we\s+regret\s+to\s+inform)",
    re.I,
)

BODY_COMPANY_PATTERNS = [
    re.compile(r"thank\s+you\s+for\s+(?:your\s+)?apply(?:ing|ication)\s+to\s+([A-Z][\w\s.&'-]{1,60})", re.I),
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
    kind: str = "applied"  # applied | rejected
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
    # Cut off if we accidentally grabbed a whole sentence / receipt trailer
    for stopper in (
        ". We ",
        ". Your ",
        ". Thank",
        " — ",
        " - we ",
        " we appreciate",
        " hiring team",
        " was received",
        " has been received",
        " is received",
        " submitted",
        " confirmation",
    ):
        idx = name.lower().find(stopper.lower())
        if idx > 2:
            name = name[:idx]
    name = re.sub(r"[!]+$", "", name).strip()
    # Drop trailing fluff
    name = re.sub(
        r"\b(careers|recruiting|talent|team|hiring|inc|llc|ltd|was|received|submitted)\.?$",
        "",
        name,
        flags=re.I,
    ).strip(" -")
    # Second pass if "LlamaIndex was" style leftover
    name = re.sub(r"\b(was|has been)\b.*$", "", name, flags=re.I).strip(" -")
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
    # "Garvit, Thank You for Applying to Brex!"
    m = re.search(
        r"^[^,]{1,40},\s*thank\s+you\s+for\s+(?:your\s+)?apply(?:ing|ication)\s+to\s+(.+?)(?:\s*[!.]|$)",
        s,
        re.I,
    )
    if m:
        return _clean_company(m.group(1)), ""
    # "Thank you for applying to Stripe" / "Thank you for your application to Asana!"
    m = re.search(
        r"thank\s+you\s+for\s+(?:your\s+)?apply(?:ing|ication)(?:\s+to\s+(.+?))?(?:\s*[!.]|$)",
        s,
        re.I,
    )
    if m and m.group(1):
        return _clean_company(m.group(1)), ""
    # "Thanks for applying to Cohere!" / "Thanks for your application to X"
    m = re.search(
        r"thanks\s+for\s+(?:your\s+)?apply(?:ing|ication)(?:\s+to\s+(.+?))?(?:\s*[!.]|$)",
        s,
        re.I,
    )
    if m and m.group(1):
        return _clean_company(m.group(1)), ""
    # "Your application for Senior PM at Stripe" / "Your application to Stripe"
    # Also: "Your application to LlamaIndex was received"
    m = re.search(
        r"your\s+application\s+(?:for\s+(.+?)\s+at\s+|to\s+)"
        r"(.+?)(?:\s+(?:was|has been)\s+received|\s*[!.]|$)",
        s,
        re.I,
    )
    if m:
        title = (m.group(1) or "").strip()[:120]
        return _clean_company(m.group(2)), title
    # "Application for … at …"
    m = re.search(r"application\s+for\s+(.+?)\s+at\s+(.+?)(?:\s*[!.]|$)", s, re.I)
    if m:
        return _clean_company(m.group(2)), m.group(1).strip()[:120]
    # "Applied: Senior Product Manager @ Anthropic"
    m = re.search(r"applied:\s*(.+?)\s*[@|–—-]\s*(.+)$", s, re.I)
    if m:
        return _clean_company(m.group(2)), m.group(1).strip()[:120]
    # "Edra Update - AI Engineer (London)" / "Company Update: Role"
    m = re.search(r"^([A-Z][\w.&'-]{1,40})\s+update\s*[-–—:]\s*(.+)$", s, re.I)
    if m:
        return _clean_company(m.group(1)), m.group(2).strip()[:120]
    # "Update on your application to Stripe" / "Application update from Figma"
    m = re.search(
        r"(?:update\s+on\s+your\s+application|application\s+update|regarding\s+your\s+application)"
        r"(?:\s+(?:to|from|at|-)\s+(.+))?$",
        s,
        re.I,
    )
    if m and m.group(1):
        return _clean_company(m.group(1)), ""
    # "Application received — Stripe" / "ElevenLabs | Application Received"
    m = re.search(
        r"^(?:(.+?)\s*[|\-–—]\s*)?application\s+(?:received|submitted)(?:\s+(?:—|-|to)\s+(.+))?$",
        s,
        re.I,
    )
    if m:
        return _clean_company(m.group(2) or m.group(1) or ""), ""
    # "Stripe: Application received"
    m = re.search(r"^([A-Z][\w\s.&'-]{1,40}):\s*application", s, re.I)
    if m:
        return _clean_company(m.group(1)), ""
    # "Regarding your application to Modal" / "... to LlamaIndex was received"
    m = re.search(
        r"application\s+to\s+(.+?)(?:\s+(?:was|has been)\s+received|\s*[!.]|$)",
        s,
        re.I,
    )
    if m:
        return _clean_company(m.group(1)), ""
    # "Discord! Thanks for applying" style — company bang at start
    m = re.search(r"^([A-Z][\w.&'-]{1,40})!\s+", s)
    if m:
        return _clean_company(m.group(1)), ""
    return "", ""


def _detect_kind(subject: str, body: str) -> str:
    blob = f"{subject}\n{body[:2500]}"
    thanks = re.search(r"thank(?:s| you)\s+for\s+(?:your\s+)?apply", subject, re.I)
    if thanks:
        return "applied"
    if REJECTION_SUBJECT.search(subject):
        return "rejected"
    # "Company Update - Role" only if body clearly rejects
    if re.search(r"\bupdate\b", subject, re.I) and REJECTION_BODY.search(body[:2500] or ""):
        return "rejected"
    if REJECTION_BODY.search(blob):
        return "rejected"
    return "applied"


def _extract_from_body(body: str) -> tuple[str, str]:
    # Prefer short "applying / application to X" before long sentence captures
    m = re.search(
        r"thank\s+you\s+for\s+(?:your\s+)?apply(?:ing|ication)\s+to\s+"
        r"([A-Z][\w.&'-]*(?:\s+[A-Z][\w.&'-]*){0,4})",
        body,
        re.I,
    )
    if m:
        return _clean_company(m.group(1)), ""
    m = re.search(
        r"thanks\s+for\s+(?:your\s+)?apply(?:ing|ication)\s+to\s+"
        r"([A-Z][\w.&'-]*(?:\s+[A-Z][\w.&'-]*){0,4})",
        body,
        re.I,
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

    body = _plain_body(msg)
    ats = _from_looks_ats(from_addr, from_name)
    applyish = _subject_looks_apply(subject)
    rejectish = bool(REJECTION_SUBJECT.search(subject) or REJECTION_BODY.search(body[:2500]))
    if not ats and not applyish and not rejectish:
        return None

    company, title = _extract_from_subject(subject)
    if not company:
        c2, t2 = _extract_from_body(body)
        company = company or c2
        title = title or t2
    if not company and from_name and not _from_looks_ats("", from_name):
        company = _clean_company(from_name)

    kind = _detect_kind(subject, body)

    conf = 0.4
    if ats:
        conf += 0.25
    if applyish or rejectish:
        conf += 0.2
    if company:
        conf += 0.15
    if title:
        conf += 0.05
    if kind == "rejected" and company:
        conf = max(conf, 0.7)

    return InboxHit(
        message_id=mid[:200],
        subject=subject[:300],
        from_addr=from_addr.lower(),
        date=date[:80],
        company=company,
        title=title,
        confidence=min(conf, 0.99),
        raw_snippet=(body[:280] if body else subject),
        kind=kind,
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
    # Gmail raw search — applications + rejections / updates
    return (
        f'X-GM-RAW "newer_than:{days}d '
        f'(subject:\\"thank you for applying\\" OR '
        f'subject:\\"thank you for your application\\" OR '
        f'subject:\\"thanks for applying\\" OR '
        f'subject:\\"application received\\" OR '
        f'subject:\\"we received your application\\" OR '
        f'subject:\\"your application\\" OR '
        f'subject:\\"regarding your application\\" OR '
        f'subject:\\"update on your application\\" OR '
        f'subject:\\"application update\\" OR '
        f'subject:\\"not moving forward\\" OR '
        f'subject:\\"out of consideration\\" OR '
        f'subject:\\"unfortunately\\" OR '
        f'subject:\\"Update -\\" OR '
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
    target = db._norm_company_key(hit.company)
    if len(target) < 2:
        return []
    rows = db.list_jobs(limit=2000, order="updated_at DESC")
    matched: list[str] = []
    for r in rows:
        st = r.get("status") or ""
        notes = r.get("notes") or ""
        if hit.kind == "rejected":
            if st in ("skipped", "rejected", "closed"):
                continue
        else:
            # applied receipt: skip terminal states unless recovering a bad inbox-reject
            if st in ("skipped", "closed", "interview"):
                continue
            if st == "rejected" and "inbox-rejected" not in notes:
                continue
            if st == "applied" and "inbox-rejected" not in notes:
                continue
        c = db._norm_company_key(r.get("company") or "")
        if not c:
            continue
        if target == c or (len(target) >= 4 and (target in c or c in target)):
            matched.append(r["id"])
    return matched


def apply_inbox_hits(
    hits: Iterable[InboxHit],
    *,
    mark_applied: bool = True,
    min_confidence: float = 0.55,
) -> dict:
    """Persist hits; mark applied and/or rejected so companies leave the review queue."""
    saved = 0
    marked_applied = 0
    marked_rejected = 0
    companies: set[str] = set()
    rejected_cos: set[str] = set()
    for hit in hits:
        if hit.confidence < min_confidence:
            continue
        # Prefer title-matched jobs when we have a role hint
        job_ids = match_jobs_for_hit(hit)
        if hit.title and job_ids:
            titled = []
            tnorm = re.sub(r"\W+", " ", hit.title.lower())
            for jid in job_ids:
                job = db.get_job(jid)
                if not job:
                    continue
                jnorm = re.sub(r"\W+", " ", (job.get("title") or "").lower())
                if any(tok in jnorm for tok in tnorm.split() if len(tok) > 3):
                    titled.append(jid)
            if titled:
                job_ids = titled
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
                "snippet": f"[{hit.kind}] {hit.raw_snippet}",
                "matched_job_ids": job_ids,
            }
        )
        saved += 1
        if hit.company:
            companies.add(hit.company)
            # Always remember — applied or rejected means don't re-queue
            src = "inbox-rejected" if hit.kind == "rejected" else "inbox"
            db.remember_applied_company(hit.company, source=src, title=hit.title)

        if not mark_applied:
            continue

        if hit.kind == "rejected":
            if hit.company:
                rejected_cos.add(hit.company)
            # Only reject title-matched jobs when possible; never spray whole ATS board
            targets = job_ids
            if hit.title and job_ids:
                tnorm = re.sub(r"\W+", " ", hit.title.lower())
                tight = []
                for jid in job_ids:
                    job = db.get_job(jid)
                    if not job:
                        continue
                    jnorm = re.sub(r"\W+", " ", (job.get("title") or "").lower())
                    tokens = [tok for tok in tnorm.split() if len(tok) > 3]
                    if tokens and sum(1 for tok in tokens if tok in jnorm) >= max(1, len(tokens) // 2):
                        tight.append(jid)
                if tight:
                    targets = tight
            for jid in targets:
                job = db.get_job(jid)
                if not job or job.get("status") == JobStatus.REJECTED.value:
                    continue
                db.set_status(jid, JobStatus.REJECTED)
                note = (job.get("notes") or "").strip()
                extra = f"inbox-rejected: {hit.subject[:80]}"
                if extra not in note:
                    db.set_status_note(jid, f"{note}; {extra}".strip("; "))
                marked_rejected += 1
        else:
            # Applied receipt: remember company (blocks review). Only mark job applied if title matches.
            targets = job_ids
            if hit.title and job_ids:
                tnorm = re.sub(r"\W+", " ", hit.title.lower())
                tight = []
                for jid in job_ids:
                    job = db.get_job(jid)
                    if not job:
                        continue
                    jnorm = re.sub(r"\W+", " ", (job.get("title") or "").lower())
                    tokens = [tok for tok in tnorm.split() if len(tok) > 3]
                    if tokens and any(tok in jnorm for tok in tokens):
                        tight.append(jid)
                if tight:
                    targets = tight
                else:
                    targets = []  # company blocklist is enough
            elif not hit.title:
                # No role hint — block company only, don't mass-mark every role applied
                targets = []
            for jid in targets:
                job = db.get_job(jid)
                if not job or job.get("status") in (
                    JobStatus.APPLIED.value,
                    JobStatus.REJECTED.value,
                ):
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
                marked_applied += 1

    parked = db.park_packets_at_applied_companies()
    return {
        "hits_saved": saved,
        "jobs_marked": marked_applied,
        "jobs_rejected": marked_rejected,
        "companies": sorted(c for c in companies if c),
        "rejected_companies": sorted(c for c in rejected_cos if c),
        "parked": parked,
    }


def scan_inbox(
    days: int = 180,
    limit: int = 400,
    mark_applied: bool = True,
    dry_run: bool = False,
) -> dict:
    console.print(
        f"[bold]Scanning inbox[/] for application + rejection emails (last {days} days)…"
    )
    hits = fetch_application_emails(days=days, limit=limit)
    applied_n = sum(1 for h in hits if h.kind == "applied")
    rejected_n = sum(1 for h in hits if h.kind == "rejected")
    console.print(
        f"Parsed [cyan]{len(hits)}[/] emails "
        f"([green]{applied_n}[/] applied · [yellow]{rejected_n}[/] rejected/out)"
    )
    if dry_run:
        for h in hits[:50]:
            console.print(
                f"  • [{h.kind:<8}] {h.company or '?':<22} | {(h.title or '')[:36]:<36} | "
                f"{h.subject[:55]}"
            )
        if len(hits) > 50:
            console.print(f"  … and {len(hits) - 50} more")
        return {
            "hits": len(hits),
            "dry_run": True,
            "companies": sorted({h.company for h in hits if h.company}),
            "rejected_companies": sorted({h.company for h in hits if h.kind == "rejected" and h.company}),
        }
    return apply_inbox_hits(hits, mark_applied=mark_applied)
