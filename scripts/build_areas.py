"""Builds areas.geojson: the inner city's districts and neighbourhoods, for the planner's map.

Two levels:
  district       Södermalm, Kungsholmen, Vasastan, Östermalm, Norrmalm, Gamla stan, Gärdet.
                 Their outlines are Stockholm's official stadsdel boundaries from OpenStreetMap,
                 with the water cut out, so they follow the shore.
  neighbourhood  SoFo, Mariatorget, Hornstull, Fridhemsplan, Sibirien… These have no official
                 borders. Each is given a centre at a place that defines it (Nytorget for SoFo,
                 the square for Mariatorget), looked up on OpenStreetMap, and each district is
                 split between its centres: every spot belongs to the nearest one.

The finder names each apartment by the neighbourhood it lies in, so the map and the cards agree.
Run it again when the list of neighbourhoods changes; it asks Nominatim about 90 questions, one
a second.

    .venv/bin/python scripts/build_areas.py
"""
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

from shapely import affinity
from shapely.geometry import MultiPoint, Point, mapping, shape
from shapely.ops import unary_union, voronoi_diagram

ROOT = Path(__file__).resolve().parent.parent
UA = {"User-Agent": "Bostadsplan/1.0 (personal apartment planner)"}
BOX = (17.97, 59.30, 18.13, 59.36)  # lon/lat box around the inner city; anything found outside it is wrong

# District -> the official stadsdelar it is made of
DISTRICTS = {
    "Södermalm": ["Södermalm", "Långholmen", "Reimersholme"],
    "Gamla stan": ["Gamla stan", "Riddarholmen"],
    "Kungsholmen": ["Kungsholmen", "Marieberg", "Fredhäll", "Stadshagen", "Kristineberg"],
    "Vasastan": ["Vasastaden"],
    "Norrmalm": ["Norrmalm", "Skeppsholmen"],
    "Östermalm": ["Östermalm"],
    "Gärdet": ["Ladugårdsgärdet"],
}
# District -> its neighbourhoods, as (name, the place on OpenStreetMap that marks its centre)
NEIGHBOURHOODS = {
    "Södermalm": [
        ("Hornstull", "Hornstull"), ("Högalid", "Högalidskyrkan"), ("Zinkensdamm", "Zinkensdamm"),
        ("Tanto", "Tantolunden"), ("Mariatorget", "Mariatorget"), ("Mariaberget", "Mariaberget"),
        ("Slussen", "Slussen"), ("Medborgarplatsen", "Medborgarplatsen"), ("Katarina", "Katarina kyrka"),
        ("SoFo", "Nytorget"), ("Sofia", "Sofia kyrka"), ("Skanstull", "Skanstull"), ("Danvikstull", "Danvikstull"),
        ("Bergsund", "Bergsunds strand"), ("Långholmen", "Långholmen"), ("Reimersholme", "Reimersholme"),
    ],
    "Kungsholmen": [
        ("Fridhemsplan", "Fridhemsplan"), ("Rålambshov", "Rålambshovsparken"), ("Thorildsplan", "Thorildsplan"),
        ("Kungsholms strand", "Kungsholms strand"), ("Kungsholmstorg", "Kungsholmstorg"), ("Rådhuset", "Rådhusets tunnelbanestation"),
        ("Stadshagen", "Stadshagen"), ("Hornsberg", "Hornsberg"), ("Kristineberg", "Kristineberg"),
        ("Fredhäll", "Fredhäll"), ("Marieberg", "Marieberg"),
    ],
    "Vasastan": [
        ("Odenplan", "Odenplan"), ("Sibirien", "Sibirien"), ("Birkastan", "Birkastaden"), ("Röda bergen", "Röda bergen"),
        ("Rörstrand", "Rörstrand"), ("Atlasområdet", "Atlasområdet"), ("Sabbatsberg", "Sabbatsberg"),
        ("Hagastaden", "Hagastaden"), ("Torsplan", "Torsplan"), ("Vasaparken", "Vasaparken"),
        ("Observatorielunden", "Observatorielunden"), ("Tegnérlunden", "Tegnérlunden"),
    ],
    "Östermalm": [
        ("Stureplan", "Stureplan"), ("Östermalmstorg", "Östermalmstorg"), ("Karlaplan", "Karlaplan"),
        ("Lärkstaden", "Lärkstaden"), ("Villastaden", "Villastaden"), ("Eriksberg", "Eriksberg"),
        ("Strandvägen", "Strandvägen"), ("Diplomatstaden", "Diplomatstaden"),
    ],
    "Norrmalm": [
        ("Hötorget", "Hötorget"), ("Klara", "Klara kyrka"), ("Norrmalmstorg", "Norrmalmstorg"),
        ("Kungsträdgården", "Kungsträdgården"), ("Skeppsholmen", "Skeppsholmen"),
    ],
    "Gamla stan": [("Gamla stan", "Stortorget"), ("Riddarholmen", "Riddarholmen")],
    "Gärdet": [("Gärdeshöjden", "Gärdeshöjden"), ("Tessinparken", "Tessinparken"), ("Ladugårdsgärdet", "Ladugårdsgärdet")],
}
# Bays, lakes and channels to cut out, so areas follow the shore
WATER = ["Riddarfjärden", "Strömmen", "Saltsjön", "Årstaviken", "Hammarby sjö", "Barnhusviken", "Klara sjö",
         "Karlbergssjön", "Ulvsundasjön", "Mälaren", "Nybroviken", "Ladugårdslandsviken", "Djurgårdsbrunnsviken",
         "Brunnsviken", "Långholmskanalen", "Pålsundet", "Fiskarhamnen", "Lilla Värtan"]
