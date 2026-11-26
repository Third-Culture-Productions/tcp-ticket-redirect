"""
update_links_page.py — regenerate the link-in-bio page served at go.nyamean.com/.

Replaces Linktree (whose API/MCP is Premium-only) with a static page that keeps
its own show list current: upcoming solo-tour dates and Berlin lineup dates come
straight from Fienta's public events endpoint, everything else (socials, podcast,
Show Alerts, the WIL series link) from links.json. Runs daily from the same
workflow as update_redirect.py, so a new Fienta event appears and a past one
drops off without anyone touching it.

Show selection: /public/events?organizer=23136 (no token needed — this repo is
public and must never hold credentials), then filtered client-side rather than
trusting the query string — Fienta's public endpoint has silently ignored filter
params before (series_id did, until 2026-08). Kept: organizer 23136, series id
starting with links.json's solo_series_prefix OR a title starting "American-ish
Immigrant" (most S1 2027 events have no series slug — their series_id is just
the event id, and they were silently dropped until 2026-11-23), public, on sale
or sold out, dated today or later in Berlin time, and not in exclude_event_ids
(for a cut show whose Fienta event is still on sale — 190615 = Basel 2026-12-09).

Berlin lineup shows (WIL/WIP/Comfort, links.json berlin_shows) pass the same
filters and are listed by date under the "Berlin Shows" heading, where
links.json's {"berlin_shows": true} marker sits (since 2026-11-26; before that
Berlin had only the fixed WIL series link, which stays as the catch-all).

Every Fienta link carries utm_source=golinks&utm_medium=btn so ticket sales from
this page stay separable from Linktree's (utm_source=linktree) in the
show-tracker's source attribution.

Usage:
    python update_links_page.py
    python update_links_page.py --check-only
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests

FIENTA_BASE = "https://fienta.com/api/v1"
ORGANIZER_ID = 23136
OUT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = OUT_DIR / "links.json"
ICON_DIR = OUT_DIR / "assets" / "icons"
SITE_URL = "https://go.nyamean.com/"
BERLIN = ZoneInfo("Europe/Berlin")

SHOWN_SALE_STATUSES = {"onSale", "soldOut"}
# "American -ish Immigrant | ..." (S2 2026) and "American-ish Immigrant: Still Undecided | ..." (S1 2027)
SOLO_TITLE_RE = re.compile(r"^\s*American\s*-\s*ish\s+Immigrant\b", re.IGNORECASE)
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{name} | Tickets, Tour Dates & Podcast</title>
<meta name="description" content="{bio}">
<meta property="og:title" content="{name}">
<meta property="og:description" content="{bio}">
<meta property="og:image" content="{site_url}{avatar}">
<meta property="og:url" content="{site_url}">
<link rel="canonical" href="{site_url}">
<link rel="icon" href="{avatar}">
<link rel="preload" href="assets/oswald-400.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="assets/oswald-500.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="assets/oswald-600.woff2" as="font" type="font/woff2" crossorigin>
{analytics}
<style>
@font-face {{ font-family: "Oswald"; font-weight: 400; font-display: swap; src: url(assets/oswald-400.woff2) format("woff2"); }}
@font-face {{ font-family: "Oswald"; font-weight: 500; font-display: swap; src: url(assets/oswald-500.woff2) format("woff2"); }}
@font-face {{ font-family: "Oswald"; font-weight: 600; font-display: swap; src: url(assets/oswald-600.woff2) format("woff2"); }}
:root {{
  --bg: #7b746c;  /* Linktree's #958e85, darkened for 4.5:1 white-text contrast */
  --text: #ffffff;
  --button: #f4e860;
  --button-text: #010101;
  --shadow: #000000;
  --muted: #ffffff;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: "Oswald", "Arial Narrow", Arial, sans-serif;
  -webkit-font-smoothing: antialiased;
}}
main {{ max-width: 580px; margin: 0 auto; padding: 40px 16px 56px; }}
header {{ text-align: center; }}
.avatar {{ width: 96px; height: 96px; border-radius: 50%; object-fit: cover; display: block; margin: 0 auto 14px; }}
h1 {{ font-size: 1.4rem; font-weight: 600; margin: 0 0 6px; letter-spacing: 0.01em; }}
.bio {{ font-size: 1rem; font-weight: 400; line-height: 1.45; margin: 0 auto; max-width: 420px; }}
.socials {{ display: flex; flex-wrap: wrap; justify-content: center; gap: 2px; margin: 16px 0 28px; padding: 0; list-style: none; }}
.socials a {{ display: flex; padding: 7px; color: var(--text); border-radius: 50%; }}
.socials svg {{ width: 26px; height: 26px; fill: currentColor; }}
h2 {{ font-size: 1.05rem; font-weight: 500; text-align: center; margin: 40px 0 16px; letter-spacing: 0.04em; text-transform: uppercase; }}
ul.links {{ list-style: none; margin: 0; padding: 0; display: grid; gap: 20px; }}
ul.links a {{
  display: block;
  min-height: 56px;
  padding: 14px 20px;
  background: var(--button);
  color: var(--button-text);
  text-decoration: none;
  text-align: center;
  border: 2px solid var(--shadow);
  border-radius: 999px;
  box-shadow: 4px 4px 0 var(--shadow);
  font-size: 1rem;
  font-weight: 500;
  line-height: 1.3;
  overflow-wrap: anywhere;
  transition: transform 0.08s ease, box-shadow 0.08s ease;
}}
ul.links a:hover {{ transform: translate(2px, 2px); box-shadow: 2px 2px 0 var(--shadow); }}
ul.links a:focus-visible, .socials a:focus-visible {{ outline: 3px solid var(--text); outline-offset: 3px; }}
.show-date {{ display: block; font-size: 1.08rem; font-weight: 600; }}
.show-meta {{ display: block; font-size: 0.86rem; font-weight: 400; margin-top: 2px; }}
.badge {{ display: inline-block; margin-left: 6px; padding: 1px 8px; border-radius: 999px; background: var(--button-text); color: var(--button); font-size: 0.75rem; font-weight: 500; vertical-align: 2px; }}
.empty {{ text-align: center; color: var(--muted); margin: 0 0 4px; }}
ul.links.tour-page {{ margin-top: 28px; }}
footer {{ text-align: center; color: var(--muted); font-size: 0.8rem; margin-top: 40px; line-height: 1.8; }}
footer a {{ color: var(--text); }}
@media (prefers-reduced-motion: reduce) {{ ul.links a {{ transition: none; }} }}
</style>
</head>
<body>
<main>
<header>
<img class="avatar" src="{avatar}" alt="{name}" width="96" height="96">
<h1>{name}</h1>
<p class="bio">{bio}</p>
<ul class="socials">
{socials}
</ul>
</header>
<ul class="links">
{links_before}
</ul>
<h2>Upcoming Shows</h2>
{shows}
<ul class="links tour-page">
{tour_page}
</ul>
{links_after}
<footer>&copy; {year} Third Culture Productions<br>
<a href="https://www.nyamean.com/impressum">Impressum</a> · <a href="https://www.nyamean.com/datenschutzerklarung">Datenschutz</a></footer>
</main>
</body>
</html>
"""


