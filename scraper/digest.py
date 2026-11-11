"""The morning digest: what changed since the last one, for the planner and a notification on the Mac.

After the morning run it writes data/digest.json (and a readable data/digest.md): new apartments in your
filter, saved apartments with new bids or a final price, price drops, apartments back on an agent's list
after leaving it, and the coming week's showings. What the last digest saw is kept in data/digest-state.json;
a second digest the same day compares with the same morning again, so it shows the same news.

    .venv/bin/python scraper/digest.py          # write the digest and show the notification
    .venv/bin/python scraper/digest.py --dry    # only print it
"""
import argparse
import datetime as dt
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import finder  # noqa: E402  (fits, the default filter and the JSON helpers)

PLANNER = f"http://localhost:{os.environ.get('BOSTADSPLAN_PORT', '8765')}"  # server.py's address
SHOW_DAYS = 7     # showings from now until this many days ahead
MD_ROWS = 15      # rows per section in digest.md; digest.json has them all
DAYS = ["mån", "tis", "ons", "tors", "fre", "lör", "sön"]
MONTHS = ["januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti", "september", "oktober", "november", "december"]
APT_KEYS = ("url", "addr", "area", "group", "agent", "price", "rooms", "kvm", "avgift", "floor", "state", "soon")
SAVED_KEYS = ("state", "bid", "bids", "final")


def kr(n):
    return f"{n:,} kr".replace(",", " ")


def day_of(at):
    """The finder's day of a time: a run before 04:00 belongs to the day before."""
    return (dt.datetime.fromisoformat(at) - dt.timedelta(hours=finder.DAY_STARTS)).date()


def brief(p, mine):
    return {**{k: p[k] for k in APT_KEYS if p.get(k) not in (None, "")}, "saved": p["url"] in mine}


def saved_changes(old, new):
    """What happened to a saved apartment between two readings, in Swedish."""
    out = []
    bids = f" ({new['bids']} bud)" if new.get("bids") else ""
    if new.get("final") and new["final"] != old.get("final"):
        out.append(f"Slutpris {kr(new['final'])}")
    elif new.get("state") == "sold" and old.get("state") != "sold":
        out.append("Såld")
    if new.get("bid") and new["bid"] > (old.get("bid") or 0):
        out.append(f"{'Nytt högsta bud' if old.get('bid') else 'Första budet'} {kr(new['bid'])}{bids}")
    elif (new.get("bids") or 0) > (old.get("bids") or 0):
        out.append(f"{new['bids']} bud")
    elif new.get("state") == "bidding" and old.get("state") != "bidding":
        out.append("Budgivning pågår")
    if new.get("state") == "gone" and old.get("state") != "gone" and not out:
        out.append("Annonsen är borttagen")
    return out


