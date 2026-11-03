"""The förening's figures from its own annual report, through Bolagsverket's free API ("värdefulla datamängder").

Since 2023 a bostadsrättsförening's annual report must give the same key figures: årsavgift per m², debt per m²,
savings per m², interest-rate sensitivity and energy cost per m². Reports filed digitally come as iXBRL, where
each figure carries a name, so they can be read without guessing at the layout.

The API knows a förening only by its organisationsnummer. Credentials (client id from Bolagsverket's e-mail,
client secret from its text message) go in data/bolagsverket.json, which git ignores:

    {"client_id": "...", "client_secret": "...", "env": "test"}

"env" is "test" for the acceptance environment (the code marked "Test" in the text message) or "prod".

    python scraper/bolagsverket.py --setup FILE.zip    write data/bolagsverket.json from Bolagsverket's zip
                                                       (asks for the code from the text message, without showing it)
    python scraper/bolagsverket.py --ping              check that the credentials work
    python scraper/bolagsverket.py --org 7696001234    one förening: its name, reports and key figures
    python scraper/bolagsverket.py --update            every förening we have an organisationsnummer for
"""
import argparse
import datetime as dt
import io
import json
import os
import re
import sys
import time
import base64
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from html import unescape
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
CREDS = DATA / "bolagsverket.json"
CACHE = DATA / "foreningar.json"
HOSTS = {"prod": "gw.api.bolagsverket.se", "test": "gw-accept2.api.bolagsverket.se"}
# The spec puts the token at /oauth2/token on the gateway; some clients use the portal host instead
TOKEN_HOSTS = {"prod": ("gw.api.bolagsverket.se", "portal.api.bolagsverket.se"),
               "test": ("gw-accept2.api.bolagsverket.se", "portal-accept2.api.bolagsverket.se")}
SCOPE = "vardefulla-datamangder:read vardefulla-datamangder:ping"
REFRESH_DAYS = 120  # a förening files one report a year; look again a few times a year for the new one
PAUSE = 1.0  # seconds between calls, to be a polite client

# The key figures, found by words in their iXBRL names (accent-free, any case); the taxonomy's exact names vary
KEY_FIGURES = {
    "avgiftKvm": (r"arsavgift.*(kvm|kvadratmeter|yta)", r"avgift.*per.*(kvm|kvadratmeter)"),
    "skuldKvm": (r"skuld.*(kvm|kvadratmeter|yta)", r"belaning.*(kvm|kvadratmeter)"),
    "sparandeKvm": (r"sparande.*(kvm|kvadratmeter|yta)",),
    "rantekanslighet": (r"rantekanslighet",),
    "energiKvm": (r"energikostnad.*(kvm|kvadratmeter|yta)",),
}


class NoCredentials(Exception):
    pass


class Refused(Exception):
    """Bolagsverket said no (401, 403 or 429): stop for today, never retry."""


