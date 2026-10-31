"""Estate agent sites: where each agent lists its apartments for sale, and how to read them.

The finder reads every agent's for-sale list (pressing "show more" where there is one)
and, for agents whose list is incomplete, the entries in their sitemap that changed
recently. It keeps the inner-city candidates, then opens each apartment's own page,
which one shared parser reads, since most agents show the same things the same way.
"""
import datetime as dt
import gzip
import json
import re
import time
import unicodedata
import urllib.request
import urllib.robotparser
from pathlib import Path
from urllib.parse import urlparse

UA = "Bostadsplan/1.0 (personal apartment planner)"
CACHE_HOURS = 20
SITEMAP_DAYS = 30   # sitemap entries changed in the last month; older ones are mostly sold

# obj: what a link to one apartment looks like on that site. clicks: how many times to press
# "show more" at most. anywhere: the cards don't name the area, so OpenStreetMap decides.
AGENTS = [
    # Erik Olsson lists all of Sweden; its search narrows to an area by id, one page per inner-city district
    {"name": "Erik Olsson", "lists": [f"https://www.erikolsson.se/homes?areaIds={i}" for i in (467, 667, 387, 528, 898, 1961, 668)],
     "obj": r"erikolsson\.se/homes/[^/?#]+-\d+$"},
    {"name": "Alicia Edelman", "lists": ["https://aliciaedelman.se/till-salu"], "obj": r"/bostad/"},
    # Notar's national list shows only its 25 newest (it pages inside the page), and each inner-city office
    # page shows its 12 newest; together they cover most of Notar's inner-city apartments, not all
    {"name": "Notar", "lists": ["https://www.notar.se/kopa-bostad?sortBy=date&sortOrder=desc"] +
                               [f"https://www.notar.se/maklare-och-kontor/kontor/{o}" for o in
                                ("gamlastan-hornstull", "sodermalm-katarina", "kungsholmen", "vasastan", "ostermalm", "gardet")],
     "obj": r"/kopa-bostad/objekt/", "anywhere": True},
    {"name": "BOSTHLM", "lists": ["https://www.bosthlm.se/till-salu"], "obj": r"bosthlm\.se/\d+/"},
    {"name": "Historiska Hem", "lists": ["https://historiskahem.se/till-salu/"], "obj": r"/object/tillsalu-"},
    {"name": "Tradition", "lists": ["https://tradition.se/till-salu"], "obj": r"/bostad/"},
    {"name": "Södermäklarna", "lists": ["https://www.sodermaklarna.se/till-salu"], "obj": r"/bostad/"},
    {"name": "Stadshem", "lists": ["https://stadshem.se/till-salu"], "obj": r"/objekt/"},
    {"name": "Bergets Ro", "lists": ["https://www.bergetsro.se/till-salu/"], "obj": r"/houses/", "clicks": 40, "anywhere": True},
    {"name": "Bjurfors", "lists": ["https://www.bjurfors.se/sv/tillsalu/stockholm/stockholm/"], "obj": r"/sv/tillsalu/stockholm/stockholm/[^/]+/[^/]+"},
    {"name": "Behrer & Partners", "lists": ["https://www.behrer.se/vara-hem/"], "obj": r"/objekt/[^/]+/?$"},
]

# Names that place a card in innanför tullarna, and some that place it outside. Checked on
# accent-free lower case, so "Södermalm" and "sodermalm" in a link both count.
INNER = re.compile(
    r"sodermalm|\bsofo\b|\bsofia\b|katarina|\bmaria\b|mariatorget|mariaberget|hogalid|hornstull|zinkensdamm|skanstull|"
    r"slussen|tanto|reimersholme|langholmen|kungsholmen|fridhemsplan|fredhall|marieberg|stadshagen|kristineberg|"
    r"ralambshov|vasastan|vasastaden|birkastan|sibirien|roda bergen|odenplan|hagastaden|ostermalm|larkstaden|gardet|"
    r"djurgarden|norrmalm|\bcity\b|gamla stan|riddarholmen|skeppsholmen|innerstan|innerstaden|karlaplan")
OUTER = re.compile(
    r"essingen|liljeholm|hammarby|hagersten|alvsjo|bromma|solna|sundbyberg|nacka|lidingo\b|danderyd|taby|huddinge|"
    r"farsta|enskede|johanneshov|arsta|midsommarkransen|aspudden|spanga|kista|haninge|goteborg|malmo|uppsala")


