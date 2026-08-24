# Job Hunt Agent

Personal **look → apply → mark** loop for **senior / founding / AI PM** roles.

Pulls public Greenhouse / Ashby boards, scores **core PM titles only**, skips companies you’ve already applied to (Gmail inbox), and shows **one best role per company**. You apply on the ATS; the tool tracks applied/skip.

It is **not** an auto-apply, LinkedIn DM, or form-fill bot.

## Setup

```bash
cd job-hunt-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env   # or: hunt init
# Edit .env — SMTP_USER + SMTP_PASS (Gmail app password) for inbox scan
```

## Daily ritual

```bash
source .venv/bin/activate

hunt daily    # inbox → discover (skip applied cos) → rescore
hunt ui       # board: Apply → mark applied / skip
hunt metrics  # optional weekly
```

Board columns: **score · company · title · location · Apply · mark applied · skip**.

## Commands

| Command | Purpose |
|---------|---------|
| `hunt daily` | Inbox → discover → rescore |
| `hunt rescore` | Rewrite scores after filter changes |
| `hunt ui` / `hunt board` | Local web / terminal board |
| `hunt metrics` | Funnel KPIs |
| `hunt outcome <id> interview\|rejected\|ghost` | Log result |
| `hunt inbox` | Re-scan Gmail |
| `hunt applied` | Blocked companies |
| `hunt review` | Terminal queue (core PM) |
| `hunt skip` / `hunt approve --applied` | Mark from CLI |
| `hunt prep <id>` | Optional materials (not required) |
| `hunt legacy …` | Deprecated packets / send |

## How it stays sane

- **Core PM only** (`include_adjacent_roles: false`) — no sales/ops/FDE noise on the board
- **Skip applied boards** when discovering so the queue isn’t starved
- **Hard-exclude** junk titles (sales enablement, `(explore)`, campus `2026 - Product`, …)
- **Blocked geos** (Japan/APJ/India/…) zero out before “remote ok”
- Empty board means no open core PM at unapplied cos — not “run daily” theater

Tune boards and `min_score` in `config.yaml`.

## Tests

```bash
make test
```

## Docs

- [docs/NEXT.md](docs/NEXT.md) — parked form-assist ideas (do not build until the apply habit works)
