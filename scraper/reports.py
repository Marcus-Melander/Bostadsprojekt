"""The förening's annual report (årsredovisning), from the PDF the agent links on the apartment's page.

Since 2023 every bostadsrättsförening's annual report has the same key figures in its multi-year overview
(flerårsöversikt): årsavgift, debt, savings and energy cost per m², and interest-rate sensitivity. Its first page
also gives the förening's organisationsnummer, which some agents don't show on the page. The report is read once
per link and kept in the förening's store (bolagsverket.py); a scanned report has no text and is skipped.
"""
import datetime as dt
import io
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import agents
import web

MAX_BYTES = 40_000_000
LINK_RX = re.compile(r"årsredovisning|arsredovisning|årsredoovisning", re.I)

# Each key figure by the words its row starts with, matched without accents (text recognition reads "å" as "a").
# The debt per m² of bostadsrätt area is the one the agents give and the red flags compare; the debt per m² of
# the whole building is kept beside it. Text recognition reads HSB's small "kr/kvm" as "kr/kum", and a scan's
# "Årsavgift", "Sparande" and "Energikostnad" as "Arsavaitt", "Soarande" and "Enerdikostnad". Some write it
# short: "Skuldsättn/kvm uppl m borätt", "Energi/kvm", or the debt as "Lån/kvm bostadsrättsyta"; an energy use
# in kWh per m² is not its cost. One template has the savings as "Sparande till underhåll och investeringar",
# without its "per kvm". A report whose font maps "Å", "å" and "ä" wrong ("#rsavgift", "R"ntek"nslighet",
# "bostadsr tt") is read with those put back (BROKEN)
U = r"(?:k[vu]m|kvadratmeter|m2|m²)"
BR = r"(?:bostadsr ?a?tt|bo-?ratt|br[f-]?[ -]?yta|bostadsyta|boarea|upplaten)"
DEBT = r"(?:skuld\w*|lan)\b"
ROWS = [
    ("avgiftKvm", rf"^arsav\w*\b[^\d\n]*?{U}"),
    ("skuldKvm", rf"^{DEBT}(?=[^\d\n]*{U})(?=[^\d\n]*{BR})[^\d\n]*?(?:{U}|{BR})(?:[^\d\n]*?(?:{U}|{BR}))?"),
    ("skuldKvmTotal", rf"^{DEBT}[^\d\n]*?{U}"),
    ("sparandeKvm", rf"^s[po]arande\w*\b(?:[^\d\n]*?{U}| till underhall och investeringar\b)"),
    ("energiKvm", rf"^ener[gqda]i(?:kostnad\w*\b|(?=/| per\b))(?![^\d\n]*kwh)[^\d\n]*?{U}"),
    ("rantekanslighet", r"^r ?a?ntek ?a?nslighet"),
]
BROKEN = re.compile(r"[#!]rsredovisning|[#!]rsavgift|R\"ntek\"nslighet")
YEAR = re.compile(r"\b(20[1-3]\d)(?:/(?:20)?\d\d)?\b")
# One number of a row: 1–3 digits that may go on in groups of three, a number written without spaces, a
# decimal (with a comma, or a full stop as text recognition sometimes reads it), or "-" for none
NUM = re.compile(r"-?(?:0|[1-9]\d{0,2})(?:[,.]\d{1,2})?|-?[1-9]\d{3,}|-?\d+[,.]\d{1,2}|-")
# A row's formula, by the numbers of the rows it is worked out from: "(=P4/P3)", "(=(P8/P4)/100)", "(P14/P2)"
FORMULA = re.compile(r"\(=?\(?P\d+[^()]*(?:\)[^()]*)?\)")
FOLD = str.maketrans("åäöÅÄÖ", "aaoAAO")
# What a förening's key figure can be, kr per m² (räntekänslighet in %). Text recognition sometimes drops a
# column, and the row's numbers then split wrong (an årsavgift of 375 338 kr per m²); such a value is left out.
# A förening's debt is none or at least DEBT_MIN kr per m²: below that, "5 125 5 125" was split as four numbers
RANGE = {"avgiftKvm": (50, 3000), "skuldKvm": (0, 100000), "skuldKvmTotal": (0, 100000), "sparandeKvm": (-3000, 5000),
         "energiKvm": (10, 1500), "rantekanslighet": (0, 100)}
