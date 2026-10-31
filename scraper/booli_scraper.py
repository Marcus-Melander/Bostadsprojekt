"""Daily Booli reader for Stockholm Bostadsplan.

Reads Booli's search results for each area you have ticked in the planner and
saves them to data/listings.json. Each result carries the apartment's next
showing, price, size, avgift, features and estate agent. For apartments in
your budget with a showing in the next two weeks, it then reads the agent's
own listing page (see agents.py) for every showing time, the build year,
running costs and the description.

It drives your installed Google Chrome, as it comes, at a slow pace. Booli
refuses headless Chrome, so a Chrome window opens while it runs. If Booli
blocks a page or asks for a human check, it stops and says so in
data/status.json; nothing is retried.

    .venv/bin/python scraper/booli_scraper.py                 # normal run
    .venv/bin/python scraper/booli_scraper.py --areas 255555 --pages 1
    .venv/bin/python scraper/booli_scraper.py --no-agents     # Booli search results only
"""
import argparse
import datetime as dt
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

import agents

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
BOOLI = "https://www.booli.se"
MAX_PAGES = 4                      # result pages per area
DETAIL_EVERY = dt.timedelta(hours=20)
BOOLI_PAUSE_S = (10, 20)           # seconds between Booli pages, to keep the load on Booli low
PAUSE_S = (4, 9)                   # between pages on an estate agent's site, which gets only a few visits

# Used until the planner has saved a config. Same list and order as HOODS in the planner.
DEFAULT_AREAS = [
    ["255555", "SoFo"], ["874678", "Mariatorget"], ["41668", "Mariaberget"], ["845555", "Hornstull"],
    ["4796", "Sofia"], ["7197", "Katarina"], ["8069", "Maria"], ["849853", "Högalid"],
    ["826197", "Främre Kungsholmen"], ["880478", "Övre Kungsholmen"], ["931427", "Västra Kungsholmen"],
    ["874672", "Nedre Kungsholmen"], ["874670", "Östra Kungsholmen"], ["3598", "Fredhäll"],
    ["874666", "Birkastan"], ["874687", "Sibirien"], ["96512", "Hagastaden"], ["1012251", "Lärkstaden"],
    ["115341", "Other Södermalm"], ["115353", "Other Kungsholmen"], ["115349", "Other Vasastan"],
    ["115348", "Other Östermalm"], ["115350", "Norrmalm"], ["115346", "Gamla stan"], ["115347", "Gärdet"],
]
FEATS = {"Hiss", "Balkong", "Eldstad", "Uteplats", "Terrass", "Takterrass"}
SV_MONTHS = ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec"]
BLOCKED = re.compile(r"captcha|verify you are human|är du en människa|access denied|unusual traffic", re.I)


class Blocked(Exception):
    pass


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


# ---------- Parsing (dates are local "YYYY-MM-DDTHH:MM" strings, like the planner) ----------

def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M")


def date_for(day, month_idx, time_s, now):
    year = now.year
    d = dt.datetime(year, month_idx + 1, day)
    if d < now.replace(hour=0, minute=0, second=0, microsecond=0) - dt.timedelta(days=7):
        d = d.replace(year=year + 1)
    return d.strftime("%Y-%m-%dT") + time_s.zfill(5)


def parse_when(txt, now):
    """'Sön 8 nov kl 12:30', 'Idag kl 17:00', 'Imorgon kl 12:45'"""
    t = re.search(r"kl (\d{1,2}:\d{2})", txt or "")
    if not t:
        return None
    if re.match(r"(Idag|Imorgon)", txt, re.I):
        d = now + dt.timedelta(days=1 if txt.lower().startswith("imorgon") else 0)
        return d.strftime("%Y-%m-%dT") + t.group(1).zfill(5)
    m = re.search(r"(\d{1,2}) ([a-zåäö]{3})", txt, re.I)
    if not m or m.group(2).lower() not in SV_MONTHS:
        return None
    return date_for(int(m.group(1)), SV_MONTHS.index(m.group(2).lower()), t.group(1), now)


def num(s):
    s = re.sub(r"[^\d,]", "", str(s)).replace(",", ".")
    try:
        v = float(s)
    except ValueError:
        return None
    return int(v) if v.is_integer() else v


def parse_card(lines, now):
    """One result card's text, one line per item, as Booli shows it."""
    c = {"feats": []}
    lines = [re.sub(r"[\u00a0\u202f]", " ", l).strip() for l in lines]  # Booli uses non-breaking spaces
    for i, s in enumerate(lines):
        if re.match(r"(Mån|Tis|Ons|Tor|Fre|Lör|Sön|Idag|Imorgon)\b.*kl \d{1,2}:\d{2}$", s, re.I):
            c["next"] = parse_when(s, now)
        elif s.startswith("Spara ") and i + 1 < len(lines):
            c["addr"] = lines[i + 1]
        elif s.startswith("Lägenhet · "):
            c["label"] = s.split(" · ")[1]
        elif re.fullmatch(r"[\d\s ]+ kr", s):
            c["price"] = num(s)
        elif s == "Pris ej angivet":
            c["price"] = None
        elif s.endswith(" m²") and "kr" not in s:
            c["kvm"] = num(s)
        elif re.fullmatch(r"[\d,]+ rum", s):
            c["rooms"] = num(s)
        elif s.startswith("vån "):
            c["floor"] = s[4:]
        elif s == "BV":
            c["floor"] = "BV"
        elif s.endswith(" kr/mån"):
            c["avgift"] = num(s)
        elif s in FEATS and s not in c["feats"]:
            c["feats"].append(s)
        elif s == "Snart till salu":
            c["soon"] = True
    return c


