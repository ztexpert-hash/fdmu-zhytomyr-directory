# -*- coding: utf-8 -*-
"""Configurable valuation on top of the v11.10 site replica.

Baseline (rooms as a hard filter, 12 months, multiplier 1.24, floor only in
the score) goes through fdmu_calc.compute_valuation unchanged. Later stages
use valuate(), which keeps the same filters, score, auto-selection and IQR
trim and only changes the switches named in the stage.
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime

import fdmu_calc as fc

# Form options already on the site. Not tuned on the certificates.
PERIOD_LADDER = (1, 2, 3, 4, 5, 6, 12)
MIN_COMPS = 5
# 400 m, then five expansions of +20%.
GEO_RADII_M = tuple(400.0 * (1.2 ** i) for i in range(6))
FIRST_FLOOR_COEF = 0.8646

_STREET_CACHE: dict[str, str] = {}
_STREET_CORE_ORIG = fc.street_core


def _street_core(value) -> str:
    key = value if isinstance(value, str) else str(value or "")
    hit = _STREET_CACHE.get(key)
    if hit is None:
        hit = _STREET_CORE_ORIG(value)
        _STREET_CACHE[key] = hit
    return hit


# build_candidates and compute_valuation look up street_core on the module.
fc.street_core = _street_core


def load_records(path: str):
    """Load registry rows. The appraiser field is dropped immediately."""
    data = fc._load_raw_json(fc.Path(path))
    records = []
    for raw in data.get("records") or []:
        raw.pop("вик", None)
        for extra in ("виконавець", "executor", "edrpou", "рнкпп", "рнокпп"):
            raw.pop(extra, None)
        if not fc.valid_city(raw):
            continue
        rec = fc.city_zone_record(raw)
        rec["_stCore"] = _street_core(rec.get("ад"))
        rec["_geoKey"] = geo_key(rec.get("ад"))
        records.append(rec)
    return records


def _parse_as_of(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    parsed = fc.parse_date_value(value)
    if not parsed:
        raise ValueError(f"bad date: {value!r}")
    return parsed


def target_from_case(case: dict, months: str) -> dict:
    def num(key):
        raw = case.get(key)
        if raw is None or str(raw).strip() == "":
            return None
        return float(str(raw).replace(",", "."))

    def whole(key):
        value = num(key)
        if value is None:
            return None
        return int(value)

    year = whole("year_num")
    return {
        "objectType": "apartment",
        "district": "all",
        "rooms": whole("rooms_num"),
        "area": num("area_m2"),
        "street": (case.get("street") or "").strip() or None,
        "house": (case.get("house") or "").strip() or None,
        "year": year,
        "floor": whole("floor_num"),
        "totalFloors": whole("floors_num"),
        "dataScope": str(months),
        "datePeriod": str(months),
        "oliivkaPickMode": "similar",
        "lat": num("lat"),
        "lon": num("lon"),
    }


def _pack(cands, t):
    selected = fc.recommended_auto_selection(cands, t)
    trimmed = fc.trim_by_ppm(selected)
    return selected, trimmed["used"], trimmed


def build_candidates(source, t, as_of: date, *, rooms_hard: bool, rooms_penalty: float, floor_score: bool):
    base = [r for r in source if fc.object_type_ok(r, t) and fc.date_period_ok(r, t, as_of)]
    if t.get("district") != "all":
        base = [r for r in base if fc.district_match(r, t["district"])]
    if rooms_hard and t.get("objectType") != "house":
        rooms = t.get("rooms")
        base = [r for r in base if fc.js_num(r.get("к")) == rooms]
    if t.get("area"):
        base = [r for r in base if fc.area_ok(r, t)]
    t["_streetCore"] = _street_core(t["street"]) if t.get("street") else None
    out = []
    for i, r in enumerate(base):
        rr = dict(r)
        rr["_stCore"] = r.get("_stCore") or _street_core(r.get("ад"))
        rr["_s"] = fc.similarity(
            rr, t, rooms_penalty=rooms_penalty, floor_score=floor_score
        )
        out.append(rr)
    out.sort(key=lambda x: -x["_s"])
    return out


def _equirect(lat, lon, lat0):
    return (
        (lon) * math.cos(math.radians(lat0)) * 111_320.0,
        lat * 110_540.0,
    )


def _point_segment_m(plat, plon, a, b):
    ax, ay = _equirect(a[0], a[1], plat)
    bx, by = _equirect(b[0], b[1], plat)
    px, py = _equirect(plat, plon, plat)
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    if denom <= 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _point_lines_m(plat, plon, lines):
    best = None
    for line in lines:
        if len(line) == 1:
            d = _point_segment_m(plat, plon, line[0], line[0])
            best = d if best is None else min(best, d)
            continue
        for i in range(len(line) - 1):
            d = _point_segment_m(plat, plon, line[i], line[i + 1])
            if best is None or d < best:
                best = d
    return best


_TYPE_RE = {
    "ПРОСП": re.compile(r"(^|\s)(ПРОСПЕКТ|ПРОСП|ПР-Т)\.?(?=\s|$)"),
    "БУЛ": re.compile(r"(^|\s)(БУЛЬВАР|БУЛ)\.?(?=\s|$)"),
    "ПЛ": re.compile(r"(^|\s)(ПЛОЩА|ПЛ)\.?(?=\s|$)"),
    "ПРОЇЗД": re.compile(r"(^|\s)ПРОЇЗД(?=\s|$)"),
    "НАБ": re.compile(r"(^|\s)(НАБЕРЕЖНА|НАБ)\.?(?=\s|$)"),
}


def geo_key(value) -> str:
    """Street key that keeps проспект / вулиця / провулок apart.

    street_core folds 'просп. Миру' and 'вул. Миру' into one token. That is
    fine for the similarity bonus and wrong for a coordinate: house 1 on
    those two streets is more than a kilometre apart.
    """
    raw = str(value or "").upper()
    core = _street_core(value)
    if not core:
        return ""
    if core.startswith("ПРОВ~"):
        return core
    for prefix, pattern in _TYPE_RE.items():
        if pattern.search(raw):
            return f"{prefix}~{core}"
    return f"ВУЛ~{core}"


def norm_house(value) -> str:
    s = str(value or "").upper().replace(" ", "")
    s = s.replace("А", "A").replace("І", "I").replace("Ї", "I")
    s = s.replace("–", "-").replace("—", "-").replace("−", "-")
    return s


def house_keys(value):
    """Variants so '151-А', '151А' and '4-а' can meet OSM '151а' / '4'."""
    raw = norm_house(value)
    if not raw:
        return []
    keys = [raw]
    flat = raw.replace("-", "")
    if flat not in keys:
        keys.append(flat)
    head = raw.split("/")[0]
    if head and head not in keys:
        keys.append(head)
    head_flat = head.replace("-", "")
    if head_flat and head_flat not in keys:
        keys.append(head_flat)
    digits = "".join(ch for ch in raw if ch.isdigit())
    # Digits-only is a last resort and is intentionally not returned when the
    # house has a letter or a fraction: '38/17' must not match house '38'.
    if digits and digits == flat and digits not in keys:
        keys.append(digits)
    return keys


def _length_centroid(lines):
    sx = sy = sl = 0.0
    for line in lines:
        if len(line) == 1:
            sx += line[0][0]
            sy += line[0][1]
            sl += 1.0
            continue
        for i in range(len(line) - 1):
            a, b = line[i], line[i + 1]
            mid_lat = (a[0] + b[0]) / 2
            mid_lon = (a[1] + b[1]) / 2
            dist = _point_segment_m(a[0], a[1], a, b)
            if dist <= 0:
                continue
            sx += mid_lat * dist
            sy += mid_lon * dist
            sl += dist
    if sl <= 0:
        return None
    return (sx / sl, sy / sl)


def _leading_int(house_key):
    match = re.match(r"(\d+)", str(house_key or ""))
    return int(match.group(1)) if match else None


def _building_fallback(houses, house):
    """Exact house missed. Use a nearby number, else the buildings' centroid."""
    want = _leading_int(norm_house(house).replace("-", ""))
    numbered = []
    points = []
    for key, loc in houses.items():
        pt = (float(loc[0]), float(loc[1]))
        points.append(pt)
        num = _leading_int(key)
        if num is not None:
            numbered.append((num, pt, key))
    if want is not None and numbered:
        _num, pt, _key = min(numbered, key=lambda item: (abs(item[0] - want), item[0], item[2]))
        if abs(_num - want) <= 4:
            return pt, "nearby-house"
    if not points:
        return None, "unmatched"
    lat = sum(p[0] for p in points) / len(points)
    lon = sum(p[1] for p in points) / len(points)
    return (lat, lon), "street-buildings"