DEBT_MIN = 50


def report_link(links):
    """The latest annual report among a page's links [(text, href)]: the one whose text names the latest year."""
    best = None
    for text, href in links:
        # HusmanHagberg names the link only "Ladda ner"; the report's name is in its address
        hay = f"{text} {urllib.parse.unquote(href or '')}"
        if not href or not LINK_RX.search(hay):
            continue
        ys = [int(y) for y in YEAR.findall(hay)] or [0]
        if not best or max(ys) > best[0]:
            best = (max(ys), href)
    return best and best[1]


def _values(toks, n, dash=None, ok=lambda vals: True):
    """The n numbers at the start of one row of the overview, written with spaces between thousands
    ("1 501 845 1 489 039"): a number of 1–3 digits may go on in groups of exactly 3. A row may leave its
    oldest years empty ("711 695 685" under four years), so up to two fewer are taken, padded with None.
    A year written "-" is `dash`. Fewer numbers are tried when `ok` turns a split down ("3 377 3 075" is
    3 377 and 3 075, not 3, 377 and 3 075). None when the tokens can't be split that way."""
    group = re.compile(r"\d{3}(?:[,.]\d{1,2})?")
    for end in range(len(toks), 0, -1):  # the row ends where its numbers end
        if all(NUM.fullmatch(t) or group.fullmatch(t) for t in toks[:end]):
            break
    else:
        return None
    toks = toks[:end]

    def split(i, acc, k):
        if i == len(toks):
            return acc if len(acc) == k else None
        if len(acc) == k or not NUM.fullmatch(toks[i]):
            return None
        num, j, cands = toks[i], i + 1, [(i + 1, toks[i])]
        while re.fullmatch(r"-?[1-9]\d{0,2}(?:\d{3})*", num) and j < len(toks) and group.fullmatch(toks[j]):
            num += toks[j]
            j += 1
            cands.append((j, num))
        for j2, v in reversed(cands):  # the longest number first
            if r := split(j2, acc + [v], k):
                return r
        return None

    for k in range(n, max(n - 2, 1) - 1, -1):
        if vals := split(0, [], k):
            vals = [dash if v == "-" else float(v.replace(",", ".")) for v in vals] + [None] * (n - k)
            if ok(vals):
                return vals
    return None


# One cell of a table that gives each cell a line of its own
CELL = re.compile(r"-|-?\d{1,3}(?: \d{3})*(?:[,.]\d+)?%?|-?\d+(?:[,.]\d+)?%?")


def _cells(lines):
    """A table written one cell a line ("Sparande / kvm", "63", "64", "224", "227", and its years the same way) put
    back one row a line, each run of cells onto the label above it. A "- " cell before a number is its minus sign."""
    out, building, minus = [], False, False
    for raw in lines:
        s = raw.strip()
        if out and out[-1].strip() and CELL.fullmatch(s) and (building or not re.search(r"\d", out[-1])):
            if s == "-" and raw.endswith(" "):
                out[-1] += " -"
                minus = True
            else:
                out[-1] += ("" if minus else " ") + s
                minus = False
            building = True
        else:
            out.append(raw)
            building = minus = False
    return out