last = 0.0


def nominatim(q, **extra):
    global last
    time.sleep(max(0, 1.1 - (time.time() - last)))
    params = {"q": f"{q}, Stockholm", "format": "jsonv2", "limit": 5, "countrycodes": "se", **extra}
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        last = time.time()
        return json.load(r)


def in_box(lon, lat):
    return BOX[0] <= lon <= BOX[2] and BOX[1] <= lat <= BOX[3]


def stadsdel(name):
    for h in nominatim(name, polygon_geojson=1):
        if h["category"] == "boundary" and h["type"] == "administrative" and h["geojson"]["type"] in ("Polygon", "MultiPolygon"):
            return shape(h["geojson"]).buffer(0)
    return None


def centre(query):
    """The place's point: OpenStreetMap's own neighbourhood point when there is one (the search
    often lists a station or street of the same name first), otherwise the first hit."""
    hits = [h for h in nominatim(query) if in_box(float(h["lon"]), float(h["lat"]))]
    hits.sort(key=lambda h: h["category"] != "place")
    return Point(float(hits[0]["lon"]), float(hits[0]["lat"])) if hits else None


# Nearest is measured in metres: a degree east-west is about half as long as one north-south here
EAST = 0.511  # cos(59.33°)


def metric(g):
    return affinity.scale(g, xfact=EAST, yfact=1, origin=(0, 0))


def degrees(g):
    return affinity.scale(g, xfact=1 / EAST, yfact=1, origin=(0, 0))


def water(name):
    for h in nominatim(name, polygon_geojson=1, polygon_threshold=0.00005):
        if h["geojson"]["type"] in ("Polygon", "MultiPolygon") and h["category"] in ("natural", "water", "waterway", "place"):
            g = shape(h["geojson"]).buffer(0)
            if g.intersects(Point((BOX[0] + BOX[2]) / 2, (BOX[1] + BOX[3]) / 2).buffer(0.1)):
                return g
    return None


def main():
    wet = [w for n in WATER if (w := water(n)) is not None]
    sea = unary_union(wet) if wet else None
    print(f"{len(wet)} bodies of water to cut out")
    features = []
    for district, parts in DISTRICTS.items():
        polys = [p for n in parts if (p := stadsdel(n)) is not None]
        if not polys:
            print(f"No boundary for {district}")
            continue
        land = unary_union(polys)
        if sea is not None:
            land = land.difference(sea).buffer(0)
        land = unary_union([g for g in getattr(land, "geoms", [land]) if g.area > 2e-7])  # drop slivers of shore
        pts = {}
        for name, query in NEIGHBOURHOODS.get(district, []):
            pt = centre(query)
            if pt is None:
                print(f"  {district}: no place found for {name} ({query})")
            elif land.buffer(0.002).contains(pt):
                pts[name] = pt
            else:
                print(f"  {district}: {name} lies outside the district, left out")
        if len(pts) < 2:
            cells = {(next(iter(pts)) if pts else district): land}
        else:
            diagram = voronoi_diagram(MultiPoint([metric(p) for p in pts.values()]), envelope=metric(land.envelope.buffer(0.02)))
            cells = {n: degrees(c).intersection(land) for c in diagram.geoms for n, p in pts.items() if c.contains(metric(p))}
        for name, geom in cells.items():
            if geom.is_empty:
                continue
            features.append({"type": "Feature", "properties": {"kind": "area", "name": name, "district": district, "group": district},
                             "geometry": round_coords(mapping(geom.simplify(0.00004, preserve_topology=True)))})
        features.append({"type": "Feature", "properties": {"kind": "district", "name": district, "group": district},
                         "geometry": round_coords(mapping(land.simplify(0.00004, preserve_topology=True)))})
        print(f"{district}: {len(cells)} neighbourhoods")
    out = {"type": "FeatureCollection", "attribution": "Boundaries and places © OpenStreetMap contributors (ODbL)", "features": features}
    (ROOT / "areas.geojson").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), "utf-8")
    print(f"Wrote areas.geojson: {sum(f['properties']['kind'] == 'area' for f in features)} neighbourhoods")


def round_coords(g):
    def r(c):
        return [round(c[0], 5), round(c[1], 5)] if isinstance(c[0], (int, float)) else [r(x) for x in c]
    return {"type": g["type"], "coordinates": r(g["coordinates"])}


if __name__ == "__main__":
    main()
