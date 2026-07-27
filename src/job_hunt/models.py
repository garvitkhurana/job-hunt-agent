from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    DISCOVERED = "discovered"
    SCORED = "scored"
    PACKET_READY = "packet_ready"
    QUEUED = "queued"
    APPROVED = "approved"
    APPLIED = "applied"
    OUTREACH_SENT = "outreach_sent"
    FOLLOWUP_DUE = "followup_due"
    FOLLOWUP_SENT = "followup_sent"
    REPLIED = "replied"
    INTERVIEW = "interview"
    REJECTED = "rejected"
    SKIPPED = "skipped"
    CLOSED = "closed"


class JobSource(str, Enum):
    GREENHOUSE = "greenhouse"
    ASHBY = "ashby"
    YC = "yc"
    MANUAL = "manual"
    OTHER = "other"


class Job(BaseModel):
    id: str
    source: JobSource
    company: str
    title: str
    location: str = ""
    url: str = ""
    description: str = ""
    department: str = ""
    remote: bool = False
    posted_at: Optional[str] = None
    raw: Dict[str, Any] = Field(default_factory=dict)


class ScoreBreakdown(BaseModel):
    role_fit: float = 0.0
    location_fit: float = 0.0
    keyword_fit: float = 0.0
    seniority_fit: float = 0.0
    company_score: float = 0.0
    company_tier: str = "unknown"
    track: str = "core"
    role_family: str = ""
    total: float = 0.0
    reasons: List[str] = Field(default_factory=list)


class ApplicationPacket(BaseModel):
    job_id: str
    tailored_resume_md: str
    cover_letter: str
    linkedin_note: str
    email_subject: str
    email_body: str
    founder_pitch: str = ""
    apply_checklist: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class TouchType(str, Enum):
    APPLICATION = "application"
    LINKEDIN = "linkedin"
    EMAIL = "email"
    FOLLOWUP = "followup"
    REFERRAL_ASK = "referral_ask"


class Touch(BaseModel):
    job_id: str
    touch_type: TouchType
    channel: str
    content: str
    sent_at: Optional[datetime] = None
    followup_n: int = 0