def figures(text):
    """The key figures for the latest year in a report's text, and the year."""
    # Some reports write "å" as "a" and a ring after it, and a dash or minus as "–" or "−"
    text = unicodedata.normalize("NFC", text)
    lines = _cells([re.sub(r"[\u00a0\u202f\u2009]", " ", l).replace("\u2212", "-").replace("\u2013", "-") for l in text.splitlines()])
    lines = [l.strip() for l in lines]
    # "*" marks the key figures the law requires ("*Årsavgift per kvm"). Some number the rows and say how each is
    # worked out, with "kr" after every year: "6 Årsavg/kvm uppl m bostadsrätt kr (=P4/P3) 845 kr 695 kr"
    lines = [l.lstrip("*") for l in lines]
    lines = [re.sub(r"(?<=\d) kr\b", "", re.sub(r"^\d{1,2} +(?=[A-ZÅÄÖ])", "", re.sub(FORMULA, "", l))).strip() for l in lines]
    for k, l in enumerate(lines):
        # A header's years as text recognition reads HSB's ("2025 7074 7023 2022"), a broken year written
        # "2024 - 2025", years in short ("24/25 23/24") and years as months ("2501-2512")
        if re.fullmatch(r"(?:[1-3]\d/[1-3]\d\s*){2,}", l):
            l = re.sub(r"\b([1-3]\d)/", r"20\1/", l)
        if YEAR.search(l) and len(re.sub(r"[\d\s/-]", "", l)) < 40:
            l = re.sub(r"\b7(0[1-3]\d)\b", r"2\1", l)
            lines[k] = re.sub(r"\b(20[1-3]\d) ?- ?(20[1-3]\d)\b", lambda m: f"{m[1]}/{m[2]}" if int(m[2]) == int(m[1]) + 1 else m[0], l)
        lines[k] = re.sub(r"\b([1-3]\d)01-\1 ?12\b", r"20\1", lines[k])
    broken = BROKEN.search(text)
    heads = [k for k, l in enumerate(lines) if len(YEAR.findall(l)) >= 2 and len(re.sub(r"[\d\s/-]", "", l)) < 40]
    out = {}
    for i, line in enumerate(lines):
        # A label split over two lines ("Skuldsättning per kvm upplåten" / "med bostadsrätt (kr/kvm) 16 432 …"),
        # or, in a scan, its numbers on the line below
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if not re.search(r"\d", line) and (nxt[:1].islower() or re.fullmatch(r"[-\d\s,.%]+", nxt)):
            line = line + " " + nxt
        low = line.lower().translate(FOLD)
        if broken:
            low = re.sub(r'["#!]', "a", low)
        for key, rx in ROWS:
            if key in out or not (m := re.match(rx, low)):
                continue
            if key == "skuldKvmTotal" and re.match(ROWS[1][1], low):
                continue  # that's the bostadsrätt row
            rest = line[m.end():]
            toks = [t.rstrip("%*") for t in rest.split()]  # "275*" points to a footnote
            while toks and not NUM.fullmatch(toks[0]):
                # A first number text recognition misread ("§ SS7 6 679 6 100") would move every year one column
                if "§" in toks[0] or re.search(r"\d", re.sub(r"m[2²]|\d\)", "", toks[0])):
                    toks = []
                    break
                toks.pop(0)
            k = next((k for k in reversed(heads) if i - 60 < k < i), None)
            if k is None:
                continue
            head = YEAR.findall(lines[k])
            # A header text recognition split over two lines ("2025 2024" / "2023 2022")
            if k - 1 in heads and not set(YEAR.findall(lines[k - 1])) & set(head):
                head = YEAR.findall(lines[k - 1]) + head
            # A year after this one is text recognition misreading another ("2025 2024 2023 2027"); the header
            # still counts when its other years follow each other, not when it's garbled ("2029/2022 2020/2024 2010/2020")
            rest = [int(y) for y in head if int(y) <= dt.date.today().year]
            if len(rest) < len(head) and not (len(rest) >= 2 and all(abs(a - b) == 1 for a, b in zip(rest, rest[1:]))):
                continue
            latest = str(max(rest))
            at = head.index(latest)
            ok = lambda v: v and v[at] is not None and RANGE[key][0] <= v[at] <= RANGE[key][1] and \
                not (key.startswith("skuld") and 0 < v[at] < DEBT_MIN)
            # A debt row's "-" is a förening without loans: no debt that year
            dash = 0.0 if key.startswith("skuld") else None
            vals = _values(toks, len(head), dash, ok)
            if not ok(vals) and at == 0:  # text recognition can miss a year in the header: try one column more
                vals = _values(toks, len(head) + 1, dash, ok)
            if not ok(vals) and at == 0 and len(toks) <= 3:  # the first year a report gives the new figures, there's one
                vals = _values(toks, 1, dash, ok)
            if not ok(vals):
                continue
            out[key] = round(vals[at], 2) if key == "rantekanslighet" else round(vals[at])
            out.setdefault("reportYear", int(latest))
            break
    # The bostadsrätt area is part of the total, so its debt per m² is never the smaller; a misread one is left out
    if out.get("skuldKvm") is not None and out.get("skuldKvmTotal") and out["skuldKvm"] < 0.9 * out["skuldKvmTotal"]:
        del out["skuldKvm"]
    # No debt means no interest-rate sensitivity and no debt on the other area; a 0 beside either is a
    # figure the report left out, not a debt-free förening
    if any(out.get(k) == 0 for k in ("skuldKvm", "skuldKvmTotal")) and (out.get("rantekanslighet") or out.get("skuldKvm") or out.get("skuldKvmTotal")):
        for k in ("skuldKvm", "skuldKvmTotal"):
            if out.get(k) == 0:
                del out[k]
    return out


