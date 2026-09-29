# Test comparison

| Model | Trees used | Macro MAE (ns) | Macro RMSE (ns) | Macro R² |
|---|---:|---:|---:|---:|
| baseline | 500 | 0.532503 | 0.598457 | -2.090698 |
| weighted | 500 | 0.482818 | 0.545337 | -1.616250 |
| weighted_es | 24 | 0.360702 | 0.395977 | -1.554309 |

Validation-selected model: weighted_es. Ties favor baseline, then weighted, then weighted_es.
Baseline and weighted-500 results/models were imported without retraining; source files were not modified.
The new candidate uses validation twice: to select tree count, then to compare candidates. Its validation score is a development score, not an independent final estimate.
Both round search and fresh refit use only the original 12 training designs.
Only four designs are present in this split. Inspect per-design MAE and R²; register rows are correlated.
If test labels were viewed in an earlier experiment, subsequent development is exploratory.

Test results do not change the validation-selected model.
