#!/usr/bin/env python3
"""
Fetch new filings and opinions from PA Supreme, Superior, and Commonwealth courts.

Two data sources:
  1. UJS Portal appellate search — new case filings (docket openings)
  2. pacourts.us RSS feeds — new published opinions

Outputs filings.json for the static site generator.
"""
import html as html_mod
import json
import pathlib
import re
import ssl
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from http.cookiejar import CookieJar
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPCookieProcessor, HTTPSHandler, urlopen

ROOT = pathlib.Path(__file__).parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) court-feed/1.0"
UJS_BASE = "https://ujsportal.pacourts.us"

COURTS = {
    "Supreme": "PA Supreme Court",
    "Superior": "PA Superior Court",
    "Commonwealth": "PA Commonwealth Court",
}

RSS_FEEDS = {
    "PA Supreme Court": "https://www.pacourts.us/Rss/Opinions/Supreme/",
    "PA Superior Court": "https://www.pacourts.us/Rss/Opinions/Superior/",
    "PA Commonwealth Court": "https://www.pacourts.us/Rss/Opinions/Commonwealth/",
}

CA3_PREC_URL = "https://www2.ca3.uscourts.gov/recentop/week/recprec.htm"
CA3_NONPREC_URL = "https://www2.ca3.uscourts.gov/recentop/week/recnonprec.htm"

SCOTUS_JSON = "https://www.supremecourt.gov/RSS/Cases/JSON/{docket}.json"

NS = {"dc": "http://purl.org/dc/elements/1.1/"}


def _ssl_ctx() -> ssl.SSLContext:
    try:
        ctx = ssl.create_default_context()
        urlopen(Request("https://www.pacourts.us/", headers={"User-Agent": "probe"}),
                timeout=10, context=ctx)
        return ctx
    except (ssl.SSLCertVerificationError, Exception):
        return ssl._create_unverified_context()


def fetch_ujs_filings(ctx: ssl.SSLContext, days_back: int = 7) -> list[dict]:
    """Search UJS portal for recent appellate filings across all three courts."""
    jar = CookieJar()
    opener = build_opener(HTTPCookieProcessor(jar), HTTPSHandler(context=ctx))

    req = Request(f"{UJS_BASE}/CaseSearch", headers={"User-Agent": UA})
    page = opener.open(req, timeout=30).read().decode()

    token_match = re.search(r'__RequestVerificationToken.*?value=["\']([^"\']+)', page)
    token = token_match.group(1) if token_match else ""

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    all_filings = []

    for court_key, court_name in COURTS.items():
        print(f"  Searching UJS for {court_name} ({start_date} to {end_date})...")
        data = urlencode({
            "SearchBy": "AppellateCourtName",
            "AppellateCourtName": court_key,
            "FiledStartDate": start_date,
            "FiledEndDate": end_date,
            "__RequestVerificationToken": token,
        }).encode()

        req = Request(
            f"{UJS_BASE}/CaseSearch",
            data=data,
            headers={
                "User-Agent": UA,
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": f"{UJS_BASE}/CaseSearch",
            },
        )

        try:
            result = opener.open(req, timeout=30).read().decode()
        except Exception as e:
            print(f"    ERROR: {e}", file=sys.stderr)
            continue

        filings = parse_ujs_results(result, court_name)
        print(f"    Found {len(filings)} filings")
        all_filings.extend(filings)

    return all_filings


def parse_ujs_results(html_text: str, court_name: str) -> list[dict]:
    """Parse the UJS search results HTML table.

    Row structure (from <tbody>):
      td[0]: sort index (hidden)
      td[1]: hidden 0
      td[2]: Docket Number
      td[3]: Court Type ("Appellate")
      td[4]: Case Caption
      td[5]: Case Status
      td[6]: Filing Date (MM/DD/YYYY)
      td[7+]: mostly empty, last td has docket sheet link
    """
    filings = []

    tbody_idx = html_text.find("<tbody")
    if tbody_idx == -1:
        return filings

    tbody_end = html_text.find("</tbody>", tbody_idx)
    tbody = html_text[tbody_idx:tbody_end] if tbody_end > -1 else html_text[tbody_idx:]

    for row_match in re.finditer(r"<tr>(.*?)</tr>", tbody, re.DOTALL):
        row = row_match.group(1)
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
        if len(cells) < 7:
            continue

        def cell_text(html_str: str) -> str:
            t = re.sub(r"<[^>]+>", " ", html_str)
            t = re.sub(r"\s+", " ", t).strip()
            return html_mod.unescape(t)

        docket = cell_text(cells[2])
        caption = cell_text(cells[4])
        status = cell_text(cells[5])
        filing_date = cell_text(cells[6])

        link_match = re.search(
            r'href=["\'/]*(Report/PacDocketSheet\?docketNumber=[^"\'&]+&amp;dnh=[^"\']+)',
            row,
        )
        url = ""
        if link_match:
            url = f"{UJS_BASE}/{html_mod.unescape(link_match.group(1))}"

        filings.append({
            "type": "filing",
            "court": court_name,
            "docket": docket,
            "caption": caption,
            "status": status,
            "filing_date": filing_date,
            "url": url,
        })

    return filings


