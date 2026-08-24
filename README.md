# Job Hunt Agent

Personal **look → apply → measure** loop for senior / founding PM (+ strong adjacent hybrid) roles.

Pulls public Greenhouse / Ashby boards, scores matches, blocks companies you’ve already applied to or been rejected from (via Gmail), and surfaces **one best role per company** on a local board. Metrics tell you whether targeting is working.

It is **not** a LinkedIn Easy Apply / auto-DM / form-autofill bot.

## Setup

```bash
cd job-hunt-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env   # or: hunt init
# Edit .env — SMTP_USER + SMTP_PASS (Gmail app password) for inbox scan
```

LLM keys (`ANTHROPIC_API_KEY`, etc.) are **only** for optional `hunt prep` — daily does not need them.

## Daily ritual (~15 min)

```bash
source .venv/bin/activate

hunt daily              # inbox → discover → score
hunt ui                 # or: hunt board
# apply / skip on the board → mark applied
hunt metrics            # weekly: precision, applies, outcomes
hunt metrics --baseline # once: write output/metrics/baseline.json
```

On the board, per company:

1. Open **Apply** (you submit on the ATS — CAPTCHA stays human)
2. Open **LinkedIn** (search URL) — paste the note yourself
3. **mark applied** or **skip** (logs metrics events)
4. Optional: **prep** for on-demand materials (experimental)

When you hear back:

```bash
hunt outcome <job_id> interview   # or: rejected | ghost
```

## Commands

| Command | Purpose |
|---------|---------|
| `hunt daily` | Inbox → discover → score |
| `hunt ui` / `hunt board` | Local web / terminal board |
| `hunt metrics` | Funnel KPIs (precision, applies/week, outcomes) |
| `hunt outcome <id> …` | Log interview / rejected / ghost |
| `hunt prep <id>` | On-demand materials (experimental; not daily) |
| `hunt inbox` | Re-scan Gmail |
| `hunt applied` | Blocked companies |
| `hunt review` | Terminal queue (1 role / company) |
| `hunt skip` / `hunt approve --applied` | Mark from CLI |
| `hunt add-job …` | Manual role |
| `hunt legacy …` | Soft-deprecated packets / send / contacts |

## What to optimize (in order)

1. **Review precision** — applies / (applies + skips)
2. **Applies / week** — throughput without spray
3. **Interview rate / apply** — after you log outcomes

Tune score knobs in `config.yaml` (`min_score`, `stretch_penalty`, `visa_priority_boost`, …) only after you have a baseline.

## Phase 2 habit (no new features)

Use the ritual for 1–2 weeks of real applies. Log outcomes. Glance `hunt metrics` weekly. **Do not** build form autofill or essay generators until this is habit. See [docs/NEXT.md](docs/NEXT.md).

## Targets & filters

Configured in `config.yaml`:

- Senior / Lead / Founding PM sweet spot (Staff / Director / VP demoted)
- Visa boost: London / UK / Canada; also NYC, Cali, US remote
- One best role per company; `+N` siblings
- Adjacent roles only at strong companies

## Project layout

| Path | Purpose |
|------|---------|
| `config.yaml` | Boards, filters, score knobs |
| `data/profile.yaml` | Positioning |
| `data/hunt.db` | Jobs, applied cos, events (gitignored) |
| `output/metrics/` | Baseline / snapshots |
| `src/job_hunt/` | CLI, pipeline, inbox, scoring, UI, metrics |

## Tests

```bash
make test   # or: pytest -q
```

## Design constraints

- No LinkedIn auto-DM / Easy Apply / Playwright submit
- Form assist (defaults + narrative drafts) is **later** — see [docs/NEXT.md](docs/NEXT.md)
- CAPTCHA, EEO invent, consent auto-yes: never automate
