# Step 4: freeze 20 designs and their evaluation split

Dataset version: `designs-v1`, frozen 2026-09-28, before fitting or comparing models.
This package contains the selected RTL files with notices, pinned repository
revisions and SHA-256 file hashes, exact top modules and parameter overrides,
clock/reset declarations, shared-scenario SDCs, and fixed split assignments.
No HDL generator, FPGA vendor tool, SRAM macro, or DSP macro is needed.

## What “freeze the split” means

Decide once which designs belong to each group and record the decision in a
version-controlled file. Then use those same groups for every model comparison:

- Training: fit model weights/trees and any learned preprocessing.
- Validation: choose features, hyperparameters, model architecture, and stopping
  criteria. These designs do not fit the candidate model's parameters.
- Test: estimate performance on unseen design families after the model and
  preprocessing choices are fixed. Do not use test errors to redesign the model.

The assignments are manual and category-aware. There is no random seed for the
split because no random partition was used. `splits.json` is the authoritative
list; a seed alone would not document this grouped partition adequately.

All register rows, BOG representations, sampled paths, width/depth variants,
snapshots, and extracted submodules from one selected family must stay in its
one split. For example, all `sha256` rows are training rows. Splitting its
registers randomly between training and test would allow almost identical
design context into both sets and exaggerate generalization.

`FREEZE.lock.json` detects accidental edits to the selected designs, parameters,
split membership, constraints, and runner. It is an integrity check, not a file
permission lock. Commit the files so any intentional change has a visible
history and a new dataset version.

## The selected designs

Each category has four designs. There are 12 training, four validation, and four
test designs. Reference register counts are measured after Yosys lowering and
memory mapping, not guessed from source widths. Tool versions can change counts.

| Design ID | Design / fixed configuration | Split | Register bits |
|---|---|---|---:|
| `picorv32` | PicoRV32, counters/MUL/DIV/IRQ disabled | train | 1465 |
| `femtorv_quark` | FemtoRV32 Quark, 24-bit internal address width | train | 1215 |
| `osu_riscv32i` | ORFS OSU RV32I core, external instruction/data interfaces | validation | 1056 |
| `serv` | SERV with register file, W=1, CSR/compressed/MDU disabled | test | 1190 |
| `fastfir8` | FIR, 8 taps, 12-bit sample/coefficient, 28-bit output | train | 508 |
| `boxcar16` | 15-sample moving sum, 16-entry buffer, 16-bit input | train | 354 |
| `divider32` | ZipCPU standalone 32-bit signed/unsigned divider | validation | 142 |
| `seqcordic` | Sequential CORDIC, checked-in generated configuration | test | 152 |
| `aes128` | ORFS/OpenCores AES-128 encryption | train | 562 |
| `sha256` | Secworks SHA-224/256 core | train | 1034 |
| `chacha` | Secworks ChaCha core | validation | 1100 |
| `crc32` | CRC-32 registered wrapper, 8-bit input per cycle | test | 64 |
| `uart_tx` | UART transmitter, 8-bit data | train | 35 |
| `uart_rx` | UART receiver, 8-bit data | train | 44 |
| `simple_spi` | Wishbone SPI master, one slave-select output | train | 132 |
| `i2c_master` | I2C master core | validation | 72 |
| `axis_fifo16` | AXI-stream FIFO, 16 words x 32 bits | train | 593 |
| `axis_arb_mux4` | AXI-stream round-robin 4-to-1 mux, 32-bit data | train | 210 |
| `axis_skid32` | AXI-stream skid buffer, 32-bit data, REG_TYPE=2 | train | 67 |
| `sfifo16` | Independent synchronous FIFO, 16 x 32, registered read | test | 591 |

Category groups, in the same order as the table: processors/subsystems;
arithmetic/DSP; cryptography/checksum; protocol/control; interconnect/buffering.

The dataset has 10,586 reference register bits: 6,219 train, 2,370 validation,
and 1,997 test. These are **candidate endpoints, not validated label-row counts**.
Some endpoints may need documented exclusion during later STA/matching audits.

Why this mix: the selection covers bit-serial and word-wide CPU control,
arithmetic carry chains and multipliers, feedback accumulation, add/rotate/XOR,
substitution logic, counters/state machines, arbitration, and flop-based buffers.
Small controllers are retained intentionally; the assignment allows choosing
design complexity, and a small Codespace should be able to process this mix.

