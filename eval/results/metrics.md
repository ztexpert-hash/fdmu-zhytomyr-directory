# Valuation error against the SPFU Module

Error = forecast / Module value - 1. The forecast is result B on the site.
MdAPE is the median absolute percentage error. Mean bias is the mean signed percentage error. Median bias is shown as well because the original review reported it.
Within ±25% uses scored certificates only. A run with no comparables is a failure and is outside that share.

Real set: 23 parsed certificates. R23, Ozerne, Aviation Street, is outside the Zhytomyr registry and is excluded. 22 remain, including the same object entered with a different room count, floor or date.
Sensitivity file `dovidky_sensitivity_unique.csv` contains 19 rows, not 20. The broader extract has one extra exact duplicate of the 45 m2 certificate, and that duplicate is not in this file. S01 and S03 are exact duplicates of each other and are both kept. S08 (`is_typo=1`, area 70, 9 rooms, 70 floors) is excluded from the headline and listed alone.
The multiplier is a single constant rounded to 2 decimals: the median of Module / (registry x floor coefficient) on the 22 real certificates. The sensitivity apartment is not used to choose it. Leave-one-out uses the unrounded median of the other 21.

Step 2 median Module/A: 1.3282, rounded 1.33.
Step 3 median Module/(A x floor coef): 1.3660, rounded 1.37.
Step 4 median Module/(A x floor coef) with the radius: 1.3022, rounded 1.30.
Constant used by the site: 1.30.


## Real certificates (22)

| Step | Scored | Fail | MdAPE | Mean bias | Median bias | Within ±25% |
|---|---:|---:|---:|---:|---:|---:|
| 0. Baseline B = A x 1.24, 12 months, rooms hard | 20/22 | 2 | 16.7% | -15.2% | -16.7% | 85.0% |
| 1. Rooms removed from the hard filter | 22/22 | 0 | 17.7% | -15.4% | -17.7% | 90.9% |
| 1b. Rooms as a soft score, penalty 4 (not shipped) | 22/22 | 0 | 17.7% | -15.1% | -17.7% | 90.9% |
| 2a. Auto period from 1 month, still x1.24 | 22/22 | 0 | 10.0% | -6.2% | -6.6% | 100.0% |
| 2. Auto period, fixed x1.33 | 22/22 | 0 | 7.0% | +0.6% | +0.1% | 95.5% |
| 3. Floor coef 0.8646, floor score off, x1.37 | 22/22 | 0 | 6.8% | +1.5% | +0.3% | 95.5% |
| 2 LOO unrounded k | 22/22 | 0 | 7.2% | +0.5% | +0.0% | 95.5% |
| 3 LOO unrounded k | 22/22 | 0 | 7.6% | +1.3% | +0.0% | 95.5% |
| 4a. Radius, hold x1.37 | 22/22 | 0 | 6.0% | +6.1% | +5.2% | 95.5% |
| 4. Radius, refit x1.30 (shipped) | 22/22 | 0 | 6.2% | +0.7% | -0.2% | 95.5% |
| 4 LOO unrounded k | 22/22 | 0 | 6.1% | +0.8% | +0.0% | 95.5% |

## Sensitivity, Kroshenska 45 (typo excluded)

| Step | Scored | Fail | MdAPE | Mean bias | Median bias | Within ±25% |
|---|---:|---:|---:|---:|---:|---:|
| 0. Baseline | 18/18 | 0 | 19.9% | -18.8% | -19.9% | 88.9% |
| 1. Rooms off | 18/18 | 0 | 20.4% | -18.3% | -20.4% | 94.4% |
| 2. Auto period, calibrated | 18/18 | 0 | 9.8% | -8.1% | -9.6% | 100.0% |
| 3. Floor coef x1.37 | 18/18 | 0 | 9.0% | -5.6% | -9.0% | 100.0% |
| 4a. Radius, hold x1.37 | 18/18 | 0 | 0.8% | -0.7% | -0.4% | 100.0% |
| 4. Radius, refit x1.30 (shipped) | 18/18 | 0 | 5.5% | -5.8% | -5.5% | 100.0% |

## Typo row, excluded from the headline

Inputs are area 70 m2, 9 rooms, 70 floors. The Module still returned a value. It is not a real apartment description, so it does not enter MdAPE.

| Step | Pred, UAH | Error | Used | Scope |
|---|---:|---:|---:|---|
| 0_baseline | fail | n/a | 0 | 12 |
| 1_rooms_off | 3062083 | -25.1% | 77 | 12 |
| 2b_period_calibrated | 3598163 | -12.0% | 32 | 1 |
| 3_floor | 3560805 | -13.0% | 18 | 1 |
| 4_shipped | 3486537 | -14.8% | 6 | 1 |

## Periods actually used (real, shipped model)

- 1 month: 20
- 2 months: 1
- 4 months: 1

## Geocoding (real, step 4)

Subject point source: building 21, nearby-house 1

Radius kept: 400 m 12, 480 m 1, 576 m 3, 691 m 4, 829 m 1, 995 m 1

Distance from the gazetteer point to the certificate coordinate: median 3 m, max 211 m, n 22.

## Leave-one-out stability

Step 2 folds rounded to 2 decimals: 1.33.
Step 3 folds rounded to 2 decimals: 1.36, 1.37.
Step 4 folds rounded to 2 decimals: 1.30.

## Asking prices

Not used. `listings_data.json` is one matrix dated 2026-11-02 plus a citywide monthly median. Many months before 2026 are flagged low (often n under 30, and several n=1). There is no per-listing history with coordinates, so the matrix cannot be applied to a June-October certificate without using a later price level. No other open asking-price history is in the repository.

## Restore

The previous calculator is tag `baseline-calc-v11.10` on commit 01ccb589b75c6c1a224d34b1438c2cac20618d7d. Restore the page with `git checkout baseline-calc-v11.10 -- index.html`.
