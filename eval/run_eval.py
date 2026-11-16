# -*- coding: utf-8 -*-
"""Measure registry-forecast error against Module e-Довідки.

Reads eval/data/cases.csv (no applicant names, no tax ids, no appraiser field).
Writes eval/results/metrics.md and eval/results/results.csv.

  python3 eval/run_eval.py
  python3 eval/run_eval.py --geo-dir eval/geo
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import model  # noqa: E402

CASES_PATH = ROOT / "data" / "cases.csv"
OUT_DIR = ROOT / "results"


def load_cases():
    with CASES_PATH.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_geo(directory: Path | None):
    if directory is None:
        return None
    streets = json.loads((directory / "streets.json").read_text(encoding="utf-8"))
    buildings = json.loads((directory / "buildings.json").read_text(encoding="utf-8"))
    return {"streets": streets.get("streets") or {}, "buildings": buildings.get("buildings") or {}}


def round_half_up(value, digits=2):
    factor = 10 ** digits
    return math.floor(value * factor + 0.5) / factor


def _headline(cases):
    """Real in-scope certificates, and sensitivity rows that are not typos."""
    real = [c for c in cases if c["set"] == "real" and c["in_scope"] == "1"]
    sens = [c for c in cases if c["set"] == "sensitivity" and c["is_typo"] != "1"]
    typo = [c for c in cases if c["is_typo"] == "1"]
    return real, sens, typo


def run_stage(records, cases, cfg, geo):
    out = {}
    for case in cases:
        out[case["case_id"]] = model.valuate(records, case, cfg, geo=geo)
    return out


def _errors(cases, preds):
    errs = []
    for case in cases:
        pred = preds.get(case["case_id"]) or {}
        if not pred.get("ok"):
            continue
        module = float(case["value_uah"])
        if module == 0:
            continue
        errs.append(pred["fund"] / module - 1.0)
    return errs


def summarize(cases, preds):
    scored = []
    failed = 0
    for case in cases:
        pred = preds.get(case["case_id"]) or {}
        if not pred.get("ok"):
            failed += 1
            continue
        module = float(case["value_uah"])
        scored.append(pred["fund"] / module - 1.0)
    if not scored:
        return {
            "n": len(cases),
            "scored": 0,
            "failed": failed,
            "mdape": None,
            "mean_bias": None,
            "median_bias": None,
            "within_25": None,
        }
    abs_err = [abs(e) for e in scored]
    return {
        "n": len(cases),
        "scored": len(scored),
        "failed": failed,
        "mdape": statistics.median(abs_err) * 100,
        "mean_bias": statistics.fmean(scored) * 100,
        "median_bias": statistics.median(scored) * 100,
        "within_25": sum(e <= 0.25 for e in abs_err) / len(scored) * 100,
    }


def fmt_pct(value):
    if value is None:
        return "n/a"
    return f"{value:+.1f}%" if value < 0 or True else f"{value:.1f}%"


def fmt_plain(value):
    if value is None:
        return "n/a"
    return f"{value:.1f}%"


def registry_ratios(cases, preds, use_coef=False):
    ratios = []
    for case in cases:
        pred = preds.get(case["case_id"]) or {}
        if not pred.get("ok"):
            continue
        base = pred["total"] * (pred.get("coef") or 1.0 if use_coef else 1.0)
        # use_coef: Module / (A * floor coef). Otherwise Module / A.
        if use_coef:
            base = pred["total"] * (pred.get("coef") or 1.0)
        else:
            base = pred["total"]
        if not base:
            continue
        ratios.append((case["case_id"], float(case["value_uah"]) / base))
    return ratios


def loo_summary(cases, preds, use_coef=False):
    """Leave-one-out multiplier: each forecast uses the median ratio of the others."""
    ratios = registry_ratios(cases, preds, use_coef=use_coef)
    by_id = {case_id: ratio for case_id, ratio in ratios}
    order = [case_id for case_id, _ratio in ratios]
    errs = []
    rounded_keys = []
    for case_id in order:
        others = [by_id[other] for other in order if other != case_id]
        if not others:
            continue
        k = statistics.median(others)
        rounded_keys.append(round_half_up(k, 2))
        pred = preds[case_id]
        base = pred["total"] * ((pred.get("coef") or 1.0) if use_coef else 1.0)
        module = float(next(c["value_uah"] for c in cases if c["case_id"] == case_id))
        errs.append(base * k / module - 1.0)
    if not errs:
        return None
    abs_err = [abs(e) for e in errs]
    return {
        "n": len(errs),
        "mdape": statistics.median(abs_err) * 100,
        "mean_bias": statistics.fmean(errs) * 100,
        "median_bias": statistics.median(errs) * 100,
        "within_25": sum(e <= 0.25 for e in abs_err) / len(errs) * 100,
        "k_rounded_set": sorted(set(rounded_keys)),
    }


def fixed_k_summary(cases, preds, k, use_coef=False):
    errs = []
    failed = 0
    for case in cases:
        pred = preds.get(case["case_id"]) or {}
        if not pred.get("ok"):
            failed += 1
            continue
        coef = (pred.get("coef") or 1.0) if use_coef else 1.0
        module = float(case["value_uah"])
        errs.append(pred["total"] * coef * k / module - 1.0)
    abs_err = [abs(e) for e in errs]
    return {
        "n": len(cases),
        "scored": len(errs),
        "failed": failed,
        "mdape": statistics.median(abs_err) * 100,
        "mean_bias": statistics.fmean(errs) * 100,
        "median_bias": statistics.median(errs) * 100,
        "within_25": sum(e <= 0.25 for e in abs_err) / len(errs) * 100,
    }


def row_line(name, summary):
    return (
        f"| {name} | {summary['scored']}/{summary['n']} | {summary['failed']} | "
        f"{fmt_plain(summary['mdape'])} | {fmt_pct(summary['mean_bias'])} | "
        f"{fmt_pct(summary['median_bias'])} | {fmt_plain(summary['within_25'])} |"
    )


def write_case_csv(path, cases, stages):
    fields = [
        "step",
        "case_id",
        "set",
        "in_scope",
        "is_typo",
        "street",
        "house",
        "area_m2",
        "rooms_num",
        "floor_num",
        "floors_num",
        "date",
        "module_value",
        "pred_fund",
        "pred_registry",
        "err_pct",
        "used",
        "scope",
        "radius_m",
        "geo",
        "ok",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for name, preds in stages:
            for case in cases:
                pred = preds.get(case["case_id"]) or {}
                module = float(case["value_uah"])
                fund = pred.get("fund")
                err = ""
                if pred.get("ok") and fund is not None and module:
                    err = f"{(fund / module - 1) * 100:.4f}"
                writer.writerow(
                    {
                        "step": name,
                        "case_id": case["case_id"],
                        "set": case["set"],
                        "in_scope": case["in_scope"],
                        "is_typo": case["is_typo"],
                        "street": case["street"],
                        "house": case["house"],
                        "area_m2": case["area_m2"],
                        "rooms_num": case["rooms_num"],
                        "floor_num": case["floor_num"],
                        "floors_num": case["floors_num"],
                        "date": case["date"],
                        "module_value": case["value_uah"],
                        "pred_fund": "" if fund is None else f"{fund:.2f}",
                        "pred_registry": "" if pred.get("total") is None else f"{pred['total']:.2f}",
                        "err_pct": err,
                        "used": pred.get("used", ""),
                        "scope": pred.get("scope", ""),
                        "radius_m": "" if pred.get("radius_m") is None else f"{pred['radius_m']:.1f}",
                        "geo": pred.get("geo", ""),
                        "ok": "1" if pred.get("ok") else "0",
                    }
                )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--records", default=str(ROOT.parent / "calc_data.json"))
    parser.add_argument("--geo-dir", default="")
    args = parser.parse_args()
    cases = load_cases()
    real, sens, typo = _headline(cases)
    print(f"cases {len(cases)} real {len(real)} sensitivity {len(sens)} typo {len(typo)}", file=sys.stderr)
    records = model.load_records(args.records)
    print(f"records {len(records)}", file=sys.stderr)
    geo = load_geo(Path(args.geo_dir)) if args.geo_dir else None

    # Sanity: the configurable path with baseline switches matches compute_valuation.
    base_cfg = {
        "months": "12",
        "rooms_hard": True,
        "rooms_penalty": 0,
        "auto_period": False,
        "floor_score": True,
        "floor_coef": False,
        "multiplier": 1.24,
        "geo": False,
    }
    original = run_stage(records, real, {**base_cfg, "baseline_path": True}, None)
    mirrored = run_stage(records, real, base_cfg, None)
    mismatches = []
    for case in real:
        a = original[case["case_id"]]
        b = mirrored[case["case_id"]]
        if a.get("ok") != b.get("ok"):
            mismatches.append(case["case_id"] + ":ok")
            continue
        if a.get("ok") and abs((a["fund"] or 0) - (b["fund"] or 0)) > 1:
            mismatches.append(f"{case['case_id']}:{a['fund']:.0f}!={b['fund']:.0f}")
    print(f"baseline parity mismatches {len(mismatches)} {mismatches[:8]}", file=sys.stderr)

    stages_cfg = [
        ("0_baseline", {**base_cfg, "baseline_path": True}),
        ("1_rooms_off", {**base_cfg, "rooms_hard": False, "rooms_penalty": 0}),
        ("1b_rooms_soft4", {**base_cfg, "rooms_hard": False, "rooms_penalty": 4}),
        (
            "2a_period_x124",
            {**base_cfg, "rooms_hard": False, "months": "1", "auto_period": True, "multiplier": 1.24},
        ),
    ]
    # Preliminary stages, then the calibrated ones once k is known.
    preds = {}
    for name, cfg in stages_cfg:
        print(f"run {name}", file=sys.stderr)
        preds[name] = run_stage(records, cases, cfg, geo)

    ratios_2a = [ratio for _cid, ratio in registry_ratios(real, preds["2a_period_x124"], use_coef=False)]
    k2 = round_half_up(statistics.median(ratios_2a), 2)
    k2_raw = statistics.median(ratios_2a)
    print(f"k2 raw {k2_raw:.4f} rounded {k2:.2f}", file=sys.stderr)

    cfg_2b = {
        **base_cfg,
        "rooms_hard": False,
        "months": "1",
        "auto_period": True,
        "multiplier": k2,
    }
    print("run 2b_period_calibrated", file=sys.stderr)
    preds["2b_period_calibrated"] = run_stage(records, cases, cfg_2b, geo)

    cfg_3 = {**cfg_2b, "floor_score": False, "floor_coef": True, "multiplier": 1.0}
    # multiplier 1 so `total` is A and `coef` is the floor factor; fund = A * coef.
    print("run 3_components", file=sys.stderr)
    preds["3_components"] = run_stage(records, cases, cfg_3, geo)
    ratios_3 = [ratio for _cid, ratio in registry_ratios(real, preds["3_components"], use_coef=True)]
    k3 = round_half_up(statistics.median(ratios_3), 2)
    k3_raw = statistics.median(ratios_3)
    print(f"k3 raw {k3_raw:.4f} rounded {k3:.2f}", file=sys.stderr)

    # Each step keeps the multiplier that belongs to that comp set:
    # the median Module/(A*coef) on the 22 real certificates, rounded to 2 decimals.
    preds["3_floor"] = run_stage(records, cases, {**cfg_3, "multiplier": k3}, geo)
    preds["3_floor_k2"] = run_stage(records, cases, {**cfg_3, "multiplier": k2}, geo)
    k4 = k4_raw = None
    loo4 = None
    if geo is not None:
        print("run 4_geo", file=sys.stderr)
        preds["4_geo"] = run_stage(records, cases, {**cfg_3, "multiplier": 1.0, "geo": True}, geo)
        ratios_4 = [ratio for _cid, ratio in registry_ratios(real, preds["4_geo"], use_coef=True)]
        k4_raw = statistics.median(ratios_4)
        k4 = round_half_up(k4_raw, 2)
        print(f"k4 raw {k4_raw:.4f} rounded {k4:.2f}", file=sys.stderr)
        preds["4_shipped"] = run_stage(
            records, cases, {**cfg_3, "multiplier": k4, "geo": True}, geo
        )
        loo4 = loo_summary(real, preds["4_geo"], use_coef=True)
    shipped_k = k4 if k4 is not None else k3

    lines = []
    lines.append("# Valuation error against the SPFU Module")
    lines.append("")
    lines.append("Error = forecast / Module value - 1. The forecast is result B on the site.")
    lines.append("MdAPE is the median absolute percentage error. Mean bias is the mean signed percentage error. Median bias is shown as well because the original review reported it.")
    lines.append("Within ±25% uses scored certificates only. A run with no comparables is a failure and is outside that share.")
    lines.append("")
    lines.append("Real set: 23 parsed certificates. R23, Ozerne, Aviation Street, is outside the Zhytomyr registry and is excluded. 22 remain, including the same object entered with a different room count, floor or date.")
    lines.append("Sensitivity file `dovidky_sensitivity_unique.csv` contains 19 rows, not 20. The broader extract has one extra exact duplicate of the 45 m2 certificate, and that duplicate is not in this file. S01 and S03 are exact duplicates of each other and are both kept. S08 (`is_typo=1`, area 70, 9 rooms, 70 floors) is excluded from the headline and listed alone.")
    lines.append("The multiplier is a single constant rounded to 2 decimals: the median of Module / (registry x floor coefficient) on the 22 real certificates. The sensitivity apartment is not used to choose it. Leave-one-out uses the unrounded median of the other 21.")
    lines.append("")
    lines.append(f"Step 2 median Module/A: {k2_raw:.4f}, rounded {k2:.2f}.")
    lines.append(f"Step 3 median Module/(A x floor coef): {k3_raw:.4f}, rounded {k3:.2f}.")
    if k4 is not None:
        lines.append(f"Step 4 median Module/(A x floor coef) with the radius: {k4_raw:.4f}, rounded {k4:.2f}.")
    lines.append(f"Constant used by the site: {shipped_k:.2f}.")
    lines.append("")

    def add_block(title, case_list, key, summary=None):
        summary = summary or summarize(case_list, preds[key])
        lines.append(row_line(title, summary))
        return summary

    def add_loo(title, loo, n_cases):
        lines.append(row_line(title, {
            "n": n_cases,
            "scored": loo["n"],
            "failed": n_cases - loo["n"],
            "mdape": loo["mdape"],
            "mean_bias": loo["mean_bias"],
            "median_bias": loo["median_bias"],
            "within_25": loo["within_25"],
        }))

    lines.append("")
    lines.append("## Real certificates (22)")
    lines.append("")
    lines.append("| Step | Scored | Fail | MdAPE | Mean bias | Median bias | Within ±25% |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for title, key in (
        ("0. Baseline B = A x 1.24, 12 months, rooms hard", "0_baseline"),
        ("1. Rooms removed from the hard filter", "1_rooms_off"),
        ("1b. Rooms as a soft score, penalty 4 (not shipped)", "1b_rooms_soft4"),
        ("2a. Auto period from 1 month, still x1.24", "2a_period_x124"),
        (f"2. Auto period, fixed x{k2:.2f}", "2b_period_calibrated"),
        (f"3. Floor coef 0.8646, floor score off, x{k3:.2f}", "3_floor"),
    ):
        add_block(title, real, key)
    loo2 = loo_summary(real, preds["2a_period_x124"], use_coef=False)
    loo3 = loo_summary(real, preds["3_components"], use_coef=True)
    add_loo("2 LOO unrounded k", loo2, len(real))
    add_loo("3 LOO unrounded k", loo3, len(real))
    if "4_shipped" in preds:
        add_block(f"4. Radius, previous x{k3:.2f} (not the shipped constant)", real, "4_geo")
        # 4_geo was run with multiplier 1 so its fund is A*coef, not a forecast.
        # Report the previous-k and the refit-k forecasts explicitly.
        lines[-1] = row_line(
            f"4a. Radius, hold x{k3:.2f}",
            fixed_k_summary(real, preds["4_geo"], k3, use_coef=True),
        )
        add_block(f"4. Radius, refit x{k4:.2f} (shipped)", real, "4_shipped")
        add_loo("4 LOO unrounded k", loo4, len(real))

    lines.append("")
    lines.append("## Sensitivity, Kroshenska 45 (typo excluded)")
    lines.append("")
    lines.append("| Step | Scored | Fail | MdAPE | Mean bias | Median bias | Within ±25% |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for title, key in (
        ("0. Baseline", "0_baseline"),
        ("1. Rooms off", "1_rooms_off"),
        ("2. Auto period, calibrated", "2b_period_calibrated"),
        (f"3. Floor coef x{k3:.2f}", "3_floor"),
    ):
        add_block(title, sens, key)
    if "4_shipped" in preds:
        lines.append(row_line(
            f"4a. Radius, hold x{k3:.2f}",
            fixed_k_summary(sens, preds["4_geo"], k3, use_coef=True),
        ))
        add_block(f"4. Radius, refit x{k4:.2f} (shipped)", sens, "4_shipped")

    lines.append("")
    lines.append("## Typo row, excluded from the headline")
    lines.append("")
    lines.append("Inputs are area 70 m2, 9 rooms, 70 floors. The Module still returned a value. It is not a real apartment description, so it does not enter MdAPE.")
    lines.append("")
    lines.append("| Step | Pred, UAH | Error | Used | Scope |")
    lines.append("|---|---:|---:|---:|---|")
    for key in ("0_baseline", "1_rooms_off", "2b_period_calibrated", "3_floor", "4_shipped"):
        if key not in preds:
            continue
        pred = preds[key][typo[0]["case_id"]]
        if not pred.get("ok"):
            lines.append(f"| {key} | fail | n/a | {pred.get('used', 0)} | {pred.get('scope', '')} |")
            continue
        module = float(typo[0]["value_uah"])
        err = (pred["fund"] / module - 1) * 100
        lines.append(
            f"| {key} | {pred['fund']:.0f} | {err:+.1f}% | {pred['used']} | {pred['scope']} |"
        )

    lines.append("")
    lines.append("## Periods actually used (real, shipped model)")
    lines.append("")
    scopes = {}
    scope_key = "4_shipped" if "4_shipped" in preds else "3_floor"
    for case in real:
        pred = preds[scope_key][case["case_id"]]
        scopes.setdefault(str(pred.get("scope")), 0)
        scopes[str(pred.get("scope"))] += 1
    for scope, count in sorted(scopes.items(), key=lambda kv: kv[0]):
        word = "month" if scope == "1" else "months"
        lines.append(f"- {scope} {word}: {count}")

    if geo is not None and "4_geo" in preds:
        lines.append("")
        lines.append("## Geocoding (real, step 4)")
        lines.append("")
        how = {}
        radii = {}
        for case in real:
            pred = preds["4_shipped"][case["case_id"]]
            how[pred.get("geo") or "?"] = how.get(pred.get("geo") or "?", 0) + 1
            label = "city" if pred.get("radius_m") is None else f"{pred['radius_m']:.0f} m"
            radii[label] = radii.get(label, 0) + 1
        lines.append("Subject point source: " + ", ".join(f"{k} {v}" for k, v in sorted(how.items())))
        lines.append("")
        lines.append("Radius kept: " + ", ".join(f"{k} {v}" for k, v in sorted(radii.items())))
        # Geocode error versus the certificate coordinates, for audit only.
        errs_m = []
        for case in real:
            point, how_one = model.subject_point(
                model.target_from_case(case, "1"), geo
            )
            if point and case.get("lat") and case.get("lon"):
                errs_m.append(
                    model._point_segment_m(
                        float(case["lat"]),
                        float(case["lon"]),
                        point,
                        point,
                    )
                )
        if errs_m:
            lines.append("")
            lines.append(
                f"Distance from the gazetteer point to the certificate coordinate: "
                f"median {statistics.median(errs_m):.0f} m, max {max(errs_m):.0f} m, n {len(errs_m)}."
            )

    lines.append("")
    lines.append("## Leave-one-out stability")
    lines.append("")
    lines.append(
        f"Step 2 folds rounded to 2 decimals: {', '.join(f'{k:.2f}' for k in loo2['k_rounded_set'])}."
    )
    lines.append(
        f"Step 3 folds rounded to 2 decimals: {', '.join(f'{k:.2f}' for k in loo3['k_rounded_set'])}."
    )
    if loo4 is not None:
        lines.append(
            f"Step 4 folds rounded to 2 decimals: {', '.join(f'{k:.2f}' for k in loo4['k_rounded_set'])}."
        )
    lines.append("")
    lines.append("## Asking prices")
    lines.append("")
    lines.append("Not used. `listings_data.json` is one matrix dated 2026-11-02 plus a citywide monthly median. Many months before 2026 are flagged low (often n under 30, and several n=1). There is no per-listing history with coordinates, so the matrix cannot be applied to a June-October certificate without using a later price level. No other open asking-price history is in the repository.")
    lines.append("")
    lines.append("## Restore")
    lines.append("")
    lines.append("The previous calculator is tag `baseline-calc-v11.10` on commit 01ccb589b75c6c1a224d34b1438c2cac20618d7d. Restore the page with `git checkout baseline-calc-v11.10 -- index.html`.")
    lines.append("")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines)
    (OUT_DIR / "metrics.md").write_text(text, encoding="utf-8")
    ordered = [
        ("0_baseline", preds["0_baseline"]),
        ("1_rooms_off", preds["1_rooms_off"]),
        ("1b_rooms_soft4", preds["1b_rooms_soft4"]),
        ("2a_period_x124", preds["2a_period_x124"]),
        ("2b_period_calibrated", preds["2b_period_calibrated"]),
        ("3_floor_k2", preds["3_floor_k2"]),
        ("3_floor", preds["3_floor"]),
    ]
    if "4_shipped" in preds:
        for pred in preds["4_geo"].values():
            if pred.get("ok"):
                pred["fund"] = pred["total"] * (pred.get("coef") or 1.0) * k3
        ordered.append(("4_prev_k", preds["4_geo"]))
        ordered.append(("4_shipped", preds["4_shipped"]))
    write_case_csv(OUT_DIR / "results.csv", cases, ordered)
    meta = {
        "k2_raw": k2_raw,
        "k2": k2,
        "k3_raw": k3_raw,
        "k3": k3,
        "k4_raw": k4_raw,
        "k4": k4,
        "shipped_k": shipped_k,
        "baseline_mismatches": mismatches,
        "loo2_mdape": loo2["mdape"],
        "loo3_mdape": loo3["mdape"],
        "loo4_mdape": None if loo4 is None else loo4["mdape"],
    }
    (OUT_DIR / "calibration.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(text)
    print("WROTE", OUT_DIR / "metrics.md", file=sys.stderr)


if __name__ == "__main__":
    main()
