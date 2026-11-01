"""Daily apartment finder for Stockholm Bostadsplan.

Reads every estate agent's for-sale list (see agents.py), opens the apartments in
innanför tullarna that may fit your filter, and saves them to data/listings.json:
every showing time, price, size, avgift, floor, build year, description, features and
photos. Each address is placed on OpenStreetMap for its neighbourhood (geo.py).

Once a run, it also reads how many apartments Booli shows for the same filter, as an
answer key for what the finder may have missed. That is one search page; if Booli
refuses it, the count is simply missing that day.

It drives your installed Google Chrome, follows each site's robots.txt, keeps a slow
pace, and stops reading a site that blocks it. Nothing is retried.

    .venv/bin/python scraper/finder.py               # normal run
    .venv/bin/python scraper/finder.py --agents Notar BOSTHLM
    .venv/bin/python scraper/finder.py --show        # watch the browser
"""
import argparse
import datetime as dt
import json
import os
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlencode

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

import agents
import geo
from history import History

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("BOSTADSPLAN_DATA", ROOT / "data"))  # another folder for test runs
AREAS = geo.Areas(ROOT / "areas.geojson")
PAUSE_S = (3, 6)                   # between pages on one agent's site
READ_EVERY = dt.timedelta(hours=20)
AT_ONCE = 4                        # agents read side by side; each site still gets one page at a time
FOLLOW_DAYS = 30                   # how long an apartment that left its agent's list is checked for a final price
# A challenge page, not the notice under a contact form ("skyddas av reCAPTCHA", "skyddas mot missbruk av reCAPTCHA")
BLOCKED = re.compile(r"(?<! av )(?<! by )\b(?:re)?captcha|verify you are human|är du en människa|"
                     r"access denied|unusual traffic", re.I)
DEFAULT_FILTER = {"maxPrice": 4500000, "minRooms": None, "minArea": None, "maxAvgift": None, "notGround": False, "includeSoon": True}
# Booli's own area, used only to read its count for the same filter. Only the whole inner city:
# Booli answers one search a session and refuses the next, so district counts aren't possible.
BOOLI_AREAS = {"Stockholms innerstad": "143"}


# ---------- Files ----------

def read_json(path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, path)


