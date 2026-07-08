#!/usr/bin/env python3
"""
Build data/daily_stats.json — day-level data across every channel, plus a per-entry
time-log list, so the dashboard can re-bucket to day/week/month or any custom range
(and break delivery hours down per project) entirely client-side.

Everything is a raw COUNT or SUM; ratios (view/reply/win rate) are derived in the
browser per bucket so they stay correct at any grouping.

Sources (all day-dated):
  - Upwork DB          -> applications, viewed, connects, connect_cost
  - Proposals DB       -> proposals_drafted, proposals_signed, value_signed
  - Projects Overview  -> jobs won, ATTRIBUTED BY Lead Source (won_upwork / won_coldemail).
                          "Jobs won" = a real engagement (Stage != Lost). Notion is the
                          source of truth for attribution, matching how Harshan tags a job's
                          origin when he logs it. Dated by the Created property, else the
                          row's system created-time.
  - Project Time Log   -> hours (daily total) + a per-entry {date, project, hours} list
                          so the dashboard can compare hours across projects.
  - Instantly daily    -> emails, new_leads, replies, positive

Secrets from .env (hard rules #1/#3/#4): notion_internal_integration_secret,
instantly_api_key. Touches only Notion + Instantly REST — never the Claude API.

Usage:  python3 build_daily_data.py
"""

import json
import os
import urllib.request
from collections import defaultdict
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, os.pardir, ".env")
OUT = os.path.join(HERE, "data", "daily_stats.json")

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"
UPWORK_DS = "27508da8-1c36-80d7-8069-000b0d06461a"
PROJECTS_DS = "23908da8-1c36-80f0-8201-000bfdf634ed"
TIMELOG_DS = "962fc42c-7b94-4cac-b3ef-971a405a8f79"
CONNECT_USD = 0.15  # Upwork connect price; matches the Notion "Cost (USD)" formula.
# Which Lead Source maps to which "jobs won" bucket. Everything else (Referral / Inbound /
# blank / anything future) falls through to won_other — so new channels are tracked automatically.
SRC_KEY = {"Upwork": "won_upwork", "Cold email": "won_coldemail"}

INSTANTLY_DAILY = "https://api.instantly.ai/api/v2/campaigns/analytics/daily"
# api.instantly.ai sits behind Cloudflare — a browser UA is mandatory (else 403/1010).
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HISTORY_START = "2026-01-01"  # comfortably before the first real activity


def die(msg):
    raise SystemExit(f"ERROR: {msg}")


def load_env(path):
    # No .env in CI (GitHub Actions) — secrets come from environment variables there.
    env = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k] = v
    return env


def get_json(url, headers, data=None, method=None):
    req = urllib.request.Request(url, headers=headers,
                                 data=json.dumps(data).encode() if data is not None else None,
                                 method=method)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def notion_pages(token, ds_id):
    """Yield every full page dict from a data source (paginated)."""
    headers = {"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION,
               "Content-Type": "application/json"}
    cursor = None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        res = get_json(f"{NOTION_API}/data_sources/{ds_id}/query", headers, body, "POST")
        for pg in res["results"]:
            yield pg
        if not res.get("has_more"):
            break
        cursor = res["next_cursor"]


def prop(p):
    """Flatten a Notion property to a plain scalar."""
    if not p:
        return None
    t = p.get("type")
    if t in ("title", "rich_text"):
        return "".join(x["plain_text"] for x in p[t])
    if t == "select":
        return p["select"]["name"] if p["select"] else None
    if t == "number":
        return p["number"]
    if t == "date":
        return p["date"]["start"] if p["date"] else None
    if t == "formula":
        return p["formula"].get("number")
    return None


def new_day():
    return dict(applications=0, viewed=0, uw_replies=0, connects=0.0, connect_cost=0.0,
                proposals_drafted=0, proposals_signed=0, value_signed=0.0,
                won_upwork=0, won_coldemail=0, won_other=0, sales_calls=0, hours=0.0, worked=0.0,
                emails=0, new_leads=0, replies=0, positive=0,
                revenue=0.0, expenses=0.0)


