#!/usr/bin/env python3
"""
Fetch court calendars for EDPA, Third Circuit, and PA appellate courts.

EDPA: plain HTTP POST to ecf.paed.uscourts.gov/cgi-bin/CourtSched.pl
Third Circuit: Playwright (JS-rendered PowerApps portal)
PA Supreme/Superior/Commonwealth: HTML + PDF parsing from pacourts.us
"""
import asyncio
import html as html_mod
import io
import json
import pathlib
import re
import ssl
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    import certifi
    _CERTIFI_CAFILE = certifi.where()
except ImportError:
    _CERTIFI_CAFILE = None

try:
    import PyPDF2
except ImportError:
    PyPDF2 = None

ROOT = pathlib.Path(__file__).parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) court-feed/1.0"


def _ssl_ctx() -> ssl.SSLContext:
    if _CERTIFI_CAFILE:
        return ssl.create_default_context(cafile=_CERTIFI_CAFILE)
    try:
        ctx = ssl.create_default_context()
        urlopen(Request("https://ecf.paed.uscourts.gov/", headers={"User-Agent": "probe"}),
                timeout=10, context=ctx)
        return ctx
    except Exception:
        return ssl._create_unverified_context()


def fetch_edpa_calendar(ctx: ssl.SSLContext, days_ahead: int = 14) -> list[dict]:
    """Fetch EDPA court schedule for the next `days_ahead` days."""
    print("  Fetching EDPA calendar...")
    today = datetime.now()
    start = today.strftime("%m/%d/%Y")
    end = (today + timedelta(days=days_ahead)).strftime("%m/%d/%Y")

    url = "https://ecf.paed.uscourts.gov/cgi-bin/CourtSched.pl"
    data = urlencode({"from": start, "to": end}).encode()
    req = Request(url, data=data, headers={
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded",
    })

    try:
        with urlopen(req, timeout=30, context=ctx) as resp:
            html_text = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"    ERROR: {e}", file=sys.stderr)
        return []

    events = []
    for m in re.finditer(r"<tr><td(.*?)</tr>", html_text, re.DOTALL):
        row = m.group(0)
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
        if len(cells) < 6:
            continue

        def clean(s: str) -> str:
            t = re.sub(r"<[^>]+>", " ", s)
            t = html_mod.unescape(re.sub(r"\s+", " ", t).strip())
            return t

        time_str = clean(cells[0])
        case_raw = clean(cells[1])
        judge = clean(cells[2])
        city = clean(cells[3])
        courtroom = clean(cells[4])
        hearing_type = clean(cells[5])

        case_num = ""
        caption = case_raw
        cm = re.match(r"(\d+-\w+-\d+):\s*(.*)", case_raw)
        if cm:
            case_num = cm.group(1)
            caption = cm.group(2)

        link = ""
        lm = re.search(r"href\s*=\s*['\"]([^'\"]+)['\"]", cells[1])
        if lm:
            link = lm.group(1)
            if not link.startswith("http"):
                link = f"https://ecf.paed.uscourts.gov{link}"

        events.append({
            "court": "EDPA",
            "datetime": time_str,
            "case_number": case_num,
            "caption": caption,
            "judge": judge,
            "city": city,
            "courtroom": courtroom,
            "hearing_type": hearing_type,
            "url": link,
        })

    print(f"    Found {len(events)} events")
    return events


def _get_ca3_hearing_dates(ctx: ssl.SSLContext) -> list[str]:
    """Get dates with oral arguments from the ca3 calendar month views."""
    dates = set()
    now = datetime.now()
    for delta in [0, 1]:
        month = now.month + delta
        year = now.year
        if month > 12:
            month -= 12
            year += 1
        url = f"https://www.ca3.uscourts.gov/calendar/month/{year}-{month:02d}"
        req = Request(url, headers={"User-Agent": UA})
        try:
            with urlopen(req, timeout=15, context=ctx) as resp:
                html_text = resp.read().decode("utf-8", errors="replace")
        except Exception:
            continue

        for m in re.finditer(r'data-date="(\d{4}-\d{2}-\d{2})"', html_text):
            date = m.group(1)
            idx = m.start()
            if "case-list" in html_text[idx:idx + 500]:
                dates.add(date)

    return sorted(dates)


