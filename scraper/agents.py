"""Estate agent sites: where each agent lists its apartments for sale, and how to read them.

The finder reads every agent's for-sale list (pressing "show more" where there is one)
and, for agents whose list is incomplete, the entries in their sitemap that changed
recently. It keeps the inner-city candidates, then opens each apartment's own page,
which one shared parser reads, since most agents show the same things the same way.
"""
import datetime as dt
import gzip
import html
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
    # Stockholm city's apartments, about 34 a page over ?p=2, ?p=3…
    {"name": "Svensk Fastighetsförmedling", "lists": ["https://www.svenskfast.se/lagenhet/stockholm/stockholm/stockholm/"],
     "obj": r"svenskfast\.se/bostadsratt/stockholm/stockholm/stockholm/.+/\d+/?$"},
    # Its list shows only the first 36 of Stockholm's apartments, mostly suburbs; the sitemap lists every
    # apartment for sale (and recently sold), without dates. Discreet sales have no address and are left out.
    {"name": "Länsförsäkringar", "sitemaps": ["https://www.lansfast.se/sitemap/"], "undated": True,
     "obj": r"lansfast\.se/till-salu/bostadsratt/stockholms/stockholm/stockholm/(?!diskret/)[^/]+/[a-z0-9]{20,}/?$"},
    # Its terms allow a private person to save what they're looking at, not to copy the site in bulk,
    # so only apartments in the city are read. Facts are folded into sections that open on click.
    {"name": "SkandiaMäklarna", "lists": ["https://www.skandiamaklarna.se/hitta-hem/bostadsratt/stockholm/"],
     "sitemaps": ["https://www.skandiamaklarna.se/sitemap-estates.xml"],
     "obj": r"skandiamaklarna\.se/hitta-hem/bostadsratt/stockholm/[^/]+/\d+/?$", "open": ["Om bostaden", "Ekonomi"]},
    {"name": "ESNY", "lists": ["https://www.esny.se/vara-hem/?object_type=all&object_city=stockholm&object_order=published&count=300&object_search="],
     "obj": r"esny\.se/objekt/"},
    {"name": "Karlsson & Uddare", "lists": ["https://karlssonuddare.se/till-salu/"], "obj": r"karlssonuddare\.se/objekt/"},
    # Fastighetsbyrån's robots.txt and terms forbid automatic reading without their permission. They gave it by
    # e-mail on 2026-10-31 for this as a hobby project; ask again before it grows beyond that (see PLAN.md).
    # One page per inner-city area they name, plus the newest of the city's apartments for areas without a page.
    {"name": "Fastighetsbyrån", "permission": "E-mail to Marcus, 2026-10-31: allowed while this is a hobby project",
     "lists": [f"https://www.fastighetsbyran.com/sv/sverige/till-salu/stockholms-lan/stockholms-stad/{a}" for a in
               ("kungsholmen", "vasastan", "ostermalm", "hogalid", "katarina", "sofia", "maria", "fredhall", "kristineberg",
                "marieberg", "lindhagen", "gamla-stan", "reimersholme")] +
              ["https://www.fastighetsbyran.com/sv/sverige/till-salu/stockholms-lan/stockholms-stad"],
     "obj": r"fastighetsbyran\.com/sv/sverige/till-salu/stockholms-lan/stockholms-stad/objekt/\?objektID=\d+$"},
    # Mäklarhuset, HusmanHagberg and Mäklarringen gave written permission (per Marcus, 2026-11-01), so their
    # robots.txt isn't read. Mäklarhuset's district page lists that district first and then the rest of the
    # city, so its first two pages hold the district's apartments.
    {"name": "Mäklarhuset", "permission": "Written permission, per Marcus 2026-11-01",
     "lists": [f"https://www.maklarhuset.se/bostad/sverige/stockholm/stockholm/{a}{p}" for a in
               ("kungsholmen", "vasastan", "sodermalm", "ostermalm") for p in ("", "?page=1")],
     "obj": r"maklarhuset\.se/bostad/sverige/stockholm/stockholm/[^/]+/\d+$"},
    # HusmanHagberg's list pages only by clicking, so its 18 newest come from the list and the rest from its
    # sitemap, whose addresses end in the area's name
    {"name": "HusmanHagberg", "permission": "Written permission, per Marcus 2026-11-01",
     "lists": ["https://www.husmanhagberg.se/till-salu/lagenhet/stockholm-stockholm/"],
     "sitemaps": ["https://www.husmanhagberg.se/sitemap/objekt/sitemap.xml"],
     "obj": r"husmanhagberg\.se/objekt/[^/]*-(?:vasastan|norrmalm|ostermalm|sodermalm|kungsholmen|gamla-stan|hagastaden|"
            r"birkastan|odenplan|fridhemsplan|sofo|sofia|katarina|hogalid|mariatorget|zinkensdamm|skanstull|hornstull|"
            r"marieberg|fredhall|kristineberg|stadshagen|lilla-essingen|gardet|hjorthagen|reimersholme|lindhagen|"
            r"rodabergen|atlas|larkstaden)(?:-[^/]*)?/OBJ[^/]+/?$"},
    {"name": "Mäklarringen", "permission": "Written permission, per Marcus 2026-11-01",
     "lists": ["https://www.maklarringen.se/till-salu?q=%5B%22Stockholm%22%5D"],
     "obj": r"maklarringen\.se/maklare/[^/]+/bostadsratt/stockholm/stockholm/"},
]
# Sites whose owner allowed the finder in writing, past what their robots.txt says
PERMITTED = {urlparse(u).netloc for a in AGENTS if a.get("permission") for u in a.get("lists", []) + a.get("sitemaps", [])}

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
        if host in PERMITTED:  # the site's owner said yes in writing, see the agent's "permission"
            return True
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
    out = [[html.unescape(m.group(1).strip()), (m.group(2) or "").strip()]
           for m in re.finditer(r"<loc>\s*([^<]+?)\s*</loc>(?:\s*<lastmod>\s*([^<]+?)\s*</lastmod>)?", text)]
    cache.parent.mkdir(exist_ok=True)
    cache.write_text(json.dumps(out))
    return out