def fetch_rss_opinions(ctx: ssl.SSLContext) -> list[dict]:
    """Fetch recent opinions from pacourts.us RSS feeds."""
    opinions = []

    for court_name, feed_url in RSS_FEEDS.items():
        print(f"  Fetching RSS for {court_name}...")
        req = Request(feed_url, headers={"User-Agent": UA})
        try:
            with urlopen(req, timeout=30, context=ctx) as resp:
                root = ET.fromstring(resp.read())
        except Exception as e:
            print(f"    ERROR: {e}", file=sys.stderr)
            continue

        channel = root.find("channel")
        if channel is None:
            continue

        count = 0
        for item in channel.findall("item"):
            raw_title = item.findtext("title", "").strip()
            link = item.findtext("link", "").strip()
            guid = item.findtext("guid", link).strip()
            pub_date = item.findtext("pubDate", "").strip()
            author = item.findtext("dc:creator", "", NS).strip()

            case_name = raw_title
            docket = ""
            if "\n" in raw_title:
                parts = raw_title.split("\n", 1)
                case_name = parts[0].strip()
                docket = parts[1].strip()
            elif " - No. " in raw_title:
                idx = raw_title.index(" - No. ")
                case_name = raw_title[:idx].strip()
                docket = raw_title[idx + 3:].strip()
            elif re.search(r" - \d+", raw_title):
                m = re.search(r" - (\d+.*)", raw_title)
                if m:
                    case_name = raw_title[:m.start()].strip()
                    docket = m.group(1).strip()

            opinions.append({
                "type": "opinion",
                "court": court_name,
                "docket": docket,
                "caption": case_name,
                "author": author,
                "pub_date": pub_date,
                "url": link,
                "guid": guid,
            })
            count += 1

        print(f"    Found {count} opinions")

    return opinions


def _parse_ca3_page(html_text: str, is_precedential: bool) -> list[dict]:
    """Parse a ca3.uscourts.gov recent opinions HTML page.

    Pattern per opinion:
      Filed MM/DD/YYYY, No. XX-XXXX<br />
      <a href="URL">Case Name</a><br />
      Originating District<br />
    """
    opinions = []
    for m in re.finditer(
        r"Filed\s+(\d{2}/\d{2}/\d{4}),\s*No\.\s*([\d-]+)\s*<br\s*/>"
        r"\s*<a\s+href=[\"']([^\"']+)[\"'][^>]*>([^<]+)</a>\s*<br\s*/>"
        r"\s*([^<]+?)\s*<br\s*/>",
        html_text,
    ):
        filing_date = m.group(1)
        docket = m.group(2)
        url = m.group(3)
        caption = html_mod.unescape(m.group(4)).strip()
        origin = html_mod.unescape(m.group(5)).strip()

        dt = datetime.strptime(filing_date, "%m/%d/%Y").replace(tzinfo=timezone.utc)
        pub_date = dt.strftime("%a, %d %b %Y 00:00:00 +0000")

        opinions.append({
            "type": "opinion",
            "court": "Third Circuit",
            "docket": docket,
            "caption": caption,
            "author": "",
            "pub_date": pub_date,
            "url": url,
            "guid": f"ca3-{docket}-{url.split('/')[-1]}",
            "precedential": is_precedential,
            "origin": origin,
        })

    return opinions


def fetch_third_circuit(ctx: ssl.SSLContext) -> list[dict]:
    """Fetch recent Third Circuit opinions from ca3.uscourts.gov."""
    opinions = []

    for label, url, prec in [
        ("precedential", CA3_PREC_URL, True),
        ("non-precedential", CA3_NONPREC_URL, False),
    ]:
        print(f"  Fetching Third Circuit {label} opinions...")
        req = Request(url, headers={"User-Agent": UA})
        try:
            with urlopen(req, timeout=30, context=ctx) as resp:
                html_text = resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            print(f"    ERROR: {e}", file=sys.stderr)
            continue

        page_opinions = _parse_ca3_page(html_text, prec)
        print(f"    Found {len(page_opinions)} {label} opinions")
        opinions.extend(page_opinions)

    return opinions


