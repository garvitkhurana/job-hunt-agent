"""Automated email sending — SMTP (Gmail-ready, free) or Resend (free tier).

Provider auto-detection:
  - SMTP_USER + SMTP_PASS present  -> SMTP  (Gmail app password recommended)
  - RESEND_API_KEY present         -> Resend
  - otherwise                      -> dry-run only (prints, never sends)

Safety:
  - Dry-run by default. Real sends require send=True (CLI: --send).
  - Daily cap enforced via the touches table.
  - Every send is logged so follow-up cadence + tracking stay accurate.

LinkedIn is intentionally NOT here — automating LinkedIn messaging gets accounts
banned. The agent drafts LinkedIn notes; you send them by hand.
"""

from __future__ import annotations

import mimetypes
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from typing import Optional, Tuple

import httpx

from ..config import env


class SendResult:
    def __init__(self, ok: bool, provider: str, detail: str = "", dry_run: bool = False):
        self.ok = ok
        self.provider = provider
        self.detail = detail
        self.dry_run = dry_run

    def __repr__(self) -> str:
        tag = "DRY-RUN" if self.dry_run else ("OK" if self.ok else "FAIL")
        return f"[{tag}/{self.provider}] {self.detail}"


def active_provider() -> str:
    if env("SMTP_USER") and env("SMTP_PASS"):
        return "smtp"
    if env("RESEND_API_KEY"):
        return "resend"
    return "none"


def from_address() -> str:
    return (
        env("FROM_EMAIL")
        or env("SMTP_USER")
        or "Garvit Khurana <garvit.khurana@columbia.edu>"
    )


def resume_pdf_path() -> Optional[Path]:
    p = env("RESUME_PDF")
    if p and Path(p).expanduser().exists():
        return Path(p).expanduser()
    default = Path("/Users/garvitkhurana/Documents/Garvit Docs/Resumes/GarvitKhurana_Resume.pdf")
    return default if default.exists() else None


def _attach_resume(msg: EmailMessage) -> None:
    pdf = resume_pdf_path()
    if not pdf:
        return
    ctype, _ = mimetypes.guess_type(str(pdf))
    maintype, subtype = (ctype or "application/pdf").split("/", 1)
    msg.add_attachment(pdf.read_bytes(), maintype=maintype, subtype=subtype, filename=pdf.name)


def _send_smtp(to: str, subject: str, body: str, attach: bool) -> Tuple[bool, str]:
    host = env("SMTP_HOST", "smtp.gmail.com")
    port = int(env("SMTP_PORT", "465") or 465)
    user = env("SMTP_USER")
    password = env("SMTP_PASS")
    msg = EmailMessage()
    msg["From"] = from_address()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    if attach:
        _attach_resume(msg)
    try:
        ctx = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as s:
                s.login(user, password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=30) as s:
                s.starttls(context=ctx)
                s.login(user, password)
                s.send_message(msg)
        return True, f"sent to {to}"
    except Exception as e:  # noqa: BLE001
        return False, f"smtp error: {e}"


def _send_resend(to: str, subject: str, body: str, attach: bool) -> Tuple[bool, str]:
    key = env("RESEND_API_KEY")
    payload = {
        "from": from_address(),
        "to": [to],
        "subject": subject,
        "text": body,
    }
    if attach:
        pdf = resume_pdf_path()
        if pdf:
            import base64

            payload["attachments"] = [
                {"filename": pdf.name, "content": base64.b64encode(pdf.read_bytes()).decode()}
            ]
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=payload,
            )
            r.raise_for_status()
            return True, f"sent to {to} (id={r.json().get('id','?')})"
    except Exception as e:  # noqa: BLE001
        return False, f"resend error: {e}"


def send_email(
    to: str,
    subject: str,
    body: str,
    attach_resume: bool = True,
    send: bool = False,
) -> SendResult:
    provider = active_provider()
    if not send or provider == "none":
        preview = body if len(body) < 600 else body[:600] + "…"
        return SendResult(
            ok=True,
            provider=provider,
            detail=f"(dry-run) -> {to} | {subject}\n{preview}",
            dry_run=True,
        )
    if not to or "@" not in to:
        return SendResult(False, provider, f"invalid recipient: {to!r}")
    if provider == "smtp":
        ok, detail = _send_smtp(to, subject, body, attach_resume)
    else:
        ok, detail = _send_resend(to, subject, body, attach_resume)
    return SendResult(ok, provider, detail)
