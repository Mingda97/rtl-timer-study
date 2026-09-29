# Step 4 validation record

Validation date: 2026-09-28. All source files were checked against their pinned
repository revisions before selection. No source RTL was edited to pass checks.
No model training, validation scores, or test prediction errors were used.

The final runner was executed across all 20 selected configurations with
Yosys 0.33 (`2584903a060`). Every design passed both full Nangate45 and the
authors' restricted SOG mappings, `check -assert -mapped`, no-residual-memory
and no-latch checks, and the single declared positive-edge clock check.

The measured register-bit range was 35–1465 and the total was 10,586. The
per-design counts are in `reference_screening.json` and `design_manifest.csv`.
These are structural D-endpoint counts, not fully audited timing-label counts.

Standalone OpenSTA 3.1.0 at source revision
`ef17b3fd81404d685d33ec65e0354c122d701e4d` successfully linked all 20 mapped
netlists and loaded their supplied SDCs. One clock and the expected number of
actual D pins were checked. Full timing paths/labels were not exported, and
the user's particular OpenROAD build was not executed here.

The SDC checks exposed asynchronous reset/set pins in `all_registers -data_pins`
for three designs. The audit counted actual `D` pins and excluded `RN`/`SN`;
the README documents the required adjustment to the future batch label exporter.

Additional integrity/partition checks:

- Exactly 20 unique design IDs, four designs per category, and a 12/4/4 split.
- All 15 conservative family groups are confined to one split each.
- No exact selected RTL-file hash appears across different splits.
- Every included source/notice file matches its recorded SHA-256 hash.
- A synthetic identity-only CSV partitions to exactly 12/4/4 design rows with
  membership identical to `splits.json`.
- Missing designs, unknown/pilot IDs, and duplicate endpoint rows are rejected.
- A modified copy of `splits.json` fails the freeze checksum check.

Synthetic partition fixtures were only used to test routing and rejection;
they were not timing data and are not included as a dataset.

This record establishes reproducible selection and synthesis eligibility.
It does not establish functional signoff, complete endpoint matching, timing
label quality, or predictive performance. Those are subsequent workflow steps.
