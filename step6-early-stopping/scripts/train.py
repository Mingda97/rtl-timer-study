#!/usr/bin/env python3
"""Add weighted MAE early stopping and a fresh fixed-round refit; preserve prior fits."""
import argparse
import math
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

import common as c

ROOT = Path(__file__).resolve().parents[1]
MODELS = ['baseline', 'weighted', 'weighted_es']


def separate_paths(previous, source, output):
    for protected in (previous.resolve(), source.resolve()):
        dest = output.resolve()
        c.need(not (dest == protected or dest.is_relative_to(protected)
                    or protected.is_relative_to(dest)),
               f'Output must be separate from protected input: {protected}')


def preflight(args):
    previous = args.previous_run.resolve()
    separate_paths(previous, args.input_run, args.out)
    c.need((previous/'run_manifest.json').is_file(),
           f'Missing previous weighting-only run: {previous}. Run its baseline, weighted and compare stages first.')
    old = c.load(previous/'run_manifest.json')
    c.need(c.object_sha(old['signature']) == old['fingerprint'], 'Previous run fingerprint is invalid.')
    sig = old['signature']
    c.need(sig['protocol']['revision'] == 'step6-weighting-only-v2', 'Expected the corrected weighting-only experiment.')
    c.need(sig['threads'] == args.threads, 'Use the same --threads setting as the previous run.')
    c.need(sig['protocol']['baseline'] == c.load(ROOT/'protocol.json')['baseline'],
           'Base estimator settings changed; this experiment changes tree-count selection only.')
    old_ctx = {'fingerprint':old['fingerprint']}
    markers = {}
    for model in MODELS[:2]:
        folder = previous/'models'/model
        c.need(c.verify_complete(old_ctx, folder), f'Missing completed previous model: {model}')
        markers[model] = c.sha(folder/'complete.json')
    c.need((previous/'selection.json').is_file(), 'Run compare in the previous experiment first.')
    decision = c.load(previous/'selection.json')
    c.need(decision['fingerprint'] == old['fingerprint'], 'Previous selection does not match its run.')
    for model in MODELS[:2]:
        c.need(decision['model_sha256'][model] == c.sha(previous/'models'/model/'model.ubj'),
               'Previous model differs from its selection record.')
    args.previous_identity = {
        'fingerprint':old['fingerprint'], 'manifest_sha256':c.sha(previous/'run_manifest.json'),
        'completion_sha256':markers, 'selection_sha256':c.sha(previous/'selection.json'),
        'test_already_opened':(previous/'evaluation_lock.json').exists(),
    }
    # Check compatibility before creating any new output.
    input_manifest = c.load(args.input_run/'run_manifest.json')
    c.need(input_manifest['fingerprint'] == sig['step5_fingerprint'], 'Different Step 5 run from the previous experiment.')
    c.need(c.load(args.input_run/'data/OUTPUTS.sha256.json') == sig['input_data_sha256'],
           'Data differ from the previous experiment.')
    c.need(c.versions() == sig['versions'], 'Package versions differ from the previous experiment.')
    out = args.out.resolve()
    c.need(not out.exists() or (out/'run_manifest.json').exists() or not any(out.iterdir()),
           'Output has unrelated files; choose a new empty --out directory.')
    ctx = c.preflight(args)
    ctx['previous'] = previous
    ctx['previous_identity'] = args.previous_identity
    es = ctx['protocol']['early_stopping']
    c.need(isinstance(es['max_estimators'], int) and es['max_estimators'] > 0
           and isinstance(es['patience'], int) and es['patience'] > 0
           and es['eval_metric'] == 'mae', 'Invalid early-stopping protocol.')
    if args.previous_identity['test_already_opened']:
        print('NOTE: Previous test evaluation has already begun; this follow-up is exploratory.', flush=True)
    return ctx


def assert_training_open(ctx):
    c.need(not (ctx['out']/'evaluation_lock.json').exists(),
           'Final test has begun; training and round selection are closed for this run.')


