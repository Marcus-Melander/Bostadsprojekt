"""Travel times from each apartment to your important addresses, and the nearest grocery store and gym.

Your addresses come from the planner (data/plan.json, show.places). Trips are SL's own journey
planner (Journey-planner v2 on Trafiklab, open without a key), arriving by the time you set for each
address on a weekday. Grocery stores (ICA, Hemköp, Coop, Willys and Lidl), gyms, and bars and pubs come from OpenStreetMap through Overpass, fetched once a week.
Trips are looked up only for the apartments you saved, to keep the requests to SL few; the nearest
shop and gym are worked out here for every apartment in your filter. Everything is kept in
data/travel.json, so an apartment is looked up once per address.

    .venv/bin/python scraper/travel.py
"""
import datetime as dt
import json
import math
import re
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import finder  # noqa: E402  (fits, log and the JSON helpers)

UA = "Bostadsplan/1.0 (personal apartment planner)"
SL = "https://journeyplanner.integration.sl.se/v2/"
OVERPASS = "https://overpass-api.de/api/interpreter"
BBOX = (59.29, 17.97, 59.37, 18.14)  # the inner city with a margin, so shops just outside count too
SHOPS_DAYS = 7     # how often shops and gyms are fetched again
TRIP_DAYS = 90     # timetables change; a trip is looked up again after this long
PAUSE = 1.0        # seconds between requests to SL
WALK = 80 / 1.25   # metres a minute on foot, counting the streets' detours over a straight line
GROCERS = re.compile(r"\b(?:ica|hemköp|coop|willys|lidl)\b", re.I)  # the chains you shop at
SHOPS_VERSION = 3  # raise when what's fetched changes, so the weekly cache is refreshed at once


