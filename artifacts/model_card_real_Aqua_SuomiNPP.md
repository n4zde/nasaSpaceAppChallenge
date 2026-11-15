# Model card: Suomi-NPP → Aqua harmonisation

* Generated: 2026-11-14T19:05:36Z  
* Data origin: **REAL** (imported data)  
* Method: ember-method-1.0.0 (config fingerprint 19f09ad1134cfb29), app 0.1.0  
* Area: Pilot: Türkiye (1311874.2 km²), grid 18 km, weekly
* Status: **Research estimate (coverage-unadjusted, not validated)** (`research_estimate`). Held-out agreement checks passed, but no valid-observation coverage was available, so differences in cloud/swath coverage are absorbed into the ratio (confounded). Use as an exploratory estimate only.

## Intended use

Express weekly active-fire detection activity of the source platform on the reference platform's scale, for historical calendars, seasonal baselines and cross-sensor continuity. Not for burned-area estimation, fire counting, or forecasting.

## Target

Aqua-equivalent quality-filtered detections per week (coverage assumed comparable between platforms)

## Model

Paired conditional rate-ratio model (seasonal + land cover); seasonal Fourier order 2; land-cover classes []; dispersion 1.18 (cell-week), 6.67 (week totals); 200 block-bootstrap replicates.

| Term | Estimate | SE (quasi) |
|---|---|---|
| intercept (log ratio) | 1.872 | 0.016 |
| sin 1/yr | -0.027 | 0.024 |
| cos 1/yr | -0.350 | 0.019 |
| sin 2/yr | 0.124 | 0.018 |
| cos 2/yr | -0.147 | 0.019 |

## Training data

Overlap 2018-01-01 to 2020-12-23 (3 years), 24185 paired cell-weeks with detections, 11193 reference and 77406 source quality detections, mode `hotspot_only`.

## Held-out evaluation

Measures: agreement with held-out reference-platform observations (not independent truth).

### temporal blocks

90% predictive-interval coverage: 0.897

| Method | MAE (week) | RMSE (week) | Deviance (week) | Rel. bias | Deviance (cell-week) |
|---|---|---|---|---|---|
| Raw substitution (source detections treated as reference detections) | 424.4 | 596.4 | 585.305 | 5.916 | 0.289 |
| Single global ratio (train totals) | 24.0 | 42.1 | 14.382 | 0.005 | 0.142 |
| Paired conditional rate-ratio model (seasonal + land cover) | 19.7 | 31.6 | 9.607 | 0.014 | 0.141 |
| Negative-binomial regression calibration | 860.9 | 3041.8 | 1487.874 | 11.941 | 0.515 |

### spatial blocks

90% predictive-interval coverage: 0.934

| Method | MAE (week) | RMSE (week) | Deviance (week) | Rel. bias | Deviance (cell-week) |
|---|---|---|---|---|---|
| Raw substitution (source detections treated as reference detections) | 106.1 | 165.3 | 148.054 | 5.916 | 0.289 |
| Single global ratio (train totals) | 7.3 | 12.7 | 5.222 | 0.022 | 0.142 |
| Paired conditional rate-ratio model (seasonal + land cover) | 6.2 | 10.7 | 4.066 | 0.001 | 0.141 |
| Negative-binomial regression calibration | 359.1 | 3411.3 | 662.855 | 19.910 | 0.785 |

## Gates

* PASS: temporal: beats raw_substitution on held-out AOI-week deviance (9.61 vs 585)
* PASS: temporal: beats global_ratio on held-out AOI-week deviance (9.61 vs 14.4)
* PASS: temporal: 90% predictive interval coverage within [0.8, 0.97] (0.897)
* PASS: spatial: beats raw_substitution on held-out AOI-week deviance (4.07 vs 148)
* PASS: spatial: beats global_ratio on held-out AOI-week deviance (4.07 vs 5.22)
* PASS: spatial: 90% predictive interval coverage within [0.8, 0.97] (0.934)

## Diagnostics

* Activity dependence of the ratio: -0.076 ± 0.008 (flagged)
* Drift of the log ratio per year: 0.113 ± 0.013 (flagged)
* Zero check: Not assessable: without valid-observation coverage, zero cell-weeks cannot be distinguished from unobserved ones.

## Warnings

* Significant drift of the sensor ratio across the overlap: transfer outside the overlap is unreliable.
* The sensor ratio depends on activity level: constant-ratio conversion may be biased for very large or very small events.
* No valid-observation coverage: coverage differences between platforms are absorbed into the ratio, and zero weeks cannot be distinguished from unobserved weeks.

## Limitations

* Validation measures agreement with the reference platform, not accuracy against independent fire observations.
* Conversion outside the overlap assumes a stationary sensor ratio.
* Without valid-observation coverage, coverage differences are confounded with sensitivity differences.
* The ratio may depend on fire size; see the activity-dependence diagnostic.