async def _scrape_ca3_date(browser, date_str: str) -> list[dict]:
    """Scrape one Third Circuit oral argument date via Playwright."""
    page = await browser.new_page()
    url = f"https://ca03portal.powerappsportals.us/Oral-Argument-landing?hrngDt={date_str}"
    try:
        await page.goto(url, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(5000)
    except Exception as e:
        print(f"    ERROR loading {date_str}: {e}", file=sys.stderr)
        await page.close()
        return []

    events = []

    for section_id, disposition in [("#argued_container", "Argued"), ("#submitted_container", "Submitted")]:
        rows = await page.query_selector_all(f"{section_id} table tbody tr")
        for row in rows:
            cells = await row.query_selector_all("td")
            texts = []
            for c in cells:
                t = (await c.inner_text()).strip()
                texts.append(t)

            if disposition == "Argued" and len(texts) >= 4 and re.match(r"\d+-\d+", texts[0]):
                events.append({
                    "court": "Third Circuit",
                    "date": date_str,
                    "case_number": texts[0],
                    "caption": texts[1],
                    "panel": texts[2],
                    "location_time": texts[3] if len(texts) > 3 else "",
                    "disposition": disposition,
                })
            elif disposition == "Submitted" and len(texts) >= 3 and re.match(r"\d+-\d+", texts[0]):
                events.append({
                    "court": "Third Circuit",
                    "date": date_str,
                    "case_number": texts[0],
                    "caption": texts[1],
                    "panel": texts[2],
                    "location_time": "",
                    "disposition": disposition,
                })

    await page.close()
    return events


async def fetch_ca3_calendar(ctx: ssl.SSLContext) -> list[dict]:
    """Fetch Third Circuit oral argument calendar via Playwright."""
    print("  Fetching Third Circuit calendar...")
    dates = _get_ca3_hearing_dates(ctx)
    today = datetime.now().strftime("%Y-%m-%d")
    future_dates = [d for d in dates if d >= today]
    print(f"    Found {len(future_dates)} upcoming hearing dates: {future_dates}")

    if not future_dates:
        return []

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("    WARNING: playwright not installed, skipping Third Circuit calendar", file=sys.stderr)
        return []

    events = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        for date_str in future_dates:
            print(f"    Scraping {date_str}...")
            day_events = await _scrape_ca3_date(browser, date_str)
            print(f"      {len(day_events)} cases")
            events.extend(day_events)
        await browser.close()

    print(f"    Total: {len(events)} Third Circuit calendar entries")
    return events


PACOURTS_BASE = "https://www.pacourts.us"
MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def _fetch_url(url: str, ctx: ssl.SSLContext) -> bytes | None:
    req = Request(url, headers={"User-Agent": UA})
    try:
        with urlopen(req, timeout=30, context=ctx) as resp:
            return resp.read()
    except Exception as e:
        print(f"    ERROR fetching {url}: {e}", file=sys.stderr)
        return None


def _extract_pdf_sessions(html_text: str, year: int) -> list[dict]:
    """Extract (date_text, location, pdf_url) from a pacourts calendar table for a given year."""
    sessions = []
    pattern = re.compile(
        r"<tr[^>]*>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*</tr>",
        re.DOTALL,
    )
    for m in pattern.finditer(html_text):
        date_raw = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        date_raw = date_raw.replace("&nbsp;", " ").replace("\xa0", " ")
        location = re.sub(r"<[^>]+>", "", m.group(2)).strip().replace("&nbsp;", " ")
        link_cell = m.group(3)

        pdf_match = re.search(r'href="([^"]+\.pdf)"', link_cell)
        if not pdf_match:
            continue

        pdf_path = pdf_match.group(1)
        if not pdf_path.startswith("http"):
            pdf_path = PACOURTS_BASE + pdf_path

        start_date = _parse_session_date(date_raw, year)
        sessions.append({
            "date_text": date_raw,
            "location": html_mod.unescape(location),
            "pdf_url": pdf_path,
            "start_date": start_date,
            "year": year,
        })

    return sessions


def _parse_session_date(date_text: str, year: int) -> str | None:
    """Parse 'May 12 - 14' or 'March 25' into 'YYYY-MM-DD'."""
    date_text = re.sub(r"\s+", " ", date_text).strip()
    m = re.match(r"(\w+)\s+(\d+)", date_text)
    if not m:
        return None
    month_name = m.group(1).lower()
    day = int(m.group(2))
    month = MONTH_MAP.get(month_name)
    if not month:
        return None
    return f"{year}-{month:02d}-{day:02d}"


def _clean_city(raw: str) -> str:
    """Strip PyPDF2 artifacts from city/location fields."""
    raw = re.sub(r"(Listed\s*/?\s*Submitted\s*:?|Panel\s*:?|Begin Date\s*:?|End Date\s*:?|Location\s*:?|City\s*:?).*", "", raw)
    return raw.strip().rstrip(",")


def _parse_supreme_pdf(data: bytes, pdf_url: str) -> list[dict]:
    reader = PyPDF2.PdfReader(io.BytesIO(data))
    events = []
    current_date = ""
    current_location = ""
    current_city = ""

    for page in reader.pages:
        text = page.extract_text() or ""
        lines = text.split("\n")

        for i, line in enumerate(lines):
            dm = re.match(r"(\d{2}/\d{2}/\d{4})", line.strip())
            if dm:
                current_date = dm.group(1)
                if i + 1 < len(lines):
                    current_location = _clean_city(lines[i + 1].strip())
                if i + 2 < len(lines):
                    current_city = _clean_city(lines[i + 2].strip())

            cm = re.match(
                r"(\d+\s+MAP\s+\d{4})\s+(J-\d+-\d+)\s+(\d+)\s+(.*?)\s+(Oral Argument|Submitted)",
                line.strip()
            )
            if cm:
                events.append({
                    "court": "PA Supreme Court",
                    "date": current_date,
                    "docket_number": cm.group(1),
                    "journal_number": cm.group(2),
                    "caption": cm.group(4).strip(),
                    "consideration_type": cm.group(5).strip(),
                    "location": current_location,
                    "city": current_city or "Harrisburg",
                    "panel": "Full Court",
                    "judges": "",
                })

    return events


def _parse_superior_pdf(data: bytes, pdf_url: str) -> list[dict]:
    reader = PyPDF2.PdfReader(io.BytesIO(data))
    events = []
    panel = ""
    judges = ""
    location = ""
    city = ""
    current_date = ""
    begin_date = ""
    end_date = ""

    for page in reader.pages:
        text = page.extract_text() or ""
        lines = text.split("\n")

        for i, line in enumerate(lines):
            s = line.strip()

            pm = re.match(r"(\d+-ARGUMENT-\d+-\d+|\d+-EN BANC-\d+-\d+)", s)
            if pm:
                panel = pm.group(1)

            # Judges line: may start with "City:" due to PyPDF2 merge
            jm = re.search(r"([A-Z]{3,}(?:\s*;\s*[A-Z]{3,})+\s*,\s*JJ\.)", s)
            if jm:
                judges = jm.group(1)

            if "17th Floor" in s or "530 Walnut" in s or s.startswith("Courtroom "):
                location = _clean_city(s)

            # City: may be merged like "PhiladelphiaPanel :"
            city_m = re.match(r"^(Harrisburg|Philadelphia|Pittsburgh|Lackawanna|Villanova|Duquesne|Centre County)", s)
            if city_m:
                city = city_m.group(1)

            # Begin/end dates on their own lines (e.g., "06/15/2026")
            bare_date = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", s)
            if bare_date:
                date_val = s
                if not begin_date:
                    begin_date = date_val
                else:
                    end_date = date_val

            # Listed/Submitted date - may have spaces like "06 /15/2026"
            dm = re.match(r"Listed\s*/\s*Submitted\s*:\s*(\d{2})\s*/\s*(\d{2})\s*/\s*(\d{4})", s)
            if dm:
                current_date = f"{dm.group(1)}/{dm.group(2)}/{dm.group(3)}"

            cm = re.match(
                r"(\d+\s+[A-Z]{2,4}\s+\d{4})\s+(J-A\d+-\d+)\s+(.*?)\s+"
                r"(Standard|Expedited)\s+(Argument Panel|Submit(?:ted)?\s*(?:on Briefs-)?Panel|Continued|Stricken|Quashed|Completed|Discontinued)",
                s,
            )
            if cm:
                event_date = current_date or begin_date
                events.append({
                    "court": "PA Superior Court",
                    "date": event_date,
                    "docket_number": cm.group(1),
                    "journal_number": cm.group(2),
                    "caption": cm.group(3).strip(),
                    "consideration_type": f"{cm.group(4)} — {cm.group(5)}",
                    "location": location,
                    "city": city,
                    "panel": panel,
                    "judges": judges,
                })

    return events


def _parse_commonwealth_pdf(data: bytes, pdf_url: str) -> list[dict]:
    reader = PyPDF2.PdfReader(io.BytesIO(data))
    events = []
    current_panel = ""
    session_location = ""
    session_city = ""
    session_begin = ""
    session_end = ""

    full_text = ""
    for page in reader.pages:
        full_text += (page.extract_text() or "") + "\n"

    loc_m = re.search(r"Sitting in ([^,]+),\s*(\w+)", full_text)
    if loc_m:
        session_city = loc_m.group(1).strip()

    begin_m = re.search(r"Beginning:\s*(\w+\s+\d+,\s*\d{4})", full_text)
    if begin_m:
        session_begin = begin_m.group(1)

    end_m = re.search(r"Ending:\s*(\w+\s+\d+,\s*\d{4})", full_text)
    if end_m:
        session_end = end_m.group(1)

    addr_m = re.search(r"((?:Ninth Floor|Fourth Floor|Courtroom)[^\n]{5,80})", full_text)
    if addr_m:
        session_location = addr_m.group(1).strip()

    lines = full_text.split("\n")
    current_date = ""

    for line in lines:
        s = line.strip()

        pm = re.match(r"Panel\s+(\d+)", s)
        if pm:
            current_panel = f"Panel {pm.group(1)}"

        day_m = re.match(r"(Monday|Tuesday|Wednesday|Thursday|Friday),\s+(\w+\s+\d+,\s*\d{4})", s)
        if day_m:
            try:
                dt = datetime.strptime(day_m.group(2), "%B %d, %Y")
                current_date = dt.strftime("%m/%d/%Y")
            except ValueError:
                pass

        cm = re.match(
            r"(\d+[a-z]?-\d+-\d{4})\s+(\d+\s+[A-Z]{2}\s+\d{4})\s+(.*?)\s+"
            r"(\d{2}/\d{2}/\d{4}\s*-\s*\d+:\d+\s*[ap]\.m\.|SUBMITTED(?:-PANEL|-EN BANC)?|CONTINUED|STRICKEN)",
            s,
        )
        if cm:
            journal = cm.group(1)
            docket = cm.group(2)
            caption = cm.group(3).strip()
            status = cm.group(4).strip()

            date_for_event = current_date
            time_m = re.match(r"(\d{2}/\d{2}/\d{4})\s*-\s*(.+)", status)
            if time_m:
                date_for_event = time_m.group(1)

            events.append({
                "court": "PA Commonwealth Court",
                "date": date_for_event or session_begin,
                "docket_number": docket,
                "journal_number": journal,
                "caption": caption,
                "consideration_type": status,
                "location": session_location,
                "city": session_city,
                "panel": current_panel,
                "judges": "",
            })

    return events


def fetch_pa_appellate_calendars(ctx: ssl.SSLContext) -> dict:
    """Fetch calendars for PA Supreme, Superior, and Commonwealth courts."""
    if PyPDF2 is None:
        print("  WARNING: PyPDF2 not installed, skipping PA appellate calendars", file=sys.stderr)
        return {"pa_supreme": [], "pa_superior": [], "pa_commonwealth": []}

    today = datetime.now()
    cutoff = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    results = {"pa_supreme": [], "pa_superior": [], "pa_commonwealth": []}

    courts = [
        ("supreme-court", "PA Supreme Court", "pa_supreme", _parse_supreme_pdf),
        ("superior-court", "PA Superior Court", "pa_superior", _parse_superior_pdf),
        ("commonwealth-court", "PA Commonwealth Court", "pa_commonwealth", _parse_commonwealth_pdf),
    ]

    for slug, name, key, parser in courts:
        print(f"  Fetching {name} calendar...")
        url = f"{PACOURTS_BASE}/courts/{slug}/calendar"
        raw = _fetch_url(url, ctx)
        if not raw:
            continue
        html_text = raw.decode("utf-8", errors="replace")

        year_match = re.search(r"<h2[^>]*>\s*(\d{4})\s*Session", html_text)
        year = int(year_match.group(1)) if year_match else today.year

        sessions = _extract_pdf_sessions(html_text, year)
        future_sessions = [s for s in sessions if s["start_date"] and s["start_date"] >= cutoff]
        # Also skip conference lists for Commonwealth
        if key == "pa_commonwealth":
            future_sessions = [s for s in future_sessions if "conference" not in s["pdf_url"].lower()]

        print(f"    {len(future_sessions)} upcoming sessions (of {len(sessions)} total)")

        all_events = []
        for sess in future_sessions:
            pdf_data = _fetch_url(sess["pdf_url"], ctx)
            if not pdf_data:
                continue
            try:
                events = parser(pdf_data, sess["pdf_url"])
                print(f"    {sess['date_text']} ({sess['location']}): {len(events)} cases")
                all_events.extend(events)
            except Exception as e:
                print(f"    ERROR parsing {sess['pdf_url']}: {e}", file=sys.stderr)

        results[key] = all_events
        print(f"    Total: {len(all_events)} {name} calendar entries")

    return results


def main() -> None:
    print("Fetching court calendars...")
    ctx = _ssl_ctx()

    edpa = fetch_edpa_calendar(ctx)
    ca3 = asyncio.run(fetch_ca3_calendar(ctx))
    pa = fetch_pa_appellate_calendars(ctx)

    output = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "edpa": edpa,
        "third_circuit": ca3,
        "pa_supreme": pa["pa_supreme"],
        "pa_superior": pa["pa_superior"],
        "pa_commonwealth": pa["pa_commonwealth"],
    }

    out_path = DATA_DIR / "calendar.json"
    out_path.write_text(json.dumps(output, indent=2))
    counts = " + ".join([
        f"{len(edpa)} EDPA",
        f"{len(ca3)} Third Circuit",
        f"{len(pa['pa_supreme'])} PA Supreme",
        f"{len(pa['pa_superior'])} PA Superior",
        f"{len(pa['pa_commonwealth'])} PA Commonwealth",
    ])
    print(f"\nWrote {counts} events to {out_path}")


if __name__ == "__main__":
    main()
