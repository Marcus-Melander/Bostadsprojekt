"""Predict each for-sale apartment's final price from the apartment itself, without prices (Phase 5, the second way).

Reads data/history.sqlite, data/listings.json, data/travel.json and data/floorplans/index.json, and writes
data/model.json: per apartment url {low, mid, high, kvm, basis, reasons}.

    .venv/bin/python scraper/model.py --dry   # the cross-validated error against predict.py's; writes nothing
    .venv/bin/python scraper/model.py         # write data/model.json

Method
  A ridge regression (scikit-learn's RidgeCV, its penalty picked by leave-one-out on the sales) of the final price
  per m² against Svensk Mäklarstatistik's for the district (as in predict.py), on a log scale, from:
  - the apartment: size (log), rooms per m², floor, ground floor, top floor ("4 av 4"), lift, build-year era,
    avgift per m², and the description's features (balcony, water view, stambyte done, fireplace ...);
  - the place: district and neighbourhood (one column each, so a neighbourhood with few sales is pulled toward
    its district by the penalty) and the distance to Sergels torg;
  - the förening: tomträtt, debt per m² (from the annual report, else from the facts' "Låg skuld: 4 000 kr/m²"),
    savings, energy and interest sensitivity per m², stambyte coming, avgift going up. Most sales were read off an
    agent's list of sold apartments and never had their own listing, so these come from any listing, for sale or
    gone, at the same street address: the same house, so the same förening;
  - the floor plan: how many there are and the first one's aspect (data/floorplans/index.json); plan_features() is
    the place for an image model's numbers later;
  - the walk to the nearest grocery shop (travel.json) where known.
  Missing numbers take the sales' median, with a column saying they were missing where that's common.
  The range is an ~80 % interval, ±1.28 SD of the leave-one-out errors on a log scale. `reasons` are the features
  that move this apartment's price most from an average sale's, in Swedish, e.g. "Mariatorget +7 %".
  No asking price, bid or agent goes in, so the estimate can be set beside predict.py's as a second opinion.

Checked by leaving each usable sale (predict.sales_of) out in turn, picking the penalty and fitting on the rest
(model.py --dry; --trees adds boosted trees). On 16 Nov 2026, with 125 usable sales:
  - this model: median error 8.8 %, mean 10.5 %, 9 in 10 within 23.2 %; boosted trees (depth 2, 8 sales a leaf)
    9.3 %; predict.py from the size on the same sales 11.3 %, and this model with only size and place 11.1 %.
  - on the 74 sales with an asking price, predict.py's estimate from it is off by 3.8 % and this model by 9.9 %;
    blending 10–30 % of this into it makes the median worse (4.2–4.4 %), the two errors correlate 0.43.
  So it adds something only where there is no usable asking price, in place of predict.py's per-m² estimate.
  Leaving out one group of features at a time (förening, plan, grocery, distance, description, era, floor) moved
  the median between 8.3 % and 9.4 %, so no single group is shown to help yet; together they take it from 11.1 %.
  The förening is known for 45 of the sales (by address), its report for 5, so the report's savings, energy and
  interest sensitivity are left out until MIN_KNOWN sales have them. Re-run the check as sales come in.
"""
import argparse
import json
import math
import re
import sqlite3
import statistics
import sys
from pathlib import Path

import numpy as np
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.linear_model import RidgeCV

sys.path.insert(0, str(Path(__file__).resolve().parent))
import finder  # noqa: E402  (the district prices per m² and the JSON helpers)
import predict  # noqa: E402  (the sales and the asking-price estimate to compare with)

ALPHAS = np.logspace(-1, 3, 25)  # the ridge penalties tried
Z80 = predict.Z80
SERGELS_TORG = (59.3326, 18.0649)
MIN_SALES = 30                   # fewer usable sales than this: no model
MIN_KNOWN = 20                   # a feature known for fewer sales than this is left out (it would fit those few)

# Description features (history's `features`, the listing's `extra`) and their Swedish names
EXTRAS = {"balcony": "balkong", "patio": "uteplats", "water view": "sjöutsikt", "fireplace": "öppen spis",
          "high ceilings": "högt i tak", "herringbone parquet": "fiskbensparkett", "renovated kitchen": "renoverat kök",
          "renovated bathroom": "renoverat badrum", "tiled bathroom": "kaklat badrum", "stambyte done": "stambyte gjort",
          "corner flat": "hörnlägenhet", "quiet courtyard side": "mot gården", "washing machine": "tvättmaskin",
          "dishwasher": "diskmaskin", "storage": "förråd"}
