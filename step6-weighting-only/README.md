# Step 6: isolate equal-design training weights

This revision corrects the earlier combined experiment. It compares the
upstream baseline with a candidate that changes **only training sample
weights**. Both use the same 25 features, target, frozen designs, row order,
XGBoost version, constructor parameters, CPU threads, and all 500 trees.
Neither uses early stopping. Hyperparameter tuning is deferred.

The archive extracts to `step6-weighting-only`, alongside the existing
`step6-training` folder. The earlier combined experiment's code, configuration,
and reference results are retained under `legacy_combined_experiment/` for
provenance. They are not executed by this revision.

Read `TRAIN_PY_WALKTHROUGH.md` for the detailed code and interview explanation.
Read `TESTING.md` for the measured reference results and checks.

## Run in your existing VSCode Codespace

Upload `step6-weighting-only.zip` to `/workspaces/codespaces-blank`, then run:

```bash
cd /workspaces/codespaces-blank
python3 -m zipfile -e step6-weighting-only.zip .
cd step6-weighting-only

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v

python scripts/train.py preflight
python scripts/train.py baseline --timeout 1200
python scripts/train.py weighted --timeout 1200
python scripts/train.py compare
```

Alternatively, if the earlier Step 6 virtual environment is already installed,
activate `../step6-training/.venv/bin/activate` instead of creating a new one.
The pinned requirements have not changed. In VSCode use **Python: Select
Interpreter** to select the environment you activated. A GPU is not needed
for these reference-sized datasets; both fits here used CPU.

The default input is the completed, audited Step 5 run:

```text
/workspaces/codespaces-blank/step5-features/runs/run2
```

If your audited run has a different name, supply `--input-run /absolute/path/to/run`
on **every** command. Similarly, repeat any `--out` or `--threads` override on
every command. The default thread setting remains 25 to match upstream.

Do not re-extract features or rerun synthesis for this experiment. Step 6
consumes Step 5's audited CSVs. If preflight says a file changed or the audit
is missing, resolve that upstream issue; do not remove checksum checks.

## Read your validation results

The default output folder is `runs/weighting_only`. In VSCode open:

| File within that folder | What it tells you |
|---|---|
| `validation/REPORT.md` | Short comparison and selected model |
| `validation/summary.csv` | Macro and pooled metrics |
| `validation/per_design_comparison.csv` | Which individual designs improved or worsened |
| `validation/mae_by_design.png` | Per-design error chart |
| `models/weighted/design_weights.csv` | Row counts, weights and equal total design contributions |
| `models/baseline/result.json` and `models/weighted/result.json` | Training/validation metrics, requested parameters, tree counts |
| `models/*/booster_config.json` | Resolved XGBoost defaults and fitted configuration |
| `models/*/validation_predictions.csv` | Actual mapped arrival, prediction and error for each endpoint |
| `models/*/model.ubj` | Saved model for inference |
| `selection.json` | Choice based only on validation macro MAE |
| `run_manifest.json` | Dataset, code and software fingerprints |

The primary metric is the arithmetic average of four per-design MAEs.
Lower is better. The weighted model is not assumed to win, and other metrics
may worsen even if this primary metric improves. No hyperparameter search
runs automatically.

## Final test, when model development is finished

If you still intend to tune regularization using validation, stop after
`compare`. When you are ready to finish this two-model experiment, run:

```bash
python scripts/train.py final-test
```

This verifies the saved models and validation-based choice, writes an
evaluation lock, then reads the four held-out test designs and evaluates
both fixed models. It saves `test/REPORT.md`, summary and per-design tables,
prediction CSVs and a plot. It does not refit on train+validation or select
a model from the test results. Further training in that output folder is
blocked. Creating another folder does not make already-viewed test labels
unseen; subsequent changes would need to be disclosed as exploratory.

If you already evaluated these test designs using the earlier combined
experiment, state that when reporting this revised comparison. The new
folder preserves files, not statistical independence from earlier knowledge.

## Scope of the upstream baseline

The package preserves the unmodified `training()` function from
`third_party/rtl_timer/train_infer_k_fold_BOG.slack.py`, pinned to commit
`206ff4078368c251d2fafaffcc648282c68316f1` of
<https://github.com/hkust-zhiyao/rtl-timer>.

Only that function executes. Its original leave-one-design-out driver and
metrics do not replace our frozen 12/4/4 design split and evaluation code.
The function receives our data through an adapter. The separate upstream
`train_infer_BOG.slack.py` is retained for inspection; its training loop
resets the accumulated rows inside the per-design loop, so it is not used.

This is an upstream-code baseline on our dataset. It does not reproduce
the full paper's multi-representation ensemble, path sampling, max-loss
training, signal aggregation or downstream optimization. Our labels are
post-synthesis OpenSTA arrivals under fixed Nangate45 constraints, with no
placement or extracted wire parasitics.

## Changes from the earlier package

- Candidate stage `balanced` is replaced by `weighted`.
- The candidate reads `protocol["baseline"]`; there is no separate candidate
  hyperparameter dictionary to drift out of sync.
- Only `sample_weight` is added to the candidate's `fit` call.
- Validation is evaluated after fitting, with no `eval_set`, validation
  weights, early stopping or best-iteration selection.
- Both models must contain and use all 500 trees. Comparison rejects
  unequal estimator parameter dictionaries.
- The weight audit shows the weights actually applied in each fit.
- A focused test verifies identical constructors, features, targets and
  row order, and verifies that `sample_weight` is the sole fit difference.
- Earlier combined reference results remain historical; their improvement
  must not be attributed to weighting alone.

Data, code, package versions and thread settings are fingerprinted. A
modified experiment needs a new output folder. Do not copy completion files
or models from the old run into this one; rerun both fits.