## Family grouping and limits

There are 20 named designs but only **15 conservative family groups**:

- PicoRV32 and FemtoRV are both training designs. FemtoRV documents an optional
  PicoRV-inspired shifter, so they are conservatively kept together even though
  that optional macro is not enabled in this configuration.
- FIR and boxcar are from one DSP-filter project, both training.
- UART TX/RX are from one UART project, both training.
- The three AXI-stream blocks are from one project, all training.
- Each remaining design has a separate family assignment.

Same function does not automatically mean same source family: the test FIFO is
an independent implementation, whereas a different depth of `axis_fifo` would
still belong to the training AXI-stream family. Whole selected RTL files are
also checked for exact duplicates across splits. This is not a complete
semantic clone-detection analysis.

Four test designs cannot cover all five categories. Protocol/control has a
validation design but no test design in v1. Do not claim held-out protocol
generalization. All four CPU implementations are RISC-V; this does not test
generalization across unrelated instruction-set architectures.

`tiny_adder` is excluded from all three groups. It was used to debug the pipeline
and remains an engineering smoke test, not an independent test design.

## Run in your existing VSCode Codespace

Upload `step4-designs.zip` to the project folder containing `part3-pipeline`,
then use Terminal > New Terminal:

```bash
python3 -m zipfile -e step4-designs.zip .
cd step4-designs
python3 scripts/dataset.py check
```

Expected:

```text
SPLIT CHECK PASS: 20 designs; 12 train / 4 validation / 4 test; 15 family groups
```

The selected RTL is already included, with its source notices. No additional
Python packages are required for this step. `design_manifest.csv` is the
human-readable inventory; `designs.json` is the complete machine-readable one.
Open these and `splits.json` in VSCode to inspect the exact configuration.

If a source file is absent, this optional command fetches the exact pinned file
and verifies its content hash; it never overwrites a modified local source:

```bash
python3 scripts/dataset.py fetch
```

The 20 designs were already screened during package preparation. To confirm
with your installed Yosys version, run:

```bash
export ORFS_ROOT=/OpenROAD-flow-scripts
python3 scripts/dataset.py screen --jobs 2
```

It uses the full Liberty under ORFS and, by default, the SOG Liberty from the
sibling `part3-pipeline/external/rtl-timer` folder. If your folders differ:

```bash
# Replace the following paths with the real absolute paths from your Part 3 setup.
export FULL_LIB=/your/actual/path/NangateOpenCellLibrary_typical.lib
export SOG_LIB=/your/actual/path/nangate45_sog.lib
python3 scripts/dataset.py screen --jobs 2
```

The reference library hashes are frozen. A different library must be resolved
explicitly; silently using different libraries across subsets invalidates a
clean model comparison. A per-design timeout defaults to 300 seconds per Yosys
invocation. Reduce `--jobs` to 1 if the Codespace has limited memory.

To rerun just one design after investigating a build issue:

```bash
python3 scripts/dataset.py screen --design fastfir8 --jobs 1
```

Screening outputs go into `build/<design_id>/` and `screening/`. These commands
do not change split assignments. A Yosys elaboration issue found for the FIR
was resolved by restoring the declared top name after parameter elaboration;
the upstream RTL itself was not edited.

## What has and has not been verified

All 20 selected configurations passed:

1. Native Yosys Verilog/SystemVerilog parsing and top-level elaboration.
2. Generic synthesis, flattening, and inferred-memory mapping.
3. No residual memories or latches; at least 16 register bits.
4. All generic flip-flops use the one declared rising-edge clock.
5. Complete Nangate45 library mapping with `check -assert -mapped`.
6. Restricted SOG library mapping with `check -assert -mapped`.

This is an eligibility screen. Functional simulation/formal proof for all 20
cores, per-register identity matching, and complete STA label coverage are
later checks. See `reference_screening.json` for the measured synthesis results.
No model was trained and no prediction errors were inspected to choose the split.

The supplied SDCs were also loaded with standalone OpenSTA 3.1.0 after linking
each mapped netlist; clock and actual D-pin counts were checked. This does not
replace a later endpoint-by-endpoint timing coverage audit in your OpenROAD build.

## Timing scenario and the transition from Part 3