def _page_cards(page, obj_rx, cards):
    # A card is the largest element around a link that holds no other apartment's link
    links = page.eval_on_selector_all(
        "a[href]", """(els, rx) => { const re = new RegExp(rx), key = h => h.split('#')[0];
            const others = (el, own) => [...el.querySelectorAll('a[href]')].some(x => re.test(x.href) && key(x.href) !== own);
            return els.map(a => { const own = key(a.href); let card = a;
                while (card.parentElement && card.parentElement !== document.body && !others(card.parentElement, own)) card = card.parentElement;
                return [own, (card.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 400)]; }); }""", obj_rx)
    for h, t in links:
        if re.search(obj_rx, h) and not re.search(r"/(salda|sold|slutpris)", h, re.I):
            cards[h] = max(cards.get(h, ""), t, key=len)


MORE_RX = r"^\s*(ladda|visa) fler(?! sålda)"
# The necessary-cookies-only answer on a cookie banner. Cookiebot's "Allow selection" keeps
# only necessary cookies, which are the only ones ticked to begin with.
NO_COOKIES_RX = re.compile(r"^\s*((?:neka|avvisa|avböj)(?: alla)?|deny( all)?|decline( all)?|reject all|endast nödvändiga|bara nödvändiga|"
                           r"(use )?(only )?necessary( cookies)?( only)?|tillåt urval|allow selection)\s*$", re.I)


def decline_cookies(page):
    """Answer a cookie banner with necessary cookies only, so it doesn't cover the list.
    Banners often appear a moment after the page, so it watches for one for a few seconds."""
    for _ in range(6):
        try:
            btn = page.locator("button:visible, a[role=button]:visible").filter(has_text=NO_COOKIES_RX)
            if btn.count():
                btn.first.click(timeout=3000)
                page.wait_for_timeout(800)
                return
        except Exception:
            return
        page.wait_for_timeout(700)


