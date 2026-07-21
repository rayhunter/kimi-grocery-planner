#!/usr/bin/env python3
"""
Local web UI for the grocery planner.

    python web.py            # serves http://127.0.0.1:8000
    python web.py --port 9000

A single form page submits run parameters; the run executes in the background
on the server's event loop (same run_shopping_planner as the CLI), progress
lines stream to the page via polling, and the finished report is rendered
server-side from the same ShoppingReport the CLI renders.
"""
from __future__ import annotations
import argparse
import asyncio
import html
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from agents.orchestrator import run_shopping_planner
from models.report import ShoppingReport, ItemRecommendation
from models.product import DealType
from main import grouped_listings, unit_display, rank_label
from tools.privacy import redact
from tools.web_search import is_offline, set_offline_mode

app = FastAPI(title="Kimi K3 Grocery Planner")

_INDEX_HTML = Path(__file__).parent / "templates" / "index.html"

# In-memory run registry: run_id -> {status, progress, report_html, error}
RUNS: dict[str, dict] = {}
MAX_KEPT_RUNS = 20


class RunRequest(BaseModel):
    locale: str = Field(min_length=2, description="City/state or zip code")
    items: list[str] = Field(min_length=1, description="Grocery items to compare")
    max_stores: int = Field(default=6, ge=1, le=12)
    max_concurrency: int = Field(default=4, ge=1, le=8)


# ── Report → HTML ──────────────────────────────────────────────────────────

def _e(text: str | None) -> str:
    return html.escape(text or "")


def _listing_rows(rec: ItemRecommendation) -> str:
    rows = []
    for group_idx, (unit, listings) in enumerate(grouped_listings(rec)):
        rows.append(
            f'<tr class="unit-head"><td colspan="6">Price basis: {_e(unit_display(unit))}</td></tr>'
        )
        for i, l in enumerate(listings):
            p = l.price
            deal_bits = []
            if p.deal_description:
                deal_bits.append(_e(p.deal_description))
            elif p.deal_type != DealType.REGULAR:
                deal_bits.append(_e(p.deal_type.value.replace("_", " ")))
            if p.membership_required:
                deal_bits.append(f"requires {_e(p.membership_required)}")
            if p.percent_off:
                deal_bits.append(f"{p.percent_off:.0f}% off")
            verified = (
                '<span class="ok">✅</span>'
                if l.price_verified else '<span class="warn" title="not found in raw sources">? unverified</span>'
            )
            savings = f"−${p.savings:.2f}" if p.savings > 0 else ""
            rows.append(
                "<tr>"
                f"<td>{_e(rank_label(i, plain=(i >= 3)))}</td>"
                f"<td>{_e(l.store_name)}<div class='sub'>{_e(l.product_name)}</div></td>"
                f"<td class='num'>${p.regular_price:.2f}</td>"
                f"<td class='num best'>${p.effective_price:.2f}</td>"
                f"<td>{_e(unit_display(p.unit.value))}</td>"
                f"<td>{' · '.join(deal_bits)} {verified} <span class='save'>{savings}</span></td>"
                "</tr>"
            )
    return "".join(rows)


