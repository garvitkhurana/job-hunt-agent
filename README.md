# Job Hunt Agent

Personal agent for **Garvit Khurana** — discover senior/founding PM (+ adjacent hybrid) roles, block companies you’ve already applied to / been rejected from, and review **one best role per company**.

## Look → apply (primary ritual)

```bash
cd ~/Projects/job-hunt-agent
source .venv/bin/activate

# one-time: SMTP_USER + SMTP_PASS (Gmail app password) in .env
hunt daily          # inbox → discover → score (no LLM packets)
hunt ui             # local board: apply link + LinkedIn search + mark applied
# or: hunt board
```

On the board for each company:
1. Open **Apply**
2. Open **LinkedIn** (hiring-manager search) — paste the short note yourself
3. Click **mark applied** when done

Inbox scan (inside `daily`) detects thank-you + rejection emails and blocks those companies.

## Design constraints

- **Not** a LinkedIn auto-DM / Easy Apply bot (ban risk)
- **One role per company** in review (core or adjacent — highest score wins; `+N` = siblings)
- Adjacent roles (FDE, Applied AI, Solutions, …) stay on, only at strong companies
- Email cover packets / SMTP blast are optional legacy (`hunt packets`, `hunt send`) — off the happy path

## Seniority

Sweet spot: Senior / Lead / Founding PM. Staff / Director / VP are stretch and usually demoted.

## Locations

Visa-friendly boost: London / UK / Canada. Also NYC, Cali, US remote.

## Useful commands

```bash
hunt inbox              # re-scan Gmail (applied + rejected)
hunt applied            # companies blocked
hunt review             # terminal queue (1 / company)
hunt review --all-roles # every role (no company dedupe)
hunt suggest            # adjacent-only list
hunt approve <id> --applied
hunt skip <id>
hunt status
```

## Add companies

Edit `config.yaml` → `sources.greenhouse_boards` / `ashby_boards` (public ATS slugs). Names without a public board are listed as comments.

```bash
hunt add-job --company "Acme" --title "Founding Product Manager" \
  --url "https://..." --location "New York, NY"
```

## Tests

```bash
pip install -e ".[dev]"
pytest
```

## Files

| Path | Purpose |
|------|---------|
| `data/profile.yaml` | Positioning |
| `data/resume_master.md` | Master resume (optional packets) |
| `data/hunt.db` | Tracker + applied companies |
| `config.yaml` | Boards, targets, filters |

## Free LLM keys (optional, for `hunt packets` only)

Daily look+apply does **not** need an LLM. For optional packet generation:

- NVIDIA NIM — https://build.nvidia.com  
- OpenRouter — https://openrouter.ai/keys  

Put keys in `.env` (never commit). See `.env.example`.