def import_previous(ctx):
    assert_training_open(ctx)
    for model in MODELS[:2]:
        dest = ctx['out']/'models'/model
        if c.verify_complete(ctx, dest):
            print('REUSE imported model:', model)
            continue
        c.need(not dest.exists(), f'Incomplete import at {dest}; use a fresh --out rather than overwriting it.')
        source = ctx['previous']/'models'/model
        shutil.copytree(source, dest)
        (dest/'complete.json').rename(dest/'source_complete.json')
        c.save(dest/'import_record.json', {
            'source_directory':str(source), 'source_identity':ctx['previous_identity'],
            'model_retrained':False,
        })
        c.complete(ctx, dest)
        print('IMPORTED without retraining:', model)


def require_imports(ctx):
    for name in MODELS[:2]:
        c.need(c.verify_complete(ctx, ctx['out']/'models'/name), 'Run import-previous first.')


def parameters(model):
    return {k:('NaN (missing-value sentinel)' if isinstance(v,float) and math.isnan(v) else v)
            for k,v in model.get_params().items()}


def check_parameter_changes(reference, current, allowed):
    changed = {k for k in reference.keys() | current.keys() if reference.get(k) != current.get(k)}
    c.need(changed.issubset(allowed), f'Unexpected estimator changes: {sorted(changed - allowed)}')


def base_parameters(ctx):
    return dict(
        ctx['protocol']['baseline'],
        nthread=ctx['threads'],
        learning_rate=0.05,
        max_depth=6,
    )


def choose_rounds(ctx, train, validation):
    """Fit on train; select the best visited round using equal-design validation MAE."""
    import numpy as np
    import xgboost as xgb
    X_train, y_train = c.xy(ctx, train)
    X_val, y_val = c.xy(ctx, validation)
    train_weights = c.design_weights(train)
    validation_weights = c.design_weights(validation)
    settings = ctx['protocol']['early_stopping']
    params = base_parameters(ctx)
    params.update(n_estimators=settings['max_estimators'], eval_metric='mae',
                  early_stopping_rounds=settings['patience'])
    search_model = xgb.XGBRegressor(**params)
    search_model.fit(
        X_train, y_train, sample_weight=train_weights,
        eval_set=[(X_val, y_val)],
        sample_weight_eval_set=[validation_weights], verbose=50,
    )
    best_iteration = int(search_model.best_iteration)
    best_n_estimators = best_iteration + 1  # XGBoost's index starts at zero.
    history = search_model.evals_result()['validation_0']['mae']
    rounds = search_model.get_booster().num_boosted_rounds()
    c.need(rounds == len(history) and 1 <= best_n_estimators <= rounds <= settings['max_estimators'],
           'Inconsistent best iteration or history length.')
    c.need(best_iteration == int(np.argmin(history)), 'Best iteration differs from the first minimum in history.')
    prediction = search_model.predict(X_val, iteration_range=(0, best_n_estimators))
    macro_mae = c.evaluate(validation, prediction)['macro']['mae_ns']
    c.need(np.isclose(macro_mae, float(search_model.best_score), rtol=1e-6, atol=2e-6),
           'Monitored MAE does not match equal-design validation MAE.')
    record = {
        'best_iteration_zero_based':best_iteration, 'best_n_estimators':best_n_estimators,
        'best_validation_macro_mae_ns':macro_mae,
        'xgboost_best_score':float(search_model.best_score),
        'rounds_actually_trained':rounds, 'max_estimators':settings['max_estimators'],
        'patience':settings['patience'], 'reached_round_cap':rounds == settings['max_estimators'],
        'stopped_before_cap':rounds < settings['max_estimators'],
        'training_designs':ctx['partition']['designs']['train'],
        'selection_designs':ctx['partition']['designs']['validation'],
        'effective_parameters':parameters(search_model), 'test_evaluated':False,
    }
    return search_model, record, history


def fresh_refit(ctx, train, best_n_estimators):
    """New estimator, same 12 training designs/weights, exactly the selected tree count."""
    import xgboost as xgb
    X_train, y_train = c.xy(ctx, train)
    params = base_parameters(ctx)
    params['n_estimators'] = best_n_estimators
    model = xgb.XGBRegressor(**params)
    # No eval_set, early_stopping_rounds, xgb_model, or train+validation concatenation.
    model.fit(X_train, y_train, sample_weight=c.design_weights(train))
    c.need(model.get_booster().num_boosted_rounds() == best_n_estimators,
           'Fresh refit did not use exactly the selected number of trees.')
    return model


