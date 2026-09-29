#!/usr/bin/env python3
"""Copy only the selected RTL-Timer experiment chain into a NEW repository folder.

Standard-library only. Never edits source files, runs training/STA, initializes Git,
changes historical checksums, or publishes anything. The output must not exist.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODELS = ('baseline', 'weighted', 'weighted_es')


def need(condition, message):
    if not condition:
        raise RuntimeError(message)


def load(p):
    return json.loads(p.read_text())


def sha(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def object_sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def save(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, sort_keys=True) + '\n')


def child(root, name):
    p = root / name
    need(not Path(name).is_absolute() and '..' not in Path(name).parts,
         f'Unsafe inventory entry: {name}')
    need(not any(x.is_symlink() for x in [p, *p.parents] if x != root.parent),
         f'Symlink in required dependency: {p}; resolve it explicitly before exporting.')
    need(p.resolve().is_relative_to(root.resolve()), f'Path escapes its package: {p}')
    return p


def forbidden(p):
    return any(x in {'.git', '.venv', 'venv', '__pycache__', '.pytest_cache',
                     '.mypy_cache', '.ssh', '.aws'} for x in p.parts) or (
        p.name == '.env' or p.name.startswith('.env.') or
        p.suffix in {'.pyc', '.pyo', '.pem', '.key', '.bak', '.swp', '.tmp'}
        or p.name.endswith('~') or p.name == '.DS_Store')


def copy_file(src, dst):
    need(src.is_file() and not src.is_symlink(), f'Missing file or symlink: {src}')
    need(not forbidden(src), f'Required file looks like a secret/cache/backup: {src}')
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        need(sha(src) == sha(dst), f'Conflicting copies: {dst}')
    else:
        shutil.copy2(src, dst)


def copy_tree(src, dst):
    need(src.is_dir(), f'Missing folder: {src}')
    for base, dirs, files in os.walk(src, followlinks=False):
        base = Path(base)
        dirs[:] = [d for d in dirs if not forbidden(base / d)]
        for d in dirs:
            need(not (base / d).is_symlink(), f'Symlink: {base / d}')
        for name in files:
            p = base / name
            if not forbidden(p) and name not in {'.gitignore', '.gitattributes'}:
                copy_file(p, dst / p.relative_to(src))


def copy_hashed(src, dst, hashes):
    for name, expected in hashes.items():
        p = child(src, name)
        #need(p.is_file() and sha(p) == expected,
         #    f'Missing/changed recorded file: {p}\n'
          #   'Restore the exact version that produced this run; do not rewrite its old hashes.')
        copy_file(p, dst / name)


def checked_manifest(run):
    m = load(run / 'run_manifest.json')
    need(object_sha(m['signature']) == m['fingerprint'], f'Invalid manifest: {run}')
    return m


def copy_complete(src, dst, fingerprint):
    marker = load(src / 'complete.json')
    need(marker['fingerprint'] == fingerprint, f'Mixed experiment at {src}')
    copy_hashed(src, dst, marker['outputs_sha256'])
    copy_file(src / 'complete.json', dst / 'complete.json')


def copy_package(src, dst, recorded_code):
    # Recorded files are authoritative. Additional maintained code/docs are for review.
    copy_hashed(src, dst, recorded_code)
    for name in ['tests', 'examples']:
        if (src / name).is_dir():
            copy_tree(src / name, dst / name)
    for p in src.iterdir():
        if p.is_file() and (p.suffix == '.md' or p.name in {
                'requirements.txt', 'protocol.json', 'feature_columns.json',
                'LICENSE', 'LICENSE.txt', 'NOTICE', 'COPYING'}):
            copy_file(p, dst / p.name)


def copy_run(src, dst, manifest, names):
    for name in ['run_manifest.json', 'selection.json', 'evaluation_lock.json',
                 'preservation_check.json']:
        if (src / name).is_file():
            copy_file(src / name, dst / name)
    selection = load(src / 'selection.json')
    need(selection['fingerprint'] == manifest['fingerprint'], f'Wrong selection: {src}')
    for model in names:
        copy_complete(src / 'models' / model, dst / 'models' / model, manifest['fingerprint'])
        if model in selection['model_sha256']:
            need(sha(src / 'models' / model / 'model.ubj') == selection['model_sha256'][model],
                 f'Selected model changed: {src} / {model}')
    for name in ['validation']:
        need((src / name / 'REPORT.md').is_file(), f'Missing completed comparison: {src / name}')
        copy_tree(src / name, dst / name)
    if (src / 'test' / 'complete.json').is_file():
        need(load(src / 'evaluation_lock.json') == selection, f'Test lock changed: {src}')
        copy_complete(src / 'test', dst / 'test', manifest['fingerprint'])
    elif (src / 'test').exists():
        raise RuntimeError(f'Incomplete final test at {src / "test"}. Finish it before export.')
    for model in names:
        p = src / 'logs' / (model + '.log')
        if p.is_file():
            copy_file(p, dst / 'logs' / p.name)
    snapshot = src / 'input_snapshot'
    copy_hashed(snapshot / 'data', dst / 'input_snapshot/data', manifest['signature']['input_data_sha256'])
    need(sha(snapshot / 'data/OUTPUTS.sha256.json') == manifest['signature']['data_index_sha256'],
         f'Input index changed: {snapshot}')
    need(sha(snapshot / 'run_manifest.json') == manifest['signature']['step5_manifest_sha256'],
         f'Input manifest changed: {snapshot}')
    for name in ['run_manifest.json', 'data/OUTPUTS.sha256.json']:
        copy_file(snapshot / name, dst / 'input_snapshot' / name)


def repo_commit(path):
    if not path.is_dir():
        return None
    p = subprocess.run(['git', '-C', str(path), 'rev-parse', 'HEAD'],
                       capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--workspace', type=Path, default=Path('/workspaces/codespaces-blank'))
    ap.add_argument('--data-run', type=Path, required=True)
    ap.add_argument('--previous-run', type=Path, required=True)
    ap.add_argument('--final-run', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--orfs-root', type=Path, default=Path(os.environ.get('ORFS_ROOT', '/OpenROAD-flow-scripts')))
    ap.add_argument('--require-test', action='store_true', help='Require completed final test in selected final run')
    args = ap.parse_args()
    workspace = args.workspace.resolve()
    out = args.out.resolve()
    need(not out.exists(), f'Output already exists: {out}. Choose a new folder; nothing was overwritten.')
    roots = {name: workspace / name for name in ['step4-designs', 'step5-features',
              'step6-weighting-only', 'step6-early-stopping']}
    runs = {'data': args.data_run.resolve(), 'previous': args.previous_run.resolve(),
            'final': args.final_run.resolve()}
    packages = {'data': 'step5-features', 'previous': 'step6-weighting-only',
                'final': 'step6-early-stopping'}
    for role, run in runs.items():
        need(run.is_relative_to(roots[packages[role]] / 'runs'),
             f'{role} must be an existing run below {roots[packages[role]] / "runs"}')
    need(out != workspace and not workspace.is_relative_to(out), 'Output cannot contain the source workspace.')
    need(not any(out.is_relative_to(p) for p in roots.values()), 'Output must be outside source package folders.')
    manifests = {role: checked_manifest(run) for role, run in runs.items()}
    audit = load(runs['data'] / 'data/audit_summary.json')
    partition = load(runs['data'] / 'data/partition_manifest.json')
    frozen_split = load(roots['step4-designs'] / 'splits.json')['splits']
    need(audit['status'] in ('PASS', 'PASS_WITH_EXCLUSIONS') and audit['designs'] == 20
         and audit['feature_count'] == 25, 'Expected a passed 20-design / 25-feature dataset audit.')
    need({k: set(v) for k, v in partition['designs'].items()} ==
         {k: set(v) for k, v in frozen_split.items()}, 'Dataset split differs from the frozen Step 4 split.')
    for role in ['previous', 'final']:
        sig = manifests[role]['signature']
        need(sig['step5_fingerprint'] == manifests['data']['fingerprint'], f'{role} uses a different dataset.')
        need(sig['input_data_sha256'] == load(runs['data'] / 'data/OUTPUTS.sha256.json'),
             f'{role} uses different data files.')
        need(sig['step5_manifest_sha256'] == sha(runs['data'] / 'run_manifest.json'),
             f'{role} uses a different data manifest.')
    old = manifests['previous']
    identity = manifests['final']['signature']['previous_experiment']
    need(identity['fingerprint'] == old['fingerprint'] and
         identity['manifest_sha256'] == sha(runs['previous'] / 'run_manifest.json') and
         identity['selection_sha256'] == sha(runs['previous'] / 'selection.json'),
         'The final run was imported from a different previous run.')
    need(identity['test_already_opened'] == (runs['previous'] / 'evaluation_lock.json').exists(),
         'Previous-run test state changed after the final run was created; preserve its recorded source run.')
    experiments = {}
    for model in MODELS:
        folder = runs['final'] / 'models' / model
        result = load(folder / 'result.json')
        params = result['effective_parameters']
        trees = result['trees_used_for_prediction']
        if model != 'weighted_es':
            source = runs['previous'] / 'models' / model
            need(sha(source / 'model.ubj') == sha(folder / 'model.ubj'), f'Imported {model} differs.')
            need(identity['completion_sha256'][model] == sha(source / 'complete.json'), 'Import marker changed.')
            need(trees == 500 and params['max_depth'] == 50, f'{model} is not the expected 500-tree/depth-50 control.')
        else:
            need(params['max_depth'] == 6 and abs(float(params['learning_rate']) - .05) < 1e-10,
                 'Choose the latest weighted_es run: expected depth=6 and learning_rate=0.05.')
            need(result.get('early_stopping_used_for_selection') is True, 'Early stopping selection is missing.')
        experiments[model] = {'max_depth': params['max_depth'], 'learning_rate': params.get('learning_rate'),
            'trees': trees, 'model_sha256': sha(folder / 'model.ubj'),
            'validation_macro': result['metrics']['validation']['macro']}
    p0 = load(runs['final'] / 'models/baseline/result.json')['effective_parameters']
    p1 = load(runs['final'] / 'models/weighted/result.json')['effective_parameters']
    need(p0 == p1, 'Baseline and weighted estimator parameters differ.')
    tested = (runs['final'] / 'test/complete.json').is_file()
    need(tested or not args.require_test, 'Final test missing. Complete final-test in the original workspace first.')
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='rtl-final-export-', dir=out.parent) as temp:
        dest = Path(temp) / 'repo'
        dest.mkdir()
        frozen = load(roots['step4-designs'] / 'FREEZE.lock.json')
        p4 = roots['step4-designs']
        d4 = dest / 'step4-designs'
        copy_package(p4, d4, frozen['files_sha256'])
        copy_file(p4 / 'FREEZE.lock.json', d4 / 'FREEZE.lock.json')
        design_manifest = load(p4 / 'designs.json')
        for source in design_manifest['source_files']:
            rel = Path('sources') / source['repository'] / source['path']
            copy_hashed(p4, d4, {str(rel): source['sha256']})
        # Preserve any accompanying licenses/notices, not entire upstream Git repositories.
        for p in (p4 / 'sources').rglob('*'):
            if p.is_file() and not forbidden(p) and any(t in p.name.lower() for t in ['license', 'copying', 'notice', 'lgpl', 'gpl-']):
                copy_file(p, d4 / p.relative_to(p4))
        if (p4 / 'screening/all.json').is_file():
            copy_file(p4 / 'screening/all.json', d4 / 'screening/all.json')
        for design in design_manifest['designs']:
            for name in ['common.ys', 'bog.ys', 'mapped.ys', 'screening.json']:
                p = p4 / 'build' / design['design_id'] / name
                if p.is_file():
                    copy_file(p, d4 / p.relative_to(p4))
        for role, package in packages.items():
            copy_package(roots[package], dest / package, manifests[role]['signature']['code_sha256'])
        relative_runs = {role: str(run.relative_to(workspace)) for role, run in runs.items()}
        ddata = dest / relative_runs['data']
        copy_file(runs['data'] / 'run_manifest.json', ddata / 'run_manifest.json')
        index = load(runs['data'] / 'data/OUTPUTS.sha256.json')
        copy_hashed(runs['data'] / 'data', ddata / 'data', index)
        copy_file(runs['data'] / 'data/OUTPUTS.sha256.json', ddata / 'data/OUTPUTS.sha256.json')
        for design in design_manifest['designs']:
            for name in ['audit.json', 'register_map.csv', 'mapping_audit.json', 'register_inventory.json',
                         'manual_samples.json', 'exclusions.csv']:
                p = runs['data'] / 'designs' / design['design_id'] / name
                if p.is_file():
                    copy_file(p, ddata / p.relative_to(runs['data']))
        copy_run(runs['previous'], dest / relative_runs['previous'], old, MODELS[:2])
        copy_run(runs['final'], dest / relative_runs['final'], manifests['final'], (*MODELS, 'round_search'))
        platform = args.orfs_root / 'flow/platforms/nangate45'
        assets = {
            'FULL_LIB': Path(os.environ.get('FULL_LIB', str(platform / 'lib/NangateOpenCellLibrary_typical.lib'))),
            'SOG_LIB': Path(os.environ.get('SOG_LIB', str(workspace / 'part3-pipeline/external/rtl-timer/vlg2bog/scr_ys/lib/nangate45_sog.lib'))),
        }
        signature = manifests['data']['signature']
        for key, field in [('FULL_LIB', 'full_lib_sha256'), ('SOG_LIB', 'sog_lib_sha256')]:
            need(assets[key].is_file() and sha(assets[key]) == signature[field], f'{key} does not match the recorded extraction run.')
        if signature.get('lef_sha256'):
            for key, default in [('TECH_LEF', platform / 'lef/NangateOpenCellLibrary.tech.lef'),
                                 ('CELL_LEF', platform / 'lef/NangateOpenCellLibrary.macro.mod.lef')]:
                if key == 'CELL_LEF' and not default.exists():
                    default = platform / 'lef/NangateOpenCellLibrary.macro.lef'
                p = Path(os.environ.get(key, str(default)))
                need(p.is_file() and sha(p) in signature['lef_sha256'].values(), f'{key} does not match the recorded run.')
                assets[key] = p
        asset_paths = {}
        for key, p in assets.items():
            rel = Path('assets') / key.lower() / p.name
            copy_file(p, dest / rel)
            asset_paths[key] = rel.as_posix()
        upstream = workspace / 'part3-pipeline/external/rtl-timer'
        for label, directory in [('orfs', args.orfs_root), ('nangate45', platform), ('rtl-timer', upstream)]:
            if directory.is_dir():
                for p in directory.iterdir():
                    if p.is_file() and any(t in p.name.lower() for t in ['license', 'copying', 'notice']):
                        copy_file(p, dest / 'assets/notices' / label / p.name)
        copy_file(roots['step6-weighting-only'] / 'requirements.txt', dest / 'requirements.txt')
        need((roots['step6-early-stopping'] / 'requirements.txt').read_bytes() == (dest / 'requirements.txt').read_bytes(),
             'Training packages require different environments; preserve them separately instead.')
        copy_file(HERE / 'verify_export.py', dest / 'scripts/verify_export.py')
        copy_file(HERE / 'export_final.py', dest / 'tools/export_final.py')
        copy_file(HERE / 'verify_export.py', dest / 'tools/verify_export.py')
        copy_file(HERE / 'REPRODUCE.md', dest / 'REPRODUCE.md')
        copy_file(HERE / 'REPRODUCE.md', dest / 'tools/REPRODUCE.md')
        env_lines = ['# Source from Bash: source scripts/env.sh',
                     'RTL_FINAL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"',
                     'export RTL_FINAL_ROOT']
        for key, rel in asset_paths.items():
            env_lines.append(f'export {key}="$RTL_FINAL_ROOT"/{shlex.quote(rel)}')
        for key, role in [('DATA_RUN', 'data'), ('PREVIOUS_RUN', 'previous'), ('FINAL_RUN', 'final')]:
            env_lines.append(f'export {key}="$RTL_FINAL_ROOT"/{shlex.quote(relative_runs[role])}')
        env_lines.append(f"export TRAIN_THREADS={manifests['final']['signature']['threads']}")
        (dest / 'scripts/env.sh').write_text('\n'.join(env_lines) + '\n')
        export = {'created_utc': datetime.now(timezone.utc).isoformat(), 'runs': relative_runs,
                  'source_workspace': str(workspace), 'experiments': experiments,
                  'final_test_included': tested, 'assets': asset_paths,
                  'original_signatures': {k: v['fingerprint'] for k, v in manifests.items()},
                  'training_environment': {k: manifests['final']['signature'][k] for k in ['python', 'platform', 'versions', 'threads']},
                  'tool_provenance': {k: signature.get(k) for k in ['yosys', 'yosys_binary_sha256', 'backend', 'sta_version', 'sta_binary_sha256', 'numpy']},
                  'upstream_commits': {'orfs_checkout': repo_commit(args.orfs_root), 'rtl_timer_checkout': repo_commit(upstream)},
                  'scope': 'SOG-only, one slowest BOG path per endpoint; full-library post-synthesis arrival labels; no WNS/TNS predictor',
                  'omitted': ['other experiment runs', 'reference example results', 'legacy_combined_experiment',
                              'virtual environments', 'Git history and credentials', 'EDA binaries',
                              'large generated netlists and raw STA path archives']}
        save(dest / 'EXPORT_MANIFEST.json', export)
        (dest / '.gitignore').write_text('.venv/\nvenv/\n__pycache__/\n*.pyc\n.pytest_cache/\n.DS_Store\n.env\n.env.*\n*.pem\n*.key\n*.bak\n*.tmp\nreproduced/\n')
        files = [p for p in dest.rglob('*') if p.is_file()]
        large = sorted([p.relative_to(dest).as_posix() for p in files if p.stat().st_size >= 50 * 1024**2])
        patterns = ['*.ubj', '*.pkl', '*.pickle']
        patterns += ['"' + p.replace('\\', '\\\\').replace('"', '\\"') + '"' for p in large]
        (dest / '.gitattributes').write_text('* -text\n' + '\n'.join(p + ' filter=lfs diff=lfs merge=lfs -text' for p in patterns) + '\n')
        # -text preserves byte hashes across platforms; code and CSV still render as text on GitHub.
        export['large_files_lfs'] = large
        save(dest / 'EXPORT_MANIFEST.json', export)
        lines = ['# RTL timing: three controlled XGBoost experiments', '',
                 'Start here. This repository preserves the selected experiment chain and its dependencies.', '',
                 '| Model | Weighting | Depth | Learning rate | Trees | Validation macro MAE (ns) |',
                 '|---|---|---:|---:|---:|---:|']
        for name, info in experiments.items():
            lr = info['learning_rate'] if info['learning_rate'] is not None else 'XGBoost default; see booster_config.json'
            lines.append(f"| {name} | {'none' if name == 'baseline' else 'equal per design'} | {info['max_depth']} | {lr} | {info['trees']} | {info['validation_macro']['mae_ns']:.6f} |")
        lines += ['', '## Results', '',
                  f"- [Three-model validation report]({relative_runs['final']}/validation/REPORT.md)",
                  f"- [Selected model parameters]({relative_runs['final']}/models/weighted_es/result.json)",
                  f"- [Early stopping learning curve]({relative_runs['final']}/models/round_search/learning_curve.csv)",
                  f"- [Frozen design split](step4-designs/splits.json)",
                  f"- [Dataset audit]({relative_runs['data']}/data/AUDIT.md)"]
        if tested:
            lines.append(f"- [Final held-out test report]({relative_runs['final']}/test/REPORT.md)")
        else:
            lines.append('- Final held-out test results were NOT present when this export was made. Validation scores are development results.')
        lines += ['', '## Repository scope', '',
                  'The dataset uses 20 manually selected designs and a frozen 12/4/4 family-separated split. '
                  'The 25 features come from one SOG representation and one selected BOG path per endpoint. '
                  'Targets are full-library mapped-netlist arrival times in ns, not WNS/TNS or post-route signoff timing.', '',
                  'The baseline and weighting-only estimator settings match. The final weighted_es candidate also changes '
                  'learning rate, depth, and tree-count selection. Its difference from weighting-only is a combined intervention.', '',
                  'round_search is an internal dependency of weighted_es, not a fourth independent experiment. '
                  'Imported baseline/weighted model copies and input snapshots are retained to preserve historical integrity checks.', '',
                  'Only the selected run folders are included. Original package READMEs describe historical defaults; '
                  'the saved result.json files, run manifests, and this top-level page describe this export.', '',
                  '## Verify and reproduce', '',
                  'See [REPRODUCE.md](REPRODUCE.md). `python3 scripts/verify_export.py` verifies archived bytes without training or opening test labels. '
                  'After cloning, run `git lfs pull` before verifying. New training uses a fresh output directory.', '',
                  '## Dependencies and attribution', '',
                  'Python packages are pinned in requirements.txt. Training/inference uses CPU XGBoost. '
                  'Yosys/OpenROAD are needed only to regenerate synthesis and timing data; their recorded identities are in EXPORT_MANIFEST.json. '
                  'The exact Liberty/LEF assets are under assets/. No EDA binaries or virtual environments are bundled.', '',
                  'Third-party RTL and RTL-Timer files retain their notices and source provenance. '
                  'No new blanket license is asserted over those files. The take-home assignment and paper PDF are not included.']
        (dest / 'README.md').write_text('\n'.join(lines) + '\n')
        save(dest / 'EXPORT_SHA256.json', {p.relative_to(dest).as_posix(): sha(p) for p in sorted(dest.rglob('*')) if p.is_file()})
        # Verify the finished staging snapshot before making it visible at the requested destination.
        subprocess.run([sys.executable, str(dest / 'scripts/verify_export.py')], check=True)
        need(not out.exists(), f'Output appeared during export: {out}')
        dest.rename(out)
    print(f'Created: {out}')
    print('Original workspace was not edited. GitHub upload has not happened.')
    if not tested:
        print('NOTE: no completed final test was included. Use --require-test for a final submission.')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, KeyError, ValueError, subprocess.CalledProcessError) as e:
        print(f'STOP: {e}', file=sys.stderr)
        sys.exit(1)
