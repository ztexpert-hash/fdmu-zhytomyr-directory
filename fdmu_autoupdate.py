#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ФДМУ Житомир — автооновлення для GitHub Actions.

Що робить:
1. Заходить на сторінку ФДМУ, знаходить посилання на тижневі ZIP-архіви.
2. Пропускає ті, що вже є у processed_files.json.
3. Качає нові, дістає Obekty_Zhytlovoi_neruxomosti*.csv.
4. Доливає записи міста Житомир у calc_data.json (з дедуплікацією).
5. Перераховує аналітику, оновлює archive_calc_data.json, README.txt,
   processed_files.json, fdmu_schema_columns.json.

Запуск: python3 fdmu_autoupdate.py          (усі нові архіви)
        python3 fdmu_autoupdate.py --limit 3 (не більше 3 за прогін)
        python3 fdmu_autoupdate.py --dry-run (тільки показати, що знайдено)
"""
from __future__ import annotations
import io, json, re, sys, zipfile, csv, statistics, argparse, datetime
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

SPFU_PAGE = "https://www.spfu.gov.ua/ua/content/spf-estimate-basereport-dani-z-edinoi-bazi.html"
TARGET_PREFIX = "obekty_zhytlovoi_neruxomosti"
UA = "Mozilla/5.0 FDMU-Zhytomyr-Updater/8.0"

STATUS_KEEP = ("зареєстровано", "перевірено")
OLIIVKA_BAD = ("ХУДОЛІЇВК", "МОЗОЛІЇВК", "ЗАБІЛОЧ")
OLIIVKA_MIN_YEAR = 2018
BAD_ADDRESS_TOKENS = ("БЕРДИЧ", "НОВОГРАД", "ЗВЯГ", "КОРОСТ", "МАЛИН",
                      "ОВРУЧ", "ЧУДНІВ", "АНДРУШ", "РАДОМИШЛ")

ROOT = Path(__file__).resolve().parent
CALC = ROOT / "calc_data.json"
ARCHIVE = ROOT / "archive_calc_data.json"
PROCESSED = ROOT / "processed_files.json"
SCHEMA = ROOT / "fdmu_schema_columns.json"
README = ROOT / "README.txt"
MERGE = ROOT / "executor_merge.json"


# ---------------------------------------------------------------- утиліти
def num(s):
    s = str(s or "").replace("\xa0", " ").replace(" ", "").replace(",", ".").strip()
    try:
        return float(s)
    except ValueError:
        return None


def norm_addr(a):
    a = (a or "").upper().replace("\xa0", " ")
    a = re.sub(r"\b(ВУЛ|ПРОВ|ПРОСП|ПЛ|БУЛЬВ|ПРОЇЗД|МАЙДАН|ШОСЕ|НАБ)\.?\b", " ", a)
    a = re.sub(r"[^А-ЯЇІЄҐA-Z0-9\- ]", " ", a)
    a = re.sub(r"\b\d+-?[ЙЯГАЕИ]{0,2}\b", " ", a)
    return " ".join(sorted(w for w in a.split() if len(w) > 1)).strip()


def obj_type(v):
    v = (v or "").lower()
    if "гуртожит" in v or "кімнат" in v:
        return "Кімната/гуртожиток"
    if "квартир" in v:
        return "Квартира"
    if "будинок" in v or "садиб" in v or "таунхаус" in v:
        return "Будинок"
    return None


def rooms_of(area, typ):
    if typ != "Квартира" or not area:
        return None, None
    if area <= 40:
        return 1, "1-кімнатна"
    if area <= 65:
        return 2, "2-кімнатна"
    if area <= 90:
        return 3, "3-кімнатна"
    return 4, "4+-кімнатна"


def district(np_, street, typ, year):
    t = re.sub(r"\s+", " ", (np_ or "").strip().upper())
    st = (street or "").upper()
    if "БОГУНСЬК" in t:
        return "Богунський"
    if "КОРОЛЬОВСЬК" in t or "КОРОЛЕВ" in t:
        return "Корольовський"
    if "ОЛІЇВ" in t:
        if any(b in st for b in OLIIVKA_BAD):
            return None
        if typ in ("Квартира", "Кімната/гуртожиток") and year and year >= OLIIVKA_MIN_YEAR:
            return "Оліївка новобудови"
        return None
    if "ЖИТОМИРСЬК" in t and "РАЙОН" in t and "ЖИТОМИР" not in t.replace("ЖИТОМИРСЬК", ""):
        return None
    if re.search(r"(^|\b)(М\.?\s*)?ЖИТОМИР(\b|$)", t):
        return "Житомир (без району)"
    return None


def col(row, *names):
    for k in row:
        kk = (k or "").replace("’", "'").lower()
        for n in names:
            if n.replace("’", "'").lower() in kk:
                return row[k]
    return ""


def dk(s):
    p = (s or "").split(".")
    return (p[2], p[1], p[0]) if len(p) == 3 else ("", "", "")


# ---------------------------------------------------------------- мережа
def fetch(url, timeout=90):
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=timeout) as r:
        return r.read()


def page_zip_links():
    raw = fetch(SPFU_PAGE)
    for enc in ("utf-8", "windows-1251", "cp1251"):
        try:
            html = raw.decode(enc)
            break
        except Exception:
            continue
    else:
        html = raw.decode("utf-8", errors="ignore")
    out = []
    for h in re.findall(r'href=["\']([^"\']+\.zip(?:\?[^"\']*)?)["\']', html, re.I):
        u = urljoin(SPFU_PAGE, h)
        if u not in out:
            out.append(u)
    return out


def csv_from_zip(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        cand = [n for n in z.namelist()
                if TARGET_PREFIX in Path(n).name.lower()
                and Path(n).suffix.lower() in (".csv", ".txt")]
        if not cand:
            return None
        cand.sort(key=lambda n: z.getinfo(n).file_size, reverse=True)
        return z.read(cand[0])


def parse_csv(raw):
    for enc in ("utf-8-sig", "cp1251", "windows-1251", "utf-8"):
        try:
            txt = raw.decode(enc)
            return list(csv.DictReader(io.StringIO(txt), delimiter=";"))
        except Exception:
            continue
    raise RuntimeError("не вдалося прочитати CSV")


# ---------------------------------------------------------------- обробка
def extract(rows, label):
    out = []
    for x in rows:
        if "ЖИТОМИР" not in (col(x, "Регіон") or "").upper():
            continue
        if (col(x, "Статус звіту") or "").strip().lower() not in STATUS_KEEP:
            continue
        typ = obj_type(col(x, "Вид об’єкта нерухомості"))
        if not typ:
            continue
        street = (col(x, "Вулиця") or "").strip().replace("\xa0", " ")
        if any(b in street.upper() for b in BAD_ADDRESS_TOKENS):
            continue
        y = re.search(r"(19|20)\d{2}", col(x, "Рік введення") or "")
        year = int(y.group()) if y else None
        dist = district(col(x, "Населений пункт"), street, typ, year)
        if not dist:
            continue
        area, val = num(col(x, "Загальна площа")), num(col(x, "Оціночна вартість об’єкта оцінки"))
        if not area or not val:
            continue
        m = re.match(r"\s*(\d+)", col(x, "Поверх у будівлі") or "")
        pref = (col(x, "Тип вулиці") or "").strip()
        addr = (pref + " " + street).strip() if pref else street
        k, cat = rooms_of(area, typ)
        out.append({
            "р": dist, "нп": (col(x, "Населений пункт") or "").strip(),
            "пл": area, "пов": int(m.group(1)) if m else None, "пх": None, "рік": year,
            "в": round(val), "цкв": round(val / area),
            "ад": addr, "ад_норм": norm_addr(addr),
            "дата": (col(x, "Дата оцінки") or "").strip(),
            "вик": (col(x, "СОД") or "").strip(),
            "тип": typ, "к": k, "категорія": cat, "файл": label,
            "зона": (col(x, "Зона населеного пункту") or "").strip(),
            "тип_буд": (col(x, "Тип будинку") or "").strip().replace("\xa0", "") or "інше",
            "клас": (col(x, "Клас будинку") or "").strip(),
            "стіни": (col(x, "Матеріал стін") or "").strip(),
            "перекр": (col(x, "Матеріал перекриття") or "").strip(),
            "стан": (col(x, "Технічний стан") or "").strip(),
            "інж": (col(x, "Інженерне обладнання") or "").strip(),
            "паркінг": (col(x, "Наявність вбудованого паркінгу") or "").strip(),
            "жпл": num(col(x, "Площа житлових приміщень")),
        })
    return out


def key(r):
    return (r.get("ад_норм") or norm_addr(r.get("ад")), round(r.get("в") or 0),
            round(r.get("пл") or 0, 2), r.get("дата"), r.get("пов"), r.get("рік"))


def month_row(name, rs):
    v = [r["цкв"] for r in rs]
    raw_med, raw_avg = round(statistics.median(v)), round(sum(v) / len(v))
    med, avg, adj = raw_med, raw_avg, False
    cnt = Counter(r["вик"] for r in rs)
    if cnt and len(rs) >= 5:
        top, n = cnt.most_common(1)[0]
        if n / len(rs) >= 0.20:
            rest = [r["цкв"] for r in rs if r["вик"] != top]
            if len(rest) >= 3:
                rm = statistics.median(rest)
                if rm and abs(raw_med - rm) / rm >= 0.15:
                    med, avg, adj = round(rm), round(sum(rest) / len(rest)), True
    return {"name": name, "count": len(rs), "median_ppm": med, "avg_ppm": avg,
            "raw_median_ppm": raw_med, "raw_avg_ppm": raw_avg,
            "raw_count": len(rs), "adjusted": adj}


def recalc(d):
    rec = d["records"]
    rec.sort(key=lambda r: dk(r["дата"]))
    ppm = sorted(r["цкв"] for r in rec if r.get("цкв"))

    def pct(v, p):
        i = (len(v) - 1) * p
        lo, hi = int(i), min(int(i) + 1, len(v) - 1)
        return round(v[lo] + (v[hi] - v[lo]) * (i - int(i)))

    q1, q3 = pct(ppm, .25), pct(ppm, .75)
    st = d.setdefault("stats", {})
    st.update({
        "apartments": sum(1 for r in rec if r["тип"] == "Квартира"),
        "dorms": sum(1 for r in rec if r["тип"] == "Кімната/гуртожиток"),
        "houses": sum(1 for r in rec if r["тип"] == "Будинок"),
        "districts": dict(Counter(r["р"] for r in rec)),
        "executors": len({r["вик"] for r in rec if r.get("вик")}),
        "avg_ppm": round(sum(ppm) / len(ppm)), "median_ppm": round(statistics.median(ppm)),
        "q1_ppm": q1, "q3_ppm": q3, "iqr_ppm": q3 - q1, "city_total": len(rec),
        "unique_normalized_addresses": len({r["ад_норм"] for r in rec if r.get("ад_норм")}),
    })

    by_m = defaultdict(list)
    for r in rec:
        p = r["дата"].split(".")
        if len(p) == 3 and r.get("цкв"):
            by_m[f"{p[2]}-{p[1]}"].append(r)
    months = [month_row(m, by_m[m]) for m in sorted(by_m)]
    d.setdefault("v5_analytics", {})["months"] = months
    d.setdefault("archive_analytics", {}).setdefault("market", {})["months"] = months

    def grp(fn, src=rec):
        g = defaultdict(list)
        for r in src:
            k = fn(r)
            if k and r.get("цкв"):
                g[k].append(r["цкв"])
        return sorted(({"name": k, "count": len(v), "median_ppm": round(statistics.median(v)),
                        "avg_ppm": round(sum(v) / len(v)), "min_ppm": min(v), "max_ppm": max(v)}
                       for k, v in g.items()), key=lambda x: -x["count"])

    va = d["v5_analytics"]
    va["streets_top"] = grp(lambda r: r.get("ад_норм"))[:60]
    va["districts"] = [{k: x[k] for k in ("name", "count", "median_ppm", "avg_ppm")}
                       for x in grp(lambda r: r.get("р"))]
    va["rooms"] = [{"cat": x["name"], "count": x["count"], "median_ppm": x["median_ppm"],
                    "avg_ppm": x["avg_ppm"]} for x in grp(lambda r: r.get("категорія"))]

    mr = d.setdefault("market_research", {})
    base = [r for r in rec if r["дата"][6:] in ("2025", "2026") and r.get("цкв")]
    mr["base_count"] = len(base)
    for fld, fn in (("zone", lambda r: r.get("зона")), ("house_type", lambda r: r.get("тип_буд")),
                    ("house_class", lambda r: r.get("клас")), ("walls", lambda r: r.get("стіни"))):
        mr[fld] = [{k: x[k] for k in ("name", "count", "median_ppm", "avg_ppm")}
                   for x in grp(fn, base)]
    mr["activity"] = [{"year": y, "count": c} for y, c in
                      sorted(Counter(r["дата"][6:] for r in rec).items())]
    mr["by_type"] = [{"name": x["name"], "count": x["count"], "median_ppm": x["median_ppm"]}
                     for x in grp(lambda r: r.get("тип"))]

    # --- дати періоду
    st["min_date"] = min((r["дата"] for r in rec), key=dk)
    st["max_date"] = max((r["дата"] for r in rec), key=dk)
    st["date_period"] = f'{st["min_date"]} — {st["max_date"]}'

    # --- market_research: age / floor / area (квартири базового періоду)
    flats = [r for r in base if r["тип"] == "Квартира"]

    def age_band(y):
        if not y:
            return None
        if y >= 2015:
            return "Новобудова (2015+)"
        if y >= 2000:
            return "2000–2014"
        if y >= 1991:
            return "1991–1999"
        if y >= 1961:
            return "1961–1990"
        return "до 1960"

    def floor_band(f):
        if not f:
            return None
        if f == 1:
            return "1 поверх"
        if f >= 10:
            return "10+ поверх"
        if f >= 5:
            return "5–9 поверх"
        return "2–4 поверх"

    def area_band(a):
        if not a:
            return None
        for hi, nm in ((35, "до 35 м²"), (45, "35–45 м²"), (55, "45–55 м²"),
                       (70, "55–70 м²"), (90, "70–90 м²")):
            if a < hi:
                return nm
        return "90+ м²"

    for fld, fn in (("age", lambda r: age_band(r.get("рік"))),
                    ("floor", lambda r: floor_band(r.get("пов"))),
                    ("area", lambda r: area_band(r.get("пл")))):
        mr[fld] = [{k: x[k] for k in ("name", "count", "median_ppm", "avg_ppm")}
                   for x in grp(fn, flats)]

    # --- сезонність (уся база)
    MON = ["Січень", "Лютий", "Березень", "Квітень", "Травень", "Червень",
           "Липень", "Серпень", "Вересень", "Жовтень", "Листопад", "Грудень"]
    mc = Counter(r["дата"][3:5] for r in rec if len(r["дата"]) == 10)
    tot_m = sum(mc.values())
    avg_m = tot_m / 12 if tot_m else 0
    mr["season"] = [{"name": MON[i], "count": mc.get(f"{i+1:02d}", 0),
                     "dev_pct": round((mc.get(f"{i+1:02d}", 0) - avg_m) / avg_m * 100)
                     if avg_m else 0} for i in range(12)]

    # --- ВИКОНАВЦІ (archive_analytics.executors) зі збереженням аліасів
    # --- ВИКОНАВЦІ: групування СТРОГО за кодом ЄДРПОУ.
    # Аліаси — це лише перелік назв, під якими цей код зустрічався у ФДМУ
    # (включно з помилками вводу). Назва НІКОЛИ не переважає код.
    def canon(vik):
        v = (vik or "").strip()
        if "," in v:
            code, nm = v.split(",", 1)
            code, nm = code.strip(), nm.strip()
            if code:
                return code, nm
            v = nm
        return re.sub(r'[^А-ЯЇІЄҐA-Z0-9]', '', v.upper())[:20] or "—", v

    # ручне об'єднання СОД за виконавцем (єдиний дозволений виняток з правила
    # «строго за ЄДРПОУ»); порожній файл або його відсутність = без об'єднань
    merge = {}
    if MERGE.exists():
        try:
            merge = {str(k): str(v) for k, v in
                     json.loads(MERGE.read_text(encoding="utf-8")).get("merge", {}).items()}
        except Exception as e:
            print(f"! executor_merge.json не прочитано: {e}")
    for _ in range(5):                      # розгортання ланцюжків A->B->C
        ch = {k: merge.get(v, v) for k, v in merge.items()}
        if ch == merge:
            break
        merge = ch

    eg = defaultdict(list)
    for r in rec:
        c, nm = canon(r.get("вик"))
        eg[merge.get(c, c)].append((r, nm, c))
    if merge:
        print(f"Об'єднано СОД за виконавцем: {len(merge)} пар")

    total = len(rec) or 1
    top_all = []
    for c, items in eg.items():
        rs = [x[0] for x in items]
        v = [x["цкв"] for x in rs if x.get("цкв")]
        names = Counter(x[1] for x in items)
        # назва береться з КАНОНІЧНОГО коду (орієнтир — виконавець, не поглинута фірма)
        own = Counter(x[1] for x in items if x[2] == c)
        main = (own or names).most_common(1)[0][0]
        al = sorted(n for n in names if n != main)
        yrs = Counter(x["дата"][6:] for x in rs if len(x["дата"]) == 10)
        mos = Counter(f'{x["дата"][6:]}-{x["дата"][3:5]}' for x in rs if len(x["дата"]) == 10)
        top_all.append({
            "name": main, "aliases": al, "code": c,
            "merged_codes": sorted({k for k, v in merge.items() if v == c}) or None,
            "count": len(rs),
            "share_pct": round(len(rs) * 100 / total, 2),
            "median_ppm": round(statistics.median(v)) if v else 0,
            "avg_ppm": round(sum(v) / len(v)) if v else 0,
            "last_date": max((x["дата"] for x in rs), key=dk),
            "years": dict(sorted(yrs.items())), "months": dict(sorted(mos.items())),
        })
    top_all.sort(key=lambda e: -e["count"])
    all_years = sorted({y for e in top_all for y in e["years"]})
    all_months = sorted({m for e in top_all for m in e["months"]})
    d["archive_analytics"]["executors"] = {
        "executors_count": len(top_all), "top_all": top_all,
        "years": all_years, "months_list": all_months,
        "years_available": all_years, "months_available": all_months,
        "concentration_top5_pct": round(sum(e["count"] for e in top_all[:5]) * 100 / total, 1),
    }
    d["archive_analytics"]["market"]["streets_top"] = va["streets_top"][:15]
    d["archive_analytics"]["market"]["districts"] = va["districts"]

    aa = d["archive_analytics"].setdefault("stats", {})
    aa["median_ppm"] = st["median_ppm"]
    if months:
        aa["last_month_median"] = months[-1]["median_ppm"]
        aa["last_month_avg"] = months[-1]["avg_ppm"]

    d["count"] = d["archive_count"] = len(rec)
    d["updated"] = datetime.date.today().isoformat()
    d["working_period_to"] = max((r["дата"] for r in rec), key=dk)
    return d


def write_readme(d, files):
    rec, st = d["records"], d["stats"]
    dates = [r["дата"] for r in rec]
    first, last = min(dates, key=dk), max(dates, key=dk)
    txt = f"""Довідник зареєстрованих оцінок ФДМУ — Житомир

