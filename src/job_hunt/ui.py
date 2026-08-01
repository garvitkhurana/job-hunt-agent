"""Local look+apply board — one HTML page over SQLite (no new deps)."""
from __future__ import annotations

import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import db
from .contacts import linkedin_people_search, target_personas
from .models import Job, JobSource, JobStatus

console = Console()
DEFAULT_PORT = 8765

LI_NOTE = (
    "Hi — Product Lead on BlackRock's AI Accelerator (RAG platform, +40% bond TTM). "
    "Curious about {title} at {company}. Open to a quick chat?"
)


def _job_from_row(r: dict) -> Job:
    return Job(
        id=r["id"],
        source=JobSource(r.get("source") or "other"),
        company=r["company"],
        title=r["title"],
        location=r.get("location") or "",
        url=r.get("url") or "",
        description=(r.get("description") or "")[:500],
    )


def linkedin_url_for_row(r: dict) -> str:
    try:
        job = _job_from_row(r)
        personas = target_personas(job)
        titles = personas[0].titles if personas else ["Head of Product", "VP Product"]
        return linkedin_people_search(r["company"], titles)
    except Exception:
        return linkedin_people_search(r["company"], ["Head of Product", "VP Product"])


def note_for_row(r: dict) -> str:
    return LI_NOTE.format(title=r.get("title") or "the role", company=r.get("company") or "your team")[
        :280
    ]


