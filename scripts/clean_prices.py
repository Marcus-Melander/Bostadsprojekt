"""Clears asking prices that were read wrong from the price history and the finder's listings.

Four kinds of misread, found on 12 November 2026 and checked against the agents' pages that day:
  liknande  Tradition's "Pris på förfrågan" apartments took the price of the "Liknande bostad" card below them
            until the page was cut at that heading (11 November): the same price as another apartment that
            day, or any price before the cut when the cut page shows none. At the other agents, until the first cut
            on 1–2 November: a price another of the agent's apartments had that day, never read again after
  amount    another amount on a page without a price: the förening's income ("lokalintäkter om cirka 650 000 kr
            per år"), SkandiaMäklarna's "Indirekt nettoskuldsättning: 809 569 kr" or a tomträttsavgäld of 763 200 kr.
            Not a round thousand, or an amount in the apartment's description
  bid       before the finder kept the price from before the bidding (11 November), the highest bid as the price
            (Erik Olsson, Notar, Tradition, Fastighetsbyrån): put back to the price before the bidding, or emptied
            when that was a bid too
  jump      one-offs looked at on the page (CHECKED below)
Prices far below the size times the district's price per m², or far off the latest one, are listed to look at
and left alone: HusmanHagberg's Döbelnsgatan 114C (54.5 m²) really says "1 250 000 kr utgångspris".

The wrong prices are emptied in data/history.sqlite's readings (the bid kind gets its earlier asking price back),
apartments.first_price and last_price are worked out again, and the same price is emptied in data/listings.json,
so the next run reads it again instead of keeping it. Both files are copied to data/backup/ first.

    .venv/bin/python scripts/clean_prices.py            # what it would change (--dry, the default)
    .venv/bin/python scripts/clean_prices.py --apply    # change it; not while the finder runs
    .venv/bin/python scripts/clean_prices.py --data /some/copy --apply
"""
import argparse
import collections
import datetime as dt
import json
import os
import re
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper"))
from finder import DISTRICT_KVM_PRICE, INNER_KVM_PRICE, finder_running  # noqa: E402

CUT = "2026-11-11"     # the first run that cut pages at "Liknande bostad" and kept the price from before bidding
TOO_LOW = 0.25         # listed to look at: below a quarter of the size times the district's price per m²
JUMP = 1.25            # listed to look at: more than a quarter off the apartment's latest price
AMOUNT_RX = re.compile(r"(\d{1,3}(?:[ .,\u00a0]\d{3}){1,3})\s*kr", re.I)

# Looked at on the agents' pages on 12 November 2026: prices no rule finds, and Tradition's own prices that look
# like a card's (2 995 000 kr is a common price)
CHECKED = {
    "https://aliciaedelman.se/bostad/lagenhet/brannkyrkagatan-45-stockholm-obj5q2m5s63hcmxnpk9bt":
        {380000: ("amount", "the förening's \"hyresintäkter om cirka 380 000 kr\"; the page has no price")},
    "https://aliciaedelman.se/bostad/lagenhet/krukmakargatan-5-stockholm-obj5pt9nxt3hcmps8kdbz":
        {650000: ("amount", "\"lokalintäkter om cirka 650 000 kr per år\"; no price")},
    "https://www.husmanhagberg.se/objekt/vastmannagatan-54-vasastan-odenplan/OBJ5Q6FK5836FV6MTBPBT/":
        {1250000: ("amount", "\"Årliga hyresintäkter överstigande 1 250 000 kr / år\"; Kommande®, no price")},
    "https://www.erikolsson.se/homes/Lagenhet-2rum-Sodermannagatan-21-Sodermalm-Stockholm-kommun-5999562":
        {3295000: ("jump", "Utgångspris 4 295 000 kr; 3 295 000 kr isn't on the page")},
    "https://www.husmanhagberg.se/objekt/lindvallsplan-10-sodermalm-hornstull/OBJ5Q5ZVJV3DVKQRBVZB3/":
        {3650000: ("jump", "2 695 000 kr two days later; 3 650 000 kr is a card in HusmanHagberg's list of other apartments")},
    "https://tradition.se/bostad/lagenhet/drottningholmsvagen-4-stockholm-obj5q2c6hv2shmdvlqlbt":
        {9495000: ("liknande", "Pris på förfrågan"), 10250000: ("liknande", "Pris på förfrågan")},
}
OWN = {"https://tradition.se/bostad/lagenhet/ola-hanssonsgatan-9-stockholm-obj5pv9dz32gwg6cf3pbj": {2995000}}


