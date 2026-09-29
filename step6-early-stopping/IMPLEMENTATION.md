# Explaining R², early stopping, and the new training code

## Why R² can be negative

For each design, the metric code computes:

```text
R² = 1 - sum((actual - prediction)²) / sum((actual - mean(actual))²)
```

The numerator is the model's sum of squared errors (SSE). The denominator
is the squared error from always predicting that evaluation design's mean
target (SST). Therefore R² = 1 is perfect, R² = 0 matches that constant-mean
reference, and R² < 0 is worse than it under squared error. This definition
is not generally the square of Pearson correlation, so negativity is valid.
The evaluation-set mean is a scoring reference, not a deployed predictor
whose target mean would already be known on an unseen design.

Example: actual values `[1, 2, 3]`, predictions `[3, 3, 3]` give SSE = 5 and
SST = 2, so R² = 1 - 5/2 = -1.5. The previous `common.metric_values` formula
already implements this correctly. Constant-target designs have undefined
R² in this project and are recorded as null instead of fabricated scores.

Our reported macro R² averages the four per-design R² values. In the prior
weighting-only reference run, those values were:

| Design | R² |
|---|---:|
| osu_riscv32i | -0.294227 |
| divider32 | -2.460272 |
| chacha | 0.444772 |
| i2c_master | 0.889780 |
| Arithmetic mean | -0.354987 |

The strongly negative divider result pulls the mean below zero. A design with
low variation in its actual targets can have a small denominator, making R²
very sensitive to prediction errors. MAE uses absolute error in ns; R² uses
squared error normalized by each design's target variation. Improved macro
MAE therefore does not imply improved macro R². Always show per-design results.

## The controlled comparison

We retain two previous models and add exactly one new candidate:

| Model | Training weights | Number of trees | Source |
|---|---|---|---|
| `baseline` | Equal per register row | 500 | Imported unchanged |
| `weighted` | Equal total per training design | 500 | Imported unchanged |
| `weighted_es` | Equal total per training design | Chosen by validation MAE | New fresh refit |

The new candidate keeps maximum depth 50, learning-rate default 0.3, all
other XGBoost defaults, CPU threads, data order, features, labels and frozen
design split. The search ceiling is 2,000 instead of the old fixed 500.
Early stopping itself controls effective model complexity through tree count;
there is no separate depth/penalty/sampling change.

The experiment tests this tree-count-selection procedure. It does not isolate
the effects of every component of the procedure (cap and patience) or search
multiple values of either one.

## 1. Preserving the old experiment

In `scripts/train.py`, `preflight()` reads and verifies the old run manifest,
both model completion records and the old selection. It requires matching
Step 5 data hashes, feature schema, XGBoost/package versions, base estimator
settings and thread count. It records the old experiment's identity in the
new fingerprint through the shared `common.preflight()` helper.

`separate_paths()` rejects a new output that is the same as, above, or below
either protected input directory. Symlinks are resolved before comparison.

`import_previous()` copies the completed old model directories into the new
output. It preserves the source completion record as `source_complete.json`,
adds an import record, and verifies future reuse with a new completion record.
The originals are read only. It never calls `fit()` for baseline or weighted.

`common.py` contains the prior audited-data, weighting and metric helpers. It
does not contain or execute the old training driver. The upstream source files
remain included for provenance checks; no upstream baseline retraining occurs.

## 2. The settings you control

`protocol.json` is the declared experiment. Its baseline section retains the
original 500/depth-50/25-thread constructor. The additional section is:

```json
"early_stopping": {
  "max_estimators": 2000,
  "patience": 50,
  "eval_metric": "mae"
}
```

`choose_rounds()` copies the base parameters, then changes only these search
settings:

```python
params = base_parameters(ctx)
params.update(
    n_estimators=settings['max_estimators'],
    eval_metric='mae',
    early_stopping_rounds=settings['patience'],
)
search_model = xgb.XGBRegressor(**params)
```

Under the pinned XGBoost 3.0.5 sklearn API, early stopping and evaluation
metric are constructor parameters. `eval_set` and its row weights belong to
`fit()`. `check_parameter_changes()` compares against the old weighted model
and refuses unexpected changes such as a new maximum depth or learning rate.

`eval_metric='mae'` controls monitoring and stopping. It does not change the
training objective, which remains `reg:squarederror`. We are selecting a
squared-error-trained boosted-tree model according to validation MAE.

