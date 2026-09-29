# Verification record: weighting-only revision

Date: 2026-09-29. The protocol was set before this comparison; no tuning or model search was run.

## Environment and data

CPU only, Linux, Python 3.12.14, pinned packages in requirements.txt. Both models requested 25 threads. The audited Step 5 reference data has 6,209 training rows, 2,370 validation rows and 1,997 reserved test rows, across 20 designs (12/4/4). The user's Codespace files were not available here, so these are reference results, not a claim about the user's own run.

## Measured results

| Metric | Baseline | Weighting only |
|---|---:|---:|
| Training-stage time, seconds | 1.327 | 0.897 |
| Trees trained and used | 500 | 500 |
| Train macro mae_ns | 0.012184 | 0.011882 |
| Validation macro mae_ns | 1.589510 | 1.435412 |
| Validation macro rmse_ns | 1.890989 | 1.905142 |
| Validation macro r2 | 0.523270 | -0.354987 |
| Validation macro pearson_r | 0.903510 | 0.926106 |
| Validation macro spearman_r | 0.658507 | 0.650308 |

Validation macro MAE fell by 9.69%. Only two of four designs improved; RMSE and R² worsened. This is a tradeoff, not uniform improvement. Macro R² can be negative. The selected model is weighted because the predeclared selection metric is macro MAE. No claim of significance is made from correlated register rows.

| Design | Baseline MAE (ns) | Weighted MAE (ns) |
|---|---:|---:|
| osu_riscv32i | 1.295320 | 1.401057 |
| divider32 | 0.084927 | 0.345498 |
| chacha | 4.845644 | 3.872372 |
| i2c_master | 0.132148 | 0.122719 |

Most aggregate benefit comes from ChaCha; osu_riscv32i and divider32 worsen. The previous combined candidate is archived separately. Its result does not measure weighting alone.

Training-stage time includes the baseline adapter construction but excludes preflight, subprocess startup, scoring and plotting. It is not a hardware-independent performance guarantee.

## Checks completed

- Seven focused unit tests passed, including equal design totals, macro versus pooled metrics, upstream input preservation, and identical model constructors/input rows/targets with sample_weight as the sole fit difference. Thread overrides of 25 and 2 were checked in the recording-estimator test.
- Real preflight, baseline, weighted and validation comparison stages passed. Both estimators trained all 500 rounds with no early stopping.
- Requested estimator settings match. Resolved booster configurations also match after excluding the automatically fitted base_score. This learned intercept is allowed to respond to weights.
- Reloaded model predictions reproduce the saved validation predictions exactly as float32 values.
- In an isolated temporary copy, final-test was exercised with a mocked 12-row frame drawn from validation data. It generated reports, reused verified outputs without another read, and blocked further training after locking. This was a workflow check, not evaluation on real held-out designs.
- The validation comparison plot was rendered and visually inspected.

The real reference test CSV was hashed and copied for provenance, but its feature/label rows were not parsed or evaluated. No real test scores are included. Run final-test on your own experiment only when model development is complete.

reference_validation/ contains the source/environment manifest, selection, logs, model result JSON, resolved configurations, training weight audit, validation predictions and comparison reports. Model binaries, adapter pickles and data snapshots are omitted to keep the archive small; your actual run generates them.