class Client:
    def __init__(self, creds=None):
        creds = creds or read_creds()
        self.env = creds.get("env", "prod")
        self.id, self.secret = creds["client_id"], creds["client_secret"]
        self.base = f"https://{HOSTS[self.env]}/vardefulla-datamangder/v1"
        self.token, self.expires = None, 0

    def _open(self, req):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def _token(self):
        if self.token and time.time() < self.expires - 30:
            return self.token
        basic = base64.b64encode(f"{self.id}:{self.secret}".encode()).decode()
        body = urllib.parse.urlencode({"grant_type": "client_credentials", "scope": SCOPE}).encode()
        last = None
        for host in TOKEN_HOSTS[self.env]:
            status, data = self._open(urllib.request.Request(f"https://{host}/oauth2/token", data=body, headers={
                "Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"}))
            if status == 200:
                j = json.loads(data)
                self.token, self.expires = j["access_token"], time.time() + int(j.get("expires_in", 3600))
                return self.token
            last = (status, data[:200].decode("utf-8", "replace"))
        raise Refused(f"Bolagsverket refused the credentials ({last[0]}: {last[1]})")

    def _call(self, method, path, body=None):
        time.sleep(PAUSE)
        headers = {"Authorization": f"Bearer {self._token()}", "Accept": "*/*"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        status, data = self._open(urllib.request.Request(self.base + path, method=method, headers=headers,
                                                         data=json.dumps(body).encode() if body is not None else None))
        if status == 404:
            return None
        if status in (401, 403, 429):
            raise Refused(f"Bolagsverket answered {status} on {path}")
        if status >= 400:
            raise RuntimeError(f"Bolagsverket answered {status} on {path}: {data[:200]!r}")
        return data

    def ping(self):
        return self._call("GET", "/isalive").decode().strip()

    def organisation(self, orgnr):
        data = self._call("POST", "/organisationer", {"identitetsbeteckning": orgnr})
        orgs = (json.loads(data) if data else {}).get("organisationer") or []
        return orgs[0] if orgs else None

    def documents(self, orgnr):
        data = self._call("POST", "/dokumentlista", {"identitetsbeteckning": orgnr})
        return (json.loads(data) if data else {}).get("dokument") or []

    def document(self, doc_id):
        return self._call("GET", f"/dokument/{urllib.parse.quote(doc_id)}")


def read_creds():
    try:
        creds = json.loads(CREDS.read_text())
    except (OSError, ValueError):
        raise NoCredentials(f"No Bolagsverket credentials: put client_id, client_secret and env in {CREDS}")
    if not creds.get("client_id") or not creds.get("client_secret"):
        raise NoCredentials(f"{CREDS} needs both client_id and client_secret")
    return creds


def setup(path):
    """Write the client id and secret to data/bolagsverket.json, from Bolagsverket's password-protected zip (opened
    with the code from its text message) or from the client_credentials.txt already taken out of it. Nothing is
    printed but the result."""
    if path.lower().endswith(".txt"):
        name, text, test = Path(path).name, Path(path).read_text("utf-8-sig"), None
    else:
        import getpass
        code = getpass.getpass("The code from Bolagsverket's text message (not shown): ").strip()
        test = input("Is it the code marked \"Test\"? [y/N] ").strip().lower().startswith(("y", "j"))
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.lower().endswith(".txt"))
            try:
                text = z.read(name, pwd=code.encode()).decode("utf-8-sig", "replace")
            except RuntimeError:
                sys.exit("That code doesn't open the zip. Try the other code from the text message.")
    pairs = {re.sub(r"[^a-z]", "", k.lower()): v.strip() for k, v in re.findall(r"^\s*([\w .-]+?)\s*[:=]\s*(\S+)\s*$", text, re.M)}
    cid = next((v for k, v in pairs.items() if "id" in k), None)
    secret = next((v for k, v in pairs.items() if "secret" in k), None)
    if not (cid and secret):
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if len(lines) == 2:
            cid, secret = lines
    if not (cid and secret):
        sys.exit(f"Couldn't tell the client id from the secret in {name}; its lines are named: "
                 + ", ".join(k for k in pairs) or "(none)")
    if test is None:  # a bare text file doesn't say which environment it's for: ask each for a token
        why = []
        for env in ("prod", "test"):
            try:
                Client({"client_id": cid, "client_secret": secret, "env": env})._token()
                test = env == "test"
                break
            except Exception as e:
                why.append(f"{env}: {e}")
        else:
            sys.exit("Neither environment accepted these credentials. " + " / ".join(why))
    CREDS.parent.mkdir(exist_ok=True)
    CREDS.write_text(json.dumps({"client_id": cid, "client_secret": secret, "env": "test" if test else "prod"}))
    CREDS.chmod(0o600)
    print(f"Saved to {CREDS} ({'test' if test else 'prod'} environment). Check it with: python scraper/bolagsverket.py --ping")


def clean_orgnr(s):
    """"769600-1234" or "7696001234" → "7696001234"; a förening's number starts with 7."""
    d = re.sub(r"\D", "", s or "")
    return d if len(d) == 10 and d.startswith("7") else None


def _ascii(s):
    return s.lower().translate(str.maketrans("åäöé", "aaoe"))


