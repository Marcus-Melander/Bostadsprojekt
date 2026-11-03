"""The förening's annual report (årsredovisning), from the PDF the agent links on the apartment's page.

Since 2023 every bostadsrättsförening's annual report has the same key figures in its multi-year overview
(flerårsöversikt): årsavgift, debt, savings and energy cost per m², and interest-rate sensitivity. Its first page
also gives the förening's organisationsnummer, which some agents don't show on the page. The report is read once
per link and kept in the förening's store (bolagsverket.py); a scanned report has no text and is skipped.
"""
import io
import re
import urllib.request
from pathlib import Path

import agents

MAX_BYTES = 40_000_000
LINK_RX = re.compile(r"årsredovisning|arsredovisning|årsredoovisning", re.I)

# Each key figure by the words its row starts with. The debt per m² of bostadsrätt area is the one the agents give
# and the red flags compare; the debt per m² of the whole building is kept beside it
U = r"(?:kvm|m2|m²)"
BR = r"(?:bostadsrätt|br[f-]?[ -]?yta|bostadsyta)"
ROWS = [
    ("avgiftKvm", rf"^årsavgift\w*\b[^\d\n]*?{U}"),
    ("skuldKvm", rf"^skuld\w*\b(?=[^\d\n]*{U})(?=[^\d\n]*{BR})[^\d\n]*?(?:{U}|{BR})(?:[^\d\n]*?(?:{U}|{BR}))?"),
    ("skuldKvmTotal", rf"^skuld\w*\b[^\d\n]*?{U}"),
    ("sparandeKvm", rf"^sparande\w*\b[^\d\n]*?{U}"),
    ("energiKvm", rf"^energikostnad\w*\b[^\d\n]*?{U}"),
    ("rantekanslighet", r"^räntekänslighet"),
]
YEAR = re.compile(r"\b(20[1-3]\d)(?:/(?:20)?\d\d)?\b")
NUM = re.compile(r"-?(?:0|[1-9]\d{0,2})(?:,\d+)?|-?\d+,\d+|-")


def report_link(links):
    """The latest annual report among a page's links [(text, href)]: the one whose text names the latest year."""
    best = None
    for text, href in links:
        if not href or not LINK_RX.search(f"{text} {href}"):
            continue
        ys = [int(y) for y in YEAR.findall(text or "")] or [0]
        if not best or max(ys) > best[0]:
            best = (max(ys), href)
    return best and best[1]


def _values(toks, n):
    """The n numbers at the start of one row of the overview, written with spaces between thousands
    ("1 501 845 1 489 039"): every number is 1–3 digits followed by groups of exactly 3. None when the tokens
    can't be split into n numbers."""
    # The row must end where its n numbers end, or the split is a guess
    for end in range(len(toks), 0, -1):
        if all(NUM.fullmatch(t) or re.fullmatch(r"\d{3}(?:,\d+)?", t) for t in toks[:end]):
            break
    else:
        return None
    toks = toks[:end]

    def exact(i, acc):
        if i == len(toks):
            return acc if len(acc) == n else None
        if len(acc) == n or not NUM.fullmatch(toks[i]):
            return None
        num, j, cands = toks[i], i + 1, [(i + 1, toks[i])]
        while "," not in num and num != "-" and j < len(toks) and re.fullmatch(r"\d{3}(?:,\d+)?", toks[j]):
            num += toks[j]
            j += 1
            cands.append((j, num))
        for j2, v in reversed(cands):
            if r := exact(j2, acc + [v]):
                return r
        return None

    vals = exact(0, [])
    return vals and [None if v == "-" else float(v.replace(",", ".")) for v in vals]


