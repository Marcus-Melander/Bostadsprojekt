"""Predict each for-sale apartment's final price from our own price history (Phase 5, the first of two ways).

Reads data/history.sqlite and writes data/predictions.json: per apartment url {low, mid, high, basis, n, ...}.

    .venv/bin/python scraper/predict.py --dry   # summary and leave-one-out error; writes nothing
    .venv/bin/python scraper/predict.py         # write data/predictions.json

Method
  Two estimates, each built the same way: a number per sale, averaged over the apartment's neighbourhood, with
  each group pulled toward the wider group it sits in (all sales → district → neighbourhood). A group's own
  average counts as much as its wider group's when it has SHRINK sales, so a neighbourhood with one sale moves
  the estimate a little and one with twenty moves it almost all the way.
  - From the asking price: how far the final price lands above asking, log(final / asking), per neighbourhood,
    plus the agent's own lean: how far its sales land above or below what their neighbourhoods predict,
    shrunk toward nothing the same way.
  - From the size, for apartments without an asking price (or with one that looks misread): the final price
    per m² against Svensk Mäklarstatistik's latest price per m² for the district (finder.DISTRICT_KVM_PRICE,
    the planner's Områdespriser), with one slope for size (small apartments cost more per m²), per neighbourhood.
  The range is an ~80 % interval: the spread of the sales around their own estimates (log scale, ±1.28 SD),
  with each group's mean counted against the sales it was fitted on, widened by sqrt(1 + 1/n) for the n sales in
  the group the estimate leans on (its neighbourhood with 3+ sales, else its district, else all). An apartment
  with a bid never gets a range below its highest bid; a bid above the estimate moves the whole range up with it.
  `basis` says in Swedish what it rests on, `method` is "asking" or "kvm", `bid` the highest bid if any.
  Below MIN_SALES usable sales in all, or without a district and size for the per-m² estimate, there is none.

What is left out
  - Länsförsäkringar's list shows sold apartments with their asking price as the final price: on 11 Nov 2026,
    44 of its 48 finals equalled the asking price, 40 of them read as sold on the day they were first seen. A final
    equal to the asking price at an agent where most finals are (FALSE_FINAL_SHARE) is not used. A final at
    asking elsewhere (Bjurfors, Notar, BOSTHLM) is kept, since sales at asking do happen.
  - An asking price per m² below half or above twice the district's (774 193 kr for 35 m² at HusmanHagberg,
    10 000 000 kr for 35 m² at Erik Olsson, SkandiaMäklarna's 763 200 kr): a sale counts only for the per-m²
    estimate, and an apartment for sale gets the per-m² estimate, with a note in `basis`. 27 for sale on 11 Nov 2026, among them a dozen of
    HusmanHagberg's at ~30 000 kr/m², which may be real but not ordinary bostadsrätter.
  - A final price beyond 0.8–1.6 times the asking price, or a final price per m² beyond half or twice the
    district's (16 300 000 kr for 11 m²): the sale isn't used.
  - The asking price is the lowest one read while the apartment was for sale, since Erik Olsson, Notar and
    Fastighetsbyrån showed the highest bid as the price in bidding until 10 November. A fall of more than a
    quarter counts as a misread, as in the finder, and the first price is used.

Checked by leaving each usable sale out in turn, fitting on the rest and comparing with its real final price
(predict.py --dry prints it; bids are left out of the check). On 11 Nov 2026, with 108 usable sales of 154 finals, 63 of them with an asking price:
  - from the asking price: median error 3.4 %, mean 5.6 %, 9 in 10 within 12.5 %, bias +0.6 %; the 80 % range
    held the final price in 78 % of sales. Just taking the asking price is off by 9.4 % (median), 9.1 % too low.
  - from the size alone: median error 11.2 %, mean 11.9 %, 9 in 10 within 23.4 %, range held 79 %.
  - the agent's lean changes little yet (without it: median 3.5 %, mean 5.5 %); the leans are −1.3 % to +1.6 %.
    SHRINK of 2, 4, 8 and 16 gave medians of 3.4–4.4 %, so the choice of 4 is not tuned to these sales.
Limits: 11 days of sales in November 2026, mostly from five agents (Bjurfors, Alicia Edelman, Erik Olsson,
Bergets Ro, Notar), so most neighbourhoods have 0–3 sales and the estimate is mostly the district's. Östermalm
has two sales, Norrmalm one and Gamla stan none; those rest on all sales. Season, interest rates and
how long the apartment has been for sale aren't in it. Agents that don't publish final prices (Behrer,
SkandiaMäklarna, Historiska Hem, Innerstadsspecialisten, Svensk Fastighetsförmedling) have no lean of their own.
Re-run the check as sales come in and update the figures above.
"""
import argparse
import math
import sqlite3
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import finder  # noqa: E402  (the district prices per m² and the JSON helpers)

