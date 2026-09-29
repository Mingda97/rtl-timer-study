# Step 5 — extract features and audit the complete dataset

This package turns the frozen Step 4 netlists into a register-level dataset.
Continue using your existing VSCode Codespace and working Yosys/OpenROAD setup.
There is no new IDE or Docker installation to perform.

Each retained row represents one surviving, publicly named RTL register bit.
Its 25 numeric features come only from the restricted Boolean operator graph
(BOG, specifically the SOG representation). Its target is the maximum setup-path
arrival time at the corresponding register D pin in the full-library netlist.

The authors' **actual, unmodified** `timing_path.py` is included under
`third_party/rtl_timer/`, together with its license, source commit, and hashes.
Our adapter calls `timing_path.get_path_feat()`. It does not try to feed OpenSTA
text reports into the authors' PrimeTime text parser.

The reference run completed all 20 designs: **10,576 usable rows, 25 features,
and 10 explicitly documented exclusions**. Your counts can differ with Yosys
versions. Use your own generated dataset consistently throughout training.

## 1. Put the package next to the previous packages

In VSCode, open the project directory containing `step4-designs` and
`part3-pipeline`. Upload `step5-features.zip` there using the Explorer. Open the
integrated terminal in that project directory and run:

```bash
python3 -m zipfile -e step5-features.zip .
cd step5-features
```

The expected sibling directories are:

| Directory | Purpose |
|---|---|
| `part3-pipeline/` | Existing pilot and pinned restricted SOG Liberty |
| `step4-designs/` | Frozen designs, constraints, and successful synthesis outputs |
| `step5-features/` | This package |

Step 5 reads `step4-designs`; it does not edit that package, rerun synthesis,
change RTL parameters, alter the split, or replace failed designs.

Check that NumPy is available in this terminal:

```bash
python3 -c "import numpy; print(numpy.__version__)"
```

If it prints a version, continue. If it reports `ModuleNotFoundError`, either
activate the Python environment you used for Step 3 or create one here:

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

Python 3.10 or newer is required. NumPy is the only Python dependency; no pandas,
scikit-learn, PyTorch, or XGBoost is needed for data generation.

## 2. Check paths and prerequisites

In the existing ORFS Codespace, this is the expected repository location:

```bash
export ORFS_ROOT=/OpenROAD-flow-scripts
python3 scripts/extract.py preflight
```

Expected final line begins with:

```text
PREFLIGHT PASS: 20 screened designs; 25 features;
```

The preflight checks the frozen source/split hashes, the **full** Step 4
screening report, all required netlists, both Liberty hashes, the upstream
extractor hash, tool versions, and required LEFs. It creates
`runs/run1/run_manifest.json` to record the exact inputs, code, and tools.

If your directories differ, supply their actual paths. For example, from
`step5-features` you can set these once in the current terminal:

```bash
export P5_DATASET="$(pwd)/../step4-designs"
export FULL_LIB="$ORFS_ROOT/flow/platforms/nangate45/lib/NangateOpenCellLibrary_typical.lib"
export SOG_LIB="$(pwd)/../part3-pipeline/external/rtl-timer/vlg2bog/scr_ys/lib/nangate45_sog.lib"
```

The defaults already use those locations. `TECH_LEF` and `CELL_LEF` can also
override the ORFS Nangate45 technology and macro LEF paths when necessary.

If preflight says Step 4 screening is incomplete, finish that step first:

```bash
cd ../step4-designs
python3 scripts/dataset.py screen --jobs 1 --timeout 1200
cd ../step5-features
python3 scripts/extract.py preflight
```

The earlier `$scopeinfo` compatibility patch belongs in Step 4. This package
accepts the recorded pipeline revision but will not bypass failing synthesis.
Keep the same Yosys version for the entire screening/data generation experiment.

## 3. Run one small design first

```bash
python3 scripts/extract.py run --design uart_tx --jobs 1 --timeout 1200
```

