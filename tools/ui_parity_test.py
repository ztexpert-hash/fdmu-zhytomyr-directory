#!/usr/bin/env python3
"""A10 · UI-паритет: числа на сторінці (ТОП виконавців за кожен рік і місяць,
картки виконавців) == незалежний перерахунок із records.
Потрібно: pip install playwright; Chrome/Chromium.  Запуск: python3 tools/ui_parity_test.py"""
import json, re, statistics, sys, threading, functools, math
from collections import defaultdict
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
d = json.loads((ROOT / "calc_data.json").read_text(encoding="utf-8"))
merge = json.loads((ROOT / "executor_merge.json").read_text(encoding="utf-8")).get("merge", {})
rec = d["records"]


def jsround(x):                       # Math.round у JS: .5 → вгору
    return math.floor(x + 0.5)


def code_of(v):
    v = (v or "").strip()
    if "," in v:
        c, nm = v.split(",", 1)
        if c.strip():
            return c.strip()
        v = nm.strip()
    return re.sub(r"[^А-ЯЇІЄҐA-Z0-9]", "", v.upper())[:20] or "—"


G = defaultdict(list)
for r in rec:
    c = code_of(r.get("вик")); G[merge.get(c, c)].append(r)
NAME = {str(x["code"]): x["name"] for x in d["archive_analytics"]["executors"]["top_all"]}
ym = lambda dt: f"{dt[6:]}-{dt[3:5]}"
dk = lambda dt: (dt[6:], dt[3:5], dt[:2])


def expected_top(kind, val):
    rows, total = [], 0
    for c, rs in G.items():
        sel = rs if kind == "all" else [r for r in rs if (r["дата"][6:] if kind == "year" else ym(r["дата"])) == val]
        total += len(sel)
        if sel:
            v = [r["цкв"] for r in sel if r.get("цкв")]
            rows.append({"name": NAME.get(c, c), "count": len(sel),
                         "med": jsround(statistics.median(v)) if v else 0,
                         "last": max((r["дата"] for r in sel), key=dk)})
    return rows, total


num = lambda s: int(re.sub(r"[^\d]", "", s) or 0)
fails, checked = [], 0