def get_json(url, data=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def metres(a, b):
    dy = (a[0] - b[0]) * 111320
    dx = (a[1] - b[1]) * 111320 * math.cos(math.radians(a[0]))
    return math.hypot(dx, dy)


def find_address(addr):
    """(name, lat, lon) of an address as SL knows it, or None."""
    q = urllib.parse.urlencode({"name_sf": addr, "type_sf": "any", "any_obj_filter_sf": 46})
    locs = get_json(SL + "stop-finder?" + q).get("locations", [])
    best = max(locs, key=lambda l: l.get("matchQuality", 0), default=None)
    if not best or not best.get("coord"):
        return None
    return best.get("name") or addr, best["coord"][0], best["coord"][1]


def next_weekday(now):
    """The coming Tuesday, an ordinary weekday whose timetable is already published."""
    d = now.date() + dt.timedelta(days=1)
    while d.weekday() != 1:
        d += dt.timedelta(days=1)
    return d


def trip(origin, dest, day, arrive):
    """Minutes door to door by SL, arriving by `arrive` ("08:00"), with the lines used; None if no trip."""
    q = urllib.parse.urlencode({
        "type_origin": "coord", "name_origin": f"{origin[1]:.5f}:{origin[0]:.5f}:WGS84[dd.ddddd]",
        "type_destination": "coord", "name_destination": f"{dest[1]:.5f}:{dest[0]:.5f}:WGS84[dd.ddddd]",
        "itd_date": day.strftime("%Y%m%d"), "itd_time": arrive.replace(":", ""), "itd_trip_date_time_dep_arr": "arr",
        "calc_number_of_trips": 3,
    })
    journeys = get_json(SL + "trips?" + q).get("journeys", [])
    if not journeys:
        return None
    j = min(journeys, key=lambda j: j["tripDuration"])
    lines = []
    for leg in j["legs"]:
        t = leg.get("transportation") or {}
        name = (t.get("product") or {}).get("name")
        if name and name != "footpath":
            lines.append(f"{name} {t.get('disassembledName') or ''}".strip())
    return {"min": round(j["tripDuration"] / 60), "changes": j.get("interchanges", 0), "lines": lines}


def fetch_shops():
    s, w, n, e = BBOX
    q = (f"[out:json][timeout:60];(nwr[\"shop\"=\"supermarket\"]({s},{w},{n},{e});"
         f"nwr[\"leisure\"=\"fitness_centre\"]({s},{w},{n},{e});"
         f"nwr[\"amenity\"~\"^(bar|pub)$\"]({s},{w},{n},{e}););out center tags;")
    out = {"grocery": [], "gym": [], "bar": []}
    for el in get_json(OVERPASS, urllib.parse.urlencode({"data": q}).encode())["elements"]:
        t = el.get("tags", {})
        lat, lon = (el["lat"], el["lon"]) if "lat" in el else (el["center"]["lat"], el["center"]["lon"])
        name = t.get("name") or t.get("brand")
        kind = "grocery" if t.get("shop") == "supermarket" else "bar" if t.get("amenity") in ("bar", "pub") else "gym"
        if not name or (kind == "gym" and re.search(r"yoga|pilates", name, re.I)):
            continue
        if kind == "grocery" and not GROCERS.search(f"{name} {t.get('brand', '')}"):
            continue
        out[kind].append({"name": name, "lat": lat, "lon": lon})
    return out


def nearest(pt, places):
    best = min(places, key=lambda s: metres(pt, (s["lat"], s["lon"])), default=None)
    if not best:
        return None
    m = metres(pt, (best["lat"], best["lon"]))
    return {"name": best["name"], "m": round(m / 10) * 10, "walk": max(1, round(m / WALK))}


def run():
    plan = finder.read_json(DATA / "plan.json", {})
    places = [p for p in (plan.get("show") or {}).get("places", []) if (p.get("addr") or "").strip()]
    items = finder.read_json(DATA / "listings.json", {}).get("items", {})
    store = finder.read_json(DATA / "travel.json", {})
    now = dt.datetime.now()

    # Shops and gyms, once a week
    shops = store.get("shops")
    if not shops or store.get("shopsVersion") != SHOPS_VERSION or (store.get("shopsAt") or "") < (now - dt.timedelta(days=SHOPS_DAYS)).isoformat():
        try:
            shops = fetch_shops()
            store.update(shops=shops, shopsAt=now.isoformat(timespec="seconds"), shopsVersion=SHOPS_VERSION)
            finder.log(f"Travel: {len(shops['grocery'])} grocery stores, {len(shops['gym'])} gyms and {len(shops['bar'])} bars and pubs from OpenStreetMap")
        except Exception as e:
            finder.log(f"Travel: couldn't fetch shops and gyms from OpenStreetMap ({e})")
            shops = shops or {"grocery": [], "gym": [], "bar": []}

    # Your addresses, as SL knows them
    known = store.get("places", {})
    for p in places:
        k = p["addr"].strip().lower()
        if k not in known:
            try:
                hit = find_address(p["addr"])
                known[k] = {"name": hit[0], "lat": hit[1], "lon": hit[2]} if hit else None
                finder.log(f"Travel: {p['addr']} is {hit[0] if hit else 'not found by SL'}")
            except Exception as e:
                finder.log(f"Travel: couldn't look up {p['addr']} ({e})")
            time.sleep(PAUSE)
    store["places"] = known

    # Every apartment in the inner city gets its nearest shop, gym and bar (worked out here, from the weekly list of
    # shops, so it costs nothing, and friends online with other filters see them too); the ones you saved get a trip
    # to each address
    want = {u: p for u, p in items.items() if p.get("lat") and not p.get("outside")}
    saved = {a.get("link") for a in plan.get("apts", []) if a.get("link")}
    near, trips = store.get("near", {}), store.get("trips", {})
    for u, p in want.items():
        pt = (p["lat"], p["lon"])
        near[u] = {k: nearest(pt, shops.get(k, [])) for k in ("grocery", "gym", "bar")}
    day, stale, done, failed = next_weekday(now), (now - dt.timedelta(days=TRIP_DAYS)).isoformat(), 0, 0

    def needed(u, pl):  # not looked up yet, the address or time changed, or the answer is old
        loc = known.get(pl["addr"].strip().lower())
        if not loc:
            return False
        had = (trips.get(u) or {}).get(pl["id"])
        return not had or had.get("key") != f"{loc['lat']},{loc['lon']},{pl.get('arrive') or '08:00'}" or had.get("at", "") < stale

    todo = [(u, items[u], pl) for u in saved if (items.get(u) or {}).get("lat") for pl in places if needed(u, pl)]
    if todo:
        finder.log(f"Travel: looking up {len(todo)} trips with SL")
    for i, (u, p, pl) in enumerate(todo):
        loc, arrive = known[pl["addr"].strip().lower()], pl.get("arrive") or "08:00"
        try:
            t = trip((p["lat"], p["lon"]), (loc["lat"], loc["lon"]), day, arrive)
            trips.setdefault(u, {})[pl["id"]] = {**(t or {"min": None}), "key": f"{loc['lat']},{loc['lon']},{arrive}", "at": now.isoformat(timespec="seconds")}
            done += 1
        except Exception as e:
            failed += 1
            if failed >= 5:
                finder.log(f"Travel: SL isn't answering ({e}); the rest is looked up next run")
                break
        if i % 50 == 49:  # save as it goes, so the planner fills in while a long first run continues
            finder.write_json(DATA / "travel.json", {**store, "near": near, "trips": trips, "updated": now.isoformat(timespec="seconds")})
        time.sleep(PAUSE)
    store.update(near=near, trips=trips, updated=dt.datetime.now().isoformat(timespec="seconds"))
    finder.write_json(DATA / "travel.json", store)
    if todo:
        finder.log(f"Travel: {done} trips looked up{f', {failed} failed' if failed else ''}")


def running():
    lock = DATA / ".travel.lock"
    try:
        os.kill(int(lock.read_text()), 0)
        return True
    except (OSError, ValueError):
        return False


def main():
    """One run at a time: the morning finder run and the planner (when you change an address) both start it."""
    if running():
        return
    lock = DATA / ".travel.lock"
    lock.write_text(str(os.getpid()))
    try:
        run()
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
