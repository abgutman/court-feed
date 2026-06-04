#!/usr/bin/env python3
"""
Generate a static HTML site from filings.json.
Two views: New Filings (from UJS) and Recent Opinions (from RSS).
"""
import html
import json
import pathlib
import re
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).parent
DATA_FILE = ROOT / "data" / "filings.json"
OUT_DIR = ROOT / "site"
OUT_DIR.mkdir(exist_ok=True)

COURT_COLORS = {
    "PA Supreme Court": "#1a237e",
    "PA Superior Court": "#283593",
    "PA Commonwealth Court": "#303f9f",
    "Third Circuit": "#4a148c",
    "SCOTUS": "#b71c1c",
}

COURT_ORDER = ["PA Supreme Court", "PA Superior Court", "PA Commonwealth Court", "Third Circuit", "SCOTUS"]

PHILLY_AREA_KW = re.compile(
    r"philadelphia|phila\b|bucks county|chester county|montgomery county"
    r"|delaware county|montco|delco|chesco"
    r"|Eastern District of Pennsylvania"
    r"|septa|ppa|pgw|peco|school district of philadelphia"
    r"|city of philadelphia|temple univ|drexel|penn\b|upenn"
    r"|jefferson health|children.s hospital|chop\b",
    re.IGNORECASE,
)


def is_philly_area(entry: dict) -> bool:
    docket = entry.get("docket", "")
    if re.search(r"\d+\s+E[A-Z]*\s+\d{4}", docket):
        return True
    text = entry.get("caption", "") + " " + docket + " " + entry.get("origin", "")
    return bool(PHILLY_AREA_KW.search(text))


def filing_sort_key(f: dict) -> str:
    """Sort filings by date descending, then docket."""
    date_str = f.get("filing_date", "")
    if "/" in date_str:
        parts = date_str.split("/")
        if len(parts) == 3:
            return f"{parts[2]}{parts[0]}{parts[1]}"
    return date_str


def build_filing_card(f: dict) -> str:
    color = COURT_COLORS.get(f["court"], "#333")
    court_short = f["court"].replace("PA ", "")
    caption = html.escape(f["caption"]) if f["caption"] else "<em>No caption</em>"
    status_badge = ""
    if f.get("status"):
        if f["status"] == "Cert Petition":
            bg = "#b71c1c"
        elif f["status"] == "Active":
            bg = "#2e7d32"
        else:
            bg = "#78909c"
        status_badge = (
            f'<span class="badge" style="background:{bg}">'
            f'{html.escape(f["status"])}</span>'
        )

    philly_badge = ""
    if is_philly_area(f):
        philly_badge = '<span class="badge" style="background:#c62828">PHILADELPHIA AREA</span>'

    lower_court_line = ""
    if f.get("lower_court"):
        lc = html.escape(f["lower_court"])
        ld = html.escape(f.get("lower_court_docket", ""))
        lower_court_line = f'<span class="origin">From {lc} {ld}</span>'

    return f"""<div class="card">
  <div class="card-header" style="border-left:4px solid {color}">
    <div class="card-title">{caption}</div>
    <div class="card-meta">
      <span class="court-tag" style="background:{color}">{html.escape(court_short)}</span>
      {status_badge}
      {philly_badge}
      <span class="docket">{html.escape(f["docket"])}</span>
      {lower_court_line}
      <span class="date">Filed {html.escape(f.get("filing_date", ""))}</span>
    </div>
  </div>
  <div class="card-actions">
    <a href="{html.escape(f["url"])}" target="_blank" rel="noopener">View Docket Sheet &rarr;</a>
  </div>
</div>"""