# ---------- Reading Booli ----------

def search_url(query, area_id, page):
    p = dict(parse_qsl(query or ""))
    p.setdefault("objectType", "Lägenhet")
    p["areaIds"] = area_id
    p.pop("page", None)
    if page > 1:
        p["page"] = str(page)
    return f"{BOOLI}/sok/till-salu?{urlencode(p)}"


def visit(page, url):
    resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
    if resp is not None and resp.status in (403, 429):
        raise Blocked(f"Booli answered {resp.status} on {url}")
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PlaywrightError:
        pass
    body = page.inner_text("body")
    if BLOCKED.search(body):
        raise Blocked(f"Booli asked for a human check on {url}")
    return body


def read_results(page, now):
    try:  # Booli draws the results after the page has loaded
        page.wait_for_selector('li a[href^="/annons/"], li a[href^="/bostad/"]', timeout=20000)
    except PlaywrightError:
        pass  # an area with nothing for sale
    cards = page.eval_on_selector_all(
        'a[href^="/annons/"], a[href^="/bostad/"]',
        # The estate agent's logo is the card's last image that isn't a photo of the apartment
        """els => els.filter(a => a.closest('li')).map(a => ({href: a.getAttribute('href'), text: a.innerText,
            broker: [...a.closest('li').querySelectorAll('img[alt]')].map(i => i.alt)
              .filter(x => x && !/bild|planritning|interiör|exteriör|balkong|kök|badrum|sovrum|vardagsrum/i.test(x)).pop() || null}))""",
    )
    items = []
    for c in cards:
        m = re.match(r"/(annons|bostad)/(\d+)", c["href"])
        if not m:
            continue
        card = parse_card([l for l in c["text"].split("\n") if l.strip()], now)
        if card.get("addr"):
            if c.get("broker"):
                card["broker"] = c["broker"]
            card["key"] = f"{m.group(1)}/{m.group(2)}"
            items.append(card)
    has_next = page.get_by_role("link", name="Nästa sida").count() > 0
    return items, has_next


def merge(listings, cards, area_id, order):
    seen = dt.datetime.now().isoformat(timespec="seconds")
    for c in cards:
        p = listings["items"].setdefault(c["key"], {"key": c["key"], "in": []})
        p.update(c, seen=seen)
        if area_id not in p["in"]:
            p["in"].append(area_id)
        p["hood"] = min(p["in"], key=lambda a: order.index(a) if a in order else len(order))


def needs_detail(p, now, max_price, never):
    if not p.get("next") or any(a in never for a in p.get("in", [])):
        return False
    if not (iso(now) <= p["next"] <= iso(now + dt.timedelta(days=14))):
        return False
    if p.get("price") and max_price and p["price"] > max_price * 1.15:
        return False
    last = p.get("detail")
    return not last or now - dt.datetime.fromisoformat(last) > DETAIL_EVERY


def prune(listings, visits):
    cutoff = iso(dt.datetime.now() - dt.timedelta(days=2))
    for k in list(listings["items"]):
        p = listings["items"][k]
        last = max([p.get("next") or ""] + [s["start"] for s in p.get("shows", [])])
        if k not in visits and last < cutoff:
            del listings["items"][k]


def read_agents(page, listings, max_price, never, started):
    """For apartments in budget with a showing soon: read the estate agent's own page."""
    now = dt.datetime.now()
    robots, refused = agents.Robots(), set()
    index = agents.AgentIndex(page, robots, DATA / "cache", log)
    want = [p for p in listings["items"].values() if needs_detail(p, now, max_price, never)]
    read = 0
    for i, p in enumerate(want, 1):
        ag = agents.agent_for(p.get("broker"))
        if not ag or ag["name"] in refused:
            continue
        status("running", f"Reading estate agent pages, {i} of {len(want)}", started=started)
        stamp = dt.datetime.now().isoformat(timespec="seconds")
        # the page found last time first, but look again in case it has been taken down or sold
        urls = list(dict.fromkeys(([p["agentUrl"]] if p.get("agentUrl") else []) + index.candidates(ag, p.get("addr", ""))))
        if not urls:
            p.update(detail=stamp, agentMiss=f"Not found on {ag['name']}'s site")
            continue
        found = None
        for url in urls:
            if not robots.allowed(url):
                p.update(detail=stamp, agentMiss=f"{ag['name']}'s robots.txt asks visitors not to read it")
                continue
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(2500)
                text = page.inner_text("body")
            except PlaywrightError as e:
                log(f"{ag['name']}: couldn't open {url} ({e})")
                continue
            if (resp is not None and resp.status in (403, 429)) or BLOCKED.search(text):
                log(f"{ag['name']} refused {url}; skipping this agent for today")
                refused.add(ag["name"])
                break
            time.sleep(random.uniform(*PAUSE_S))
            d = agents.parse_agent_page(text, now)
            if (resp is None or resp.status < 400) and not agents.is_sold(text) and (d["shows"] or len(urls) == 1 or url == urls[-1]):
                found = (url, d)
                break
        if not found:
            p.setdefault("agentMiss", f"No current listing found on {ag['name']}'s site")
            p["detail"] = stamp
            continue
        url, d = found
        p.pop("agentMiss", None)
        p.update(agentUrl=url, agentName=ag["name"], detail=stamp, **{k: v for k, v in d.items() if v})
        read += 1
        write_json(DATA / "listings.json", listings)
    covered = sum(1 for p in want if p.get("agentUrl"))
    log(f"Estate agents: read {read} pages; {covered} of {len(want)} apartments in budget have agent details")
    return f"Agent details for {covered} of {len(want)} apartments in your budget with a showing soon." if want else ""


