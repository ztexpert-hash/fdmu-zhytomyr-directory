#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Калькулятор вартості за алгоритмом довідника
«Довідник зареєстрованих оцінок ФДМУ - Житомир», сайт v11.10.

Відтворює функції з fdmu.html: buildCandidates, recommendedAutoSelection,
trimByPpm, recalcSelected. Стандартна бібліотека Python, без сторонніх пакетів.

Поле «вик» (ПІБ оцінювача і РНОКПП) ніколи не зберігається і не виводиться.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import urllib.request
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path

DIR = Path(__file__).resolve().parent
DATA_DIR = DIR / "data"
BASE_URL = "https://ztexpert-hash.github.io/fdmu-zhytomyr-directory"
FALLBACK_SOURCES = [
    Path("/tmp/calc_data.json"),
    Path("/tmp/archive_calc_data.json"),
]

APP_VERSION = "11.10"
USD_RATE = 41.5  # довідковий курс у додатку, не курс НБУ
FUND_MULTIPLIER = 1.24

ALLOWED = frozenset(
    ["Богунський", "Корольовський", "Житомир (без району)", "Оліївка новобудови"]
)
BLACKLIST = [
    "БЕРДИЧ",
    "НОВОГРАД",
    "ЗВЯГ",
    "КОРОСТ",
    "МАЛИН",
    "РАЙОН",
    "АНДРУШ",
    "ЧУДНІВ",
    "ОВРУЧ",
]
_ROMAN = {"І": "1", "ІІ": "2", "ІІІ": "3", "ІV": "4", "IV": "4", "V": "5"}
_WORDNUM = {
    "ПЕРШИЙ": "1",
    "ДРУГИЙ": "2",
    "ТРЕТІЙ": "3",
    "ЧЕТВЕРТИЙ": "4",
    "ПЯТИЙ": "5",
    "ШОСТИЙ": "6",
    "СЬОМИЙ": "7",
    "ВОСЬМИЙ": "8",
}
_TITLES = frozenset(
    [
        "ГЕНЕРАЛА",
        "АКАДЕМІКА",
        "ПРОФЕСОРА",
        "МИТРОПОЛИТА",
        "ІМЕНІ",
        "ОТАМАНА",
        "ОТАМАНІВ",
        "ГЕТЬМАНА",
        "АДМІРАЛА",
        "КНЯЗІВ",
        "КНЯЗЯ",
    ]
)
PII_FIELDS = ("вик", "виконавець", "executor", "edrpou", "рнкпп", "рнокпп")

_ADDR_TYPE_RE = re.compile(
    r"(^|\s)(ВУЛ\.?|ВУЛИЦЯ|ПРОСП\.?|ПРОСПЕКТ|ПР-Т|ПЛ\.?|ПЛОЩА|ПРОВ\.?|ПРОВУЛОК|БУЛ\.?|БУЛЬВАР|ПРОЇЗД)(?=\s|$)",
    re.IGNORECASE,
)
_PUNCT_RE = re.compile(r"""[.,;:()\[\]«»"'`´/]+""")
_LANE_RE = re.compile(r"(^|\s)(ПРОВУЛОК|ПРОВ)\.?(?=\s|$)")
_SPACED_LETTERS_RE = re.compile(
    r"(?:(?<=\s)|^)(?:[А-ЯҐЄІЇ]\s+){2,}[А-ЯҐЄІЇ](?=\s|$)"
)
_HOUSE_UNIT_RE = re.compile(
    r"(^|\s)(КВ|КВАРТ|КІМ|КІМНАТА|БУД|БУДИНОК)\.?\s*\d+[А-ЯҐЄІЇ]?"
)
_INITIAL_DOT_RE = re.compile(r"(^|\s)[А-ЯҐЄІЇ]\.\s*")
_LONE_LETTER_RE = re.compile(r"(^|\s)([А-ЯҐЄІЇ])(?=\s|$)")
_ROMAN_RE = re.compile(r"(^|\s)(І{2,3}|ІV|V)(?:-?[ЙИЄ]{1,2})?(?=\s|$)")
_ORDINAL_RE = re.compile(r"(\d+)\s*-\s*(?:[ЙИЄ]{1,2})?(?=\s|$)")
_NUM_LETTER_RE = re.compile(r"\d+[А-ЯҐЄІЇ]")
_NON_ALNUM_RE = re.compile(r"[^А-ЯҐЄІЇA-Z0-9\s]")
_WORDNUM_RES = [
    (re.compile(r"(^|\s)" + w + r"(?=\s|$)"), " " + n + " ")
    for w, n in _WORDNUM.items()
]