def save_weight_table(directory, train, validation):
    import pandas as pd
    records = []
    for split, frame in [('train',train), ('validation',validation)]:
        weights = c.design_weights(frame)
        for design, count in frame['design'].value_counts().items():
            mask = frame['design'].to_numpy() == design
            records.append({'split':split, 'design':design, 'rows':int(count),
                            'row_weight':float(weights[mask][0]),
                            'total_weight':float(weights[mask].sum()),
                            'fraction':float(weights[mask].sum()/weights.sum()),
                            'role':'tree_fitting' if split == 'train' else 'round_selection_metric'})
    pd.DataFrame(records).to_csv(directory/'design_weights.csv', index=False)


def fit_worker(ctx, which):
    import numpy as np
    import pandas as pd
    import xgboost as xgb
    assert_training_open(ctx)
    require_imports(ctx)
    directory = ctx['out']/'models'/which
    if c.verify_complete(ctx, directory):
        print('REUSE verified stage:', which)
        return
    directory.mkdir(parents=True, exist_ok=True)
    train, validation = c.read_partition(ctx,'train'), c.read_partition(ctx,'validation')
    reference = c.load(ctx['out']/'models/weighted/result.json')['effective_parameters']
    started = time.perf_counter()
    if which == 'round_search':
        model, record, history = choose_rounds(ctx, train, validation)
        check_parameter_changes(reference, parameters(model),
                                {'n_estimators','eval_metric','early_stopping_rounds','learning_rate','max_depth'})
        record['fit_seconds'] = time.perf_counter() - started
        model.save_model(directory/'model.ubj')
        (directory/'booster_config.json').write_text(model.get_booster().save_config()+'\n')
        c.save(directory/'round_selection.json', record)
        c.save(directory/'learning_curve.json', model.evals_result())
        pd.DataFrame({'iteration_zero_based':range(len(history)),
                      'trees':range(1,len(history)+1),
                      'validation_macro_mae_ns':history}).to_csv(directory/'learning_curve.csv',index=False)
        save_weight_table(directory, train, validation)
        print(f"ROUND SELECTION: best={record['best_n_estimators']} trees; "
              f"trained={record['rounds_actually_trained']}; cap={record['max_estimators']}; "
              f"validation macro MAE={record['best_validation_macro_mae_ns']:.6f} ns",flush=True)
        if record['reached_round_cap']:
            print('NOTE: Reached the round cap. Best means best observed; stopping convergence is not established.')
    else:
        c.need(which == 'weighted_es', 'Unknown fit stage.')
        search_dir = ctx['out']/'models/round_search'
        c.need(c.verify_complete(ctx, search_dir), 'Run select-rounds before refit.')
        selected = c.load(search_dir/'round_selection.json')
        best_count = selected['best_n_estimators']
        model = fresh_refit(ctx, train, best_count)
        seconds = time.perf_counter() - started
        check_parameter_changes(reference, parameters(model), {'n_estimators', 'learning_rate','max_depth'})
        search = xgb.XGBRegressor(); search.load_model(search_dir/'model.ubj')
        search.set_params(nthread=ctx['threads'], device='cpu')
        metrics, max_difference = {}, 0.0
        for split, frame in [('train',train), ('validation',validation)]:
            X, _ = c.xy(ctx, frame)
            prediction = model.predict(X)
            expected = search.predict(X, iteration_range=(0,best_count))
            max_difference = max(max_difference, float(np.max(np.abs(prediction-expected))))
            c.need(np.allclose(prediction, expected, rtol=1e-6, atol=1e-6),
                   'Fresh refit differs from selected search prefix; inspect settings/data/order.')
            metrics[split] = c.evaluate(frame, prediction)
            c.write_predictions(directory/(split+'_predictions.csv'), frame, prediction)
        model.save_model(directory/'model.ubj')
        (directory/'booster_config.json').write_text(model.get_booster().save_config()+'\n')
        c.save(directory/'result.json', {
            'model':'weighted_es', 'fit_seconds':seconds, 'metrics':metrics,
            'best_iteration_zero_based':selected['best_iteration_zero_based'],
            'trees_used_for_prediction':best_count, 'trees_in_saved_booster':best_count,
            'early_stopping_used_for_selection':True, 'early_stopping_used_during_refit':False,
            'refit_max_prediction_difference_ns':max_difference,
            'round_selection_sha256':c.sha(search_dir/'round_selection.json'),
            'training_designs':ctx['partition']['designs']['train'],
            'effective_parameters':parameters(model), 'test_evaluated':False,
        })
        print(f"REFIT PASS: {best_count} trees; macro MAE={metrics['validation']['macro']['mae_ns']:.6f} ns; "
              f"maximum prefix difference={max_difference:.3g} ns",flush=True)
    c.complete(ctx, directory)