def figures(text):
    """The key figures for the latest year in a report's text, and the year."""
    lines = [re.sub(r"[\u00a0\u202f\u2009]", " ", l).replace("\u2212", "-").strip() for l in text.splitlines()]
    heads = [k for k, l in enumerate(lines) if len(YEAR.findall(l)) >= 2 and len(re.sub(r"[\d\s/-]", "", l)) < 40]
    out = {}
    for i, line in enumerate(lines):
        # A label split over two lines ("Skuldsättning per kvm upplåten" / "med bostadsrätt (kr/kvm) 16 432 …")
        if not re.search(r"\d", line) and i + 1 < len(lines) and lines[i + 1][:1].islower():
            line = line + " " + lines[i + 1]
        low = line.lower()
        for key, rx in ROWS:
            if key in out or not (m := re.match(rx, low)):
                continue
            if key == "skuldKvmTotal" and re.match(ROWS[1][1], low):
                continue  # that's the bostadsrätt row
            rest = line[m.end():]
            toks = [t.rstrip("%") for t in rest.split()]
            while toks and not NUM.fullmatch(toks[0]):
                toks.pop(0)
            head = next((YEAR.findall(lines[k]) for k in reversed(heads) if i - 60 < k < i), None)
            vals = _values(toks, len(head)) if head else None
            if not vals:
                continue
            at = head.index(max(head))
            if vals[at] is None:
                continue
            out[key] = round(vals[at], 2) if key == "rantekanslighet" else round(vals[at])
            out.setdefault("reportYear", int(max(head)))
            break
    return out


def read(url, robots, log=print):
    """Download one annual report and read it: {"orgnr", the key figures, "reportYear"} or {"scanned": True}."""
    if not robots.allowed(url):
        return {"blocked": True}
    req = urllib.request.Request(url, headers={"User-Agent": agents.UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES or not data.startswith(b"%PDF"):
        return {"notPdf": True}
    import pypdf
    pages = pypdf.PdfReader(io.BytesIO(data)).pages
    text = "\n".join((p.extract_text() or "") for p in pages[:30])
    if len(re.sub(r"\s", "", text)) < 500:
        return {"scanned": True}
    out = figures(text)
    if o := agents.find_orgnr(text[:6000]):
        out["orgnr"] = o
    return out


DONE = Path(__file__).resolve().parent.parent / "data" / "reports.json"
FIGURES = ("avgiftKvm", "skuldKvm", "skuldKvmTotal", "sparandeKvm", "energiKvm", "rantekanslighet", "reportYear")
PER_RUN = 80  # reports downloaded per morning run, a second apart; the rest wait for the next morning


def update(items, log=print, limit=PER_RUN):
    """Read the annual reports the agents link, each link once (kept in data/reports.json). Each apartment
    gets its report's figures as "report", and the organisationsnummer from the report's first page when
    its own page didn't give one."""
    import datetime as dt
    import json
    import time
    done = json.loads(DONE.read_text()) if DONE.exists() else {}
    robots = agents.Robots()
    # Apartments without a number, or whose förening has no figures yet, first
    todo = sorted({p["reportUrl"] for p in items.values() if p.get("reportUrl") and not p.get("outside")} - set(done),
                  key=lambda u: any(p.get("orgnr") for p in items.values() if p.get("reportUrl") == u))
    n = 0
    for url in todo[:limit]:
        try:
            r = read(url, robots, log)
        except Exception as e:  # never retried: a link that fails today is left
            r = {"error": str(e)[:120]}
        done[url] = {**r, "read": dt.date.today().isoformat()}
        n += 1
        time.sleep(1)
    DONE.write_text(json.dumps(done, ensure_ascii=False, indent=1))
    got = 0
    for p in items.values():
        r = done.get(p.get("reportUrl")) or {}
        if r.get("orgnr") and not p.get("orgnr"):
            p.update(orgnr=r["orgnr"], orgnrFrom="report")
        if f := {k: r[k] for k in FIGURES if k in r}:
            p["report"] = {**f, "url": p["reportUrl"]}
            got += 1
    left = len(todo) - n
    log(f"Annual reports: {n} read today{f', {left} left for the next runs' if left > 0 else ''}; "
        f"{got} apartments have their förening's figures from one")
    return n
