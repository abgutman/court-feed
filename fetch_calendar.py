#!/usr/bin/env python3
"""
Fetch court calendars for EDPA and Third Circuit.

EDPA: plain HTTP POST to ecf.paed.uscourts.gov/cgi-bin/CourtSched.pl
Third Circuit: Playwright (JS-rendered PowerApps portal)
"""
import asyncio
import html as html_mod
import json
import pathlib
import re
import ssl
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = pathlib.Path(__file__).parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) court-feed/1.0"


def _ssl_ctx() -> ssl.SSLContext:
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


def main() -> None:
    print("Fetching court calendars...")
    ctx = _ssl_ctx()

    edpa = fetch_edpa_calendar(ctx)
    ca3 = asyncio.run(fetch_ca3_calendar(ctx))

    output = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "edpa": edpa,
        "third_circuit": ca3,
    }

    out_path = DATA_DIR / "calendar.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"\nWrote {len(edpa)} EDPA + {len(ca3)} Third Circuit events to {out_path}")


if __name__ == "__main__":
    main()