def build_opinion_card(o: dict) -> str:
    color = COURT_COLORS.get(o["court"], "#333")
    court_short = o["court"].replace("PA ", "")
    caption = html.escape(o["caption"]) if o["caption"] else "<em>No caption</em>"
    author_line = ""
    if o.get("author"):
        author_line = f'<span class="author">{html.escape(o["author"])}</span>'
    docket_line = ""
    if o.get("docket"):
        docket_line = f'<span class="docket">{html.escape(o["docket"])}</span>'

    pub = o.get("pub_date", "")
    if pub:
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(pub)
            pub = dt.strftime("%m/%d/%Y")
        except Exception:
            pass

    philly_badge = ""
    origin = o.get("origin", "")
    if is_philly_area(o):
        philly_badge = '<span class="badge" style="background:#c62828">PHILADELPHIA AREA</span>'

    origin_line = ""
    if origin:
        origin_line = f'<span class="origin">{html.escape(origin)}</span>'

    return f"""<div class="card">
  <div class="card-header" style="border-left:4px solid {color}">
    <div class="card-title">{caption}</div>
    <div class="card-meta">
      <span class="court-tag" style="background:{color}">{html.escape(court_short)}</span>
      {f'<span class="badge" style="background:#2e7d32">PRECEDENTIAL</span>' if o.get("precedential") is True else f'<span class="badge" style="background:#9e9e9e">NON-PRECEDENTIAL</span>' if o.get("precedential") is False else '<span class="badge" style="background:#2e7d32">Opinion</span>'}
      {philly_badge}
      {docket_line}
      {origin_line}
      {author_line}
      <span class="date">{html.escape(pub)}</span>
    </div>
  </div>
  <div class="card-actions">
    <a href="{html.escape(o["url"])}" target="_blank" rel="noopener">View Opinion (PDF) &rarr;</a>
  </div>
</div>"""