def subject_point(t, geo):
    """Point the static site can resolve without a network call.

    Exact building, then a nearby house number on the same street, then the
    street geometry. Certificate lat/lon are not the subject point.
    """
    if not geo:
        return None, "off"
    key = geo_key(t.get("street"))
    buildings = (geo.get("buildings") or {}).get(key) or {}
    for house_key in house_keys(t.get("house")):
        hit = buildings.get(house_key)
        if hit:
            return (float(hit[0]), float(hit[1])), "building"
    if buildings:
        point, how = _building_fallback(buildings, t.get("house"))
        if point:
            return point, how
    lines = (geo.get("streets") or {}).get(key)
    if lines:
        cen = _length_centroid(lines)
        if cen:
            return cen, "street"
    return None, "unmatched"


def _distances(cands, point, geo):
    streets = geo.get("streets") or {}
    cache = {}
    for r in cands:
        key = r.get("_geoKey") or geo_key(r.get("ад"))
        r["_geoKey"] = key
        if key in cache:
            continue
        lines = streets.get(key)
        cache[key] = _point_lines_m(point[0], point[1], lines) if lines else None
    return cache


def _choose_geo_pool(cands, t, dist, min_comps):
    for radius in GEO_RADII_M:
        pool = [
            r
            for r in cands
            if dist.get(r.get("_geoKey")) is not None and dist[r["_geoKey"]] <= radius
        ]
        _selected, used, _trimmed = _pack(pool, t)
        if len(used) >= min_comps:
            return pool, radius
    return None, None


