# RTL timing: three controlled XGBoost experiments

Start here. This repository preserves the selected experiment chain and its dependencies.

| Model | Weighting | Depth | Learning rate | Trees | Validation macro MAE (ns) |
|---|---|---:|---:|---:|---:|
| baseline | none | 50 | XGBoost default; see booster_config.json | 500 | 1.818556 |
| weighted | equal per design | 50 | XGBoost default; see booster_config.json | 500 | 1.729960 |
| weighted_es | equal per design | 6 | 0.05 | 24 | 1.212595 |

## Results

- [Three-model validation report](step6-early-stopping/runs/es_lr005_depth6/validation/REPORT.md)
- [Selected model parameters](step6-early-stopping/runs/es_lr005_depth6/models/weighted_es/result.json)
- [Early stopping learning curve](step6-early-stopping/runs/es_lr005_depth6/models/round_search/learning_curve.csv)
- [Frozen design split](step4-designs/splits.json)
- [Dataset audit](step5-features/runs/run2/data/AUDIT.md)
- [Final held-out test report](step6-early-stopping/runs/es_lr005_depth6/test/REPORT.md)

## Repository scope

The dataset uses 20 manually selected designs and a frozen 12/4/4 family-separated split. The 25 features come from one SOG representation and one selected BOG path per endpoint. Targets are full-library mapped-netlist arrival times in ns, not WNS/TNS or post-route signoff timing.

The baseline and weighting-only estimator settings match. The final weighted_es candidate also changes learning rate, depth, and tree-count selection. Its difference from weighting-only is a combined intervention.

round_search is an internal dependency of weighted_es, not a fourth independent experiment. Imported baseline/weighted model copies and input snapshots are retained to preserve historical integrity checks.

Only the selected run folders are included. The saved result.json files, run manifests, and this top-level page describe this export.

## Verify and reproduce

See [REPRODUCE.md](REPRODUCE.md). `python3 scripts/verify_export.py` verifies archived bytes without training or opening test labels. After cloning, run `git lfs pull` before verifying. New training uses a fresh output directory.

## Dependencies and attribution

Python packages are pinned in requirements.txt. Training/inference uses CPU XGBoost. Yosys/OpenROAD are needed only to regenerate synthesis and timing data; their recorded identities are in EXPORT_MANIFEST.json. The exact Liberty/LEF assets are under assets/. No EDA binaries or virtual environments are bundled.

Third-party RTL and RTL-Timer files retain their notices and source provenance. No new blanket license is asserted over those files. The take-home assignment and paper PDF are not included.
