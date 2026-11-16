# -*- coding: utf-8 -*-
"""Build a static Zhytomyr gazetteer from an Overpass JSON dump.

Keeps street geometry and building coordinates only. Names of businesses,
phones and websites from the dump are not written.

Usage:
  python3 eval/build_geo.py \
    --streets /tmp/geo/streets.json \
    --buildings /tmp/geo/buildings.json \
    --out-dir eval/geo
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import model  # noqa: E402


def _dist_m(a, b):
    lat = (a[0] + b[0]) / 2
    x = (a[1] - b[1]) * math.cos(math.radians(lat)) * 111_320.0
    y = (a[0] - b[0]) * 110_540.0
    return math.hypot(x, y)


def _simplify(points, min_m=35.0):
    if len(points) <= 2:
        return points
    kept = [points[0]]
    for pt in points[1:-1]:
        if _dist_m(kept[-1], pt) >= min_m:
            kept.append(pt)
    if _dist_m(kept[-1], points[-1]) < min_m / 2 and len(kept) > 1:
        kept[-1] = points[-1]
    else:
        kept.append(points[-1])
    return kept


def _way_points(el):
    geom = el.get("geometry") or []
    pts = []
    for g in geom:
        if "lat" not in g or "lon" not in g:
            continue
        pts.append((round(float(g["lat"]), 5), round(float(g["lon"]), 5)))
    return _simplify(pts)


def _cores_from_tags(tags):
    cores = []
    for key in ("name", "name:uk"):
        value = tags.get(key)
        if not value:
            continue
        core = model.geo_key(value)
        if core and core not in cores:
            cores.append(core)
    return cores


def _old_cores(tags):
    cores = []
    for key in ("old_name", "old_name:uk"):
        value = tags.get(key)
        if not value:
            continue
        core = model.geo_key(value)
        if core and core not in cores:
            cores.append(core)
    return cores


def build_streets(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    lines = defaultdict(list)
    current = set()
    old_alias = defaultdict(list)
    for el in data.get("elements") or []:
        tags = el.get("tags") or {}
        pts = _way_points(el)
        if len(pts) < 2:
            continue
        cores = _cores_from_tags(tags)
        for core in cores:
            lines[core].append(pts)
            current.add(core)
        for core in _old_cores(tags):
            if core not in cores:
                old_alias[core].append(pts)
    for core, extra in old_alias.items():
        if core not in current:
            lines[core].extend(extra)
    return {core: geom for core, geom in lines.items()}


def _element_latlon(el):
    if "lat" in el and "lon" in el:
        return float(el["lat"]), float(el["lon"])
    center = el.get("center") or {}
    if "lat" in center and "lon" in center:
        return float(center["lat"]), float(center["lon"])
    return None


def build_buildings(path: Path):
    """street core -> house key -> [lat, lon]. Coordinates only."""
    data = json.loads(path.read_text(encoding="utf-8"))
    acc = defaultdict(lambda: defaultdict(list))
    for el in data.get("elements") or []:
        tags = el.get("tags") or {}
        street = tags.get("addr:street")
        house = tags.get("addr:housenumber")
        loc = _element_latlon(el)
        if not street or not house or not loc:
            continue
        core = model.geo_key(street)
        if not core:
            continue
        for key in model.house_keys(house):
            acc[core][key].append(loc)
    out = {}
    for core, houses in acc.items():
        packed = {}
        for key, pts in houses.items():
            lats = sorted(p[0] for p in pts)
            lons = sorted(p[1] for p in pts)
            mid = len(pts) // 2
            # Median resists a stray duplicate tag better than the mean when
            # the same house number was entered twice far apart.
            lat = round(lats[mid], 5)
            lon = round(lons[mid], 5)
            packed[key] = [lat, lon]
        out[core] = packed
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--streets", required=True)
    parser.add_argument("--buildings", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--records", default="calc_data.json")
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    streets = build_streets(Path(args.streets))
    buildings = build_buildings(Path(args.buildings))
    meta = {
        "source": "OpenStreetMap contributors, ODbL-1.0",
        "via": "Overpass API, bounding box 50.210,28.560,50.330,28.780",
        "fields": "street geometry and building coordinates only",
    }
    street_path = out_dir / "streets.json"
    building_path = out_dir / "buildings.json"
    street_path.write_text(
        json.dumps({"meta": meta, "streets": streets}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    building_path.write_text(
        json.dumps({"meta": meta, "buildings": buildings}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"streets {len(streets)} bytes {street_path.stat().st_size}")
    print(f"buildings {sum(len(v) for v in buildings.values())} bytes {building_path.stat().st_size}")

    # Match rate against the registry. No appraiser field is read out.
    raw = json.loads(Path(args.records).read_text(encoding="utf-8"))
    cores = []
    for rec in raw.get("records") or []:
        rec.pop("вик", None)
        cores.append(model.geo_key(rec.get("ад")))
    known = [c for c in cores if c in streets]
    print(f"registry rows {len(cores)} with street geometry {len(known)} ({len(known)/len(cores):.1%})")
    uniq = set(cores)
    print(f"unique street cores {len(uniq)} matched {sum(c in streets for c in uniq)}")


if __name__ == "__main__":
    main()
