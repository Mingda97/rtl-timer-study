# Step 5 validation record

Validation date: 2026-09-28.

## Actual execution

The final packaged extraction code ran across all 20 frozen designs, using:

- Yosys 0.33 (git sha1 2584903a060)
- Standalone OpenSTA: `3.1.0`
- OpenSTA source revision: `ef17b3fd81404d685d33ec65e0354c122d701e4d`
- NumPy: `2.3.5`
- RTL-Timer source revision: `206ff4078368c251d2fafaffcc648282c68316f1`
- Full Liberty SHA-256: `8d540a4d4cf6d09d27c87ad067857a9c0c2eeb023ab7a56e058cd3113db4e9b1`
- Restricted SOG Liberty SHA-256: `a04c9abd0862d3ad5562f64c6fa2e43f38a86a4aeff41965244aa5275e151f7c`

The input netlists came from the complete Step 4 run with the recorded
`scopeinfo-fix-1` pipeline revision. No source RTL, frozen design parameter,
constraint, or family assignment was changed for Step 5.

Results:

| Item | Measured result |
|---|---:|
| Designs | 20 |
| Conservative families | 15 |
| Common checkpoint flip-flops | 10,586 |
| Matched public RTL bits / retained rows | 10,576 |
| Excluded private-name PicoRV32 FFs | 10 |
| Numeric BOG features | 25 |
| Training rows | 6,209 |
| Validation rows | 2,370 |
| Test rows | 1,997 |
| BOG input-starting / register-starting rows | 4,611 / 5,965 |
| Mapped input-starting / register-starting rows | 4,765 / 5,811 |
| Endpoint/polarity queries per branch | 21,172 |
| Endpoint/polarity period-invariance checks | 21,172 |
| Missing timing queries in either main branch | 0 |
| Single-polarity retained labels | 0 |
| Control pins excluded in BOG / mapped inventories | 2,148 / 1,074 |
| Identical complete feature vectors crossing splits | 0 |

The register population here is the post-lowering common checkpoint population.
It is not a count of every original RTL declaration before optimization.

## Checks exercised

- Complete alias matching, D-pin inventory, both polarity queries, timing unit
  conversions, finite features/labels, and split-family integrity.
- The 10 ns versus 20 ns test kept arrivals unchanged and moved required time
  and slack by 10 ns for every reported mapped endpoint/polarity.
- The original full extraction and final-code full extraction produced
  byte-identical `features.csv`, `labels.csv`, `dataset.csv`, and partition CSVs.
- Rerunning a completed UART job reused its verified outputs.
- An isolated copy with an altered `dataset.csv` was rejected by the audit
  with `Output changed`; the reference results were not modified.
- A polarity fixture with maximum arrival on one edge but minimum slack on
  the other confirmed that the label selector follows maximum arrival.
- Both provided data-reading and endpoint-inspection examples were executed.
- Training-only distribution summaries were generated without fitting a
  model, removing outliers, scaling, or selecting features.

## Included evidence

`reference_results/` contains the measured run manifest, per-design register
maps and inventories, both original timing polarities in compressed JSONL,
period-test indices, manual examples, complete audit reports, and final CSVs.
It is a reference run; default commands write your results into `runs/run1/`.
Absolute paths in the reference manifest describe the environment where that
run occurred. Reproduction uses file hashes and your actual configured paths.

## Limits

The user's OpenROAD executable was not available in this execution environment;
the same Tcl interface was exercised through standalone OpenSTA 3.1.0. Run the
provided UART pilot with your OpenROAD build before the full batch.

This establishes structural and timing/data consistency for the selected
single-clock, post-synthesis scenario. It does not establish full 20-design
formal equivalence, silicon or post-route accuracy, or model generalization.
The SOG-only, single-path scope and differences from the full paper are
explained in README.md. No model was trained and no test prediction score was
used in this step.