_STREET_REPLACEMENTS = [
    (re.compile(p), r)
    for p, r in [
        (r"ГРУШЕВСЬКОГО МИХАЙЛА|МИХАЙЛА ГРУШЕВСЬКОГО|ГРУШЕВСЬКОГО", "ГРУШЕВСЬКОГО"),
        (r"МАЗЕПИ ІВАНА|ІВАНА МАЗЕПИ", "МАЗЕПИ"),
        (r"ЛЕСІ УКРАЇНКИ|УКРАЇНКИ ЛЕСІ", "ЛЕСІ УКРАЇНКИ"),
        (r"КНЯЗІВ ОСТРОЗЬКИХ|ОСТРОЗЬКИХ КНЯЗІВ|КНЯЗІВ ОСТРОВСЬКИХ", "КНЯЗІВ ОСТРОЗЬКИХ"),
        (
            r"ГЕНЕРАЛА ВСЕВОЛОДА ПЕТРІВА|ПЕТРІВА ВСЕВОЛОДА ГЕНЕРАЛА|ПЕТРІВА ВСЕВОЛОДА|ГЕНЕРАЛА ВСЕВОЛОДА ПЕРТІВА",
            "ПЕТРІВА",
        ),
        (r"МАЛА БЕРДИЧІВСЬКА|М БЕРДИЧІВСЬКА|МАЛА БЕРДИЧІВСКА", "МАЛА БЕРДИЧІВСЬКА"),
        (r"ВЕЛИКА БЕРДИЧІВСЬКА|В БЕРДИЧІВСЬКА|ВЕЛИКА БЕРДИЧІВСКА", "ВЕЛИКА БЕРДИЧІВСЬКА"),
        (r"СТЕПАНА БАНДЕРИ|БАНДЕРИ СТЕПАНА", "БАНДЕРИ"),
        (r"ЛЕХА КАЧИНСЬКОГО", "КАЧИНСЬКОГО"),
        (r"АКАДЕМІКА ТУТКОВСЬКОГО|ТУТКОВСЬКОГО АКАДЕМІКА", "ТУТКОВСЬКОГО"),
        (r"СЛЬОТИ ІВАНА|ІВАНА СЛЬОТИ", "СЛЬОТИ"),
        (r"ВОЛОДИМИРА ГНАТЮКА|ГНАТЮКА ВОЛОДИМИРА", "ГНАТЮКА"),
        (r"ІВАНА ОГІЄНКА|ОГІЄНКА ІВАНА|ІВАНА ОГІЄНКО", "ОГІЄНКА"),
        (r"ІВАНА БАГРЯНОГО|БАГРЯНОГО ІВАНА", "БАГРЯНОГО"),
        (r"ІВАНА КОЧЕРГИ|КОЧЕРГИ ІВАНА", "КОЧЕРГИ"),
        (r"ІВАНА БОГУНА|БОГУНА ІВАНА", "БОГУНА"),
        (r"ІВАНА ГОНТИ|ГОНТИ ІВАНА", "ГОНТИ"),
        (r"БОРИСА ТЕНА|ТЕНА БОРИСА", "ТЕНА"),
        (r"БОРИСА ЛЯТОШИНСЬКОГО|ЛЯТОШИНСЬКОГО БОРИСА", "ЛЯТОШИНСЬКОГО"),
        (r"ОЛЕКСАНДРА КЛОСОВСЬКОГО|КЛОСОВСЬКОГО ОЛЕКСАНДРА", "КЛОСОВСЬКОГО"),
        (r"ГЕТЬМАНА САМОЙЛОВИЧА|САМОЙЛОВИЧА ГЕТЬМАНА", "САМОЙЛОВИЧА"),
    ]
]


def js_round(n):
    """Еквівалент Math.round: .5 завжди в бік +∞."""
    if n is None or not math.isfinite(n):
        return 0
    return math.floor(n + 0.5)


def fmt(n):
    return f"{js_round(n):,}".replace(",", " ")


def fmt_usd(n):
    return "$" + fmt(n)


def js_num(v):
    """Еквівалент унарного + у JS для типових полів запису."""
    if v is None or v is False or v == "":
        return 0
    if v is True:
        return 1
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip().replace(",", "."))
    except (TypeError, ValueError):
        return float("nan")


def percentile_sorted(sorted_arr, p):
    if not sorted_arr:
        return 0
    if len(sorted_arr) == 1:
        return sorted_arr[0]
    idx = (len(sorted_arr) - 1) * p
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return sorted_arr[lo]
    return sorted_arr[lo] + (sorted_arr[hi] - sorted_arr[lo]) * (idx - lo)


def median(arr):
    if not arr:
        return 0
    a = sorted(arr)
    m = len(a) // 2
    if len(a) % 2:
        return a[m]
    return (a[m - 1] + a[m]) / 2


def range_pct(n):
    if not n or n < 1:
        return 10
    return js_round(max(8, min(30, 38 / math.sqrt(n))))


def reliability_text(n):
    if n >= 20:
        return ("Висока надійність вибірки", "rel-high")
    if n >= 8:
        return ("Середня надійність вибірки", "rel-mid")
    return ("Низька надійність вибірки", "rel-low")


def quality_grade(n):
    if n >= 30:
        return {"g": "A", "txt": "висока"}
    if n >= 10:
        return {"g": "B", "txt": "середня"}
    if n >= 3:
        return {"g": "C", "txt": "низька"}
    return {"g": "—", "txt": "недостатньо даних"}


def normalize_address_for_compare(v):
    s = str(v or "").upper()
    s = _ADDR_TYPE_RE.sub(" ", s)
    s = _PUNCT_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    for pat, repl in _STREET_REPLACEMENTS:
        s = pat.sub(repl, s)
    return re.sub(r"\s+", " ", s).strip()