def build(data_dir, now=None, since=None):
    """The digest and the state to keep for the next one."""
    data = Path(data_dir)
    now = now or dt.datetime.now()
    at = now.isoformat(timespec="seconds")
    store = finder.read_json(data / "listings.json", {})
    items, followed = store.get("items", {}), store.get("followed", {})
    plan = finder.read_json(data / "plan.json", {})
    f = {**finder.DEFAULT_FILTER, **(finder.read_json(data / "scrape.json", {}).get("filter") or {})}
    names = {a["link"]: a.get("address") or a["link"] for a in plan.get("apts", []) if str(a.get("link", "")).startswith("http")}
    mine = set(names) | set(plan.get("show", {}).get("visits", {}))
    try:
        db = sqlite3.connect(f"file:{data / 'history.sqlite'}?mode=ro", uri=True)
        db.execute("SELECT 1 FROM readings LIMIT 1")
    except sqlite3.Error:
        db = None  # no history yet: no price drops, and only the list tells what's new

    # What the last digest saw. Without one, today's news is what the history first saw today.
    state = finder.read_json(data / "digest-state.json", {})
    if since:
        base = {"since": since}  # a time given by hand: compared through the history only
    elif state.get("at") and day_of(state["at"]) == day_of(at):
        base = state.get("base") or {}
    else:
        base = {"since": state.get("at"), **(state.get("now") or {})}
    on_list = {u for u, p in items.items() if not p.get("outside")}
    saved_now = {u: {k: v for k in SAVED_KEYS if (v := (followed.get(u) or items.get(u) or {}).get(k)) is not None} for u in names}
    since = base.get("since") or now.replace(hour=finder.DAY_STARTS, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    if since > at:  # before 04:00 with no digest yet
        since = (dt.datetime.fromisoformat(since) - dt.timedelta(days=1)).isoformat(timespec="seconds")
    was_listed = set(base["onList"]) if "onList" in base else None

    new, back, drops = [], [], []
    for u in sorted(on_list):
        p = items[u]
        fits = finder.fits(p, f)
        if not (fits or u in mine) or p.get("state") == "sold":
            continue
        first = db.execute("SELECT first_seen FROM apartments WHERE url=?", (u,)).fetchone() if db else None
        first = first and first[0]
        if was_listed is None:  # no digest yet: new is what the history first saw since then
            if fits and first and first > since:
                new.append(brief(p, mine))
        elif u not in was_listed:
            if first and first <= since:
                back.append({**brief(p, mine), "firstSeen": first})  # known from before, but on no list at the last digest
            elif fits:
                new.append(brief(p, mine))
        # The finder's own mark (finder.carry_over), which leaves out misread prices and the highest bid shown as the price
        if (dr := p.get("drop")) and p.get("price") and dr.get("at", "") > since:
            drops.append({**brief(p, mine), "was": dr["from"]})
        if (rl := p.get("relisted")) and rl.get("at", "") > since and u not in {a["url"] for a in back}:
            back.append({**brief(p, mine), "firstSeen": rl.get("left")})
    if db:
        db.close()

    saved = []
    for u, name in names.items():
        fo, cur = followed.get(u) or {}, saved_now[u]
        if "saved" in base:
            old = base["saved"].get(u)
        else:  # no digest yet: the last reading the finder kept from before, if the page was read since
            old = next((h for h in reversed(fo.get("history", [])) if h.get("at", "") <= since), None) if fo.get("read", "") > since else None
        if old is not None and (changes := saved_changes(old, cur)):
            saved.append({**brief({"addr": name, "agent": next((a.get("agent") for a in plan["apts"] if a.get("link") == u), None),
                                   **items.get(u, {}), **cur, "url": u}, mine), **cur, "changes": changes})

    until = (now + dt.timedelta(days=SHOW_DAYS)).strftime("%Y-%m-%dT00:00")
    showings = [{**brief(p, mine), "start": s["start"], "end": s.get("end")}
                for p in (items[u] for u in on_list) if (p["url"] in mine or finder.fits(p, f)) and p.get("state") != "sold"
                for s in p.get("shows", []) if at[:16] <= s["start"] < until]

    by_price = lambda a: (not a.get("price"), a.get("price") or 0, a.get("addr", ""))
    d = {"at": at, "since": since, "planner": PLANNER,
         "new": sorted(new, key=by_price), "saved": saved, "drops": sorted(drops, key=lambda a: a["price"] - a["was"]),
         "back": sorted(back, key=by_price), "showings": sorted(showings, key=lambda s: (s["start"], not s["saved"], s.get("addr", "")))}
    d["quiet"] = not (d["new"] or d["saved"] or d["drops"] or d["back"])
    d["summary"] = summary(d)
    return d, {"at": at, "base": {**base, "since": since}, "now": {"onList": sorted(on_list), "saved": saved_now}}


def summary(d):
    """One line for the notification."""
    n = lambda key, one, many: [f"{len(d[key])} {one if len(d[key]) == 1 else many}"] if d[key] else []
    shows = n("showings", "visning i veckan", "visningar i veckan")
    if d["quiet"]:
        return " ".join(["Inget nytt sedan senast."] + [s[0].upper() + s[1:] + "." for s in shows])
    return " · ".join(n("new", "ny i ditt filter", "nya i ditt filter") + n("saved", "sparad med nyheter", "sparade med nyheter")
                      + n("drops", "prissänkning", "prissänkningar") + n("back", "åter på listan", "åter på listan") + shows)


def when(s):
    t = dt.datetime.fromisoformat(s["start"])
    return f"{DAYS[t.weekday()]} {t.day}/{t.month} {t:%H:%M}" + (f"–{s['end']}" if s.get("end") else "")


def line(a, lead=""):
    rooms = f"{a['rooms']:g} rok".replace(".", ",") if a.get("rooms") else ""
    kvm = f"{a['kvm']:g} m²".replace(".", ",") if a.get("kvm") else ""
    parts = [a.get("group") or a.get("area"), ", ".join(x for x in (rooms, kvm) if x),
             kr(a["price"]) if a.get("price") else "pris ej satt", a.get("agent")]
    star = " ★" if a.get("saved") else ""
    return f"- {lead}[{a.get('addr') or a['url']}]({a['url']}){star} · " + " · ".join(x for x in parts if x)


def markdown(d):
    t = dt.datetime.fromisoformat(d["at"])
    out = [f"# Morgonkoll {t.day} {MONTHS[t.month - 1]} {t.year}", "", f"**{d['summary']}**", "",
           f"Sedan {d['since'].replace('T', ' ')[:16]}. Planeraren: {d['planner']}"]

    def section(title, rows):
        if rows:
            out.extend(["", f"## {title} ({len(rows)})", ""] + rows[:MD_ROWS])
            if len(rows) > MD_ROWS:
                out.append(f"- … och {len(rows) - MD_ROWS} till")

    section("Nya i ditt filter", [line(a) for a in d["new"]])
    section("Sparade med nyheter", [line(a) + " · **" + ", ".join(a["changes"]) + "**" for a in d["saved"]])
    section("Prissänkningar", [line(a) + f" · sänkt från {kr(a['was'])} ({kr(a['price'] - a['was'])})" for a in d["drops"]])
    section("Åter på listan", [line(a) for a in d["back"]])
    section("Veckans visningar", [line(s, when(s) + " · ") for s in d["showings"]])
    return "\n".join(out) + "\n"


def show_notification(text):
    """A notification on the Mac. terminal-notifier, where installed, opens the planner on a click;
    one from osascript can't open anything."""
    try:
        if tn := shutil.which("terminal-notifier"):
            cmd = [tn, "-title", "Bostadsplan", "-message", text, "-open", PLANNER, "-group", "se.bostadsplan.digest"]
        else:
            cmd = ["osascript", "-e", "on run argv", "-e", 'display notification (item 1 of argv) with title "Bostadsplan"', "-e", "end run", text]
        return subprocess.run(cmd, capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def run(data_dir=None, notify=True, dry=False, since=None):
    """Write the digest for the planner and show the notification. Returns the digest; `dry` only builds it."""
    data = Path(data_dir or finder.DATA)
    d, state = build(data, since=since)
    if dry:
        return d
    finder.write_json(data / "digest.json", d)
    (data / "digest.md").write_text(markdown(d), "utf-8")
    finder.write_json(data / "digest-state.json", state)
    if notify:
        show_notification(d["summary"])
    return d


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry", action="store_true", help="print the digest; write nothing and show no notification")
    ap.add_argument("--quiet", action="store_true", help="write the digest without the notification")
    ap.add_argument("--since", help="compare with this time (2026-11-09T07:00) instead of the last digest")
    ap.add_argument("--data", help="another data folder, for test runs")
    args = ap.parse_args()
    out = run(args.data, notify=not args.quiet, dry=args.dry, since=args.since)
    print(markdown(out) if args.dry else out["summary"])