def run(args):
    DATA.mkdir(exist_ok=True)
    lock = DATA / ".scrape.lock"
    if lock.exists() and time.time() - lock.stat().st_mtime < 3 * 3600:
        print("Another run is going.", file=sys.stderr)
        return 1
    lock.write_text(str(os.getpid()))
    try:
        return scrape(args)
    finally:
        lock.unlink(missing_ok=True)


def scrape(args):
    cfg = read_json(DATA / "scrape.json", {})
    areas = cfg.get("areas") or DEFAULT_AREAS
    if args.areas:
        areas = [a for a in areas if a[0] in args.areas] or [[a, "Booli area " + a] for a in args.areas]
    order = [a[0] for a in areas]
    query, max_price, never = cfg.get("query", "objectType=Lägenhet"), cfg.get("maxPrice"), set(cfg.get("never", []))
    query, max_price = args.query or query, args.max_price or max_price
    visits = read_json(DATA / "plan.json", {}).get("show", {}).get("visits", {})
    listings = read_json(DATA / "listings.json", {"items": {}, "done": {}, "lastRun": None})
    started = dt.datetime.now().isoformat(timespec="seconds")
    status("running", "Starting", started=started)
    log(f"Run started: {len(areas)} areas, filter {query}")
    pages, note = 0, ""
    try:
        with sync_playwright() as pw:
            try:  # your installed Google Chrome; Playwright's own Chromium as a fallback
                browser = pw.chromium.launch(channel="chrome", headless=args.headless)
            except PlaywrightError:
                browser = pw.chromium.launch(headless=args.headless)
            page = browser.new_page(locale="sv-SE", timezone_id="Europe/Stockholm")
            for area_id, name in ([] if args.agents_only else areas):
                for n in range(1, args.pages + 1):
                    status("running", f"Reading {name}" + (f", page {n}" if n > 1 else ""), started=started)
                    visit(page, search_url(query, area_id, n))
                    items, has_next = read_results(page, dt.datetime.now())
                    pages += 1
                    merge(listings, items, area_id, order)
                    write_json(DATA / "listings.json", listings)
                    log(f"{name} page {n}: {len(items)} apartments")
                    time.sleep(random.uniform(*BOOLI_PAUSE_S))
                    if not has_next or not items:
                        break
                listings["done"][area_id] = dt.datetime.now().isoformat(timespec="seconds")
            if not args.no_agents:
                note = read_agents(page, listings, max_price, never, started)
            browser.close()
    except Blocked as e:
        write_json(DATA / "listings.json", listings)
        log(f"Stopped: {e}")
        status("blocked", f"Stopped: {e}. Nothing was retried.", started=started)
        return 2
    except Exception as e:  # keep what was read, and say what went wrong
        write_json(DATA / "listings.json", listings)
        log(f"Failed: {e!r}")
        status("error", f"Failed: {e}", started=started)
        return 3
    prune(listings, visits)
    listings["lastRun"] = dt.datetime.now().isoformat(timespec="seconds")
    write_json(DATA / "listings.json", listings)
    log(f"Run finished: {pages} pages, {len(listings['items'])} apartments saved")
    status("done", note or f"Read {pages} Booli pages", started=started, finished=listings["lastRun"], count=len(listings["items"]))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--headless", action="store_true", help="hide the browser window (Booli refuses this today)")
    ap.add_argument("--query", help="Booli filter to use instead of the planner's, e.g. maxListPrice=6000000")
    ap.add_argument("--max-price", type=int, help="max price instead of the planner's budget")
    ap.add_argument("--agents-only", action="store_true", help="skip Booli; read agent pages for apartments already saved")
    ap.add_argument("--no-agents", action="store_true", help="skip estate agent pages, read Booli search results only")
    ap.add_argument("--areas", nargs="*", help="only these Booli area ids")
    ap.add_argument("--pages", type=int, default=MAX_PAGES, help="result pages per area")
    sys.exit(run(ap.parse_args()))