MISSING = {"built_missing", "avgift_missing", "forening_missing", "plan_missing"}  # never given as a reason
NAMES = {"size": "storlek", "rooms_per_kvm": "antal rum för ytan", "floor": "våning", "ground": "bottenvåning",
         "top": "högsta våningen", "lift": "hiss", "era_old": "byggt före 1930", "era_funkis": "byggt 1930–59",
         "era_late": "byggt 1960–99", "era_new": "byggt 2000 eller senare", "built_missing": "byggår okänt",
         "avgift_kvm": "avgift per m²", "avgift_missing": "avgift okänd", "centre_km": "avstånd till city",
         "tomtratt": "tomträtt", "skuld_kvm": "föreningens skuld", "forening_missing": "föreningen okänd",
         "sparande_kvm": "föreningens sparande", "energi_kvm": "energi per m²", "rantekanslighet": "räntekänslighet",
         "stambyte_coming": "stambyte på gång", "avgift_up": "avgiften höjs", "plans": "planritningar",
         "plan_aspect": "planritningens form", "plan_missing": "ingen planritning", "grocery_m": "väg till matbutik",
         **{"x_" + k: v for k, v in EXTRAS.items()}}


def km(a, b):
    dy = (a[0] - b[0]) * 111.2
    dx = (a[1] - b[1]) * 111.2 * math.cos(math.radians(a[0]))
    return math.hypot(dx, dy)


def street(addr):
    """'Swedenborgsgatan 27 B, 4 tr' → 'swedenborgsgatan 27': the house, for finding its förening."""
    a = (addr or "").lower().split(",")[0]
    m = re.match(r"\s*(\D+?)\s*(\d+)", a)
    return f"{m.group(1)} {m.group(2)}" if m else None


def num(x):
    try:
        return float(str(x).replace(",", "."))
    except (TypeError, ValueError):
        return None


def plan_features(entries):
    """Numbers from an apartment's floor plans (data/floorplans/index.json entries). An image model goes here."""
    if not entries:
        return {"plans": 0, "plan_aspect": None}
    w, h = entries[0].get("width"), entries[0].get("height")
    return {"plans": min(len(entries), 3), "plan_aspect": math.log(max(w, h) / min(w, h)) if w and h else None}


def forening(listings):
    """The förening's figures from the listings of one house (its own first, then others at the same address)."""
    f = {"tomtratt": None, "skuld_kvm": None, "sparande_kvm": None, "energi_kvm": None, "rantekanslighet": None,
         "stambyte_coming": None, "avgift_up": None}
    for x in listings:
        r = x.get("report") or {}
        for key, src in (("skuld_kvm", "skuldKvm"), ("sparande_kvm", "sparandeKvm"), ("energi_kvm", "energiKvm"),
                         ("rantekanslighet", "rantekanslighet")):
            if f[key] is None and r.get(src) is not None:
                f[key] = r[src]
        labels = [fl.get("label", "") for fl in x.get("flags") or [] if isinstance(fl, dict)]
        if not labels and not r:
            continue
        if f["tomtratt"] is None and labels:
            f["tomtratt"] = float(any(lb == "Tomträtt" for lb in labels))
            f["stambyte_coming"] = float(any("Stambyte på gång" in lb or "Stambyte planerat" in lb for lb in labels))
            f["avgift_up"] = float(any(lb.startswith("Avgiften höjs") for lb in labels))
        if f["skuld_kvm"] is None:
            for lb in labels:
                if lb in ("Skuldfri förening", "Ingen skuld", "Ingen nettoskuld"):
                    f["skuld_kvm"] = 0
                m = re.search(r"skuld:\s*([\d\s ]+)\s*kr", lb)
                if m:
                    f["skuld_kvm"] = int(re.sub(r"\D", "", m.group(1)))
    return f


