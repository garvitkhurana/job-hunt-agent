# Docs

Start with the root [README](../README.md) — setup, daily ritual, and command reference.

## Config cheatsheet

| File | Edit when… |
|------|------------|
| `config.yaml` | Adding Greenhouse/Ashby boards, changing score thresholds, location prefs |
| `data/profile.yaml` | Updating positioning / target roles |
| `.env` | SMTP/IMAP for inbox scan; optional LLM keys for packets |

## Local data (gitignored)

| Path | Purpose |
|------|---------|
| `data/hunt.db` | Job tracker + applied companies |
| `data/packets/` | Generated cover packets (legacy path) |
| `output/` | Misc exports |
