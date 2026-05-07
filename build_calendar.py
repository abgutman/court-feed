#!/usr/bin/env python3
"""
Generate a static HTML calendar page from calendar.json.
Two sections: EDPA schedule and Third Circuit oral arguments.
"""
import html
import json
import pathlib
import re
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).parent
DATA_FILE = ROOT / "data" / "calendar.json"
OUT_DIR = ROOT / "site"
OUT_DIR.mkdir(exist_ok=True)

COURT_COLORS = {
    "EDPA": "#1565c0",
    "Third Circuit": "#4a148c",
}


def _format_edpa_date(dt_str: str) -> tuple[str, str]:
    """Parse '05/11/2026 09:30AM' into (date_heading, time_display)."""
    try:
        dt = datetime.strptime(dt_str, "%m/%d/%Y %I:%M%p")
        date_heading = dt.strftime("%A, %B %d, %Y")
        time_display = dt.strftime("%I:%M %p").lstrip("0")
        return date_heading, time_display
    except Exception:
        return dt_str, ""


def _format_ca3_date(date_str: str) -> str:
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%A, %B %d, %Y")
    except Exception:
        return date_str


def build_edpa_cards(events: list[dict]) -> str:
    """Build EDPA calendar cards grouped by date."""
    by_date: dict[str, list] = {}
    for e in events:
        heading, time = _format_edpa_date(e["datetime"])
        e["_date_heading"] = heading
        e["_time"] = time
        by_date.setdefault(heading, []).append(e)

    cards = []
    for heading, day_events in by_date.items():
        cards.append(f'<h4 class="date-heading">{html.escape(heading)}</h4>')
        for e in day_events:
            case_link = ""
            if e.get("url"):
                case_link = f' <a href="{html.escape(e["url"])}" target="_blank" rel="noopener">View Docket &rarr;</a>'

            case_display = html.escape(e.get("case_number", ""))
            caption = html.escape(e.get("caption", "")) or "<em>No caption</em>"
            judge = html.escape(e.get("judge", ""))
            city = html.escape(e.get("city", "")) or "TBD"
            courtroom = html.escape(e.get("courtroom", ""))
            hearing_type = html.escape(e.get("hearing_type", ""))
            time_display = html.escape(e.get("_time", ""))

            ht_bg = "#78909c"
            ht_lower = hearing_type.lower()
            if "trial" in ht_lower or "jury" in ht_lower:
                ht_bg = "#c62828"
            elif "sentencing" in ht_lower:
                ht_bg = "#e65100"
            elif "arraignment" in ht_lower or "initial" in ht_lower:
                ht_bg = "#2e7d32"
            elif "motion" in ht_lower:
                ht_bg = "#1565c0"

            cards.append(f"""<div class="card">
  <div class="card-header" style="border-left:4px solid {COURT_COLORS['EDPA']}">
    <div class="card-title">{caption}</div>
    <div class="card-meta">
      <span class="badge" style="background:{ht_bg}">{hearing_type}</span>
      <span class="docket">{case_display}</span>
      <span class="judge">{judge}</span>
      <span class="location">{city} &mdash; {courtroom}</span>
      <span class="time">{time_display}</span>
    </div>
  </div>
  <div class="card-actions">{case_link}</div>
</div>""")

    return "\n".join(cards) if cards else "<p>No scheduled events.</p>"


def build_ca3_cards(events: list[dict]) -> str:
    """Build Third Circuit oral argument cards grouped by date."""
    by_date: dict[str, list] = {}
    for e in events:
        heading = _format_ca3_date(e.get("date", ""))
        by_date.setdefault(heading, []).append(e)

    cards = []
    for heading, day_events in by_date.items():
        cards.append(f'<h4 class="date-heading">{html.escape(heading)}</h4>')
        for e in day_events:
            caption = html.escape(e.get("caption", "")) or "<em>No caption</em>"
            case_num = html.escape(e.get("case_number", ""))
            panel = html.escape(e.get("panel", ""))
            loc_time = html.escape(e.get("location_time", ""))
            disposition = e.get("disposition", "Argued")

            disp_bg = "#2e7d32" if disposition == "Argued" else "#78909c"

            cards.append(f"""<div class="card">
  <div class="card-header" style="border-left:4px solid {COURT_COLORS['Third Circuit']}">
    <div class="card-title">{caption}</div>
    <div class="card-meta">
      <span class="badge" style="background:{disp_bg}">{html.escape(disposition)}</span>
      <span class="docket">{case_num}</span>
      <span class="panel">Panel: {panel}</span>
      <span class="location">{loc_time}</span>
    </div>
  </div>
</div>""")

    return "\n".join(cards) if cards else "<p>No scheduled oral arguments.</p>"


