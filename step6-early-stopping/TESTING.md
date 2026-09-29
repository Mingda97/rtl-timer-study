# Validation and preservation checks

Date: 2026-09-29. Search cap 2,000 and patience 50 were declared before this run; no search across other patience values or estimator settings was performed.

## Environment and scope

Linux, Python 3.12.14, pinned packages from requirements.txt, CPU only, 25 threads. The audited reference dataset contains 6,209 training rows, 2,370 validation rows and 1,997 reserved test rows (12/4/4 designs; 25 input features). These are reference-data results, not the user's own Codespace results.

## Round selection and refit

- Best zero-based iteration: 4; selected trees: 5.
- Actual search rounds: 55; cap: 2000; patience: 50.
- Weighted validation MAE reported by XGBoost: 1.259466684 ns.
- Independently computed validation macro MAE: 1.259466656 ns.
- Search fit time: 0.528 seconds; fresh refit fit time: 0.030 seconds.
- Maximum train/validation prediction difference between refit and selected search prefix: 0 ns.
- No cap expansion or further training configuration was attempted.

Fit-stage times exclude subprocess startup, preflight, copying prior results, scoring and reporting. Previous model fit times in imported result files are historical; those models were not retrained.

## Validation comparison

| Model | Trees | Macro MAE (ns) | Macro RMSE (ns) | Macro R² |
|---|---:|---:|---:|---:|
| baseline | 500 | 1.589510 | 1.890989 | 0.523270 |
| weighted | 500 | 1.435412 | 1.905142 | -0.354987 |
| weighted_es | 5 | 1.259467 | 1.759340 | -0.249699 |

New macro MAE is 12.26% lower than weighting-only 500 trees, and 20.76% lower than the unweighted baseline. The validation-selected model is weighted_es.

| Design | Baseline MAE (ns) | Weighted-500 MAE (ns) | Weighted early-stop/refit MAE (ns) |
|---|---:|---:|---:|
| osu_riscv32i | 1.295320 | 1.401057 | 0.060903 |
| divider32 | 0.084927 | 0.345498 | 0.452800 |
| chacha | 4.845644 | 3.872372 | 4.403143 |
| i2c_master | 0.132148 | 0.122719 | 0.121021 |

Relative to weighted-500, osu_riscv32i improves strongly, i2c_master improves slightly, and divider32 and chacha worsen. Macro R² remains negative; divider32 R² is about -3.149 in the new model. A lower aggregate MAE does not establish improvement for every design or metric. Validation was used to choose tree count, so this is a development score.

## Verification completed

- Seven focused tests passed: valid negative R²; weighted validation MAE equals macro MAE; correct zero-based conversion and fresh refit using only training rows; protected output paths; no unexpected depth changes; selection before test reads; training/search closure after test lock.
- Real preflight, import-previous, select-rounds, refit and compare stages all passed.
- The entire previous run file inventory and all SHA256 digests were identical before and after this experiment. Old model/result files were not rewritten.
- The independent macro-MAE calculation agrees with the weighted metric used by early stopping within floating-point tolerance.
- Reloading the new saved model reproduces its recorded validation predictions exactly as float32 values.
- New model validation predictions also exactly equal the first five trees of the previously saved weighted-500 model, consistent with keeping the learning setup fixed.
- A mocked final-test workflow in an isolated temporary directory used 12 rows drawn from validation (three per design). It produced three-model reports, reused verified outputs without another read, and blocked subsequent fitting. Those mock metrics are not reported as real test results.

The real held-out test CSV was copied and hashed for integrity only; its rows were not parsed or evaluated. No real test metrics are included.

reference_validation/ preserves the manifest, old-result import metadata, three-model comparison, per-design metrics, validation predictions, search history/selection, resolved model configurations and logs. It omits model binaries and input snapshots; the user's run generates those.

## Preservation evidence

Verified unchanged inventory and contents of all 53 files in the previous reference run. See reference_validation/preservation_check.json.
