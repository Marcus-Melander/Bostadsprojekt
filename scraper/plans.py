"""Floor plans (planlösning): tell them from photos, and keep a copy of each for training.

The finder hands over the images an agent's page names as a floor plan (agents.floorplans), or for
agents that don't name them, the gallery's last photos. Each is downloaded once and kept only if it
looks like a drawing: mostly one flat background colour with sharp lines on it. Kept plans are saved in data/floorplans/ with the apartment's details in index.json,
so a model can later be trained on them; the listing's address joins them to the price history.
Everything stays on this computer (data/ isn't in git).

    .venv/bin/python scraper/plans.py --backfill    # floor plans and förening facts for apartments read before these existed
"""
import datetime as dt
import hashlib
import io
import json
import os
import re
import sys
import threading
import time
import urllib.request
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DIR = DATA / "floorplans"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Bostadsplan/1.0 (personal apartment planner)"
LOCK = threading.Lock()  # the finder reads several agents at once; index.json is written by one at a time
DETAILS = ("addr", "area", "group", "agent", "rooms", "kvm", "price", "avgift", "floor", "built", "lat", "lon")


def looks_like_plan(img):
    """A drawing, not a photo: at least 65 % of it is one flat background colour (white, beige, or black where
    a transparent drawing was flattened), with sharp lines on it. Photos measured below half."""
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, "white")
        bg.paste(img, mask=img.split()[-1])
        img = bg
    small = img.convert("RGB").resize((160, max(1, int(160 * img.height / img.width))))
    px = list(small.get_flattened_data() if hasattr(small, "get_flattened_data") else small.getdata())
    mode = Counter((r // 16, g // 16, b // 16) for r, g, b in px).most_common(1)[0][0]
    m = [c * 16 + 8 for c in mode]
    flat = sum(1 for p in px if max(abs(p[0] - m[0]), abs(p[1] - m[1]), abs(p[2] - m[2])) <= 20) / len(px)
    lum = lambda p: 0.3 * p[0] + 0.59 * p[1] + 0.11 * p[2]
    lines = sum(1 for p in px if abs(lum(p) - lum(m)) > 40) / len(px)
    return flat >= 0.65 and lines >= 0.01


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def load_index():
    try:
        return json.loads((DIR / "index.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def save_index(index):
    tmp = DIR / "index.tmp"
    tmp.write_text(json.dumps(index, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, DIR / "index.json")


def keep(candidates, listing, log=print, limit=3):
    """The candidates that are floor plans, saved to data/floorplans/; returns their addresses on the agent's site."""
    with LOCK:
        return _keep(candidates, listing, log, limit)


def _keep(candidates, listing, log, limit):
    DIR.mkdir(parents=True, exist_ok=True)
    index, kept = load_index(), []
    by_source = {v["src"]: k for k, v in index.items()}
    for u in candidates:
        if len(kept) >= limit:
            break
        if u in by_source:  # downloaded before
            kept.append(u)
            continue
        if urlparse(u).path.lower().endswith(".svg"):  # icons (Södermäklarna's close button), never a scanned plan
            continue
        try:
            raw = fetch(u)
            img = Image.open(io.BytesIO(raw))
            img.load()
        except Exception as e:
            log(f"Couldn't read the floor plan {u} ({e})")
            continue
        if min(img.size) < 300 or not looks_like_plan(img):
            continue
        name = hashlib.sha1(u.encode()).hexdigest()[:16] + "." + (img.format or "jpg").lower().replace("jpeg", "jpg")
        (DIR / name).write_bytes(raw)
        index[name] = {"src": u, "listing": listing.get("url"), "saved": dt.datetime.now().isoformat(timespec="seconds"),
                       "width": img.width, "height": img.height, **{k: listing[k] for k in DETAILS if listing.get(k) is not None}}
        kept.append(u)
    save_index(index)
    return kept


def candidates(page, photos):
    """What to test: the images the page names as a floor plan, else the gallery's last four photos."""
    import agents
    named = agents.floorplans(page)
    if named:
        return named
    # Unnamed: floor plans usually come last in the gallery, so open the whole gallery if the page has a button for it
    gallery = []
    btn = page.locator("button:visible, a:visible").filter(has_text=re.compile(r"^\s*(?:alla bilder|visa alla bilder|bilder|visa alla)\s*(?:\(?\d+\)?)?\s*$", re.I))
    if btn.count():
        try:
            btn.first.click(timeout=3000)
            page.wait_for_timeout(2000)
            for _ in range(4):
                page.mouse.wheel(0, 3000)
                page.wait_for_timeout(400)
            gallery = [u for u in dict.fromkeys(page.eval_on_selector_all(
                "img", "els => els.filter(i => i.naturalWidth >= 500).map(i => i.currentSrc || i.src)"))
                if u.startswith("http") and not re.search(r"logo|icon|avatar|maklare|broker", u, re.I)][-6:]
            page.keyboard.press("Escape")
        except Exception:
            pass
    return list(dict.fromkeys(gallery + photos[-4:]))


def backfill():
    """Open each apartment in your filter that was read before floor plans or förening facts were, once, and read what's missing."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import agents
    import finder
    from playwright.sync_api import sync_playwright

    store = finder.read_json(DATA / "listings.json", {})
    f = finder.read_json(DATA / "scrape.json", {}).get("filter", {})
    # Every apartment in your filter (counted the way Booli counts, so an unpriced one whose price is still wrong is in)
    todo = [u for u, p in store.get("items", {}).items() if ("plans" not in p or stale_facts(p) or "pageChecked" not in p or "orgnrChecked" not in p)
            and p.get("lat") and not p.get("outside") and finder.fits(p, f, estimate=False)]
    finder.log(f"Backfill: {len(todo)} apartments to look at for floor plans and förening facts")
    robots, refused, found = agents.Robots(), set(), {}
    with sync_playwright() as pw:
        browser = finder.launch(pw, False)
        page = browser.new_page()
        for i, u in enumerate(todo):
            p = store["items"][u]
            if p.get("agent") in refused or not robots.allowed(u):
                continue
            try:
                resp = page.goto(u, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(2000)
                agents.decline_cookies(page)
                for _ in range(3):
                    page.mouse.wheel(0, 2500)
                    page.wait_for_timeout(500)
                if (resp and resp.status in (403, 429)) or finder.BLOCKED.search(page.inner_text("body")):
                    finder.log(f"Floor plans: {p.get('agent')} refused {u}; skipping the rest of its apartments")
                    refused.add(p.get("agent"))
                    continue
                got = {}
                if "pageChecked" not in p:  # read again with only this apartment's part of the page (fixed 2026-11-01)
                    d = agents.parse_agent_page(page.inner_text("body"), dt.datetime.now())
                    got.update(price=d.get("price"), soon=d.get("soon"), shows=d.get("shows", []), pageChecked=finder.stamp())
                    got.update({k: d[k] for k in ("rooms", "kvm", "avgift", "floor", "floorText", "built") if d.get(k)})
                if stale_facts(p) or "orgnrChecked" not in p:  # first, before the floor plan button changes the page
                    ftext = agents.forening_text(page)
                    if stale_facts(p):
                        got.update(facts=agents.forening_facts(ftext), factsVersion=agents.FACTS_VERSION)
                    if orgnr := agents.find_orgnr(ftext, page.inner_text("body"), page.content()):
                        got["orgnr"] = orgnr
                    got["orgnrChecked"] = True
                if "plans" not in p:
                    got["plans"] = keep(candidates(page, p.get("photos") or []), p, finder.log)
                found[u] = got
            except Exception as e:
                finder.log(f"Floor plans: couldn't open {u} ({e})")
            if i % 25 == 24:
                merge(found)
                found = {}
            time.sleep(2)
        browser.close()
    merge(found)
    finder.log("Backfill: done")


def stale_facts(p):
    """No förening facts yet, or none found by a reader that knew fewer agents' layouts."""
    import agents
    return "facts" not in p or (not p["facts"] and p.get("factsVersion", 1) < agents.FACTS_VERSION)


def merge(found):
    """Write what the backfill read into listings.json without losing what a finder run wrote meanwhile."""
    import agents
    import finder
    if not found:
        return
    store = finder.read_json(DATA / "listings.json", {})
    for u, got in found.items():
        if u in store.get("items", {}):
            p = store["items"][u]
            p.update(got)
            if "facts" in got:
                p["flags"] = agents.red_flags(p.get("desc"), p.get("built"), facts=p.get("facts"), kvm=p.get("kvm"), bv=p.get("bv"))
    finder.write_json(DATA / "listings.json", store)


if __name__ == "__main__":
    if "--backfill" in sys.argv:
        lock = DATA / ".scrape.lock"  # the finder's own lock: the two never write listings.json at once
        try:
            os.kill(int(lock.read_text()), 0)
            sys.exit("The finder is running; try again when it's done.")
        except (OSError, ValueError):
            pass
        lock.write_text(str(os.getpid()))
        try:
            backfill()
        finally:
            lock.unlink(missing_ok=True)
    else:
        print(__doc__)