`uart_tx` is a canonical design ID from `designs.json`, not an arbitrary top
module name. A successful run ends with a line similar to:

```text
PASS uart_tx: 35/35 rows; 0 exclusions
```

This single command performs the following work:

1. Match register Q aliases between common, BOG, and mapped JSON netlists.
2. Run OpenROAD/OpenSTA on the BOG with the frozen 10 ns constraints.
3. Run it on the full-library netlist with the same constraints.
4. Query **every actual D pin**, separately for rising and falling data paths.
5. Run the full-library netlist again at 20 ns to check the timing definition.
6. Extract BOG features, create labels, join by register identity, and audit.

Reset/set pins such as `RN` and `SN` can appear in `all_registers -data_pins`.
The Tcl exporter explicitly excludes them. They are not register data labels.

To inspect a real row and its original timing paths:

```bash
python3 examples/inspect_endpoint.py --design uart_tx
```

This selects the first retained row. To select a specific bit:

```bash
python3 examples/inspect_endpoint.py --design uart_tx --rtl-bit 'bit_cnt[0]'
```

## 4. Run all 20 designs

```bash
python3 scripts/extract.py run --jobs 1 --timeout 1200
```

The completed UART result is reused after checking its hashes. Other designs
are processed in manifest order. `--jobs 1` is a conservative default for a
small Codespace. You can use `--jobs 2` when resources allow; neither option
changes the split or feature definitions.

`--timeout 1200` allows each timing invocation up to 20 minutes. It does not
mean the whole dataset should take 20 minutes. Timing-tool logs are under
`runs/run1/designs/<design>/timing/<branch>/sta.log`.

For example, in a second terminal you can watch the BOG run for PicoRV32:

```bash
tail -f runs/run1/designs/picorv32/timing/bog/sta.log
```

Ctrl+C in the second terminal stops watching that log. It does not stop the
extraction command in the first terminal.

The full run automatically combines and audits the dataset. Expected ending:

```text
STEP5 AUDIT PASS: dataset is ready for the training step; exclusions remain documented.
```

You can rerun the audit independently:

```bash
python3 scripts/extract.py audit
```

### Resuming or changing an experiment

After a timeout or interruption, rerun the same command. Completed designs are
reused only if their input/tool/code fingerprint and output hashes still match.
An interrupted design is rerun. A larger timeout does not change the fingerprint.

If you deliberately change code, tools, libraries, or netlists, use a new output
directory rather than mixing results:

```bash
python3 scripts/extract.py run --out runs/run2 --jobs 1 --timeout 1200
python3 scripts/extract.py audit --out runs/run2
```

Changing output directories does not change the frozen design split.

## 5. What the dataset means

Consider a register `counter[3]`. The common lowered netlist retains a public
wire alias for its Q output. Both independently mapped branches retain an alias
for the same electrical register bit. The code uses that alias to find the
corresponding D pin in each branch. Instance names and JSON numeric net IDs
are allowed to differ between branches.

The label is:

```text
label_arrival_ns = max(mapped rising-data arrival, mapped falling-data arrival)
```

Arrival includes the imposed input delay for an input-starting path, or
clock-to-Q and combinational delays for a register-starting path. It excludes
the capture setup requirement. Required time and slack are saved separately.
The path with minimum slack need not have the maximum arrival when comparing
rising and falling data, so the script makes that selection explicitly.

When the clock period changes from 10 to 20 ns, this fixed single-clock scenario
should leave arrival unchanged and increase required time and slack by 10 ns.
The package checks this for every reported endpoint/polarity in every design.

The main scenario remains the frozen Step 4 scenario: Nangate45 typical,
10 ns period, 0.05 ns uncertainty and transition, 1 ns maximum input/output
delay, 5 fF output loads, resets held inactive, and no explicit wire RC.
These are **post-synthesis timing labels**, not post-route/signoff timing.

