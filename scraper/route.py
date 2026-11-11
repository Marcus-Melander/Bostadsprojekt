"""A showing-day route: for each day of the coming week, the showings you can get to, in order, with the trip between them.

Which showings: the apartments in your Visningar filter (plan.json's show.f, areas marked Aldrig här left out when they're
hidden) and every apartment you saved, minus the ones you've already been to (show.visits). Each showing is a window
(`shows`' start and end); you stay STAY minutes, or the whole window when it's shorter.

The order: the most worth seeing that fits (a booked viewing counts most, then a saved apartment, then one in the
filter), and among equals the least travel. It's worked out as a search over sequences (any order, not only by start
time), keeping for each last stop the few best ways to get there.

The trips: on foot when the straight-line distance, with the streets' detours (travel.WALK), is short enough; otherwise
SL's journey planner, the same one travel.py uses (travel.trip, open without a key), arriving by the next showing's
start on that very day. SL is asked only when walking doesn't make it and a fast trip still could, and once more for the
chosen legs too long to walk comfortably; at most MAX_REQUESTS a run, a second apart, and every answer is kept in
data/routes-cache.json by place and hour, so the next morning asks almost nothing.

Limits: the route starts at the first showing, since where you set off from isn't known; SL's time is door to door
for a weekday, Saturday or Sunday at that hour, not the exact minute; an estimate (SL over its limit or not answering)
is rough, about 6 minutes plus a minute per 350 m; STAY is the same everywhere; the search keeps LABELS ways to each
stop, so on a very full day a slightly better order can be missed; and each day is planned on its own, so an apartment
shown twice in the week can turn up on both days.

    .venv/bin/python scraper/route.py --dry            # print the routes, write nothing but the cache
    .venv/bin/python scraper/route.py --dry --offline  # the same without asking SL (estimates only)
"""
import argparse
import datetime as dt
import heapq
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import finder  # noqa: E402  (fits and the JSON helpers)
import travel  # noqa: E402  (SL's journey planner, distances and walking speed)

DAYS = 7            # the coming week, today included
STAY = 20           # minutes at each showing
WALK_ONLY = 12      # a walk this short is never looked up with SL
MAX_REQUESTS = 120  # SL lookups a run; past this, trips are estimated
TRIP_DAYS = 90      # a cached trip is looked up again after this long
WEIGHT = {"booked": 10, "saved": 4, "filter": 1}
LABELS = 6          # ways to reach each stop kept by the search
CACHE_VERSION = 1


def minutes(hhmm):
    h, m = hhmm.split(":")[:2]
    return int(h) * 60 + int(m)


def clock(mins):
    return f"{int(mins) // 60:02d}:{int(mins) % 60:02d}"


def fast_bound(m):
    """The least SL could take over m metres door to door: a short walk at each end and the T-bana in between."""
    return 4 + m / 600


def transit_guess(m):
    """A trip by SL when SL can't be asked."""
    return round(6 + m / 350)


class Trips:
    """Trip times between two points at a time of day: on foot, from the cache or from SL, asked as little as possible."""

    def __init__(self, data_dir, offline=False, log=print):
        self.path = Path(data_dir) / "routes-cache.json"
        store = finder.read_json(self.path, {})
        self.cache = store.get("trips", {}) if store.get("v") == CACHE_VERSION else {}
        self.offline, self.log = offline, log
        self.asked = self.failed = 0
        self.stale = (dt.datetime.now() - dt.timedelta(days=TRIP_DAYS)).isoformat()

    def key(self, a, b, day, arrive):
        kind = "sun" if day.weekday() == 6 else "sat" if day.weekday() == 5 else "wkd"
        return f"{a[0]:.4f},{a[1]:.4f}>{b[0]:.4f},{b[1]:.4f}@{kind}{arrive // 60:02d}"

    def sl(self, a, b, day, arrive):
        """SL's answer {min, lines, changes} (min None when SL has no trip), or None when it couldn't be asked."""
        k = self.key(a, b, day, arrive)
        had = self.cache.get(k)
        if had and had.get("at", "") >= self.stale:
            return had
        if self.offline or self.asked >= MAX_REQUESTS or self.failed >= 5:
            return None
        self.asked += 1
        try:
            t = travel.trip(a, b, day, clock(arrive)) or {"min": None, "changes": 0, "lines": []}
        except Exception as e:
            self.failed += 1
            if self.failed == 5:
                self.log(f"Route: SL isn't answering ({e}); the rest of the trips are estimated")
            return None
        finally:
            time.sleep(travel.PAUSE)
        self.cache[k] = {**t, "at": dt.datetime.now().isoformat(timespec="seconds")}
        return self.cache[k]

    def leg(self, a, b, day, arrive, need=None):
        """The best way from a to b arriving around `arrive` (minutes after midnight). With `need`, the minutes there
        are: a walk that fits is taken as it is, and when not even a fast trip could fit, SL isn't asked and the leg
        comes back with "short": True and its least possible time."""
        m = travel.metres(a, b)
        walk = max(1, round(m / travel.WALK))
        on_foot = {"min": walk, "mode": "walk", "source": "estimate", "m": round(m)}
        if walk <= WALK_ONLY or (need is not None and walk <= need):
            return on_foot
        if need is not None and fast_bound(m) > need:
            return {**on_foot, "min": round(fast_bound(m)), "mode": "transit", "short": True}
        t = self.sl(a, b, day, arrive)
        if t is None:
            guess = transit_guess(m)
            return on_foot if walk <= guess else {**on_foot, "min": guess, "mode": "transit"}
        if t.get("min") is None:  # SL has no trip: walk
            return on_foot
        if not t.get("lines"):  # SL's own answer is to walk
            return {**on_foot, "min": t["min"], "source": "sl"}
        if walk <= t["min"]:
            return on_foot
        return {"min": t["min"], "mode": "transit", "source": "sl", "m": round(m),
                "lines": t.get("lines", []), "changes": t.get("changes", 0)}

    def save(self):
        finder.write_json(self.path, {"v": CACHE_VERSION, "trips": self.cache})