def log(msg):
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line, flush=True)
    with open(DATA / "scraper.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def status(state, msg="", **extra):
    s = read_json(DATA / "status.json", {})
    s.update(state=state, msg=msg, updated=dt.datetime.now().isoformat(timespec="seconds"), **extra)
    write_json(DATA / "status.json", s)


def stamp():
    return dt.datetime.now().isoformat(timespec="seconds")


# ---------- Your filter ----------

def fits(p, f):
    """Whether an apartment matches the filter set in the planner."""
    if p.get("state") == "sold":
        return False
    if p.get("soon") and not f.get("includeSoon", True):
        return False
    if f.get("maxPrice") and p.get("price") and p["price"] > f["maxPrice"]:
        return False
    if f.get("minRooms") and p.get("rooms") and p["rooms"] < f["minRooms"]:
        return False
    if f.get("minArea") and p.get("kvm") and p["kvm"] < f["minArea"]:
        return False
    if f.get("maxAvgift") and p.get("avgift") and p["avgift"] > f["maxAvgift"]:
        return False
    if f.get("notGround") and p.get("floor") in ("BV", "0"):
        return False
    return True


# ---------- The answer key ----------

def booli_query(f, area_id):
    q = {"areaIds": area_id, "objectType": "Lägenhet"}
    if f.get("maxPrice"):
        q["maxListPrice"] = f["maxPrice"]
    if f.get("minRooms"):
        q["minRooms"] = f["minRooms"]
    if f.get("minArea"):
        q["minLivingArea"] = f["minArea"]
    if f.get("maxAvgift"):
        q["maxRent"] = f["maxAvgift"]
    if f.get("notGround"):
        q["floor"] = "aboveBottomFloor"
    return "https://www.booli.se/sok/till-salu?" + urlencode(q)


def booli_count(pw, f):
    """How many apartments Booli shows for the same filter in Stockholms innerstad: one search page,
    in a Chrome window of its own (Booli refuses Chrome without one). If Booli refuses it, there's
    no count that day."""
    out = {"districts": {}, "at": stamp()}
    try:
        try:
            browser = pw.chromium.launch(channel="chrome", headless=False)
        except PlaywrightError:
            browser = pw.chromium.launch(headless=False)
        page = browser.new_page(locale="sv-SE", timezone_id="Europe/Stockholm")
        for i, (name, area_id) in enumerate(BOOLI_AREAS.items()):
            if i:
                time.sleep(random.uniform(20, 40))
            resp = page.goto(booli_query(f, area_id), wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(4000)
            text = page.inner_text("body").replace("\u00a0", " ")
            if (resp is not None and resp.status >= 400) or BLOCKED.search(text):
                log(f"Booli's count: Booli refused the page for {name} ({resp.status if resp else '?'}); no more counts today")
                break
            m = re.search(r"(\d+)\s+till salu(?:\s+och\s+(\d+)\s+snart till salu)?", text)
            if not m:
                log(f"Booli's count: no count shown for {name}")
                continue
            c = {"forSale": int(m.group(1)), "soon": int(m.group(2) or 0)}
            if name == "Stockholms innerstad":
                out.update(c)
            else:
                out["districts"][name] = c
        browser.close()
    except PlaywrightError as e:
        log(f"Booli's count: couldn't load a page ({e})")
    return out if "forSale" in out or out["districts"] else None


def counted(items, f):
    """The apartments to compare with Booli, counted the way Booli counts: each once (two agents
    can list the same one); one without an asking price as coming soon, as Booli shows those,
    unless it's already in bidding;
    and one for sale only when its page states what the filter checks, since Booli can't filter
    on what's missing."""
    seen, out = set(), []
    for p in items.values():
        if p.get("outside") or not fits(p, f):
            continue
        if not p.get("price") and p.get("state") != "bidding":  # in bidding it's for sale, price or not
            p = {**p, "soon": True}
        if not p.get("soon"):
            needs = [("rooms", f.get("minRooms")), ("kvm", f.get("minArea")), ("floor", f.get("notGround")), ("avgift", f.get("maxAvgift"))]
            if any(on and p.get(k) in (None, "") for k, on in needs):
                continue
        key = (re.sub(r"[^a-z0-9]+", "", agents.ascii_lower(p.get("addr", ""))), round(p.get("kvm") or 0))
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


# ---------- Reading the agents ----------

def read_listing(page, url, card, now, places, opens=()):
    """Open one apartment page, opening the sections named in `opens` (facts some agents keep
    folded away). Returns (listing or None, refused?)."""
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2000)
        for _ in range(3):  # galleries load their photos as you scroll
            page.mouse.wheel(0, 2500)
            page.wait_for_timeout(500)
        for label in opens:
            section = page.locator("button, summary").filter(has_text=re.compile(rf"^\s*{label}\s*$"))
            if section.count():
                try:
                    section.first.click(timeout=3000)
                    page.wait_for_timeout(800)
                except PlaywrightError:
                    pass
        text = page.inner_text("body")
    except PlaywrightError as e:
        log(f"Couldn't open {url} ({e})")
        return None, False
    if resp is not None and resp.status in (403, 429):
        return None, True
    if BLOCKED.search(text):
        return None, True
    if resp is not None and resp.status >= 400:
        return None, False
    d = agents.parse_agent_page(text, now)
    d.update(agents.parse_bidding(text))
    h1 = page.locator("h1").first.inner_text() if page.locator("h1").count() else ""
    title = page.title()
    addr = agents.find_address(h1, title, card, text[:600])
    if not addr:
        log(f"No address found on {url}")
        return None, False
    place = places.lookup(addr)
    if not place or not place["inner"]:
        return {"url": url, "addr": addr, "outside": True, "read": stamp()}, False
    d.update(url=url, addr=addr, read=stamp(), photos=agents.photos(page), **{k: place[k] for k in ("lat", "lon", "area", "group")})
    AREAS.rename(d)
    if d["state"] == "sold":
        d["shows"] = []
    d.update({k: v for k, v in agents.parse_card(card).items() if k not in d})
    return d, False


def follow_left(page, store, robots, hist, started):
    """Apartments that left their agent's list: check their page daily for up to a month,
    to record that they sold and, where the agent shows it, the final price."""
    left = store.setdefault("left", {})
    now = dt.datetime.now()
    todo = [u for u, p in left.items() if not p.get("final") and p.get("state") != "gone"
            and now - dt.datetime.fromisoformat(p["left"]) < dt.timedelta(days=FOLLOW_DAYS)]
    for i, url in enumerate(todo, 1):
        p = left[url]
        if not robots.allowed(url):
            continue
        status("running", f"Checking apartments that left the lists, {i} of {len(todo)}", started=started)
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2000)
            text = page.inner_text("body")
        except PlaywrightError as e:
            log(f"Couldn't open {url} ({e})")
            continue
        if resp is not None and resp.status in (404, 410):
            p["state"] = "gone"  # the page was taken down, usually after the sale
        elif (resp is not None and resp.status in (403, 429)) or BLOCKED.search(text):
            log(f"{p.get('agent')} refused {url}; trying again tomorrow")
            continue
        else:
            p.update(agents.parse_bidding(text))
        hist.record(p, stamp(), on_list=False)
        time.sleep(random.uniform(*PAUSE_S))
    # After a month, or once the final price is known, the apartment stays only in the history
    for u in [u for u, p in left.items() if p.get("final") or p.get("state") == "gone"
              or now - dt.datetime.fromisoformat(p["left"]) >= dt.timedelta(days=FOLLOW_DAYS)]:
        del left[u]


