"""Builds areas.geojson: the inner city's districts and neighbourhoods, for the planner's map.

Districts (stadsdelar such as Södermalm, Vasastaden, Kungsholmen) are Stockholm's official
boundaries, as mapped in OpenStreetMap. Neighbourhoods (Skanstull, Sibirien, Hornstull…)
have no official borders: OpenStreetMap marks each with a point. So each neighbourhood is
drawn as the part of its district within about 500 m of its point, nearer to it than to any
other neighbourhood; the rest of a district keeps the district's name. That is roughly how
Nominatim names an address's neighbourhood, so the drawn areas agree with the names on the
apartments (a central Södermalm address is just "Södermalm").

Run it again only when the list of areas should change; it asks Nominatim about 60 questions,
one a second.

    .venv/bin/python scripts/build_areas.py
"""
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

from shapely.geometry import MultiPoint, Point, mapping, shape
from shapely import affinity
from shapely.ops import unary_union, voronoi_diagram

ROOT = Path(__file__).resolve().parent.parent
UA = {"User-Agent": "Bostadsplan/1.0 (personal apartment planner)"}

# Stockholm's districts inside the tolls, and the plain name each is grouped under in the planner
DISTRICTS = {
    "Södermalm": "Södermalm", "Långholmen": "Södermalm", "Reimersholme": "Södermalm",
    "Gamla stan": "Gamla stan", "Riddarholmen": "Gamla stan",
    "Kungsholmen": "Kungsholmen", "Marieberg": "Kungsholmen", "Fredhäll": "Kungsholmen",
    "Stadshagen": "Kungsholmen", "Kristineberg": "Kungsholmen",
    "Vasastaden": "Vasastan", "Norrmalm": "Norrmalm", "Skeppsholmen": "Norrmalm",
    "Östermalm": "Östermalm", "Ladugårdsgärdet": "Gärdet",
}
# Neighbourhood names to place, beyond those the finder has already met
EXTRA_NEIGHBOURHOODS = [
    "SoFo", "Mariatorget", "Mariaberget", "Högalid", "Tanto", "Slussen", "Medborgarplatsen", "Skanstull",
    "Zinkensdamm", "Hornstull", "Danvikstull", "Bergsund", "Sibirien", "Birkastaden", "Röda bergen", "Rörstrand",
    "Atlasområdet", "Sabbatsberg", "Hagastaden", "Torsplan", "Odenplan", "Lärkstaden", "Villastaden", "Eriksberg",
    "Gärdeshöjden", "Hornsberg", "Rålambshov", "Fridhemsplan",
]
REACH_M = 500   # how far a neighbourhood reaches from its point
last = 0.0


def nominatim(q, **extra):
    global last
    time.sleep(max(0, 1.1 - (time.time() - last)))
    params = {"q": f"{q}, Stockholm", "format": "jsonv2", "limit": 5, "countrycodes": "se", **extra}
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
        last = time.time()
        return json.load(r)


def district(name):
    for h in nominatim(name, polygon_geojson=1):
        if h["category"] == "boundary" and h["type"] == "administrative" and h["geojson"]["type"] in ("Polygon", "MultiPolygon"):
            return shape(h["geojson"])
    return None


def neighbourhood(name):
    for h in nominatim(name):
        if h["category"] == "place" and h["type"] in ("neighbourhood", "quarter"):
            return Point(float(h["lon"]), float(h["lat"]))
    return None


def main():
    districts = {}
    for name, group in DISTRICTS.items():
        poly = district(name)
        if poly is None:
            print(f"No boundary for {name}")
            continue
        districts[name] = (poly.buffer(0), group)
        print(f"District {name}: {poly.geom_type}")
    seen = set()
    try:
        cache = json.loads((ROOT / "data" / "geocode.json").read_text("utf-8"))
        seen = {v["area"] for v in cache.values() if v and v.get("inner") and v.get("area")}
    except (OSError, ValueError):
        pass
    points = {}
    for name in sorted((seen | set(EXTRA_NEIGHBOURHOODS)) - set(DISTRICTS)):
        pt = neighbourhood(name)
        if pt is not None:
            points[name] = pt
    print(f"{len(points)} neighbourhood points")

    features = []
    for dname, (poly, group) in districts.items():
        inside = {n: p for n, p in points.items() if poly.contains(p)}
        cells = {}
        if inside:
            if len(inside) == 1:
                regions = {next(iter(inside)): poly}
            else:
                diagram = voronoi_diagram(MultiPoint(list(inside.values())), envelope=poly.envelope.buffer(0.02))
                regions = {n: c for c in diagram.geoms for n, p in inside.items() if c.contains(p)}
            for n, region in regions.items():
                cells[n] = region.intersection(reach(inside[n])).intersection(poly)
        rest = poly.difference(unary_union(list(cells.values()))) if cells else poly
        if rest.area > poly.area * 0.02:  # the part no neighbourhood reaches keeps the district's name
            cells[dname] = unary_union([rest, cells.get(dname, rest)])
        for n, geom in cells.items():
            geom = geom.simplify(0.00006, preserve_topology=True)
            features.append({"type": "Feature", "properties": {"kind": "area", "name": n, "district": dname, "group": group},
                             "geometry": round_coords(mapping(geom))})
        outline = poly.simplify(0.00006, preserve_topology=True)
        features.append({"type": "Feature", "properties": {"kind": "district", "name": dname, "group": group},
                         "geometry": round_coords(mapping(outline))})
    out = {"type": "FeatureCollection", "attribution": "Boundaries © OpenStreetMap contributors (ODbL)", "features": features}
    (ROOT / "areas.geojson").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), "utf-8")
    print(f"Wrote areas.geojson: {len(districts)} districts, {sum(f['properties']['kind'] == 'area' for f in features)} areas")


def reach(pt):
    """A circle of REACH_M metres around a point, stretched for Stockholm's latitude."""
    r = REACH_M / 111320
    return affinity.scale(pt.buffer(r, 32), xfact=1 / 0.511, yfact=1, origin=pt)  # cos(59.33°) ≈ 0.511


def round_coords(g):
    def r(c):
        return [round(c[0], 5), round(c[1], 5)] if isinstance(c[0], (int, float)) else [r(x) for x in c]
    return {"type": g["type"], "coordinates": r(g["coordinates"])}


if __name__ == "__main__":
    main()