def ascii_lower(s):
    s = unicodedata.normalize("NFKD", str(s).lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def looks_inner(url, text, anywhere=False):
    """A card worth opening: it names an inner-city area and nothing clearly outside it.
    A card with no text (from a sitemap), or from an agent whose cards don't name the area,
    is opened, and its address decides."""
    hay = ascii_lower(urlparse(url).path.replace("-", " ").replace("/", " ") + " " + text)
    if OUTER.search(hay):
        return False
    return bool(INNER.search(hay)) or not text or anywhere


# ---------- Being a polite visitor ----------

class Robots:
    """robots.txt for each site, read once per run, from the address the site actually answers on."""

    def __init__(self):
        self.parsers = {}

    def allowed(self, url):
        host = urlparse(url).netloc
        if host not in self.parsers:
            rp = urllib.robotparser.RobotFileParser()
            try:
                req = urllib.request.Request(f"https://{host}/robots.txt", headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=20) as r:
                    rp.parse(r.read().decode("utf-8", "replace").splitlines())
            except Exception:
                rp.parse([])  # no robots.txt: nothing is disallowed
            self.parsers[host] = rp
        return self.parsers[host].can_fetch("*", url)


def fetch_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as r:
        b = r.read()
    return (gzip.decompress(b) if b[:2] == b"\x1f\x8b" else b).decode("utf-8", "replace")


def sitemap_entries(url, cache_dir):
    """[url, lastmod] pairs from one sitemap, cached for most of a day."""
    cache = Path(cache_dir) / ("sitemap-" + re.sub(r"[^a-z0-9]+", "-", url.lower())[:80] + ".json")
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_HOURS * 3600:
        data = json.loads(cache.read_text())
        if data and isinstance(data[0], list):
            return data
    text = fetch_text(url)
    out = [[m.group(1).strip(), (m.group(2) or "").strip()]
           for m in re.finditer(r"<loc>\s*([^<]+?)\s*</loc>(?:\s*<lastmod>\s*([^<]+?)\s*</lastmod>)?", text)]
    cache.parent.mkdir(exist_ok=True)
    cache.write_text(json.dumps(out))
    return out


def _page_cards(page, obj_rx, cards):
    links = page.eval_on_selector_all(
        "a[href]", """els => els.map(a => {
            const card = a.closest('article, li, [class*=card], [class*=object], [class*=item]') || a;
            return [a.href.split('#')[0], (card.innerText || a.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 400)];
        })""")
    for h, t in links:
        if re.search(obj_rx, h) and not re.search(r"/(salda|sold|slutpris)", h, re.I):
            cards[h] = max(cards.get(h, ""), t, key=len)


def list_cards(page, url, obj_rx, clicks=8, max_pages=30):
    """Every apartment link on a for-sale list, with its card text. Scrolls, presses
    "Ladda fler" / "Visa fler" so lists that load in parts show everything, and follows
    numbered page links ("?page=2") for lists split into pages."""
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(2500)
    # Lists that grow as you scroll: keep scrolling until no new apartments appear
    seen, still = -1, 0
    for _ in range(40):
        n = page.eval_on_selector_all("a[href]", f"els => els.filter(a => new RegExp({json.dumps(obj_rx)}).test(a.href)).length")
        still = still + 1 if n == seen else 0
        if still >= 2:
            break
        seen = n
        page.mouse.wheel(0, 8000)
        page.wait_for_timeout(900)
    for _ in range(clicks):
        for _ in range(3):
            page.mouse.wheel(0, 5000)
            page.wait_for_timeout(500)
        more = page.locator("button, a").filter(has_text=re.compile(r"^\s*(ladda|visa) fler(?! sålda)", re.I))
        if not more.count():
            break
        try:
            more.first.click(timeout=3000)
            page.wait_for_timeout(1500)
        except Exception:
            break
    cards = {}
    _page_cards(page, obj_rx, cards)
    base = urlparse(url).path
    pages = {}
    for h in page.eval_on_selector_all("a[href]", "els => els.map(a => a.href)"):
        u = urlparse(h)
        if u.path == base and (m := re.search(r"(?:^|&)page=(\d+)", u.query)) and int(m.group(1)) > 1:
            pages.setdefault(int(m.group(1)), h)
    # Page links often skip the middle ("1 2 3 4 5 … 18"), so every page up to the last is visited
    if pages:
        last = max(pages)
        template = pages[last]
        pages = {n: re.sub(r"((?:^|[?&])page=)\d+", rf"\g<1>{n}", template) for n in range(2, last + 1)}
    for n in sorted(pages)[:max_pages]:
        time.sleep(2)
        try:
            page.goto(pages[n], wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2000)
            _page_cards(page, obj_rx, cards)
        except Exception:
            break
    return cards


def discover(agent, page, robots, cache_dir, log):
    """Candidate apartments for one agent: {url: card text}."""
    found = {}
    for url in agent.get("lists", []):
        if not robots.allowed(url):
            log(f"{agent['name']}: robots.txt asks visitors not to read {url}, skipped")
            continue
        try:
            found.update(list_cards(page, url, agent["obj"], clicks=agent.get("clicks", 8)))
        except Exception as e:
            log(f"{agent['name']}: couldn't read {url} ({e})")
        time.sleep(3)
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=SITEMAP_DAYS)).isoformat()
    for url in agent.get("sitemaps", []):
        try:
            for u, lastmod in sitemap_entries(url, cache_dir):
                if re.search(agent["obj"], u) and lastmod and lastmod >= since and u not in found:
                    found[u] = ""
        except Exception as e:
            log(f"{agent['name']}: couldn't read sitemap {url} ({e})")
    return found


