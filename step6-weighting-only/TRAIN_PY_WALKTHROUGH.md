# Understanding train.py and the timing-learning experiment

This guide describes the corrected weighting-only revision. A comparison
with the previous combined candidate appears below. In VSCode, use Ctrl+F
(Cmd+F on macOS) to find the function names shown here.

## 1. The problem the model solves

One dataset row represents one retained register bit in one design. Its
25 numeric inputs come from the restricted simple-gate representation
(called SOG in the upstream code and `bog` in our file paths). The target is
the corresponding register D-pin arrival time from the full-library mapped
netlist, in nanoseconds.

The model learns a function `predicted_arrival = f(25 SOG features)`. It does
not read Verilog, synthesize RTL, place cells, or run static timing analysis.
Those operations needed for features and labels occurred in Steps 4 and 5.

This division is fundamental: full-library STA is the reference used to
train and evaluate the predictor; SOG information is what the predictor sees.
On a new design, producing predictions requires the same SOG feature pipeline,
but not that new design's full-library target labels. Therefore this is not
inference directly from raw RTL with no synthesis/STA work at all.

## 2. Exactly where the full-library labels originate

The relevant Step 5 files are `scripts/export_sta.tcl` and
`scripts/extract.py`, in your **step5-features** folder, not this folder.

Both synthesis branches start from the same lowered Yosys common checkpoint.
One branch uses the restricted gate library; the other uses the full Nangate45
cell library. The full-library branch is named `mapped`.

1. `map_registers(...)` uses public Q-net aliases to identify a register bit
   across the common, SOG and mapped representations. It records each branch's
   actual D pin. It checks uniqueness and rejects ambiguous or many-to-one
   matches. It does not match by assuming cell instance numbers or Yosys
   JSON net IDs survive independent mapping. This structural correspondence
   check is not a formal equivalence proof.
2. `timing_run(...)` invokes OpenROAD/OpenSTA separately for `bog` and `mapped`.
3. `export_sta.tcl` loads the appropriate Liberty, netlist and SDC, links
   the design, and queries actual flip-flop D pins. Asynchronous control pins
   are excluded. For each D pin it requests a max-delay path for each endpoint
   polarity, rise and fall.
4. `load_timing(...)` reads the exported values and paths. The extraction
   code checks units, including conversion of SI-valued JSON fields to ns/fF.
5. `process_design(...)` calls `extract_features(...)` with only the SOG graph
   and timing. Separately, it chooses the larger arrival among the available
   mapped rise/fall path records and assigns it to the row's target.

The decisive Step 5 code is:

```python
candidates = timing['mapped'][item['mapped_d_pin']]
chosen = max_arrival(candidates)
label = {
    # Other metadata is omitted here for clarity.
    'arrival_ns': chosen['arrival_ns'],
}
joined.append({**feature, 'label_arrival_ns': label['arrival_ns']})
```

The label is data arrival time, not the SOG arrival feature, not slack, and
not a prediction. `bog_arrival_ns` is one input; `label_arrival_ns` is the
full-library target. `required_ns` and `slack_ns` remain timing-audit metadata.
Setup slack is required time minus arrival; required time can differ across
endpoints and polarities, so converting all arrivals with a single clock
period would not be equivalent.

The reference scenario uses a 10 ns clock, 0.05 ns uncertainty and transition,
1 ns maximum input delay, and 5 fF output load. There is no placement or wire
RC extraction. Arrivals can include launch-clock/input-delay context, so
describe them as arrivals under these constraints, not bare gate delay or
signoff post-route timing. The clock-period check changes 10 ns to 20 ns:
arrival should stay unchanged while required time and slack shift by 10 ns.

Missing reportable paths are explicit exclusions, never invented zero labels.
`audit_all(...)` checks the joins and writes the frozen split CSVs.

For one endpoint, follow this evidence chain in your run:

- Step 5 `designs/<design>/register_map.csv`: RTL bit to mapped D pin.
- Step 5 `designs/<design>/labels.csv`: mapped arrival and associated timing.
- Step 5 `designs/<design>/timing/mapped/index.tsv`: raw per-polarity values.
- Step 5 `designs/<design>/dataset.csv`: joined features and target.
- Step 6 `models/<model>/validation_predictions.csv` or
  `test/<model>_predictions.csv`: reference, prediction and absolute error.

