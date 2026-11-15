# Model card: Suomi-NPP → Aqua harmonisation

* Generated: 2026-11-15T00:56:02Z  
* Data origin: **REAL** (imported data)  
* Method: ember-method-1.0.0 (config fingerprint 8a59e828fabaaa64), app 0.1.0  
* Area: Pilot: Türkiye (1311874.2 km²), grid 18 km, weekly
* Status: **Unvalidated candidate calibration** (`not_evaluated`). The calibration has been fitted but blocked hold-out validation has not been run for this selection. Treat converted values as provisional.

## Intended use

Express weekly active-fire detection activity of the source platform on the reference platform's scale, for historical calendars, seasonal baselines and cross-sensor continuity. Not for burned-area estimation, fire counting, or forecasting.

## Target

Aqua-equivalent quality-filtered detections per week (coverage assumed comparable between platforms)

## Model

Paired conditional rate-ratio model (seasonal + land cover); seasonal Fourier order 2; land-cover classes []; dispersion 1.32 (cell-week), 10.56 (week totals); 200 block-bootstrap replicates.

| Term | Estimate | SE (quasi) |
|---|---|---|
| intercept (log ratio) | 1.769 | 0.011 |
| sin 1/yr | -0.053 | 0.018 |
| cos 1/yr | -0.312 | 0.014 |
| sin 2/yr | 0.121 | 0.014 |
| cos 2/yr | -0.210 | 0.014 |

## Training data

Overlap 2018-01-01 to 2025-12-24 (5 years), 41627 paired cell-weeks with detections, 21452 reference and 133891 source quality detections, mode `hotspot_only`.

## Held-out evaluation

Measures: agreement with held-out reference-platform observations (not independent truth).

### temporal blocks

90% predictive-interval coverage: 0.865

| Method | MAE (week) | RMSE (week) | Deviance (week) | Rel. bias | Deviance (cell-week) |
|---|---|---|---|---|---|
| Raw substitution (source detections treated as reference detections) | 444.5 | 635.7 | 576.932 | 4.506 | 0.318 |
| Single global ratio (train totals) | 37.5 | 58.6 | 25.812 | 0.015 | 0.165 |
| Paired conditional rate-ratio model (seasonal + land cover) | 37.5 | 58.6 | 25.812 | 0.015 | 0.165 |
| Negative-binomial regression calibration | 1574.9 | 4438.2 | 2781.743 | 15.882 | 0.861 |

### spatial blocks

90% predictive-interval coverage: 0.940

| Method | MAE (week) | RMSE (week) | Deviance (week) | Rel. bias | Deviance (cell-week) |
|---|---|---|---|---|---|
| Raw substitution (source detections treated as reference detections) | 111.2 | 177.7 | 146.251 | 4.506 | 0.318 |
| Single global ratio (train totals) | 10.3 | 17.3 | 8.198 | 0.023 | 0.165 |
| Paired conditional rate-ratio model (seasonal + land cover) | 9.0 | 15.6 | 6.779 | 0.038 | 0.164 |
| Negative-binomial regression calibration | 885.6 | 7949.5 | 1684.463 | 35.766 | 1.790 |

## Gates

* PASS: temporal: beats raw_substitution on held-out AOI-week deviance (25.8 vs 577)
* PASS: temporal: beats global_ratio on held-out AOI-week deviance (25.8 vs 25.8)
* PASS: temporal: 90% predictive interval coverage within [0.8, 0.97] (0.865)
* PASS: spatial: beats raw_substitution on held-out AOI-week deviance (6.78 vs 146)
* PASS: spatial: beats global_ratio on held-out AOI-week deviance (6.78 vs 8.2)
* PASS: spatial: 90% predictive interval coverage within [0.8, 0.97] (0.940)

## Diagnostics

* Activity dependence of the ratio: -0.012 ± 0.006 (not flagged)
* Drift of the log ratio per year: -0.029 ± 0.003 (not flagged)
* Zero check: Not assessable: without valid-observation coverage, zero cell-weeks cannot be distinguished from unobserved ones.

## Warnings

* No valid-observation coverage: coverage differences between platforms are absorbed into the ratio, and zero weeks cannot be distinguished from unobserved weeks.

## Limitations

* Validation measures agreement with the reference platform, not accuracy against independent fire observations.
* Conversion outside the overlap assumes a stationary sensor ratio.
* Without valid-observation coverage, coverage differences are confounded with sensitivity differences.
* The ratio may depend on fire size; see the activity-dependence diagnostic.