# ---------- Reading cards and listing pages ----------

SV_MONTHS = ["januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti", "september", "oktober", "november", "december"]
MONTH_RX = "|".join(m[:3] for m in SV_MONTHS)
# "Söndag 8 november kl 12:15 - 13:00", "8 nov. 11:00", "8 | NOVEMBER 2026 | SÖNDAG | KL 12.30-13.00", "söndag 8 nov | 14:15 - 15:00"
SHOW_RX = re.compile(
    rf"(?<!\d)(\d{{1,2}})\s*(?:\|\s*)?({MONTH_RX})[a-zåäö]*\.?(?:\s*\d{{4}})?[^0-9]{{0,40}}?"
    rf"(\d{{1,2}})[:.](\d{{2}})(?:\s*[-–]\s*(\d{{1,2}})[:.](\d{{2}}))?", re.I)
PRICE_RX = re.compile(r"(\d{1,3}(?:[ . ]\d{3}){1,3})\s*kr(?!\s*/\s*m)", re.I)
ROOMS_RX = re.compile(r"(\d+(?:[.,]5)?)\s*(?:rum|rok)\b", re.I)
KVM_RX = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:\+\s*\d+(?:[.,]\d+)?\s*)?(?:kvm|m²|m2)\b", re.I)
AVGIFT_RX = re.compile(r"(\d{1,2}[ . ]?\d{3})\s*kr\s*/\s*mån", re.I)
SOON_RX = re.compile(r"snart till salu|kommer snart|snart på marknaden|kommande försäljning|på förhand|förhandsvisning", re.I)
SOLD_RX = re.compile(r"(?<![a-zåäö])(?:såld|sålt)(?![a-zåäö])|slutpris\s*:?\s*\d", re.I)
ADDR_RX = re.compile(r"([A-ZÅÄÖ][A-Za-zÅÄÖåäöéü.'\-]+(?: [A-Za-zÅÄÖåäöéü.'\-]+){0,3} \d{1,3} ?[A-Za-z]?)(?!\d)")
FEATURE_RX = [
    ("stambyte done", r"stamby(?:te|tet|ten)\s+(?:är\s+|har\s+)?(?:gjor|genomför|utför|klar|färdig)|nya stammar|stambytt"),
    ("stambyte coming", r"stambyte\s+(?:är\s+)?(?:planera|beslut|kommer|inplanera|påbörja)"),
    ("renovated kitchen", r"renoverat kök|nytt kök|köket (?:är |har )?(?:ny|helt? |nyligen )?renoverat"),
    ("renovated bathroom", r"renoverat (?:bad|dusch)rum|nytt (?:bad|dusch)rum|badrummet (?:är |har )?(?:ny|helt? |nyligen )?renoverat"),
    ("tiled bathroom", r"helkaklat"),
    ("fireplace", r"kakelugn|öppen spis|eldstad"),
    ("herringbone parquet", r"fiskbensparkett"),
    ("water view", r"(?:sjö|vatten)utsikt|utsikt över (?:vattnet|riddarfjärden|mälaren|saltsjön|årstaviken)"),
    ("quiet courtyard side", r"mot (?:lugn |rofylld |grönskande |)(?:inner)?gård"),
    ("corner flat", r"hörnläge|hörnlägenhet"),
    ("high ceilings", r"(?:hög|generös) takhöjd|takhöjd (?:på )?(?:ca |cirka )?(?:2[,.][89]|3[,.]\d)"),
    ("washing machine", r"tvättmaskin"),
    ("dishwasher", r"diskmaskin"),
    ("lift", r"hiss finns|\bhiss\b"),
    ("balcony", r"balkong"),
    ("patio", r"uteplats"),
    ("storage", r"förråd"),
]
SKIP_PARA = re.compile(r"cookie|samtycke|personuppgift|integritet|nyhetsbrev|gdpr", re.I)