def load(data_dir):
    """Every apartment in the history with its features; and the usable sales (predict.sales_of)."""
    data = Path(data_dir)
    apts = predict.load(data)
    db = sqlite3.connect(f"file:{data / 'history.sqlite'}?mode=ro", uri=True)
    cols = ("url", "addr", "lat", "lon", "rooms", "floor", "built", "avgift", "features")
    more = {r[0]: dict(zip(cols, r)) for r in db.execute(f"SELECT {','.join(cols)} FROM apartments")}
    db.close()
    lj = finder.read_json(data / "listings.json", {})
    listings = {**(lj.get("left") or {}), **(lj.get("items") or {})}
    houses = {}
    for x in listings.values():
        houses.setdefault(street(x.get("addr")), []).append(x)
    plans = {}
    for name, p in finder.read_json(data / "floorplans" / "index.json", {}).items():
        plans.setdefault(p.get("listing"), []).append(p)
    near = finder.read_json(data / "travel.json", {}).get("near") or {}
    for a in apts:
        a.update(more.get(a["url"], {}))
        a["x"] = features(a, listings.get(a["url"]), houses.get(street(a.get("addr")), []), plans.get(a["url"]),
                          near.get(a["url"]))
    return apts, predict.sales_of(apts)[0]


def features(a, own, house, plans, near):
    """{name: number or None} for one apartment; None is filled in later."""
    own = own or {}
    kvm = a.get("kvm")
    x = {"size": math.log(kvm / 50) if kvm else None,
         "rooms_per_kvm": (a.get("rooms") or own.get("rooms") or 0) / kvm * 10 if kvm and (a.get("rooms") or own.get("rooms")) else None}
    fl = num(a.get("floor") or own.get("floor"))
    text = (own.get("floorText") or "").lower()
    of = re.search(r"(-?[\d.,]+)\s*av\s*(\d+)", text)
    x["floor"] = min(fl, 8) if fl is not None else None
    x["ground"] = float(fl < 1) if fl is not None else None
    x["top"] = float(num(of.group(1)) >= num(of.group(2))) if of and num(of.group(1)) is not None else 0.0
    extras = set(json.loads(a.get("features") or "[]")) | {e for e in own.get("extra") or [] if isinstance(e, str)}
    x["lift"] = float("lift" in extras or "hiss finns" in text)
    for k in EXTRAS:
        x["x_" + k] = float(k in extras)
    built = a.get("built") or own.get("built")
    x["built_missing"] = float(not built)
    for name, lo, hi in (("era_old", 0, 1930), ("era_funkis", 1930, 1960), ("era_late", 1960, 2000), ("era_new", 2000, 9999)):
        x[name] = float(bool(built) and lo <= built < hi)
    av = a.get("avgift") or own.get("avgift")
    x["avgift_kvm"] = av * 12 / kvm / 1000 if av and kvm else None
    x["avgift_missing"] = float(x["avgift_kvm"] is None)
    x["centre_km"] = km((a["lat"], a["lon"]), SERGELS_TORG) if a.get("lat") else None
    f = forening(([own] if own else []) + [h for h in house if h is not own])
    x.update({k: (v / 1000 if k.endswith("_kvm") and v is not None else v) for k, v in f.items()})
    x["forening_missing"] = float(f["tomtratt"] is None and f["skuld_kvm"] is None)
    x.update(plan_features(plans))
    x["plan_missing"] = float(not plans)
    x["grocery_m"] = (near.get("grocery") or {}).get("m") if near else None
    return x


