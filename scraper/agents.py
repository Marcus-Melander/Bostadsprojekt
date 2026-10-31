"""Estate agent sites: find a Booli listing on its agent's own site and read the details there.

Booli names the agent on each search result. Each agent below says where it lists
apartments for sale: a for-sale page (read in the browser, matched on the link text)
or a sitemap (matched on the street address in the link). The listing page itself is
read with one shared parser, since most agents show the same things the same way.
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

AGENTS = [
    {"name": "Alicia Edelman", "match": "alicia edelman", "lists": ["https://aliciaedelman.se/till-salu"]},
    {"name": "Notar", "match": "notar", "lists": ["https://www.notar.se/kopa-bostad?sortBy=date&sortOrder=desc"]},
    {"name": "BOSTHLM", "match": "bosthlm", "lists": ["https://www.bosthlm.se/till-salu"]},
    {"name": "Historiska Hem", "match": "historiska hem", "lists": ["https://historiskahem.se/till-salu/"]},
    {"name": "Tradition", "match": "tradition", "lists": ["https://tradition.se/till-salu"]},
    {"name": "Södermäklarna", "match": "södermäklarna", "lists": ["https://www.sodermaklarna.se/till-salu"]},
    {"name": "Stadshem", "match": "stadshem", "lists": ["https://stadshem.se/till-salu"]},
    {"name": "Bergets Ro", "match": "bergets ro", "sitemaps": ["https://www.bergetsro.se/sitemap_index.xml"]},
    {"name": "Bjurfors", "match": "bjurfors", "sitemaps": ["https://www.bjurfors.se/sitemap.xml"]},
    {"name": "Behrer & Partners", "match": "behrer", "sitemaps": ["https://www.behrer.se/sitemap.xml"]},
]


def agent_for(broker):
    b = (broker or "").lower()
    return next((a for a in AGENTS if a["match"] in b), None)


# ---------- Addresses ----------

def slug(s):
    s = unicodedata.normalize("NFKD", str(s).lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"(\d+)\s+([a-z])\b", r"\1\2", s)  # "33 A" and "33A" are the same door
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return re.sub(r"(\d+)-([a-z])(?=-|$)", r"\1\2", s)  # and so is "33-a" in a link


def matches(addr, href, text=""):
    a = slug(addr)
    if not a:
        return False
    # the address must end at a word boundary, so "Hornsgatan 15" doesn't match "Hornsgatan 153"
    rx = re.compile(rf"(^|-){re.escape(a)}(-|$)")
    return bool(rx.search(slug(urlparse(href).path)) or rx.search(slug(text)))


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


def sitemap_urls(url, cache_dir, depth=0):
    """Every page link in a sitemap, following sitemap indexes. Cached for most of a day."""
    cache = Path(cache_dir) / ("sitemap-" + slug(url)[:80] + ".json")
    if depth == 0 and cache.exists() and time.time() - cache.stat().st_mtime < CACHE_HOURS * 3600:
        return json.loads(cache.read_text())
    text = fetch_text(url)
    locs = re.findall(r"<loc>\s*([^<]+?)\s*</loc>", text)
    if "<sitemapindex" in text and depth < 2:
        out = []
        for sub in locs:
            if re.search(r"post|page-sitemap|news|blog|nyhet|artik|kontor|broker|maklare|personal|category|tag", sub, re.I):
                continue
            out += sitemap_urls(sub, cache_dir, depth + 1)
            time.sleep(1)
        locs = out
    if depth == 0:
        cache.parent.mkdir(exist_ok=True)
        cache.write_text(json.dumps(locs))
    return locs


def list_links(page, url):
    """Links on an agent's for-sale page, after scrolling so lazy lists load."""
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(2500)
    for _ in range(6):
        page.mouse.wheel(0, 5000)
        page.wait_for_timeout(700)
    return page.eval_on_selector_all(
        "a[href]", "els => els.map(a => [a.href, (a.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 200)])")


