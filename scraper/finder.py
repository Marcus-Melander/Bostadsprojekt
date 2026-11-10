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
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlencode

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

import agents
import plans
import reports
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

# The latest price per m² for bostadsrätter in each district, from Svensk Mäklarstatistik (the planner's
# Områdespriser, three months to 8 October 2026), for estimating what an apartment without a price would cost
DISTRICT_KVM_PRICE = {"Södermalm": 116680, "Gamla stan": 116680, "Kungsholmen": 112452, "Vasastan": 135782, "Norrmalm": 135782,
                      "Östermalm": 129172, "Gärdet": 129172}
INNER_KVM_PRICE = 121933
PRICEY_MARGIN = 1.1  # the estimate comes from final prices, and asking prices are often set a little lower


def estimate_price(p):
    return round(DISTRICT_KVM_PRICE.get(p.get("group"), INNER_KVM_PRICE) * p["kvm"] / 50000) * 50000 if p.get("kvm") else 0


def fits(p, f, estimate=True):
    """Whether an apartment matches the filter set in the planner. Without an asking price, it's left out when its
    estimated price (the district's price per m² times its size) is more than 10 % above the max price; `estimate=False` skips
    that, for counting the way Booli counts."""
    if p.get("state") == "sold":
        return False
    if p.get("soon") and not f.get("includeSoon", True):
        return False
    if f.get("maxPrice") and p.get("price") and p["price"] > f["maxPrice"]:
        return False
    if f.get("minPrice") and p.get("price") and p["price"] < f["minPrice"]:
        return False
    if estimate and f.get("skipPricey", True) and not p.get("price") and f.get("maxPrice") and estimate_price(p) > f["maxPrice"] * PRICEY_MARGIN:
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
    # Since November 2026 Booli shows one count for both for sale and coming soon, so its coming-soon ones are hidden
    # ("Dölj snart till salu") and the page counts those for sale, the number ours is compared with
    q = {"areaIds": area_id, "objectType": "Lägenhet", "upcomingSale": "0"}
    if f.get("minPrice"):
        q["minListPrice"] = f["minPrice"]
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
            resp = agents.goto(page, booli_query(f, area_id))
            page.wait_for_timeout(4000)
            text = page.inner_text("body").replace("\u00a0", " ")
            if (resp is not None and resp.status >= 400) or BLOCKED.search(text):
                log(f"Booli's count: Booli refused the page for {name} ({resp.status if resp else '?'}); no more counts today")
                break
            m = re.search(r"(\d+)\s+till salu(?:\s+och\s+(\d+)\s+snart till salu)?", text)
            tab = re.search(r"\bTill salu\s*(\d+)", text)  # the newer page: "Till salu 133" on the tab above the results
            if m:
                c = {"forSale": int(m.group(1)), "soon": int(m.group(2) or 0)}
            elif tab:
                c = {"forSale": int(tab.group(1)), "soon": None}
            else:
                log(f"Booli's count: no count shown for {name}")
                continue
            if name == "Stockholms innerstad":
                out.update(c, apartments=booli_apartments(page.content()))
            else:
                out["districts"][name] = c
        browser.close()
    except PlaywrightError as e:
        log(f"Booli's count: couldn't load a page ({e})")
    return out if "forSale" in out or out["districts"] else None


def booli_apartments(html):
    """The for-sale apartments on Booli's search page (its first page of results, about 35), from the data
    the page carries: address, m², asking price as shown, and agent. Projects and sold ones are left out."""
    out, dec = {}, json.JSONDecoder()
    for m in re.finditer(r'"items":\[\{"__typename":"ListableProperty"', html):
        try:
            arr, _ = dec.raw_decode(html, m.start() + len('"items":'))
        except ValueError:
            continue
        for it in arr:
            if not str(it.get("originId", "")).startswith("listing:"):
                continue
            dp = {d.get("key"): (d.get("value") or {}).get("plainText", "") for d in (it.get("displayAttributes") or {}).get("dataPoints", [])}
            kvm = re.search(r"\d+(?:,\d+)?", dp.get("livingArea", ""))
            out[it["originId"]] = {"addr": it.get("title", ""), "kvm": float(kvm.group().replace(",", ".")) if kvm else None,
                                   "price": it.get("displayPrice", ""), "agent": (it.get("presenter") or {}).get("name", ""),
                                   "url": "https://www.booli.se" + it.get("url", "")}
    return list(out.values())