def list_cards(page, url, obj_rx, clicks=8, max_pages=30, more_rx=MORE_RX, picks=()):
    """Every apartment link on a for-sale list, with its card text. Scrolls, presses
    "Ladda fler" / "Visa fler" (or the agent's own label) so lists that load in parts show
    everything, and follows numbered page links ("?page=2", "?p=2") for lists split into pages.
    `picks` are filter labels to click first, for lists whose filters aren't in the address."""
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(2500)
    decline_cookies(page)
    for label in picks:
        page.get_by_text(label, exact=True).first.click(timeout=5000)
        page.wait_for_timeout(2000)
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
        more = page.locator("button, a").filter(has_text=re.compile(more_rx, re.I))
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
        if u.path == base and (m := re.search(r"(?:^|&)(?:page|p)=(\d+)", u.query)) and int(m.group(1)) > 1:
            pages.setdefault(int(m.group(1)), h)
    # Page links often skip the middle ("1 2 3 4 5 … 18"), so every page up to the last is visited
    if pages:
        last = max(pages)
        template = pages[last]
        pages = {n: re.sub(r"((?:^|[?&])(?:page|p)=)\d+", rf"\g<1>{n}", template) for n in range(2, last + 1)}
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
            found.update(list_cards(page, url, agent["obj"], clicks=agent.get("clicks", 8), more_rx=agent.get("more", MORE_RX), picks=agent.get("pick", ())))
        except Exception as e:
            log(f"{agent['name']}: couldn't read {url} ({e})")
        time.sleep(3)
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=SITEMAP_DAYS)).isoformat()
    for url in agent.get("sitemaps", []):
        try:
            for u, lastmod in sitemap_entries(url, cache_dir):
                # An agent whose sitemap lists only what is for sale now, without dates, takes every entry
                if re.search(agent["obj"], u) and (agent.get("undated") or (lastmod and lastmod >= since)) and u not in found:
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
SOON_RX = re.compile(r"snart till salu|kommer snart|\bpå gång\b|snart på marknaden|kommande försäljning|på förhand|förhandsvisning", re.I)
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


# Where a page stops being about this apartment: lists of other apartments, whose "Budgivning pågår" isn't ours
OTHERS_RX = re.compile(r"^\s*(?:liknande (?:bostäder|objekt)|andra (?:bostäder|har också tittat på)|fler bostäder|"
                       r"bostäder i närheten|andra objekt|du kanske också gillar|senast sålda|missa inte din drömbostad)\b", re.I | re.M)

BID_AMOUNT = re.compile(r"^(\d{1,3}(?: \d{3}){1,3}) ?kr$")
BID_WHEN = re.compile(
    r"^(?:(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})|(?P<d2>\d{1,2})/(?P<m2>\d{1,2})(?:[/-](?P<y2>\d{2,4}))?|"
    rf"(?P<d3>\d{{1,2}}) (?P<mon>{MONTH_RX})[a-zåäö]*\.?(?: (?P<y3>\d{{4}}))?)"
    r"(?:,? (?:kl\.? ?)?(?P<H>\d{1,2})[:.](?P<M>\d{2}))?$", re.I)
BID_WHO = re.compile(r"^(?:budgivare|bidder|budg\.?)? ?(\d{1,3}|[A-Z]\d{0,2})$", re.I)


def _when(m, now):
    g = m.groupdict()
    d, mo, y = (g["d"] or g["d2"] or g["d3"]), g["m"] or g["m2"], g["y"] or g["y2"] or g["y3"]
    if g["mon"]:
        mo = [s[:3] for s in SV_MONTHS].index(g["mon"][:3].lower()) + 1
    y = int(y) + (2000 if y and len(y) == 2 else 0) if y else now.year
    try:
        at = dt.datetime(y, int(mo), int(d), int(g["H"] or 0), int(g["M"] or 0))
    except ValueError:
        return None
    if not (g["y"] or g["y2"] or g["y3"]) and at > now + dt.timedelta(days=2):
        at = at.replace(year=y - 1)  # "14/10" seen in January is last October
    return at.isoformat(timespec="minutes")