def street_core(v):
    raw = str(v or "").upper()
    is_lane = bool(_LANE_RE.search(raw))
    raw = _SPACED_LETTERS_RE.sub(lambda m: " " + re.sub(r"\s+", "", m.group(0)) + " ", raw)
    s = normalize_address_for_compare(raw)
    s = _HOUSE_UNIT_RE.sub(" ", s)
    s = _INITIAL_DOT_RE.sub(" ", s)
    s = _LONE_LETTER_RE.sub(" ", s)
    s = _ROMAN_RE.sub(lambda m: " " + _ROMAN.get(m.group(2), m.group(2)) + " ", s)
    for pat, repl in _WORDNUM_RES:
        s = pat.sub(repl, s)
    s = _ORDINAL_RE.sub(r" \1 ", s)
    s = _NUM_LETTER_RE.sub(" ", s)
    s = _NON_ALNUM_RE.sub(" ", s)
    toks = [
        t
        for t in s.split()
        if t and (t.isdigit() or (len(t) > 1 and t not in _TITLES))
    ]
    nums = sorted(t for t in toks if t.isdigit())
    words = sorted(t for t in toks if not t.isdigit())
    core = " ".join(nums + words)
    core = re.sub(r"\s+", " ", core).strip()
    return ("ПРОВ~" if is_lane else "") + core


def valid_city(r):
    if r.get("р") in ALLOWED:
        return True
    ad = str(r.get("ад") or "").upper()
    return not any(b in ad for b in BLACKLIST)


def is_oliivka_record(r):
    if not r:
        return False
    district = str(r.get("р") or "").upper()
    micro = str(r.get("мікрозона") or r.get("micro_location") or "").upper()
    addr = str(r.get("ад") or r.get("адреса") or "").upper()
    return (
        "ОЛІЇВК" in district
        or "ОЛІЇВК" in micro
        or "РИХЛИК" in addr
        or "РИХЛІК" in addr
        or "ОЛІЇВК" in addr
    )


def city_zone_record(r):
    if not r:
        return r
    if is_oliivka_record(r):
        rr = dict(r)
        rr["мікрозона"] = "Оліївка"
        return rr
    return r


def district_match(r, d):
    if d == "all":
        return True
    if d == "Житомир (без району)":
        return r.get("р") == "Житомир (без району)" and not is_oliivka_record(r)
    return r.get("р") == d


def is_dorm_rec(r):
    tp = str(r.get("тип") or r.get("вид") or r.get("об") or "").upper()
    ad = str(r.get("ад") or "").upper()
    return (
        "ГУРТОЖ" in tp
        or "КОМУНАЛ" in tp
        or "КІМНАТ" in tp
        or "ГУРТОЖ" in ad
    )


def is_house_rec(r):
    tp = str(r.get("тип") or r.get("вид") or r.get("об") or "").upper()
    return any(
        x in tp for x in ("БУДИНОК", "КОТЕДЖ", "ТАУНХАУС", "САДИБ", "ДАЧ")
    )


def object_type_ok(r, t):
    is_dorm = is_dorm_rec(r)
    is_house = is_house_rec(r)
    if t["objectType"] == "dorm":
        return is_dorm
    if t["objectType"] == "house":
        return is_house
    return (not is_dorm) and (not is_house)


def parse_date_value(v):
    s = str(v or "").strip()
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$", s)
    if m:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def record_date(r):
    return r.get("дата") or r.get("Дата оцінки") or r.get("date") or "—"


def date_period_ok(r, t, as_of: date):
    if not t.get("datePeriod") or t["datePeriod"] == "all":
        return True
    d = parse_date_value(record_date(r))
    if not d:
        return True
    try:
        months = int(t["datePeriod"])
    except (TypeError, ValueError):
        return True
    if not months:
        return True
    cutoff = as_of - timedelta(days=months * 30)
    return cutoff <= d <= as_of


def area_ok(r, t):
    if not t.get("area"):
        return True
    a = js_num(r.get("пл"))
    area = t["area"]
    pct = 0.35 if area < 30 else (0.28 if area <= 60 else 0.22)
    return a >= area * (1 - pct) and a <= area * (1 + pct)


def similarity(r, t, rooms_penalty=0.0, floor_score=True):
    """Бал схожості сайту v11.10.

    rooms_penalty і floor_score мають значення за замовчуванням, які
    повторюють старий сайт: кімнати тут не штрафуються (їх відсікає
    build_candidates), а поверх лишається у балі.
    """
    s = 100.0
    area = js_num(r.get("пл")) or 0
    if t.get("area"):
        da = abs(area - t["area"]) / t["area"]
        s -= min(da * 135, 48)
    if r.get("рік") and t.get("year"):
        dy = abs(js_num(r.get("рік")) - t["year"])
        if dy <= 8:
            pen = dy * 0.4
        elif dy <= 25:
            pen = 3.2 + (dy - 8) * 1.2
        else:
            pen = 23.6 + (dy - 25) * 0.5
        s -= min(pen, 32)
    if t.get("district") != "all" and not district_match(r, t["district"]):
        s -= 12
    if t.get("_streetCore"):
        rc = r["_stCore"] if "_stCore" in r else street_core(r.get("ад"))
        if rc:
            if rc == t["_streetCore"]:
                s += 10
            elif t["_streetCore"] in rc or rc in t["_streetCore"]:
                s += 5
            else:
                s -= 14
    if floor_score and r.get("пов") and t.get("floor"):
        # Як у JS: `tf=t.totalFloors&&t.floor===t.totalFloors` і `rf=r['пх']&&r['пов']===r['пх']`,
        # потім tf!==rf. Не зводимо до bool: false !== null у JS дає true,
        # тобто запис без поверховості штрафується, якщо в цілі вказано кількість поверхів
        # і цільовий поверх не останній.
        tf = t.get("totalFloors") and t["floor"] == t["totalFloors"]
        rf = r.get("пх") and r.get("пов") == r.get("пх")
        if (t["floor"] == 1) != (r.get("пов") == 1):
            s -= 8
        if tf != rf:
            s -= 5
        s -= min(abs((js_num(r.get("пов")) or 0) - t["floor"]) * 0.4, 6)
    if rooms_penalty and t.get("objectType") != "house" and t.get("rooms"):
        rk = r.get("к")
        if rk not in (None, ""):
            if js_num(rk) != t.get("rooms"):
                s -= rooms_penalty
    return max(1, js_round(s))


