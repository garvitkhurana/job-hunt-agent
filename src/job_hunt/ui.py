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
from .models import JobStatus

console = Console()
DEFAULT_PORT = 8765


def snapshot() -> dict:
    db.init_db()
    stats = db.stats()
    # Core PM only — one best role per unapplied company
    review = db.queue_for_review(limit=25, track="core", one_per_company=True)
    applied_cos = db.list_applied_companies(limit=100)
    from .metrics import compute_metrics

    m = compute_metrics(7)
    f = m["funnel"]
    return {
        "stats": stats,
        "review": review,
        "applied_companies": applied_cos,
        "metrics": m,
        "totals": {
            "applied_companies": len(applied_cos),
            "review_companies": len(review),
            "scored": stats.get("scored", 0),
            "applied_jobs": stats.get("applied", 0),
            "precision_7d": f.get("review_precision"),
            "applies_7d": f.get("applies", 0),
            "skips_7d": f.get("skips", 0),
            "median_hours": f.get("median_hours_discover_to_applied"),
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
    prec = t.get("precision_7d")
    prec_s = "—" if prec is None else f"{prec:.0%}"
    med = t.get("median_hours")
    med_s = "—" if med is None else f"{med}h"
    review_rows = "".join(
        f"<tr>"
        f"<td class='num'>{r.get('score', 0):.2f}</td>"
        f"<td><strong>{_esc(r['company'])}</strong></td>"
        f"<td>{_esc(r['title'][:64])}</td>"
        f"<td>{_esc((r.get('location') or '')[:36])}</td>"
        f"<td class='dim'>{'+' + str(r['sibling_roles']) if r.get('sibling_roles') else ''}</td>"
        f"<td class='actions'>"
        f"<a href='{_esc(r.get('url'))}' target='_blank' rel='noopener'>Apply</a> · "
        f"<a class='btn' href='/action?op=applied&id={_esc(r['id'])}'>mark applied</a> "
        f"<a class='btn ghost' href='/action?op=skip&id={_esc(r['id'])}'>skip</a>"
        f"</td></tr>"
        for r in data["review"]
    ) or (
        "<tr><td colspan='6' class='dim'>"
        "No core PM roles at unapplied companies. Run <code>hunt daily</code> or add boards."
        "</td></tr>"
    )

    applied_cos = "".join(
        f"<tr><td>{_esc(r['company'])}</td><td class='dim'>{_esc(r.get('source'))}</td></tr>"
        for r in data["applied_companies"]
    ) or "<tr><td colspan='2' class='dim'>None yet</td></tr>"

    live_stats = {
        k: v
        for k, v in sorted(data["stats"].items(), key=lambda x: -x[1])
        if k in ("scored", "applied", "skipped", "rejected", "discovered")
    }

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
  .kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(100px, 1fr)); gap: .7rem; margin-bottom: 1.2rem; }}
  .kpi {{ background: var(--card); border: 1px solid var(--line); padding: .75rem .9rem; }}
  .kpi .n {{ font-size: 1.45rem; font-weight: 600; }}
  .kpi .l {{ color: var(--muted); font-size: .72rem; text-transform: uppercase; letter-spacing: .04em; }}
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
  .grid2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-top: 1.4rem; }}
  @media (max-width: 800px) {{ .grid2 {{ grid-template-columns: 1fr; }} }}
  code {{ font-size: .85em; }}
</style>
</head>
<body>
<header>
  <h1>Hunt board</h1>
  <div class="meta">Look → apply → mark · core PM only · <a href="/">reload</a> · <a href="/refresh">re-run daily</a></div>