SHRINK = 4               # a group's own mean weighs as much as its wider group's at this many sales
SHRINK_AGENT = 8         # the same for an agent's lean, pulled toward none
MIN_SALES = 10           # fewer usable sales than this in all: no estimate of that kind
MIN_GROUP = 3            # the narrowest group named in `basis` has at least this many sales
Z80 = 1.2816             # ±1.28 SD covers 80 % of a normal spread
FALSE_FINAL_SHARE = 0.6  # at an agent where this share of finals equal asking (and 5+ finals), those finals aren't used
RATIO_OK = (0.8, 1.6)    # final / asking outside this: a misread
KVM_OK = (0.5, 2.0)      # price per m² / the district's outside this: a misread
DROP_OK = 0.75           # a lower asking price under this share of the first counts as a misread
LIVE = ("for sale", "bidding", "soon")


def kvm_price(district):
    return finder.DISTRICT_KVM_PRICE.get(district, finder.INNER_KVM_PRICE)


def asking_of(first, prices):
    """The asking price: the lowest read while for sale, unless it fell by more than a quarter."""
    ps = [p for p in [first, *prices] if p]
    if not ps:
        return None
    low = min(ps)
    return low if not first or low >= first * DROP_OK else first


def per_kvm_ok(price, kvm, district):
    return bool(price and kvm) and KVM_OK[0] <= price / kvm / kvm_price(district) <= KVM_OK[1]


def load(data_dir):
    """Every apartment in the history, with its asking price as described above."""
    db = sqlite3.connect(f"file:{Path(data_dir) / 'history.sqlite'}?mode=ro", uri=True)
    prices = {}
    for url, price in db.execute("SELECT url, price FROM readings WHERE state IN ('for sale', 'soon') AND price > 0"):
        prices.setdefault(url, []).append(price)
    cols = ("url", "agent", "area", "district", "kvm", "first_price", "state", "highest_bid", "final_price")
    apts = []
    for row in db.execute(f"SELECT {','.join(cols)} FROM apartments"):
        a = dict(zip(cols, row))
        a["asking"] = asking_of(a["first_price"], prices.get(a["url"], []))
        apts.append(a)
    db.close()
    return apts


def sales_of(apts):
    """The sales the estimates learn from, and why the others were set aside."""
    finals = {}
    for a in apts:
        if a["final_price"]:
            finals.setdefault(a["agent"], []).append(a["final_price"] == a["asking"])
    false_final = {ag for ag, eq in finals.items() if len(eq) >= 5 and sum(eq) / len(eq) >= FALSE_FINAL_SHARE}
    out, skipped = [], {}
    for a in apts:
        f = a["final_price"]
        if not f:
            continue
        why = None
        if a["agent"] in false_final and f == a["asking"]:
            why = "final = asking at an agent that shows asking as final"
        elif a["kvm"] and not per_kvm_ok(f, a["kvm"], a["district"]):
            why = "final price per m² misread"
        s = dict(a, ask_ok=False)
        if not why and a["asking"] and per_kvm_ok(a["asking"], a["kvm"], a["district"]):
            if RATIO_OK[0] <= f / a["asking"] <= RATIO_OK[1]:
                s["ask_ok"] = True
            else:
                why = "final / asking out of range"
        if why:
            skipped[why] = skipped.get(why, 0) + 1
        else:
            out.append(s)
    return out, skipped