`bog_path_kind` follows the actual selected BOG path's startpoint:

- `input_to_reg`: starts at a primary input.
- `reg_to_reg`: starts at a register output.

The mapped slowest path can have a different startpoint; its type is retained
in `labels.csv` for diagnostics. It is not a model input.

## 6. The 25 numeric features

Table 2 of the paper defines design, cone, and path feature families. The
implementation below covers those families for one SOG representation and one
slowest BOG path per endpoint.

| Family | CSV column(s) | Definition |
|---|---|---|
| Design | `bog_rank_level` | 1 for most critical, then groups 2, 3, 4 at BOG fractions 0.05, 0.40, 0.70 |
| Design | `bog_fraction_strictly_slower` | Fraction of finite BOG endpoints with strictly larger arrival |
| Design | `bog_sequential_cells` | Number of BOG flip-flop cells, including synthesis-generated FFs |
| Design | `bog_combinational_cells` | BOG cell count excluding flip-flops |
| Design | `bog_total_cells` | Sequential plus combinational cells |
| Cone | `driving_register_count` | Distinct FFs reaching this D input through combinational logic |
| Path | `bog_arrival_ns` | Maximum constrained BOG arrival across data polarities |
| Path | `logic_levels` | Combinational output stages on the selected BOG path |
| Path | `and2_count`, `or2_count`, `inv_count`, `xor2_count`, `mux2_count` | Operator counts on that path |
| Path | `fanout_sum`, `fanout_mean`, `fanout_variance`, `fanout_std` | Statistics of driver-pin load counts along the path |
| Path | `cap_sum_ff`, `cap_mean_ff`, `cap_variance_ff2`, `cap_std_ff` | Statistics of reported load capacitances |
| Path | `slew_sum_ns`, `slew_mean_ns`, `slew_variance_ns2`, `slew_std_ns` | Statistics of retained pin slews |

That is 5 design features + 1 cone feature + 19 path features = **25**.

For the cone, reconvergent paths do not count the same source register twice.
Traversal stops at any FF output, including `QN`, and never passes through a
flip-flop into its clock/reset pins. Memoized bitsets avoid repeatedly walking
large shared cones. This is a structural BOG cone; it is not an enumeration of
all sensitizable paths under the SDC.

The rank calculation uses **all finite BOG D-endpoint timings**, including
synthesis-generated FFs, before joining with labels. It never uses mapped
arrivals or the membership of the eventual label table. Ties share the same
fraction and group, so groups do not necessarily have exact 5/35/30/30% sizes.

### Adapter details and changes from the tiny pilot

- The original extractor supplies 16 values: arrival, path length, five
  operator counts, and sum/mean/population variance for fanout, capacitance,
  and slew. The paper's text also mentions standard deviation. We retain the
  public code's variance and append its square root for the three statistics.
- OpenSTA JSON includes intermediate cell input pins. We keep launch CK when
  available, launch Q or input port, combinational outputs, and capture D so
  an operator is not counted twice.
- The public code computes `len(path)-3`, assuming a CK/Q/D register path.
  We replace that element with an explicit combinational-stage count, which
  also handles input-starting paths correctly.
- Empty statistical lists produce zero instead of NaN. The upstream code
  excludes zero fanout/capacitance entries through its truth-value checks;
  this behavior is retained. Zero slew values remain in the slew statistics.
- Unlike the initial tiny pilot, primary-input fanout is counted when the
  selected path begins at that input. Path classification uses the actual
  startpoint, and rank level uses the paper's four criticality groups rather
  than a dense integer ordering.
- OpenSTA JSON stores seconds/farads. We multiply by `1e9` and `1e15`.
  Nangate command/report units here are ns/fF. The TSV-versus-JSON consistency
  check catches unit mistakes. JSON values have limited printed precision;
  the arrival feature/label use the more precise `get_property` TSV values.

