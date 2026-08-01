# Job Hunt Agent

Personal look → apply agent for senior / founding PM (+ strong adjacent hybrid) roles.

Pulls public Greenhouse / Ashby boards, scores matches, blocks companies you’ve already applied to or been rejected from (via Gmail), and surfaces **one best role per company** on a local board.

## What it does

1. **Discover** roles from configured ATS boards (+ manual adds)
2. **Score** against your profile (`data/profile.yaml` + `config.yaml`)
3. **Inbox scan** — thank-you / rejection mail → block those companies
4. **Board** — apply link + LinkedIn hiring-manager search + mark applied

It is **not** a LinkedIn Easy Apply / auto-DM bot.

## Setup

```bash
cd job-hunt-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env   # or: hunt init
# Edit .env — at minimum SMTP_USER + SMTP_PASS (Gmail app password) for inbox scan
```

Optional LLM keys in `.env` are only needed for legacy `hunt packets` / email generation — the daily look+apply loop does **not** require them. See `.env.example`.

## Daily ritual

```bash
source .venv/bin/activate

hunt daily          # inbox → discover → score
hunt ui             # local board (or: hunt board)
```

On the board, per company:

1. Open **Apply**
2. Open **LinkedIn** (hiring-manager search) — paste your note yourself
3. Click **mark applied** when done

## Commands

| Command | Purpose |
|---------|---------|
| `hunt daily` | Full look+apply loop |
| `hunt ui` / `hunt board` | Local web / terminal board |
| `hunt inbox` | Re-scan Gmail (applied + rejected) |
| `hunt applied` | List blocked companies |
| `hunt review` | Terminal queue (1 role / company) |
| `hunt review --all-roles` | Every role (no company dedupe) |
| `hunt suggest` | Adjacent-only list |
| `hunt approve <id> --applied` | Mark applied |
| `hunt skip <id>` | Skip a role |
| `hunt add-job --company … --title … --url …` | Manual role |
| `hunt status` | Pipeline counts |
| `hunt packets` / `hunt send` | Optional legacy cover packets / SMTP |

## Targets & filters

Configured in `config.yaml`:

- **Seniority sweet spot:** Senior / Lead / Founding PM (Staff / Director / VP usually demoted)
- **Locations:** visa-friendly boost for London / UK / Canada; also NYC, California, US remote
- **One role per company** in review — highest score wins; `+N` = sibling roles at the same company
- **Adjacent** (FDE, Applied AI, Solutions, …) kept only at strong companies

Add boards under `sources.greenhouse_boards` / `sources.ashby_boards` (public ATS slugs).

## Project layout

| Path | Purpose |
|------|---------|
| `config.yaml` | Boards, targets, score thresholds |
| `data/profile.yaml` | Positioning |
| `data/resume_master.md` | Master resume (optional packets) |
| `data/hunt.db` | Tracker + applied companies (local, gitignored) |
| `src/job_hunt/` | CLI, pipeline, inbox, scoring, UI |
| `.env` | Secrets — never commit |

## Tests

```bash
pip install -e ".[dev]"
pytest
```

## Design constraints

- No LinkedIn auto-DM / Easy Apply automation (ban risk)
- Email cover packets / SMTP blast are optional legacy — off the happy path
- Free LLM keys (NVIDIA NIM / OpenRouter) only for optional packet generation — see `.env.example`
