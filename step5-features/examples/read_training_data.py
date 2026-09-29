#!/usr/bin/env python3
"""Read audited training inputs and targets without fitting a model."""
import argparse
import csv
import json
from pathlib import Path
import numpy as np

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--run', type=Path, default=Path(__file__).resolve().parents[1] / 'runs/run1')
args = p.parse_args()
columns = json.loads((args.run / 'data/feature_columns.json').read_text())
with (args.run / 'data/train.csv').open(newline='') as stream:
    rows = list(csv.DictReader(stream))
assert rows and all(row['split'] == 'train' for row in rows)
X = np.asarray([[float(row[c]) for c in columns] for row in rows], dtype=float)
y = np.asarray([float(row['label_arrival_ns']) for row in rows], dtype=float)
assert np.isfinite(X).all() and np.isfinite(y).all()
print('Feature columns:', columns)
print('X:', X.shape)
print('y:', y.shape)
print('First row identity:', rows[0]['design'], rows[0]['rtl_bit'])
print('First row BOG arrival:', rows[0]['bog_arrival_ns'], 'ns')
print('First row target arrival:', y[0], 'ns')