The restricted SOG library may include buffers. They count toward logic levels,
but the upstream 16-element vector has no buffer-count feature.

## 7. Output files to open in VSCode

| Path under `runs/run1/` | Contents |
|---|---|
| `data/AUDIT.md` | Human-readable coverage report |
| `data/audit_summary.json` | Machine-readable totals and checks |
| `data/coverage_by_design.csv` | Every design's denominator, usable rows, and exclusions |
| `data/features.csv` | Metadata plus the 25 BOG features for joined rows |
| `data/labels.csv` | Full-library arrivals, timing diagnostics, and mapped D pins |
| `data/dataset.csv` | Joined features and `label_arrival_ns` |
| `data/train.csv`, `validation.csv`, `test.csv` | Frozen design-family partitions |
| `data/feature_columns.json` | Explicit allowlist for model inputs |
| `data/exclusions.csv` | Every excluded common-register bit and its reason |
| `data/training_statistics.csv` | Training-only feature/label distributions; no rows removed |
| `data/dataset_bog_reg_to_reg.csv` | Subset whose selected **BOG** path starts at a register |
| `data/partition_manifest.json` | Design membership and row counts |
| `run_manifest.json` | Code, tool, library, source/netlist, and split fingerprints |
| `designs/<id>/register_inventory.json` | Public aliases and matching decisions |
| `designs/<id>/register_map.csv` | Common FF and BOG/mapped D-pin correspondence |
| `designs/<id>/features_bog_only.csv` | Features before label availability is considered |
| `designs/<id>/manual_samples.json` | Three deterministic examples per design with feature/path evidence |
| `designs/<id>/timing/<branch>/index.tsv` | Both polarity queries, including missing-path statuses |
| `designs/<id>/timing/<branch>/pins.tsv` | D pins and excluded asynchronous control pins |
| `designs/<id>/timing/<branch>/paths.jsonl.gz` | Compressed original timing paths for both polarities |
| `designs/<id>/timing/mapped_period20/` | Clock-period diagnostic run, not another training scenario |

The JSONL archive is compressed text: one JSON object per line. Individual
temporary path JSON files are removed only after the archive is written.
The inspection example reads the archive for you.

To print the final audit:

```bash
python3 -m json.tool runs/run1/data/audit_summary.json
```

To load training data as arrays, without training a model:

```bash
python3 examples/read_training_data.py
```

The reference shape is `X: (6209, 25)` and `y: (6209,)`.
The example deliberately selects `feature_columns.json`; it does not treat
every numeric-looking CSV column as an input.

## 8. What the audit guarantees and what it does not

The audit verifies:

1. All 20 designs have completed outputs and unchanged source/split hashes.
2. Every named common Q bit has a unique matching FF in each branch.
3. Candidate FF counts agree across common/BOG/mapped netlists.
4. Actual D pins agree between Yosys and STA; RN/SN controls are excluded.
5. Every D pin is queried for both data polarities, including positive slack.
6. Units, endpoint identity, capture clock, slack arithmetic, and 10-to-20 ns
   clock-period invariance are consistent.
7. Features/labels are finite and joined by `(design, rtl_bit)` without duplicates.
8. Counts reconcile: retained rows plus exclusions equal the common FF count.
9. Both overall and publicly named coverage are at least 95% **per design**.
   This threshold was set before inspecting labels or prediction results.
10. Families do not cross the frozen partitions. Input columns are explicitly
    limited to BOG-derived features. Identical feature vectors crossing splits
    are reported, not automatically discarded or reassigned.

Missing timing is never replaced with a zero label. An unconstrained path is a
hard failure. If neither polarity has a reportable constrained path, the bit
is listed as an exclusion; a single available polarity is retained and counted
in the audit. In the reference run every matched bit had both polarities.