def booli_matched(apts, items, f):
    """Each of Booli's apartments with what we have for it: "found" (for sale here too), "unpriced" (here, without
    an asking price), "filtered" (here, but outside the filter or the inner city as we read it) or "missing".
    Matched on address and m² (within 2), since an address can hold several apartments."""
    key = lambda a: re.sub(r"[^a-z0-9]+", "", agents.ascii_lower((a or "").split(",")[0]))
    by = {}
    for p in items.values():
        by.setdefault(key(p.get("addr")), []).append(p)
    out = []
    for a in apts:
        ours = [p for p in by.get(key(a["addr"]), []) if a.get("kvm") is None or abs((p.get("kvm") or 0) - a["kvm"]) <= 2]
        if not ours:
            why = "missing"
        elif any(p.get("price") and not p.get("outside") and fits(p, f, estimate=False) for p in ours):
            why = "found"
        elif any(not p.get("price") and not p.get("outside") for p in ours):
            why = "unpriced"
        else:
            why = "filtered"
        out.append({**a, "here": why})
    return out


def counted(items, f):
    """The apartments to compare with Booli, counted the way Booli counts: each once (two agents
    can list the same one); one without an asking price as coming soon, as Booli shows those,
    unless it's already in bidding;
    and one for sale only when its page states what the filter checks, since Booli can't filter
    on what's missing."""
    seen, out = set(), []
    for p in items.values():
        if p.get("outside") or not fits(p, f, estimate=False):  # Booli can't estimate, so neither does the answer key
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

def read_listing(page, url, card, now, places, opens=(), known=None):
    """Open one apartment page, opening the sections named in `opens` (facts some agents keep
    folded away). Returns (listing or None, refused?)."""
    try:
        resp = agents.goto(page, url)
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
    # Some agents give the floor only in the address ("Tulegatan 32A, 2tr", Innerstadsspecialisten)
    if not d.get("floor") and (m := re.search(r",\s*(\d+(?:[.,]5)?)\s*tr\b", h1, re.I)):
        d["floor"] = m.group(1).replace(",", ".")
    place = places.lookup(addr)
    if not place or not place["inner"]:
        return {"url": url, "addr": addr, "outside": True, "read": stamp()}, False
    d.update(url=url, addr=addr, read=stamp(), photos=agents.photos(page), **{k: place[k] for k in ("lat", "lon", "area", "group")})
    AREAS.rename(d)
    try:  # the förening's facts, with its tabs and folded sections opened
        ftext = agents.forening_text(page)
        d["facts"] = agents.forening_facts(ftext)
        if orgnr := agents.find_orgnr(ftext, page.inner_text("body"), page.content()):
            d["orgnr"] = orgnr
        d["orgnrChecked"] = True
        d["factsVersion"] = agents.FACTS_VERSION
    except Exception as e:
        log(f"Couldn't read the förening's facts on {url} ({e})")
    try:  # the förening's latest annual report, where the agent links it; read after the run (reports.py)
        if u := reports.report_link(reports.links(page)):
            d["reportUrl"] = u
    except Exception as e:
        log(f"Couldn't read the links on {url} ({e})")
    d["flags"] = agents.red_flags(d.get("desc"), d.get("built"), facts=d.get("facts"), kvm=d.get("kvm"), bv=d.get("bv"), report=d.get("report"))
    try:  # last, since finding the plan may press the page's floor plan button
        d["plans"] = plans.keep(plans.candidates(page, d["photos"]), d, log)
    except Exception as e:
        log(f"Couldn't read the floor plan on {url} ({e})")
    # A report behind the page's documents button takes several seconds to find, so it's looked for once per
    # apartment, after everything else on the page is read
    known = known or {}
    if not d.get("reportUrl") and not known.get("reportChecked"):
        try:
            if u := reports.report_behind_buttons(page, log):
                d["reportUrl"] = u
        except Exception as e:
            log(f"Couldn't open the documents on {url} ({e})")
    if not d.get("reportUrl") and known.get("reportUrl"):
        d["reportUrl"] = known["reportUrl"]
    d["reportChecked"] = True
    if d["state"] == "sold":
        d["shows"] = []
    d.update({k: v for k, v in agents.parse_card(card).items() if k not in d})
    return d, False