def find(db, store):
    """{(url, day): (kind, wrong price, price to put back or None)} for every reading to change, and a list of
    readings that look odd but weren't changed."""
    apts = {r[0]: dict(zip(("agent", "addr", "district", "kvm"), r[1:]))
            for r in db.execute("SELECT url, agent, addr, district, kvm FROM apartments")}
    rows = collections.defaultdict(list)
    for url, day, price, state, bid in db.execute("SELECT url, day, price, state, bid FROM readings ORDER BY day"):
        rows[url].append({"day": day, "price": price, "state": state, "bid": bid})
    known = {**store.get("left", {}), **store.get("items", {})}
    same_day = collections.Counter((r["day"], r["price"]) for rs in rows.values() for r in rs if r["price"])
    same_agent = collections.Counter(((apts.get(u) or {}).get("agent"), r["day"], r["price"])
                                     for u, rs in rows.items() for r in rs if r["price"])
    out = {}

    def mark(url, r, kind, back=None):
        if (url, r["day"]) not in out:
            out[(url, r["day"])] = (kind, r["price"], back)

    for url, rs in rows.items():
        a, item = apts.get(url) or {}, known.get(url) or {}
        for r in rs:
            if r["price"] in CHECKED.get(url, {}):
                mark(url, r, CHECKED[url][r["price"]][0])
        # liknande: Tradition before the cut. Wrong when the cut page has no price (read since, for sale, without
        # one; in bidding the finder carried the old one on), or when another apartment had the same price that day
        if a.get("agent") == "Tradition":
            reread = (item.get("read") or "") >= CUT
            own = OWN.get(url, set()) | ({item.get("price")} if reread and item.get("state") == "for sale" else set())
            wrong = {r["price"] for r in rs if r["day"] < CUT and r["price"] and r["price"] not in own
                     and (reread or same_day[(r["day"], r["price"])] > 1)}
            for r in rs:  # also where the finder carried it on past the cut
                if r["price"] in wrong:
                    mark(url, r, "liknande")
        # amount: not a round thousand, or an amount from the description (the förening's income, its tomträttsavgäld)
        prose = {int(re.sub(r"\D", "", m.group(1))) for m in AMOUNT_RX.finditer(item.get("desc") or "")}
        for r in rs:
            if r["price"] and (r["price"] % 1000 or r["price"] in prose):
                mark(url, r, "amount")
        # bid: before the cut, the highest bid as the price, above the price read before the bidding; put that back,
        # unless it was a bid itself (above the lowest bid: Kocksgatan 51's 3 725 000 kr was the third bid)
        bids = {r["bid"] for r in rs if r["bid"]} | {b["amount"] for b in store.get("followed", {}).get(url, {}).get("bidList", [])}
        before, wrong = None, {}
        for r in rs:
            if not r["price"] or (url, r["day"]) in out:
                continue
            if r["day"] < CUT and r["price"] in bids and (before and r["price"] > before or
                                                         r["state"] != "for sale" and r["price"] > min(bids)):
                wrong[r["price"]] = before if before and before < r["price"] and before <= min(bids) else None
                mark(url, r, "bid", wrong[r["price"]])
            elif r["price"] in wrong and r["state"] != "for sale":  # carried on in bidding after the cut
                mark(url, r, "bid", wrong[r["price"]])
            elif before is None or (r["state"] == "for sale" and r["price"] != r["bid"] and not wrong):  # or lowered
                before = r["price"]
        # liknande at any agent: before the cut, another of the agent's apartments had the same price that day, and
        # no later reading has a price (Erik Olsson's 3 295 000 kr on five apartments on 31 October, HusmanHagberg's
        # 3 650 000 kr from Norrbackagatan 49C on four on 1 November)
        for p in {r["price"] for r in rs if r["price"] and r["day"] < CUT}:
            last = max(i for i, r in enumerate(rs) if r["price"] == p)
            if rs[last]["day"] < CUT and rs[last + 1:] and not any(x["price"] for x in rs[last + 1:]) \
                    and any(same_agent[(a.get("agent"), r["day"], p)] > 1 for r in rs if r["price"] == p):
                for r in rs:
                    if r["price"] == p:
                        mark(url, r, "liknande")
    # To look at, not changed: far below what the size costs, or far off the apartment's latest price
    odd = []
    for url, rs in rows.items():
        a = apts.get(url) or {}
        good = [r for r in rs if r["price"] and (url, r["day"]) not in out]
        if not good:
            continue
        low = a.get("kvm") and good[-1]["price"] < TOO_LOW * a["kvm"] * DISTRICT_KVM_PRICE.get(a.get("district"), INNER_KVM_PRICE)
        far = sorted({r["price"] for r in good if max(r["price"], good[-1]["price"]) / min(r["price"], good[-1]["price"]) > JUMP})
        if low or far:
            odd.append((url, "low for its size" if low else "", far, good[-1]["price"]))
    return out, apts, rows, odd