Join using both `design` and `rtl_bit`, never just CSV row number across files.

## 3. Where the parameters live

The old candidate had a separate `balanced` dictionary in `protocol.json`.
`fit_worker(...)` expanded it into `XGBRegressor(**params)`. That is where
depth 4, learning rate 0.05, additional regularization, 80% sampling and
early stopping came from. They were deliberate candidate settings, not
requirements for sample weighting. Changing them simultaneously confounded
the interpretation of the comparison.

In this revision, `protocol.json` contains only this estimator configuration:

```json
"baseline": {
  "n_estimators": 500,
  "max_depth": 50,
  "nthread": 25
}
```

`train_upstream(...)` runs the authors' unchanged training function, whose
constructor is hardcoded to those three values. A wrapper checks that its
arguments match `protocol['baseline']`. The optional CLI `--threads` value
then overrides `nthread` identically for both models.

`train_weighted(...)` copies that same dictionary:

```python
params = dict(ctx['protocol']['baseline'])
params['nthread'] = ctx['threads']
model = xgb.XGBRegressor(**params)
model.fit(X_train, y_train, sample_weight=design_weights(train))
```

There is no independent weighted-model parameter block. Editing only the
baseline protocol to a different depth will fail the upstream constructor
check; this is intentional protection of the upstream baseline. For a later
tuning experiment, retain the baseline and add a separately named candidate
configuration/training stage in a new experiment. Do not change the vendored
function and continue calling it unmodified upstream code.

The pinned XGBoost version supplies unspecified defaults. These resolved
values were verified in both saved `booster_config.json` files:

| Setting | Baseline and weighting-only candidate | Role |
|---|---|---|
| `n_estimators` | 500 | Boosting rounds; one tree per round here |
| `max_depth` | 50 | Maximum permitted tree depth, not necessarily actual depth |
| `learning_rate` / `eta` | 0.3 | Scales each tree's contribution |
| `min_child_weight` | 1 | Minimum Hessian sum for a child node |
| `reg_lambda` | 1 | L2 leaf-weight penalty |
| `reg_alpha` | 0 | L1 leaf-weight penalty |
| `subsample` | 1 | Fraction of rows sampled per tree |
| `colsample_bytree` | 1 | Fraction of features sampled per tree |
| `objective` | `reg:squarederror` | Squared-error training objective |
| tree method | `auto`, histogram updater here | Split construction algorithm |
| seed | 0 default | Random-number seed |
| device | CPU | Execution device |
| threads | 25 by default | CPU thread request |
| early stopping | None | Both fits complete all 500 rounds |

`get_params()` records requested settings and may show `None` for defaults;
the saved booster configuration shows their resolved values. The learned
trees and automatically estimated `base_score` can differ because weights
affect fitting. That is an effect of the single intervention, not a second
manually changed hyperparameter. Do not demand byte-identical trained models.

The baseline already includes default L2 regularization. Weighting-only does
not mean unregularized. XGBoost's exact tree construction and regularization
can interact with row weights even when their settings remain fixed.

## 4. How equal design weighting works

`design_weights(frame)` counts usable rows within the training partition:

```python
counts = frame['design'].value_counts()
weights = frame['design'].map(
    len(frame) / (len(counts) * counts)
).to_numpy(float)
```

Let N be training rows, D be training designs, and n_d be rows in design d.
Each row of design d gets weight N / (D * n_d). Therefore:

- Each design's total weight is n_d * N / (D * n_d) = N / D.
- Total weight is N; average row weight is 1.
- Designs with fewer rows give each row a larger weight.

Example: two designs containing 1,000 and 100 rows get per-row weights 0.55
and 5.5. Each contributes total weight 550. In our training split, each of
12 designs gets 1/12 of total weight.

Ignoring the common regularization term, the normalized data-fitting loss
changes from a mean across rows to a mean of per-design mean losses:

```text
unweighted: (1/N) * sum over designs and rows of loss(y, prediction)
weighted:   (1/D) * sum over designs [ (1/n_d) * sum over its rows of loss ]
```

For this estimator the loss is squared error. `sample_weight` changes each
row's contribution to the fitting calculations; it does not multiply feature
values or labels. It is passed to `fit`, not `predict`. Weights use training
row counts only. Validation is scored separately after the fit; there are
no validation weights or validation-driven stopping rules in this revision.