Each selected design has `constraints/<design_id>.sdc`. The files use the same
10 ns clock period, 0.05 ns clock uncertainty/transition, 1 ns maximum I/O
delay, 0.05 ns input transition, and 5 fF output load. Resets are held inactive
using their actual polarity: for example, `aes128`'s `rst` and FemtoRV's `reset`
are active-low despite not having an `_n` suffix.

Map inferred storage to flip-flops for this experiment; do not introduce SRAM
macros only for some designs. Do not enable retiming. These are post-synthesis
timing experiments without explicit interconnect RC, not complete chip signoff.
Serial I/O timing is represented by a consistent synchronous input-delay model;
it is not a model of a real asynchronous UART/SPI/I2C external environment.

**The Part 3 runner is deliberately hard-coded to `tiny_adder` and 25 bits.**
Do not simply substitute another filename into it. The next batch-generation
step must read each design's `top`, file list, parameters, clock/reset, family,
and SDC from this manifest and generalize the endpoint matching and audits.
Do not apply the adder's reset polarity or 25-register assertion to other designs.

**Filter actual D pins in the generalized exporter.** In these Nangate45 runs,
OpenSTA's `all_registers -data_pins` also returned asynchronous `RN`/`SN` pins
for OSU RV32I, SHA-256, and Simple SPI. Counting that collection blindly would
produce 1,088, 2,068, and 140 pins instead of 1,056, 1,034, and 132 D endpoints.
Use the D pins identified through the mapped flip-flops and register matching;
for this specific library, `get_property $pin lib_pin_name` equal to `D` is a
useful additional filter. Reset recovery/removal checks are not data-arrival
labels. The tiny-adder pilot did not expose this because its mapped flip-flops
had no asynchronous reset pins.

For each design retain the BOG features separately from mapped-netlist labels,
and audit every endpoint. The batch dataset's `design` column must use the
canonical `design_id` in this package, not an arbitrary top-module name.

## Partition the dataset later, without changing membership

After batch generation produces one endpoint row per `(design, rtl_bit)` in a
single CSV, run this from `step4-designs`:

```bash
python3 scripts/dataset.py partition ../all_designs_dataset.csv --out partitioned
```

That future command creates `train.csv`, `validation.csv`, `test.csv`, and a
partition manifest. It rejects unknown design IDs, duplicate endpoint rows,
missing selected designs, and overwriting an existing partition directory.
It does **not** generate timing data. Do not run it until the input CSV exists.

For example, the conceptual operation is:

```python
train_rows = rows[rows['design'].isin(train_design_ids)]
validation_rows = rows[rows['design'].isin(validation_design_ids)]
test_rows = rows[rows['design'].isin(test_design_ids)]
```

Do not use a random train/test split on the endpoint rows. Fit scalers,
imputation, feature selection, and learned normalization on training rows only.
Apply those fitted transformations unchanged to validation and test rows.
Per-design BOG-only features such as rank may be computed for an unseen design;
they must not use that design's mapped arrival labels.

Large designs contribute many more register rows than small ones. Later report
both an overall endpoint-weighted metric and the mean of per-design metrics,
plus the four separate test-design results. That keeps a large CPU from hiding
poor performance on smaller test designs. Four test families support a small
pilot evaluation, not a precise estimate of performance on arbitrary RTL.

## Changes, failures, and backups

Routine bug fixes are possible. What is prohibited by the evaluation protocol
is moving difficult designs out of test or choosing replacements after seeing
their prediction errors. If generation fails, retain its failure log and reason.
Resolve the tool issue or publish a clearly documented `designs-v2` manifest
and rerun comparisons consistently. Never silently shrink the test set.

`backup_candidates.json` records fallback ideas tied to the same family and
split. They are unqualified candidates, excluded from v1 and its 20 designs.
None is currently needed because all 20 selected designs passed synthesis.
Any replacement requires source/dependency checks and an explicit new version
before model comparison; the scripts never substitute a backup automatically.

Within your existing private Git repository, record this choice:

```bash
git add designs.json splits.json FREEZE.lock.json design_manifest.csv \
  backup_candidates.json constraints scripts sources README.md TESTING.md \
  reference_screening.json .gitignore
git commit -m "Freeze designs-v1: 20 designs with family-grouped 12/4/4 split"
```

Source URLs, exact commit IDs, source hashes, and license evidence are in the
manifest. Keep the included notices with any redistributed source files.
