"""Find the right people to reach out to for a given role.

Strategy (in priority order):
  1. Hiring manager  — Head of Product / VP Product / CPO (or founder/CEO for founding roles)
  2. Warm 2nd-degree connection on the team (referral = best conversion)
  3. Team peers (other PMs / eng)
  4. Recruiter (fast logistics, lowest leverage for senior roles)

LinkedIn is never auto-messaged (bans accounts). We build safe *search URLs* you open
and act on manually. Emails can be discovered via Apollo (if key set) or pattern-guessed.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional
from urllib.parse import quote_plus

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import env
from ..models import Job

UA = {"User-Agent": "job-hunt-agent/0.1 (personal job search)"}


# --- persona targeting -------------------------------------------------------

class Persona:
    def __init__(self, priority: int, label: str, titles: List[str], channel: str, note: str):
        self.priority = priority
        self.label = label
        self.titles = titles
        self.channel = channel
        self.note = note


def _is_founding(job: Job) -> bool:
    t = job.title.lower()
    return any(k in t for k in ("founding", "co-founder", "first product manager", "first pm"))


def _is_engineering(job: Job) -> bool:
    t = job.title.lower()
    return any(k in t for k in ("engineer", "forward deployed", "fde", "solutions architect", "solutions engineer", "solutions consultant"))


def target_personas(job: Job) -> List[Persona]:
    """Who to contact, tailored to the role type."""
    personas: List[Persona] = []

    if _is_founding(job):
        personas.append(
            Persona(
                1,
                "Founder / CEO",
                ["Founder", "Co-Founder", "CEO", "Chief Executive"],
                "linkedin+email",
                "For founding roles the founder IS the hiring manager. Lead with your 0->1 RAG build.",
            )
        )
        personas.append(
            Persona(2, "CTO / Technical co-founder", ["CTO", "Co-Founder", "Chief Technology"], "linkedin+email", "Show hands-on eng depth (Python/LangChain)." )
        )
    elif _is_engineering(job):
        personas.append(
            Persona(1, "Eng hiring manager", ["Head of Engineering", "VP Engineering", "Engineering Manager", "Director of Engineering"], "linkedin+email", "Lead with the hybrid product+eng angle and shipped systems.")
        )
        personas.append(
            Persona(2, "Head of Applied AI / FDE lead", ["Head of Applied AI", "Forward Deployed", "Head of Solutions", "Applied AI Lead"], "linkedin+email", "Most relevant for forward-deployed roles.")
        )
    else:
        personas.append(
            Persona(1, "Product hiring manager", ["Head of Product", "VP Product", "VP of Product", "Chief Product Officer", "Director of Product", "Group Product Manager"], "linkedin+email", "Primary target. Reply here skips the ATS. Lead with 90-day angle.")
        )

    personas.append(
        Persona(3, "Team peer", ["Product Manager", "Senior Product Manager", "Product"] if not _is_engineering(job) else ["Software Engineer", "AI Engineer", "Product Engineer"], "linkedin", "Ask for an honest read + a possible referral.")
    )
    personas.append(
        Persona(4, "Recruiter / Talent", ["Recruiter", "Technical Recruiter", "Talent", "People"], "linkedin+email", "Fast logistics; lower leverage for senior roles.")
    )
    return personas


# --- LinkedIn safe search URLs (open + act manually) -------------------------

def linkedin_people_search(company: str, titles: List[str]) -> str:
    kw = " ".join(titles[:2])
    return (
        "https://www.linkedin.com/search/results/people/"
        f"?keywords={quote_plus(kw)}&origin=FACETED_SEARCH"
        f"&company={quote_plus(company)}"
    )


def linkedin_company_people(company: str) -> str:
    return f"https://www.linkedin.com/company/{quote_plus(company.lower().replace(' ', '-'))}/people/"


def google_fallback(company: str, titles: List[str]) -> str:
    q = f'site:linkedin.com/in "{titles[0]}" "{company}"'
    return f"https://www.google.com/search?q={quote_plus(q)}"


# --- email discovery ---------------------------------------------------------

def guess_domain(company: str, url: str = "") -> Optional[str]:
    # Prefer the apply URL host
    m = re.search(r"https?://([^/]+)/", url or "")
    if m:
        host = m.group(1).lower()
        host = re.sub(r"^(www|boards|jobs|careers|apply|job-boards)\.", "", host)
        if "greenhouse" not in host and "ashby" not in host and "lever" not in host and "workday" not in host:
            return host
    slug = re.sub(r"[^a-z0-9]", "", company.lower())
    return f"{slug}.com" if slug else None


def email_patterns(first: str, last: str, domain: str) -> List[str]:
    first = re.sub(r"[^a-z]", "", first.lower())
    last = re.sub(r"[^a-z]", "", last.lower())
    if not first or not domain:
        return []
    f, l = first[0], (last[0] if last else "")
    cands = [
        f"{first}.{last}@{domain}",
        f"{first}@{domain}",
        f"{f}{last}@{domain}",
        f"{first}{last}@{domain}",
        f"{first}_{last}@{domain}",
        f"{last}.{first}@{domain}",
        f"{f}.{last}@{domain}",
    ]
    seen, out = set(), []
    for c in cands:
        if c not in seen and "@" in c and c.split("@")[1]:
            seen.add(c)
            out.append(c)
    return out


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=0.5, min=0.5, max=3))
def apollo_people(company: str, titles: List[str], domain: Optional[str], limit: int = 5) -> List[Dict]:
    """Apollo people search. Needs APOLLO_API_KEY. Returns [{name,title,email,linkedin}]."""
    key = env("APOLLO_API_KEY")
    if not key:
        return []
    payload = {
        "person_titles": titles,
        "page": 1,
        "per_page": limit,
    }
    if domain:
        payload["q_organization_domains"] = domain
    else:
        payload["q_organization_name"] = company
    headers = {"Content-Type": "application/json", "Cache-Control": "no-cache", "X-Api-Key": key, **UA}
    try:
        with httpx.Client(timeout=20.0, headers=headers) as client:
            r = client.post("https://api.apollo.io/v1/mixed_people/search", json=payload)
            r.raise_for_status()
            data = r.json()
    except Exception:
        return []
    people = []
    for p in data.get("people", [])[:limit]:
        people.append(
            {
                "name": f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
                "first": p.get("first_name", ""),
                "last": p.get("last_name", ""),
                "title": p.get("title", ""),
                "email": p.get("email") or "",
                "linkedin": p.get("linkedin_url") or "",
                "source": "apollo",
            }
        )
    return people


def hunter_domain_pattern(domain: str) -> Optional[str]:
    key = env("HUNTER_API_KEY")
    if not key or not domain:
        return None
    try:
        with httpx.Client(timeout=15.0, headers=UA) as client:
            r = client.get(f"https://api.hunter.io/v2/domain-search?domain={domain}&api_key={key}&limit=1")
            r.raise_for_status()
            return r.json().get("data", {}).get("pattern")
    except Exception:
        return None


def find_contacts(job: Job) -> Dict:
    """Assemble a full outreach target sheet for a job."""
    personas = target_personas(job)
    domain = guess_domain(job.company, job.url)
    result = {
        "company": job.company,
        "domain": domain,
        "hunter_pattern": hunter_domain_pattern(domain) if domain else None,
        "personas": [],
    }
    for persona in personas:
        block = {
            "priority": persona.priority,
            "label": persona.label,
            "channel": persona.channel,
            "note": persona.note,
            "linkedin_search": linkedin_people_search(job.company, persona.titles),
            "linkedin_company_people": linkedin_company_people(job.company),
            "google_fallback": google_fallback(job.company, persona.titles),
            "people": [],
            "email_guesses": [],
        }
        # Live people via Apollo (if key)
        people = apollo_people(job.company, persona.titles, domain, limit=4)
        for p in people:
            if not p["email"] and domain and p.get("first"):
                p["email_guesses"] = email_patterns(p["first"], p.get("last", ""), domain)
            block["people"].append(p)
        # Generic pattern hint even without names
        if domain and not people:
            block["email_guesses"] = [f"<firstname>@{domain}", f"<first>.<last>@{domain}"]
        result["personas"].append(block)
    return result