def follow(page, store, saved, robots, hist):
    """Every saved apartment, read on every run whatever the filter says: its state,
    the highest bid and, once sold, the final price. Each day's reading is kept."""
    followed = store.setdefault("followed", {})
    for url, name in saved.items():
        if not robots.allowed(url):
            continue
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2500)
            text = page.inner_text("body")
        except PlaywrightError as e:
            log(f"Following {name}: couldn't open the page ({e})")
            continue
        f = followed.setdefault(url, {"addr": name, "history": []})
        if resp is not None and resp.status == 404:
            f["state"] = "gone"  # the agent took the page down, usually soon after the sale
            continue
        if (resp is not None and resp.status in (403, 429)) or BLOCKED.search(text):
            log(f"Following {name}: the site refused the page")
            continue
        b = agents.parse_bidding(text)
        f.update(b, read=stamp())
        known = store["items"].get(url) or store.get("left", {}).get(url)
        if known:
            hist.record({**known, **b}, stamp(), on_list=url in store["items"])
        today, last = stamp()[:10], (f["history"][-1] if f["history"] else {})
        entry = {"at": stamp(), **{k: b[k] for k in ("state", "bid", "bids", "final") if k in b}}
        if last.get("at", "")[:10] == today:
            f["history"][-1] = entry
        elif {k: v for k, v in last.items() if k != "at"} != {k: v for k, v in entry.items() if k != "at"} or not last:
            f["history"].append(entry)
        log(f"Following {name}: {b['state']}" + (f", highest bid {b['bid']:,} kr".replace(",", " ") if b.get("bid") else ""))
        time.sleep(random.uniform(*PAUSE_S))


def launch(pw, show):
    try:  # your installed Google Chrome; Playwright's own Chromium as a fallback
        return pw.chromium.launch(channel="chrome", headless=not show)
    except PlaywrightError:
        return pw.chromium.launch(headless=not show)


def progress(sh, agent=None, text=None):
    """One status line for all agents being read at once."""
    with sh["lock"]:
        if agent:
            if text is None:
                sh["busy"].pop(agent, None)
            else:
                sh["busy"][agent] = text
        now = ", ".join(f"{a} {t}" for a, t in sh["busy"].items())
        status("running", f"Reading estate agents, {sh['done']} of {sh['total']} done" + (f". Now: {now}" if now else ""), started=sh["started"])


