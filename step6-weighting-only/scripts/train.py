#!/usr/bin/env python3
"""Upstream baseline vs equal-design training weights, with identical hyperparameters. See README.md."""
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


@contextmanager
def in_directory(directory):
    previous = Path.cwd()
    os.chdir(directory)
    try:
        yield
    finally:
        os.chdir(previous)


def train_upstream(ctx, train, directory):
    """Execute the unmodified upstream training() AST against our data adapter."""
    import numpy as np
    import pandas as pd
    import xgboost as xgb
    source = ROOT/'third_party/rtl_timer/train_infer_k_fold_BOG.slack.py'
    tree = ast.parse(source.read_text())
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'training']
    need(len(functions) == 1, 'Upstream training function not found uniquely.')
    function_source = ast.get_source_segment(source.read_text(), functions[0])
    (directory/'upstream_training_function.py.txt').write_text(function_source+'\n')
    interface = directory/'upstream_adapter'
    feature_dir = interface/'preprocess/feat_label_timing'
    workdir = interface/'RTL_timing_model'
    feature_dir.mkdir(parents=True, exist_ok=True)
    workdir.mkdir(parents=True, exist_ok=True)
    counts = {}
    for design in ctx['partition']['designs']['train']:
        records = []
        for _, row in train.loc[train['design'] == design].iterrows():
            vector = [float(row[c]) for c in ctx['features']]
            records.append({'name':row['rtl_bit'], 'feat_design':vector[:6], 'feat_path':vector[6:],
                            'label_slack':float(row[TARGET])})
        # These are fresh local adapter files; no external pickle is loaded.
        with (feature_dir/(design+'_sog_init.pkl')).open('wb') as f:
            pickle.dump(records,f,protocol=4)
        counts[design] = len(records)
    need(sum(counts.values()) == len(train) and len(counts) == 12, 'Upstream adapter dropped training rows.')
    seen = {}
    def constructor(**kwargs):
        need(kwargs == ctx['protocol']['baseline'], 'Unexpected upstream estimator constructor.')
        if ctx['threads'] != 25:
            kwargs['nthread'] = ctx['threads']  # Optional, documented runtime-only override.
        seen.update(kwargs)
        return xgb.XGBRegressor(**kwargs)
    namespace = {'np':np, 'pd':pd, 'pickle':pickle, 'cmd':'sog', 'label_cmd':'_init',
                 'xgb':SimpleNamespace(XGBRegressor=constructor)}
    compiled = compile(ast.Module(body=functions, type_ignores=[]), str(source), 'exec')
    exec(compiled, namespace)
    with in_directory(workdir):
        model = namespace['training'](ctx['partition']['designs']['train'])
    need(model.n_features_in_ == 25 and model.get_booster().num_boosted_rounds() == 500,
         'Unexpected baseline fit shape or tree count.')
    save(directory/'adapter_record.json', {'source_sha256':sha(source), 'rows_by_design':counts,
         'total_training_rows':len(train), 'feature_order':ctx['features'], 'effective_constructor':seen,
         'data_key_mapping':{'label_slack':'label_arrival_ns (ns; values copied without transformation)',
                             'feat_design':'first 6 Step 5 features', 'feat_path':'remaining 19 Step 5 features'},
         'scope':'unmodified training() only; upstream leave-one-design-out driver is NOT executed'})
    return model


def train_weighted(ctx, train):
    """Same constructor and rows as the baseline; sample_weight is the sole intervention."""
    import xgboost as xgb
    X_train, y_train = xy(ctx, train)
    params = dict(ctx['protocol']['baseline'])
    params['nthread'] = ctx['threads']  # Apply the same optional override to both fits.
    model = xgb.XGBRegressor(**params)
    model.fit(X_train, y_train, sample_weight=design_weights(train))
    return model


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