def _price(m):
    """An asking price from a match, or None for amounts too small to be one (an avgift, a fee)."""
    v = int(re.sub(r"\D", "", m.group(1)))
    return v if v >= 300000 else None


def _num(s):
    v = float(re.sub(r"[^\d,.]", "", s).replace(",", "."))
    return int(v) if v.is_integer() else v


def parse_card(text):
    """What a for-sale list shows about an apartment: its price and whether it's coming soon."""
    t = re.sub(r"[  ]", " ", text)
    out = {}
    for m in PRICE_RX.finditer(t):
        if p := _price(m):
            out["price"] = p
            break
    if SOON_RX.search(t):
        out["soon"] = True
    return out


def is_sold(text):
    return bool(SOLD_RX.search(text[:4000]))


def find_address(*texts):
    """The street address in a heading or page title, e.g. "Hornsgatan 153" from "Hornsgatan 153, 4 tr"."""
    for t in texts:
        if t and (m := ADDR_RX.search(re.sub(r"\s+", " ", t))):
            a = m.group(1).strip()
            a = re.sub(r"(\d) ([A-Za-z])$", r"\1\2", a)
            return a[0] + a[1:].lower() if a.isupper() else a
    return None


def _date(day, month_idx, now):
    d = dt.date(now.year, month_idx + 1, day)
    if d < now.date() - dt.timedelta(days=7):
        d = d.replace(year=now.year + 1)
    return d


def parse_agent_page(text, now):
    lines = [re.sub(r"[  \t]+", " ", l).strip() for l in text.split("\n")]
    lines = [l for l in lines if l]
    flat = " | ".join(lines)
    out = {"shows": []}
    for m in SHOW_RX.finditer(flat):
        mi = next(i for i, mo in enumerate(SV_MONTHS) if mo.startswith(m.group(2).lower()[:3]))
        try:
            d = _date(int(m.group(1)), mi, now)
        except ValueError:
            continue
        if not (now.date() - dt.timedelta(days=1) <= d <= now.date() + dt.timedelta(days=60)):
            continue
        start = f"{d:%Y-%m-%d}T{int(m.group(3)):02d}:{m.group(4)}"
        end = f"{int(m.group(5)):02d}:{m.group(6)}" if m.group(5) else None
        same = next((s for s in out["shows"] if s["start"] == start), None)
        if same:
            if end and not same.get("end"):
                same["end"] = end
        else:
            out["shows"].append({"start": start, **({"end": end} if end else {})})
    # A page's own showings have a start and an end; times alone are usually other apartments' cards
    if any("end" in s for s in out["shows"]):
        out["shows"] = [s for s in out["shows"] if "end" in s]
    out["shows"].sort(key=lambda s: s["start"])

    def value(label_rx):
        for i, l in enumerate(lines):
            m = re.match(rf"(?:{label_rx})\s*:?\s*(.*)$", l, re.I)
            if m:
                v = m.group(1).strip() or (lines[i + 1] if i + 1 < len(lines) else "")
                if v:
                    return v
        return None

    v = value(r"byggnadsår|byggår")
    if v and (m := re.search(r"(1[6-9]\d\d|20\d\d)", v)):
        out["built"] = int(m.group(1))
    v = value(r"driftkostnader totalt|driftskostnad(?:er)?|driftkostnad(?:er)?")
    if v and (m := re.search(r"(\d[\d ]*)\s*kr", v)):
        out["drift"] = int(m.group(1).replace(" ", ""))
    v = value(r"våningsplan|våning")
    if v and len(v) < 60:
        out["floorText"] = v
        if re.match(r"\s*(bv|bottenvåning|entréplan|gatuplan)", v, re.I):
            out["floor"] = "BV"
        elif m := re.search(r"(\d+(?:[.,]5)?)", v):
            out["floor"] = m.group(1).replace(",", ".")
    avg = value(r"månadsavgift|avgift")
    if avg and len(avg) < 200 and (m := re.search(r"(\d{1,2}[ .]?\d{3})\s*kr", avg)):
        out["avgift"] = int(re.sub(r"\D", "", m.group(1)))
    v = value(r"utgångspris|utropspris|accepterat pris|pris")
    if v and (m := PRICE_RX.search(v)) and _price(m):
        out["price"] = _price(m)
    v = value(r"antal rum|rum")
    if v and (m := re.match(r"\s*(\d+(?:[.,]5)?)", v)):
        out["rooms"] = _num(m.group(1))
    v = value(r"boarea|boyta|storlek|bostadsyta")
    if v and (m := re.match(r"\s*(\d+(?:[.,]\d+)?)", v)):
        out["kvm"] = _num(m.group(1))
    # Otherwise the first price, rooms, size and avgift the page mentions
    if "price" not in out:
        for m in PRICE_RX.finditer(flat[:3000]):
            if p := _price(m):
                out["price"] = p
                break
    if "rooms" not in out and (m := ROOMS_RX.search(flat[:3000])):
        out["rooms"] = _num(m.group(1))
    if "kvm" not in out and (m := KVM_RX.search(flat[:3000])):
        out["kvm"] = _num(m.group(1))
    if "avgift" not in out and (m := AVGIFT_RX.search(flat)):
        out["avgift"] = int(re.sub(r"\D", "", m.group(1)))
    if SOON_RX.search(flat[:3000]):
        out["soon"] = True

    paras = [l for l in lines if len(l) >= 160 and not SKIP_PARA.search(l)]
    if paras:
        out["desc"] = "\n\n".join(paras[:5])[:3000]
    hay = " ".join([out.get("desc", ""), out.get("floorText", ""), avg or ""]).lower()
    out["extra"] = [name for name, rx in FEATURE_RX if re.search(rx, hay)]
    if "stambyte done" in out["extra"] and "stambyte coming" in out["extra"]:
        out["extra"].remove("stambyte coming")
    return out