def parse_bid_list(text, now=None):
    """The agent's own list of bids, where it shows one: [{"amount", "at", "who"}], newest first as the page has it.
    A row is an amount, a date with a time, and usually a bidder number, in any order and on one to three lines
    ("3 365 000 kr | Budgivare 4 | 2026-10-21 09:09", "1  14/10 15:24  2 550 000 kr", "4 900 000 kr | 12 aug 2026 kl. 20:46 | 1")."""
    now = now or dt.datetime.now()
    cells = [c.strip() for c in re.split(r"[\n\t|]+", re.sub(r"[\u00a0\u202f]", " ", text)) if c.strip()]
    rows, i = [], 0
    while i < len(cells):
        window = cells[i:i + 3]
        amt = next((j for j, c in enumerate(window) if BID_AMOUNT.match(c)), None)
        when = next((j for j, c in enumerate(window) if BID_WHEN.match(c) and BID_WHEN.match(c).group("H")), None)
        if amt is None or when is None or (min(amt, when) > 0 and not BID_WHO.match(window[0])):
            i += 1  # a row starts at its amount, its time or its bidder, not at a heading before them
            continue
        who = next((BID_WHO.match(c).group(1) for j, c in enumerate(window) if j not in (amt, when) and BID_WHO.match(c)), None)
        at = _when(BID_WHEN.match(window[when]), now)
        if at:
            rows.append({"amount": int(BID_AMOUNT.match(window[amt]).group(1).replace(" ", "")), "at": at, "who": who})
        i += max(amt, when) + 1
    rows = list({(r["amount"], r["at"], r["who"]): r for r in rows}.values())  # the same list read twice (page and bidding page)
    # A real list has at least one row; amounts beside dates elsewhere (a showing with a price) are one-offs
    return rows if len(rows) >= 2 or (rows and re.search(r"budgivare|budg\.|\bbud\b", text, re.I)) else []


def parse_bidding(text):
    """Where an apartment is in its sale: for sale, in bidding (with the highest bid and how
    many bids there are) or sold (with the final price, when the agent publishes it)."""
    t = full = re.sub(r"[\u00a0\u202f]", " ", text)
    if m := OTHERS_RX.search(t):
        t = t[:m.start()]  # the state is read from this apartment's part of the page only
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
    if bl := parse_bid_list(full):  # an opened bid list is often added at the end of the page
        out.update(state="bidding" if out["state"] == "for sale" else out["state"], bidList=bl, bids=len(bl),
                   bidders=len({b["who"] for b in bl if b["who"]}) or None, bid=max(out.get("bid") or 0, max(b["amount"] for b in bl)))
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


# ---------- Floor plans ----------