class Pooled:
    """Means of y per district and neighbourhood, each pulled toward the wider group's; plus an agent lean."""

    def __init__(self, rows, agents=True):
        # rows: (y, district, area, agent)
        self.n = len(rows)
        self.mu = sum(r[0] for r in rows) / self.n
        self.groups = {}
        for y, d, ar, ag in rows:
            for key in (("d", d), ("a", d, ar), ("g", ag)):
                self.groups.setdefault(key, []).append(y)
        self.lean = {}
        if agents:
            res = {}
            for y, d, ar, ag in rows:
                res.setdefault(ag, []).append(y - self.place(d, ar))
            self.lean = {ag: sum(r) / (len(r) + SHRINK_AGENT) for ag, r in res.items()}
        res = [y - self.place(d, ar) - self.lean.get(ag, 0) for y, d, ar, ag in rows]
        # Each group's mean uses up part of a sale's freedom, as much as it is its own (n / (n + SHRINK))
        used = 1 + sum(len(ys) / (len(ys) + (SHRINK_AGENT if k[0] == "g" else SHRINK))
                       for k, ys in self.groups.items() if k[0] != "g" or agents)
        self.sd = math.sqrt(sum(r * r for r in res) / max(1, self.n - used))

    def count(self, *key):
        return len(self.groups.get(key, ()))

    def _pull(self, key, toward):
        ys = self.groups.get(key, [])
        return (sum(ys) + SHRINK * toward) / (len(ys) + SHRINK)

    def place(self, d, ar):
        return self._pull(("a", d, ar), self._pull(("d", d), self.mu))

    def predict(self, d, ar, ag):
        """(y for the place, the agent's lean, the SD of their sum, n the estimate leans on, the group's name)."""
        na, nd = self.count("a", d, ar), self.count("d", d)
        n, name = (na, ar) if na >= MIN_GROUP else (nd, d) if nd >= MIN_GROUP else (self.n, "alla")
        return self.place(d, ar), self.lean.get(ag, 0), self.sd * math.sqrt(1 + 1 / n), n, name


class Model:
    """Both estimates, fitted on a list of sales."""

    def __init__(self, sales, agents=True):
        ask = [s for s in sales if s["ask_ok"]]
        self.ask = Pooled([(math.log(s["final_price"] / s["asking"]), s["district"], s["area"], s["agent"]) for s in ask],
                          agents) if len(ask) >= MIN_SALES else None
        size = [s for s in sales if s["kvm"] and s["district"]]
        self.size = None
        if len(size) >= MIN_SALES:
            xs = [math.log(s["kvm"] / 50) for s in size]
            ys = [math.log(s["final_price"] / s["kvm"] / kvm_price(s["district"])) for s in size]
            xbar, ybar = sum(xs) / len(xs), sum(ys) / len(ys)
            self.slope = sum((x - xbar) * (y - ybar) for x, y in zip(xs, ys)) / (sum((x - xbar) ** 2 for x in xs) or 1)
            self.size = Pooled([(y - self.slope * x, s["district"], s["area"], s["agent"]) for x, y, s in zip(xs, ys, size)],
                               agents=False)

    def estimate(self, a):
        """{low, mid, high, basis, n, method} or None when there's nothing to stand on."""
        if self.ask and a["asking"] and per_kvm_ok(a["asking"], a["kvm"], a["district"]):
            y, lean, sd, n, name = self.ask.predict(a["district"], a["area"], a["agent"])
            base, method = a["asking"], "asking"
            basis = f"{pct(y)} över utropspris i {name} ({n} försäljningar)"
            if abs(lean) >= 0.005:
                basis += f", {a['agent']} {pct(lean)}"
            y += lean
        elif self.size and a["kvm"] and a["district"]:
            y, _, sd, n, name = self.size.predict(a["district"], a["area"], a["agent"])
            y += self.slope * math.log(a["kvm"] / 50)
            base, method = a["kvm"] * kvm_price(a["district"]), "kvm"
            basis = f"{round(math.exp(y) * kvm_price(a['district']) / 1000)} tkr/m² i {name} ({n} försäljningar)"
            if a["asking"]:  # a misread, or not an ordinary bostadsrätt (HusmanHagberg's at ~30 000 kr/m²)
                basis += f"; utropspriset {kr(a['asking'])} är inte med"
        else:
            return None
        mid, w = base * math.exp(y), Z80 * sd
        return {"low": tens(mid * math.exp(-w)), "mid": tens(mid), "high": tens(mid * math.exp(w)),
                "basis": basis, "n": n, "method": method}