def parse_show(s):
    """(date, start, end) in minutes after midnight, from {"start": "2026-11-15T13:15", "end": "13:45"}."""
    try:
        start = dt.datetime.fromisoformat(s["start"])
    except (KeyError, TypeError, ValueError):
        return None
    end = s.get("end") or ""
    try:
        e = minutes(end[11:16] if "T" in end else end)
    except (ValueError, IndexError):
        e = start.hour * 60 + start.minute + 30  # no end given: the usual half hour
    b = start.hour * 60 + start.minute
    return (start.date(), b, e) if e > b else (start.date(), b, b + 30)


def candidates(data_dir, now):
    """{date: [showing]} for the coming week, and the number of apartments considered."""
    d = Path(data_dir)
    plan = finder.read_json(d / "plan.json", {})
    show = plan.get("show") or {}
    f = show.get("f") or finder.read_json(d / "scrape.json", {}).get("filter", {})
    ratings = show.get("ratings") or {}
    never = lambda p: show.get("hideNever", True) and "never" in ((ratings.get(p.get("area")) or {}).get("r"),
                                                                  (ratings.get(p.get("group")) or {}).get("r"))
    items = finder.read_json(d / "listings.json", {}).get("items", {})
    saved = {a["link"]: a for a in plan.get("apts", []) if a.get("link")}
    went = set((show.get("visits") or {}).keys())
    first, last = now.date(), now.date() + dt.timedelta(days=DAYS - 1)
    today = now.hour * 60 + now.minute
    days, seen = {}, 0
    for u, p in items.items():
        if not p.get("shows") or not p.get("lat") or u in went or p.get("state") == "sold":
            continue
        mine = saved.get(u)
        if not mine and (p.get("outside") or never(p) or not finder.fits(p, f)):
            continue
        seen += 1
        why = "booked" if mine and "booked" in (mine.get("status") or "").lower() else "saved" if mine else "filter"
        for s in p["shows"]:
            got = parse_show(s)
            if not got or not first <= got[0] <= last or (got[0] == first and got[2] - min(STAY, got[2] - got[1]) < today):
                continue
            day, b, e = got
            days.setdefault(day, []).append({
                "url": u, "addr": p.get("addr") or (mine or {}).get("address") or u, "area": p.get("area") or p.get("group"),
                "pt": (p["lat"], p["lon"]), "b": b, "e": e, "stay": min(STAY, e - b), "why": why, "w": WEIGHT[why],
                "price": p.get("price"), "agent": p.get("agent"),
            })
    return days, seen