def main():
    env = load_env(ENV_PATH)
    # env vars (CI/GitHub Actions) take precedence; fall back to local .env.
    ntoken = os.environ.get("NOTION_TOKEN") or env.get("notion_internal_integration_secret") or die("Notion token missing (NOTION_TOKEN or .env)")
    ikey = os.environ.get("INSTANTLY_KEY") or env.get("instantly_api_key") or die("Instantly key missing (INSTANTLY_KEY or .env)")

    days = defaultdict(new_day)
    timelog = []          # [{date, project, hours}]

    # --- Projects Overview: jobs won attributed by Lead Source + id->name map ---
    proj_name = {}        # dashless page id -> client name (for time-log join)
    for pg in notion_pages(ntoken, PROJECTS_DS):
        p = pg["properties"]
        proj_name[pg["id"].replace("-", "")] = prop(p.get("Client")) or "Untitled"
        # Proposals were MERGED into the Pipeline (2026-07-02): a row with a Proposal Status is a proposal.
        ps = prop(p.get("Proposal Status"))
        if ps:
            dd = (prop(p.get("Created")) or pg["created_time"])[:10]
            days[dd]["proposals_drafted"] += 1
            if ps == "Signed":
                sd = (prop(p.get("Signed")) or prop(p.get("Created")) or pg["created_time"])[:10]
                days[sd]["proposals_signed"] += 1
                days[sd]["value_signed"] += (prop(p.get("Value (AUD)")) or 0)
        # Won = an actually-secured engagement. The Pipeline also holds "Proposal Sent"/blank rows —
        # only Active/Dormant count as wins (Stage "No More" = lost, excluded).
        if prop(p.get("Stage")) not in ("Active", "Dormant"):
            continue
        key = SRC_KEY.get(prop(p.get("Lead Source")), "won_other")  # unknown/blank -> Other
        d = (prop(p.get("Created")) or pg["created_time"])[:10]
        days[d][key] += 1

    # --- Time Log: daily hours total + per-project entries ---
    for pg in notion_pages(ntoken, TIMELOG_DS):
        p = pg["properties"]
        d = prop(p.get("Date"))
        if not d:
            continue
        d = d[:10]
        hrs = prop(p.get("Time")) or 0
        days[d]["hours"] += hrs
        days[d]["worked"] += hrs   # total logged hours (Time Log is now the single input DB)
        days[d]["sales_calls"] += (prop(p.get("Sales Calls/Meetings")) or 0)  # renamed 2026-07-08; on the daily-totals rows
        rel = (p.get("Project") or {}).get("relation") or []
        name = proj_name.get(rel[0]["id"].replace("-", ""), "Unattributed") if rel else "Unattributed"
        if hrs:
            timelog.append({"date": d, "project": name, "hours": hrs})

    # --- Upwork applications ---
    for pg in notion_pages(ntoken, UPWORK_DS):
        p = pg["properties"]
        d = prop(p.get("Date"))
        if not d:
            continue
        b = days[d[:10]]
        b["applications"] += 1
        if prop(p.get("Viewed")) == "Yes":
            b["viewed"] += 1
        if prop(p.get("Replied")) == "Yes":   # client opened a conversation (status Activated/Hired)
            b["uw_replies"] += 1
        conn = (prop(p.get("Connects")) or 0) + (prop(p.get("Bid Connects")) or 0)
        b["connects"] += conn
        b["connect_cost"] += round(conn * CONNECT_USD, 2)

    # (Proposals were merged into the Pipeline 2026-07-02 — counted in the Pipeline loop above.)

    # (Daily Tracker was RETIRED 2026-07-08 — sales_calls + hours now come from the Time Log loop above,
    #  the single CRM input DB. Day metrics live on the "📊 Daily totals" rows.)

    # --- Business Finance: revenue (Category=Revenue, excl inter-biz) + expenses (Direction=Expense) per day ---
    # DB id (not a secret); env var override for CI, else .env, else the known id.
    FINANCE_DS = (os.environ.get("NOTION_FINANCE_DS") or env.get("notion_finance_transactions_ds_id")
                  or "35a08da8-1c36-8015-a165-000b51d3862e")
    if FINANCE_DS:
        for pg in notion_pages(ntoken, FINANCE_DS):
            p = pg["properties"]
            if prop(p.get("Category")) == "Monthly summary":  # skip synthetic summary rows
                continue
            d = prop(p.get("Date"))
            if not d:
                continue
            b = days[d[:10]]
            val = prop(p.get("Value")) or 0
            if prop(p.get("Category")) == "Revenue":
                b["revenue"] += val
            if prop(p.get("Direction")) == "Expense":
                b["expenses"] += abs(val)

    # --- Instantly (aggregated per day; summed defensively) ---
    today = date.today().isoformat()
    ihdr = {"Authorization": f"Bearer {ikey}", "User-Agent": BROWSER_UA}
    for row in get_json(f"{INSTANTLY_DAILY}?start_date={HISTORY_START}&end_date={today}", ihdr):
        d = row.get("date")
        if not d:
            continue
        b = days[d[:10]]
        b["emails"] += int(row.get("sent") or 0)
        b["new_leads"] += int(row.get("new_leads_contacted") or 0)
        b["replies"] += int(row.get("unique_replies") or 0)
        b["positive"] += int(row.get("unique_opportunities") or 0)

    out = {"generated": today,
           "source": "Notion (Upwork/Proposals/Projects/Time Log) + Instantly daily analytics",
           "days": [dict(date=d, **days[d]) for d in sorted(days)],
           "timelog": sorted(timelog, key=lambda x: x["date"])}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=0)
    D = out["days"]
    print(f"Wrote {OUT}: {len(D)} days ({D[0]['date']}..{D[-1]['date']}); "
          f"won UW/CE/other={sum(x['won_upwork'] for x in D)}/"
          f"{sum(x['won_coldemail'] for x in D)}/{sum(x['won_other'] for x in D)}, "
          f"sales_calls={sum(x['sales_calls'] for x in D)}, timelog={len(timelog)}")


if __name__ == "__main__":
    main()