def run_fit(ctx, args, which):
    assert_training_open(ctx)
    directory = ctx['out']/'models'/which
    if c.verify_complete(ctx,directory):
        print('REUSE verified stage:',which)
        return
    logs = ctx['out']/'logs'; logs.mkdir(exist_ok=True)
    path = logs/(which+'.log')
    command = [sys.executable,str(Path(__file__).resolve()),'_fit','--model',which,
               '--previous-run',str(args.previous_run.resolve()),
               '--input-run',str(args.input_run.resolve()),'--out',str(ctx['out']),
               '--threads',str(args.threads)]
    print(f'START {which}: log {path}',flush=True)
    start = time.monotonic()
    with path.open('w') as stream:
        process = subprocess.Popen(command,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            while True:
                remaining = args.timeout-(time.monotonic()-start)
                c.need(remaining > 0, f'{which} exceeded --timeout; inspect {path}')
                try:
                    code = process.wait(timeout=min(30,remaining))
                    break
                except subprocess.TimeoutExpired:
                    print(f'RUNNING {which}: {time.monotonic()-start:.0f}s; {path}',flush=True)
        except BaseException:
            if process.poll() is None:
                os_kill_group(process, signal.SIGTERM)
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os_kill_group(process, signal.SIGKILL); process.wait()
            raise
    tail = '\n'.join(path.read_text(errors='replace').splitlines()[-10:])
    c.need(code == 0, f'Fit failed; inspect {path}\n{tail}')
    c.need(c.verify_complete(ctx,directory), 'No verified stage completion.')
    print(tail,flush=True)


def os_kill_group(process, sig):
    import os
    try: os.killpg(process.pid,sig)
    except ProcessLookupError: pass


def report(ctx, split, tables, directory, selected):
    import pandas as pd
    directory.mkdir(parents=True,exist_ok=True)
    records = [{'model':name, **{'macro_'+k:v for k,v in tables[name]['macro'].items()},
                **{'pooled_'+k:v for k,v in tables[name]['pooled'].items()}} for name in MODELS]
    pd.DataFrame(records).to_csv(directory/'summary.csv',index=False)
    per_design = []
    for name in MODELS:
        per_design += [{'model':name, **row} for row in tables[name]['per_design']]
    pd.DataFrame(per_design).to_csv(directory/'per_design.csv',index=False)
    c.save(directory/'comparison.json', {'split':split,'models':tables,'selected_on_validation':selected})
    lines = [f'# {split.capitalize()} comparison', '',
             '| Model | Trees used | Macro MAE (ns) | Macro RMSE (ns) | Macro R² |',
             '|---|---:|---:|---:|---:|']
    for name in MODELS:
        n = c.load(ctx['out']/'models'/name/'result.json')['trees_used_for_prediction']
        m = tables[name]['macro']; r2 = f"{m['r2']:.6f}" if m['r2'] is not None else 'undefined'
        lines.append(f"| {name} | {n} | {m['mae_ns']:.6f} | {m['rmse_ns']:.6f} | {r2} |")
    lines += ['',f'Validation-selected model: {selected}. Ties favor baseline, then weighted, then weighted_es.',
              'Baseline and weighted-500 results/models were imported without retraining; source files were not modified.',
              'The new candidate uses validation twice: to select tree count, then to compare candidates. Its validation score is a development score, not an independent final estimate.',
              'Both round search and fresh refit use only the original 12 training designs.',
              'Only four designs are present in this split. Inspect per-design MAE and R²; register rows are correlated.',
              'If test labels were viewed in an earlier experiment, subsequent development is exploratory.', '']
    if split == 'validation': lines += ['No test predictions are generated by compare.','']
    else: lines += ['Test results do not change the validation-selected model.','']
    (directory/'REPORT.md').write_text('\n'.join(lines))
    print('\n'.join(lines[:8]),flush=True)


def compare(ctx):
    tables, hashes = {}, {}
    for name in MODELS:
        folder = ctx['out']/'models'/name
        c.need(c.verify_complete(ctx,folder), f'Missing model {name}; import previous fits and complete select-rounds/refit.')
        tables[name] = c.load(folder/'result.json')['metrics']['validation']
        hashes[name] = c.sha(folder/'model.ubj')
    choice = min(MODELS, key=lambda name:tables[name]['macro']['mae_ns'])
    decision = {'selected_model':choice, 'metric':'validation macro MAE (ns)',
                'model_sha256':hashes,'fingerprint':ctx['fingerprint']}
    path = ctx['out']/'selection.json'
    if path.exists(): c.need(c.load(path) == decision, 'Selection changed within this experiment.')
    else: c.save(path,decision)
    report(ctx,'validation',tables,ctx['out']/'validation',choice)
    return decision


def final_test(ctx):
    import xgboost as xgb
    c.need((ctx['out']/'selection.json').exists(), 'Run compare before final-test.')
    decision = c.load(ctx['out']/'selection.json')
    c.need(decision['fingerprint'] == ctx['fingerprint'], 'Selection belongs to another run.')
    for name in MODELS:
        folder = ctx['out']/'models'/name
        c.need(c.verify_complete(ctx,folder), f'Missing model: {name}')
        c.need(c.sha(folder/'model.ubj') == decision['model_sha256'][name], 'Model changed after selection.')
    lock = ctx['out']/'evaluation_lock.json'
    if lock.exists(): c.need(c.load(lock) == decision, 'Evaluation lock mismatch.')
    else: c.save(lock,decision)  # Before reading test rows.
    directory = ctx['out']/'test'
    if c.verify_complete(ctx,directory):
        print('REUSE: verified final-test results.'); return
    frame = c.read_partition(ctx,'test'); X, _ = c.xy(ctx,frame)
    tables = {}; directory.mkdir(exist_ok=True)
    for name in MODELS:
        model = xgb.XGBRegressor(); model.load_model(ctx['out']/'models'/name/'model.ubj')
        model.set_params(nthread=ctx['threads'],device='cpu')
        prediction = model.predict(X)
        tables[name] = c.evaluate(frame,prediction)
        c.write_predictions(directory/(name+'_predictions.csv'),frame,prediction)
    report(ctx,'test',tables,directory,decision['selected_model'])
    c.complete(ctx,directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=['preflight','import-previous','select-rounds','refit','compare','final-test','_fit'])
    parser.add_argument('--previous-run',type=Path,default=ROOT.parent/'step6-weighting-only/runs/weighting_only')
    parser.add_argument('--input-run',type=Path,default=ROOT.parent/'step5-features/runs/run2')
    parser.add_argument('--out',type=Path,default=ROOT/'runs/es_2000_patience50')
    parser.add_argument('--threads',type=int,default=25)
    parser.add_argument('--timeout',type=int,default=1200)
    parser.add_argument('--model',choices=['round_search','weighted_es'],help=argparse.SUPPRESS)
    args = parser.parse_args()
    c.need(args.threads > 0 and args.timeout > 0, 'Threads and timeout must be positive.')
    ctx = preflight(args)
    if args.stage == 'import-previous': import_previous(ctx)
    elif args.stage in ('select-rounds','refit'):
        run_fit(ctx,args,'round_search' if args.stage == 'select-rounds' else 'weighted_es')
    elif args.stage == '_fit':
        c.need(args.model is not None, 'Internal fit requires --model.'); fit_worker(ctx,args.model)
    elif args.stage == 'compare': compare(ctx)
    elif args.stage == 'final-test': final_test(ctx)


if __name__ == '__main__':
    try: main()
    except (RuntimeError,OSError,ValueError,KeyError) as error:
        print('STOP:',error,file=sys.stderr); sys.exit(1)
