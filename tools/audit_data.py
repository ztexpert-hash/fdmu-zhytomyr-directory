#!/usr/bin/env python3
"""Аудит узгодженості calc_data.json / archive_calc_data.json.
Незалежний перерахунок усіх агрегатів з records і звірка з тим, що бачить сайт.
Вихід: 0 = усі перевірки PASS, 1 = є FAIL. Нічого не змінює.
Запуск:  py -3.14 tools/audit_data.py   (Linux/CI: python3 tools/audit_data.py)"""
import hashlib, json, re, statistics, sys, datetime
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CALC, ARCH, MERGE = ROOT / "calc_data.json", ROOT / "archive_calc_data.json", ROOT / "executor_merge.json"
RESULTS = []          # (id, title, ok, n_bad, examples)
WARN = []             # не блокують, але йдуть у звіт


def check(cid, title, bad):
    bad = list(bad)
    RESULTS.append((cid, title, not bad, len(bad), bad[:10]))


def ym(dt):            # 'DD.MM.YYYY' -> 'YYYY-MM'
    return f"{dt[6:]}-{dt[3:5]}"


def dk(dt):
    return (dt[6:], dt[3:5], dt[:2])


def med(v):
    return round(statistics.median(v)) if v else 0


def main():
    a, b = CALC.read_bytes(), ARCH.read_bytes()
    # A1 ------------------------------------------------------------------
    d, d2 = json.loads(a), json.loads(b)
    check("A1", "MD5 calc_data.json == archive_calc_data.json",
          [] if hashlib.md5(a).digest() == hashlib.md5(b).digest() else
          [f"calc={hashlib.md5(a).hexdigest()} archive={hashlib.md5(b).hexdigest()}; "
           f"різні ключі: {[k for k in set(d) | set(d2) if d.get(k) != d2.get(k)]}"])
    rec = d["records"]
    # A2 ------------------------------------------------------------------
    check("A2", "count == archive_count == len(records)",
          [] if d["count"] == d["archive_count"] == len(rec) else
          [f"count={d['count']} archive_count={d['archive_count']} records={len(rec)}"])

    # A8 (валідність записів) — далі розрахунки спираються на дати
    date_re = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
    upd = d.get("updated", "")
    bad_date, future = [], []
    for i, r in enumerate(rec):
        dt = r.get("дата") or ""
        if not date_re.match(dt):
            bad_date.append(f"#{i} дата={dt!r}")
            continue
        try:
            dd = datetime.date(int(dt[6:]), int(dt[3:5]), int(dt[:2]))
        except ValueError:
            bad_date.append(f"#{i} неіснуюча дата {dt}"); continue
        if upd and dd.isoformat() > upd:
            future.append(f"#{i} {dt} > updated {upd}")
    check("A8.1", "Дати DD.MM.YYYY, валідні, не в майбутньому", bad_date + future)
    # цкв рахується з точної площі ФДМУ; у частині старих записів пл округлена до 0,1 м².
    # Допуск = ефект округлення площі на ±0,05 м² + округлення цкв до 1 грн.
    def ppm_tol(r):
        pl = r["пл"]
        return r["в"] * 0.05 / (pl * max(pl - 0.05, 0.01)) + 1.0
    check("A8.2", "цкв == в/пл (допуск: округлення пл до 0,1 м² і цкв до 1 грн)",
          [f"#{i} {r.get('ад')} {r.get('дата')} в={r.get('в')} пл={r.get('пл')} цкв={r.get('цкв')} в/пл={r['в']/r['пл']:.1f}"
           for i, r in enumerate(rec)
           if r.get("в") and r.get("пл") and r.get("цкв") is not None
           and abs(r["в"] / r["пл"] - r["цкв"]) > ppm_tol(r)])
    check("A8.3", "пл > 0, в > 0, цкв > 0",
          [f"#{i} {r.get('ад')} пл={r.get('пл')} в={r.get('в')} цкв={r.get('цкв')}"
           for i, r in enumerate(rec) if not (r.get("пл") or 0) > 0 or not (r.get("в") or 0) > 0
           or not (r.get("цкв") or 0) > 0])
    check("A8.4", "пов ≤ пх (де обидва вказані)",
          [f"#{i} {r.get('ад')} {r.get('дата')} пов={r['пов']} пх={r['пх']}"
           for i, r in enumerate(rec) if r.get("пов") and r.get("пх") and r["пов"] > r["пх"]])
    kc = Counter((r.get("ад_норм"), round(r.get("в") or 0), round(r.get("пл") or 0, 2),
                  r.get("дата"), r.get("пов"), r.get("рік")) for r in rec)
    check("A8.5", "Дублікати за ключем (ад_норм, в, пл, дата, пов, рік)",
          [f"{k} ×{n}" for k, n in kc.items() if n > 1])
    junk = [f"#{i} ад={r.get('ад')!r} цкв={r.get('цкв')} {r.get('дата')}" for i, r in enumerate(rec)
            if str(r.get("ад") or "").rstrip().endswith(" -") or str(r.get("ад") or "").strip() in ("", "-")]
    if junk:
        WARN.append(("A8.6", "Сміттєві адреси (закінчуються на ' -' або порожні)", len(junk), junk[:10]))
    # Вулиці: однаковий ад_норм — різні написання ад (інформативно)
    sp = defaultdict(set)
    for r in rec:
        if r.get("ад_норм"):
            sp[r["ад_норм"]].add(re.sub(r"\s+", " ", str(r.get("ад") or "").strip()))
    # A9 класифікація Оліївки ------------------------------------------------
    olv = [r for r in rec if (r.get("р") or "") == "Оліївка" or "ОЛІЇВКА" in str(r.get("нп") or "").upper()]
    check("A9", "Оліївка: рік ≥ 2018, не Худоліївка/Мозоліївка/Забілоччя",
          [f"{r.get('нп')} {r.get('ад')} рік={r.get('рік')} {r.get('дата')}" for r in olv
           if not (r.get("рік") or 0) >= 2018 or any(x in str(r.get("нп") or "").upper()
                                                        for x in ("ХУДОЛІЇВ", "МОЗОЛІЇВ", "ЗАБІЛОЧ"))])

    # A3/A4 виконавці --------------------------------------------------------
    merge = json.loads(MERGE.read_text(encoding="utf-8")).get("merge", {}) if MERGE.exists() else {}

    def code_of(v):
        v = (v or "").strip()
        if "," in v:
            c, nm = v.split(",", 1)
            if c.strip():
                return c.strip()
            v = nm.strip()
        return re.sub(r"[^А-ЯЇІЄҐA-Z0-9]", "", v.upper())[:20] or "—"

    g = defaultdict(list)
    for r in rec:
        c = code_of(r.get("вик"))
        g[merge.get(c, c)].append(r)
    ex = d["archive_analytics"]["executors"]
    top = {str(x["code"]): x for x in ex["top_all"]}
    bad = []
    for c in set(g) | set(top):
        rs, x = g.get(c, []), top.get(c)
        if x is None:
            bad.append(f"{c}: є в records ({len(rs)}), відсутній у top_all"); continue
        if not rs:
            bad.append(f"{c} {x['name']}: у top_all, але 0 записів"); continue
        v = [r["цкв"] for r in rs if r.get("цкв")]
        exp = {"count": len(rs),
               "share_pct": round(len(rs) * 100 / len(rec), 2),
               "median_ppm": med(v), "avg_ppm": round(sum(v) / len(v)) if v else 0,
               "last_date": max((r["дата"] for r in rs), key=dk),
               "years": dict(sorted(Counter(r["дата"][6:] for r in rs).items())),
               "months": dict(sorted(Counter(ym(r["дата"]) for r in rs).items()))}
        for k, ev in exp.items():
            if x.get(k) != ev:
                diff = ev if k not in ("years", "months") else \
                    {m: (x.get(k, {}).get(m), ev.get(m)) for m in set(ev) | set(x.get(k) or {})
                     if x.get(k, {}).get(m) != ev.get(m)}
                bad.append(f"{c} {x['name']}: {k} у файлі={x.get(k) if k not in ('years','months') else ''} очікується={diff}")
    check("A3", "Кожен виконавець top_all == перерахунок із records (count/years/months/median/avg/last/share)", bad)
    check("A4", "Σ top_all.count == len(records); executors_count; concentration_top5",
          [m for m in [
              None if sum(x["count"] for x in ex["top_all"]) == len(rec) else
              f"Σ={sum(x['count'] for x in ex['top_all'])} records={len(rec)}",
              None if ex["executors_count"] == len(ex["top_all"]) == len(g) else
              f"executors_count={ex['executors_count']} top_all={len(ex['top_all'])} груп={len(g)}",
              None if ex["concentration_top5_pct"] == round(sum(sorted((len(v) for v in g.values()), reverse=True)[:5]) * 100 / len(rec), 1)
              else f"concentration_top5_pct={ex['concentration_top5_pct']}"] if m])
    # A5 списки періодів -----------------------------------------------------
    allm = sorted({ym(r["дата"]) for r in rec}); ally = sorted({r["дата"][6:] for r in rec})
    bad = []
    for k, exp in (("months_list", allm), ("months_available", allm), ("years", ally), ("years_available", ally)):
        if ex.get(k) != exp:
            bad.append(f"{k}: зайві={sorted(set(ex.get(k) or [])-set(exp))} відсутні={sorted(set(exp)-set(ex.get(k) or []))} відсорт={ex.get(k)==sorted(ex.get(k) or [])}")
    check("A5", "months_list/years: повні, відсортовані, без дублікатів", bad)

    # A6 ринок по місяцях (правило домінантного виконавця) ---------------------
    bym = defaultdict(list)
    for r in rec:
        if r.get("цкв"):
            bym[ym(r["дата"])].append(r)

    def mrow(name, rs):
        v = [r["цкв"] for r in rs]
        rm_, ra_ = med(v), round(sum(v) / len(v))
        m_, a_, adj = rm_, ra_, False
        cnt = Counter(r["вик"] for r in rs)
        if len(rs) >= 5:
            t, n = cnt.most_common(1)[0]
            if n / len(rs) >= 0.20:
                rest = [r["цкв"] for r in rs if r["вик"] != t]
                if len(rest) >= 3:
                    rmed = statistics.median(rest)
                    if rmed and abs(rm_ - rmed) / rmed >= 0.15:
                        m_, a_, adj = round(rmed), round(sum(rest) / len(rest)), True
        return {"name": name, "count": len(rs), "median_ppm": m_, "avg_ppm": a_,
                "raw_median_ppm": rm_, "raw_avg_ppm": ra_, "raw_count": len(rs), "adjusted": adj}
    expm = [mrow(m, bym[m]) for m in sorted(bym)]
    bad = []
    for src in ("v5_analytics", "archive_analytics.market"):
        got = d["v5_analytics"]["months"] if src == "v5_analytics" else d["archive_analytics"]["market"]["months"]
        if len(got) != len(expm):
            bad.append(f"{src}: місяців {len(got)} ≠ {len(expm)}")
        for gm, em in zip(got, expm):
            for k in em:
                if gm.get(k) != em[k]:
                    bad.append(f"{src} {em['name']}: {k}={gm.get(k)} очікується {em[k]}")
    check("A6", "Ринок по місяцях (count/median/avg/adjusted) == перерахунок", bad)
    # A7 stats ---------------------------------------------------------------
    st, aa = d["stats"], d["archive_analytics"]["stats"]
    ppm = [r["цкв"] for r in rec if r.get("цкв")]
    bad = []
    for k, ev in (("median_ppm", med(ppm)), ("avg_ppm", round(sum(ppm) / len(ppm))),
                  ("city_total", len(rec)),
                  ("apartments", sum(r["тип"] == "Квартира" for r in rec)),
                  ("houses", sum(r["тип"] == "Будинок" for r in rec)),
                  ("dorms", sum(r["тип"] == "Кімната/гуртожиток" for r in rec)),
                  ("min_date", min((r["дата"] for r in rec), key=dk)),
                  ("max_date", max((r["дата"] for r in rec), key=dk))):
        if st.get(k) != ev:
            bad.append(f"stats.{k}={st.get(k)} очікується {ev}")
    if st.get("districts") != dict(Counter(r["р"] for r in rec)):
        bad.append(f"stats.districts={st.get('districts')} очікується {dict(Counter(r['р'] for r in rec))}")
    for k, ev in (("median_ppm", med(ppm)), ("last_month_median", expm[-1]["median_ppm"]),
                  ("last_month_avg", expm[-1]["avg_ppm"])):
        if aa.get(k) != ev:
            bad.append(f"archive_analytics.stats.{k}={aa.get(k)} очікується {ev}")
    if d.get("working_period_to") != st.get("max_date"):
        bad.append(f"working_period_to={d.get('working_period_to')} ≠ max_date={st.get('max_date')}")
    check("A7", "stats / archive_analytics.stats / період == перерахунок", bad)
    lm = expm[-1]
    WARN.append(("A7.i", f"Останній місяць {lm['name']} неповний: {lm['count']} записів (база до {st.get('max_date')})", 0, []))

    # звіт -------------------------------------------------------------------
    out = [f"# Аудит даних — {datetime.datetime.now():%Y-%m-%d %H:%M}",
           f"Файл: calc_data.json, записів {len(rec)}, оновлено {upd}\n",
           "| ID | Перевірка | Результат | Порушень |", "|---|---|---|---|"]
    out += [f"| {i} | {t} | {'PASS' if ok else '**FAIL**'} | {n} |" for i, t, ok, n, _ in RESULTS]
    for i, t, ok, n, ex_ in RESULTS:
        if not ok:
            out += [f"\n### {i} FAIL — приклади"] + [f"- {e}" for e in ex_]
    if WARN:
        out.append("\n## Попередження (не блокують)")
        for i, t, n, ex_ in WARN:
            out.append(f"- {i}: {t}" + (f" — {n}" if n else ""))
            out += [f"  - {e}" for e in ex_]
    txt = "\n".join(out)
    print(txt)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(txt, encoding="utf-8")
    return 0 if all(ok for _, _, ok, _, _ in RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