# ---------- Bidding ----------

BIDDING_RX = re.compile(r"pågående budgivning|budgivning pågår|budgivningen pågår", re.I)
HIGH_BID_RX = re.compile(r"(?:högsta|aktuellt|senaste|ledande)\s+bud(?:et)?\s*:?\s*(\d{1,3}(?:[ .]\d{3}){1,3})\s*kr", re.I)
FINAL_RX = re.compile(r"(?:slutpris|såld för|sålt för|köpeskilling)\s*:?\s*(\d{1,3}(?:[ .]\d{3}){1,3})\s*kr", re.I)


def parse_bidding(text):
    """Where an apartment is in its sale: for sale, in bidding (with the highest bid and how
    many bids there are) or sold (with the final price, when the agent publishes it)."""
    t = re.sub(r"[\u00a0\u202f]", " ", text)
    out = {"state": "for sale"}
    if m := FINAL_RX.search(t):
        out.update(state="sold", final=int(re.sub(r"\D", "", m.group(1))))
    elif is_sold(t):
        out["state"] = "sold"
    elif BIDDING_RX.search(t) or HIGH_BID_RX.search(t):
        out["state"] = "bidding"
    if m := HIGH_BID_RX.search(t):
        out["bid"] = int(re.sub(r"\D", "", m.group(1)))
    # The bid list: one amount per row under the "Tidpunkt" header
    lines = [l.strip() for l in t.split("\n") if l.strip()]
    if "Tidpunkt" in lines:
        rows = lines[lines.index("Tidpunkt") + 1:][:120]
        out["bids"] = sum(1 for l in rows if re.fullmatch(r"\d{1,3}(?: \d{3}){1,3} kr", l))
    return out


# ---------- Photos ----------

def _photo_key(url):
    # The same photo comes in several sizes: "1200_med5q7.jpg", "med5q7_1920.jpg", "x-1536x1024.jpg"
    stem = urlparse(url).path.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
    return re.sub(r"^\d{3,4}_|_(?:\d{3,4}|thumb)$|-\d+x\d+$", "", stem)


def photos(page, limit=15):
    """The listing's photos: the page's share image first, then the large gallery images."""
    urls = page.eval_on_selector_all('meta[property="og:image"]', "els => els.map(e => e.content.trim())")
    urls += page.eval_on_selector_all(
        "img", "els => els.filter(i => i.naturalWidth >= 600).map(i => i.currentSrc || i.src)")
    out, seen = [], set()
    for u in urls:
        if not u.startswith("http") or re.search(r"\.svg(\?|$)|logo|icon|avatar|maklare|broker|staff", u, re.I):
            continue
        k = _photo_key(u)
        if k not in seen:
            seen.add(k)
            out.append(u)
    return out[:limit]
