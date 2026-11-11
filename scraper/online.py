"""The planner online: what the Mac sends to Supabase after a run, and the plan it fetches before one.

Needs data/supabase.json (git-ignored, chmod 600), made by you from the Supabase project's API settings:
    {"url": "https://<project>.supabase.co", "anonKey": "<publishable key>", "serviceKey": "<secret key>"}
Without it, nothing here does anything and the planner stays local. The secret key passes row-level security,
so it never leaves this Mac.

    .venv/bin/python scraper/online.py --check   # can we reach the project?
    .venv/bin/python scraper/online.py --pull    # fetch the plan and filter changed online
    .venv/bin/python scraper/online.py --push    # send listings, travel times, floor plans and the price history
"""
import datetime as dt
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
BUCKET = "data"
FILES = ("listings.json", "travel.json", "status.json", "digest.json")
BATCH = 500  # rows per request


def config():
    try:
        c = json.loads((DATA / "supabase.json").read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return c if c.get("url") and c.get("serviceKey") and "<" not in c["serviceKey"] else None


def _call(c, method, path, body=None, headers=None, timeout=60):
    h = {"apikey": c["serviceKey"], "Authorization": "Bearer " + c["serviceKey"], **(headers or {})}
    if isinstance(body, (dict, list)):
        body, h["Content-Type"] = json.dumps(body, ensure_ascii=False).encode("utf-8"), "application/json"
    req = urllib.request.Request(c["url"].rstrip("/") + path, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Supabase answered {e.code} for {method} {path.split('?')[0]}: {e.read()[:200]!r}") from None
    return json.loads(raw) if raw[:1] in (b"{", b"[") else None


def upload(c, name, raw, kind="application/json"):
    _call(c, "POST", f"/storage/v1/object/{BUCKET}/{urllib.parse.quote(name)}", raw,
          {"Content-Type": kind, "x-upsert": "true", "Cache-Control": "no-cache"}, timeout=120)


def upsert(c, table, rows, key):
    for i in range(0, len(rows), BATCH):
        _call(c, "POST", f"/rest/v1/{table}?on_conflict={key}", rows[i:i + BATCH],
              {"Prefer": "resolution=merge-duplicates,return=minimal"})


def state():
    try:
        return json.loads((DATA / "online.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(s):
    (DATA / "online.json").write_text(json.dumps(s, indent=1), "utf-8")


def write_json(path, data):  # as finder.write_json, without loading the finder into the planner's server
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, path)


# ---------- The plan ----------

def push_plan(plan=None, scrape=None, c=None):
    """Your plan and filter, as the local planner just saved them."""
    c = c or config()
    if not c:
        return False
    row = {"id": 1, "updated_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    if plan is not None:
        row["plan"] = plan
    if scrape is not None:
        row["scrape"] = scrape
    upsert(c, "plan", [row], "id")
    s = state()
    s["planPushed"] = row["updated_at"]
    save_state(s)
    return True


def pull_plan(log=print, timeout=60):
    """The plan and filter from Supabase, when they were changed online after this Mac last saw them."""
    c = config()
    if not c:
        return False
    rows = _call(c, "GET", "/rest/v1/plan?id=eq.1&select=plan,scrape,updated_at", timeout=timeout) or []
    if not rows:
        return False
    row, s = rows[0], state()
    seen = max(s.get("planPushed", ""), s.get("planPulled", ""))
    if row["updated_at"] <= seen:
        return False
    if row.get("plan"):
        write_json(DATA / "plan.json", row["plan"])
    if row.get("scrape"):
        write_json(DATA / "scrape.json", row["scrape"])
    s["planPulled"] = row["updated_at"]
    save_state(s)
    log(f"Online: took the plan saved online at {row['updated_at'][:16]}")
    return True


# ---------- After a run ----------

def push(log=print):
    """Listings, travel times, status and digest; floor plans not sent before; the price history since last time."""
    c = config()
    if not c:
        return False
    s = state()
    for name in FILES:
        if (DATA / name).exists():
            upload(c, name, (DATA / name).read_bytes())
    # Floor plans: the index by apartment, as the planner reads it, and each file once
    index = json.loads((DATA / "floorplans" / "index.json").read_text("utf-8")) if (DATA / "floorplans" / "index.json").exists() else {}
    by = {}
    for name, v in index.items():
        by.setdefault(v.get("listing"), []).append(name)
    sent, new = set(s.get("plansSent", [])), 0
    for name in index:
        if name not in sent and (DATA / "floorplans" / name).exists():
            upload(c, f"floorplans/{name}", (DATA / "floorplans" / name).read_bytes(),
                   "image/" + name.rsplit(".", 1)[-1].replace("jpg", "jpeg"))
            sent.add(name)
            new += 1
    upload(c, "floorplans.json", json.dumps(by).encode("utf-8"))
    s["plansSent"] = sorted(sent)
    # The price history: every apartment (a few thousand rows), and the readings from the day before the last push on
    db = sqlite3.connect(f"file:{DATA / 'history.sqlite'}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    apts = [dict(r) for r in db.execute("SELECT * FROM apartments")]
    for a in apts:
        a["features"] = json.loads(a["features"] or "[]")
    since = s.get("readingsFrom", "")
    reads = [dict(r) for r in db.execute("SELECT * FROM readings WHERE day >= ?", (since,))]
    db.close()
    upsert(c, "apartments", apts, "url")
    upsert(c, "readings", reads, "url,day")
    s["readingsFrom"] = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    s["pushed"] = dt.datetime.now().isoformat(timespec="seconds")
    save_state(s)
    log(f"Online: sent {len(FILES)} files, {new} new floor plans, {len(apts)} apartments and {len(reads)} readings")
    return True


def check():
    c = config()
    if not c:
        sys.exit("No data/supabase.json with the project's url and secret key yet.")
    _call(c, "GET", "/rest/v1/plan?select=id")
    _call(c, "POST", f"/storage/v1/object/list/{BUCKET}", {"prefix": "", "limit": 1})
    print(f"Supabase reached: the plan table and the '{BUCKET}' bucket are there.")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    if "--check" in sys.argv:
        check()
    elif "--pull" in sys.argv:
        print("Took the online plan." if pull_plan() else "Nothing newer online.")
    elif "--push" in sys.argv:
        if not push():
            sys.exit("No data/supabase.json yet.")
    else:
        print(__doc__)