PLAN_WORDS = r"planritning|planlösning|plan\s*ritning|floor\s*-?plan|planskiss"
_PLAN_JS = r"""([rx, minW]) => {
  const re = new RegExp(rx, "i"), out = [];
  const big = (u) => u && /^https?:/.test(u) && !/\.svg(\?|$)|\.pdf(\?|$)|logo|icon|instagram|avatar/i.test(u);
  const src = (el) => el.currentSrc || el.src || el.getAttribute("data-src") || el.getAttribute("data-lazy") || (el.getAttribute("srcset") || "").split(",").pop().trim().split(" ")[0] || "";
  const add = (u) => { if (big(u) && !out.includes(u)) out.push(u); };
  // 1. Images and lightbox links named as a floor plan
  for (const el of document.querySelectorAll("img, a[href], [data-fancybox], [data-lightbox], [data-caption]")) {
    const label = [el.alt, el.title, el.getAttribute("aria-label"), el.dataset && (el.dataset.fancybox || el.dataset.lightbox || el.dataset.caption), (el.closest("figure") || {}).innerText].filter(Boolean).join(" ");
    if (!re.test(label)) continue;
    if (el.tagName === "IMG") { if (!el.naturalWidth || el.naturalWidth >= minW) add(src(el)); }
    else if (/\.(jpe?g|png|webp|gif)(\?|$)/i.test(el.href || "")) add(el.href);
  }
  if (out.length) return out;  // named floor plans are the surest; a section may also hold the gallery's photos
  const fromSection = (sec) => { for (const img of sec.querySelectorAll("img, source")) if (!img.naturalWidth || img.naturalWidth >= minW) add(src(img)); };
  // 2. Images in the nearest box around a "Planritning" label that isn't a link (a heading, a folded section)
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walk.nextNode()) {
    const n = walk.currentNode;
    if (!re.test(n.nodeValue) || n.nodeValue.trim().length > 30 || n.parentElement.closest("a, nav, header, footer")) continue;
    let el = n.parentElement;
    for (let i = 0; i < 4 && el; i++, el = el.parentElement) {
      const imgs = [...el.querySelectorAll("img")].filter((img) => !img.naturalWidth || img.naturalWidth >= minW);
      if (imgs.length) { imgs.slice(0, 3).forEach((img) => add(src(img))); break; }
    }
  }
  if (out.length) return out;
  // 3. The section the page's own "Planritning" link points to
  for (const a of document.querySelectorAll('a[href*="#"]')) {
    if (!re.test(a.innerText || "") && !re.test(a.hash || "")) continue;
    let target = null;
    try { target = a.hash && document.querySelector(a.hash); } catch (e) {}
    if (target && !/bild|image|gallery|galleri/i.test(a.hash)) fromSection(target);
  }
  // 4. Sections whose id or class names the floor plan
  for (const sec of document.querySelectorAll('[id*="plan" i], [class*="planritning" i], [class*="floorplan" i], [class*="floor-plan" i]')) {
    if (!re.test(sec.id + " " + sec.className) && !/^plan$/i.test(sec.id)) continue;
    fromSection(sec);
  }
  return out;
}"""


def floorplans(page, limit=4):
    """The listing's floor plans. Most agents name them (alt text, a caption, a "Planritning" section);
    otherwise the page's floor plan button is pressed and the large images it shows are taken."""
    found = page.evaluate(_PLAN_JS, [PLAN_WORDS, 300])
    if not found:
        before = set(page.eval_on_selector_all("img", "els => els.map(i => i.currentSrc || i.src)"))
        btn = page.locator("button:visible, a:visible").filter(has_text=re.compile(rf"^\s*(?:visa\s+)?(?:\d+\s+)?(?:{PLAN_WORDS})(?:ar|en|er)?\s*$", re.I))
        if btn.count():
            try:
                btn.first.click(timeout=3000)
                page.wait_for_timeout(1500)
                found = page.evaluate(_PLAN_JS, [PLAN_WORDS, 300])
                if not found:  # a gallery opened at the floor plan: the new large images
                    found = [u for u in page.eval_on_selector_all(
                        "img", "els => els.filter(i => i.naturalWidth >= 500 && i.getBoundingClientRect().width > 200).map(i => i.currentSrc || i.src)")
                        if u not in before and u.startswith("http")][:2]
                page.keyboard.press("Escape")
            except Exception:
                pass
    out, seen = [], set()
    for u in found:
        k = _photo_key(u)
        if k not in seen:
            seen.add(k)
            out.append(u)
    return out[:limit]


# ---------- Bid lists ----------

OPEN_BIDS_RX = re.compile(r"^\s*(?:budgivning pågår\s*[–-]\s*)?(?:visa|se)\s+(?:alla\s+)?(?:bud(?:givning|givningen|historik|en|lista)?)\s*$"
                          r"|^\s*(?:budhistorik|alla bud|visa/lämna bud)\s*$", re.I)