def follow_left(page, store, robots, hist, started):
    """Apartments that left their agent's list, or that it still lists as sold: check their page daily
    for up to a month, to record that they sold and, where the agent shows it, the final price."""
    left = store.setdefault("left", {})
    now = dt.datetime.now()
    # A sold apartment on the list isn't read again by its agent's run, but the final price often comes days after "Såld"
    sold = {u: p for u, p in store["items"].items() if p.get("state") == "sold" and not p.get("final") and not p.get("outside")}
    for p in sold.values():
        p.setdefault("sold", stamp())
    todo = [u for u, p in sold.items() if now - dt.datetime.fromisoformat(p["sold"]) < dt.timedelta(days=FOLLOW_DAYS)]
    todo += [u for u, p in left.items() if not p.get("final") and p.get("state") != "gone"
             and now - dt.datetime.fromisoformat(p["left"]) < dt.timedelta(days=FOLLOW_DAYS)]
    for i, url in enumerate(todo, 1):
        p, on_list = (store["items"][url], True) if url in sold else (left[url], False)
        if not robots.allowed(url):
            continue
        status("running", f"Kollar sålda lägenheter och de som lämnat listorna, {i} av {len(todo)}", started=started)
        try:
            resp = agents.goto(page, url)
            page.wait_for_timeout(2000)
            text = page.inner_text("body")
        except PlaywrightError as e:
            log(f"Couldn't open {url} ({e})")
            continue
        if resp is not None and resp.status in (404, 410):
            if on_list:
                continue  # still on the list: the agent's own run tells whether it's gone
            p["state"] = "gone"  # the page was taken down, usually after the sale
        elif (resp is not None and resp.status in (403, 429)) or BLOCKED.search(text):
            log(f"{p.get('agent')} refused {url}; trying again tomorrow")
            continue
        else:
            p.update(agents.parse_bidding(text))
            if p["state"] != "sold":
                p.pop("sold", None)  # wasn't sold after all: the agent's run reads it in full again
        hist.record(p, stamp(), on_list=on_list)
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
            resp = agents.goto(page, url)
            page.wait_for_timeout(2500)
            agents.decline_cookies(page)
            text = page.inner_text("body")
            if not ((resp is not None and resp.status in (403, 429)) or BLOCKED.search(text)):
                text += "\n" + agents.bid_text(page)  # the bid list, where the agent folds it away
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
        # Every bid seen on any day, newest first: agents sometimes shorten their list as bidding goes on
        seen = {(x["amount"], x["at"]): x for x in f.get("bidList", []) + b.pop("bidList", [])}
        if seen:
            f["bidList"] = sorted(seen.values(), key=lambda x: x["at"], reverse=True)
            b["bids"] = max(b.get("bids") or 0, len(seen))
            b["bidders"] = len({x["who"] for x in seen.values() if x["who"]}) or None
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
        status("running", f"Läser mäklare, {sh['done']} av {sh['total']} klara" + (f". Nu: {now}" if now else ""), started=sh["started"])


DROP_FLOOR = 0.75  # a price more than a quarter lower is a misread, not a lowered price
BACK_DAYS = 7  # away from the lists this long before coming back counts as a relisting; shorter is a list that loaded badly


