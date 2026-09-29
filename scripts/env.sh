# Source from Bash: source scripts/env.sh
RTL_FINAL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export RTL_FINAL_ROOT
export FULL_LIB="$RTL_FINAL_ROOT"/assets/full_lib/NangateOpenCellLibrary_typical.lib
export SOG_LIB="$RTL_FINAL_ROOT"/assets/sog_lib/nangate45_sog.lib
export TECH_LEF="$RTL_FINAL_ROOT"/assets/tech_lef/NangateOpenCellLibrary.tech.lef
export CELL_LEF="$RTL_FINAL_ROOT"/assets/cell_lef/NangateOpenCellLibrary.macro.mod.lef
export DATA_RUN="$RTL_FINAL_ROOT"/step5-features/runs/run2
export PREVIOUS_RUN="$RTL_FINAL_ROOT"/step6-weighting-only/runs/weighting_only
export FINAL_RUN="$RTL_FINAL_ROOT"/step6-early-stopping/runs/es_lr005_depth6
export TRAIN_THREADS=25