def build_page(data: dict) -> str:
    generated = data.get("generated", "")
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(generated)
        dt_eastern = dt.astimezone(ZoneInfo("America/New_York"))
        tz_abbr = dt_eastern.strftime("%Z")
        generated_display = dt_eastern.strftime(f"%B %d, %Y at %I:%M %p {tz_abbr}")
    except Exception:
        generated_display = generated

    edpa_events = data.get("edpa", [])
    ca3_events = data.get("third_circuit", [])

    edpa_html = build_edpa_cards(edpa_events)
    ca3_html = build_ca3_cards(ca3_events)

    edpa_dates = len(set(e.get("_date_heading", "") for e in edpa_events if "_date_heading" in e))
    ca3_dates = len(set(e.get("date", "") for e in ca3_events))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Court Calendars &mdash; EDPA &amp; Third Circuit</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: #f5f5f5;
  color: #1a1a1a;
  line-height: 1.5;
}}
header {{
  background: #0d1b3e;
  color: #fff;
  padding: 24px 20px;
  text-align: center;
}}
header h1 {{
  font-size: 24px;
  font-weight: 700;
  letter-spacing: 0.5px;
}}
header p {{
  color: #b0bec5;
  font-size: 13px;
  margin-top: 4px;
}}
header nav {{
  margin-top: 10px;
}}
header nav a {{
  color: #90caf9;
  font-size: 13px;
  text-decoration: none;
  margin: 0 8px;
}}
header nav a:hover {{ text-decoration: underline; }}
.container {{
  max-width: 900px;
  margin: 0 auto;
  padding: 20px;
}}
.tabs {{
  display: flex;
  gap: 0;
  margin-bottom: 24px;
  border-bottom: 2px solid #ddd;
}}
.tab {{
  padding: 12px 24px;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  border: none;
  background: none;
  color: #666;
  border-bottom: 3px solid transparent;
  margin-bottom: -2px;
  transition: all 0.2s;
}}
.tab:hover {{ color: #333; }}
.tab.active {{
  color: #0d1b3e;
  border-bottom-color: #0d1b3e;
}}
.tab-content {{ display: none; }}
.tab-content.active {{ display: block; }}
.date-heading {{
  font-size: 13px;
  font-weight: 600;
  color: #666;
  margin: 16px 0 8px;
  padding: 6px 0;
  border-bottom: 1px solid #e0e0e0;
  text-transform: uppercase;
  letter-spacing: 0.3px;
}}
.card {{
  background: #fff;
  border-radius: 6px;
  margin-bottom: 10px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.08);
  overflow: hidden;
}}
.card-header {{
  padding: 14px 16px 10px;
}}
.card-title {{
  font-size: 15px;
  font-weight: 600;
  margin-bottom: 6px;
}}
.card-meta {{
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #666;
}}
.badge {{
  display: inline-block;
  padding: 2px 8px;
  border-radius: 3px;
  font-size: 11px;
  font-weight: 600;
  color: #fff;
}}
.docket {{ color: #555; font-family: monospace; font-size: 12px; }}
.judge {{ color: #555; font-style: italic; }}
.panel {{ color: #555; }}
.location {{ color: #888; }}
.time {{ color: #0d1b3e; font-weight: 600; }}
.card-actions {{
  padding: 8px 16px 12px;
}}
.card-actions a {{
  color: #1565c0;
  font-size: 13px;
  text-decoration: none;
}}
.card-actions a:hover {{ text-decoration: underline; }}
footer {{
  text-align: center;
  padding: 24px;
  color: #999;
  font-size: 12px;
}}
footer a {{ color: #999; }}
@media (max-width: 600px) {{
  .tabs {{ overflow-x: auto; }}
  .tab {{ padding: 10px 16px; font-size: 14px; white-space: nowrap; }}
}}
</style>
</head>
<body>
<header>
  <h1>Court Calendars</h1>
  <p>EDPA Schedule &amp; Third Circuit Oral Arguments</p>
  <p>Last updated: {html.escape(generated_display)}</p>
  <nav><a href="index.html">&larr; Filings &amp; Opinions</a></nav>
</header>

<div class="container">
  <div class="tabs">
    <button class="tab active" onclick="switchTab('edpa')">EDPA ({len(edpa_events)})</button>
    <button class="tab" onclick="switchTab('ca3')">Third Circuit ({len(ca3_events)})</button>
  </div>

  <div class="tab-content active" id="edpa-tab">
    {edpa_html}
  </div>

  <div class="tab-content" id="ca3-tab">
    {ca3_html}
  </div>
</div>

<footer>
  Source: <a href="https://ecf.paed.uscourts.gov/cgi-bin/CourtSched.pl">EDPA ECF</a> &amp;
  <a href="https://www.ca3.uscourts.gov/calendar">Third Circuit Calendar</a><br>
  Data refreshed automatically. Schedules are subject to change.
</footer>

<script>
function switchTab(tab) {{
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.getElementById(tab + '-tab').classList.add('active');
  event.target.classList.add('active');
}}
</script>
</body>
</html>"""


def main() -> None:
    data = json.loads(DATA_FILE.read_text())
    page = build_page(data)
    out = OUT_DIR / "calendar.html"
    out.write_text(page)
    edpa = len(data.get("edpa", []))
    ca3 = len(data.get("third_circuit", []))
    print(f"Built calendar: {out} ({edpa} EDPA + {ca3} Third Circuit events)")


if __name__ == "__main__":
    main()