Mean-one normalization retains the baseline's overall weight scale. A raw
`1/n_d` formula would change the loss scale relative to regularization and
Hessian thresholds. Balancing removes contribution proportional merely to
row count; it cannot guarantee equal accuracy or prevent unusually large
errors from dominating squared error. Designs are balanced, not families.

## 5. Reading the training script in execution order

| Function | Responsibility | Why it matters |
|---|---|---|
| `main` | Parse stage, input/output directories, threads and timeout | Every command enters here |
| `preflight` | Verify Step 5 audit, hashes, feature order, family split, versions; snapshot inputs | Prevent silent changes or incomplete data |
| `read_partition` | Read one frozen partition; check and sort rows | Never create a random register-level split |
| `xy` | Separate 25 input columns from the arrival target | Prevent label/ID leakage |
| `design_weights` | Compute equal total weight per training design | The one experimental intervention |
| `train_upstream` | Adapt CSV rows and execute the unmodified upstream function | Faithful training-function baseline |
| `train_weighted` | Same estimator and training rows, weighted fit | Controlled comparison |
| `fit_worker` | Fit one model, save it, score train and validation | Produces model and predictions |
| `run_fit` | Manage subprocess, log, timeout and verified reuse | A failed fit cannot look complete |
| `metric_values` | Calculate error and correlation metrics | Explicit metric definitions |
| `evaluate` | Per-design, macro and pooled summaries | Reveal design-size effects |
| `write_predictions` | Save endpoint-level actual/predicted/error values | Inspect individual failures |
| `compare_validation` | Verify same estimator settings and select by macro MAE | Choice uses validation only |
| `comparison` / `plot_comparison` | Write tables, report and plot | Presentation outputs |
| `final_test` | Lock selection, load models, evaluate held-out designs | Final estimate without refitting |
| `complete` / `verify_complete` | Hash and validate saved outputs | Reuse only intact matching results |

### Preflight and the split

Training uses 12 designs, validation four, and final test four. Related
designs in the same family stay together. Register rows from the same design
are correlated: random row splitting could expose nearly identical circuit
structure during both fitting and evaluation, overstating new-design accuracy.

Preflight hashes/copies the test CSV for integrity, but does not parse its
feature or target values. The partition manifest exposes row counts and
membership, which are not model performance. Only `final_test` reads test rows.

The run fingerprint includes data and source checksums, Python/package versions,
thread settings and protocol. A changed setup requires a new output directory.
This is reproducibility bookkeeping, not part of the learning algorithm.

### Separating X and y

`TARGET = 'label_arrival_ns'` appears at the top. `xy(...)` does:

```python
X = pd.DataFrame(frame[ctx['features']].to_numpy(dtype=float))
y = frame[TARGET].to_numpy(dtype=float)
```

`feature_columns.json` is the ordered allowlist of 25 inputs. The six metadata
columns `design`, `rtl_bit`, `split`, `family`, `category`, and `bog_path_kind`
are excluded. The target, mapped D-pin identity and mapped timing metadata
are also excluded. The model cannot use a design-name shortcut. No feature
normalization, target transformation, clipping or missing-label filling occurs.

### The upstream adapter

The upstream function expects per-design pickle records, so `train_upstream`
writes fresh local adapter records. It divides the 25-feature vector into
`feat_design` (first six) and `feat_path` (remaining 19), then the upstream
function concatenates them in the same order. This is an interface split,
not a claim that our full feature definition exactly matches the entire paper.

The legacy key `label_slack` receives our arrival target unchanged. Its name
does not change the physical meaning of the value. Our exported CSVs and
metrics use explicit arrival names. Do not present the target as slack.

Python's AST parser extracts only the upstream `training()` definition. It
does not execute that file's original driver, cross-validation or metrics.
The constructor wrapper checks the three upstream parameters and applies
only the shared optional thread override. Unit tests confirm all 12 designs,
every feature value and every label reach the function.

### Fitting and prediction

The baseline's effective learning call is:

```python
model = xgb.XGBRegressor(n_estimators=500, max_depth=50, nthread=25)
model.fit(X_train, y_train)
```

The candidate's call is identical except for:

```python
model.fit(X_train, y_train, sample_weight=weights)
```

Both are trained once on the 12 training designs. Validation rows are
available for subsequent scoring, but are not passed to either fit. The
training objective is squared error; the reported primary metric is MAE.
Selecting by MAE does not silently change the estimator's objective to MAE.