def carry_over(p, old, store, hist):
    """What earlier readings tell about today's: the asking price from before the bidding, a lowered price,
    and that the apartment has been for sale before."""
    old = old or {}
    was, state = old.get("price"), p.get("state") or "for sale"
    if was and state != "for sale":
        p["price"] = was  # in bidding several agents show the highest bid as the price (Erik Olsson, Notar)
    elif was and p.get("price") and old.get("state", "for sale") == "for sale":
        top = (old.get("drop") or {}).get("from", was)
        if p["price"] < was and p["price"] >= top * DROP_FLOOR:
            p["drop"] = {"from": top, "at": stamp()}
        elif p["price"] == was and old.get("drop"):
            p["drop"] = old["drop"]
    back = store.get("left", {}).pop(p["url"], None)  # on its agent's list again
    cutoff = (dt.datetime.now() - dt.timedelta(days=BACK_DAYS)).isoformat(timespec="seconds")
    if old.get("relisted"):
        p["relisted"] = old["relisted"]
    elif back and back.get("left", cutoff) < cutoff:
        p["relisted"] = {"at": stamp(), "left": back["left"], "agent": back.get("agent"), "price": back.get("price")}
    elif not old and not p.get("outside") and (e := hist.earlier(p, cutoff)) and e["url"] not in store["items"]:
        p["relisted"] = {"at": stamp(), "left": e["last_seen"], "agent": e["agent"], "price": e["last_price"], "url": e["url"]}


def read_agent(ag, sh):
    """One agent's list and apartments, in a browser of its own. Shared data is changed under the lock."""
    items, f, now, robots = sh["items"], sh["f"], sh["now"], sh["robots"]
    try:
        with sync_playwright() as pw:
            browser = launch(pw, sh["show"])
            page = browser.new_page(locale="sv-SE", timezone_id="Europe/Stockholm")
            progress(sh, ag["name"], "(listan)")
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
                p, refused = read_listing(page, url, card, now, sh["places"], ag.get("open", ()), known=old)
                if refused:
                    log(f"{ag['name']} refused {url}; skipping the rest of this agent today")
                    with sh["lock"]:
                        sh["notes"].append(f"{ag['name']} nekade en sida, så några av deras lägenheter kan saknas.")
                    break
                read += 1
                with sh["lock"]:
                    if p:
                        p.update(agent=ag["name"], seen=stamp())
                        carry_over(p, old, sh["store"], sh["hist"])
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
            sh["notes"].append(f"Läsningen av {ag['name']} stannade i förtid; se data/scraper.log.")
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


# The job starts at 06:24, at login and every hour (scripts/install-mac.sh), so a morning the Mac was off or asleep
# is caught up as soon as it's on again; a run that finished since 04:00 counts as today's
DAY_STARTS = 4


def done_today(now=None):
    now = now or dt.datetime.now()
    start = now.replace(hour=DAY_STARTS, minute=0, second=0, microsecond=0)
    if now < start:
        start -= dt.timedelta(days=1)
    finished = read_json(DATA / "status.json", {}).get("finished")
    try:
        return dt.datetime.fromisoformat(finished) >= start
    except (TypeError, ValueError):
        return False


def finder_running(pid):
    """Whether the lock's process is still a finder (or bolagsverket.py --update, which takes the same lock): after a
    restart its number may belong to something else."""
    try:
        cmd = subprocess.run(["ps", "-p", str(int(pid)), "-o", "command="], capture_output=True, text=True).stdout
    except ValueError:
        return False
    return any(n in cmd for n in ("finder.py", "bolagsverket.py")) and int(pid) != os.getpid()


