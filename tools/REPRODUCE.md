# Inspect or reproduce the selected experiment chain

Start with README.md and EXPORT_MANIFEST.json. This export contains the actual selected
run files and source versions verified against the historical code/data hashes.
It does not merge or refactor the training implementations.

## 1. Inspect the archived results

From the repository root, in the Bash terminal in VSCode/Codespaces:

```bash
git lfs pull
python3 scripts/verify_export.py
source scripts/env.sh
cat "$FINAL_RUN/validation/REPORT.md"
```

If the final test was completed before export:

```bash
cat "$FINAL_RUN/test/REPORT.md"
```

The baseline and weighted result/model files inside the final run are imported copies.
The round_search directory is part of weighted_es: it preserves the selected tree
count, learning curve, and search checkpoint. There are only three compared candidates.

Historical result.json files may contain `test_evaluated: false`: final-test writes
its outputs to a separate test/ folder and does not update those older JSON files.
Use test/complete.json and test/REPORT.md to identify completed final evaluation.

Export verification does not train, invoke EDA tools, unpickle data, or parse test
labels. On Git LFS pointer errors, install Git LFS and run `git lfs pull` first.
Export integrity does not certify functional equivalence of the HDL designs.

## 2. Python dependencies

Use Linux/Codespaces and the Python version recorded in EXPORT_MANIFEST.json when
reproducing. These packages are pinned to the original training requirements:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip check
```

Do not copy an old .venv into the repository. Requirements are sufficient to install
Python dependencies. EDA executables are separate dependencies; they are unnecessary
for training from the archived feature dataset.

## 3. Optional fresh reproduction of the three training experiments

This retrains into new folders under reproduced/, which Git ignores. It is not required
to upload the repository or inspect the saved results. Do not edit archived results or
disable fingerprint checks to force reuse across environments. Preserve the fixed
train/validation/test split. Once test results have been examined, reproduction is a
rerun of the fixed protocol, not another opportunity to tune using those results.

Run these commands from the repository root after activating the environment:

```bash
source scripts/env.sh

REPRO_PREVIOUS="$RTL_FINAL_ROOT/reproduced/weighting_only"
REPRO_FINAL="$RTL_FINAL_ROOT/reproduced/weighted_es_depth6"

python step6-weighting-only/scripts/train.py preflight \
  --input-run "$DATA_RUN" --out "$REPRO_PREVIOUS" --threads "$TRAIN_THREADS"
python step6-weighting-only/scripts/train.py baseline \
  --input-run "$DATA_RUN" --out "$REPRO_PREVIOUS" --threads "$TRAIN_THREADS" --timeout 1200
python step6-weighting-only/scripts/train.py weighted \
  --input-run "$DATA_RUN" --out "$REPRO_PREVIOUS" --threads "$TRAIN_THREADS" --timeout 1200
python step6-weighting-only/scripts/train.py compare \
  --input-run "$DATA_RUN" --out "$REPRO_PREVIOUS" --threads "$TRAIN_THREADS"

python step6-early-stopping/scripts/train.py preflight \
  --input-run "$DATA_RUN" --previous-run "$REPRO_PREVIOUS" --out "$REPRO_FINAL" --threads "$TRAIN_THREADS"
python step6-early-stopping/scripts/train.py import-previous \
  --input-run "$DATA_RUN" --previous-run "$REPRO_PREVIOUS" --out "$REPRO_FINAL" --threads "$TRAIN_THREADS"
python step6-early-stopping/scripts/train.py select-rounds \
  --input-run "$DATA_RUN" --previous-run "$REPRO_PREVIOUS" --out "$REPRO_FINAL" --threads "$TRAIN_THREADS" --timeout 1200
python step6-early-stopping/scripts/train.py refit \
  --input-run "$DATA_RUN" --previous-run "$REPRO_PREVIOUS" --out "$REPRO_FINAL" --threads "$TRAIN_THREADS" --timeout 1200
python step6-early-stopping/scripts/train.py compare \
  --input-run "$DATA_RUN" --previous-run "$REPRO_PREVIOUS" --out "$REPRO_FINAL" --threads "$TRAIN_THREADS"
```

Stop if any command fails. The early-stopping package is copied from the selected
depth-6 / learning-rate-0.05 run, with matching code hashes. Its older package README
may describe the initial defaults; the exported result and code are authoritative.
Check the new models/weighted_es/result.json before comparing reproduced results.

Only when performing the fixed final evaluation:

```bash
python step6-early-stopping/scripts/train.py final-test \
  --input-run "$DATA_RUN" --previous-run "$REPRO_PREVIOUS" --out "$REPRO_FINAL" --threads "$TRAIN_THREADS"
```

Exact numerical identity can depend on Python, package, tool and platform versions.
Recorded predictions and model bytes remain the evidence for the original experiment.

## 4. Optional regeneration from RTL

The export includes the 20 pinned RTL configurations, constraints, synthesis and
extraction code, and the exact Liberty/LEF files used for the recorded dataset. Source
repository URLs/revisions and licenses remain in the Step 4 manifest and source tree.

It omits large generated netlists and raw STA archives. The small retained build/
files are synthesis scripts and counts, not complete reusable synthesis outputs.
Step 5 run data is an audited training snapshot, not a resumable full timing run.
The endpoint inspection example requires regenerated raw paths if they are absent.

Use the original working Yosys/OpenROAD environment. Their recorded version strings
and binary hashes are preserved, including a binary identity if OpenROAD reports
`unknown`. The binaries themselves and a Docker image are not bundled. This is not
a claim that the repository alone provisions an identical EDA toolchain.

```bash
source scripts/env.sh
yosys -V
openroad -version

cd "$RTL_FINAL_ROOT/step4-designs"
python3 scripts/dataset.py check
python3 scripts/dataset.py screen --jobs 1 --timeout 1200

cd "$RTL_FINAL_ROOT/step5-features"
python3 scripts/extract.py run \
  --dataset-root "$RTL_FINAL_ROOT/step4-designs" \
  --out "$RTL_FINAL_ROOT/reproduced/step5" --jobs 1 --timeout 1200
python3 scripts/extract.py audit \
  --dataset-root "$RTL_FINAL_ROOT/step4-designs" \
  --out "$RTL_FINAL_ROOT/reproduced/step5"
```

For a source run using standalone OpenSTA, use its recorded backend and executable
instead of the default OpenROAD backend; refer to the Step 5 CLI help. For new tool
versions, use a fresh dataset/run version and inspect the audit rather than rewriting
the original recorded hashes.

## 5. Keeping the original evidence intact

Historical manifests can contain original absolute workspace paths. Those strings are
provenance; they are deliberately not rewritten. The env.sh file supplies the current
paths for explicit command arguments. Other identity inputs, especially platform and
software versions, may still prevent resuming a historical run elsewhere. Fresh
reproduction outputs avoid mixing old and new run identities.

After intentionally editing code, the exported snapshot verifier will flag the changed
file. Keep the initial snapshot commit/tag for the interview submission. Do not rewrite
historical completion files to make later changes look like the original experiment.
