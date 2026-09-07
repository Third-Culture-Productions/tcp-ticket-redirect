"""
update_redirect.py — regenerate the WIL/WIP evergreen redirect pages.

Self-contained copy that lives in this repo (Third-Culture-Productions/
tcp-ticket-redirect) rather than the private monorepo, specifically so its
GitHub Actions workflow can commit updates using this repo's own default
GITHUB_TOKEN — no cross-repo PAT/secret needed, and this repo never gets
access to anything in the private business repo. The canonical/documented
copy lives at landing-redirect/update_evergreen_redirect.py in
Third-Culture-Productions/third-culture-productions-tour; keep the two in
sync if the logic changes.

Problem this solves: Strategy P's evergreen Meta ads linked directly to
Fienta's *series* page (fienta.com/s/what-is-love), which lists every
upcoming date and forces a "pick a date" step before checkout, which tanked
LP-view-to-checkout conversion and was the actual cause of Strategy P's
revert (2026-09-03) — not the merged/multi-creative concept itself. This
script keeps the "evergreen ad, never edited per cycle" property while
fixing the landing target: it queries Fienta's public series endpoint,
takes the soonest upcoming published event in the series, and regenerates
a tiny static redirect page per brand pointing straight at that event's own
direct checkout URL. /public/events only ever returns active/upcoming
events, so "first result sorted by starts_at" is always correct.

Usage:
    python update_redirect.py
    python update_redirect.py --check-only
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

FIENTA_BASE = "https://fienta.com/api/v1"
OUT_DIR = Path(__file__).resolve().parent

BRANDS = {
    "wil": "what-is-love",
    "wip": "comedy-without-citizenship",
}

PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="0;url={target}">
<link rel="canonical" href="{target}">
<title>Getting your tickets…</title>
</head>
<body>
<script>
(function () {{
  var base = {target_json};
  var qs = window.location.search;
  if (qs) {{
    base += (base.indexOf('?') === -1 ? '?' : '&') + qs.slice(1);
  }}
  window.location.replace(base);
}})();
</script>
<p>Redirecting to tickets… <a href="{target}">click here if nothing happens</a>.</p>
</body>
</html>
"""


def fetch_next_event(series_slug: str) -> dict | None:
    """Return the soonest upcoming published event for a Fienta series, or None."""
    resp = requests.get(
        f"{FIENTA_BASE}/public/events",
        params={"series_id": series_slug, "per_page": 100},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    events = data if isinstance(data, list) else data.get("events", data.get("data", []))
    events = [e for e in events if e.get("is_published", True)]
    if not events:
        return None
    events.sort(key=lambda e: e.get("starts_at", ""))
    return events[0]


def event_checkout_url(event: dict) -> str | None:
    return event.get("url") or event.get("buy_tickets_url") or None


def build_page(target_url: str) -> str:
    return PAGE_TEMPLATE.format(target=target_url, target_json=json.dumps(target_url))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check-only", action="store_true",
        help="Print what would be written without touching disk",
    )
    args = parser.parse_args()

    manifest: dict[str, dict] = {}
    changed = False

    for brand, series_slug in BRANDS.items():
        event = fetch_next_event(series_slug)
        if event is None:
            print(f"[{brand}] No upcoming published event found for series '{series_slug}' — skipping.")
            continue

        target_url = event_checkout_url(event)
        if not target_url:
            print(f"[{brand}] Event {event.get('id')} has no url/buy_tickets_url field — skipping.")
            continue

        starts_at = event.get("starts_at", "")
        out_path = OUT_DIR / f"{brand}.html"
        page_html = build_page(target_url)

        previous = out_path.read_text() if out_path.exists() else None
        if previous != page_html:
            changed = True
            if not args.check_only:
                out_path.write_text(page_html)
            print(f"[{brand}] -> {target_url} (event {event.get('id')}, starts_at={starts_at}) "
                  f"{'[would write]' if args.check_only else '[written]'}")
        else:
            print(f"[{brand}] unchanged -> {target_url}")

        manifest[brand] = {
            "event_id": event.get("id"),
            "starts_at": starts_at,
            "target_url": target_url,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    if not args.check_only:
        (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))

    print("changed:" if changed else "no changes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