</header>
<main>
  <div class="kpis">
    <div class="kpi"><div class="n">{t['review_companies']}</div><div class="l">To review</div></div>
    <div class="kpi"><div class="n">{t['applied_companies']}</div><div class="l">Applied cos</div></div>
    <div class="kpi"><div class="n">{t.get('applies_7d', 0)}</div><div class="l">Applies 7d</div></div>
    <div class="kpi"><div class="n">{prec_s}</div><div class="l">Precision 7d</div></div>
    <div class="kpi"><div class="n">{med_s}</div><div class="l">Med hrs→apply</div></div>
  </div>

  <section>
    <h2>Review — 1 best core PM / company</h2>
    <table>
      <thead><tr><th>Score</th><th>Company</th><th>Title</th><th>Location</th><th>+</th><th></th></tr></thead>
      <tbody>{review_rows}</tbody>
    </table>
  </section>

  <div class="grid2">
    <section>
      <h2>Already applied / out</h2>
      <table>
        <thead><tr><th>Company</th><th>Source</th></tr></thead>
        <tbody>{applied_cos}</tbody>
      </table>
    </section>
    <section>
      <h2>Pipeline</h2>
      <table>
        <thead><tr><th>Status</th><th>Count</th></tr></thead>
        <tbody>{"".join(f"<tr><td>{_esc(k)}</td><td class='num'>{v}</td></tr>" for k, v in live_stats.items())}</tbody>
      </table>
    </section>
  </div>
</main>
</body>
</html>"""


class BoardHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        console.print(f"[dim]ui[/] {args[0] if args else fmt}")

    def _send_html(self, body: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
                hours = db.hours_since_created(jid)
                db.mark_sent(jid, "application", "portal", "", 0, cfg.followup.cadence_days)
                if job:
                    db.remember_applied_company(job["company"], source="manual", title=job.get("title") or "")
                    db.park_packets_at_applied_companies()
                db.log_event(
                    "applied",
                    job_id=jid,
                    payload={
                        "company": (job or {}).get("company"),
                        "hours_to_applied": hours,
                    },
                )
            elif jid and op == "skip":
                job = db.get_job(jid)
                db.set_status(jid, JobStatus.SKIPPED)
                db.log_event(
                    "skipped",
                    job_id=jid,
                    payload={"company": (job or {}).get("company")},
                )
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()
            return

        if parsed.path in ("/", "/board"):
            self._send_html(render_html(snapshot()).encode("utf-8"))
            return

        if parsed.path == "/api/snapshot":
            body = json.dumps(snapshot(), default=str).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/refresh":
            self.send_response(303)
            self.send_header("Location", "/api/run-daily")
            self.end_headers()
            return

        if parsed.path == "/api/run-daily":
            from .pipeline import run_daily

            try:
                run_daily(skip_inbox=False)
                msg = "Daily finished. <a href='/'>Back to board</a>"
            except Exception as e:
                msg = f"Daily failed: {_esc(e)}. <a href='/'>Back</a>"
            self._send_html(f"<html><body><p>{msg}</p></body></html>".encode("utf-8"))
            return

        self.send_error(404)

    def do_POST(self) -> None:
        self.send_error(405)


def serve(port: int = DEFAULT_PORT, open_browser: bool = True) -> None:
    db.init_db()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), BoardHandler)
    url = f"http://127.0.0.1:{port}/"
    data = snapshot()
    console.print(
        Panel.fit(
            f"[bold]Hunt board[/] → {url}\n"
            f"{data['totals']['review_companies']} core PM companies to review · "
            f"{data['totals']['applied_companies']} applied/out",
            title="look → apply → mark",
        )
    )
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[dim]ui stopped[/]")


def print_board(limit: int = 25) -> None:
    data = snapshot()
    table = Table(title="Review — core PM (1 / company)")
    table.add_column("Score", justify="right")
    table.add_column("Company")
    table.add_column("Title")
    table.add_column("Location")
    table.add_column("URL")
    for r in data["review"][:limit]:
        table.add_row(
            f"{r.get('score', 0):.2f}",
            r["company"],
            (r.get("title") or "")[:48],
            (r.get("location") or "")[:28],
            (r.get("url") or "")[:40],
        )
    console.print(table)
    if not data["review"]:
        console.print("[yellow]Empty — hunt daily, or expand boards in config.yaml[/]")