def tens(x):
    """To the nearest 10 000 kr."""
    return int(round(x, -4))


def kr(n):
    return f"{n:,} kr".replace(",", " ")


def pct(y):
    p = (math.exp(y) - 1) * 100
    return f"{'+' if p >= 0 else '−'}{abs(p):.1f} %".replace(".", ",")


def leave_one_out(sales, agents=True):
    """Per method: the errors (estimate / final − 1) and how often the range held the final price."""
    out = {"asking": [], "kvm": []}
    for i, s in enumerate(sales):
        m = Model(sales[:i] + sales[i + 1:], agents)
        for method in ("asking", "kvm"):
            if method == "asking" and not s["ask_ok"]:
                continue
            e = m.estimate(dict(s, asking=s["asking"] if method == "asking" else None))
            if e and e["method"] == method:
                f = s["final_price"]
                out[method].append((e["mid"] / f - 1, e["low"] <= f <= e["high"]))
    return out


def predictions(apts, model):
    out = {}
    for a in apts:
        if a["state"] not in LIVE:
            continue
        e = model.estimate(a)
        if not e:
            continue
        bid = a["highest_bid"]
        if bid and bid > e["low"]:
            # The final price is at least the highest bid; past the estimate, the range moves up with it
            up = max(1, bid / e["mid"])
            e["low"], e["mid"], e["high"] = bid, tens(e["mid"] * up), tens(e["high"] * up)
            e["basis"] += f"; högsta bud {kr(bid)}"
        if bid:
            e["bid"] = bid
        out[a["url"]] = e
    return out


def describe(errs):
    if not errs:
        return "no sales to check"
    ab = sorted(abs(e) for e, _ in errs)
    return (f"{len(errs)} sales, median error {statistics.median(ab) * 100:.1f} %, mean {statistics.mean(ab) * 100:.1f} %, "
            f"90 % within {ab[int(0.9 * (len(ab) - 1))] * 100:.1f} %, bias {statistics.mean(e for e, _ in errs) * 100:+.1f} %, "
            f"range held {sum(h for _, h in errs) / len(errs) * 100:.0f} %")


def update(data_dir=None, log=print, dry=False):
    """Write data/predictions.json for every apartment for sale. Returns the predictions."""
    data = Path(data_dir or finder.DATA)
    apts = load(data)
    sales, skipped = sales_of(apts)
    model = Model(sales)
    preds = predictions(apts, model)
    live = sum(a["state"] in LIVE for a in apts)
    by = {m: sum(p["method"] == m for p in preds.values()) for m in ("asking", "kvm")}
    log(f"Predictions: {len(preds)} of {live} apartments for sale ({by['asking']} from the asking price, {by['kvm']} from "
        f"the size), from {sum(s['ask_ok'] for s in sales)} sales with an asking price and {len(sales)} in all")
    if dry:
        log(f"Sales set aside: {skipped or 'none'}")
        loo = leave_one_out(sales)
        log(f"Leave one out, from the asking price: {describe(loo['asking'])}")
        log(f"Leave one out, from the size:         {describe(loo['kvm'])}")
        log(f"  without the agent's lean:           {describe(leave_one_out(sales, agents=False)['asking'])}")
        log(f"  the asking price itself:            {describe([(s['asking'] / s['final_price'] - 1, False) for s in sales if s['ask_ok']])}")
        if model.ask:
            log(f"All sales: {pct(model.ask.mu)} over asking, spread {model.ask.sd * 100:.1f} %; agents' lean: "
                + ", ".join(f"{ag} {pct(v)}" for ag, v in sorted(model.ask.lean.items(), key=lambda x: -x[1])))
        for url, p in list(preds.items())[:8]:
            log(f"  {p['low']:>11,.0f} {p['mid']:>11,.0f} {p['high']:>11,.0f}  {p['basis']}  {url}")
    else:
        finder.write_json(data / "predictions.json", preds)
    return preds


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry", action="store_true", help="print a summary and the leave-one-out error; write nothing")
    ap.add_argument("--data", help="another data folder")
    args = ap.parse_args()
    update(args.data, dry=args.dry)