def read_agent(ag, sh):
    """One agent's list and apartments, in a browser of its own. Shared data is changed under the lock."""
    items, f, now, robots = sh["items"], sh["f"], sh["now"], sh["robots"]
    try:
        with sync_playwright() as pw:
            browser = launch(pw, sh["show"])
            page = browser.new_page(locale="sv-SE", timezone_id="Europe/Stockholm")
            progress(sh, ag["name"], "(list)")
            cards = agents.discover(ag, page, robots, DATA / "cache", log)
            want = {u: t for u, t in cards.items() if agents.looks_inner(u, t, ag.get("anywhere"))
                    and not ((c := agents.parse_card(t)).get("price") and f.get("maxPrice") and c["price"] > f["maxPrice"] * 1.15)}
            read = kept = 0
            for n, (url, card) in enumerate(want.items(), 1):
                with sh["lock"]:
                    old = items.get(url)
                    if old and (old.get("outside") or old.get("state") == "sold"):
                        continue  # outside the inner city or sold: nothing more to read
                    if old and old.get("read") and now - dt.datetime.fromisoformat(old["read"]) < READ_EVERY:
                        old["seen"] = stamp()
                        kept += not old.get("outside")
                        continue
                if not robots.allowed(url):
                    continue
                progress(sh, ag["name"], f"{n}/{len(want)}")
                p, refused = read_listing(page, url, card, now, sh["places"], ag.get("open", ()))
                if refused:
                    log(f"{ag['name']} refused {url}; skipping the rest of this agent today")
                    with sh["lock"]:
                        sh["notes"].append(f"{ag['name']} refused a page, so some of its apartments may be missing.")
                    break
                read += 1
                with sh["lock"]:
                    if p:
                        p.update(agent=ag["name"], seen=stamp())
                        items[url] = p
                        kept += not p.get("outside")
                        if not p.get("outside"):
                            sh["hist"].record(p, stamp())
                    write_json(DATA / "listings.json", sh["store"])
                time.sleep(random.uniform(*PAUSE_S))
            browser.close()
    except Exception as e:  # one agent failing doesn't stop the others
        log(f"{ag['name']}: stopped early ({e!r})")
        with sh["lock"]:
            sh["notes"].append(f"Reading {ag['name']} stopped early; see data/scraper.log.")
        cards, want, read, kept = {}, {}, 0, 0
    with sh["lock"]:
        sh["report"][ag["name"]] = {"listed": len(cards), "candidates": len(want), "read": read, "inner": kept}
        # Apartments that left an agent's list are usually sold: keep checking them for the final price
        if cards:
            for url in [u for u, p in items.items() if p.get("agent") == ag["name"] and u not in cards
                        and u not in sh["visits"] and u not in sh["saved"]]:
                p = items.pop(url)
                if not p.get("outside"):
                    sh["store"].setdefault("left", {})[url] = {**p, "left": stamp()}
        sh["done"] += 1
    log(f"{ag['name']}: {len(cards)} on the list, {len(want)} inner-city candidates, read {read}, {kept} in the inner city")
    progress(sh, ag["name"], None)


def run(args):
    DATA.mkdir(exist_ok=True)
    lock = DATA / ".scrape.lock"
    if lock.exists() and time.time() - lock.stat().st_mtime < 4 * 3600:
        try:
            os.kill(int(lock.read_text()), 0)
            print("Another run is going.", file=sys.stderr)
            return 1
        except (ValueError, OSError):
            pass  # left behind by a run that was stopped
    lock.write_text(str(os.getpid()))
    try:
        rc = find(args)
    finally:
        lock.unlink(missing_ok=True)
    try:  # travel times and nearby shops for any new apartments
        import travel
        travel.main()
    except Exception as e:
        log(f"Travel: stopped ({e})")
    return rc