def _price(arr, t, multiplier, apply_floor_coef):
    wsum = sum((r.get("_s") or 1) for r in arr)
    wppm = sum((r.get("_s") or 1) * (fc.js_num(r.get("цкв")) or 0) for r in arr) / wsum
    mppm = fc.median([fc.js_num(r.get("цкв")) or 0 for r in arr])
    skew = abs(wppm - mppm) / mppm if mppm > 0 else 0
    used_median = skew > 0.08
    base_ppm = mppm if used_median else wppm
    total = base_ppm * t["area"]
    coef = FIRST_FLOOR_COEF if apply_floor_coef and t.get("floor") == 1 else 1.0
    return {
        "total": total,
        "fund": total * multiplier * coef,
        "base_ppm": base_ppm,
        "coef": coef,
        "used_median": used_median,
    }


def valuate_baseline(records, t, as_of):
    """Original site path: hard room filter, fixed period, x1.24, no floor coef."""
    res = fc.compute_valuation(records, records, t, as_of)
    if res.get("error"):
        return {
            "ok": False,
            "error": res["error"],
            "cand": res.get("cand") or 0,
            "used": 0,
            "scope": t.get("dataScope"),
            "radius_m": None,
            "geo": "off",
            "fund": None,
            "total": None,
        }
    return {
        "ok": True,
        "fund": res["totalFund"],
        "total": res["total"],
        "used": res["used"],
        "cand": res["cand"],
        "scope": t.get("dataScope"),
        "radius_m": None,
        "geo": "off",
        "coef": 1.0,
    }


def valuate(records, case, cfg, geo=None):
    """One certificate.

    cfg keys:
      months: starting period ('12' or '1')
      rooms_hard: bool
      rooms_penalty: float, subtracted from the score when room class differs
      auto_period: extend the period until MIN_COMPS survive the trim
      floor_score: keep the v11.10 floor penalties in the score
      floor_coef: multiply the forecast by 0.8646 on the first floor
      multiplier: forecast / registry
      geo: restrict comparables to an expanding radius
    """
    as_of = _parse_as_of(case["date"])
    months = str(cfg.get("months") or "12")
    t0 = target_from_case(case, months)
    if cfg.get("baseline_path"):
        return valuate_baseline(records, t0, as_of)

    rooms_hard = bool(cfg.get("rooms_hard", True))
    rooms_penalty = float(cfg.get("rooms_penalty") or 0)
    floor_score = bool(cfg.get("floor_score", True))
    auto_period = bool(cfg.get("auto_period"))
    use_geo = bool(cfg.get("geo"))
    multiplier = float(cfg["multiplier"])
    apply_floor = bool(cfg.get("floor_coef"))
    min_comps = int(cfg.get("min_comps") or MIN_COMPS)

    if months == "archive":
        ladder = ("archive",)
    elif auto_period:
        start = int(months)
        ladder = tuple(str(m) for m in PERIOD_LADDER if m >= start)
    else:
        ladder = (months,)

    point, geo_how = (None, "off")
    if use_geo:
        point, geo_how = subject_point(t0, geo or {})

    chosen = None
    for scope in ladder:
        t = dict(t0)
        t["dataScope"] = scope
        t["datePeriod"] = "all" if scope == "archive" else scope
        cands = build_candidates(
            records,
            t,
            as_of,
            rooms_hard=rooms_hard,
            rooms_penalty=rooms_penalty,
            floor_score=floor_score,
        )
        radius = None
        pool = cands
        if use_geo and point is not None and scope != "archive":
            dist = _distances(cands, point, geo or {})
            geo_pool, radius = _choose_geo_pool(cands, t, dist, min_comps)
            if geo_pool is not None:
                pool = geo_pool
            else:
                # Radius steps did not yield 5 comparables. Keep looking in a
                # longer period before giving up and using the whole city.
                _selected, used, _trimmed = _pack(cands, t)
                chosen = (scope, cands, used, None, geo_how, t)
                if len(used) >= min_comps and scope == ladder[-1]:
                    pool = cands
                    radius = None
                else:
                    if scope != ladder[-1]:
                        continue
                    pool = cands
                    radius = None
        selected, used, _trimmed = _pack(pool, t)
        chosen = (scope, pool, used, radius, geo_how if use_geo else "off", t)
        if len(used) >= min_comps or scope == ladder[-1] or scope == "archive":
            break
        if not auto_period:
            break

    scope, pool, used, radius, geo_how, t = chosen
    if not used:
        return {
            "ok": False,
            "error": "no comparables",
            "cand": len(pool),
            "used": 0,
            "scope": scope,
            "radius_m": radius,
            "geo": geo_how,
            "fund": None,
            "total": None,
        }
    priced = _price(used, t, multiplier, apply_floor)
    return {
        "ok": True,
        "fund": priced["fund"],
        "total": priced["total"],
        "base_ppm": priced["base_ppm"],
        "coef": priced["coef"],
        "used": len(used),
        "cand": len(pool),
        "scope": scope,
        "radius_m": radius,
        "geo": geo_how,
    }