class AgentIndex:
    """Where each agent's apartments are, gathered once per run and only for agents we need."""

    def __init__(self, page, robots, cache_dir, log):
        self.page, self.robots, self.cache_dir, self.log = page, robots, cache_dir, log
        self.links = {}

    def _load(self, agent):
        links = []
        for url in agent.get("lists", []):
            if not self.robots.allowed(url):
                self.log(f"{agent['name']}: robots.txt asks visitors not to read {url}, skipped")
                continue
            try:
                links += [(h, t, True) for h, t in list_links(self.page, url)]
            except Exception as e:
                self.log(f"{agent['name']}: couldn't read {url} ({e})")
            time.sleep(3)
        for url in agent.get("sitemaps", []):
            try:
                links += [(h, "", False) for h in sitemap_urls(url, self.cache_dir)]
            except Exception as e:
                self.log(f"{agent['name']}: couldn't read sitemap {url} ({e})")
        self.links[agent["name"]] = links
        self.log(f"{agent['name']}: {len(links)} links to match against")

    def candidates(self, agent, addr):
        """Pages that may be this apartment: links from the for-sale page first. Sitemaps also keep
        old sales of the same address, so the caller tries a few and keeps the one still for sale."""
        if agent["name"] not in self.links:
            self._load(agent)
        host = urlparse((agent.get("lists") or agent.get("sitemaps"))[0]).netloc.replace("www.", "")
        hits, seen = [], set()
        for h, t, listed in self.links[agent["name"]]:
            h = h.split("#")[0]
            if h in seen or host not in h or not matches(addr, h, t) or re.search(r"/(salda|sold|slutpris)", h, re.I):
                continue
            seen.add(h)
            hits.append((not listed, h))
        return [h for _, h in sorted(hits, key=lambda x: x[0])][:3]


# ---------- One parser for agent listing pages ----------

SV_MONTHS = ["januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti", "september", "oktober", "november", "december"]
MONTH_RX = "|".join(m[:3] for m in SV_MONTHS)
# "Söndag 8 november kl 12:15 - 13:00", "8 nov. 11:00", "8 | NOVEMBER 2026 | SÖNDAG | KL 12.30-13.00", "söndag 8 nov | 14:15 - 15:00"
SHOW_RX = re.compile(
    rf"(?<!\d)(\d{{1,2}})\s*(?:\|\s*)?({MONTH_RX})[a-zåäö]*\.?(?:\s*\d{{4}})?[^0-9]{{0,40}}?"
    rf"(\d{{1,2}})[:.](\d{{2}})(?:\s*[-–]\s*(\d{{1,2}})[:.](\d{{2}}))?", re.I)
FEATURE_RX = [
    ("stambyte done", r"stamby(?:te|tet|ten)\s+(?:är\s+|har\s+)?(?:gjor|genomför|utför|klar|färdig)|nya stammar|stambytt"),
    ("stambyte coming", r"stambyte\s+(?:är\s+)?(?:planera|beslut|kommer|inplanera|påbörja)"),
    ("renovated kitchen", r"(?:ny|ny-|helt? ?|nyligen |varsamt |totalt? ?)?renoverat kök|nytt kök|köket (?:är |har )?(?:ny|helt? |nyligen )?renoverat"),
    ("renovated bathroom", r"(?:ny|helt? ?|nyligen |totalt? ?)?renoverat (?:bad|dusch)rum|nytt (?:bad|dusch)rum|badrummet (?:är |har )?(?:ny|helt? |nyligen )?renoverat"),
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


def _date(day, month_idx, now):
    d = dt.date(now.year, month_idx + 1, day)
    if d < now.date() - dt.timedelta(days=7):
        d = d.replace(year=now.year + 1)
    return d


# "Såld" or "Slutpris: 4 150 000 kr" on the page, but not menu links such as "Sålda" or "Slutpriser"
SOLD_RX = re.compile(r"(?<![a-zåäö])(?:såld|sålt)(?![a-zåäö])|slutpris\s*:?\s*\d", re.I)


def is_sold(text):
    return bool(SOLD_RX.search(text[:4000]))


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
    v = value(r"månadsavgift|avgift")
    if v and len(v) < 200 and re.search(r"\d", v):
        out["avgiftText"] = v

    paras = [l for l in lines if len(l) >= 160 and not SKIP_PARA.search(l)]
    if paras:
        out["desc"] = "\n\n".join(paras[:5])[:3000]
    hay = " ".join([out.get("desc", ""), out.get("floorText", ""), out.get("avgiftText", "")]).lower()
    out["extra"] = [name for name, rx in FEATURE_RX if re.search(rx, hay)]
    if "stambyte done" in out["extra"] and "stambyte coming" in out["extra"]:
        out["extra"].remove("stambyte coming")
    return out
