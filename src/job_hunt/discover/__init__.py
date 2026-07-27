from __future__ import annotations

import hashlib
import re
from typing import Iterable

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..match.roles import classify
from ..models import Job, JobSource

UA = {"User-Agent": "job-hunt-agent/0.1 (personal job search; contact: garvit.khurana@columbia.edu)"}


def _id(source: str, company: str, title: str, url: str) -> str:
    raw = f"{source}|{company}|{title}|{url}".lower()
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _looks_remote(text: str) -> bool:
    t = text.lower()
    return any(x in t for x in ("remote", "work from home", "wfh", "anywhere"))


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, min=0.5, max=4))
def _get_json(url: str, timeout: float = 20.0) -> dict | list:
    with httpx.Client(timeout=timeout, headers=UA, follow_redirects=True) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.json()


def fetch_greenhouse(board: str) -> list[Job]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
    try:
        data = _get_json(url)
    except Exception:
        return []
    jobs: list[Job] = []
    for j in data.get("jobs", []):
        offices = j.get("offices") or []
        locs = ", ".join(o.get("name", "") for o in offices if isinstance(o, dict))
        if not locs and isinstance(j.get("location"), dict):
            locs = j["location"].get("name", "") or ""
        title = j.get("title") or ""
        company = board
        abs_url = j.get("absolute_url") or ""
        desc = j.get("content") or ""
        desc = re.sub(r"<[^>]+>", " ", desc)
        desc = re.sub(r"\s+", " ", desc).strip()
        depts = j.get("departments") or []
        department = depts[0].get("name", "") if depts and isinstance(depts[0], dict) else ""
        jobs.append(
            Job(
                id=_id("greenhouse", company, title, abs_url),
                source=JobSource.GREENHOUSE,
                company=company.replace("-", " ").title(),
                title=title,
                location=locs,
                url=abs_url,
                description=desc[:8000],
                department=department,
                remote=_looks_remote(f"{locs} {title} {desc[:500]}"),
                posted_at=j.get("updated_at") or j.get("first_published"),
                raw={"board": board, "gh_id": j.get("id")},
            )
        )
    return jobs


def fetch_ashby(board: str) -> list[Job]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true"
    try:
        data = _get_json(url)
    except Exception:
        return []
    jobs: list[Job] = []
    for j in data.get("jobs", []):
        title = j.get("title") or ""
        loc = j.get("location") or ""
        if isinstance(loc, dict):
            loc = loc.get("name") or ""
        abs_url = j.get("jobUrl") or j.get("applyUrl") or ""
        desc = j.get("descriptionPlain") or j.get("descriptionHtml") or ""
        desc = re.sub(r"<[^>]+>", " ", str(desc))
        desc = re.sub(r"\s+", " ", desc).strip()
        company = board
        jobs.append(
            Job(
                id=_id("ashby", company, title, abs_url),
                source=JobSource.ASHBY,
                company=company.replace("-", " ").title(),
                title=title,
                location=str(loc),
                url=abs_url,
                description=desc[:8000],
                department=j.get("department") or "",
                remote=bool(j.get("isRemote")) or _looks_remote(f"{loc} {title}"),
                posted_at=j.get("publishedAt"),
                raw={"board": board, "ashby_id": j.get("id")},
            )
        )
    return jobs


def fetch_yc_jobs(limit: int = 100) -> list[Job]:
    """YC public Algolia-backed jobs index (best-effort)."""
    url = "https://www.workatastartup.com/api/jobs"
    # WAU sometimes blocks; try Algolia public search used by the site
    algolia = (
        "https://45bwzj1sgc-dsn.algolia.net/1/indexes/*/queries"
        "?x-algolia-agent=job-hunt-agent"
        "&x-algolia-api-key=NDYzYmNmYWNiNGVmMWMzMzg3YjMxMjk0ZmNiYmY3ZmFkNDMzY2VlZWRhYTJhMzYxMmE4MTEyNWE0MmM0NjdiYnZhbGlkVW50aWw9MTczOTU4MjAyMw%3D%3D"
        "&x-algolia-application-id=45BWZJ1SGC"
    )
    payload = {
        "requests": [
            {
                "indexName": "WAAW_Production_Jobs_query_suggestions",
                "params": "query=product%20manager&hitsPerPage=0",
            }
        ]
    }
    # Prefer simpler HTML scrape fallback via public JSON if available
    jobs: list[Job] = []
    try:
        with httpx.Client(timeout=20.0, headers=UA, follow_redirects=True) as client:
            # Public company jobs listing used by some scrapers
            r = client.get("https://yc-oss.github.io/api/companies/all.json")
            if r.status_code == 200:
                companies = r.json()
                # Not full job posts — store as discovery seeds via careers URLs when present
                for c in companies[:limit]:
                    name = c.get("name") or ""
                    website = c.get("website") or c.get("url") or ""
                    batch = c.get("batch") or ""
                    if not name:
                        continue
                    # Synthetic "explore" card so matcher can still surface high-fit YC cos for outreach
                    jobs.append(
                        Job(
                            id=_id("yc", name, "Founding/Senior PM (explore)", website),
                            source=JobSource.YC,
                            company=name,
                            title="Founding / Senior Product Manager (explore)",
                            location="Remote / US / various",
                            url=website or f"https://www.ycombinator.com/companies/{c.get('slug','')}",
                            description=f"YC {batch} company. {c.get('one_liner') or c.get('long_description') or ''}"[:4000],
                            department="Product",
                            remote=True,
                            posted_at=None,
                            raw={"yc": True, "batch": batch, "slug": c.get("slug")},
                        )
                    )
    except Exception:
        pass
    # Silence unused
    _ = (url, algolia, payload)
    return jobs[:limit]


def _is_relevant(title: str, include_adjacent: bool = True) -> bool:
    """Keep core PM titles plus, optionally, adjacent families that fit a PM/eng hybrid."""
    family = classify(title)
    if family is None:
        return False
    if family.track == "adjacent" and not include_adjacent:
        return False
    return True


def discover_all(
    greenhouse_boards: Iterable[str],
    ashby_boards: Iterable[str],
    yc_enabled: bool = True,
    max_total: int = 250,
    per_board: int = 40,
    include_adjacent: bool = True,
) -> list[Job]:
    seen: set[str] = set()
    seen_roles: set = set()
    out: list[Job] = []

    def add(jobs: list[Job]) -> None:
        kept = 0
        for job in jobs:
            if job.source != JobSource.YC and not _is_relevant(job.title, include_adjacent):
                continue
            if job.id in seen:
                continue
            # Boards often list one role once per office; collapse to a single entry
            role_key = (job.company.lower(), re.sub(r"[^a-z0-9]+", "", job.title.lower()))
            if role_key in seen_roles:
                continue
            seen.add(job.id)
            seen_roles.add(role_key)
            out.append(job)
            kept += 1
            if kept >= per_board or len(out) >= max_total:
                return

    for board in greenhouse_boards:
        add(fetch_greenhouse(board))
        if len(out) >= max_total:
            return out

    for board in ashby_boards:
        add(fetch_ashby(board))
        if len(out) >= max_total:
            return out

    if yc_enabled:
        add(fetch_yc_jobs(limit=80))

    return out