ROOT = Path(__file__).resolve().parent.parent
OCR_SRC, OCR_BIN = ROOT / "scripts" / "ocr.swift", ROOT / "data" / "bin" / "ocr"


def ocr(data, log=print, tiles=False):
    """The text of a scanned PDF, by macOS's own text recognition (scripts/ocr.swift, built on first use).
    With `tiles`, each page is also read in parts, for small text the whole page misses (three times as
    slow). None where it can't run."""
    import subprocess
    import tempfile
    try:
        if not OCR_BIN.exists() or OCR_BIN.stat().st_mtime < OCR_SRC.stat().st_mtime:
            OCR_BIN.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["swiftc", "-O", str(OCR_SRC), "-o", str(OCR_BIN)], check=True, capture_output=True, timeout=600)
        with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
            f.write(data)
            f.flush()
            return subprocess.run([str(OCR_BIN), f.name, "25"] + ["tiles"] * tiles, check=True, capture_output=True, text=True,
                                  timeout=900 if tiles else 300).stdout
    except (OSError, subprocess.SubprocessError) as e:
        log(f"Text recognition couldn't run ({e})")
        return None


def count(f):
    """How many of the five key figures a read has (the debt on either area)."""
    return sum(k in f for k in ("avgiftKvm", "sparandeKvm", "energiKvm", "rantekanslighet")) + any(k in f for k in ("skuldKvm", "skuldKvmTotal"))


def merge(a, b):
    """Two reads of one report together (its text, then text recognition): a's figure where both have one. Reads
    of different years aren't mixed; the one with more figures is kept."""
    if a.get("reportYear") and b.get("reportYear") and a["reportYear"] != b["reportYear"]:
        return a if count(a) >= count(b) else b
    return {**b, **a}


def derive(f):
    """Räntekänslighet is the interest-bearing debt over a year's årsavgifter, so a report that leaves it out (or
    whose row wasn't read) gets it from the debt and the årsavgift per m² of bostadsrätt; "derived" says so. In
    135 of 138 reports with all three, that is within 15 % of the one the report gives."""
    if f.get("rantekanslighet") is None and f.get("skuldKvm") is not None and f.get("avgiftKvm"):
        f.update(rantekanslighet=round(f["skuldKvm"] / f["avgiftKvm"], 1), derived="rantekanslighet")
    return f


def read(url, robots, log=print, scans=True, tiles=True):
    """Download one annual report and read it: {"orgnr", the key figures, "reportYear"}. A scan, or a report
    whose text gives only some of the key figures (a picture of the table, labels apart from their numbers), is
    also read by text recognition when `scans` is set ("ocred" says so; "scanned" where that found figures the
    text didn't). Where that still finds too little, the pages are read again in parts when `tiles` is set
    ("tiled" says so). A report that waits for either is "ocrWaiting", for another day."""
    if not robots.allowed(url):
        return {"blocked": True}
    data = web.get(url, timeout=60)
    if len(data) > MAX_BYTES or not data.startswith(b"%PDF"):
        return {"notPdf": True}
    import pypdf
    pages = pypdf.PdfReader(io.BytesIO(data)).pages
    text = "\n".join((p.extract_text() or "") for p in pages[:30])
    out = figures(text)
    # Before 2023 the law didn't ask for the key figures, so an older report's missing ones aren't looked for
    if count(out) < 3 or (count(out) < 5 and out.get("reportYear", 0) >= 2023):
        if not scans:
            return {**out, "ocrWaiting": True, **({"scanned": True} if count(out) < 3 else {})}
        # HSB's flerårsöversikt can be a small picture of a table, which only a read in parts finds
        for parts in (False, True):
            if parts and count(out) >= 3:
                break
            if parts and not tiles:
                out["ocrWaiting"] = True
                break
            if not (seen := ocr(data, log, parts)):
                break
            out["ocred"] = True
            if count(both := merge(out, figures(seen))) > count(out):
                out, text = {**both, "scanned": True}, seen + "\n" + text
            out["tiled"] = parts
        if not out.get("scanned") and len(re.sub(r"\s", "", text)) < 500:
            out["scanned"] = True
    if o := agents.find_orgnr(text[:8000]):
        out["orgnr"] = o
    return derive(out)


