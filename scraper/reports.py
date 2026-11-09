"""The förening's annual report (årsredovisning), from the PDF the agent links on the apartment's page.

Since 2023 every bostadsrättsförening's annual report has the same key figures in its multi-year overview
(flerårsöversikt): årsavgift, debt, savings and energy cost per m², and interest-rate sensitivity. Its first page
also gives the förening's organisationsnummer, which some agents don't show on the page. The report is read once
per link and kept in the förening's store (bolagsverket.py); a scanned report has no text and is skipped.
"""
import io
import re
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

import agents

MAX_BYTES = 40_000_000
LINK_RX = re.compile(r"årsredovisning|arsredovisning|årsredoovisning", re.I)

# Each key figure by the words its row starts with, matched without accents (text recognition reads "å" as "a").
# The debt per m² of bostadsrätt area is the one the agents give and the red flags compare; the debt per m² of
# the whole building is kept beside it. Text recognition reads HSB's small "kr/kvm" as "kr/kum"
U = r"(?:k[vu]m|m2|m²)"
BR = r"(?:bostadsratt|br[f-]?[ -]?yta|bostadsyta|upplaten)"
ROWS = [
    ("avgiftKvm", rf"^arsavg\w*\b[^\d\n]*?{U}"),
    ("skuldKvm", rf"^skuld\w*\b(?=[^\d\n]*{U})(?=[^\d\n]*{BR})[^\d\n]*?(?:{U}|{BR})(?:[^\d\n]*?(?:{U}|{BR}))?"),
    ("skuldKvmTotal", rf"^skuld\w*\b[^\d\n]*?{U}"),
    ("sparandeKvm", rf"^sparande\w*\b[^\d\n]*?{U}"),
    ("energiKvm", rf"^ener[gq]ikostnad\w*\b[^\d\n]*?{U}"),
    ("rantekanslighet", r"^rantekanslighet"),
]
YEAR = re.compile(r"\b(20[1-3]\d)(?:/(?:20)?\d\d)?\b")
# One number of a row: 1–3 digits that may go on in groups of three, a number written without spaces, a
# decimal (with a comma, or a full stop as text recognition sometimes reads it), or "-" for none
NUM = re.compile(r"-?(?:0|[1-9]\d{0,2})(?:[,.]\d{1,2})?|-?[1-9]\d{3,}|-?\d+[,.]\d{1,2}|-")
FOLD = str.maketrans("åäöÅÄÖ", "aaoAAO")
ROWS_KEYS = [k for k, _ in ROWS]
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


def figures(text):
    """The key figures for the latest year in a report's text, and the year."""
    # Some reports write "å" as "a" and a ring after it, and a dash or minus as "–" or "−"
    text = unicodedata.normalize("NFC", text)
    lines = [re.sub(r"[\u00a0\u202f\u2009]", " ", l).replace("\u2212", "-").replace("\u2013", "-").strip() for l in text.splitlines()]
    # "*" marks the key figures the law requires ("*Årsavgift per kvm")
    lines = [l.lstrip("*") for l in lines]
    for k, l in enumerate(lines):
        # A header's years as text recognition reads HSB's ("2025 7074 7023 2022"), a broken year written
        # "2024 - 2025", years in short ("24/25 23/24") and years as months ("2501-2512")
        if re.fullmatch(r"(?:[1-3]\d/[1-3]\d\s*){2,}", l):
            l = re.sub(r"\b([1-3]\d)/", r"20\1/", l)
        if YEAR.search(l) and len(re.sub(r"[\d\s/-]", "", l)) < 40:
            l = re.sub(r"\b7(0[1-3]\d)\b", r"2\1", l)
            lines[k] = re.sub(r"\b(20[1-3]\d) ?- ?(20[1-3]\d)\b", lambda m: f"{m[1]}/{m[2]}" if int(m[2]) == int(m[1]) + 1 else m[0], l)
        lines[k] = re.sub(r"\b([1-3]\d)01-\1 ?12\b", r"20\1", lines[k])
    heads = [k for k, l in enumerate(lines) if len(YEAR.findall(l)) >= 2 and len(re.sub(r"[\d\s/-]", "", l)) < 40]
    out = {}
    for i, line in enumerate(lines):
        # A label split over two lines ("Skuldsättning per kvm upplåten" / "med bostadsrätt (kr/kvm) 16 432 …"),
        # or, in a scan, its numbers on the line below
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if not re.search(r"\d", line) and (nxt[:1].islower() or re.fullmatch(r"[-\d\s,.%]+", nxt)):
            line = line + " " + nxt
        low = line.lower().translate(FOLD)
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
            at = head.index(max(head))
            ok = lambda v: v and v[at] is not None and RANGE[key][0] <= v[at] <= RANGE[key][1] and \
                not (key.startswith("skuld") and 0 < v[at] < DEBT_MIN)
            # A debt row's "-" is a förening without loans: no debt that year
            dash = 0.0 if key.startswith("skuld") else None
            vals = _values(toks, len(head), dash, ok)
            if not ok(vals) and at == 0:  # text recognition can miss a year in the header: try one column more
                vals = _values(toks, len(head) + 1, dash, ok)
            if not ok(vals):
                continue
            out[key] = round(vals[at], 2) if key == "rantekanslighet" else round(vals[at])
            out.setdefault("reportYear", int(max(head)))
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


