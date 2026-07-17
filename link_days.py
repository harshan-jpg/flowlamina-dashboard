#!/usr/bin/env python3
"""Auto-link Project Time Log work rows to their day's '📊 Daily totals' row.

Each work row links to its date's daily-totals row via the `Day` relation, which
feeds that row's `Total Time` rollup (the per-date summary). /log-day sets this on
rows it creates; this script catches rows added manually in Notion. It runs in the
5am GitHub Actions refresh so manual entries roll up by the next morning.

Idempotent: only touches work rows that have a Date but no Day link. Creates a
daily-totals row for any date that has work but none yet.
"""
import json, os, urllib.request, urllib.error, time

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, "..", ".env")
TL = "962fc42c-7b94-4cac-b3ef-971a405a8f79"


def load_token():
    tok = os.environ.get("NOTION_TOKEN")
    if tok:
        return tok
    if os.path.exists(ENV_PATH):
        for line in open(ENV_PATH, encoding="utf-8"):
            if line.startswith("notion_internal_integration_secret="):
                return line.split("=", 1)[1].strip()
    raise SystemExit("Notion token missing (NOTION_TOKEN or .env)")


H = {"Authorization": f"Bearer {load_token()}", "Notion-Version": "2025-09-03",
     "Content-Type": "application/json"}
DS = f"https://api.notion.com/v1/data_sources/{TL}"


def call(m, u, b=None):
    for _ in range(6):
        try:
            req = urllib.request.Request(u, data=json.dumps(b).encode() if b else None,
                                         headers=H, method=m)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2); continue
            print("HTTP", e.code, e.read()[:200]); return None
        except Exception as ex:
            print("EX", ex); time.sleep(1)
    return None


def title(P):
    return "".join(x["plain_text"] for x in (P.get("Name", {}).get("title") or []))


def dget(P):
    return ((P.get("Date") or {}).get("date") or {}).get("start")


def main():
    rows, cur = [], None
    while True:
        body = {"page_size": 100}
        if cur:
            body["start_cursor"] = cur
        q = call("POST", f"{DS}/query", body)
        rows += q["results"]
        if not q["has_more"]:
            break
        cur = q["next_cursor"]

    totals = {dget(p["properties"])[:10]: p["id"] for p in rows
              if title(p["properties"]) == "📊 Daily totals" and dget(p["properties"])}

    def make_daily(d):
        r = call("POST", "https://api.notion.com/v1/pages", {
            "parent": {"data_source_id": TL},
            "properties": {"Name": {"title": [{"text": {"content": "📊 Daily totals"}}]},
                           "Date": {"date": {"start": d}},
                           "Person": {"select": {"name": "Harshan"}}}})
        return r["id"] if r else None

    linked = created = 0
    for p in rows:
        P = p["properties"]
        if title(P) == "📊 Daily totals":
            continue
        d = dget(P)
        if not d:
            continue
        if (P.get("Day") or {}).get("relation"):     # already linked
            continue
        d = d[:10]
        pid = totals.get(d)
        if not pid:
            pid = make_daily(d)
            if not pid:
                continue
            totals[d] = pid; created += 1; time.sleep(0.34)
        if call("PATCH", f"https://api.notion.com/v1/pages/{p['id']}",
                {"properties": {"Day": {"relation": [{"id": pid}]}}}):
            linked += 1
        time.sleep(0.34)
    print(f"link_days: linked {linked} rows, created {created} daily-totals rows")


if __name__ == "__main__":
    main()