## 3. Training weights and validation weights have different jobs

`common.design_weights(frame)` uses:

```python
counts = frame['design'].value_counts()
weights = frame['design'].map(
    len(frame) / (len(counts) * counts)
).to_numpy(float)
```

The formula N/(D*n_d) makes each design's total weight N/D and the mean row
weight one. The search computes weights independently for its two partitions:

```python
train_weights = c.design_weights(train)
validation_weights = c.design_weights(validation)
```

Training has 12 designs and validation four. We do not use combined counts,
test counts or target values to calculate either weight vector.

Training weights affect the loss/gradient calculations that construct trees.
Validation weights affect only the monitored metric that chooses when to stop
and which round to keep. Validation labels influence model selection, but
validation rows do not supply training gradients or fit the tree leaves.

For validation weights v_i = N_val/(D_val*n_d), XGBoost's weighted MAE is:

```text
sum(v_i * abs(error_i)) / sum(v_i)
  = (1 / D_val) * sum over designs [ mean absolute error in that design ]
  = validation macro MAE
```

Without `sample_weight_eval_set`, XGBoost would monitor pooled row-level MAE,
which gives larger validation designs greater influence and differs from our
predeclared selection criterion. `models/round_search/design_weights.csv`
records both vectors' per-design totals and whether they affect fitting or
only round selection.

## 4. Running early stopping

The important `choose_rounds()` fit call is:

```python
search_model.fit(
    X_train,
    y_train,
    sample_weight=train_weights,
    eval_set=[(X_val, y_val)],
    sample_weight_eval_set=[validation_weights],
    verbose=50,
)
```

There is exactly one monitored validation set and one metric. This avoids
ambiguity over which set/metric controls stopping. `verbose=50` controls log
printing only; it does not evaluate only every 50 rounds. MAE is evaluated
after every boosting round and all visited values are saved.

Conceptually, each round adds another tree, measures weighted validation MAE,
and updates the best score if it improves. Every improvement resets the
patience counter. Training ends after 50 consecutive rounds without beating
the best score, or after 2,000 total rounds. Scores do not need to get worse
monotonically for stopping to trigger; they just must fail to beat the best.

The code records whether the cap was reached. Early stopping may terminate
long before 2,000. It selects the best observed round, not a guaranteed global
minimum over every possible future tree count.

## 5. Best iteration versus tree count versus trained rounds

After fitting:

```python
best_iteration = int(search_model.best_iteration)
best_n_estimators = best_iteration + 1
history = search_model.evals_result()['validation_0']['mae']
rounds = search_model.get_booster().num_boosted_rounds()
```

The iteration index starts at zero. If `best_iteration` is 4, the model uses
five boosting rounds/trees in this single-target regression setup.

The search model can retain trees grown after its best round, because it had
to continue for the patience period before it knew to stop. Thus these three
numbers are distinct:

| Quantity | Reference experiment |
|---|---:|
| Search cap | 2,000 |
| Rounds actually trained | 55 |
| Best zero-based iteration | 4 |
| Selected number of trees | 5 |

The saved search model has 55 trees. The refitted model has exactly five.
`round_selection.json` records these values separately.

We verify that the recorded best iteration equals the first minimum in the
saved history. We then explicitly predict using the selected prefix:

```python
prediction = search_model.predict(
    X_val,
    iteration_range=(0, best_n_estimators),
)
```

The upper bound is exclusive: `(0, 5)` uses tree indices 0 through 4. The
sklearn wrapper can use `best_iteration` automatically for early-stopped
models; the explicit range makes the verification unambiguous.

Finally, we recompute macro MAE using the project's independent per-design
metric code and check it agrees with `search_model.best_score`, allowing a
small floating-point tolerance. This catches a wrong validation-weight or
wrong-tree-count implementation.

## 6. Fresh refitting with the selected count

`fresh_refit()` creates another estimator from the original base parameters:

```python
params = base_parameters(ctx)
params['n_estimators'] = best_n_estimators
model = xgb.XGBRegressor(**params)

model.fit(
    X_train,
    y_train,
    sample_weight=c.design_weights(train),
)
```

The refit deliberately has no `early_stopping_rounds`, `eval_set`, or
`sample_weight_eval_set`. It also has no `xgb_model` argument: it does not
continue from the search model or add five trees to existing trees. It trains
a fresh model for exactly the selected number of rounds.