def find(args):
    cfg = read_json(DATA / "scrape.json", {})
    f = {**DEFAULT_FILTER, **(cfg.get("filter") or {})}
    if args.max_price:
        f["maxPrice"] = args.max_price
    plan = read_json(DATA / "plan.json", {})
    visits = plan.get("show", {}).get("visits", {})
    # Apartments you saved, by their page on the agent's site
    saved = {a["link"]: a.get("address") or a["link"] for a in plan.get("apts", []) if str(a.get("link", "")).startswith("http")}
    store = read_json(DATA / "listings.json", {})
    if "items" not in store or any(k.startswith(("annons/", "bostad/")) for k in store.get("items", {})):
        store = {"items": {}}  # listings saved by the old Booli scraper are replaced
    items = store["items"]
    for p in items.values():  # neighbourhoods may have been redrawn since the last run
        AREAS.rename(p)
    started, now = stamp(), dt.datetime.now()
    status("running", "Starting", started=started)
    log(f"Run started: filter {json.dumps(f, ensure_ascii=False)}")
    places, robots, hist = geo.Geocoder(DATA, log), agents.Robots(), History(DATA)
    chosen = [a for a in agents.AGENTS if not args.agents or a["name"] in args.agents]
    report, notes = {}, []
    shared = {"items": items, "store": store, "f": f, "now": now, "visits": visits, "saved": saved, "places": places,
              "robots": robots, "hist": hist, "report": report, "notes": notes, "started": started, "show": args.show,
              "lock": threading.Lock(), "busy": {}, "done": 0, "total": len(chosen)}
    try:
        with ThreadPoolExecutor(max_workers=AT_ONCE) as pool:
            list(pool.map(lambda ag: read_agent(ag, shared), chosen))
        with sync_playwright() as pw:
            browser = launch(pw, args.show)
            page = browser.new_page(locale="sv-SE", timezone_id="Europe/Stockholm")
            if saved:
                status("running", f"Following {len(saved)} saved apartment{'s' if len(saved) > 1 else ''}", started=started)
                follow(page, store, saved, robots, hist)
            follow_left(page, store, robots, hist, started)
            if not args.no_count:
                status("running", "Reading Booli's count", started=started)
                store["booli"] = booli_count(pw, f) or {**(store.get("booli") or {}), "missing": stamp()}
            browser.close()
    except Exception as e:  # keep what was read, and say what went wrong
        write_json(DATA / "listings.json", store)
        log(f"Failed: {e!r}")
        status("error", f"The finder stopped early: {str(e)[:160]}", started=started)
        return 3
    cutoff = (now - dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    for p in items.values():  # past showings drop off
        p["shows"] = [s for s in p.get("shows", []) if s["start"] >= cutoff]
    found = counted(items, f)
    by = {}
    for p in found:
        d = by.setdefault(p.get("group") or "Other", {"forSale": 0, "soon": 0})
        d["soon" if p.get("soon") else "forSale"] += 1
    store.update(lastRun=stamp(), filter=f, agents={**store.get("agents", {}), **report},
                 found={"forSale": sum(1 for p in found if not p.get("soon")), "soon": sum(1 for p in found if p.get("soon")), "districts": by})
    hist.export_csv()
    store["history"] = hist.counts()
    hist.close()
    write_json(DATA / "listings.json", store)
    b = store.get("booli") or {}
    msg = f"Found {store['found']['forSale']} for sale and {store['found']['soon']} coming soon."
    msg += f" Booli shows {b['forSale']} and {b['soon']}." if b.get("forSale") is not None and not b.get("missing") else " Booli's count wasn't available today."
    log(f"Run finished. {msg}")
    status("partial" if notes else "done", " ".join([msg] + notes), started=started, finished=store["lastRun"])
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agents", nargs="*", help="only these agents, by name")
    ap.add_argument("--max-price", type=int, help="max price instead of the planner's filter")
    ap.add_argument("--no-count", action="store_true", help="don't read Booli's count")
    ap.add_argument("--show", action="store_true", help="show the browser window")
    sys.exit(run(ap.parse_args()))