DONE = Path(__file__).resolve().parent.parent / "data" / "reports.json"
FIGURES = ("avgiftKvm", "skuldKvm", "skuldKvmTotal", "sparandeKvm", "energiKvm", "rantekanslighet", "reportYear", "derived")
PER_RUN = 200  # reports downloaded per morning run, a second apart; the rest wait for the next morning
SCANS_PER_RUN = 30  # reports read by text recognition per run, about half a minute each
TILES_PER_RUN = 5  # of those, read again in parts, two to four minutes each
READER = 3  # the reader's version: a report an older one read without all five figures is read once more, after the new links


# A failed name lookup or no network: the Mac is offline, not the site refusing
OFFLINE_RX = re.compile(r"Errno (?:8|50|51|65)\b|nodename nor servname|Could not resolve host|Network is (?:down|unreachable)|No route to host", re.I)


def offline(e):
    import socket
    if isinstance(e, (urllib.error.HTTPError, web.HTTPError)):  # the site answered, so a 403 or 429 is never read again
        return False
    reason = getattr(e, "reason", e)
    return isinstance(reason, socket.gaierror) or bool(OFFLINE_RX.search(str(reason)))


def update(items, log=print, limit=PER_RUN, first=()):
    """Read the annual reports the agents link, each link once (kept in data/reports.json). Each apartment
    gets its report's figures as "report", and the organisationsnummer from the report's first page when
    its own page didn't give one. The links of the apartments in `first` (those in the filter) are read first."""
    import json
    import time
    done = json.loads(DONE.read_text()) if DONE.exists() else {}
    robots = agents.Robots()
    # Föreningar that already have figures from another apartment's report, which they share by number
    have = {r["orgnr"] for r in done.values() if r.get("orgnr") and count(r) >= 3}
    have |= {p["orgnr"] for p in items.values() if p.get("orgnr") and p.get("report")}
    by_url = {}
    for u, p in items.items():
        if p.get("reportUrl") and not p.get("outside") and p.get("state") != "sold":
            by_url.setdefault(p["reportUrl"], []).append((u, p))
    todo = list(set(by_url) - set(done))
    # Reports that waited for text recognition on an earlier day are read again
    todo += [u for u, r in done.items() if r.get("ocrWaiting") and u not in todo]
    # Reports an older reader read (version 1 missed HSB's pictured flerårsöversikt and a debt-free förening's "–",
    # version 2 numbered rows, "Lån/kvm", a font that maps "å" wrong, and a text's missing figures that a picture has)
    todo += [u for u, r in done.items() if r.get("reader", 1) < READER and count(r) < 5
             and not any(k in r for k in ("error", "blocked", "notPdf")) and u not in todo]
    # A report robots.txt kept out, from a host whose owner has since said yes (agents.PERMITTED)
    todo = [u for u, r in done.items() if r.get("blocked") and urllib.parse.urlparse(u).netloc in agents.PERMITTED] + todo
    # Reports that failed only because the Mac was offline (just woken from sleep) are read again
    todo += [u for u, r in done.items() if OFFLINE_RX.search(r.get("error", "")) and u not in todo]
    # The filter's apartments first, also those read again (a scan that waited for text recognition would otherwise
    # wait for the whole backlog), then those whose förening has no figures yet, those without a number first
    n, at = 0, {u: i for i, u in enumerate(todo)}
    todo.sort(key=lambda u: (not any(a in first for a, _ in by_url.get(u, ())), all(p.get("orgnr") in have for _, p in by_url.get(u, ())),
                             any(p.get("orgnr") for _, p in by_url.get(u, ())), u in done, at[u]))
    scans = tiled = 0
    for url in todo[:limit]:
        try:
            r = read(url, robots, log, scans=scans < SCANS_PER_RUN, tiles=tiled < TILES_PER_RUN)
            scans += r.pop("ocred", False)
            tiled += r.pop("tiled", False)
        except Exception as e:  # never retried: a link that fails today is left
            if offline(e):  # not the link's fault: the rest wait for the next run
                log(f"Annual reports: offline ({e}); stopping for today")
                break
            r = {"error": str(e)[:120]}
        done[url] = {**r, "read": dt.date.today().isoformat(), "reader": READER}
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