It uses only the same original 12 training designs, with identical row order,
features, targets, weights and estimator settings. Combining train and
validation at this point would change training data and would prevent a fair
validation comparison with the previous models.

Refitting is not required merely to obtain best-iteration predictions from
XGBoost. We do it here because the requested procedure explicitly calls for
it, and it produces a final saved model whose tree count is exactly the
selected count. With this deterministic setup, refitting should reproduce
the selected search prefix; it is not expected to improve that prefix again.

`fit_worker()` checks the refitted tree count, verifies only `n_estimators`
changed relative to the old weighted model, and compares its train and
validation predictions against the search prefix within a small tolerance.
The measured reference maximum difference was exactly zero.

## 7. Labels and evaluation remain the same

`common.xy()` still separates the 25 SOG-derived features from the target
`label_arrival_ns`. Design names, register IDs and mapped timing are not
features. The full-library mapped arrivals were generated in Step 5; this
extension does not rerun Yosys or OpenSTA.

After refitting, `fit_worker()` does:

```python
prediction = model.predict(X)
metrics[split] = c.evaluate(frame, prediction)
c.write_predictions(path, frame, prediction)
```

`common.metric_values()` compares predictions against the actual Step 5
arrival labels. `common.evaluate()` computes per-design metrics, their macro
averages, and pooled metrics. The prediction CSV contains
`actual_arrival_ns`, `predicted_arrival_ns` and `absolute_error_ns`.

`compare()` combines the unchanged prior validation results with the new
results and selects the lowest macro MAE. Exact ties prefer baseline, then
weighted, then weighted_es. `report()` writes the summary and per-design CSVs.

## 8. Validation is now part of model selection

The four validation designs choose the tree count and compare the three
models. This is legitimate development use, but it means the selected
validation score is not an independent estimate of final generalization.
Do not claim that MAE-based early stopping guarantees improved test MAE,
improved R² or improved results for every design.

Neither round selection nor refitting reads test rows. Preflight hashes and
copies the test CSV for integrity only. `final_test()` requires an existing
selection, verifies all three model files, writes an evaluation lock before
reading test labels, then evaluates the saved models. No further fitting is
allowed in that run. Test results do not revise the selected model.

If earlier test results have already been viewed, new tuning is exploratory;
new folders do not undo that knowledge. The code records the previous test
lock state in the new run's provenance.

## 9. Function guide for VSCode and interview preparation

| Function | File | Responsibility |
|---|---|---|
| `preflight` | `scripts/train.py` | Verify prior experiment and same data/settings |
| `separate_paths` | `scripts/train.py` | Prevent output overlap with protected inputs |
| `import_previous` | `scripts/train.py` | Preserve/reuse old models without retraining |
| `choose_rounds` | `scripts/train.py` | Weighted early stopping and best-round record |
| `fresh_refit` | `scripts/train.py` | New fixed-round model on training data only |
| `check_parameter_changes` | `scripts/train.py` | Prevent unplanned hyperparameter changes |
| `fit_worker` | `scripts/train.py` | Save artifacts and verify prefix/refit consistency |
| `run_fit` | `scripts/train.py` | Subprocess logs, timeout and verified reuse |
| `compare` / `report` | `scripts/train.py` | Three-model validation comparison |
| `final_test` | `scripts/train.py` | Locked held-out evaluation |
| `preflight` / `read_partition` | `scripts/common.py` | Audit, fingerprints, frozen partitions |
| `xy` / `design_weights` | `scripts/common.py` | Model inputs/targets and per-design weights |
| `metric_values` / `evaluate` | `scripts/common.py` | R² and other error metrics |
| `write_predictions` | `scripts/common.py` | Endpoint-level comparison with mapped labels |

For an interview, explain that you first isolated design weighting, then
isolated validation-based tree-count selection while keeping the other
estimator settings fixed. Explain why validation weighting matches the
macro-MAE objective, why the best iteration needs +1, why refitting excludes
validation rows, and why final test data remain outside model development.

## Primary references

- XGBoost 3.0.5 sklearn API, early stopping, best_iteration, evaluation weights:
  <https://xgboost.readthedocs.io/en/release_3.0.0/python/python_api.html>
- Scikit-learn definition and negative values of R²:
  <https://scikit-learn.org/stable/modules/generated/sklearn.metrics.r2_score.html>

The project code computes the explicit R² formula itself; it does not depend
on scikit-learn's default replacement of undefined constant-target scores.