def main():
    global checked
    class Q(SimpleHTTPRequestHandler):
        def log_message(self, *a): pass
    h = functools.partial(Q, directory=str(ROOT))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}/index.html"
    with sync_playwright() as p:
        exe = next((x for x in ("/usr/bin/google-chrome", "/usr/bin/chromium") if Path(x).exists()), None)
        b = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch(channel="chrome")
        pg = b.new_page(); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(url)
        # сайт закритий логіном; облікові дані не використовуємо — рендеримо той самий код напряму
        pg.evaluate("(typeof DATA==='undefined'||!DATA.length) && loadData()")
        pg.wait_for_function("window.__EXEC_A && ARCHIVE_DATA && ARCHIVE_DATA.length>0", timeout=120000)
        pg.wait_for_selector("#analytics-executors table", state="attached")
        n_arch = pg.evaluate("ARCHIVE_DATA.length")
        if n_arch != len(rec):
            fails.append(f"ARCHIVE_DATA у браузері {n_arch} ≠ records {len(rec)} (фільтр validCity відкидає записи)")
        ex = d["archive_analytics"]["executors"]
        periods = [("all", "")] + [("year", y) for y in ex["years"]] + [("month", m) for m in ex["months_list"]]
        for kind, val in periods:
            got = pg.evaluate("""([k,v])=>{const ks=document.getElementById('execPeriodKind');ks.value=k;ks.dispatchEvent(new Event('change'));
                const vs=document.getElementById('execPeriodValue'); if(k!=='all'){vs.value=v; vs.dispatchEvent(new Event('change'));}
                const cap=(document.querySelector('.aud-period-cap')||{}).textContent||'';
                return {cap, rows:[...document.querySelectorAll('#analytics-executors tbody tr')].map(tr=>[...tr.children].map(td=>td.textContent.trim()))};}""", [kind, val])
            exp, total = expected_top(kind, val)
            exp.sort(key=lambda x: (-x["count"], x["name"]))
            tag = f"ТОП {kind} {val}"
            if f"оцінок у періоді: {total:,}".replace(",", " ") not in got["cap"].replace("\xa0", " "):
                fails.append(f"{tag}: підпис «{got['cap']}», очікується всього {total}")
            # порівняння за назвою (рівні кількості можуть мати інший порядок)
            ebn = {e["name"]: e for e in exp}
            cut = exp[11]["count"] if len(exp) >= 12 else 0
            if len(got["rows"]) != min(12, len(exp)):
                fails.append(f"{tag}: рядків {len(got['rows'])}, очікується {min(12, len(exp))}")
            for name, cnt, share, med, last in got["rows"]:
                checked += 1
                e = ebn.get(name)
                if not e:
                    fails.append(f"{tag}: «{name}» немає в перерахунку"); continue
                exp_share = jsround(e["count"] * 10000 / total) / 100 if kind == "all" else jsround(e["count"] / total * 1000) / 10
                if num(cnt) != e["count"]: fails.append(f"{tag} {name}: кількість {cnt} ≠ {e['count']}")
                if abs(float(share.rstrip('%')) - exp_share) > 0.051: fails.append(f"{tag} {name}: частка {share} ≠ {exp_share}")
                if num(med) != e["med"]: fails.append(f"{tag} {name}: медіана {med} ≠ {e['med']}")
                if last != e["last"]: fails.append(f"{tag} {name}: остання дата {last} ≠ {e['last']}")
            # у ТОП не пропущено нікого з більшою кількістю
            shown = {r[0] for r in got["rows"]}
            for e in exp:
                if e["count"] > cut and e["name"] not in shown:
                    fails.append(f"{tag}: пропущено {e['name']} ({e['count']})")
        # картки виконавців: усі з ≥ 20 оцінками
        for c, rs in sorted(G.items(), key=lambda kv: -len(kv[1])):
            if len(rs) < 20: break
            q = c if c.isdigit() else NAME.get(c, c)
            card = pg.evaluate("""(q)=>{const i=document.getElementById('executorSearchInput'); i.value=q; renderExecutorProfile(true);
                const box=document.getElementById('executorProfile');
                const st=[...box.querySelectorAll('.executor-result-grid')].map(g=>[...g.querySelectorAll('.stat')].map(s=>[(s.querySelector('.l')||{}).textContent,(s.querySelector('.v')||{}).textContent]));
                const months=[...box.querySelectorAll('.executor-months-wrap tbody tr')].map(tr=>[...tr.children].map(td=>td.textContent.trim()));
                const sums=[...box.querySelectorAll('.executor-details summary')].map(s=>s.textContent.trim());
                return {st,months,sums};}""", q)
            checked += 1
            tag = f"Картка {NAME.get(c, c)} ({c})"
            if not card["st"]:
                fails.append(f"{tag}: картку не знайдено за «{q}»"); continue
            m = dict(card["st"][0]); yrs = dict(card["st"][1]) if len(card["st"]) > 1 else {}
            v = [r["цкв"] for r in rs if r.get("цкв")]
            dates = sorted((r["дата"] for r in rs), key=dk)
            if num(m.get("Всього робіт", "")) != len(rs): fails.append(f"{tag}: всього {m.get('Всього робіт')} ≠ {len(rs)}")
            if num(m.get("Медіана грн/м²", "")) != jsround(statistics.median(v)): fails.append(f"{tag}: медіана {m.get('Медіана грн/м²')} ≠ {jsround(statistics.median(v))}")
            if num(m.get("Середня грн/м²", "")) != jsround(sum(v) / len(v)): fails.append(f"{tag}: середня {m.get('Середня грн/м²')} ≠ {jsround(sum(v)/len(v))}")
            per = m.get("Період", "").replace(" ", "")
            if per != f"{dates[0]}–{dates[-1]}": fails.append(f"{tag}: період {per} ≠ {dates[0]}–{dates[-1]}")
            ey = defaultdict(int); em = defaultdict(int)
            for r in rs: ey[r["дата"][6:]] += 1; em[ym(r["дата"])] += 1
            if {k: num(v_) for k, v_ in yrs.items()} != dict(ey): fails.append(f"{tag}: роки {yrs} ≠ {dict(ey)}")
            if {k: num(v_) for k, v_ in card["months"]} != dict(em): fails.append(f"{tag}: місяці розходяться")
            ys = {s.split(" — ")[0]: num(s.split(" — ")[1]) for s in card["sums"] if re.match(r"^\d{4} — ", s)}
            ms = {s.split(" — ")[0]: num(s.split(" — ")[1]) for s in card["sums"] if re.match(r"^\d{4}-\d{2} — ", s)}
            if ys != dict(ey) or ms != dict(em): fails.append(f"{tag}: «Усі роботи» розходяться з перерахунком")
        if errs: fails.append(f"JS-помилки на сторінці: {errs[:5]}")
        b.close()
    srv.shutdown()
    print(f"UI-паритет: перевірено {checked} рядків/карток, періодів {len(periods)}; розбіжностей {len(fails)}")
    for f in fails[:60]: print(" -", f)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