def read(url, robots, log=print, scans=True, tiles=True):
    """Download one annual report and read it: {"orgnr", the key figures, "reportYear"}. A scan (or a report
    whose key figures are a picture) is read by text recognition when `scans` is set; "scanned" says so.
    Where that finds too little, the pages are read again in parts when `tiles` is set ("tiled" says so),
    or else the report waits for another day."""
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
    out = figures(text)
    if sum(k in out for k in ROWS_KEYS) < 3:
        if not scans:
            return {**out, "scanned": True, "ocrWaiting": True}
        # HSB's flerårsöversikt can be a small picture of a table, which only a read in parts finds
        for parts in (False, True):
            if parts and not tiles:
                out["ocrWaiting"] = True
                break
            if not (seen := ocr(data, log, parts)):
                break
            if sum(k in (f := figures(seen)) for k in ROWS_KEYS) > sum(k in out for k in ROWS_KEYS):
                out, text = {**f, "scanned": True}, seen + "\n" + text
            out["tiled"] = parts
            if sum(k in out for k in ROWS_KEYS) >= 3:
                break
        if not out.get("scanned") and len(re.sub(r"\s", "", text)) < 500:
            out["scanned"] = True
    if o := agents.find_orgnr(text[:8000]):
        out["orgnr"] = o
    return out


DONE = Path(__file__).resolve().parent.parent / "data" / "reports.json"
FIGURES = ("avgiftKvm", "skuldKvm", "skuldKvmTotal", "sparandeKvm", "energiKvm", "rantekanslighet", "reportYear")
PER_RUN = 80  # reports downloaded per morning run, a second apart; the rest wait for the next morning
SCANS_PER_RUN = 20  # scans read by text recognition per run, about half a minute each
TILES_PER_RUN = 5  # of those, read again in parts, two to four minutes each
READER = 2  # the reader's version: a report an older one read is read once more, after the new links


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
    # Reports that waited for text recognition on an earlier day are read again
    todo += [u for u, r in done.items() if r.get("ocrWaiting") and u not in todo]
    # Reports an older reader read (version 1 missed HSB's pictured flerårsöversikt and a debt-free förening's "–")
    todo += [u for u, r in done.items() if r.get("reader", 1) < READER and not any(k in r for k in ("error", "blocked", "notPdf"))
             and u not in todo]
    # A report robots.txt kept out, from a host whose owner has since said yes (agents.PERMITTED)
    todo = [u for u, r in done.items() if r.get("blocked") and urllib.parse.urlparse(u).netloc in agents.PERMITTED] + todo
    scans = tiled = 0
    for url in todo[:limit]:
        try:
            r = read(url, robots, log, scans=scans < SCANS_PER_RUN, tiles=tiled < TILES_PER_RUN)
            scans += bool(r.get("scanned") and not r.get("ocrWaiting"))
            tiled += r.pop("tiled", False)
        except Exception as e:  # never retried: a link that fails today is left
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