def trim_by_ppm(arr):
    src = list(arr)
    if len(src) < 6:
        return {"used": src, "low": 0, "high": 0, "lowB": None, "highB": None}
    ppms = sorted(p for p in (js_num(r.get("цкв")) for r in src) if p > 0)
    if len(ppms) < 6:
        return {"used": src, "low": 0, "high": 0, "lowB": None, "highB": None}
    q1 = percentile_sorted(ppms, 0.25)
    q3 = percentile_sorted(ppms, 0.75)
    iqr = q3 - q1
    low_b = q1 - 1.5 * iqr
    high_b = q3 + 1.5 * iqr
    kept = [r for r in src if low_b <= js_num(r.get("цкв") or 0) <= high_b]
    low = len(src) - len([r for r in kept if js_num(r.get("цкв") or 0) >= low_b])
    high = len([r for r in src if js_num(r.get("цкв") or 0) > high_b])
    max_cut = len(src) // 4  # Math.floor(src.length*0.25)
    if len(src) - len(kept) > max_cut:
        med = percentile_sorted(ppms, 0.50)
        ranked = sorted(src, key=lambda r: abs((js_num(r.get("цкв")) or 0) - med))
        kept = ranked[: len(src) - max_cut]
        cut_ids = set(id(r) for r in kept)
        low = len([r for r in src if id(r) not in cut_ids and (js_num(r.get("цкв")) or 0) < med])
        high = len([r for r in src if id(r) not in cut_ids and (js_num(r.get("цкв")) or 0) >= med])
    if not kept:
        return {"used": src, "low": 0, "high": 0, "lowB": low_b, "highB": high_b}
    return {
        "used": kept,
        "low": low,
        "high": high,
        "lowB": js_round(low_b),
        "highB": js_round(high_b),
    }


def avg_ppm(arr):
    p = [js_num(r.get("цкв")) for r in arr]
    p = [x for x in p if x]
    return (sum(p) / len(p)) if p else 0


def recommended_auto_selection(cands, t):
    if not cands:
        return []
    pool = [r for r in cands if (r.get("_s") or 0) >= 70]
    if len(pool) < 15:
        pool = [r for r in cands if (r.get("_s") or 0) >= 60]
    if len(pool) < 15:
        pool = cands[: min(60, len(cands))]
    max_use = 120 if t and t.get("dataScope") == "archive" else 80
    return pool[: min(max_use, len(pool))]


def build_candidates(source, t, as_of: date):
    base = [r for r in source if object_type_ok(r, t) and date_period_ok(r, t, as_of)]
    if t.get("district") != "all":
        base = [r for r in base if district_match(r, t["district"])]
    if t.get("objectType") != "house":
        rooms = t.get("rooms")
        base = [r for r in base if js_num(r.get("к")) == rooms]
    if t.get("area"):
        base = [r for r in base if area_ok(r, t)]
    t["_streetCore"] = street_core(t["street"]) if t.get("street") else None
    out = []
    for i, r in enumerate(base):
        rr = dict(r)
        stc = street_core(r.get("ад"))
        rr["_stCore"] = stc
        rr["_adNorm"] = r.get("ад_норм") or normalize_address_for_compare(r.get("ад"))
        rr["_id"] = (
            f"{i}-{r.get('ад_норм') or r.get('ад')}-{r.get('пл')}-{r.get('в')}-{record_date(r)}"
        )
        rr["_s"] = similarity(rr, t)
        out.append(rr)
    out.sort(key=lambda x: -x["_s"])
    return out


def selected_period(arr):
    dates = sorted(
        d
        for d in (parse_date_value(record_date(r)) for r in arr)
        if d
    )
    if not dates:
        return "—"
    return dates[0].strftime("%d.%m.%Y") + " - " + dates[-1].strftime("%d.%m.%Y")