def fit_worker(ctx, which):
    import pandas as pd
    need(not (ctx['out']/'evaluation_lock.json').exists(), 'Test evaluation has begun; training is closed for this run.')
    directory = ctx['out']/'models'/which
    directory.mkdir(parents=True, exist_ok=True)
    if verify_complete(ctx, directory):
        print('REUSE:', which, flush=True)
        return
    train, validation = read_partition(ctx, 'train'), read_partition(ctx, 'validation')
    X_train, y_train = xy(ctx, train)
    X_val, y_val = xy(ctx, validation)
    weights = design_weights(train)
    applied = weights if which == 'weighted' else weights * 0 + 1
    weight_rows = []
    for design, count in train['design'].value_counts().items():
        mask = train['design'].to_numpy() == design
        weight_rows.append({'design':design, 'rows':int(count),
                           'unweighted_fraction':float(count/len(train)),
                           'equal_design_row_weight':float(weights[mask][0]),
                           'applied_row_weight':float(applied[mask][0]),
                           'applied_total_weight':float(applied[mask].sum()),
                           'applied_fraction':float(applied[mask].sum()/applied.sum())})
    pd.DataFrame(weight_rows).to_csv(directory/'design_weights.csv',index=False)
    started = time.perf_counter()
    if which == 'baseline':
        model = train_upstream(ctx, train, directory)
    else:
        model = train_weighted(ctx, train)
    elapsed = time.perf_counter() - started
    model.save_model(directory/'model.ubj')
    (directory/'booster_config.json').write_text(model.get_booster().save_config()+'\n')
    metrics = {}
    for split, frame, X in [('train',train,X_train),('validation',validation,X_val)]:
        prediction = model.predict(X)
        metrics[split] = evaluate(frame,prediction)
        write_predictions(directory/(split+'_predictions.csv'),frame,prediction)
    rounds = model.get_booster().num_boosted_rounds()
    need(rounds == 500, 'Both models must fit all 500 rounds; no early stopping.')
    result = {'model':which, 'fit_seconds':elapsed, 'early_stopping_used':False,
              'last_iteration_zero_based':rounds-1,
              'trees_used_for_prediction':rounds, 'trees_in_saved_booster':rounds,
              'effective_parameters':{k:('NaN (missing-value sentinel)' if isinstance(v,float) and math.isnan(v) else v)
                                      for k,v in model.get_params().items()}, 'metrics':metrics,
              'test_evaluated':False, 'training_designs':ctx['partition']['designs']['train']}
    save(directory/'result.json',result)
    complete(ctx,directory)
    print(f"FIT PASS {which}: {elapsed:.2f}s; validation macro MAE={metrics['validation']['macro']['mae_ns']:.6f} ns", flush=True)


