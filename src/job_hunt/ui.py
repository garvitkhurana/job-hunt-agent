"""Local status board — one HTML page over SQLite (no new deps)."""
from __future__ import annotations

import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from rich.console import Console

from . import db
from .models import JobStatus

console = Console()
DEFAULT_PORT = 8765


def snapshot() -> dict:
    db.init_db()
    stats = db.stats()
    applied_jobs = [
        r
        for r in db.list_jobs(status=JobStatus.APPLIED, limit=50, order="updated_at DESC")
    ]
    # also outreach / followup as "in flight"
    in_flight = []
    for st in (JobStatus.OUTREACH_SENT, JobStatus.FOLLOWUP_SENT, JobStatus.FOLLOWUP_DUE, JobStatus.INTERVIEW):
        in_flight.extend(db.list_jobs(status=st, limit=50, order="updated_at DESC"))
    review = db.queue_for_review(limit=20, one_per_company=True)
    suggestions = db.suggestions(limit=12, min_score=0.66)
    applied_cos = db.list_applied_companies(limit=100)
    due = db.due_followups(limit=50)
    return {
        "stats": stats,
        "applied_jobs": applied_jobs,
        "in_flight": in_flight,
        "review": review,
        "suggestions": suggestions,
        "applied_companies": applied_cos,
        "followups_due": due,
        "totals": {
            "applied_jobs": stats.get("applied", 0),
            "applied_companies": len(applied_cos),
            "review_companies": len(review),
            "packet_ready": stats.get("packet_ready", 0),
            "followups_due": len(due),
            "suggestions": len(suggestions),
            "skipped": stats.get("skipped", 0),
        },
    }


