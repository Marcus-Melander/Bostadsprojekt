"""Where an address is: coordinates, neighbourhood and district, from OpenStreetMap's Nominatim.

Nominatim asks for at most one lookup a second and an honest name for the app, so lookups
are spaced out and every answer is kept in data/geocode.json; an address is looked up once.
"""
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

UA = "Bostadsplan/1.0 (personal apartment planner)"
URL = "https://nominatim.openstreetmap.org/search?"

# Stockholm's city districts inside the tolls; a few parts of them lie outside, and are named here
INNER_DISTRICTS = {"Södermalms stadsdelsområde", "Kungsholmens stadsdelsområde", "Norra innerstadens stadsdelsområde"}
OUTSIDE = {"Stora Essingen", "Lilla Essingen", "Norra Djurgården", "Norra Djurgårdsstaden", "Hjorthagen", "Ropsten"}


class Geocoder:
    def __init__(self, data_dir, log):
        self.path = Path(data_dir) / "geocode.json"
        self.log = log
        try:
            self.cache = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            self.cache = {}
        self.last = 0.0
        self.lock = threading.Lock()  # one lookup at a time across every agent being read

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.cache, ensure_ascii=False, indent=1), "utf-8")
        os.replace(tmp, self.path)

    def lookup(self, address):
        """{lat, lon, area, group, district, inner} for a street address in Stockholm, or None."""
        with self.lock:
            return self._lookup(address)

    def _lookup(self, address):
        key = re.sub(r"\s+", " ", address.strip().lower())
        if key in self.cache:
            return self.cache[key]
        wait = 1.1 - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)
        q = urllib.parse.urlencode({"q": f"{address}, Stockholm", "format": "jsonv2", "addressdetails": 1, "limit": 1, "countrycodes": "se"})
        try:
            req = urllib.request.Request(URL + q, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                hits = json.load(r)
        except Exception as e:
            self.log(f"Couldn't look up {address} on OpenStreetMap ({e})")
            return None  # not cached, so it's tried again on the next run
        finally:
            self.last = time.time()
        place = None
        if hits:
            h, ad = hits[0], hits[0].get("address", {})
            district = ad.get("city_district") or ""
            suburb = ad.get("suburb") or ""
            area = ad.get("neighbourhood") or ad.get("quarter") or suburb or None
            place = {
                "lat": round(float(h["lat"]), 6), "lon": round(float(h["lon"]), 6),
                "area": area, "group": group_name(district, suburb), "district": district,
                "inner": district in INNER_DISTRICTS and suburb not in OUTSIDE and area not in OUTSIDE,
            }
        self.cache[key] = place
        self._save()
        return place


def group_name(district, suburb):
    """A plain name to group areas under: Södermalm, Kungsholmen, or the part of Norra innerstaden."""
    if district.startswith("Södermalm"):
        return "Gamla stan" if suburb == "Gamla stan" else "Södermalm"
    if district.startswith("Kungsholmen"):
        return "Kungsholmen"
    names = {"Vasastaden": "Vasastan", "Norrmalm": "Norrmalm", "Östermalm": "Östermalm", "Ladugårdsgärdet": "Gärdet"}
    return names.get(suburb, suburb or district.replace(" stadsdelsområde", ""))