def build_page(data: dict) -> str:
    generated = data.get("generated", "")
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(generated)
        dt_eastern = dt.astimezone(ZoneInfo("America/New_York"))
        tz_abbr = dt_eastern.strftime("%Z")  # EDT or EST automatically
        generated_display = dt_eastern.strftime(f"%B %d, %Y at %I:%M %p {tz_abbr}")
    except Exception:
        generated_display = generated

    filings = data.get("filings", [])
    opinions = data.get("opinions", [])

    # Group filings by court
    filings_by_court: dict[str, list] = {}
    for f in filings:
        filings_by_court.setdefault(f["court"], []).append(f)

    # Sort each court's filings by date descending
    for court in filings_by_court:
        filings_by_court[court].sort(key=filing_sort_key, reverse=True)

    def format_date_heading(date_str: str) -> str:
        """Convert MM/DD/YYYY to a readable date heading."""
        try:
            dt = datetime.strptime(date_str, "%m/%d/%Y")
            return dt.strftime("%A, %B %d, %Y")
        except Exception:
            return date_str or "Unknown date"

    # Group opinions by court (needed for stats grid and cards)
    opinions_by_court: dict[str, list] = {}
    for o in opinions:
        opinions_by_court.setdefault(o["court"], []).append(o)

    # Build filing cards grouped by court then date (for per-court filter)
    filing_cards = []
    for court in COURT_ORDER:
        court_filings = filings_by_court.get(court, [])
        if court_filings:
            filing_cards.append(
                f'<h3 class="court-heading" style="border-bottom:3px solid {COURT_COLORS.get(court, "#333")}">'
                f'{html.escape(court)} <span class="count">({len(court_filings)})</span></h3>'
            )
            current_date = None
            for f in court_filings:
                fdate = f.get("filing_date", "")
                if fdate != current_date:
                    current_date = fdate
                    filing_cards.append(
                        f'<h4 class="date-heading">{html.escape(format_date_heading(fdate))}</h4>'
                    )
                filing_cards.append(build_filing_card(f))

    # Build filing cards sorted by date across all courts (for "All" view)
    all_filings_sorted = sorted(filings, key=filing_sort_key, reverse=True)
    filing_cards_by_date = []
    current_date = None
    for f in all_filings_sorted:
        fdate = f.get("filing_date", "")
        if fdate != current_date:
            current_date = fdate
            filing_cards_by_date.append(
                f'<h4 class="date-heading">{html.escape(format_date_heading(fdate))}</h4>'
            )
        filing_cards_by_date.append(build_filing_card(f))

    def opinion_date(o: dict) -> str:
        pub = o.get("pub_date", "")
        if pub:
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(pub)
                return dt.strftime("%m/%d/%Y")
            except Exception:
                pass
        return ""

    opinion_cards = []
    for court in COURT_ORDER:
        court_opinions = opinions_by_court.get(court, [])
        if court_opinions:
            opinion_cards.append(
                f'<h3 class="court-heading" style="border-bottom:3px solid {COURT_COLORS.get(court, "#333")}">'
                f'{html.escape(court)} <span class="count">({len(court_opinions)})</span></h3>'
            )
            current_date = None
            for o in court_opinions:
                odate = opinion_date(o)
                if odate != current_date:
                    current_date = odate
                    opinion_cards.append(
                        f'<h4 class="date-heading">{html.escape(format_date_heading(odate))}</h4>'
                    )
                opinion_cards.append(build_opinion_card(o))

    # Build opinion cards sorted by date across all courts (for "All" view)
    def opinion_sort_key(o: dict) -> str:
        pub = o.get("pub_date", "")
        if pub:
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(pub)
                return dt.strftime("%Y%m%d")
            except Exception:
                pass
        return ""

    all_opinions_sorted = sorted(opinions, key=opinion_sort_key, reverse=True)
    opinion_cards_by_date = []
    current_date = None
    for o in all_opinions_sorted:
        odate = opinion_date(o)
        if odate != current_date:
            current_date = odate
            opinion_cards_by_date.append(
                f'<h4 class="date-heading">{html.escape(format_date_heading(odate))}</h4>'
            )
        opinion_cards_by_date.append(build_opinion_card(o))

    filing_html_by_court = "\n".join(filing_cards) if filing_cards else "<p>No filings found.</p>"
    filing_html_by_date = "\n".join(filing_cards_by_date) if filing_cards_by_date else "<p>No filings found.</p>"
    opinion_html_by_court = "\n".join(opinion_cards) if opinion_cards else "<p>No opinions found.</p>"
    opinion_html_by_date = "\n".join(opinion_cards_by_date) if opinion_cards_by_date else "<p>No opinions found.</p>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Av's Court Feed</title>
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
.court-heading {{
  font-size: 17px;
  font-weight: 700;
  margin: 24px 0 12px;
  padding-bottom: 8px;
  color: #1a1a1a;
}}
.court-heading .count {{
  font-weight: 400;
  color: #888;
  font-size: 14px;
}}
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
.court-tag {{
  display: inline-block;
  padding: 2px 8px;
  border-radius: 3px;
  font-size: 11px;
  font-weight: 600;
  color: #fff;
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
.author {{ color: #555; font-style: italic; }}
.origin {{ color: #555; font-size: 12px; }}
.date {{ color: #999; }}
.card-actions {{
  padding: 8px 16px 12px;
}}
.card-actions a {{
  color: #1565c0;
  font-size: 13px;
  text-decoration: none;
}}
.card-actions a:hover {{ text-decoration: underline; }}
.filter-bar {{
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}}
.filter-btn {{
  padding: 6px 14px;
  font-size: 13px;
  border: 1px solid #ddd;
  border-radius: 20px;
  background: #fff;
  cursor: pointer;
  color: #555;
  transition: all 0.2s;
}}
.filter-btn:hover {{ border-color: #999; }}
.filter-btn.active {{
  background: #0d1b3e;
  color: #fff;
  border-color: #0d1b3e;
}}
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
  <h1>Av's Court Feed</h1>
  <p>New filings &amp; opinions from PA Supreme, Superior, Commonwealth, Third Circuit &amp; SCOTUS cert petitions</p>
  <p>Last updated: {html.escape(generated_display)}</p>
  <nav style="margin-top:10px"><a href="https://abgutman.github.io/av-tools/" style="color:#90caf9;font-size:13px;text-decoration:none">Av's Tools Homepage</a> &middot; <a href="calendar.html" style="color:#90caf9;font-size:13px;text-decoration:none">Court Calendars &rarr;</a></nav>
</header>

<div class="container">
  <div class="tabs">
    <button class="tab active" onclick="switchTab('filings')">New Filings</button>
    <button class="tab" onclick="switchTab('opinions')">Opinions</button>
  </div>

  <div class="tab-content active" id="filings-tab">
    <div class="filter-bar">
      <button class="filter-btn active" onclick="filterCourt('filings', 'all')">All Courts</button>
      <button class="filter-btn" onclick="filterCourt('filings', 'Supreme')">Supreme</button>
      <button class="filter-btn" onclick="filterCourt('filings', 'Superior')">Superior</button>
      <button class="filter-btn" onclick="filterCourt('filings', 'Commonwealth')">Commonwealth</button>
      <button class="filter-btn" onclick="filterCourt('filings', 'SCOTUS')">SCOTUS</button>
    </div>
    <div class="view-by-date" id="filings-bydate">{filing_html_by_date}</div>
    <div class="view-by-court" id="filings-bycourt" style="display:none">{filing_html_by_court}</div>
  </div>

  <div class="tab-content" id="opinions-tab">
    <div class="filter-bar">
      <button class="filter-btn active" onclick="filterCourt('opinions', 'all')">All Courts</button>
      <button class="filter-btn" onclick="filterCourt('opinions', 'Supreme')">Supreme</button>
      <button class="filter-btn" onclick="filterCourt('opinions', 'Superior')">Superior</button>
      <button class="filter-btn" onclick="filterCourt('opinions', 'Commonwealth')">Commonwealth</button>
      <button class="filter-btn" onclick="filterCourt('opinions', 'Third Circuit')">Third Circuit</button>
    </div>
    <div class="view-by-date" id="opinions-bydate">{opinion_html_by_date}</div>
    <div class="view-by-court" id="opinions-bycourt" style="display:none">{opinion_html_by_court}</div>
  </div>
</div>

<footer>
  Source: <a href="https://ujsportal.pacourts.us/CaseSearch">UJS Portal</a>,
  <a href="https://www.pacourts.us/">pacourts.us</a> &amp;
  <a href="https://www.ca3.uscourts.gov/opinions-and-oral-arguments">Third Circuit</a><br>
  Data refreshed automatically. Recent entries may not be immediately reflected.
</footer>

<script>
function switchTab(tab) {{
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.getElementById(tab + '-tab').classList.add('active');
  event.target.classList.add('active');
}}

function filterCourt(section, court) {{
  const container = document.getElementById(section + '-tab');
  const byDate = document.getElementById(section + '-bydate');
  const byCourt = document.getElementById(section + '-bycourt');
  const buttons = container.querySelectorAll('.filter-btn');

  buttons.forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');

  if (court === 'all') {{
    byDate.style.display = '';
    byCourt.style.display = 'none';
    return;
  }}

  byDate.style.display = 'none';
  byCourt.style.display = '';

  let currentCourtVisible = false;
  const allElements = byCourt.querySelectorAll('.court-heading, .date-heading, .card');
  allElements.forEach(el => {{
    if (el.classList.contains('court-heading')) {{
      currentCourtVisible = el.textContent.includes(court);
      el.style.display = currentCourtVisible ? '' : 'none';
    }} else {{
      el.style.display = currentCourtVisible ? '' : 'none';
    }}
  }});
}}
</script>
</body>
</html>"""


def main() -> None:
    data = json.loads(DATA_FILE.read_text())
    page = build_page(data)
    out = OUT_DIR / "index.html"
    out.write_text(page)
    print(f"Built site: {out} ({len(data.get('filings', []))} filings, {len(data.get('opinions', []))} opinions)")


if __name__ == "__main__":
    main()