def snapshot() -> dict:
    db.init_db()
    stats = db.stats()
    review = db.queue_for_review(limit=25, one_per_company=True)
    for r in review:
        r["linkedin_url"] = linkedin_url_for_row(r)
        r["linkedin_note"] = note_for_row(r)
    applied_cos = db.list_applied_companies(limit=100)
    applied_jobs = db.list_jobs(status=JobStatus.APPLIED, limit=30, order="updated_at DESC")
    suggestions = db.suggestions(limit=10, min_score=0.66)
    return {
        "stats": stats,
        "review": review,
        "suggestions": suggestions,
        "applied_companies": applied_cos,
        "applied_jobs": applied_jobs,
        "totals": {
            "applied_companies": len(applied_cos),
            "review_companies": len(review),
            "scored": stats.get("scored", 0),
            "applied_jobs": stats.get("applied", 0),
            "suggestions": len(suggestions),
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
    review_rows = "".join(
        f"<tr>"
        f"<td class='num'>{r.get('score', 0):.2f}</td>"
        f"<td>{_esc(r.get('track') or 'core')}</td>"
        f"<td><strong>{_esc(r['company'])}</strong></td>"
        f"<td>{_esc(r['title'][:52])}</td>"
        f"<td>{_esc((r.get('location') or '')[:24])}</td>"
        f"<td class='dim'>{'+' + str(r['sibling_roles']) if r.get('sibling_roles') else ''}</td>"
        f"<td class='actions'>"
        f"<a href='{_esc(r.get('url'))}' target='_blank' rel='noopener'>Apply</a> · "
        f"<a href='{_esc(r.get('linkedin_url'))}' target='_blank' rel='noopener'>LinkedIn</a><br/>"
        f"<a class='btn' href='/action?op=applied&id={_esc(r['id'])}'>mark applied</a> "
        f"<a class='btn ghost' href='/action?op=skip&id={_esc(r['id'])}'>skip</a>"
        f"</td></tr>"
        f"<tr class='note'><td colspan='7'><span class='dim'>Note:</span> {_esc(r.get('linkedin_note'))}</td></tr>"
        for r in data["review"]
    ) or "<tr><td colspan='7' class='dim'>Empty — run hunt daily</td></tr>"

    applied_cos = "".join(
        f"<tr><td>{_esc(r['company'])}</td><td class='dim'>{_esc(r.get('source'))}</td></tr>"
        for r in data["applied_companies"]
    ) or "<tr><td colspan='2' class='dim'>None yet</td></tr>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Hunt board</title>
<style>
  :root {{
    --bg: #f6f3ee; --ink: #1a1a1a; --muted: #6b6560; --line: #ddd6cb;
    --card: #fffdf9; --accent: #0b5fff; --ok: #1b7a4e;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font-family: "IBM Plex Sans", "Segoe UI", sans-serif; background: var(--bg); color: var(--ink); }}
  header {{ padding: 1.1rem 1.4rem; border-bottom: 1px solid var(--line); background: var(--card);
    display: flex; justify-content: space-between; align-items: baseline; flex-wrap: wrap; gap: .75rem; }}
  h1 {{ margin: 0; font-size: 1.3rem; font-family: "IBM Plex Serif", Georgia, serif; }}
  .meta {{ color: var(--muted); font-size: .9rem; }}
  main {{ padding: 1.2rem 1.4rem 3rem; max-width: 1100px; margin: 0 auto; }}
  .kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr)); gap: .7rem; margin-bottom: 1.2rem; }}
  .kpi {{ background: var(--card); border: 1px solid var(--line); padding: .75rem .9rem; }}
  .kpi .n {{ font-size: 1.6rem; font-weight: 600; }}
  .kpi .l {{ color: var(--muted); font-size: .75rem; text-transform: uppercase; letter-spacing: .04em; }}
  h2 {{ font-size: 1rem; margin: 0 0 .55rem; font-family: "IBM Plex Serif", Georgia, serif; }}
  table {{ width: 100%; border-collapse: collapse; background: var(--card); border: 1px solid var(--line); font-size: .9rem; }}
  th, td {{ text-align: left; padding: .4rem .55rem; border-bottom: 1px solid var(--line); vertical-align: top; }}
  th {{ font-size: .72rem; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .dim {{ color: var(--muted); }}
  a {{ color: var(--accent); }}
  .btn {{ display: inline-block; padding: .12rem .4rem; border: 1px solid var(--ok); color: var(--ok);
    text-decoration: none; font-size: .75rem; margin-right: .2rem; }}
  .btn.ghost {{ border-color: var(--muted); color: var(--muted); }}
  tr.note td {{ font-size: .82rem; background: #faf8f4; border-bottom: 2px solid var(--line); }}
  .grid2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-top: 1.4rem; }}
  @media (max-width: 800px) {{ .grid2 {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<header>
  <h1>Hunt board</h1>
  <div class="meta">Look → apply → mark · <a href="/">refresh</a></div>
</header>
<main>
  <div class="kpis">
    <div class="kpi"><div class="n">{t['review_companies']}</div><div class="l">To review</div></div>
    <div class="kpi"><div class="n">{t['applied_companies']}</div><div class="l">Applied cos</div></div>
    <div class="kpi"><div class="n">{t['scored']}</div><div class="l">Scored roles</div></div>
    <div class="kpi"><div class="n">{t['suggestions']}</div><div class="l">Adjacent shown</div></div>
  </div>

  <section>
    <h2>Review (1 best role / company)</h2>
    <table>
      <thead><tr><th>Score</th><th>Track</th><th>Company</th><th>Title</th><th>Location</th><th>+</th><th></th></tr></thead>
      <tbody>{review_rows}</tbody>
    </table>
  </section>

  <div class="grid2">
    <section>
      <h2>Already applied / out (inbox)</h2>
      <table>
        <thead><tr><th>Company</th><th>Source</th></tr></thead>
        <tbody>{applied_cos}</tbody>
      </table>
    </section>
    <section>
      <h2>Pipeline</h2>
      <table>
        <thead><tr><th>Status</th><th>Count</th></tr></thead>
        <tbody>{"".join(f"<tr><td>{_esc(k)}</td><td class='num'>{v}</td></tr>" for k, v in sorted(data['stats'].items(), key=lambda x: -x[1]))}</tbody>
      </table>
    </section>
  </div>
</main>
</body>
</html>"""


class BoardHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
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
                job = db.get_job(jid)
                db.mark_sent(jid, "application", "portal", "", 0, cfg.followup.cadence_days)
                if job:
                    db.remember_applied_company(job["company"], source="manual", title=job.get("title") or "")
                    db.park_packets_at_applied_companies()
            elif jid and op == "skip":
                db.set_status(jid, JobStatus.SKIPPED)
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            return

        if parsed.path in ("/", "/index.html", "/board"):
            body = render_html(snapshot()).encode("utf-8")
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
    data = snapshot()
    t = data["totals"]
    console.print(
        Panel.fit(
            f"[bold]Review[/] {t['review_companies']} cos · "
            f"[bold]Applied cos[/] {t['applied_companies']} · "
            f"[bold]Scored[/] {t['scored']}",
            title="Hunt board",
        )
    )
    table = Table(title="Review (1 / company)")
    table.add_column("Score", justify="right")
    table.add_column("Track")
    table.add_column("Company")
    table.add_column("Title")
    table.add_column("+", justify="right", style="dim")
    for r in data["review"]:
        sib = int(r.get("sibling_roles") or 0)
        table.add_row(
            f"{r.get('score', 0):.2f}",
            r.get("track") or "core",
            r["company"][:18],
            r["title"][:40],
            f"+{sib}" if sib else "",
        )
    console.print(table)
    cos = Table(title=f"Already applied ({len(data['applied_companies'])})")
    cos.add_column("Company")
    cos.add_column("Source")
    for r in data["applied_companies"][:25]:
        cos.add_row(r["company"], r.get("source") or "")
    console.print(cos)
    console.print("\nBrowser: [bold]hunt ui[/]")