def run(args):
    DATA.mkdir(exist_ok=True)
    if args.daily and done_today():
        return 0  # the catch-up start: today's run already finished
    lock = DATA / ".scrape.lock"
    if lock.exists() and finder_running(lock.read_text()):
        print("Another run is going.", file=sys.stderr)
        return 1  # also a run that's been asleep for hours: it carries on when the Mac wakes
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
    for p in items.values():  # neighbourhoods may have been redrawn, and the red flag rules changed, since the last run
        AREAS.rename(p)
        p["flags"] = agents.red_flags(p.get("desc"), p.get("built"), facts=p.get("facts"), kvm=p.get("kvm"), bv=p.get("bv"), report=p.get("report"))
    started, now = stamp(), dt.datetime.now()
    status("running", "Startar", started=started)
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
                status("running", f"Följer {len(saved)} sparade {'lägenheter' if len(saved) > 1 else 'lägenhet'}", started=started)
                follow(page, store, saved, robots, hist)
            follow_left(page, store, robots, hist, started)
            if not args.no_count:
                status("running", "Läser Boolis antal", started=started)
                b = booli_count(pw, f)
                store["booli"] = {**b, "filter": f} if b else {**(store.get("booli") or {}), "missing": stamp()}
            browser.close()
    except Exception as e:  # keep what was read, and say what went wrong
        write_json(DATA / "listings.json", store)
        log(f"Failed: {e!r}")
        status("error", f"Bostadsletaren stannade i förtid: {str(e)[:160]}", started=started)
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
    # One comparison with Booli a day (the day's last run wins), to see the gap close while agents are added.
    # Booli's count is only comparable when it was read for the filter used now.
    b = store.get("booli") or {}
    same = b.get("forSale") is not None and not b.get("missing") and b.get("filter") == f
    day = {"forSale": store["found"]["forSale"], "soon": store["found"]["soon"], "agents": len(store["agents"])}
    if same:
        day.update(booliForSale=b["forSale"], **({"booliSoon": b["soon"]} if b.get("soon") is not None else {}))
        if b.get("apartments"):
            b["matched"] = booli_matched(b["apartments"], items, f)
            tally = {w: sum(1 for a in b["matched"] if a["here"] == w) for w in ("found", "unpriced", "filtered", "missing")}
            miss = {}
            for a in b["matched"]:
                if a["here"] == "missing":
                    miss[a["agent"] or "?"] = miss.get(a["agent"] or "?", 0) + 1
            log(f"Booli's first page: {len(b['matched'])} for sale; {tally['found']} found here, {tally['unpriced']} here without a price, "
                f"{tally['filtered']} here but filtered out, {tally['missing']} missing"
                + (" (" + ", ".join(f"{k} {v}" for k, v in sorted(miss.items(), key=lambda kv: -kv[1])) + ")" if miss else ""))
    store.setdefault("compare", {})[stamp()[:10]] = day
    with open(DATA / "compare.csv", "w", encoding="utf-8") as fh:
        fh.write("day,for_sale,soon,booli_for_sale,booli_soon,agents\n")
        for d, c in sorted(store["compare"].items()):
            fh.write(f"{d},{c['forSale']},{c['soon']},{c.get('booliForSale', '')},{c.get('booliSoon', '')},{c['agents']}\n")
    try:  # the förening's annual report and its record at Bolagsverket
        import bolagsverket
        status("running", "Läser föreningarnas årsredovisningar", started=started)
        reports.update(items, log)
        status("running", "Läser föreningarna hos Bolagsverket", started=started)
        bolagsverket.update(items, log)
        for p in items.values():
            if p.get("bv") or p.get("factsFrom") or p.get("report"):
                p["flags"] = agents.red_flags(p.get("desc"), p.get("built"), facts=p.get("facts"), kvm=p.get("kvm"), bv=p.get("bv"), report=p.get("report"))
    except Exception as e:
        log(f"Bolagsverket: stopped ({e})")
    hist.export_csv()
    store["history"] = hist.counts()
    hist.close()
    write_json(DATA / "listings.json", store)
    msg = f"Hittade {store['found']['forSale']} till salu och {store['found']['soon']} snart till salu."
    msg += f" Booli visar {b['forSale']} och {b['soon']}." if same else " Boolis antal för det här filtret gick inte att läsa i dag." if b.get("missing") or b.get("filter") == f else " Boolis antal läses för det nya filtret vid nästa morgonkörning."
    log(f"Run finished. {msg}")
    status("partial" if notes else "done", " ".join([msg] + notes), started=started, finished=store["lastRun"])
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agents", nargs="*", help="only these agents, by name")
    ap.add_argument("--max-price", type=int, help="max price instead of the planner's filter")
    ap.add_argument("--no-count", action="store_true", help="don't read Booli's count")
    ap.add_argument("--show", action="store_true", help="show the browser window")
    ap.add_argument("--daily", action="store_true", help="do nothing if today's run already finished (the scheduled starts)")
    sys.exit(run(ap.parse_args()))