def bid_text(page):
    """The text of the agent's bid list, opened if it's folded away: a "Visa budgivning" button, or a
    separate bidding page linked from the apartment (Mäklarhuset's /objekt/budgivning/<id>)."""
    out = ""
    btn = page.locator("button:visible, summary:visible, a:visible, [role=button]:visible").filter(has_text=OPEN_BIDS_RX)
    if btn.count():
        try:
            href = btn.first.get_attribute("href") or ""
            if not href.startswith("http") or urlparse(href).netloc == urlparse(page.url).netloc and "budgivning" not in href:
                btn.first.click(timeout=3000)
                page.wait_for_timeout(1500)
                out = page.inner_text("body")
                page.keyboard.press("Escape")
        except Exception:
            pass
    # A bidding page of its own: same site, "budgivning" in the address, and this apartment's number in it
    num = (re.findall(r"\d{5,}", urlparse(page.url).path) or [None])[-1]
    links = [h for h in page.eval_on_selector_all("a[href]", "els => els.map(a => a.href)")
             if "budgivning" in h.lower() and urlparse(h).netloc == urlparse(page.url).netloc and num and num in h]
    if links:
        here = page.url
        try:
            page.goto(links[0], wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2000)
            out += "\n" + page.inner_text("body")
            page.goto(here, wait_until="domcontentloaded", timeout=45000)
        except Exception:
            pass
    return out


# ---------- Red flags ----------

# Words that turn a sentence around: "inga planerade avgiftshöjningar", "ej tomträtt", "utan stambyte"
_NEG = re.compile(r"\b(?:inga|ingen|inget|ej|inte|icke|utan|aldrig|slipper|undviker|undvika)\b[^.!?]{0,60}$", re.I)
_MAYBE = re.compile(r"\b(?:eventuell\w*|kan komma|möjligen|möjligtvis|kanske)\b", re.I)
_YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")


def _sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def _hit(rx, sentences):
    """(sentence, negated?) for the first sentence that matches."""
    for s in sentences:
        if m := re.search(rx, s, re.I):  # "inga planerade avgiftshöjningar", or "stambyte kommer inte att ske"
            return s, bool(_NEG.search(s[:m.start()]) or re.match(r"\W*(?:\w+\W+){0,2}?(?:inte|ej|icke)\b", s[m.end():], re.I))
    return None, False