def best_order(cs, day, trips):
    """The stops with the most weight that fit, least travel among equals: [(index, arrive, leave)] and the legs."""
    n = len(cs)
    late = [c["e"] - c["stay"] for c in cs]  # the latest you can arrive and still stay
    # A label: (weight, travel, leave, chain of (index, arrive, leave), legs, apartments)
    keep = [[] for _ in range(n)]
    heap, seq = [], 0

    def add(j, label):
        nonlocal seq
        w, tr, leave = label[0], label[1], label[2]
        if any(o[0] >= w and o[2] <= leave and o[1] <= tr for o in keep[j]):
            return
        kept = [o for o in keep[j] if not (w >= o[0] and leave <= o[2] and tr <= o[1])] + [label]
        keep[j] = sorted(kept, key=lambda o: (-o[0], o[2], o[1]))[:LABELS]
        if not any(o is label for o in keep[j]):
            return
        seq += 1
        heapq.heappush(heap, (leave, seq, j, label))

    for j, c in enumerate(cs):
        add(j, (c["w"], 0, c["b"] + c["stay"], ((j, c["b"], c["b"] + c["stay"]),), (), frozenset([c["url"]])))
    best = None
    while heap:
        leave, _, i, label = heapq.heappop(heap)
        if not any(o is label for o in keep[i]):  # pushed aside by a better way here
            continue
        if best is None or (label[0], -label[1]) > (best[0], -best[1]):
            best = label
        for j, c in enumerate(cs):
            if c["url"] in label[5] or late[j] < leave:
                continue
            g = trips.leg(cs[i]["pt"], c["pt"], day, c["b"], need=late[j] - leave)
            if g.get("short"):
                continue
            arrive = max(c["b"], leave + g["min"])
            if arrive > late[j]:
                continue
            add(j, (label[0] + c["w"], label[1] + g["min"], arrive + c["stay"],
                    label[3] + ((j, arrive, arrive + c["stay"]),), label[4] + (g,), label[5] | {c["url"]}))
    return (best[3], best[4]) if best else ((), ())


def why_left(c, stops, cs, day, trips):
    """Why a showing isn't on the route: another showing of the same apartment, an overlap, or not enough time to get there."""
    for j, _, _ in stops:
        if cs[j]["url"] == c["url"]:
            return {"reason": "same apartment", "with": cs[j]["url"], "detail": f"seen at {clock(cs[j]['b'])}"}
    late, best = c["e"] - c["stay"], None
    for k in range(len(stops) + 1):  # each gap in the route: before the first stop, between two, after the last
        prev, nxt = (stops[k - 1] if k else None), (stops[k] if k < len(stops) else None)
        start = max(c["b"], prev[2]) if prev else c["b"]
        if start > late or (nxt and start + c["stay"] > nxt[1]):
            continue  # no room here even next door
        g_in = trips.leg(cs[prev[0]]["pt"], c["pt"], day, c["b"], need=late - prev[2]) if prev else {"min": 0}
        arrive = max(c["b"], prev[2] + g_in["min"]) if prev else c["b"]
        g_out = trips.leg(c["pt"], cs[nxt[0]]["pt"], day, cs[nxt[0]]["b"], need=nxt[1] - arrive - c["stay"]) if nxt else {"min": 0}
        if arrive > late:
            short, frm, g, coming = arrive - late, cs[prev[0]], g_in, True
        elif nxt and arrive + c["stay"] + g_out["min"] > nxt[1]:
            short, frm, g, coming = arrive + c["stay"] + g_out["min"] - nxt[1], cs[nxt[0]], g_out, False
        else:
            return {"reason": "less worth it", "detail": "fits only in place of a stop worth more"}
        if best is None or short < best[0]:
            best = (short, frm, g, coming)
    if best is None:
        hit = [cs[j] for j, a, lv in stops if a < c["e"] and lv > c["b"]] or [cs[stops[0][0]]]
        return {"reason": "overlap", "with": hit[0]["url"], "detail": f"overlaps {hit[0]['addr']} {clock(hit[0]['b'])}–{clock(hit[0]['e'])}"}
    short, frm, g, coming = best
    way = f"{'at least ' if g.get('short') else ''}{g['min']} min {'on foot' if g['mode'] == 'walk' else 'by SL'}"
    side = f"from {frm['addr']}" if coming else f"on to {frm['addr']}"
    return {"reason": "can't make it", "with": frm["url"], "detail": f"{way} {side}, {round(short)} min too few"}