The reference run had 10,586 common FFs. Ten PicoRV32 FFs had only private
synthesis names, so 10,576 public RTL bits became rows: 6,209 train, 2,370
validation, and 1,997 test. These are honest exclusions, not a claim of mapping
all pre-optimization RTL declarations. Registers removed/merged or recoded
before the common checkpoint are outside this correspondence guarantee.

No full-dataset functional equivalence proof is claimed. Step 3 proved the
tiny pilot; this step checks structural correspondence and timing/data
consistency across the real set. Assertions inside third-party RTL are not
silently converted into a claim of verification.

This is a **SOG-only, one-slowest-path baseline**. The paper's four-BOG ensemble,
random path sampling, maximum-over-paths training loss, signal-level ranking
model, and physical-design optimization are not implemented here. The feature
families are extracted, but the complete published method/accuracy is not
reproduced. These are explicit later experiment options.

Input delay can dominate paths: the reference BOG selected 4,611 input-starting
and 5,965 register-starting paths. The corresponding mapped counts were 4,765
and 5,811. This is an observed property of the chosen scenario, not model
accuracy. Later report performance by path kind and design as well as overall.

Do not randomly split register rows, fit a scaler on all three partitions, use
mapped arrivals/ranks as input features, or select changes based on test scores.
No scaling, imputation, outlier deletion, feature selection, or model fitting
is performed in Step 5.

## 9. Troubleshooting

| Message/problem | Action |
|---|---|
| Incomplete screening or missing JSON/netlist | Finish the Step 4 full screen first |
| Frozen file or Liberty hash changed | Resolve the specific change; do not rewrite all hashes |
| `no technology has been read` | Check `ORFS_ROOT`/LEF paths; the supplied OpenROAD Tcl reads LEFs before linking |
| Tool version says `unknown` | A working binary can still be used; its binary SHA-256 is recorded |
| Tool version/input fingerprint changed | Rerun Step 4 if needed, then choose a new `--out` directory |
| Unmapped `$scopeinfo` cell | Apply the prior Step 4 compatibility fix and regenerate its netlists |
| `alias match count` or many-to-one match | Inspect `common.json`, `bog.json`, and `mapped.json`; do not match by instance number |
| Timing command times out | Inspect `sta.log`; rerun with `--jobs 1 --timeout 2400` if it was still progressing |
| Less than 95% coverage | Inspect `exclusions.csv` and timing indices; do not silently remove the design |
| Missing JSON-format/property support | Send the timing log and tool version so the adapter can be adjusted |

Example diagnostic commands:

```bash
yosys -V
openroad -version
cat runs/run1/designs/uart_tx/failure.json
tail -n 60 runs/run1/designs/uart_tx/timing/bog/sta.log
```

Replace `uart_tx` with the design that failed. A `failure.json` is written only
for a failed design and is removed after a successful retry.

Standalone OpenSTA is supported when it is already installed:

```bash
python3 scripts/extract.py run --backend sta --sta-bin /actual/path/to/sta --out runs/standalone --jobs 1
```

Use those same backend/output options on later audit commands. The reference
run used standalone OpenSTA 3.1.0; your working OpenROAD is the default backend.

## 10. Record the result in your project

If the project is already a Git repository, from its parent directory:

```bash
git add step5-features
git commit -m 'Add audited SOG feature extraction for frozen 20-design dataset'
```

Generated `runs/` output is ignored. Keep `run_manifest.json`, audit reports,
and your final datasets with the experiment results according to your private
repository's storage policy. The included `reference_results/` is a separate,
measured example from our reference environment; it is not silently reused by
your extraction run. Follow `TESTING.md` for its exact validation scope.

## References

- Paper: https://arxiv.org/abs/2403.18453, especially Section 3.3/Table 2.
- Source code: https://github.com/hkust-zhiyao/RTL-Timer,
  pinned at `206ff4078368c251d2fafaffcc648282c68316f1`.
- OpenSTA command reference: https://opensta.readthedocs.io/en/latest/Commands/
