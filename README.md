# court-feed

Static site tracking PA appellate courts (Supreme, Superior, Commonwealth) and
the Third Circuit: opinions, filings, and argument calendars. Published to
GitHub Pages behind the shared client-side password gate (`auth_gate.py`).

## How it works

| Script | Role |
|--------|------|
| `fetch.py` | Scrapes court sites for opinions/filings → `data/filings.json` |
| `fetch_calendar.py` | Scrapes argument calendars → `data/calendar.json` |
| `build_site.py`, `build_calendar.py` | Render `site/` from the JSON |

Every run re-fetches everything, so a missed run self-heals on the next
successful one — failures cost freshness, never data.

## Automation (`.github/workflows/`)

- **`update.yml`** — hourly at :17 (deliberately off the congested :00 slot,
  where GitHub skips schedules and runner acquisition fails most often).
  Fetch → build → commit → deploy to Pages. `timeout-minutes` on both jobs so
  hangs fail fast.
- **`retry.yml`** — fires when `update.yml` completes with `failure` and
  re-runs the failed jobs after a 90s pause. Capped at **2 automatic retries**
  (`run_attempt < 3`) so genuine breakage can't loop. Built for the two
  transient GitHub-side failure modes observed in production:
  - *"The job was not acquired by Runner"* (July 9, 2026 — job never started)
  - *"Deployment failed, try again later"* from deploy-pages (17 failures,
    July 2–6, 2026 — Pages service instability; scrape jobs all succeeded)

  Verified July 9, 2026 with a deliberate failing workflow: retried twice,
  then correctly stopped.

## Maintenance notes

- A red ✗ on a single run is usually a GitHub infra blip; the retry workflow
  handles it. Investigate only on **repeated** failures of the same attempt
  chain — check the run's *annotations* first (job-level errors like runner
  acquisition don't produce step logs).
- The scrapers have never been the failure cause as of July 2026.
