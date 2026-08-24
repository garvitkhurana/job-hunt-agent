from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


class CandidateConfig(BaseModel):
    name: str
    email: str
    phone: str
    linkedin: str
    github: str
    current_title: str
    target_roles: List[str]
    target_keywords: List[str]
    locations: List[str]
    scheduling_link: Optional[str] = None
    # Visa-friendly geos get a ranking boost when matched
    priority_locations: List[str] = Field(default_factory=list)

    def resolved_scheduling_link(self) -> Optional[str]:
        # .env overrides config so you can keep the link out of git
        return os.getenv("SCHEDULING_LINK") or self.scheduling_link


class FiltersConfig(BaseModel):
    min_score: float = 0.62
    exclude_keywords: List[str] = Field(default_factory=list)
    prefer_keywords: List[str] = Field(default_factory=list)
    # Adjacent (non-PM) roles are only surfaced at companies at/above this quality score
    include_adjacent_roles: bool = True
    min_company_tier: float = 0.75
    min_adjacent_score: float = 0.66
    # Tuning knobs for metrics-driven optimization
    stretch_penalty: float = 0.72
    stretch_min_score: float = 0.78
    visa_priority_boost: float = 0.08
    founding_boost: float = 0.07
    ai_title_boost: float = 0.05
    adjacent_track_mult: float = 0.92


class DailyConfig(BaseModel):
    app_target: int = 30
    outreach_target: int = 40
    adjacent_target: int = 15
    max_discover: int = 250


class FollowupConfig(BaseModel):
    cadence_days: List[int] = Field(default_factory=lambda: [3, 7, 14])
    max_followups: int = 3


class SourcesConfig(BaseModel):
    greenhouse_boards: List[str] = Field(default_factory=list)
    ashby_boards: List[str] = Field(default_factory=list)
    extra_greenhouse: List[str] = Field(default_factory=list)
    extra_ashby: List[str] = Field(default_factory=list)
    yc_enabled: bool = True


class GenerationConfig(BaseModel):
    tone: str = "confident, specific, founder-friendly"
    linkedin_max_chars: int = 280
    email_max_words: int = 160
    cover_max_words: int = 220


class AppConfig(BaseModel):
    candidate: CandidateConfig
    filters: FiltersConfig
    daily: DailyConfig
    followup: FollowupConfig
    sources: SourcesConfig
    generation: GenerationConfig


def load_config(path: Optional[Path] = None) -> AppConfig:
    cfg_path = path or (ROOT / "config.yaml")
    data = yaml.safe_load(cfg_path.read_text())
    return AppConfig.model_validate(data)


def load_profile(path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or (ROOT / "data" / "profile.yaml")
    return yaml.safe_load(p.read_text())


def load_master_resume(path: Optional[Path] = None) -> str:
    p = path or (ROOT / "data" / "resume_master.md")
    return p.read_text()


def env(key: str, default: Optional[str] = None) -> Optional[str]:
    return os.getenv(key, default)


def data_dir() -> Path:
    d = ROOT / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def packets_dir() -> Path:
    d = data_dir() / "packets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def resumes_dir() -> Path:
    d = data_dir() / "resumes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return data_dir() / "hunt.db"