## 6. Where predictions are compared with mapped timing

Inside `fit_worker(...)`, after fitting, the same sequence runs for the
training and validation partitions:

```python
prediction = model.predict(X)
metrics[split] = evaluate(frame, prediction)
write_predictions(directory / (split + '_predictions.csv'), frame, prediction)
```

Inside `metric_values(...)` the numeric comparison starts with:

```python
error = prediction - y
mae = np.mean(abs(error))
rmse = np.sqrt(np.mean(error ** 2))
```

Here y is the Step 5 full-library arrival label. `write_predictions(...)`
makes that comparison explicit in a CSV:

```python
result['actual_arrival_ns'] = frame[TARGET]
result['predicted_arrival_ns'] = prediction
result['absolute_error_ns'] = abs(prediction - frame[TARGET].to_numpy(float))
```

For a hypothetical endpoint with SOG arrival 1.30 ns, mapped arrival 1.10 ns
and prediction 1.15 ns, the evaluated absolute error is 0.05 ns. The SOG
arrival is an input, not the reference subtracted during scoring.

`final_test(...)` performs the same prediction/evaluation operations on
test rows after loading the saved models. No Yosys or OpenROAD process is
started by either evaluation path.

## 7. Understanding the reported metrics

For a design, MAE is its average absolute endpoint error. RMSE squares errors
before averaging and takes a square root, so large errors have greater influence.
Both are measured in ns. R² is 1 - SSE/SST and can be negative when predictions
are worse than the design's constant-mean reference. Pearson describes linear
association; Spearman describes rank association. Correlation alone does not
establish small numerical timing error.

`evaluate(...)` first computes each metric separately per design. It then
reports:

- **Macro:** arithmetic mean of the per-design metric values. Every design
  gets one share. This is the primary MAE used for selection.
- **Pooled:** one metric calculation using all rows together. Larger designs
  contribute more rows. Pooled MAE is therefore row-count weighted.

For MAE, equal-design sample weighting during scoring equals macro MAE.
For RMSE, mean(per-design RMSE) is not the same as the square root of an
equal-design mean of per-design MSEs. The code reports the former as macro
RMSE. R² and correlations are not averaged over undefined designs; the
corresponding `defined_design_counts` explain their denominator. Undefined
correlations are null, not converted to perfect correlation.

Training weights and reporting averages are separate choices: both models
are evaluated with the same macro and pooled metrics. A metric must not be
changed after seeing which one makes the candidate look better.

`compare_validation(...)` chooses the lower validation macro MAE; exact ties
favor baseline. The test report may show the other model doing better, but
that does not retrospectively change the validation-selected model.

## 8. What can be claimed in the interview

Explain the controlled comparison: same 25 SOG features, full-library arrival
targets, family-aware design split, software and estimator settings; only
training sample weights change. Equal weights per design address the training
loss's automatic bias toward designs with more retained register rows.

Explain label provenance: both netlists come from a common checkpoint;
stable public Q aliases establish register correspondence; max-delay mapped
D-pin arrivals under fixed constraints are joined as labels. Full-library
timing is not included as a model input.

Explain evidence honestly: examine macro, pooled and per-design results.
There are four validation designs, not thousands of independent validation
experiments. A reduction in macro MAE does not prove every design or every
metric improves. Test scores are reserved for the final frozen comparison.

State the scope: reuse of an upstream training function on our audited
dataset, not a reproduction of all paper methods or headline accuracy.
Reference timing is a fixed post-synthesis model, not post-route signoff.
The old combined experiment remains useful as a combined intervention, but
its benefit cannot be assigned to weighting alone.

## Primary technical references

- Upstream source: <https://github.com/hkust-zhiyao/rtl-timer/tree/206ff4078368c251d2fafaffcc648282c68316f1>
- XGBoost 3.0.5 parameters: <https://xgboost.readthedocs.io/en/release_3.0.0/parameter.html>
- XGBoost sklearn API and sample weights: <https://xgboost.readthedocs.io/en/release_3.0.0/python/python_api.html>
- Boosted-tree objective: <https://xgboost.readthedocs.io/en/release_3.0.0/tutorials/model.html>

Parameter and API explanations refer to the pinned version, not a promise
that defaults are identical in every historical release. Project-specific
pipeline explanations refer to the inspected Step 5 and Step 6 source files.