def figures(zip_bytes):
    """The key figures for the latest year in an annual report zip (iXBRL), as plain numbers."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        names = [n for n in z.namelist() if n.lower().endswith((".xhtml", ".html", ".htm", ".xml"))]
        if not names:
            return {}
        doc = z.read(max(names, key=lambda n: z.getinfo(n).file_size)).decode("utf-8", "replace")
    # Which context is the latest year: the one whose period ends last
    ends = {}
    for m in re.finditer(r'<xbrli:context[^>]*\bid="([^"]+)".*?</xbrli:context>', doc, re.S):
        when = re.findall(r"<xbrli:(?:endDate|instant)>([\d-]+)<", m.group(0))
        if when:
            ends[m.group(1)] = max(when)
    facts = []
    for m in re.finditer(r"<ix:nonFraction\b([^>]*)>(.*?)</ix:nonFraction>", doc, re.S):
        attrs = dict(re.findall(r'(\w+(?::\w+)?)="([^"]*)"', m.group(1)))
        text = re.sub(r"<[^>]+>", "", unescape(m.group(2)))
        digits = re.sub(r"[^\d,.-]", "", text).replace(" ", "")
        if not re.search(r"\d", digits):
            continue
        v = float(digits.replace(".", "").replace(",", ".")) if "," in digits else float(digits.replace(".", "") if digits.count(".") > 1 else digits)
        v *= 10 ** int(attrs.get("scale", "0") or 0)
        if attrs.get("sign") == "-":
            v = -v
        facts.append((_ascii(attrs.get("name", "")), ends.get(attrs.get("contextRef"), ""), v))
    out = {}
    for key, patterns in KEY_FIGURES.items():
        hits = [(end, v) for name, end, v in facts if any(re.search(p, name) for p in patterns)]
        if hits:
            out[key] = round(max(hits)[1])  # the latest period's figure
    if facts and ends:
        out["period"] = max(ends.values())
    return out


def read_cache():
    try:
        return json.loads(CACHE.read_text())
    except (OSError, ValueError):
        return {}


def forening(client, orgnr, cache):
    """What Bolagsverket has for one förening, from the cache when it was read recently."""
    old = cache.get(orgnr)
    if old and old.get("read", "") > (dt.date.today() - dt.timedelta(days=REFRESH_DAYS)).isoformat():
        return old
    org = client.organisation(orgnr) or {}
    name = ((org.get("organisationsnamn") or {}).get("organisationsnamnLista") or [{}])[0].get("namn") if org else None
    # A wrong number (another förening's, read off the page) shows as a name that isn't a bostadsrättsförening
    out = {"read": dt.date.today().isoformat(), "name": name, "isBrf": bool(name and re.search(r"bostadsrätt|\bbrf\b", name, re.I))}
    if org:
        # A förening being wound up, in bankruptcy or merging, or no longer registered, is a red flag on its own
        out["registered"] = (org.get("organisationsdatum") or {}).get("registreringsdatum")
        out["winding"] = bool(org.get("pagaendeAvvecklingsEllerOmstruktureringsforfarande"))
        out["deregistered"] = bool(org.get("avregistreradOrganisation"))
        out["manager"] = (((org.get("postadressOrganisation") or {}).get("postadress") or {}).get("coAdress") or "").removeprefix("c/o ").strip() or None
    docs = sorted(client.documents(orgnr), key=lambda d: d.get("rapporteringsperiodTom") or "", reverse=True)
    out["reports"] = [d.get("rapporteringsperiodTom") for d in docs]
    for d in docs[:2]:  # the latest report, or the one before if the latest has no key figures
        z = client.document(d["dokumentId"])
        try:
            f = figures(z) if z else {}
        except zipfile.BadZipFile:
            f = {}
        if any(k in f for k in KEY_FIGURES):
            out.update(f, reportPeriod=d.get("rapporteringsperiodTom"))
            break
    cache[orgnr] = out
    return out


def update(items, log=print):
    """Bolagsverket's figures for every apartment whose förening's organisationsnummer we know; stored on the
    apartment as "bv", and in data/foreningar.json so each förening is fetched once."""
    try:
        client = Client()
    except NoCredentials as e:
        log(str(e))
        return 0
    cache, n = read_cache(), 0
    todo = {p["orgnr"] for p in items.values() if p.get("orgnr") and not p.get("outside")}
    for orgnr in sorted(todo):
        try:
            forening(client, orgnr, cache)
        except Refused as e:
            log(f"{e}; stopping for today")
            break
        except (OSError, RuntimeError) as e:
            log(f"Bolagsverket: couldn't read {orgnr} ({e})")
            continue
        n += 1
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
    for p in items.values():
        if p.get("orgnr") in cache:
            p["bv"] = {k: v for k, v in cache[p["orgnr"]].items() if k not in ("read",)}
    log(f"Bolagsverket: {n} föreningar looked up, {sum(1 for c in cache.values() if 'skuldKvm' in c)} with key figures")
    return n


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--setup", metavar="ZIP")
    ap.add_argument("--ping", action="store_true")
    ap.add_argument("--org")
    ap.add_argument("--update", action="store_true")
    a = ap.parse_args()
    try:
        if a.setup:
            setup(a.setup)
        elif a.ping:
            c = Client()
            print(f"{c.env}: {c.ping()}")
        elif a.org:
            c = Client()
            print(json.dumps(forening(c, clean_orgnr(a.org) or a.org, {}), ensure_ascii=False, indent=1))
        elif a.update:
            lock = DATA / ".scrape.lock"  # the finder's own lock: the two never write listings.json at once
            try:
                os.kill(int(lock.read_text()), 0)
                sys.exit("The finder is running; try again when it's done.")
            except (OSError, ValueError):
                pass
            lock.write_text(str(os.getpid()))
            try:
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                import finder
                store = finder.read_json(DATA / "listings.json", {})
                update(store.get("items", {}))
                finder.write_json(DATA / "listings.json", store)
            finally:
                lock.unlink(missing_ok=True)
        else:
            ap.print_help()
    except (NoCredentials, Refused) as e:
        sys.exit(str(e))