def compute_valuation(work_data, archive_data, t, as_of: date):
    t = dict(t)
    scope = str(t.get("dataScope") or "12")
    t["dataScope"] = scope
    t["datePeriod"] = "all" if scope == "archive" else scope
    t.setdefault("objectType", "apartment")
    t.setdefault("district", "all")
    t.setdefault("oliivkaPickMode", "similar")
    use_archive = scope == "archive" and archive_data
    source = archive_data if use_archive else work_data
    if not t.get("area"):
        return {"error": "Потрібна загальна площа об'єкта (кв.м). Без неї додаток показує лише загальну статистику."}

    cand = build_candidates(source, t, as_of)
    selected = recommended_auto_selection(cand, t)
    trimmed = trim_by_ppm(selected)
    arr = trimmed["used"]
    if not arr:
        return {
            "error": "Не знайдено зареєстрованих оцінок за цими параметрами.",
            "cand": len(cand),
            "selected": 0,
            "used": 0,
        }
    wsum = sum((r.get("_s") or 1) for r in arr)
    wppm = sum((r.get("_s") or 1) * (js_num(r.get("цкв")) or 0) for r in arr) / wsum
    sppm = avg_ppm(arr)
    mppm = median([js_num(r.get("цкв")) or 0 for r in arr])
    skew = abs(wppm - mppm) / mppm if mppm > 0 else 0
    used_median = skew > 0.08
    base_ppm = mppm if used_median else wppm
    total = base_ppm * t["area"]
    pct = range_pct(len(arr))
    k = pct / 100
    total_fund = total * FUND_MULTIPLIER
    top = sorted(arr, key=lambda r: -r.get("_s", 0))[:15]
    top_out = [
        {
            "address": r.get("ад") or "",
            "date": record_date(r),
            "area": r.get("пл"),
            "floor": r.get("пов"),
            "floors": r.get("пх"),
            "ppm": r.get("цкв"),
            "score": r.get("_s"),
            "district": r.get("р"),
        }
        for r in top
    ]
    rel = reliability_text(len(arr))
    grade = quality_grade(len(arr))
    return {
        "as_of": as_of.isoformat(),
        "months": None if scope == "archive" else int(scope),
        "scope": scope,
        "cand": len(cand),
        "selected": len(selected),
        "used": len(arr),
        "cut": (trimmed.get("low") or 0) + (trimmed.get("high") or 0),
        "lowB": trimmed.get("lowB"),
        "highB": trimmed.get("highB"),
        "wppm": wppm,
        "sppm": sppm,
        "mppm": mppm,
        "skew": skew,
        "usedMedian": used_median,
        "basePpm": base_ppm,
        "total": total,
        "totalFund": total_fund,
        "pct": pct,
        "total_uah_round": js_round(total),
        "fund_uah_round": js_round(total_fund),
        "ppm_round": js_round(base_ppm),
        "fund_ppm_round": js_round(base_ppm * FUND_MULTIPLIER),
        "total_usd_round": js_round(total / USD_RATE),
        "fund_usd_round": js_round(total_fund / USD_RATE),
        "range_low": js_round(total * (1 - k)),
        "range_high": js_round(total * (1 + k)),
        "fund_range_low": js_round(total_fund * (1 - k)),
        "fund_range_high": js_round(total_fund * (1 + k)),
        "reliability": rel[0],
        "grade": grade,
        "period": selected_period(arr),
        "base_text": (
            f"Через перекіс вибірки ({js_round(skew * 100)}%) за основу вартості взято медіану."
            if used_median
            else "За основу вартості взято середньозважене за схожістю."
        ),
        "top": top_out,
        "source": "archive" if use_archive else "work",
    }


def strip_pii(obj):
    if isinstance(obj, dict):
        return {
            k: strip_pii(v)
            for k, v in obj.items()
            if str(k).lower() not in PII_FIELDS and "рнкпп" not in str(k).lower()
            and "рнокпп" not in str(k).lower()
        }
    if isinstance(obj, list):
        return [strip_pii(x) for x in obj]
    return obj


def _load_raw_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _download(url: str) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": "fdmu-calc/1.0 (local valuation tool)"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def refresh_data(quiet=False):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    saved = {}
    for name in ("calc_data.json", "archive_calc_data.json"):
        url = f"{BASE_URL}/{name}?v={APP_VERSION}"
        raw = None
        try:
            raw = _download(url)
            if not quiet:
                print(f"Завантажено {url} ({len(raw)} байт)", file=sys.stderr)
        except Exception as e:
            local = Path("/tmp") / name
            if local.exists():
                if not quiet:
                    print(
                        f"Не вдалося завантажити {url}: {e}. Беру локальну копію {local}.",
                        file=sys.stderr,
                    )
                raw = local.read_bytes()
            else:
                raise
        data = json.loads(raw.decode("utf-8"))
        data = strip_pii(data)
        out = DATA_DIR / name
        with out.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        saved[name] = out
        if not quiet:
            n = len(data.get("records") or [])
            print(
                f"Збережено {out} записів: {n}, оновлено: {data.get('updated')}, без поля «вик».",
                file=sys.stderr,
            )
    return saved