def berlin_show_of(event: dict, config: dict) -> dict | None:
    """The links.json berlin_shows entry this event belongs to (series slug or title), else None."""
    series, title = str(event.get("series_id") or ""), (event.get("title") or "").lower()
    for entry in config.get("berlin_shows", []):
        if series.startswith(entry["series_prefix"]) or title.startswith(entry["title_prefix"].lower()):
            return entry
    return None


def fetch_events(config: dict, today: str) -> tuple[list[dict], list[dict]]:
    """Return (solo-tour, Berlin lineup) upcoming on-sale events for the organizer, soonest first."""
    resp = requests.get(
        f"{FIENTA_BASE}/public/events",
        params={"organizer": ORGANIZER_ID, "per_page": 200},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    events = data if isinstance(data, list) else data.get("events", data.get("data", []))

    prefix = config["solo_series_prefix"]
    excluded = set(config.get("exclude_event_ids", []))
    solo, berlin = [], []
    for e in events:
        is_solo = (str(e.get("series_id") or "").startswith(prefix)
                   or bool(SOLO_TITLE_RE.match(e.get("title") or "")))
        is_berlin = not is_solo and berlin_show_of(e, config) is not None
        reason = None
        if int(e.get("organizer_id") or 0) != ORGANIZER_ID:
            reason = f"organizer {e.get('organizer_id')}"
        elif not (is_solo or is_berlin):
            reason = "neither a solo show nor a links.json berlin_shows series"
        elif e.get("id") in excluded:
            reason = "in exclude_event_ids"
        elif not e.get("is_public", True):
            reason = "not public"
        elif e.get("sale_status") not in SHOWN_SALE_STATUSES:
            reason = f"sale_status={e.get('sale_status')}"
        elif (e.get("starts_at") or "")[:10] < today:
            reason = "in the past"
        elif not e.get("url"):
            reason = "no url"
        if reason:
            print(f"  skip {e.get('id')} {e.get('starts_at')} {e.get('title')!r}: {reason}")
            continue
        (solo if is_solo else berlin).append(e)
    solo.sort(key=lambda e: e["starts_at"])
    berlin.sort(key=lambda e: e["starts_at"])
    return solo, berlin


def city_and_note(event: dict, config: dict) -> tuple[str, str]:
    """'... in Koln' -> ('Köln', ''); '... in Tallinn (Early Show)' -> ('Tallinn', 'Early Show').
    City names go through links.json city_names; the note separates same-day double shows."""
    m = re.search(r"\bin ([^|]+)$", event.get("title") or "")
    if m:
        city = m.group(1).strip()
    else:
        city = str(event["series_id"]).removeprefix(config["solo_series_prefix"]).strip("-").replace("-", " ").title()
    note = ""
    n = re.match(r"^(.*?)\s*\(([^)]+)\)$", city)
    if n:
        city, note = n.group(1).strip(), n.group(2).strip()
    return config.get("city_names", {}).get(city, city), note


def city_of(event: dict, config: dict) -> str:
    return city_and_note(event, config)[0]


def venue_of(event: dict, config: dict) -> str:
    """Fienta venue names can carry a '| tagline' (Porto) — drop it, then apply overrides."""
    venue = str(event.get("venue") or "").split(" | ")[0].strip()
    return config.get("venue_names", {}).get(venue, venue)


def with_utm(url: str, campaign: str) -> str:
    query = urlencode({"utm_source": "golinks", "utm_medium": "btn", "utm_campaign": campaign})
    return url + ("&" if "?" in url else "?") + query


def event_attr(name: str) -> str:
    """Umami click-event label (cookieless, see analytics_tag). Umami caps names at 50 chars."""
    name = re.sub(r"^\W+", "", name).strip()[:50]  # drop leading emoji, e.g. '📍 Show Alerts'
    return f' data-umami-event="{html.escape(name)}"'


def link_item(url: str, inner_html: str, event_name: str) -> str:
    return f'<li><a href="{html.escape(url)}"{event_attr(event_name)}>{inner_html}</a></li>'


def analytics_tag(config: dict) -> str:
    """Umami Cloud (EU, Hobby plan): no cookies or device storage, so no consent banner.
    data-domains limits counting to the live site, so local previews don't pollute stats."""
    website_id = config.get("umami_website_id")
    if not website_id:
        return ""
    return (
        '<script defer src="https://cloud.umami.is/script.js" '
        f'data-website-id="{html.escape(website_id)}" data-domains="go.nyamean.com"></script>'
    )


def render_show(event: dict, config: dict) -> str:
    start = datetime.strptime(event["starts_at"], "%Y-%m-%d %H:%M:%S")
    city, note = city_and_note(event, config)
    date_label = f"{WEEKDAYS[start.weekday()]} {start.day} {MONTHS[start.month - 1]}"
    badge = '<span class="badge">Sold out</span>' if event.get("sale_status") == "soldOut" else ""
    meta = " · ".join(p for p in (venue_of(event, config), start.strftime("%H:%M"), note) if p)
    ascii_city = unicodedata.normalize("NFKD", city).encode("ascii", "ignore").decode()  # Köln -> Koln
    campaign = f"{re.sub(r'[^a-z0-9]+', '', ascii_city.lower())}-{start.day}{MONTHS[start.month - 1].lower()}"
    inner = (
        f'<span class="show-date">{html.escape(date_label)} · {html.escape(city)}{badge}</span>'
        f'<span class="show-meta">{html.escape(meta)}</span>'
    )
    label = f"Show: {city} {date_label.split(' ', 1)[1]}" + (f" {note}" if note else "")
    return link_item(with_utm(event["url"], campaign), inner, label)


def render_berlin_show(event: dict, config: dict) -> str:
    """'Tue 8 Dec · What is Love?' / 'SaliGari Bar · 20:00'; campaign berlin-<code>-<d><mon>."""
    entry = berlin_show_of(event, config)
    start = datetime.strptime(event["starts_at"], "%Y-%m-%d %H:%M:%S")
    day_month = f"{start.day} {MONTHS[start.month - 1]}"
    date_label = f"{WEEKDAYS[start.weekday()]} {day_month}"
    badge = '<span class="badge">Sold out</span>' if event.get("sale_status") == "soldOut" else ""
    meta = " · ".join(p for p in (venue_of(event, config), start.strftime("%H:%M")) if p)
    campaign = f"berlin-{entry['code']}-{start.day}{MONTHS[start.month - 1].lower()}"
    inner = (
        f'<span class="show-date">{html.escape(date_label)} · {html.escape(entry["name"])}{badge}</span>'
        f'<span class="show-meta">{html.escape(meta)}</span>'
    )
    return link_item(with_utm(event["url"], campaign), inner, f"Berlin: {entry['name']} {day_month}")


def render_links(items: list[dict], berlin_events: list[dict], config: dict) -> str:
    """Render a links.json list; {"section": ...} entries start a new headed group and
    {"berlin_shows": true} expands to the dated Berlin lineup shows (nothing if none)."""
    out: list[str] = []
    open_list = False
    for item in items:
        if "section" in item:
            if open_list:
                out.append("</ul>")
            out.append(f"<h2>{html.escape(item['section'])}</h2>")
            out.append('<ul class="links">')
            open_list = True
            continue
        if not open_list:
            out.append('<ul class="links">')
            open_list = True
        if item.get("berlin_shows"):
            out.extend(render_berlin_show(e, config) for e in berlin_events)
            continue
        url = with_utm(item["url"], item["utm_campaign"]) if item.get("utm_campaign") else item["url"]
        out.append(link_item(url, html.escape(item["title"]), item["title"]))
    if open_list:
        out.append("</ul>")
    return "\n".join(out)


def render_social(item: dict) -> str:
    svg = (ICON_DIR / f"{item['icon']}.svg").read_text().strip()
    svg = re.sub(r"<title>.*?</title>", "", svg)
    svg = svg.replace("<svg ", '<svg aria-hidden="true" focusable="false" ', 1)
    label = html.escape(item["label"])
    return (
        f'<li><a href="{html.escape(item["url"])}" aria-label="{label}" title="{label}"'
        f'{event_attr("Social: " + item["label"])}>{svg}</a></li>'
    )


def build_page(config: dict, events: list[dict], berlin_events: list[dict], year: int) -> str:
    profile = config["profile"]
    if events:
        shows = '<ul class="links">\n' + "\n".join(render_show(e, config) for e in events) + "\n</ul>"
    else:
        shows = '<p class="empty">New dates coming soon. Tap Show Alerts above to hear first.</p>'
    tour_page = config["tour_page"]
    return PAGE_TEMPLATE.format(
        name=html.escape(profile["name"]),
        bio=html.escape(profile["bio"]),
        avatar=html.escape(profile["avatar"]),
        site_url=SITE_URL,
        socials="\n".join(render_social(s) for s in config["socials"]),
        analytics=analytics_tag(config),
        links_before="\n".join(
            link_item(i["url"], html.escape(i["title"]), i["title"]) for i in config["links_before_shows"]
        ),
        shows=shows,
        tour_page=link_item(tour_page["url"], html.escape(tour_page["title"]), tour_page["title"]),
        links_after=render_links(config["links_after_shows"], berlin_events, config),
        year=year,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check-only", action="store_true",
        help="Print what would be written without touching disk",
    )
    args = parser.parse_args()

    config = json.loads(CONFIG_PATH.read_text())
    now = datetime.now(BERLIN)
    events, berlin_events = fetch_events(config, now.date().isoformat())
    for e in events:
        print(f"  show {e['starts_at']} {city_of(e, config)} ({e['id']}, {e['sale_status']})")
    for e in berlin_events:
        print(f"  berlin {e['starts_at']} {berlin_show_of(e, config)['name']} ({e['id']}, {e['sale_status']})")

    page_html = build_page(config, events, berlin_events, now.year)
    out_path = OUT_DIR / "index.html"
    previous = out_path.read_text() if out_path.exists() else None
    if previous == page_html:
        print(f"[links] unchanged ({len(events)} shows, {len(berlin_events)} Berlin)")
    else:
        if not args.check_only:
            out_path.write_text(page_html)
        print(f"[links] {len(events)} shows, {len(berlin_events)} Berlin "
              f"{'[would write]' if args.check_only else '[written]'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
