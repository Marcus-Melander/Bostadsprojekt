"""Price history of every apartment the finder reads, for predicting final prices.

Two tables in data/history.sqlite:
  apartments  one row per apartment: where it is, what it is, who sells it, when it was
              first and last seen, its first asking price and, once sold, its final price
  readings    one row per apartment per day: asking price, state, highest bid, number of
              bids, final price, showings and avgift as they were that day

After every run both tables are written out as CSV files for Excel or Numbers.
"""
import csv
import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS apartments (
  url TEXT PRIMARY KEY, agent TEXT, addr TEXT, area TEXT, district TEXT, lat REAL, lon REAL,
  rooms REAL, kvm REAL, floor TEXT, built INTEGER, avgift INTEGER, drift INTEGER, features TEXT,
  first_seen TEXT, last_seen TEXT, first_price INTEGER, last_price INTEGER,
  state TEXT, highest_bid INTEGER, bids INTEGER, final_price INTEGER, sold_seen TEXT, left_list TEXT
);
CREATE TABLE IF NOT EXISTS readings (
  url TEXT, day TEXT, at TEXT, price INTEGER, state TEXT, bid INTEGER, bids INTEGER, final INTEGER,
  avgift INTEGER, next_showing TEXT, showings INTEGER, on_list INTEGER,
  PRIMARY KEY (url, day)
);
"""
APT_COLS = ["url", "agent", "addr", "area", "district", "lat", "lon", "rooms", "kvm", "floor", "built", "avgift", "drift",
            "features", "first_seen", "last_seen", "first_price", "last_price", "state", "highest_bid", "bids",
            "final_price", "sold_seen", "left_list"]


class History:
    def __init__(self, data_dir):
        self.dir = Path(data_dir)
        self.db = sqlite3.connect(self.dir / "history.sqlite", check_same_thread=False)  # the finder writes under its own lock
        self.db.executescript(SCHEMA)

    def record(self, p, at, on_list=True):
        """Save today's reading of one apartment, and update what's known about it."""
        day = at[:10]
        shows = p.get("shows") or []
        state = p.get("state") or ("soon" if p.get("soon") else "for sale")
        self.db.execute(
            "INSERT OR REPLACE INTO readings VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (p["url"], day, at, p.get("price"), state, p.get("bid"), p.get("bids"), p.get("final"),
             p.get("avgift"), shows[0]["start"] if shows else None, len(shows), int(on_list)))
        old = self.db.execute("SELECT first_seen, first_price, final_price, sold_seen, highest_bid FROM apartments WHERE url=?",
                              (p["url"],)).fetchone()
        first_seen, first_price, final, sold_seen, high = old or (at, p.get("price"), None, None, None)
        if p.get("final"):
            final = p["final"]
        if state == "sold" and not sold_seen:
            sold_seen = at
        if p.get("bid"):
            high = max(high or 0, p["bid"])
        row = {
            "url": p["url"], "agent": p.get("agent"), "addr": p.get("addr"), "area": p.get("area"), "district": p.get("group"),
            "lat": p.get("lat"), "lon": p.get("lon"), "rooms": p.get("rooms"), "kvm": p.get("kvm"), "floor": p.get("floor"),
            "built": p.get("built"), "avgift": p.get("avgift"), "drift": p.get("drift"),
            "features": json.dumps(p.get("extra") or [], ensure_ascii=False),
            "first_seen": first_seen, "last_seen": at if on_list else None, "first_price": first_price or p.get("price"),
            "last_price": p.get("price"), "state": state, "highest_bid": high, "bids": p.get("bids"),
            "final_price": final, "sold_seen": sold_seen, "left_list": None if on_list else p.get("left"),
        }
        if old:  # keep what an earlier reading knew when today's page doesn't say
            cur = dict(zip(APT_COLS, self.db.execute(f"SELECT {','.join(APT_COLS)} FROM apartments WHERE url=?", (p["url"],)).fetchone()))
            row = {k: (v if v not in (None, "", "[]") else cur[k]) for k, v in row.items()}
        self.db.execute(f"INSERT OR REPLACE INTO apartments ({','.join(APT_COLS)}) VALUES ({','.join('?' * len(APT_COLS))})",
                        [row[c] for c in APT_COLS])
        self.db.commit()

    def earlier(self, p, before):
        """The one apartment at the same address, size and floor that was last on a list before `before`
        under another address on the web: the same apartment, for sale again. None when several match (new buildings)."""
        if not (p.get("addr") and p.get("kvm") and p.get("floor") and any(c.isdigit() for c in p["addr"])):
            return None
        rows = self.db.execute(
            "SELECT url, agent, last_seen, last_price FROM apartments WHERE addr=? AND kvm=? AND floor=? AND url<>? AND rooms IS ?",
            (p["addr"], p["kvm"], p["floor"], p["url"], p.get("rooms"))).fetchall()
        if len(rows) != 1 or not rows[0][2] or rows[0][2] >= before:
            return None
        return dict(zip(("url", "agent", "last_seen", "last_price"), rows[0]))

    def counts(self):
        a = self.db.execute("SELECT COUNT(*), COUNT(final_price), COUNT(highest_bid) FROM apartments").fetchone()
        r = self.db.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
        return {"apartments": a[0], "finalPrices": a[1], "withBids": a[2], "readings": r}

    def export_csv(self):
        """data/apartments.csv and data/readings.csv, in Excel's Swedish-friendly UTF-8."""
        for table, name in (("apartments", "apartments.csv"), ("readings", "readings.csv")):
            cur = self.db.execute(f"SELECT * FROM {table} ORDER BY 1, 2")
            with open(self.dir / name, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow([d[0] for d in cur.description])
                w.writerows(cur)

    def close(self):
        self.db.close()