def plan_day(day, cs, trips):
    cs.sort(key=lambda c: (c["b"], -c["w"]))
    chain, legs = best_order(cs, day, trips)
    # The chosen legs that are long on foot get SL's own time, so they show the way you'd really go
    legs = list(legs)
    for k, g in enumerate(legs):
        if g["mode"] == "walk" and g["min"] > WALK_ONLY and g["source"] == "estimate":
            nxt = cs[chain[k + 1][0]]
            better = trips.leg(cs[chain[k][0]]["pt"], nxt["pt"], day, nxt["b"])
            if better["min"] <= g["min"]:
                legs[k] = better
    # Arrival times again with the final legs (a better leg only makes them earlier)
    stops, leave = [], None
    for k, (j, _, _) in enumerate(chain):
        c = cs[j]
        arrive = c["b"] if leave is None else max(c["b"], leave + legs[k - 1]["min"])
        stops.append((j, arrive, arrive + c["stay"]))
        leave = arrive + c["stay"]
    out_stops = []
    for k, (j, arrive, lv) in enumerate(stops):
        c = cs[j]
        out_stops.append({
            "url": c["url"], "addr": c["addr"], "area": c["area"], "why": c["why"], "price": c["price"], "agent": c["agent"],
            "show": {"start": clock(c["b"]), "end": clock(c["e"])}, "arrive": clock(arrive), "leave": clock(lv),
            "wait": arrive - (stops[k - 1][2] + legs[k - 1]["min"]) if k else 0,
        })
    out_legs = []
    for k, g in enumerate(legs):
        (i, _, lv), (j, _, _) = stops[k], stops[k + 1]
        spare = cs[j]["e"] - cs[j]["stay"] - (lv + g["min"])  # minutes to spare before you'd be too late
        out_legs.append({"from": cs[i]["url"], "to": cs[j]["url"], "min": g["min"], "mode": g["mode"], "source": g["source"],
                         "m": g["m"], **({"lines": g["lines"], "changes": g["changes"]} if g.get("lines") else {}),
                         "free": cs[j]["b"] - lv if cs[j]["b"] > lv else 0, "spare": spare, **({"tight": True} if spare < 5 else {})})
    on = {j for j, _, _ in stops}
    left = []
    for j, c in enumerate(cs):
        if j not in on:
            left.append({"url": c["url"], "addr": c["addr"], "area": c["area"], "why": c["why"],
                         "show": {"start": clock(c["b"]), "end": clock(c["e"])}, **why_left(c, stops, cs, day, trips)})
    return {"date": day.isoformat(), "showings": len(cs), "stops": out_stops, "legs": out_legs, "left": left,
            "travel": sum(g["min"] for g in legs)}


def build(data_dir, now=None, offline=False, log=print):
    now = now or dt.datetime.now()
    days, seen = candidates(data_dir, now)
    trips = Trips(data_dir, offline=offline, log=log)
    out = [plan_day(day, days[day], trips) for day in sorted(days)]
    trips.save()
    return {"updated": now.isoformat(timespec="seconds"), "from": now.date().isoformat(), "days": out,
            "stay": STAY, "apartments": seen, "asked": trips.asked}


def update(data_dir, log=print, offline=False):
    """The morning run's step: writes data/routes.json and returns it."""
    r = build(data_dir, offline=offline, log=log)
    finder.write_json(Path(data_dir) / "routes.json", r)
    n = sum(len(d["stops"]) for d in r["days"])
    log(f"Route: {len(r['days'])} showing days, {n} stops, {r['asked']} trips asked of SL")
    return r


def show(r):
    mark = {"booked": " [booked]", "saved": " [saved]", "filter": ""}
    for d in r["days"]:
        day = dt.date.fromisoformat(d["date"])
        print(f"\n{day:%a %d %b}: {len(d['stops'])} of {d['showings']} showings, {d['travel']} min travelling")
        by = {}
        for k, s in enumerate(d["stops"]):
            by[s["url"]] = s
            if k:
                g = d["legs"][k - 1]
                how = "on foot" if g["mode"] == "walk" else "by " + ", ".join(g.get("lines") or ["SL"])
                note = " (estimate)" if g["source"] == "estimate" and g["mode"] != "walk" else ""
                print(f"      | {g['min']} min {how}{note}, {g['spare']} min to spare{'  TIGHT' if g.get('tight') else ''}")
            wait = f", waits {s['wait']} min" if s["wait"] else ""
            print(f"  {s['show']['start']}–{s['show']['end']}  {s['addr']} ({s['area']}){mark[s['why']]}"
                  f"  there {s['arrive']}–{s['leave']}{wait}")
        for x in d["left"]:
            print(f"   - left out {x['show']['start']}–{x['show']['end']} {x['addr']}{mark[x['why']]}: {x['reason']} ({x['detail']})")
    print(f"\n{r['apartments']} apartments with showings considered; {r['asked']} trips asked of SL")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry", action="store_true", help="print the routes instead of writing routes.json")
    ap.add_argument("--offline", action="store_true", help="don't ask SL; estimate the trips that aren't cached")
    ap.add_argument("--data", default=str(Path(__file__).resolve().parent.parent / "data"))
    a = ap.parse_args()
    if a.dry:
        show(build(a.data, offline=a.offline))
    else:
        show(update(a.data, offline=a.offline))


if __name__ == "__main__":
    main()