def render_report_html(report: ShoppingReport) -> str:
    parts = [f"<h2>🛒 {_e(report.locale)}</h2>"]
    parts.append(f'<div class="card summary"><h3>📋 Executive Summary</h3><p>{_e(report.executive_summary)}</p></div>')

    for rec in report.items_analyzed:
        parts.append(f"<h3>🥬 {_e(rec.item_query.title())}</h3>")
        if not rec.all_listings:
            parts.append(f'<div class="card warn-card">❓ No price data found. {_e(rec.reasoning)}</div>')
            continue
        parts.append(
            '<table><thead><tr><th>Rank</th><th>Store / Product</th><th>Regular</th>'
            "<th>Best</th><th>Unit</th><th>Deal / Verified</th></tr></thead>"
            f"<tbody>{_listing_rows(rec)}</tbody></table>"
        )
        if rec.best_pick:
            b = rec.best_pick
            tip = f"<p>💡 {_e(rec.membership_tip)}</p>" if rec.membership_tip else ""
            caveat = f'<p class="dim">⚠️ {_e(rec.data_caveats)}</p>' if rec.data_caveats else ""
            parts.append(
                f'<div class="card pick"><h4>✅ Best pick: {_e(b.store_name)} — '
                f"${b.price.effective_price:.2f} {_e(unit_display(b.price.unit.value))}</h4>"
                f"<p>{_e(rec.reasoning)}</p>{tip}{caveat}</div>"
            )
        else:
            parts.append(f'<div class="card warn-card">No confident pick. {_e(rec.reasoning)}</div>')

    if report.optimized_trips:
        parts.append("<h3>🗺️ Optimized Shopping Trips</h3>")
        for i, trip in enumerate(report.optimized_trips, 1):
            parts.append(
                f'<div class="card trip"><h4>Stop {i}: {_e(trip.primary_store)}</h4>'
                f"<p>Buy: {_e(', '.join(trip.items_to_buy_here))}</p>"
                f"<p>Est. total: <strong>${trip.estimated_total:.2f}</strong></p>"
                f'<p class="dim">{_e(trip.notes)}</p></div>'
            )

    quality = (
        f'<p class="dim">⚠️ {_e(report.data_quality_notes)}</p>' if report.data_quality_notes else ""
    )
    parts.append(
        f'<div class="card footer"><p>💰 <strong>Total deal savings: ${report.total_potential_savings:.2f}</strong> '
        f'<span class="dim">(regular vs. effective at recommended stores)</span></p>'
        f'<p class="dim">Stores searched: {_e(", ".join(s.name for s in report.stores_searched))}</p>{quality}</div>'
    )
    return "".join(parts)


# ── Run lifecycle ──────────────────────────────────────────────────────────

async def _execute_run(run_id: str, req: RunRequest) -> None:
    run = RUNS[run_id]

    def on_progress(msg: str):
        run["progress"].append(msg)

    try:
        report = await run_shopping_planner(
            locale=req.locale,
            items=req.items,
            max_stores=req.max_stores,
            max_concurrency=req.max_concurrency,
            on_progress=on_progress,
        )
        run["report_html"] = render_report_html(report)
        run["report_json"] = report.model_dump(mode="json")
        run["status"] = "completed"
    except Exception as e:  # surface, don't crash the server
        # Redact: this string is returned over HTTP to the browser.
        run["error"] = redact(f"{type(e).__name__}: {e}")
        run["status"] = "failed"


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return _INDEX_HTML.read_text()


@app.post("/api/runs")
async def start_run(req: RunRequest):
    items = [i.strip() for i in req.items if i.strip()]
    if not items:
        raise HTTPException(status_code=422, detail="At least one non-empty item is required")
    req.items = items

    run_id = uuid.uuid4().hex[:12]
    RUNS[run_id] = {"status": "running", "progress": [], "report_html": None, "error": None}
    while len(RUNS) > MAX_KEPT_RUNS:
        RUNS.pop(next(iter(RUNS)))
    asyncio.create_task(_execute_run(run_id, req))
    return {"run_id": run_id}


@app.get("/api/runs/{run_id}")
async def run_status(run_id: str):
    run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Unknown run id")
    return {
        "status": run["status"],
        "progress": run["progress"],
        "report_html": run["report_html"] if run["status"] == "completed" else None,
        "error": run["error"],
    }


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description="Grocery planner web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Privacy mode: no third-party web search for any run on this server. "
             "Server-wide, not per-request. Also settable via GROCERY_OFFLINE=1.",
    )
    args = parser.parse_args()
    if args.offline:
        set_offline_mode(True)
    print(f"🛒 Grocery planner UI → http://{args.host}:{args.port}")
    if is_offline():
        print("🔒 Offline mode: no third-party search calls will be made.")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