def _esc(s: object) -> str:
    return (
        str(s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_html(data: dict) -> str:
    t = data["totals"]
    stats_rows = "".join(
        f"<tr><td>{_esc(k)}</td><td class='num'>{v}</td></tr>"
        for k, v in sorted(data["stats"].items(), key=lambda x: -x[1])
    )
    applied_cos = "".join(
        f"<tr><td>{_esc(r['company'])}</td><td>{_esc((r.get('title') or '')[:50])}</td>"
        f"<td class='dim'>{_esc(r.get('source'))}</td></tr>"
        for r in data["applied_companies"]
    )
    applied_jobs = "".join(
        f"<tr><td>{_esc(r['company'])}</td><td>{_esc(r['title'][:55])}</td>"
        f"<td>{_esc((r.get('location') or '')[:28])}</td>"
        f"<td><a href='{_esc(r.get('url'))}' target='_blank' rel='noopener'>open</a></td></tr>"
        for r in data["applied_jobs"]
    )
    review_rows = "".join(
        f"<tr>"
        f"<td><code>{_esc(r['id'])}</code></td>"
        f"<td class='num'>{r.get('score', 0):.2f}</td>"
        f"<td>{_esc(r.get('track'))}</td>"
        f"<td><strong>{_esc(r['company'])}</strong></td>"
        f"<td>{_esc(r['title'][:48])}</td>"
        f"<td>{_esc((r.get('location') or '')[:22])}</td>"
        f"<td class='dim'>{'+' + str(r['sibling_roles']) if r.get('sibling_roles') else ''}</td>"
        f"<td class='actions'>"
        f"<a class='btn' href='/action?op=applied&id={_esc(r['id'])}'>applied</a> "
        f"<a class='btn ghost' href='/action?op=skip&id={_esc(r['id'])}'>skip</a> "
        f"<a href='{_esc(r.get('url'))}' target='_blank' rel='noopener'>apply↗</a>"
        f"</td></tr>"
        for r in data["review"]
    )
    sug_rows = "".join(
        f"<tr><td class='num'>{r.get('score', 0):.2f}</td>"
        f"<td>{_esc(r['company'])}</td><td>{_esc(r['title'][:50])}</td>"
        f"<td>{_esc((r.get('location') or '')[:22])}</td></tr>"
        for r in data["suggestions"]
    )
    fu_rows = "".join(
        f"<tr><td>{_esc(r['company'])}</td><td>{_esc(r['title'][:45])}</td>"
        f"<td class='num'>{r.get('followup_count', 0)}</td></tr>"
        for r in data["followups_due"]
    ) or "<tr><td colspan='3' class='dim'>None due</td></tr>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Hunt board</title>
<style>
  :root {{
    --bg: #f6f3ee;
    --ink: #1a1a1a;
    --muted: #6b6560;
    --line: #ddd6cb;
    --card: #fffdf9;
    --accent: #0b5fff;
    --ok: #1b7a4e;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
    background: var(--bg); color: var(--ink); line-height: 1.4;
  }}
  header {{
    padding: 1.25rem 1.5rem; border-bottom: 1px solid var(--line);
    background: var(--card); display: flex; justify-content: space-between; align-items: baseline; gap: 1rem; flex-wrap: wrap;
  }}
  header h1 {{ margin: 0; font-size: 1.35rem; letter-spacing: -0.02em; font-family: "IBM Plex Serif", Georgia, serif; }}
  header .meta {{ color: var(--muted); font-size: 0.9rem; }}
  main {{ padding: 1.25rem 1.5rem 3rem; max-width: 1200px; margin: 0 auto; }}
  .kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 0.75rem; margin-bottom: 1.5rem; }}
  .kpi {{ background: var(--card); border: 1px solid var(--line); padding: 0.85rem 1rem; }}
  .kpi .n {{ font-size: 1.75rem; font-weight: 600; letter-spacing: -0.03em; }}
  .kpi .l {{ color: var(--muted); font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.04em; }}
  section {{ margin-bottom: 1.75rem; }}
  h2 {{ font-size: 1rem; margin: 0 0 0.6rem; font-family: "IBM Plex Serif", Georgia, serif; }}
  table {{ width: 100%; border-collapse: collapse; background: var(--card); border: 1px solid var(--line); font-size: 0.92rem; }}
  th, td {{ text-align: left; padding: 0.45rem 0.6rem; border-bottom: 1px solid var(--line); vertical-align: top; }}
  th {{ font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); font-weight: 600; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .dim {{ color: var(--muted); }}
  code {{ font-size: 0.78rem; }}
  a {{ color: var(--accent); }}
  .btn {{
    display: inline-block; padding: 0.15rem 0.45rem; border: 1px solid var(--ok);
    color: var(--ok); text-decoration: none; font-size: 0.78rem; margin-right: 0.25rem;
  }}
  .btn.ghost {{ border-color: var(--muted); color: var(--muted); }}
  .actions {{ white-space: nowrap; }}
  .grid2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1.25rem; }}
  @media (max-width: 800px) {{ .grid2 {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<header>
  <h1>Hunt board</h1>
  <div class="meta">Local · refresh to reload · <a href="/">refresh</a></div>
</header>
<main>
  <div class="kpis">
    <div class="kpi"><div class="n">{t['applied_jobs']}</div><div class="l">Applied jobs</div></div>
    <div class="kpi"><div class="n">{t['applied_companies']}</div><div class="l">Cos (inbox)</div></div>
    <div class="kpi"><div class="n">{t['review_companies']}</div><div class="l">Review now</div></div>
    <div class="kpi"><div class="n">{t['packet_ready']}</div><div class="l">Packets</div></div>
    <div class="kpi"><div class="n">{t['followups_due']}</div><div class="l">Follow-ups due</div></div>
    <div class="kpi"><div class="n">{t['suggestions']}</div><div class="l">Adjacent</div></div>
  </div>

  <section>
    <h2>Review queue (1 best role / company)</h2>
    <table>
      <thead><tr><th>ID</th><th>Score</th><th>Track</th><th>Company</th><th>Title</th><th>Location</th><th>+</th><th></th></tr></thead>
      <tbody>{review_rows or "<tr><td colspan='8' class='dim'>Empty — run hunt daily</td></tr>"}</tbody>
    </table>
  </section>

  <div class="grid2">
    <section>
      <h2>Already applied companies (inbox)</h2>
      <table>
        <thead><tr><th>Company</th><th>Role hint</th><th>Source</th></tr></thead>
        <tbody>{applied_cos or "<tr><td colspan='3' class='dim'>None</td></tr>"}</tbody>
      </table>
    </section>
    <section>
      <h2>Applied jobs (tracker)</h2>
      <table>
        <thead><tr><th>Company</th><th>Title</th><th>Location</th><th></th></tr></thead>
        <tbody>{applied_jobs or "<tr><td colspan='4' class='dim'>Mark with Applied on review rows</td></tr>"}</tbody>
      </table>
    </section>
  </div>

  <div class="grid2">
    <section>
      <h2>Adjacent suggestions</h2>
      <table>
        <thead><tr><th>Score</th><th>Company</th><th>Title</th><th>Location</th></tr></thead>
        <tbody>{sug_rows or "<tr><td colspan='4' class='dim'>None</td></tr>"}</tbody>
      </table>
    </section>
    <section>
      <h2>Follow-ups due</h2>
      <table>
        <thead><tr><th>Company</th><th>Title</th><th>#</th></tr></thead>
        <tbody>{fu_rows}</tbody>
      </table>
      <h2 style="margin-top:1.25rem">Pipeline counts</h2>
      <table>
        <thead><tr><th>Status</th><th>Count</th></tr></thead>
        <tbody>{stats_rows}</tbody>
      </table>
    </section>
  </div>
</main>
</body>
</html>"""


class BoardHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # quieter
        console.print(f"[dim]ui[/] {args[0] if args else fmt}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/action":
            qs = parse_qs(parsed.query)
            op = (qs.get("op") or [""])[0]
            jid = (qs.get("id") or [""])[0]
            db.init_db()
            if jid and op == "applied":
                from .config import load_config

                cfg = load_config()
                packet = db.get_packet(jid) or {}
                db.mark_sent(
                    jid,
                    "application",
                    "portal",
                    packet.get("cover_letter") or "",
                    0,
                    cfg.followup.cadence_days,
                )
                job = db.get_job(jid)
                if job:
                    db.remember_applied_company(job["company"], source="manual", title=job.get("title") or "")
            elif jid and op == "skip":
                db.set_status(jid, JobStatus.SKIPPED)
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            return

        if parsed.path in ("/", "/index.html", "/board"):
            html = render_html(snapshot())
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/api/snapshot":
            body = json.dumps(snapshot(), default=str).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(404)
        self.end_headers()


def serve(port: int = DEFAULT_PORT, open_browser: bool = True) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), BoardHandler)
    url = f"http://127.0.0.1:{port}/"
    console.print(f"[green]Hunt board[/] → {url}")
    console.print("[dim]Ctrl+C to stop[/]")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\nStopped.")
    finally:
        server.server_close()


def print_board() -> None:
    """Rich terminal one-pager."""
    from rich.panel import Panel
    from rich.table import Table

    data = snapshot()
    t = data["totals"]
    console.print(
        Panel.fit(
            f"[bold]Applied jobs[/] {t['applied_jobs']}   "
            f"[bold]Inbox cos[/] {t['applied_companies']}   "
            f"[bold]Review[/] {t['review_companies']} cos   "
            f"[bold]Follow-ups due[/] {t['followups_due']}",
            title="Hunt board",
        )
    )

    table = Table(title="Review (1 / company)")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Score", justify="right")
    table.add_column("Company")
    table.add_column("Title")
    table.add_column("+", justify="right", style="dim")
    for r in data["review"]:
        sib = int(r.get("sibling_roles") or 0)
        table.add_row(
            r["id"],
            f"{r.get('score', 0):.2f}",
            r["company"][:18],
            r["title"][:40],
            f"+{sib}" if sib else "",
        )
    console.print(table)

    cos = Table(title=f"Already applied companies ({len(data['applied_companies'])})")
    cos.add_column("Company")
    cos.add_column("Source")
    for r in data["applied_companies"][:20]:
        cos.add_row(r["company"], r.get("source") or "")
    console.print(cos)

    if data["applied_jobs"]:
        aj = Table(title="Applied jobs")
        aj.add_column("Company")
        aj.add_column("Title")
        for r in data["applied_jobs"]:
            aj.add_row(r["company"], r["title"][:50])
        console.print(aj)

    console.print("\nBrowser UI: [bold]hunt ui[/]")
