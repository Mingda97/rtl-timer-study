# Step 6 extension: equal-design weights + early stopping + fresh refit

This is a separate experiment. It imports your completed upstream baseline
and weighting-only 500-tree models without retraining them, then adds a third
model. The new model keeps the same feature/label data, split, weights and
estimator settings, except that validation MAE selects its number of trees.

The default search permits **up to 2,000 trees**, with **patience 50**. It
monitors **equal-design validation MAE**, records `best_iteration`, and fits a
fresh model with exactly `best_iteration + 1` trees. Both fits use only the
original 12 training designs. No regularization or learning-rate tuning is
included. The original 500-tree models remain comparison controls.

Read `IMPLEMENTATION.md` for the code explanation and negative-R² discussion.
Read `TESTING.md` for reference results and verification details.

## Install alongside your existing folders

Upload `step6-early-stopping.zip` to `/workspaces/codespaces-blank`.
In the VSCode integrated terminal:

```bash
cd /workspaces/codespaces-blank
python3 -m zipfile -e step6-early-stopping.zip .
cd step6-early-stopping

source ../step6-weighting-only/.venv/bin/activate
python -m unittest discover -s tests -v

python scripts/train.py preflight
python scripts/train.py import-previous
python scripts/train.py select-rounds --timeout 1200
python scripts/train.py refit --timeout 1200
python scripts/train.py compare
```

If you used another virtual environment for Step 6, activate that one instead.
If you need a new environment, replace the `source` command above with:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Use VSCode's **Python: Select Interpreter** to select the environment you
activated. These commands run in the existing Linux Codespace; you do not
need to install another IDE or move to your GPU desktop.

## Default directories

| Role | Absolute path in your Codespace |
|---|---|
| Completed Step 5 data | `/workspaces/codespaces-blank/step5-features/runs/run2` |
| Completed weighting-only experiment, read only | `/workspaces/codespaces-blank/step6-weighting-only/runs/weighting_only` |
| New results | `/workspaces/codespaces-blank/step6-early-stopping/runs/es_2000_patience50` |

If your directories differ, add `--input-run /absolute/path`,
`--previous-run /absolute/path`, and/or `--out /absolute/path` on every command.
The new output must be separate from both input directories. The code rejects
overlapping paths, mismatched datasets, changed source results or differing
package/thread settings. A prior run must have completed `baseline`, `weighted`
and `compare`. The old combined `balanced` experiment is not the input here.

`--threads` defaults to 25 and must match the previous run; repeat any override
on every command. Source code, protocol, data, package versions and previous
run identity are fingerprinted. If you change the experiment, choose a new
output directory rather than disabling those checks.

No command writes into your prior run. `import-previous` copies verified model
artifacts into the new run and records their origin. Original model bytes,
metrics, predictions, logs and selection remain unchanged. The import includes
models, so this extension can compare all three at the eventual final test.

## What each command does

| Command | Purpose |
|---|---|
| `preflight` | Verify identical audited data, feature order, split, software and old results; snapshot inputs |
| `import-previous` | Copy verified old models and results into the new experiment without fitting |
| `select-rounds` | Train weighted trees, monitor weighted validation MAE, record best round and full visited history |
| `refit` | Fit a new weighted model on training data only, with exactly the selected number of trees |
| `compare` | Compare all three on validation; select by macro MAE |
| `final-test` | Optional final evaluation after development is finished |

The two fitting stages use monitored subprocesses. Read logs under
`runs/es_2000_patience50/logs/`. A timeout or failed check produces no valid
completion marker. Completed stages are reused only after verification.

## Read the outputs

Paths below are relative to `runs/es_2000_patience50`:

| Output | Meaning |
|---|---|
| `models/round_search/round_selection.json` | Best zero-based iteration, selected tree count, best MAE, trees actually trained, patience and cap |
| `models/round_search/learning_curve.csv` | Validation macro MAE after every visited boosting round |
| `models/round_search/design_weights.csv` | Separate training and validation weights, with their different roles |
| `models/round_search/model.ubj` | Search model, including patience rounds after its best round |
| `models/weighted_es/model.ubj` | Fresh refitted model with exactly the selected number of trees |
| `models/weighted_es/result.json` | Train/validation metrics, selected tree count and refit consistency check |
| `models/weighted_es/validation_predictions.csv` | Mapped reference arrival, prediction and absolute error per endpoint |
| `validation/REPORT.md` | Three-model comparison |
| `validation/summary.csv` | Macro and pooled metrics |
| `validation/per_design.csv` | All per-design metrics, including negative R² values |
| `selection.json` | Model selected using validation only |
| `models/baseline/import_record.json` and `models/weighted/import_record.json` | Source of unchanged imported models |

`weighted_es` is the new refitted candidate. `round_search` is its selection
artifact, not a fourth candidate. The result can have fewer or more than 500
trees, up to the 2,000-tree search cap. The best round is chosen only among
rounds visited before stopping; early stopping does not exhaustively search
all 2,000 possibilities after it stops.

## Final test when ready

Do not use the test set to choose tree count or patience. If you may still
develop another candidate, stop after validation comparison. When finished:

```bash
python scripts/train.py final-test
```

This locks all three models and their validation-based selection before
reading the four held-out test designs. It evaluates saved models without
refitting and writes reports under `test/`. It blocks further round selection
and training in this run. It does not change the earlier run's evaluation lock.

If the earlier experiment's test labels were already evaluated, this extension
records that fact and its results should be described as exploratory. A new
directory does not restore independence from previously viewed test results.

## Change the search settings only for a separately declared experiment

`protocol.json` contains:

```json
"early_stopping": {
  "max_estimators": 2000,
  "patience": 50,
  "eval_metric": "mae"
}
```

This package runs exactly this one configuration. It does not try several
patience values or increase the cap automatically. If the search reaches the
cap, `reached_round_cap` is true: the best observed round is still usable, but
the patience stopping criterion may not have been reached.

Reference run results are in `reference_validation/`; these are not your
Codespace results. Model binaries and input snapshots are omitted from this
download; your own commands create them using your existing data and models.
