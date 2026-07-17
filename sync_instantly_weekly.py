#!/usr/bin/env python3
"""
Weekly Instantly -> Notion Cold Email sync.

Pulls one week's campaign analytics from Instantly (aggregated across every
campaign), then creates or updates a single weekly row in the Cold Email Notion
database with Start Date, End Date, Emails Sent, Opportunities and Reply Rate (%).

Design notes (the *why*):
- One aggregated row per week (Mon->Sun) — matches how the DB is curated by hand.
- Reply Rate uses *unique* replies (reply_count_unique) — the conservative,
  one-reply-per-lead basis Harshan asked for.
- Idempotent: matches an existing row by Start Date and updates it in place, so
  re-runs (or the manual row already present for a week) never duplicate.
- Stdlib only (urllib/json/zoneinfo) so launchd can run it with /usr/bin/python3
  and no virtualenv.
- Secrets come from .env and are never printed (workspace hard rules 3 & 4).
- Touches only Instantly + Notion — never the Anthropic/Claude API (hard rule 1).

Usage:
    python3 sync_instantly_weekly.py                 # current Mon->Sun week (ending most recent Sunday)
    python3 sync_instantly_weekly.py --start 2026-05-18 --end 2026-05-23
    python3 sync_instantly_weekly.py --dry-run       # fetch + aggregate + query, but don't write
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# --- Constants -------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, os.pardir, os.pardir))
# Secrets: prefer a .env sitting next to the script (used by the relocated,
# launchd-run copy under ~/Library, which can't reach the Desktop-protected
# project .env), otherwise fall back to the project-root .env (manual runs).
_SIBLING_ENV = os.path.join(SCRIPT_DIR, ".env")
ENV_PATH = _SIBLING_ENV if os.path.exists(_SIBLING_ENV) else os.path.join(PROJECT_ROOT, ".env")
# Log lives in ~/Library/Logs (NOT under ~/Desktop): Desktop is TCC-protected,
# so a launchd-spawned process can't write there. Keeping one log path for both
# manual and scheduled runs means a single reliable trail.
LOG_PATH = os.path.join(os.path.expanduser("~"), "Library", "Logs", "com.flowlamina.instantly-weekly-sync.log")

TZ = ZoneInfo("Australia/Sydney")

INSTANTLY_ANALYTICS_URL = "https://api.instantly.ai/api/v2/campaigns/analytics"
# api.instantly.ai sits behind Cloudflare; the default urllib UA gets a 403 +
# CF error 1010. A browser UA is mandatory (auto-memory: instantly-api-user-agent).
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"
COLD_EMAIL_DS_ID = "33f08da8-1c36-80d7-88e3-000b97b7beae"

# Notion property names (verified against the live data source schema).
P_START = "Start Date"
P_END = "End Date"
P_EMAILS = "Emails Sent"
P_OPPS = "Opportunities"
P_REPLY_RATE = "Reply Rate (%)"


# --- Helpers ---------------------------------------------------------------

def load_env(path):
    """Parse a simple KEY=VALUE .env file. No external deps.

    Returns {} if the file is absent (e.g. in CI / GitHub Actions, where secrets
    come from environment variables instead)."""
    if not os.path.exists(path):
        return {}
    env = {}
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def die(msg):
    """Fail loudly (hard rule 3) — non-zero exit so launchd surfaces it."""
    log(f"ERROR: {msg}")
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def log(msg):
    stamp = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {msg}"
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass  # never let logging failure break the sync


def week_window(start_arg, end_arg):
    """Return (start, end) date strings YYYY-MM-DD.

    Default = the Mon->Sun week ending on the most recent Sunday, inclusive of
    today. A Sunday run therefore captures the week that is just ending (sends
    only happen Mon-Thu, so the week's data is complete by Sunday).
    """
    if start_arg and end_arg:
        return start_arg, end_arg
    if start_arg or end_arg:
        die("Pass both --start and --end, or neither.")
    today = datetime.now(TZ).date()
    days_since_sunday = (today.weekday() - 6) % 7  # Mon=0..Sun=6 -> Sunday=0
    end = today - timedelta(days=days_since_sunday)
    start = end - timedelta(days=6)
    return start.isoformat(), end.isoformat()


def http_json(req):
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500]
        die(f"{req.get_method()} {req.full_url} -> HTTP {exc.code}: {body}")
    except urllib.error.URLError as exc:
        die(f"{req.get_method()} {req.full_url} -> {exc.reason}")


# --- Instantly -------------------------------------------------------------

def fetch_instantly(api_key, start, end):
    qs = urllib.parse.urlencode({"start_date": start, "end_date": end})
    req = urllib.request.Request(
        f"{INSTANTLY_ANALYTICS_URL}?{qs}",
        headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent": BROWSER_UA,
            "Content-Type": "application/json",
        },
        method="GET",
    )
    data = http_json(req)
    if not isinstance(data, list):
        die(f"Unexpected Instantly response (expected a list): {str(data)[:300]}")
    return data


def aggregate(campaigns):
    emails = sum(int(c.get("emails_sent_count") or 0) for c in campaigns)
    replies = sum(int(c.get("reply_count_unique") or 0) for c in campaigns)
    opps = sum(int(c.get("total_opportunities") or 0) for c in campaigns)
    reply_rate = round(100 * replies / emails, 2) if emails else 0.0
    return emails, replies, opps, reply_rate


# --- Notion ----------------------------------------------------------------

def notion_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def find_row(token, start):
    """Return an existing page id whose Start Date == start, or None."""
    payload = {"filter": {"property": P_START, "date": {"equals": start}}, "page_size": 1}
    req = urllib.request.Request(
        f"{NOTION_API}/data_sources/{COLD_EMAIL_DS_ID}/query",
        data=json.dumps(payload).encode("utf-8"),
        headers=notion_headers(token),
        method="POST",
    )
    data = http_json(req)
    results = data.get("results", [])
    return results[0]["id"] if results else None


def metric_props(end, emails, opps, reply_rate):
    return {
        P_END: {"date": {"start": end}},
        P_EMAILS: {"number": emails},
        P_OPPS: {"number": opps},
        P_REPLY_RATE: {"number": reply_rate},
    }


def update_row(token, page_id, end, emails, opps, reply_rate):
    req = urllib.request.Request(
        f"{NOTION_API}/pages/{page_id}",
        data=json.dumps({"properties": metric_props(end, emails, opps, reply_rate)}).encode("utf-8"),
        headers=notion_headers(token),
        method="PATCH",
    )
    http_json(req)


def create_row(token, start, end, emails, opps, reply_rate):
    props = {P_START: {"date": {"start": start}}}
    props.update(metric_props(end, emails, opps, reply_rate))
    payload = {
        "parent": {"type": "data_source_id", "data_source_id": COLD_EMAIL_DS_ID},
        "properties": props,
    }
    req = urllib.request.Request(
        f"{NOTION_API}/pages",
        data=json.dumps(payload).encode("utf-8"),
        headers=notion_headers(token),
        method="POST",
    )
    return http_json(req).get("id")


# --- Main ------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Sync one week of Instantly analytics into the Cold Email Notion DB.")
    parser.add_argument("--start", help="Window start YYYY-MM-DD (Monday). Requires --end.")
    parser.add_argument("--end", help="Window end YYYY-MM-DD (Sunday). Requires --start.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch, aggregate and query Notion, but do not write.")
    args = parser.parse_args()

    env = load_env(ENV_PATH)
    # env vars (CI / GitHub Actions) win; fall back to the local .env.
    instantly_key = os.environ.get("INSTANTLY_KEY") or env.get("instantly_api_key") or die("Instantly key missing (INSTANTLY_KEY or .env)")
    notion_token = os.environ.get("NOTION_TOKEN") or env.get("notion_internal_integration_secret") or die("Notion token missing (NOTION_TOKEN or .env)")

    start, end = week_window(args.start, args.end)

    campaigns = fetch_instantly(instantly_key, start, end)
    emails, replies, opps, reply_rate = aggregate(campaigns)
    summary = (f"week {start}..{end} | campaigns={len(campaigns)} | emails={emails} | "
               f"replies(unique)={replies} | opps={opps} | reply_rate={reply_rate}%")

    # Querying Notion also proves the integration can see the DB.
    existing = find_row(notion_token, start)

    if args.dry_run:
        action = "would update" if existing else "would create"
        log(f"DRY-RUN | {summary} | {action}")
        print(f"DRY-RUN | {summary} | {action} (row {'exists' if existing else 'absent'})")
        return

    if existing:
        update_row(notion_token, existing, end, emails, opps, reply_rate)
        action = "updated"
    else:
        create_row(notion_token, start, end, emails, opps, reply_rate)
        action = "created"

    log(f"OK | {summary} | {action}")
    print(f"OK | {summary} | row {action}")


if __name__ == "__main__":
    main()
