# Job Hunt Agent

Personal agent for **Garvit Khurana** — discover senior/founding PM roles, tailor resume + LinkedIn + email, batch-review, then run a tight follow-up cadence.

## Design (let it fly, with review)

This is **not** a LinkedIn auto-submit bot (those get accounts banned). It is a high-throughput prep + follow-up engine:

1. **Discover** roles from Greenhouse + Ashby public boards + YC company seeds  
2. **Score** against your profile (Senior/Founding PM, AI/fintech, NYC / Cali / US remote / London / Canada)  
3. **Generate** per-role: tailored resume, cover letter, LinkedIn note, personalized email, founder pitch  
4. **Review** a ranked queue (`hunt review`) — skim and batch-approve  
5. **Follow up** automatically on day **3 / 7 / 14** (`hunt followups`)

## Two tracks: core + adjacent

Your profile is a genuine PM/engineer hybrid, so the agent tracks two kinds of roles:

**Core** — Senior / Staff / Group PM, AI PM, Technical & Platform PM, Head/Director/VP Product, Founding PM, co-founder.

**Adjacent** — strong non-PM fits, surfaced only at companies above a quality bar (`filters.min_company_tier`):

| Family | Why it fits you |
|---|---|
| Forward Deployed / Applied AI Engineer | You build RAG systems *and* run client discovery — fastest-growing hybrid role in AI startups |
| Solutions Architect / Engineer | Mirrors translating client workflows into AI product + GTM enablement |
| AI Strategy / Transformation | You sit inside BlackRock's AI Accelerator driving enterprise adoption |
| Chief of Staff / BizOps / Strategy | Columbia MSBA + cross-functional platform launches |
| Fintech / Capital Markets Specialist | Private credit, fixed income, Aladdin depth is scarce |
| Growth Product / Growth Lead | Direct growth-product startup experience (PadSquad) |
| Technical Program Manager | You already run discovery→launch across teams |
| Founding Engineer | Viable if you want to stay hands-on technical |
| Data / Analytics Product Lead | Data governance platform + SQL/ETL/Tableau |

```bash
hunt suggest                   # adjacent roles + why each fits
hunt review --track core       # PM roles only
hunt review --track adjacent   # suggestions only
```

Adjacent roles score slightly below equivalent core roles so PM stays the priority. Turn them off with `filters.include_adjacent_roles: false`.

## Seniority realism (stretch filter)

Your band is **Product Lead / Engineer II**. The agent treats titles accordingly:

| Title band | Treatment |
|---|---|
| Senior / Lead / Founding PM | **Sweet spot** — full score |
| Untitled PM, AI Engineer, Strategist, Architect | In-band — strong score |
| **Staff / Principal** | **Stretch** — demoted (one band above you) |
| **Director / VP / Head / Chief** | **Stretch** — heavier penalty, usually dropped |
| Head of Product at early-stage | Exempt from penalty (founding-scope) |

So `Director of Product, Growth/AI` at Brex now scores **0.58 → dropped**, while `Senior/Staff PM, AI` at Brex scores **1.00**. Stretch roles surface only when everything else is exceptional.

Default daily targets in `config.yaml`: **~30 apps + ~40 outreach** packets (high leverage). Raise targets there if you want more volume — quality still beats 500 spray-and-pray.

## Quick start

```bash
cd ~/Projects/job-hunt-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

cp .env.example .env
# put NVIDIA_API_KEY in .env (free from https://build.nvidia.com)
# optional — works with template fallbacks via --no-llm

hunt init
# one-time: SMTP_USER + SMTP_PASS (Gmail app password) in .env for inbox scan

hunt daily          # inbox → discover → score → packets (skips already-applied)
hunt review         # finalize applying list
hunt suggest        # adjacent (non-PM) roles worth a look
hunt show <id>      # full packet
# after you send LinkedIn/email / submit portal:
hunt approve <id> --applied   # or --outreach
# or after skimming the whole queue:
hunt approve-all --outreach

# every day:
hunt followups          # see what's due
hunt followups --mark   # after you send them
hunt status
hunt applied            # companies already applied (from inbox)
```

### Ideal flow (scan first)

**Yes — Gmail first, then finalize the applying list.** That’s now the default:

1. **`hunt inbox`** (auto-runs inside `hunt daily`) — learn where you’ve already applied  
2. **Discover + score** — pull open roles  
3. **Packets** — only for companies not already applied  
4. **`hunt review`** — you finalize / apply / outreach  

Running packets before inbox wastes LLM calls on companies you’re done with. If credentials aren’t set yet, daily soft-skips the scan and continues.

```bash
# one-time: add SMTP_USER + SMTP_PASS to .env (Gmail app password)
hunt inbox --dry-run          # preview what it finds
hunt inbox                    # write + mark applied (or just hunt daily)
hunt applied                  # see the list
```

**Limits:** only catches apps that sent a confirmation email to that inbox. LinkedIn Easy Apply without email, or apply-from-another-address, won’t show up — mark those with `hunt approve <id> --applied`.

## Add more companies

Edit `config.yaml` → `sources.greenhouse_boards` / `ashby_boards` (company ATS slugs).  
Paste one-off LinkedIn/Wellfound roles:

```bash
hunt add-job --company "Acme" --title "Founding Product Manager" \
  --url "https://..." --location "New York, NY" \
  --description "..."
hunt packets
```

## Locations locked in

NYC · California / Bay Area · US remote · London · Canada (Toronto/Vancouver/etc.)

## Files

| Path | Purpose |
|------|---------|
| `data/profile.yaml` | Your positioning + pitch angles |
| `data/resume_master.md` | Master resume the agent tailors |
| `data/packets/` | Per-job markdown packets (copy/paste) |
| `data/resumes/` | Tailored resumes |
| `data/hunt.db` | Tracker + follow-up schedule |
| `config.yaml` | Boards, targets, filters, cadence |

## Who to reach out to — and how

For each role, `hunt contacts <id>` builds a target sheet. Priority:

| # | Persona | Channel | Why |
|---|---|---|---|
| 1 | Hiring manager (Head/VP Product, or Founder for founding roles; Eng lead for FDE) | LinkedIn + email | Decision-maker — reply skips ATS |
| 2 | Team peer / 2nd-degree connection | LinkedIn | Referral = highest conversion |
| 3 | Recruiter | Email / LinkedIn | Fast logistics, lower leverage |

**Finding them:** LinkedIn people-search URLs are generated for you (open and act — **never auto-message LinkedIn**, it bans accounts). Emails come from Apollo (if `APOLLO_API_KEY` set) or pattern-guessing (`first@`, `first.last@`) + optional Hunter pattern.

**Sending:**
- **Email — fully automated** via free Gmail SMTP app-password (or Resend). Dry-run by default; add `--send` to deliver. Resume PDF attaches automatically. Scheduling link (Zoom/Calendly) goes into every email once you set `SCHEDULING_LINK`.
- **LinkedIn — you send.** Agent drafts the note; you paste it. Safe and sustainable.

```bash
hunt contacts <id>                              # who + LinkedIn URLs + email guesses
hunt set-email <id> person@company.com          # lock in a verified address
hunt send <id> --to person@company.com --name Sam     # dry-run
hunt send <id> --to person@company.com --name Sam --send   # actually send
hunt send-batch --send                          # blast everything with a recorded email
hunt followups --mark                           # day 3/7/14 cadence
```

### Free email setup (Gmail SMTP)

1. Enable 2FA on Google → create an [App Password](https://myaccount.google.com/apppasswords)
2. Put into `.env`:

```
SMTP_USER=you@gmail.com
SMTP_PASS=xxxx-xxxx-xxxx-xxxx
FROM_EMAIL=Garvit Khurana <you@gmail.com>
SCHEDULING_LINK=https://calendly.com/you/15min   # when ready
APOLLO_API_KEY=...                               # optional, finds real emails
```

## Suggested daily ritual (15–25 min)

```bash
hunt daily                  # inbox first → discover → score → packets
hunt review --track core    # finalize applying list
hunt suggest
# for each top role:
hunt contacts <id>          # open LinkedIn URLs, find emails
hunt set-email <id> a@b.com
# send LinkedIn notes by hand (paste from hunt show)
hunt send-batch --send      # auto-email everything queued with an address
hunt approve <id> --applied # after you submit the portal
hunt followups --mark
```

That keeps volume high **and** follow-up rate near 100% on everything you touched.