def _find_scotus_max_docket(ctx: ssl.SSLContext) -> tuple[int, int]:
    """Binary search for the highest current SCOTUS docket number.

    Returns (term, max_number) e.g. (25, 7338).
    """
    now = datetime.now()
    term = now.year % 100 if now.month >= 10 else (now.year - 1) % 100

    lo, hi = 5000, 9999
    last_valid = lo
    while lo <= hi:
        mid = (lo + hi) // 2
        url = SCOTUS_JSON.format(docket=f"{term}-{mid}")
        req = Request(url, headers={"User-Agent": UA})
        try:
            urlopen(req, timeout=5, context=ctx).read()
            last_valid = mid
            lo = mid + 1
        except Exception:
            hi = mid - 1

    return term, last_valid


def fetch_scotus_cert_petitions(ctx: ssl.SSLContext, days_back: int = 30) -> list[dict]:
    """Fetch recent cert petitions from SCOTUS docket that came from the Third Circuit."""
    print("  Fetching SCOTUS cert petitions (Third Circuit)...")

    term, max_num = _find_scotus_max_docket(ctx)
    print(f"    Current max docket: {term}-{max_num}")

    cutoff = datetime.now() - timedelta(days=days_back)
    scan_start = max(max_num - 150, 1)

    petitions = []
    for num in range(scan_start, max_num + 1):
        docket = f"{term}-{num}"
        url = SCOTUS_JSON.format(docket=docket)
        req = Request(url, headers={"User-Agent": UA})
        try:
            resp = urlopen(req, timeout=5, context=ctx)
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception:
            continue

        lower = data.get("LowerCourt") or ""
        if "Third Circuit" not in lower:
            continue

        docketed_str = data.get("DocketedDate", "")
        try:
            dt = datetime.strptime(docketed_str, "%B %d, %Y")
            if dt < cutoff:
                continue
        except (ValueError, TypeError):
            pass

        petitioner = data.get("PetitionerTitle", "").rstrip(", Petitioner").rstrip(", Petitioners")
        respondent = data.get("RespondentTitle", "")
        caption = f"{petitioner} v. {respondent}" if respondent else petitioner
        lower_nums = data.get("LowerCourtCaseNumbers", "")
        case_type = data.get("sJsonCaseType", "")

        docket_url = f"https://www.supremecourt.gov/docket/docketfiles/html/public/{docket}.html"

        filing_date = ""
        try:
            dt = datetime.strptime(docketed_str, "%B %d, %Y")
            filing_date = dt.strftime("%m/%d/%Y")
        except (ValueError, TypeError):
            filing_date = docketed_str

        petitions.append({
            "type": "filing",
            "court": "SCOTUS",
            "docket": docket,
            "caption": caption,
            "status": "Cert Petition",
            "filing_date": filing_date,
            "url": docket_url,
            "lower_court": lower,
            "lower_court_docket": lower_nums,
            "case_type": case_type,
        })

    print(f"    Found {len(petitions)} Third Circuit cert petitions")
    return petitions


def prune_old(filings: list[dict], opinions: list[dict], days: int = 7) -> tuple[list[dict], list[dict]]:
    """Drop entries older than `days` days."""
    from email.utils import parsedate_to_datetime

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    scotus_cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    kept_filings = []
    for f in filings:
        date_str = f.get("filing_date", "")
        try:
            dt = datetime.strptime(date_str, "%m/%d/%Y").replace(tzinfo=timezone.utc)
            threshold = scotus_cutoff if f.get("court") == "SCOTUS" else cutoff
            if dt >= threshold:
                kept_filings.append(f)
        except (ValueError, TypeError):
            kept_filings.append(f)

    kept_opinions = []
    for o in opinions:
        pub = o.get("pub_date", "")
        try:
            dt = parsedate_to_datetime(pub)
            if dt >= cutoff:
                kept_opinions.append(o)
        except Exception:
            kept_opinions.append(o)

    return kept_filings, kept_opinions


def main() -> None:
    print("Fetching PA court data...")
    ctx = _ssl_ctx()

    filings = fetch_ujs_filings(ctx)
    filings.extend(fetch_scotus_cert_petitions(ctx))
    opinions = fetch_rss_opinions(ctx)
    opinions.extend(fetch_third_circuit(ctx))

    filings, opinions = prune_old(filings, opinions, days=7)

    output = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "filings": filings,
        "opinions": opinions,
    }

    out_path = DATA_DIR / "filings.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"\nWrote {len(filings)} filings + {len(opinions)} opinions to {out_path}")


if __name__ == "__main__":
    main()