class Model:
    """The ridge regression, fitted on a list of sales; Model(sales, gbm=True) is the boosted trees to compare with."""

    def __init__(self, sales, gbm=False):
        self.names = sorted(n for n in sales[0]["x"] if sum(s["x"][n] is not None for s in sales) >= MIN_KNOWN)
        self.fill = {n: statistics.median([s["x"][n] for s in sales if s["x"][n] is not None] or [0]) for n in self.names}
        self.districts = sorted({s["district"] for s in sales if s["district"]})
        self.areas = sorted({(s["district"], s["area"]) for s in sales if s["area"]})
        X, y = self.matrix(sales), np.array([target(s) for s in sales])
        self.mean, self.sd = X.mean(0), X.std(0)
        self.sd[self.sd == 0] = 1
        nnum = len(self.names)
        self.sd[nnum:] = 1  # place columns stay 0/1 so the penalty pulls a small neighbourhood toward its district
        Z = (X - self.mean) / self.sd
        if gbm:
            self.fit = GradientBoostingRegressor(n_estimators=150, learning_rate=0.05, max_depth=2, min_samples_leaf=8,
                                                 subsample=0.8, random_state=0).fit(Z, y)
            self.loo_sd = None
            return
        self.fit = RidgeCV(alphas=ALPHAS, store_cv_results=True).fit(Z, y)
        loo = self.fit.cv_results_[:, list(ALPHAS).index(self.fit.alpha_)]
        self.loo_sd = float(np.sqrt(np.mean((loo - y) ** 2) * len(y) / max(1, len(y) - 1)))

    def row(self, a):
        x = [a["x"][n] if a["x"][n] is not None else self.fill[n] for n in self.names]
        x += [float(a["district"] == d) for d in self.districts]
        x += [float((a["district"], a["area"]) == k) for k in self.areas]
        return x

    def matrix(self, apts):
        return np.array([self.row(a) for a in apts], dtype=float)

    def predict_log(self, apts):
        return self.fit.predict((self.matrix(apts) - self.mean) / self.sd)

    def reasons(self, a, top=3):
        """The features that move this apartment's estimate most from an average sale's: [(Swedish name, log change)]."""
        z = (np.array(self.row(a)) - self.mean) / self.sd
        parts = dict(zip(self.names, self.fit.coef_[:len(self.names)] * z[:len(self.names)]))
        parts["place"] = float(self.fit.coef_[len(self.names):] @ z[len(self.names):])
        out = []
        for n, v in sorted(parts.items(), key=lambda kv: -abs(kv[1])):
            if abs(v) < 0.01 or len(out) == top:
                break
            if n not in MISSING and (n == "place" or a["x"][n] is not None):
                out.append((self.said(n, a), v))
        return out

    def said(self, n, a):
        """A reason's name in Swedish, with the apartment's own value where it is a number."""
        v = a["x"].get(n)
        if n == "place":
            return a["area"] or a["district"] or "läget"
        if n == "centre_km":
            return f"{v:.1f} km till city".replace(".", ",")
        if n == "grocery_m":
            return f"{round(v, -1):.0f} m till matbutik"
        if n == "size":
            return f"{a['kvm']:g} m²"
        if n == "rooms_per_kvm":
            return f"{a['rooms']:g} rum på {a['kvm']:g} m²"
        if n == "floor":
            return f"våning {a['floor']}"
        if n == "avgift_kvm":
            return f"avgift {v * 1000:.0f} kr/m² och år"
        if n == "skuld_kvm":
            return f"föreningens skuld {v * 1000:,.0f} kr/m²".replace(",", " ")
        if n.startswith(("x_", "era_")) or n in ("ground", "top", "lift", "tomtratt", "stambyte_coming", "avgift_up"):
            return NAMES[n] if v else "ej " + NAMES[n]
        return NAMES.get(n, n)

    def estimate(self, a):
        """{low, mid, high, kvm, basis, reasons} or None without a size or district."""
        if not a.get("kvm") or not a.get("district"):
            return None
        y = float(self.predict_log([a])[0])
        mid = math.exp(y) * a["kvm"] * predict.kvm_price(a["district"])
        w = Z80 * self.loo_sd
        reasons = [f"{n} {predict.pct(v)}" for n, v in self.reasons(a)]
        return {"low": predict.tens(mid * math.exp(-w)), "mid": predict.tens(mid), "high": predict.tens(mid * math.exp(w)),
                "kvm": int(round(mid / a["kvm"], -3)),
                "basis": f"{round(mid / a['kvm'] / 1000)} tkr/m² från lägenheten själv, utan priser",
                "reasons": reasons}


def target(s):
    """The final price per m² against the district's, on a log scale."""
    return math.log(s["final_price"] / s["kvm"] / predict.kvm_price(s["district"]))


def usable(sales):
    return [s for s in sales if s.get("kvm") and s.get("district")]


def leave_one_out(sales, gbm=False):
    """{url: the estimate / final − 1} with each sale left out of the fit in turn."""
    out = {}
    for i, s in enumerate(sales):
        m = Model(sales[:i] + sales[i + 1:], gbm=gbm)
        out[s["url"]] = math.exp(float(m.predict_log([s])[0]) + math.log(predict.kvm_price(s["district"]) * s["kvm"])) / s["final_price"] - 1
    return out


def describe(errs):
    if not errs:
        return "no sales to check"
    ab = sorted(abs(e) for e in errs)
    return (f"{len(errs)} sales, median error {statistics.median(ab) * 100:.1f} %, mean {statistics.mean(ab) * 100:.1f} %, "
            f"90 % within {ab[int(0.9 * (len(ab) - 1))] * 100:.1f} %, bias {statistics.mean(errs) * 100:+.1f} %")