def red_flags(desc, built=None, now=None):
    """What the description says about the förening that could cost you, and what reassures:
    [{"kind": "bad" | "warn" | "good" | "ask", "label", "quote"}]. Each flag quotes the sentence it comes from;
    "ask" is something to ask the agent about, shown only on saved apartments."""
    year = (now or dt.datetime.now()).year
    ss, out = _sentences(re.sub(r"[  ]", " ", desc or "")), []
    add = lambda kind, label, quote: out.append({"kind": kind, "label": label, "quote": quote[:300]})

    # Stambyte (new pipes): coming is costly, done is good; an old house that never mentions it is worth asking about
    s, neg = _hit(r"stambyte\w*\s+(?:är\s+|har\s+|blev\s+)?(?:planera|beslut|kommer|ska|skall|inplanera|påbörja|projekter|pågår|förestår)"
                  r"|(?:planera|beslut|kommande|förestående|stundande)\w*\s+stambyte|relining\s+(?:planera|kommer|ska)", ss)
    if s and not neg:
        add("warn", "Stambyte kan komma", s) if _MAYBE.search(s) else add("bad", "Stambyte på gång", s)
    elif s2 := _hit(r"stamby(?:te|tet|ten|tt)|nya stammar|relin", ss)[0]:
        ys = [int(y) for y in _YEAR.findall(s2)]
        if ys and max(ys) > year:
            add("bad", f"Stambyte {max(ys)}", s2)
        elif ys or re.search(r"genomför|gjor|utför|klar|färdig|stambytt|nya stammar|redan", s2, re.I):
            add("good", f"Stambyte gjort{' ' + str(max(ys)) if ys else ''}", s2)
    elif built and int(built) < 1980:
        add("ask", "Fråga när stambytet gjordes", f"Byggt {built}; beskrivningen säger inte när stammarna senast byttes.")

    # The avgift going up
    s, neg = _hit(r"avgift\w*\s+(?:kommer\s+att\s+|ska\s+|planeras\s+att\s+)?(?:höj|justera\w*\s+upp)|(?:höjning|höjd|höjt)\w*\s+(?:av\s+)?(?:års|månads)?avgift|avgiftshöjning", ss)
    if s:
        add("good", "Ingen avgiftshöjning planerad", s) if neg else add("warn", "Avgiften kan höjas", s) if _MAYBE.search(s) else add("bad", "Avgiften höjs", s)

    # Tomträtt: the plot is leased from the city, and the ground rent can rise steeply
    s, neg = _hit(r"tomträtt", ss)
    if s and not neg and not re.search(r"äger (?:sin |själv |)mark|friköpt", s, re.I):
        add("bad", "Tomträtt", s)
    elif (s and neg) or (s := _hit(r"äger (?:sin |själva |själv |)marken|friköpt mark|marken (?:är )?friköpt", ss)[0]):
        add("good", "Äger marken", s)

    # Äkta or oäkta förening (oäkta changes the tax rules when you sell)
    s, neg = _hit(r"oäkta\s+(?:bostadsrätts)?förening", ss)
    if s and not neg:
        add("bad", "Oäkta förening", s)
    elif s := _hit(r"\bäkta\s+(?:bostadsrätts)?förening|\bäkta brf", ss)[0]:
        add("good", "Äkta förening", s)

    # The förening's debt per m² (belåning): under 5 000 kr is low, over 15 000 kr is high
    s, _ = _hit(r"(?:belåning|skuld|lån)\w*[^.]{0,60}?\d{1,2}[ .]?\d{3}\s*kr\s*(?:/|per)\s*(?:kvm|m²|m2|kvadrat)"
                r"|\d{1,2}[ .]?\d{3}\s*kr\s*(?:/|per)\s*(?:kvm|m²|m2)[^.]{0,30}(?:belåning|skuld|lån)", ss)
    if s and (m := re.search(r"(\d{1,2}[ .]?\d{3})\s*kr\s*(?:/|per)\s*(?:kvm|m²|m2|kvadrat)", s, re.I)):
        v = int(re.sub(r"\D", "", m.group(1)))
        if v > 15000:
            add("bad", f"Hög skuld: {v:,} kr/m²".replace(",", " "), s)
        elif v > 5000:
            add("warn", f"Måttlig skuld: {v:,} kr/m²".replace(",", " "), s)
        else:
            add("good", f"Låg skuld: {v:,} kr/m²".replace(",", " "), s)
    elif s := _hit(r"skuldfri|helt utan (?:lån|skulder)", ss)[0]:
        add("good", "Skuldfri förening", s)
    elif (h := _hit(r"(?:hög|högre)\s+belåning", ss))[0] and not h[1]:
        add("bad", "Hög skuld", h[0])

    # A small förening: big repairs are shared by few
    s, _ = _hit(r"(?:omfattar|består av|har|med|totalt)\s+(?:endast\s+|bara\s+|totalt\s+)?(\d{1,3})\s+(?:lägenheter|bostadsrätter|bostäder|medlemmar)", ss)
    if s and (m := re.search(r"(\d{1,3})\s+(?:lägenheter|bostadsrätter|bostäder|medlemmar)", s)) and int(m.group(1)) < 12:
        add("warn", f"Liten förening: {m.group(1)} lägenheter", s)

    # Big works on the building that the förening hasn't paid for yet
    s, neg = _hit(r"(?:planera|beslut|kommande|förestående|stundande)\w*\s+(?:fasad|tak|fönster|balkong|hiss)\w*renovering|"
                  r"(?:fasad|tak|fönster)\w*(?:renovering|byte)\w*\s+(?:är\s+)?(?:planera|beslut|kommer|ska)", ss)
    if s and not neg:
        add("warn", "Renovering av huset planerad", s)
    return out