Версія сайту: 7.3
Оновлено базу: {d['updated']}
Джерело: ФДМУ evaluation.spfu.gov.ua, файл Obekty_Zhytlovoi_neruxomosti

Правило міста Житомир:
Місто Житомир = Житомир (без району) + Богунський район + Корольовський район
+ новобудови с. Оліївка (введені в експлуатацію з {OLIIVKA_MIN_YEAR} року).
Житомирський район не включається.

Статуси, які включаються в базу:
- Зареєстровано
- Перевірено

База:
- Період дат оцінок: {first} — {last}
- Записів: {st['city_total']}
- Квартири: {st['apartments']}
- Кімнати/гуртожитки: {st['dorms']}
- Будинки: {st['houses']}
- Виконавців: {st['executors']}
- Оброблено архівів ФДМУ: {len(files)}
- Медіана: {st['median_ppm']} грн/м²
- Q1 / Q3: {st['q1_ppm']} / {st['q3_ppm']} грн/м²

По районах:
""" + "\n".join(f"- {k}: {v}" for k, v in sorted(st["districts"].items(), key=lambda t: -t[1])) + """

Оновлення виконується автоматично (GitHub Actions, щопонеділка).

ТОВ «ЕКСПЕРТНА ДУМКА»
Оцінка майна
тел. 097 921 37 72
"""
    README.write_text(txt, encoding="utf-8", newline="\r\n")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    d = json.loads(CALC.read_text(encoding="utf-8"))
    proc = json.loads(PROCESSED.read_text(encoding="utf-8")) if PROCESSED.exists() \
        else {"processed": [], "last_run": None}
    done = set(proc.get("processed", []))
    done |= {r["файл"] for r in d["records"] if r.get("файл")}

    links = page_zip_links()
    print(f"На сторінці ФДМУ знайдено ZIP-посилань: {len(links)}")

    todo = [u for u in links if Path(u.split("?")[0]).name not in done]
    print(f"Нових архівів: {len(todo)}")
    if a.dry_run:
        for u in todo[:20]:
            print("  ", u)
        return 0
    if not todo:
        print("Оновлень немає.")
        return 0
    if a.limit:
        todo = todo[:a.limit]

    seen = {key(r) for r in d["records"]}
    total_new, cols = 0, None
    for u in todo:
        name = Path(u.split("?")[0]).name
        print(f"→ {name}")
        try:
            raw = fetch(u, timeout=300)
            cbytes = csv_from_zip(raw)
            if not cbytes:
                print("   немає Obekty_Zhytlovoi_neruxomosti — пропуск")
                proc.setdefault("processed", []).append(name)
                continue
            rows = parse_csv(cbytes)
            if rows and cols is None:
                cols = list(rows[0].keys())
            got = extract(rows, name)
            fresh = [r for r in got if key(r) not in seen]
            for r in fresh:
                seen.add(key(r))
            d["records"].extend(fresh)
            total_new += len(fresh)
            print(f"   +{len(fresh)} (у файлі по місту: {len(got)})")
        except Exception as e:
            print(f"   ПОМИЛКА: {e}")
            continue
        proc.setdefault("processed", []).append(name)

    if total_new == 0:
        print("Нових записів немає — файли не змінено.")
        return 0

    recalc(d)
    CALC.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    ARCHIVE.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    proc["processed"] = sorted(set(proc["processed"]))
    proc["last_run"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    PROCESSED.write_text(json.dumps(proc, ensure_ascii=False, indent=2), encoding="utf-8")

    if cols:
        old = json.loads(SCHEMA.read_text(encoding="utf-8")).get("columns", []) \
            if SCHEMA.exists() else []
        if old and set(old) != set(cols):
            print("! УВАГА: змінився набір колонок ФДМУ")
            print("  нові:", set(cols) - set(old))
            print("  зниклі:", set(old) - set(cols))
        SCHEMA.write_text(json.dumps({"columns": cols, "updated": d["updated"]},
                                     ensure_ascii=False, indent=2), encoding="utf-8")

    write_readme(d, proc["processed"])
    d["auto_update"] = {"last_archive": Path(todo[-1].split("?")[0]).stem,
                        "added": total_new, "ran": proc["last_run"]}
    CALC.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    print(f"\nДодано записів: {total_new}")
    print(f"Усього в базі: {d['count']}")
    print(f"Остання дата: {d['working_period_to']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