DOCS_RX = re.compile(r"dokument", re.I)
DEBUG = False
# The documents reader's version (an apartment's "reportChecked"): a page an older one found nothing on is looked
# at once more (version 1 was stopped by a cookie banner or an open dialog over HusmanHagberg's buttons)
BUTTONS = 2
# The clickable for a label naming the annual report: the label's own link or button, or else the one on the
# same line beside it (Erik Olsson's "Öppna"); never one on another line, which would be another document's
_NEAREST = """(el) => { const own = el.closest('a, button, [role=button]'); if (own) return own;
  const r = el.getBoundingClientRect(), y = (r.top + r.bottom) / 2; let best = null, gap = 1e9;
  for (const b of document.querySelectorAll('a, button, [role=button]')) {
    const q = b.getBoundingClientRect(); if (!q.height || b.contains(el)) continue;
    const dy = Math.abs((q.top + q.bottom) / 2 - y), dx = q.left - r.right;
    if (dy < Math.max(12, r.height / 2) && dx > -5 && dx < gap) { best = b; gap = dx; } }
  return best; }"""


def links(page):
    return page.eval_on_selector_all("a[href]", "as => as.map(a => [a.innerText || a.title || '', a.href])")


def report_behind_buttons(page, log=print):
    """For a page that hides its documents: open its documents section (a button or tab named "Dokument",
    also by its aria-label) and take the annual report's link, or press the report itself and keep the
    address of the PDF the page loads (Länsförsäkringar fetches it from its API, Erik Olsson behind "Öppna")."""
    # A cookie banner left up, or a dialog the förening's facts were read in (HusmanHagberg's "Fakta om bostaden"),
    # covers the buttons
    agents.decline_cookies(page)
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)
    # A section can hold another ("Fakta & dokument", then "Viktiga dokument & länkar"), so look again after each
    opened = set()
    for _ in range(3):
        for el in page.locator("button:visible, a:visible, [role=button]:visible, [role=tab]:visible, summary:visible").all()[:300]:
            try:
                name = ((el.inner_text(timeout=500) or "") + " " + (el.get_attribute("aria-label", timeout=500) or "")).strip()
                href = el.get_attribute("href", timeout=500) or ""
            except Exception:
                continue
            if name in opened or len(name) > 70 or not DOCS_RX.search(name) or (href and not href.startswith("#") and "javascript" not in href):
                continue
            try:
                el.click(timeout=2000)
                page.wait_for_timeout(1200)
            except Exception:
                continue
            opened.add(name)
            if DEBUG:
                log(f"opened {name!r}")
            break
        else:
            break
    if u := report_link(links(page)):
        return u
    pdfs, ctx = [], page.context
    grab = lambda r: pdfs.append(r.url) if "pdf" in (r.headers.get("content-type") or "") and r.url.startswith("http") else None
    ctx.on("response", grab)
    try:
        for label in page.get_by_text(re.compile(r"^\s*årsredovisning[^\n]{0,40}$", re.I)).all()[:3]:
            try:
                target = label.evaluate_handle(_NEAREST, timeout=1500).as_element()
                if not target:
                    continue
                target.click(timeout=2000)
                page.wait_for_timeout(2500)
            except Exception:
                continue
            if pdfs:
                break
    finally:
        ctx.remove_listener("response", grab)
        for p in ctx.pages:
            if p != page:
                p.close()
    return pdfs[0] if pdfs else None