def plan(db, store):
    fixes, apts, rows, odd = find(db, store)
    by_url = collections.defaultdict(list)
    for (url, day), v in sorted(fixes.items()):
        by_url[url].append((day, *v))
    listing_fixes = []
    for url, fx in by_url.items():
        wrong = {p: back for _, _, p, back in fx}
        last_kind = fx[-1][1]
        for part in ("items", "left"):
            p = store.get(part, {}).get(url)
            if p and p.get("price") in wrong:
                listing_fixes.append((part, url, p["price"], wrong[p["price"]], last_kind))
    return fixes, by_url, apts, rows, listing_fixes, odd


def kr(n):
    return f"{n:,} kr".replace(",", " ") if n else "empty"


def report(fixes, by_url, apts, rows, listing_fixes, odd):
    kinds = collections.Counter(v[0] for v in fixes.values())
    apts_by_kind = collections.Counter()
    for url, fx in by_url.items():
        for k in {f[1] for f in fx}:
            apts_by_kind[k] += 1
    print(f"{len(fixes)} readings of {len(by_url)} apartments: " +
          ", ".join(f"{k} {kinds[k]} readings / {apts_by_kind[k]} apartments" for k in ("liknande", "amount", "bid", "jump")))
    for url, fx in sorted(by_url.items(), key=lambda x: (apts[x[0]]["agent"], apts[x[0]]["addr"] or "")):
        a = apts[url]
        vals = collections.OrderedDict()
        for day, kind, p, back in fx:
            vals.setdefault((kind, p, back), []).append(day[5:])
        kept = sorted({r["price"] for r in rows[url] if r["price"] and (url, r["day"]) not in fixes})
        print(f"  {a['agent']}, {a['addr']}, {a['kvm']} m²  ({url})")
        for (kind, p, back), days in vals.items():
            print(f"      {kind:8} {kr(p):>14} on {' '.join(days)} -> {kr(back)}")
        print(f"      keeps {', '.join(kr(p) for p in kept) or 'no price'}")
    print(f"listings.json: {len(listing_fixes)} prices")
    for part, url, p, back, kind in listing_fixes:
        print(f"  {part:5} {apts[url]['agent']}, {apts[url]['addr']}: {kr(p)} -> {kr(back)} ({kind})")
    print(f"Not changed, to look at: {len(odd)}")
    for url, why, far, last in odd:
        a = apts[url]
        far = f"earlier {', '.join(kr(p) for p in far)}" if far else ""
        print(f"  {a['agent']}, {a['addr']}, {a['kvm']} m²: {kr(last)} {why}{'; ' if why and far else ''}{far}  ({url})")


def apply(data, db, store, fixes, listing_fixes):
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = data / "backup"
    backup.mkdir(exist_ok=True)
    with sqlite3.connect(backup / f"history-{stamp}.sqlite") as copy:
        db.backup(copy)
    shutil.copy2(data / "listings.json", backup / f"listings-{stamp}.json")
    print(f"Backed up to {backup}/history-{stamp}.sqlite and listings-{stamp}.json")
    with db:
        for (url, day), (_, _, back) in fixes.items():
            db.execute("UPDATE readings SET price=? WHERE url=? AND day=?", (back, url, day))
        for (url,) in {(u,) for u, _ in fixes}:
            ps = [p for (p,) in db.execute("SELECT price FROM readings WHERE url=? AND price IS NOT NULL ORDER BY day", (url,))]
            db.execute("UPDATE apartments SET first_price=?, last_price=? WHERE url=?",
                       (ps[0] if ps else None, ps[-1] if ps else None, url))
    for part, url, _, back, _ in listing_fixes:
        p = store[part][url]
        if (p.get("drop") or {}).get("from") == p["price"]:
            del p["drop"]  # a lowered price measured from the wrong one
        p["price"] = back
    tmp = (data / "listings.json").with_suffix(".tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, data / "listings.json")
    print(f"Changed {len(fixes)} readings and {len(listing_fixes)} listings.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry", action="store_true", help="only print what would change (the default)")
    g.add_argument("--apply", action="store_true", help="change the files, after backing them up")
    ap.add_argument("--data", type=Path, default=ROOT / "data", help="the data folder (default: data/)")
    args = ap.parse_args()
    data = args.data.resolve()
    lock = data / ".scrape.lock"
    if args.apply and lock.exists() and finder_running(lock.read_text()):
        print("The finder is running; try again when it's done.", file=sys.stderr)
        return 1
    db = sqlite3.connect(data / "history.sqlite") if args.apply else \
        sqlite3.connect(f"file:{data / 'history.sqlite'}?mode=ro", uri=True)
    store = json.loads((data / "listings.json").read_text("utf-8"))
    fixes, by_url, apts, rows, listing_fixes, odd = plan(db, store)
    report(fixes, by_url, apts, rows, listing_fixes, odd)
    if args.apply:
        apply(data, db, store, fixes, listing_fixes)
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
