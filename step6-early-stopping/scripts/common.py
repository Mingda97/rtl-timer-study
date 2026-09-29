#!/usr/bin/env python3
"""Shared audited-data and metric helpers, adapted from the weighting-only package."""
import argparse
import ast
from collections import Counter
from contextlib import contextmanager
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import pickle
import platform
import shutil
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
TARGET = 'label_arrival_ns'
META = ['design', 'rtl_bit', 'split', 'family', 'category', 'bog_path_kind']
PACKAGES = ['xgboost-cpu', 'numpy', 'pandas', 'scipy', 'scikit-learn', 'matplotlib']


def need(ok, message):
    if not ok:
        raise RuntimeError(message)


def load(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n')
    temp.replace(path)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def object_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def safe_child(root, name):
    child = (root / name).resolve()
    need(child.is_relative_to(root.resolve()), f'Unexpected path in checksum record: {name}')
    return child


def verify_files(root, hashes):
    for name, digest in hashes.items():
        path = safe_child(root, name)
        need(path.is_file() and sha(path) == digest, f'Missing or changed file: {path}')


def versions():
    return {name: importlib.metadata.version(name) for name in PACKAGES}


def preflight(args):
    source = args.input_run.resolve()
    data = source / 'data'
    need((data / 'OUTPUTS.sha256.json').is_file(),
         f'Missing audited Step 5 data: {data}. Complete the full Step 5 run and audit first.')
    hashes = load(data / 'OUTPUTS.sha256.json')
    required = {'train.csv', 'validation.csv', 'test.csv', 'feature_columns.json',
                'partition_manifest.json', 'audit_summary.json', 'coverage_by_design.csv', 'exclusions.csv'}
    need(required.issubset(hashes), 'Step 5 output inventory is incomplete.')
    verify_files(data, hashes)  # Test bytes are hashed, never parsed here.
    step5 = load(source / 'run_manifest.json')
    need(object_sha(step5['signature']) == step5['fingerprint'], 'Step 5 manifest fingerprint mismatch.')
    partition = load(data / 'partition_manifest.json')
    audit = load(data / 'audit_summary.json')
    features = load(data / 'feature_columns.json')
    expected_features = load(ROOT / 'feature_columns.json')
    need(features == expected_features == step5['signature']['feature_columns'], 'Unexpected feature schema/order.')
    need(partition['fingerprint'] == step5['fingerprint'], 'Step 5 audit and inputs are from different runs.')
    need(audit['status'] in ('PASS', 'PASS_WITH_EXCLUSIONS') and audit['designs'] == 20
         and audit['feature_count'] == 25, 'Step 5 audit did not pass for all 20 designs.')
    split = partition['designs']
    need({s: len(v) for s, v in split.items()} == {'train':12, 'validation':4, 'test':4},
         'Expected frozen 12/4/4 design split.')
    need(sum(map(len, split.values())) == len(set(sum(split.values(), []))), 'Overlapping design partitions.')
    need(partition['rows'] == audit['split_row_counts'], 'Step 5 row counts disagree.')
    import pandas as pd
    coverage = pd.read_csv(data / 'coverage_by_design.csv')
    need(len(coverage) == 20 and coverage['design'].is_unique, 'Invalid design coverage table.')
    need(set(coverage['design']) == set(sum(split.values(), [])), 'Design membership mismatch.')
    need(coverage.groupby('family')['split'].nunique().max() == 1, 'Family leakage across partitions.')
    need((coverage['coverage_all_common'] >= .95).all(), 'Per-design coverage below frozen threshold.')
    for s, names in split.items():
        need(set(coverage.loc[coverage['split'] == s, 'design']) == set(names), 'Coverage split mismatch.')
        need(int(coverage.loc[coverage['split'] == s, 'dataset_rows'].sum()) == partition['rows'][s],
             f'Coverage row mismatch: {s}')

    vendor = ROOT / 'third_party/rtl_timer'
    provenance = load(vendor / 'PROVENANCE.json')
    verify_files(vendor, provenance['sha256'])
    code_files = [ROOT/'protocol.json', ROOT/'feature_columns.json', ROOT/'requirements.txt']
    code_files += list((ROOT/'scripts').glob('*.py')) + [p for p in vendor.iterdir() if p.is_file()]
    software = versions()
    required_versions = dict(line.split('==') for line in (ROOT/'requirements.txt').read_text().splitlines() if line.strip())
    need(software == required_versions, 'Use the pinned Step 6 environment: python -m pip install -r requirements.txt')
    signature = {'step5_fingerprint': step5['fingerprint'], 'input_data_sha256': hashes,
                 'step5_manifest_sha256': sha(source/'run_manifest.json'),
                 'data_index_sha256': sha(data/'OUTPUTS.sha256.json'),
                 'code_sha256': {str(p.relative_to(ROOT)):sha(p) for p in sorted(code_files)},
                 'versions':software, 'python':platform.python_version(), 'platform':platform.platform(),
                 'threads':args.threads, 'protocol':load(ROOT/'protocol.json')}
    signature['previous_experiment'] = args.previous_identity
    fingerprint = object_sha(signature)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out/'run_manifest.json'
    if manifest_path.exists():
        need(load(manifest_path)['fingerprint'] == fingerprint,
             'Data, code, environment, or thread setting changed. Choose a new --out directory.')
        verify_files(out/'input_snapshot/data', hashes)
        need(sha(out/'input_snapshot/run_manifest.json') == signature['step5_manifest_sha256'],
             'Input snapshot manifest changed.')
    else:
        snapshot = out/'input_snapshot'
        (snapshot/'data').mkdir(parents=True, exist_ok=True)
        for name in hashes:
            shutil.copy2(data/name, snapshot/'data'/name)
        shutil.copy2(data/'OUTPUTS.sha256.json', snapshot/'data/OUTPUTS.sha256.json')
        shutil.copy2(source/'run_manifest.json', snapshot/'run_manifest.json')
        save(manifest_path, {'fingerprint':fingerprint, 'signature':signature, 'input_run':str(source),
                            'created_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                            'cpu_count':os.cpu_count(), 'upstream_commit':provenance['commit']})
    ctx = {'out':out, 'data':out/'input_snapshot/data', 'fingerprint':fingerprint,
           'features':features, 'partition':partition, 'coverage':coverage,
           'protocol':signature['protocol'], 'threads':args.threads}
    print(f"PREFLIGHT PASS: 20 designs; 25 features; rows={partition['rows']}; CPU threads={args.threads}", flush=True)
    return ctx


def read_partition(ctx, split):
    import numpy as np
    import pandas as pd
    frame = pd.read_csv(ctx['data']/(split+'.csv'))
    need(set(frame.columns) == set(META + ctx['features'] + [TARGET]), 'Unexpected CSV columns.')
    need(len(frame) == ctx['partition']['rows'][split], f'Row count mismatch: {split}')
    names = ctx['partition']['designs'][split]
    need(set(frame['design']) == set(names) and (frame['split'] == split).all(), f'Wrong designs in {split}')
    need(not frame.duplicated(['design', 'rtl_bit']).any(), f'Duplicate endpoints in {split}')
    need(np.isfinite(frame[ctx['features'] + [TARGET]].to_numpy(float)).all(), 'Non-finite input values.')
    families = ctx['coverage'].set_index('design')['family'].to_dict()
    need((frame['design'].map(families) == frame['family']).all(), 'Design family mismatch.')
    actual_counts = frame.groupby('design').size().to_dict()
    for name in names:
        need(actual_counts[name] == int(ctx['coverage'].set_index('design').loc[name, 'dataset_rows']),
             f'Row count mismatch for {name}')
    order = {name:i for i, name in enumerate(names)}
    frame['_order'] = frame['design'].map(order)
    return frame.sort_values(['_order','rtl_bit']).drop(columns='_order').reset_index(drop=True)


def xy(ctx, frame):
    import pandas as pd
    # Upstream creates unnamed integer DataFrame columns. Match that exactly.
    X = pd.DataFrame(frame[ctx['features']].to_numpy(dtype=float))
    y = frame[TARGET].to_numpy(dtype=float)
    return X, y


def design_weights(frame):
    import numpy as np
    counts = frame['design'].value_counts()
    weights = frame['design'].map(len(frame)/(len(counts)*counts)).to_numpy(float)
    need(np.isclose(weights.mean(), 1), 'Weights must have mean one.')
    totals = {d:float(weights[frame['design'].to_numpy() == d].sum()) for d in counts.index}
    need(np.allclose(list(totals.values()), len(frame)/len(counts)), 'Design weights are not balanced.')
    return weights


def metric_values(y, prediction):
    import numpy as np
    from scipy.stats import spearmanr
    y, prediction = np.asarray(y, dtype=float), np.asarray(prediction, dtype=float)
    error = prediction - y
    variable_y = len(y) > 1 and np.ptp(y) > 1e-12
    variable_prediction = len(y) > 1 and np.ptp(prediction) > 1e-12
    return {'mae_ns':float(np.mean(abs(error))), 'rmse_ns':float(np.sqrt(np.mean(error**2))),
            'r2':float(1-np.sum(error**2)/np.sum((y-y.mean())**2)) if variable_y else None,
            'pearson_r':float(np.corrcoef(y,prediction)[0,1]) if variable_y and variable_prediction else None,
            'spearman_r':float(spearmanr(y,prediction).statistic) if variable_y and variable_prediction else None}


def evaluate(frame, prediction):
    import numpy as np
    need(len(prediction) == len(frame) and np.isfinite(prediction).all(), 'Invalid predictions.')
    per_design = []
    for name in frame['design'].drop_duplicates():
        index = frame['design'].to_numpy() == name
        subset = frame.loc[index]
        per_design.append({'design':name, 'family':subset['family'].iloc[0], 'rows':int(index.sum()),
                           **metric_values(subset[TARGET].to_numpy(float), prediction[index])})
    macro, defined = {}, {}
    for metric in ['mae_ns', 'rmse_ns', 'r2', 'pearson_r', 'spearman_r']:
        valid = [r[metric] for r in per_design if r[metric] is not None]
        macro[metric] = float(np.mean(valid)) if valid else None
        defined[metric] = len(valid)
    return {'rows':len(frame), 'designs':len(per_design), 'macro':macro,
            'defined_design_counts':defined, 'pooled':metric_values(frame[TARGET], prediction),
            'per_design':per_design, 'negative_predictions':int((prediction < 0).sum())}


def write_predictions(path, frame, prediction):
    result = frame[META].copy()
    result['actual_arrival_ns'] = frame[TARGET]
    result['predicted_arrival_ns'] = prediction
    result['absolute_error_ns'] = abs(prediction - frame[TARGET].to_numpy(float))
    result.to_csv(path, index=False)



def verify_complete(ctx, directory):
    marker = directory/'complete.json'
    if not marker.exists():
        return False
    record = load(marker)
    need(record['fingerprint'] == ctx['fingerprint'], f'Mixed experiment: {directory}')
    verify_files(directory, record['outputs_sha256'])
    return True

def complete(ctx, directory):
    save(directory/'complete.json', {'fingerprint':ctx['fingerprint'],
         'outputs_sha256':{str(p.relative_to(directory)):sha(p) for p in directory.rglob('*')
                           if p.is_file() and p.name != 'complete.json'}})