def ensure_cache(refresh=False):
    work_p = DATA_DIR / "calc_data.json"
    arch_p = DATA_DIR / "archive_calc_data.json"
    if refresh or not work_p.exists():
        if refresh:
            refresh_data()
        else:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            src = Path("/tmp/calc_data.json")
            if not src.exists():
                refresh_data()
            else:
                if not arch_p.exists() and Path("/tmp/archive_calc_data.json").exists():
                    sources = [
                        (Path("/tmp/calc_data.json"), work_p),
                        (Path("/tmp/archive_calc_data.json"), arch_p),
                    ]
                else:
                    sources = [(src, work_p)]
                for src, dst in sources:
                    data = strip_pii(_load_raw_json(src))
                    with dst.open("w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
                if not arch_p.exists() and work_p.exists():
                    # якщо архів окремо не прийшов, не дублюємо зайвий раз
                    pass
    work = _load_raw_json(work_p)
    if arch_p.exists():
        arch = _load_raw_json(arch_p)
    else:
        arch = work
    work_recs = [city_zone_record(r) for r in (work.get("records") or []) if valid_city(r)]
    arch_recs = [city_zone_record(r) for r in (arch.get("records") or []) if valid_city(r)]
    meta = {
        "updated": work.get("updated"),
        "version_file": work.get("version"),
        "app_version": APP_VERSION,
        "source": work.get("source"),
        "data_mode": work.get("data_mode"),
        "working_period_from": work.get("working_period_from"),
        "working_period_to": work.get("working_period_to"),
        "archive_period_start": work.get("archive_period_start"),
        "count": work.get("count") or len(work_recs),
        "stats": work.get("stats"),
        "auto_update": work.get("auto_update"),
        "usd_rate": USD_RATE,
        "fund_multiplier": FUND_MULTIPLIER,
    }
    return work_recs, arch_recs, meta


def lookup_street(work_data, query, limit=12):
    core = street_core(query)
    hits = []
    districts = {}
    rooms = {}
    areas = []
    for r in work_data:
        rc = street_core(r.get("ад"))
        if not rc:
            continue
        if rc == core or (core and (core in rc or rc in core)):
            hits.append(r)
            districts[r.get("р") or "—"] = districts.get(r.get("р") or "—", 0) + 1
            k = r.get("к")
            rooms[k] = rooms.get(k, 0) + 1
            if r.get("пл"):
                areas.append(js_num(r.get("пл")))
    hits.sort(key=lambda r: parse_date_value(record_date(r)) or date(1900, 1, 1), reverse=True)
    return {
        "core": core,
        "n": len(hits),
        "districts": districts,
        "rooms": rooms,
        "area_median": median(areas) if areas else None,
        "recent": [
            {
                "address": r.get("ад"),
                "date": record_date(r),
                "area": r.get("пл"),
                "rooms": r.get("к"),
                "floor": r.get("пов"),
                "floors": r.get("пх"),
                "year": r.get("рік"),
                "district": r.get("р"),
                "ppm": r.get("цкв"),
            }
            for r in hits[:limit]
        ],
    }


def parse_as_of(s):
    if not s:
        return date.today()
    s = s.strip()
    for fmt_ in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt_).date()
        except ValueError:
            pass
    raise SystemExit(f"Невірна дата --as-of: {s}")


def print_result(title, t, res, meta, show_top=True):
    print(title)
    if res.get("error"):
        print("  " + res["error"])
        if "cand" in res:
            print(f"  Знайдено у пулі: {res['cand']}")
        return
    print(
        f"  Період: {res['months']} міс. (останні {res['months']*30} днів до {res['as_of']})"
        if res["months"]
        else f"  Період: повний архів, станом на {res['as_of']}"
    )
    print(
        f"  Аналоги: знайдено {res['cand']}, вибрано {res['selected']}, "
        f"використано {res['used']}"
        + (f", відсічено викидів {res['cut']}" if res["cut"] else ", викидів немає")
    )
    print(f"  Дати оцінок у вибірці: {res['period']}")
    print(f"  {res['base_text']} {res['reliability']}. Оцінка якості: {res['grade']['g']} ({res['grade']['txt']}).")
    print("  А) За реєстром ФДМУ (зареєстровані оцінки)")
    print(
        f"     {fmt(res['basePpm'])} грн/м²  ·  {fmt(res['total'])} грн  ·  "
        f"≈ {fmt_usd(res['total']/USD_RATE)} (курс {USD_RATE} у додатку)"
    )
    print(
        f"     Діапазон ±{res['pct']}%: {fmt(res['range_low'])} - {fmt(res['range_high'])} грн"
    )
    print("  Б) Прогноз інструменту «безкоштовної оцінки» ФДМУ (оціночна вартість, ×1,24)")
    print(
        f"     {fmt(res['fund_ppm_round'])} грн/м²  ·  {fmt(res['totalFund'])} грн  ·  "
        f"≈ {fmt_usd(res['totalFund']/USD_RATE)}"
    )
    print(
        f"     Діапазон ±{res['pct']}%: {fmt(res['fund_range_low'])} - {fmt(res['fund_range_high'])} грн"
    )
    if show_top and res.get("top"):
        print("  Найближчі аналоги (без даних оцінювача):")
        for i, a in enumerate(res["top"][:10], 1):
            fl = a["floor"]
            tf = a["floors"]
            floor = f"{fl}/{tf}" if fl and tf else (str(fl) if fl else "—")
            print(
                f"    {i:2}. {a['address']}, {a['date']}, {a['area']} м², "
                f"пов. {floor}, {fmt(a['ppm'] or 0)} грн/м², бал {a['score']}"
            )


def default_tests():
    return [
        {
            "name": "вул. Східна, 2к, 46 м², 2/5",
            "t": {
                "objectType": "apartment",
                "district": "all",
                "rooms": 2,
                "area": 46.0,
                "street": "Східна",
                "year": None,
                "floor": 2,
                "totalFloors": 5,
            },
        },
        {
            "name": "вул. Київська, 2к, 50 м², 2/4",
            "t": {
                "objectType": "apartment",
                "district": "all",
                "rooms": 2,
                "area": 50.0,
                "street": "Київська",
                "year": None,
                "floor": 2,
                "totalFloors": 4,
            },
        },
        {
            "name": "вул. Народицька, 2к, 54 м²",
            "t": {
                "objectType": "apartment",
                "district": "all",
                "rooms": 2,
                "area": 54.0,
                "street": "Народицька",
                "year": None,
                "floor": None,
                "totalFloors": None,
            },
        },
        {
            "name": "Небесної Сотні, 2к, 45 м², 5/5",
            "t": {
                "objectType": "apartment",
                "district": "all",
                "rooms": 2,
                "area": 45.0,
                "street": "Небесної Сотні",
                "year": None,
                "floor": 5,
                "totalFloors": 5,
            },
        },
        {
            "name": "1к, Богунський, 32 м²",
            "t": {
                "objectType": "apartment",
                "district": "Богунський",
                "rooms": 1,
                "area": 32.0,
                "street": None,
                "year": None,
                "floor": None,
                "totalFloors": None,
            },
        },
    ]


