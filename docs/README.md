# Docs

Start with the root [README](../README.md) — setup, daily ritual, metrics, and commands.

Parked / next phases: [NEXT.md](NEXT.md).

## Config cheatsheet

| File | Edit when… |
|------|------------|
| `config.yaml` | Boards, score knobs (`stretch_penalty`, `visa_priority_boost`, …), geos |
| `data/profile.yaml` | Positioning / target roles |
| `.env` | SMTP/IMAP for inbox; `ANTHROPIC_API_KEY` only for optional `hunt prep` |

## Local data (gitignored)

| Path | Purpose |
|------|---------|
| `data/hunt.db` | Jobs, applied companies, metrics events |
| `output/metrics/` | Baseline + snapshots from `hunt metrics --baseline` |
| `data/packets/` | Legacy cover packets |
| `output/prep/` | On-demand prep artifacts |