def run_fit(ctx, args, which):
    need(not (ctx['out']/'evaluation_lock.json').exists(), 'Final test has begun; no further training in this run.')
    directory = ctx['out']/'models'/which
    if verify_complete(ctx,directory):
        r = load(directory/'result.json')
        print(f"REUSE {which}: verified model; validation macro MAE={r['metrics']['validation']['macro']['mae_ns']:.6f} ns")
        return
    logs = ctx['out']/'logs'
    logs.mkdir(exist_ok=True)
    command = [sys.executable,str(Path(__file__).resolve()),'_fit','--model',which,
               '--input-run',str(args.input_run.resolve()),'--out',str(ctx['out']),
               '--threads',str(args.threads)]
    started = time.monotonic()
    print(f'START {which}: fit log {logs/(which+".log")}',flush=True)
    with (logs/(which+'.log')).open('w') as stream:
        process = subprocess.Popen(command,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            while True:
                remaining = args.timeout - (time.monotonic()-started)
                need(remaining > 0, f'{which} exceeded {args.timeout}s. Partial outputs are not marked complete.')
                try:
                    code = process.wait(timeout=min(30,remaining))
                    break
                except subprocess.TimeoutExpired:
                    print(f'{which}: running, {time.monotonic()-started:.0f}s elapsed',flush=True)
        except BaseException:
            try:
                os.killpg(process.pid,signal.SIGTERM)
                process.wait(timeout=5)
            except (ProcessLookupError,subprocess.TimeoutExpired):
                try: os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError: pass
                process.wait()
            raise
    tail = '\n'.join((logs/(which+'.log')).read_text(errors='replace').splitlines()[-8:])
    need(code == 0, f'Fit failed; inspect {logs/(which+".log")}\n{tail}')
    need(verify_complete(ctx,directory), 'Fit exited without a verified completion record.')
    print(tail,flush=True)


def plot_comparison(directory, tables, title):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    names = [r['design'] for r in tables['baseline']['per_design']]
    x = np.arange(len(names))
    fig, ax = plt.subplots(figsize=(8.8,4.8),layout='constrained')
    for delta, model, color in [(-.19,'baseline','#3b6ea8'),(.19,'weighted','#c46b27')]:
        lookup = {r['design']:r['mae_ns'] for r in tables[model]['per_design']}
        ax.bar(x+delta,[lookup[n] for n in names],width=.36,label=model,color=color)
    ax.set_xticks(x,names,rotation=15,ha='right')
    ax.set_ylabel('Mean absolute error (ns) — lower is better')
    ax.set_title(title)
    ax.legend(frameon=False)
    ax.spines[['top','right']].set_visible(False)
    ax.grid(axis='y',alpha=.15)
    ax.set_axisbelow(True)
    fig.savefig(directory/'mae_by_design.png',dpi=170)
    plt.close(fig)


def comparison(ctx, split, tables, directory, selected=None):
    import pandas as pd
    directory.mkdir(parents=True,exist_ok=True)
    b, c = [tables[m]['macro']['mae_ns'] for m in ('baseline','weighted')]
    change = {'baseline_macro_mae_ns':b,'weighted_macro_mae_ns':c,
              'weighted_minus_baseline_ns':c-b,'relative_mae_reduction_percent':100*(b-c)/b if b else None,
              'better_on_this_split':'weighted' if c < b else 'baseline',
              'selection_metric':'macro MAE in ns (equal average across designs)'}
    save(directory/'comparison.json',{'split':split, 'models':tables, 'comparison':change,
                                    'selected_on_validation':selected})
    records = []
    for model, stats in tables.items():
        records.append({'model':model, **{'macro_'+k:v for k,v in stats['macro'].items()},
                        **{'pooled_'+k:v for k,v in stats['pooled'].items()}})
    pd.DataFrame(records).to_csv(directory/'summary.csv',index=False)
    per_design = []
    baseline = {r['design']:r for r in tables['baseline']['per_design']}
    weighted = {r['design']:r for r in tables['weighted']['per_design']}
    for design in baseline:
        per_design.append({'design':design, 'rows':baseline[design]['rows'],
                           'baseline_mae_ns':baseline[design]['mae_ns'],
                           'weighted_mae_ns':weighted[design]['mae_ns'],
                           'weighted_minus_baseline_ns':weighted[design]['mae_ns']-baseline[design]['mae_ns']})
    pd.DataFrame(per_design).to_csv(directory/'per_design_comparison.csv',index=False)
    plot_comparison(directory,tables,split.capitalize()+' designs: baseline vs weighting only')
    lines = [f'# {split.capitalize()} comparison','',
             '| Model | Macro MAE (ns) | Pooled MAE (ns) | Macro RMSE (ns) |',
             '|---|---:|---:|---:|']
    for name, values in tables.items():
        lines.append(f"| {name} | {values['macro']['mae_ns']:.6f} | {values['pooled']['mae_ns']:.6f} | {values['macro']['rmse_ns']:.6f} |")
    lines += ['',f'Weighted minus baseline macro MAE: {c-b:+.6f} ns. Lower is better.',
              f"Better on this split: {change['better_on_this_split']}.",
              'Both fits use identical estimator parameters and all 500 trees. Only training sample weights differ.',
              'Only four independent designs are present in this split. Register rows are correlated; do not claim statistical significance from their row count.',
              'R² and correlations that are undefined for constant targets/predictions are null, not replaced with 1.', '']
    if split == 'validation':
        lines += ['Test predictions have not been generated by this command.',
                  'Use validation to decide whether to continue development. Run final-test only when ready to finish.', '']
    else:
        lines += [f'The model selected BEFORE opening test labels was: {selected}.',
                  'The test winner is not used to revise that selection. Further changes after seeing these results are exploratory.', '']
    (directory/'REPORT.md').write_text('\n'.join(lines))
    print(f"{split.upper()}: baseline macro MAE={b:.6f} ns; weighted={c:.6f} ns; change={c-b:+.6f} ns",flush=True)
    return change


def compare_validation(ctx):
    tables, model_hashes = {}, {}
    for model in ['baseline','weighted']:
        directory = ctx['out']/'models'/model
        need(verify_complete(ctx,directory), f'Run the {model} stage first.')
        tables[model] = load(directory/'result.json')['metrics']['validation']
        model_hashes[model] = sha(directory/'model.ubj')
    baseline_result = load(ctx['out']/'models/baseline/result.json')
    weighted_result = load(ctx['out']/'models/weighted/result.json')
    need(baseline_result['effective_parameters'] == weighted_result['effective_parameters'],
         'Parameter mismatch: this must remain a weighting-only experiment.')
    decision = {'selected_model':min(['baseline','weighted'],key=lambda m:tables[m]['macro']['mae_ns']),
                'metric':'validation macro MAE (ns)', 'model_sha256':model_hashes,
                'fingerprint':ctx['fingerprint']}
    path = ctx['out']/'selection.json'
    if path.exists():
        need(load(path) == decision, 'Model selection changed within this run.')
    else:
        save(path,decision)
    comparison(ctx,'validation',tables,ctx['out']/'validation',decision['selected_model'])
    print('Selected on validation:',decision['selected_model'])
    return decision


def final_test(ctx):
    import xgboost as xgb
    need((ctx['out']/'selection.json').exists(), 'Run compare before final-test.')
    decision = load(ctx['out']/'selection.json')
    need(decision['fingerprint'] == ctx['fingerprint'], 'Selection fingerprint mismatch.')
    for which in ['baseline','weighted']:
        directory = ctx['out']/'models'/which
        need(verify_complete(ctx,directory), f'Missing completed model: {which}')
        need(sha(directory/'model.ubj') == decision['model_sha256'][which], 'Model changed after selection.')
    # Freeze model choice and both model files BEFORE reading held-out targets.
    lock = ctx['out']/'evaluation_lock.json'
    if lock.exists():
        need(load(lock) == decision, 'Test lock differs from selected models.')
    else:
        save(lock,decision)
    directory = ctx['out']/'test'
    if verify_complete(ctx,directory):
        print('REUSE: verified final test results; no additional fitting or selection.')
        print((directory/'REPORT.md').read_text())
        return
    frame = read_partition(ctx,'test')
    X, _ = xy(ctx,frame)
    tables = {}
    directory.mkdir(parents=True,exist_ok=True)
    for which in ['baseline','weighted']:
        model = xgb.XGBRegressor()
        model.load_model(ctx['out']/'models'/which/'model.ubj')
        model.set_params(nthread=ctx['threads'],device='cpu')
        prediction = model.predict(X)  # Both models use all 500 trees; no early stopping.
        tables[which] = evaluate(frame,prediction)
        write_predictions(directory/(which+'_predictions.csv'),frame,prediction)
    comparison(ctx,'test',tables,directory,decision['selected_model'])
    complete(ctx,directory)
    print('FINAL TEST COMPLETE. Models and validation-based selection are frozen.',flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=['preflight','baseline','weighted','compare','final-test','_fit'])
    parser.add_argument('--input-run',type=Path,default=ROOT.parent/'step5-features/runs/run2')
    parser.add_argument('--out',type=Path,default=ROOT/'runs/weighting_only')
    parser.add_argument('--threads',type=int,default=25,help='25 preserves upstream constructor; optional runtime-only override')
    parser.add_argument('--timeout',type=int,default=1200,help='Wall-clock seconds per fit subprocess')
    parser.add_argument('--model',choices=['baseline','weighted'],help=argparse.SUPPRESS)
    args = parser.parse_args()
    need(args.threads > 0 and args.timeout > 0, 'Threads and timeout must be positive.')
    ctx = preflight(args)
    if args.stage in ('baseline','weighted'):
        run_fit(ctx,args,args.stage)
    elif args.stage == '_fit':
        need(args.model is not None, 'Internal fit requires model name.')
        fit_worker(ctx,args.model)
    elif args.stage == 'compare':
        compare_validation(ctx)
    elif args.stage == 'final-test':
        final_test(ctx)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError,OSError,ValueError,KeyError,importlib.metadata.PackageNotFoundError) as error:
        print('STOP:',error,file=sys.stderr)
        sys.exit(1)