def run_self_test(work, arch, meta, as_of, months):
    results = []
    for case in default_tests():
        print("=" * 72)
        print(case["name"])
        for m in months:
            t = dict(case["t"])
            t["dataScope"] = str(m)
            res = compute_valuation(work, arch, t, as_of)
            print_result(f"\n  [{m} міс.]", t, res, meta, show_top=(m == months[-1]))
            results.append((case["name"], m, t, res))
        print()
    return results


def compare_with_js(results, work, arch, as_of):
    """Порівняти з рушієм JS (Node), якщо він є."""
    import subprocess
    import tempfile

    engine = DIR / "fdmu_js_engine.js"
    if not engine.exists():
        print("Немає fdmu_js_engine.js - пропускаю перевірку з JS.")
        return None
    # Lightweight records for JS: only fields used in calc, already without вик
    fields = ["р", "пл", "пов", "пх", "рік", "в", "цкв", "ад", "ад_норм", "дата", "тип", "к", "мікрозона"]
    slim = [{k: r.get(k) for k in fields} for r in work]
    slim_arch = slim if arch is work else [{k: r.get(k) for k in fields} for r in arch]
    payload = {
        "as_of": as_of.isoformat(),
        "work": slim,
        "arch": slim_arch,
        "cases": [
            {"name": name, "months": m, "t": {k: v for k, v in t.items() if k != "_streetCore"}}
            for name, m, t, _ in results
        ],
    }
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as f:
        json.dump(payload, f, ensure_ascii=False)
        inp = f.name
    runner = r"""
const fs = require('fs');
const path = require('path');
const { computeValuation } = require(path.resolve(process.argv[2]));
const data = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
global.AS_OF_DATE = data.as_of;
const out = data.cases.map(c => {
  const t = Object.assign({}, c.t, {dataScope: String(c.months), datePeriod: String(c.months)});
  const r = computeValuation(data.work, data.arch, t);
  return {name: c.name, months: c.months, total_uah_round: r.total_uah_round, fund_uah_round: r.fund_uah_round,
          ppm_round: r.ppm_round, used: r.used, selected: r.selected, cand: r.cand, usedMedian: r.usedMedian,
          basePpm: r.basePpm, total: r.total, totalFund: r.totalFund, wppm: r.wppm, mppm: r.mppm};
});
process.stdout.write(JSON.stringify(out));
"""
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".js", delete=False) as f:
        f.write(runner)
        runner_path = f.name
    try:
        proc = subprocess.run(
            ["node", runner_path, str(engine), inp],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError:
        print("Node.js не знайдено - пропускаю перевірку з JS.")
        return None
    if proc.returncode != 0:
        print("Помилка Node:", proc.stderr[-2000:])
        return None
    js_out = json.loads(proc.stdout)
    print("=" * 72)
    print("Перевірка збігу Python і JS (той самий алгоритм, та сама база, та сама дата):")
    mismatches = 0
    for (name, m, _t, py), js in zip(results, js_out):
        keys = ["total_uah_round", "fund_uah_round", "ppm_round", "used", "selected", "cand", "usedMedian"]
        diffs = []
        for k in keys:
            if py.get(k) != js.get(k):
                diffs.append(f"{k}: py={py.get(k)} js={js.get(k)}")
        # also tight float check
        if py.get("total") is not None and js.get("total") is not None:
            if abs(py["total"] - js["total"]) > 0.01:
                diffs.append(f"total float: py={py['total']} js={js['total']}")
        status = "OK" if not diffs else "РОЗБІЖНІСТЬ"
        if diffs:
            mismatches += 1
        print(f"  {status}  {name}, {m} міс." + ("" if not diffs else "  " + "; ".join(diffs)))
    if mismatches == 0:
        print(f"Усі {len(results)} прогонів збіглися точно (округлені грн і кількість аналогів).")
    else:
        print(f"Розбіжностей: {mismatches} з {len(results)}.")
    return mismatches


def build_argparser():
    p = argparse.ArgumentParser(
        description="Розрахунок двох вартостей за алгоритмом довідника ФДМУ Житомир."
    )
    p.add_argument("--street", "--address", dest="street", help="Вулиця або адреса (фактор схожості, не жорсткий фільтр)")
    p.add_argument("--area", type=float, help="Загальна площа, м² (обов'язково для розрахунку)")
    p.add_argument("--rooms", type=int, help="Кількість кімнат: 1, 2, 3 або 4 (4 означає 4+). Жорсткий фільтр.")
    p.add_argument("--floor", type=int, help="Поверх (фактор схожості)")
    p.add_argument("--floors", dest="totalFloors", type=int, help="Поверховість будинку")
    p.add_argument("--year", type=int, help="Рік побудови (фактор схожості)")
    p.add_argument(
        "--district",
        default="all",
        help="Район: all (за замовчуванням, усе місто), Богунський, Корольовський, "
        "'Житомир (без району)'",
    )
    p.add_argument(
        "--type",
        dest="objectType",
        default="apartment",
        choices=["apartment", "dorm", "house"],
        help="Тип об'єкта: apartment, dorm, house",
    )
    p.add_argument(
        "--months",
        default="1,2,3",
        help="Періоди у місяцях через кому (1,2,3,4,5,6,12) або archive",
    )
    p.add_argument("--as-of", dest="as_of", help="Дата розрахунку YYYY-MM-DD (за замовчуванням сьогодні)")
    p.add_argument("--refresh", action="store_true", help="Оновити дані з сайту довідника")
    p.add_argument("--lookup", metavar="АДРЕСА", help="Показати, що є в базі за цією вулицею, без розрахунку")
    p.add_argument("--self-test", action="store_true", help="П'ять контрольних прикладів і звірка з JS")
    p.add_argument("--json", action="store_true", help="Вивести результат у JSON")
    p.add_argument("--top", type=int, default=10, help="Скільки аналогів показати")
    return p


def main(argv=None):
    args = build_argparser().parse_args(argv)
    work, arch, meta = ensure_cache(refresh=args.refresh)
    as_of = parse_as_of(args.as_of)
    months = []
    for part in str(args.months).split(","):
        part = part.strip()
        if not part:
            continue
        months.append("archive" if part == "archive" else int(part))

    if args.lookup:
        info = lookup_street(work, args.lookup)
        print(f"Пошук у базі за «{args.lookup}», ядро вулиці: {info['core'] or '—'}")
        print(f"Знайдено оцінок: {info['n']}")
        if info["districts"]:
            print("Райони:", ", ".join(f"{k} ({v})" for k, v in info["districts"].items()))
        if info["rooms"]:
            print("Кімнати:", ", ".join(f"{k}: {v}" for k, v in sorted(info["rooms"].items(), key=lambda x: str(x[0]))))
        if info["area_median"]:
            print(f"Медіана площі: {info['area_median']} м²")
        print("Останні записи (без даних оцінювача):")
        for a in info["recent"]:
            print(
                f"  {a['date']}, {a['address']}, {a['rooms']}к, {a['area']} м², "
                f"пов. {a['floor']}/{a['floors'] or '—'}, {a['year'] or '—'} р., "
                f"{a['district']}, {fmt(a['ppm'] or 0)} грн/м²"
            )
        print(
            "\nДодаток сам з адреси параметри не підставляє. "
            "Для розрахунку обов'язкові площа і кількість кімнат. "
            "Поверх, поверховість, рік і район покращують підбір, але не є обов'язковими."
        )
        if args.area and args.rooms:
            print()
        else:
            return 0

    if args.self_test:
        print(
            f"База: оновлено {meta.get('updated')}, період "
            f"{meta.get('working_period_from')} - {meta.get('working_period_to')}, "
            f"записів {meta.get('count')}, версія сайту {APP_VERSION}, файл {meta.get('version_file')}."
        )
        print(f"Дата розрахунку (as-of): {as_of.isoformat()}. Курс USD у додатку: {USD_RATE} (фіксований, не НБУ).")
        results = run_self_test(work, arch, meta, as_of, months)
        compare_with_js(results, work, arch, as_of)
        return 0

    if args.area is None or args.rooms is None:
        print(
            "Для розрахунку потрібні принаймні --area (площа, м²) і --rooms (кількість кімнат)."
        )
        print(
            "Додаток не заповнює їх з адреси автоматично. Вулиця, поверх, рік і район - необов'язкові."
        )
        print("Приклад: python3 fdmu_calc.py --street 'Східна' --rooms 2 --area 46 --floor 2 --floors 5")
        print("Довідка по вулиці в базі: python3 fdmu_calc.py --lookup 'вул. Східна'")
        return 2

    t_base = {
        "objectType": args.objectType,
        "district": args.district,
        "rooms": args.rooms,
        "area": args.area,
        "street": args.street,
        "year": args.year,
        "floor": args.floor,
        "totalFloors": args.totalFloors,
    }
    all_res = []
    if not args.json:
        print(
            f"Об'єкт: {args.objectType}, {args.rooms}к, {args.area} м², "
            f"вул. {args.street or 'не вказано'}, "
            f"пов. {args.floor or '—'}/{args.totalFloors or '—'}, "
            f"рік {args.year or 'не вказано'}, район {args.district}."
        )
        print(
            f"База оновлена {meta.get('updated')}, оцінки "
            f"{meta.get('working_period_from')} - {meta.get('working_period_to')}."
        )
        print(f"Розрахунок станом на {as_of.isoformat()}. Курс у додатку: {USD_RATE} грн/$ (фіксований).")
    for m in months:
        t = dict(t_base)
        t["dataScope"] = str(m)
        res = compute_valuation(work, arch, t, as_of)
        all_res.append({"months": m, "result": res})
        if not args.json:
            print()
            print_result(f"=== {m} міс. ===" if m != "archive" else "=== Повний архів ===", t, res, meta, show_top=True)
            if res.get("top") and args.top != 10:
                pass
    if args.json:
        out = {"meta": {k: meta[k] for k in meta if k != "stats"}, "as_of": as_of.isoformat(), "target": t_base, "results": all_res}
        json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