def check(sales, log, trees=False):
    """The leave-one-out comparison with predict.py's two estimates, and whether a blend with the asking price helps."""
    ridge = leave_one_out(sales)
    log(f"Leave one out, this model (ridge):       {describe(list(ridge.values()))}")
    if trees:  # ~35 s
        log(f"  boosted trees instead:                 {describe(list(leave_one_out(sales, gbm=True).values()))}")
    ask, size = {}, {}
    for i, s in enumerate(sales):
        m = predict.Model(sales[:i] + sales[i + 1:])
        e = m.estimate(dict(s, asking=None))
        if e and e["method"] == "kvm":
            size[s["url"]] = e["mid"] / s["final_price"] - 1
        if s["ask_ok"]:
            e = m.estimate(s)
            if e and e["method"] == "asking":
                ask[s["url"]] = e["mid"] / s["final_price"] - 1
    log(f"  predict.py from the size, same sales:  {describe(list(size.values()))}")
    log(f"  predict.py from the asking price:      {describe(list(ask.values()))}")
    log(f"  this model on those same sales:        {describe([ridge[u] for u in ask])}")
    for w in (0.1, 0.2, 0.3):
        blend = [math.exp((1 - w) * math.log(1 + ask[u]) + w * math.log(1 + ridge[u])) - 1 for u in ask]
        log(f"  asking estimate blended {w:.0%} with this: {describe(blend)}")
    if len(ask) > 10:
        a, r = np.log1p(list(ask.values())), np.log1p([ridge[u] for u in ask])
        log(f"  correlation of the two errors: {np.corrcoef(a, r)[0, 1]:.2f}")
    return ridge


def update(data_dir=None, log=print, dry=False, trees=False):
    """Write data/model.json for every apartment for sale. Returns the estimates."""
    data = Path(data_dir or finder.DATA)
    apts, sales = load(data)
    sales = usable(sales)
    if len(sales) < MIN_SALES:
        log(f"Model: only {len(sales)} usable sales, no estimates")
        return {}
    model = Model(sales)
    out = {}
    for a in apts:
        if a["state"] in predict.LIVE:
            e = model.estimate(a)
            if e:
                out[a["url"]] = e
    live = sum(a["state"] in predict.LIVE for a in apts)
    log(f"Model: {len(out)} of {live} apartments for sale estimated from {len(sales)} sales, without prices "
        f"(penalty {model.fit.alpha_:.3g}, ±{Z80 * model.loo_sd * 100:.0f} % for 80 %)")
    if dry:
        known = {n: sum(s["x"][n] is not None for s in sales) for n in model.names}
        log("Known among the sales: " + ", ".join(f"{n} {c}" for n, c in known.items() if c < len(sales)))
        coef = sorted(zip(model.names, model.fit.coef_[:len(model.names)]), key=lambda kv: -abs(kv[1]))
        log("Largest effects (per SD): " + ", ".join(f"{NAMES.get(n, n)} {predict.pct(c)}" for n, c in coef[:12]))
        log(f"Left out, known for under {MIN_KNOWN} sales: {', '.join(n for n in sales[0]['x'] if n not in model.names) or 'none'}")
        check(sales, log, trees)
        for url, p in list(out.items())[:8]:
            log(f"  {p['low']:>11,.0f} {p['mid']:>11,.0f} {p['high']:>11,.0f}  {', '.join(p['reasons'])}  {url}")
    else:
        finder.write_json(data / "model.json", out)
    return out


def use_for_unpriced(data_dir=None, log=print):
    """Where an apartment has no asking price, put this model's estimate in data/predictions.json in place of
    predict.py's from the size alone, since it's the better of the two there (8.8 % against 11.3 % off, median)."""
    data = Path(data_dir or finder.DATA)
    preds, mine = finder.read_json(data / "predictions.json", {}), finder.read_json(data / "model.json", {})
    n = 0
    for url, e in mine.items():
        if (preds.get(url) or {}).get("method") != "asking":
            preds[url] = {"low": e["low"], "mid": e["mid"], "high": e["high"], "basis": e["basis"], "n": None, "method": "model"}
            n += 1
    finder.write_json(data / "predictions.json", preds)
    log(f"Model: used for {n} apartments without an asking price")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry", action="store_true", help="print a summary and the leave-one-out error; write nothing")
    ap.add_argument("--trees", action="store_true", help="with --dry, also check boosted trees in place of the ridge")
    ap.add_argument("--data", help="another data folder")
    args = ap.parse_args()
    update(args.data, dry=args.dry, trees=args.trees)
