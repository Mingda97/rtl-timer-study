#!/usr/bin/env python3
"""Extract and audit the frozen 20-design, SOG-only register timing dataset.

Run `python3 scripts/extract.py preflight`, then `run`, then `audit`.
This script reads Step 4 outputs; it never changes RTL, constraints, or splits.
"""
import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
from sta_netlist import prepare as prepare_sta_netlist

ROOT = Path(__file__).resolve().parents[1]
REVISION = 'step5-sog-v1-signed-fix1'
PATH_COLUMNS = [
    'bog_arrival_ns', 'logic_levels', 'and2_count', 'or2_count', 'inv_count',
    'xor2_count', 'mux2_count', 'fanout_sum', 'fanout_mean', 'fanout_variance',
    'cap_sum_ff', 'cap_mean_ff', 'cap_variance_ff2',
    'slew_sum_ns', 'slew_mean_ns', 'slew_variance_ns2',
]
FEATURE_COLUMNS = [
    'bog_rank_level', 'bog_fraction_strictly_slower', 'bog_sequential_cells',
    'bog_combinational_cells', 'bog_total_cells', 'driving_register_count',
    *PATH_COLUMNS, 'fanout_std', 'cap_std_ff', 'slew_std_ns',
]
META_COLUMNS = ['design', 'rtl_bit', 'split', 'family', 'category', 'bog_path_kind']
FEATURE_FIELDS = META_COLUMNS + FEATURE_COLUMNS
LABEL_FIELDS = ['design', 'rtl_bit', 'mapped_d_pin', 'arrival_ns', 'arrival_edge',
                'required_ns', 'slack_ns', 'min_setup_slack_ns', 'mapped_path_kind',
                'mapped_polarities', 'scenario']
EXCLUSION_FIELDS = ['design', 'common_ff', 'rtl_bit', 'reason', 'details']
MIN_COVERAGE = 0.95  # fixed before label/prediction inspection, per design


def need(ok, message):
    if not ok:
        raise RuntimeError(message)


def load(path):
    return json.loads(Path(path).read_text())


def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + '\n')
    temp.replace(path)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def object_sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def write_csv(path, rows, fields):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='') as stream:
        w = csv.DictWriter(stream, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def read_csv(path, delimiter=','):
    with Path(path).open(newline='') as stream:
        return list(csv.DictReader(stream, delimiter=delimiter))


def run_command(command, logfile, cwd, timeout, env=None):
    logfile.parent.mkdir(parents=True, exist_ok=True)
    with logfile.open('w') as stream:
        p = subprocess.Popen(list(map(str, command)), cwd=cwd, env=env,
                             stdout=stream, stderr=subprocess.STDOUT,
                             start_new_session=True)
        try:
            result = p.wait(timeout=timeout)
        except BaseException:
            # Kill only this subprocess group, including children, on timeout.
            try:
                os.killpg(p.pid, signal.SIGTERM)
                p.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                p.wait()
            raise
    if result:
        tail = '\n'.join(logfile.read_text(errors='replace').splitlines()[-15:])
        raise RuntimeError(f'Command exit {result}; see {logfile}\n{tail}')


def tool_version(binary, option):
    return subprocess.check_output([str(binary), option], text=True,
                                   stderr=subprocess.STDOUT, timeout=30).strip()


def yosys_identity(version):
    # Frontend wrappers/builds may add compiler or packaging text only to -V.
    release = re.search(r'\bYosys\s+(\S+)', version)
    commit = re.search(r'git sha1\s+([^\s,)]+)', version)
    need(release is not None, f'Unrecognized Yosys version: {version}')
    return release.group(1), commit.group(1) if commit else None


def preflight(args):
    import numpy
    dataset = args.dataset_root.resolve()
    need((dataset / 'scripts/dataset.py').is_file(),
         f'Missing Step 4 bundle: {dataset}. Set --dataset-root.')
    # Use the existing frozen verifier, including the recorded scopeinfo fix.
    subprocess.run([sys.executable, str(dataset / 'scripts/dataset.py'), 'check'], check=True)
    manifest = load(dataset / 'designs.json')
    screening = load(dataset / 'screening/all.json')
    expected = {d['design_id'] for d in manifest['designs']}
    screened = {r['design_id']: r for r in screening['designs']}
    need(set(screened) == expected and all(r['status'] == 'PASS' for r in screened.values()),
         'Complete Step 4 screening first: python3 scripts/dataset.py screen --jobs 1 --timeout 1200')
    yosys = shutil.which('yosys')
    sta_binary = shutil.which(args.sta_bin or args.backend)
    need(yosys and sta_binary, 'Yosys or the timing executable is missing from PATH.')
    yosys_version = tool_version(yosys, '-V')
    need(yosys_version == screening['yosys'],
         'Yosys differs from the full Step 4 screening run. Rerun Step 4 with the chosen tool version.')

    platform = Path(os.environ.get('ORFS_ROOT', '/OpenROAD-flow-scripts')) / 'flow/platforms/nangate45'
    full = Path(os.environ.get('FULL_LIB', str(platform / 'lib/NangateOpenCellLibrary_typical.lib'))).resolve()
    sog = Path(os.environ.get('SOG_LIB', str(ROOT.parent / 'part3-pipeline/external/rtl-timer/vlg2bog/scr_ys/lib/nangate45_sog.lib'))).resolve()
    for path, key in [(full, 'full_lib_sha256'), (sog, 'sog_lib_sha256')]:
        need(path.is_file(), f'Missing {path}; set FULL_LIB/SOG_LIB to your Step 3 Liberty files.')
        need(sha(path) == manifest['protocol'][key], f'Liberty changed: {path}')
        text = path.read_text()
        need(re.search(r'time_unit\s*:\s*"1ns"', text), 'Expected ns time unit')
        need(re.search(r'capacitive_load_unit\s*\(\s*1\s*,\s*ff\s*\)', text, re.I), 'Expected fF capacitance unit')
    tech = Path(os.environ.get('TECH_LEF', str(platform / 'lef/NangateOpenCellLibrary.tech.lef'))).resolve()
    cell = Path(os.environ.get('CELL_LEF', str(platform / 'lef/NangateOpenCellLibrary.macro.mod.lef'))).resolve()
    if not cell.exists() and 'CELL_LEF' not in os.environ:
        cell = platform / 'lef/NangateOpenCellLibrary.macro.lef'
    if args.backend == 'openroad':
        need(tech.is_file() and cell.is_file(), 'Missing technology/cell LEF; set ORFS_ROOT or TECH_LEF/CELL_LEF.')

    vendor = ROOT / 'third_party/rtl_timer'
    provenance = load(vendor / 'PROVENANCE.json')
    for name, digest in provenance['sha256'].items():
        need(sha(vendor / name) == digest, f'Upstream file changed: {name}')
    code = {str(p.relative_to(ROOT)): sha(p) for base in ['scripts', 'third_party/rtl_timer']
            for p in sorted((ROOT / base).glob('*')) if p.is_file()}
    inputs = {}
    for d in manifest['designs']:
        name = d['design_id']
        files = [dataset / 'build' / name / f for f in
                 ['common.json', 'common.il', 'bog.json', 'bog.v', 'mapped.json', 'mapped.v', 'screening.json']]
        files += [dataset / 'constraints' / (name + '.sdc')]
        for p in files:
            need(p.is_file(), f'Missing screened output {p}; rerun Step 4.')
        inputs[name] = {str(p.relative_to(dataset)): sha(p) for p in files}
    signature = {
        'revision': REVISION, 'code_sha256': code, 'dataset_version': manifest['dataset_version'],
        'freeze_lock_sha256': sha(dataset / 'FREEZE.lock.json'),
        'designs_sha256': sha(dataset / 'designs.json'), 'splits_sha256': sha(dataset / 'splits.json'),
        'inputs_sha256': inputs, 'yosys': yosys_version, 'yosys_binary_sha256': sha(yosys),
        'backend': args.backend, 'sta_version': tool_version(sta_binary, '-version'),
        'sta_binary_sha256': sha(sta_binary), 'python': sys.version, 'numpy': numpy.__version__,
        'full_lib_sha256': sha(full), 'sog_lib_sha256': sha(sog),
        'lef_sha256': {str(p): sha(p) for p in [tech, cell]} if args.backend == 'openroad' else {},
        'upstream_commit': provenance['commit'], 'minimum_per_design_coverage': MIN_COVERAGE,
        'feature_columns': FEATURE_COLUMNS,
    }
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    fingerprint = object_sha(signature)
    meta_path = out / 'run_manifest.json'
    if meta_path.exists():
        need(load(meta_path)['fingerprint'] == fingerprint,
             'Inputs, code, or tools changed. Use a NEW --out directory; do not mix old and new rows.')
    else:
        save(meta_path, {'fingerprint': fingerprint, 'signature': signature,
                        'created_utc': datetime.now(timezone.utc).isoformat(),
                        'dataset_root': str(dataset), 'sta_binary': str(sta_binary),
                        'protocol': manifest['protocol'],
                        'scope': 'SOG-only; one maximum-arrival BOG path per endpoint; post-synthesis labels; no placement/wire RC'})
    ctx = dict(dataset=dataset, out=out, manifest=manifest, fingerprint=fingerprint,
               full=full, sog=sog, tech=tech, cell=cell, sta_binary=sta_binary,
               backend=args.backend, timeout=args.timeout, yosys_version=yosys_version)
    print(f'PREFLIGHT PASS: {len(expected)} screened designs; {len(FEATURE_COLUMNS)} features; {out}', flush=True)
    return ctx


def aliases(module, public_only=False):
    result = defaultdict(set)
    for name, net in module['netnames'].items():
        if public_only and net.get('hide_name', 0):
            continue
        for i, bit in enumerate(net['bits']):
            if not isinstance(bit, int):
                continue
            index = int(net.get('offset', 0)) + (len(net['bits']) - 1 - i if net.get('upto', 0) else i)
            # Keep (wire name, index) separate; names themselves can contain brackets.
            result[bit].add((name, index))
    return result


def alias_text(alias, module):
    name, index = alias
    net = module['netnames'][name]
    return f'{name}[{index}]' if len(net['bits']) > 1 or net.get('offset', 0) else name


def registers(module, generic=False):
    result = {}
    for name, cell in module['cells'].items():
        t = cell['type']
        is_ff = t.startswith('$_DFF') if generic else t.startswith('DFF')
        if is_ff:
            c = cell['connections']
            need(len(c['D']) == len(c['Q']) == 1, f'Non-bitwise register {name}')
            result[name] = {'cell': name, 'type': t, 'D': c['D'][0], 'Q': c['Q'][0], 'pin': name + '/D'}
    return result


def map_registers(design, modules, base):
    common = modules['common']
    regs = {b: registers(m, b == 'common') for b, m in modules.items()}
    pub = aliases(common, True)
    inverses = {}
    for b in ['bog', 'mapped']:
        inverse = defaultdict(set)
        names = aliases(modules[b])
        for name, reg in regs[b].items():
            for alias in names[reg['Q']]:
                inverse[alias].add(name)
        inverses[b] = inverse
    mappings, exclusions, inventory = [], [], []
    used = {'bog': set(), 'mapped': set()}
    identities = set()
    for name, reg in sorted(regs['common'].items()):
        q_aliases = pub[reg['Q']]
        if not q_aliases:
            exclusions.append(dict(design=design['design_id'], common_ff=name, rtl_bit='',
                                   reason='no_public_rtl_q_alias', details='Synthesis-generated register; do not invent an RTL name'))
            inventory.append({'common_ff': name, 'status': 'no_public_rtl_q_alias', 'aliases': []})
            continue
        # All aliases denote this same electrical Q bit. Pick one stable public name.
        canonical = min(q_aliases, key=lambda a: (a[0].count('.'), len(a[0]), a))
        identity = alias_text(canonical, common)
        need(identity not in identities, f'Duplicate RTL bit identity {identity}')
        identities.add(identity)
        item = {'design': design['design_id'], 'rtl_bit': identity, 'common_ff': name}
        for b in ['bog', 'mapped']:
            matches = set().union(*(inverses[b].get(a, set()) for a in q_aliases))
            need(len(matches) == 1, f'{design["design_id"]} {identity}: {b} alias match count {len(matches)}; no guessing allowed')
            match = next(iter(matches))
            need(match not in used[b], f'{identity}: many-to-one register match in {b}')
            used[b].add(match)
            item[b + '_ff'] = match
            item[b + '_d_pin'] = regs[b][match]['pin']
        mappings.append(item)
        inventory.append({**item, 'status': 'unique_public_q_alias',
                          'aliases': [alias_text(a, common) for a in sorted(q_aliases)]})
    need(len(regs['common']) == len(regs['bog']) == len(regs['mapped']), 'Branch register counts differ')
    need(bool(mappings), 'No public RTL register bits matched')
    save(base / 'register_inventory.json', inventory)
    write_csv(base / 'register_map.csv', mappings,
              ['design', 'rtl_bit', 'common_ff', 'bog_ff', 'bog_d_pin', 'mapped_ff', 'mapped_d_pin'])
    save(base / 'mapping_audit.json', {
        'common_registers': len(regs['common']), 'matched_public_rtl_bits': len(mappings),
        'no_public_rtl_q_alias': len(exclusions),
        'unclaimed_branch_ffs': {b: sorted(set(regs[b]) - used[b]) for b in ['bog', 'mapped']},
        'method': 'Bijection by common-checkpoint public Q-wire aliases, not instance numbering or row order',
    })
    return mappings, exclusions, regs


class Graph:
    """BOG connectivity; memoized bitsets count distinct upstream registers."""
    def __init__(self, module):
        self.module = module
        self.cells = module['cells']
        self.regs = registers(module)
        self.drivers, self.loads, self.port_bits = {}, Counter(), {}
        self.reg_index = {n: i for i, n in enumerate(sorted(self.regs))}
        self.primary_bits = set()
        for n, p in module['ports'].items():
            for i, bit in enumerate(p['bits']):
                index = p.get('offset', 0) + (len(p['bits']) - 1 - i if p.get('upto', 0) else i)
                key = f'{n}[{index}]' if len(p['bits']) > 1 or p.get('offset', 0) else n
                self.port_bits[key] = bit
                if p['direction'] == 'input':
                    self.primary_bits.add(bit)
                elif p['direction'] == 'output':
                    self.loads[bit] += 1
        for name, cell in self.cells.items():
            need(not cell['type'].startswith('$'), f'Unmapped/metadata cell {name}: rerun Step 4 with the scopeinfo fix')
            for pin, bits in cell['connections'].items():
                for bit in bits:
                    if not isinstance(bit, int):
                        continue
                    if cell['port_directions'][pin] == 'output':
                        need(bit not in self.drivers, f'Multiple drivers on net {bit}')
                        self.drivers[bit] = (name, pin)
                    else:
                        self.loads[bit] += 1
        self.cache = {}

    def pin(self, name):
        if '/' not in name:
            need(name in self.port_bits, f'Unknown path port {name}')
            return None, None, self.port_bits[name]
        cell_name, pin = name.rsplit('/', 1)
        need(cell_name in self.cells, f'STA/JSON cell mismatch: {cell_name}')
        cell = self.cells[cell_name]
        need(pin in cell['connections'] and len(cell['connections'][pin]) == 1,
             f'STA/JSON pin mismatch: {name}')
        return cell, pin, cell['connections'][pin][0]

    def cone_mask(self, bit):
        # Stop at ANY FF output, including QN; never traverse FF clock/reset pins.
        # The explicit stack avoids Python recursion depth and repeated cone walks.
        visiting = set()
        stack = [(bit, False)]
        while stack:
            net, expanded = stack.pop()
            if net in self.cache:
                continue
            if not isinstance(net, int):
                self.cache[net] = 0
                continue
            driver = self.drivers.get(net)
            if driver is None:
                need(net in self.primary_bits, f'Undriven BOG net {net}')
                self.cache[net] = 0
                continue
            name, _ = driver
            if name in self.regs:
                self.cache[net] = 1 << self.reg_index[name]
                continue
            cell = self.cells[name]
            inputs = {b for p, bits in cell['connections'].items()
                      if cell['port_directions'][p] == 'input' for b in bits}
            if expanded:
                value = 0
                for source in inputs:
                    value |= self.cache[source]
                self.cache[net] = value
                visiting.remove(net)
            else:
                need(net not in visiting, f'Combinational loop at net {net}')
                visiting.add(net)
                stack.append((net, True))
                stack.extend((source, False) for source in inputs if source not in self.cache)
        return self.cache[bit]

    def path_kind(self, path):
        cell, _, _ = self.pin(path['startpoint'])
        if cell is None:
            return 'input_to_reg'
        need(cell['type'].startswith('DFF'), 'Timing path does not start at an input or register')
        return 'reg_to_reg'


def timing_run(ctx, design, branch, count, base, period=10):
    tag = branch if period == 10 else f'{branch}_period{period}'
    out = base / 'timing' / tag
    out.mkdir(parents=True, exist_ok=True)
    source_sdc = ctx['dataset'] / 'constraints' / (design['design_id'] + '.sdc')
    sdc = source_sdc
    if period != 10:
        text, n = re.subn(r'(create_clock -name core_clk -period )10\.0\b',
                         lambda m: m.group(1) + str(float(period)), source_sdc.read_text())
        need(n == 1, 'Could not create the period-invariance test constraint')
        sdc = out / 'period_test.sdc'
        sdc.write_text(text)
    sta_netlist = prepare_sta_netlist(
        ctx['dataset'] / 'build' / design['design_id'] / (branch + '.v'),
        base / 'netlists' / (branch + '.v'), design['top'],
        ctx['sog'] if branch == 'bog' else ctx['full'],
        ctx['timeout'], run_command, ROOT)
    env = os.environ.copy()
    env.update({k: str(v) for k, v in {
        'P5_BACKEND': ctx['backend'], 'P5_LIB': ctx['sog'] if branch == 'bog' else ctx['full'],
        'P5_TECH_LEF': ctx['tech'], 'P5_CELL_LEF': ctx['cell'],
        'P5_NETLIST': sta_netlist,
        'P5_TOP': design['top'], 'P5_SDC': sdc, 'P5_REPORT_DIR': out,
        'P5_EXPECTED_FFS': count, 'P5_PERIOD': period, 'P5_JSON': int(period == 10),
    }.items()})
    run_command([ctx['sta_binary'], '-no_init', '-exit', ROOT / 'scripts/export_sta.tcl'],
                out / 'sta.log', ROOT, ctx['timeout'], env)
    need(f'P5_STA_OK endpoints={count} ' in (out / 'sta.log').read_text(),
         f'Timing tool did not finish successfully: {out}/sta.log')
    rows = read_csv(out / 'index.tsv', '\t')
    need(len(rows) == 2 * count, f'Missing timing queries in {tag}')
    if period == 10:
        # Archive both polarities with their stable report IDs, then remove only
        # the generated per-path JSON staging files after successful compression.
        archive = out / 'paths.jsonl.gz'
        with gzip.open(archive, 'wt') as dest:
            for row in rows:
                if row['status'] == 'ok':
                    path = out / (row['file_id'] + '.json')
                    checks = load(path)['checks']
                    need(len(checks) == 1, f'Expected one timing check in {path}')
                    dest.write(json.dumps({'file_id': row['file_id'], 'path': checks[0]}) + '\n')
        for row in rows:
            if row['status'] == 'ok':
                (out / (row['file_id'] + '.json')).unlink()
    return out


def load_timing(out, expected_pins, with_paths=True):
    paths = {}
    if with_paths:
        with gzip.open(out / 'paths.jsonl.gz', 'rt') as stream:
            for line in stream:
                data = json.loads(line)
                need(data['file_id'] not in paths, 'Duplicate path file ID')
                paths[data['file_id']] = data['path']
    rows = read_csv(out / 'index.tsv', '\t')
    grouped, statuses, keys = defaultdict(list), Counter(), set()
    for row in rows:
        pin, edge = row['endpoint_pin'], row['edge']
        need(pin in expected_pins and edge in {'rise', 'fall'}, f'Unexpected STA endpoint {pin}')
        need((pin, edge) not in keys, f'Duplicate endpoint/polarity {pin} {edge}')
        keys.add((pin, edge))
        statuses[row['status']] += 1
        need(row['status'] in {'ok', 'no_timing_path'}, f'Unconstrained/invalid endpoint {pin} {edge}: {row["status"]}')
        if row['status'] != 'ok':
            continue
        for key in ['arrival_ns', 'required_ns', 'slack_ns']:
            row[key] = float(row[key])
            need(math.isfinite(row[key]), f'Nonfinite timing at {pin}')
        need(row['arrival_ns'] >= 0, f'Negative arrival at {pin}')
        need(abs(row['required_ns'] - row['arrival_ns'] - row['slack_ns']) < 2e-6,
             f'Slack arithmetic failed at {pin}')
        if with_paths:
            path = paths[row['file_id']]
            need(path['endpoint'] == pin and path['target_clock'] == 'core_clk', 'Timing endpoint/clock mismatch')
            need(path['path_type'] == 'max' and path['target_clock_edge'] == 'rise', 'Not the requested setup scenario')
            for key, column in [('data_arrival_time', 'arrival_ns'), ('required_time', 'required_ns'), ('slack', 'slack_ns')]:
                # JSON has four significant digits; TSV get_property is more precise.
                need(math.isclose(float(path[key]) * 1e9, row[column], rel_tol=6e-4, abs_tol=2e-6),
                     f'JSON/TSV units or path mismatch: {pin} {column}')
            need(path['source_path'][-1]['pin'] == pin, 'Source path does not end at requested D pin')
            row['path'] = path
        grouped[pin].append(row)
    need(keys == {(pin, edge) for pin in expected_pins for edge in ['rise', 'fall']},
         'Not every D endpoint was queried for both polarities')
    return grouped, dict(statuses)


def max_arrival(rows):
    return max(rows, key=lambda r: (r['arrival_ns'], r['edge']))


def upstream_module():
    path = ROOT / 'third_party/rtl_timer/timing_path.py'
    spec = importlib.util.spec_from_file_location('rtl_timer_features', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def path_features(selected, graph, upstream):
    path = selected['path']
    retained = []
    kind = graph.path_kind(path)
    if kind == 'reg_to_reg' and path.get('source_clock_path'):
        retained.append(path['source_clock_path'][-1])
    levels = 0
    operator_counts = Counter()
    for point in path['source_path']:
        cell, pin, _ = graph.pin(point['pin'])
        if cell is None or cell['port_directions'][pin] == 'output' or point['pin'] == path['endpoint']:
            retained.append(point)
            if cell and cell['port_directions'][pin] == 'output' and not cell['type'].startswith('DFF'):
                levels += 1
                operator_counts[re.sub(r'_X\d+$', '', cell['type'])] += 1
    need(not (set(operator_counts) - {'AND2', 'OR2', 'INV', 'XOR2', 'MUX2', 'BUF'}),
         f'Unexpected SOG operator: {operator_counts}')
    obj = upstream.timing_path(path['startpoint'], path['endpoint'])
    for i, point in enumerate(retained):
        cell, pin, bit = graph.pin(point['pin'])
        t = re.sub(r'_X\d+$', '', cell['type']) if cell else 'PORT'
        driver = cell is None or cell['port_directions'][pin] == 'output'
        key = f'{i}:{point["pin"]}'
        obj.path.append(key)
        obj.node_dict[key] = upstream.CellNode(
            key, t, 1, float(point['slew']) * 1e9, 0,
            float(point['arrival']) * 1e9, '',
            fanout=graph.loads[bit] if driver else None,
            cap=float(point['capacitance']) * 1e15 if 'capacitance' in point else None)
    original = obj.get_feat_from_lst
    # Upstream mean([]) is NaN; empty fanout/cap lists represent zero here.
    obj.get_feat_from_lst = lambda values: original(values) if values else [0.0, 0.0, 0.0]
    vec = list(map(float, obj.get_path_feat()))  # Execute the authors' actual code.
    need(len(vec) == 16, 'Upstream feature-vector layout changed')
    vec[0] = selected['arrival_ns']  # same BOG value at TSV precision
    vec[1] = float(levels)  # upstream len(path)-3 assumes a CK/Q/D register path
    features = dict(zip(PATH_COLUMNS, vec))
    features.update(fanout_std=math.sqrt(features['fanout_variance']),
                    cap_std_ff=math.sqrt(features['cap_variance_ff2']),
                    slew_std_ns=math.sqrt(features['slew_variance_ns2']))
    for op, col in [('AND2', 'and2_count'), ('OR2', 'or2_count'), ('INV', 'inv_count'),
                    ('XOR2', 'xor2_count'), ('MUX2', 'mux2_count')]:
        need(features[col] == operator_counts[op], f'Operator counting mismatch: {op}')
    need(sum(features[k] for k in ['and2_count','or2_count','inv_count','xor2_count','mux2_count']) <= levels,
         'Operator count exceeds logic depth')
    need(all(math.isfinite(v) and v >= 0 for v in features.values()), 'Invalid path features')
    return features, retained


def period_check(original, changed):
    need(set(original) == set(changed), 'Clock period changed endpoint reachability')
    checked = 0
    for pin in original:
        a = {r['edge']: r for r in original[pin]}
        b = {r['edge']: r for r in changed[pin]}
        need(set(a) == set(b), f'Clock period changed polarity coverage: {pin}')
        for edge in a:
            need(abs(a[edge]['arrival_ns'] - b[edge]['arrival_ns']) < 1e-5,
                 f'Arrival changed with clock period: {pin} {edge}')
            for col in ['required_ns', 'slack_ns']:
                need(abs(b[edge][col] - a[edge][col] - 10.0) < 1e-4,
                     f'Period/slack invariant failed: {pin} {edge} {col}')
            checked += 1
    return checked


def extract_features(design, mapping, graph, bog, upstream):
    # This function receives NO mapped timing information. Rank is computed
    # over all finite BOG endpoints before the later label join.
    best = {pin: max_arrival(rows) for pin, rows in bog.items()}
    descending = sorted(-row['arrival_ns'] for row in best.values())
    need(bool(descending), 'No finite BOG arrivals')
    rows, evidence = {}, {}
    for item in mapping:
        pin = item['bog_d_pin']
        if pin not in best:
            continue
        chosen = best[pin]
        feature, trace = path_features(chosen, graph, upstream)
        fraction = bisect_left(descending, -chosen['arrival_ns']) / len(descending)
        group = 1 if fraction < .05 else 2 if fraction < .40 else 3 if fraction < .70 else 4
        drivers = graph.cone_mask(graph.regs[item['bog_ff']]['D']).bit_count()
        row = {'design': design['design_id'], 'rtl_bit': item['rtl_bit'],
               'split': design['split'], 'family': design['family'], 'category': design['category'],
               'bog_path_kind': graph.path_kind(chosen['path']),
               'bog_rank_level': group, 'bog_fraction_strictly_slower': fraction,
               'bog_sequential_cells': len(graph.regs),
               'bog_combinational_cells': len(graph.cells) - len(graph.regs),
               'bog_total_cells': len(graph.cells), 'driving_register_count': drivers, **feature}
        rows[item['rtl_bit']] = row
        evidence[item['rtl_bit']] = {'bog_file_id': chosen['file_id'], 'bog_d_pin': pin,
                                    'bog_edge': chosen['edge'], 'retained_bog_path': trace}
    return rows, evidence


def process_design(ctx, design):
    name = design['design_id']
    base = ctx['out'] / 'designs' / name
    base.mkdir(parents=True, exist_ok=True)
    complete = base / 'complete.json'
    if complete.exists():
        marker = load(complete)
        if marker['fingerprint'] == ctx['fingerprint'] and all(
                (base / p).is_file() and sha(base / p) == digest for p, digest in marker['outputs_sha256'].items()):
            print(f'REUSE {name}: verified existing outputs', flush=True)
            return load(base / 'audit.json')
        raise RuntimeError(f'{name}: existing completed outputs changed. Choose a new --out directory.')
    started = time.monotonic()
    print(f'START {name}', flush=True)
    modules = {}
    for b in ['common', 'bog', 'mapped']:
        data = load(ctx['dataset'] / 'build' / name / (b + '.json'))
        need(yosys_identity(data['creator']) == yosys_identity(ctx['yosys_version']),
             f'{name}: JSON was produced by a different Yosys version')
        modules[b] = data['modules'][design['top']]
    mapping, exclusions, regs = map_registers(design, modules, base)
    graphs = {b: Graph(modules[b]) for b in ['bog', 'mapped']}
    timing = {}
    statuses = {}
    controls = {}
    for b in ['bog', 'mapped']:
        directory = timing_run(ctx, design, b, len(regs[b]), base)
        timing[b], statuses[b] = load_timing(directory, {r['pin'] for r in regs[b].values()})
        pins = read_csv(directory / 'pins.tsv', '\t')
        controls[b] = sum(r['role'] == 'control_excluded' for r in pins)
    changed = timing_run(ctx, design, 'mapped', len(regs['mapped']), base, 20)
    t20, s20 = load_timing(changed, {r['pin'] for r in regs['mapped'].values()}, with_paths=False)
    checked = period_check(timing['mapped'], t20)
    need(statuses['mapped'] == s20, 'Clock period changed missing-path status')
    features, evidence = extract_features(design, mapping, graphs['bog'], timing['bog'], upstream_module())
    # Keep all BOG feature rows, including any which later lack a usable label.
    write_csv(base / 'features_bog_only.csv', sorted(features.values(), key=lambda r:r['rtl_bit']), FEATURE_FIELDS)
    joined, labels, used_features = [], [], []
    for item in sorted(mapping, key=lambda r:r['rtl_bit']):
        bit = item['rtl_bit']
        missing = []
        if bit not in features:
            missing.append('bog')
        if item['mapped_d_pin'] not in timing['mapped']:
            missing.append('mapped')
        if missing:
            exclusions.append(dict(design=name, common_ff=item['common_ff'], rtl_bit=bit,
                                   reason='no_constrained_path_' + '_and_'.join(missing),
                                   details='Neither edge has a reportable path in the listed branch(es); no zero label substituted'))
            continue
        candidates = timing['mapped'][item['mapped_d_pin']]
        chosen = max_arrival(candidates)
        label = {'design': name, 'rtl_bit': bit, 'mapped_d_pin': item['mapped_d_pin'],
                 'arrival_ns': chosen['arrival_ns'], 'arrival_edge': chosen['edge'],
                 'required_ns': chosen['required_ns'], 'slack_ns': chosen['slack_ns'],
                 'min_setup_slack_ns': min(r['slack_ns'] for r in candidates),
                 'mapped_path_kind': graphs['mapped'].path_kind(chosen['path']),
                 'mapped_polarities': len(candidates), 'scenario': 'nangate45_typical_period10_zero_wire_rc'}
        feature = features[bit]
        used_features.append(feature)
        labels.append(label)
        joined.append({**feature, 'label_arrival_ns': label['arrival_ns']})
        evidence[bit].update(mapped_file_id=chosen['file_id'], mapped_d_pin=item['mapped_d_pin'],
                             mapped_edge=chosen['edge'], label_arrival_ns=label['arrival_ns'])
    write_csv(base / 'features.csv', used_features, FEATURE_FIELDS)
    write_csv(base / 'labels.csv', labels, LABEL_FIELDS)
    write_csv(base / 'dataset.csv', joined, FEATURE_FIELDS + ['label_arrival_ns'])
    write_csv(base / 'exclusions.csv', exclusions, EXCLUSION_FIELDS)
    sample_bits = sorted(r['rtl_bit'] for r in joined)
    sample_bits = sorted({sample_bits[i] for i in [0, len(sample_bits)//2, len(sample_bits)-1]}) if sample_bits else []
    save(base / 'manual_samples.json', [{'rtl_bit': bit, 'features': features[bit], **evidence[bit]} for bit in sample_bits])
    audit = {'design': name, 'split': design['split'], 'family': design['family'],
             'common_registers': len(regs['common']), 'matched_public_rtl_bits': len(mapping),
             'bog_feature_rows_before_label_join': len(features), 'dataset_rows': len(joined),
             'excluded_registers': len(exclusions), 'exclusion_reasons': dict(Counter(r['reason'] for r in exclusions)),
             'coverage_all_common': len(joined)/len(regs['common']),
             'coverage_public_rtl': len(joined)/len(mapping),
             'bog_path_kinds': dict(Counter(r['bog_path_kind'] for r in joined)),
             'mapped_path_kinds': dict(Counter(r['mapped_path_kind'] for r in labels)),
             'timing_query_status': statuses, 'excluded_control_pins': controls,
             'period_invariance_polarities_checked': checked,
             'one_polarity_mapped_rows': sum(r['mapped_polarities'] == 1 for r in labels),
             'elapsed_seconds': round(time.monotonic() - started, 2)}
    need(len(joined) + len(exclusions) == len(regs['common']), 'Register coverage accounting does not balance')
    need(audit['coverage_all_common'] >= MIN_COVERAGE and audit['coverage_public_rtl'] >= MIN_COVERAGE,
         f'{name}: less than {MIN_COVERAGE:.0%} coverage; review exclusions before proceeding')
    audit['status'] = 'PASS_WITH_EXCLUSIONS' if exclusions else 'PASS'
    save(base / 'audit.json', audit)
    (base / 'failure.json').unlink(missing_ok=True)
    outputs = {str(p.relative_to(base)): sha(p) for p in base.rglob('*') if p.is_file() and p != complete}
    save(complete, {'fingerprint': ctx['fingerprint'], 'outputs_sha256': outputs})
    print(f'{audit["status"]} {name}: {len(joined)}/{len(regs["common"])} rows; {len(exclusions)} exclusions', flush=True)
    return audit


def run_all(ctx, args):
    designs = [d for d in ctx['manifest']['designs'] if not args.design or d['design_id'] == args.design]
    need(bool(designs), f'Unknown design ID: {args.design}')
    failures = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(process_design, ctx, d): d for d in designs}
        for future in as_completed(futures):
            name = futures[future]['design_id']
            try:
                future.result()
            except Exception as error:
                detail = {'design': name, 'error': str(error)}
                save(ctx['out'] / 'designs' / name / 'failure.json', detail)
                failures.append(detail)
                print(f'FAIL {name}: {error}', flush=True)
    need(not failures, 'Extraction failures remain. Inspect designs/<id>/failure.json and timing/*/sta.log; rerun after resolving.')
    if not args.design:
        audit_all(ctx)


def audit_all(ctx):
    all_rows, all_features, all_labels, all_exclusions, audits = [], [], [], [], []
    for design in ctx['manifest']['designs']:
        base = ctx['out'] / 'designs' / design['design_id']
        need((base / 'complete.json').is_file(), f'Missing completed design: {design["design_id"]}')
        marker = load(base / 'complete.json')
        need(marker['fingerprint'] == ctx['fingerprint'], 'Mixed extraction runs')
        for name, digest in marker['outputs_sha256'].items():
            need(sha(base / name) == digest, f'Output changed: {base/name}')
        rows = read_csv(base / 'dataset.csv')
        features = read_csv(base / 'features.csv')
        labels = read_csv(base / 'labels.csv')
        need(len(rows) == len(features) == len(labels), 'Feature/label join lost rows')
        for row, feature, label in zip(rows, features, labels):
            need(row['design'] == feature['design'] == label['design'] == design['design_id'], 'Wrong design joined')
            need(row['rtl_bit'] == feature['rtl_bit'] == label['rtl_bit'], 'Wrong register joined')
            need(row['split'] == design['split'] and row['family'] == design['family'], 'Split/family drift')
            need(set(feature) == set(FEATURE_FIELDS), 'Feature schema changed')
            need(all(row[k] == feature[k] for k in FEATURE_FIELDS), 'Feature values changed in label join')
            need(float(row['label_arrival_ns']) == float(label['arrival_ns']), 'Wrong label joined')
            for key in FEATURE_COLUMNS + ['label_arrival_ns']:
                need(math.isfinite(float(row[key])), f'NaN/inf: {design["design_id"]} {key}')
            need(float(row['bog_total_cells']) == float(row['bog_sequential_cells']) + float(row['bog_combinational_cells']), 'Cell counts inconsistent')
            need(0 <= float(row['driving_register_count']) <= float(row['bog_sequential_cells']), 'Invalid cone size')
            need(0 <= float(row['bog_fraction_strictly_slower']) < 1, 'Invalid rank percentile')
        all_rows.extend(rows); all_features.extend(features); all_labels.extend(labels)
        all_exclusions.extend(read_csv(base / 'exclusions.csv'))
        audits.append(load(base / 'audit.json'))
    keys = [(r['design'], r['rtl_bit']) for r in all_rows]
    need(len(keys) == len(set(keys)), 'Duplicate endpoint rows')
    need(len({r['design'] for r in all_rows}) == 20, 'A selected design has no usable rows')
    forbidden = ['mapped', 'label', 'slack', 'required', 'split', 'family', 'category', 'design', 'rtl_bit']
    need(not any(any(token in c for token in forbidden) for c in FEATURE_COLUMNS), 'Feature allowlist leakage')
    partitions = {s: [r for r in all_rows if r['split'] == s] for s in ['train', 'validation', 'test']}
    split = load(ctx['dataset'] / 'splits.json')['splits']
    for name, rows in partitions.items():
        need({r['design'] for r in rows} == set(split[name]), f'{name}: frozen membership changed')
    families = defaultdict(set)
    for row in all_rows:
        families[row['family']].add(row['split'])
    need(all(len(v) == 1 for v in families.values()), 'Family leakage across splits')
    # Identical feature vectors can legitimately occur in unrelated circuits;
    # report them instead of moving or deleting held-out rows.
    feature_splits = defaultdict(set)
    for row in all_rows:
        feature_splits[tuple(float(row[c]) for c in FEATURE_COLUMNS)].add(row['split'])
    cross_feature_duplicates = sum(len(s) > 1 for s in feature_splits.values())
    train = partitions['train']
    constant_columns = [c for c in FEATURE_COLUMNS if len({r[c] for r in train}) == 1]
    output = ctx['out'] / 'data'
    write_csv(output / 'features.csv', all_features, FEATURE_FIELDS)
    write_csv(output / 'labels.csv', all_labels, LABEL_FIELDS)
    write_csv(output / 'dataset.csv', all_rows, FEATURE_FIELDS + ['label_arrival_ns'])
    write_csv(output / 'exclusions.csv', all_exclusions, EXCLUSION_FIELDS)
    write_csv(output / 'dataset_bog_reg_to_reg.csv', [r for r in all_rows if r['bog_path_kind']=='reg_to_reg'],
              FEATURE_FIELDS + ['label_arrival_ns'])
    for name, rows in partitions.items():
        write_csv(output / (name + '.csv'), rows, FEATURE_FIELDS + ['label_arrival_ns'])
    save(output / 'feature_columns.json', FEATURE_COLUMNS)
    # Descriptive statistics are computed on TRAINING rows only. No scaling,
    # imputation, outlier removal, or feature selection is fitted here.
    import numpy as np
    statistics = []
    for col in FEATURE_COLUMNS + ['label_arrival_ns']:
        values = np.asarray([float(row[col]) for row in train], dtype=float)
        p0, p25, p50, p75, p95, p100 = np.percentile(values, [0, 25, 50, 75, 95, 100])
        statistics.append({'column': col, 'n': len(values), 'min': p0, 'p25': p25,
                           'median': p50, 'p75': p75, 'p95': p95, 'max': p100,
                           'mean': float(np.mean(values)), 'std': float(np.std(values))})
    write_csv(output / 'training_statistics.csv', statistics,
              ['column', 'n', 'min', 'p25', 'median', 'p75', 'p95', 'max', 'mean', 'std'])
    save(output / 'partition_manifest.json', {'fingerprint': ctx['fingerprint'],
         'split_unit': 'design family', 'designs': split, 'rows': {s:len(r) for s,r in partitions.items()}})
    columns = ['design', 'split', 'family', 'common_registers', 'matched_public_rtl_bits',
               'dataset_rows', 'excluded_registers', 'coverage_all_common', 'coverage_public_rtl',
               'period_invariance_polarities_checked', 'one_polarity_mapped_rows', 'status']
    write_csv(output / 'coverage_by_design.csv', [{k:a[k] for k in columns} for a in audits], columns)
    summary = {'status': 'PASS_WITH_EXCLUSIONS' if all_exclusions else 'PASS',
        'designs': 20, 'families': len(families), 'feature_count': len(FEATURE_COLUMNS),
        'common_registers': sum(a['common_registers'] for a in audits),
        'matched_public_rtl_bits': sum(a['matched_public_rtl_bits'] for a in audits),
        'dataset_rows': len(all_rows), 'excluded_registers': len(all_exclusions),
        'exclusion_reasons': dict(Counter(r['reason'] for r in all_exclusions)),
        'split_design_counts': {s:len(v) for s,v in split.items()},
        'split_row_counts': {s:len(v) for s,v in partitions.items()},
        'bog_path_kinds': dict(Counter(r['bog_path_kind'] for r in all_rows)),
        'mapped_path_kinds': dict(Counter(r['mapped_path_kind'] for r in all_labels)),
        'period_invariance_polarities_checked': sum(a['period_invariance_polarities_checked'] for a in audits),
        'constant_features_on_training_only': constant_columns,
        'identical_feature_vectors_crossing_splits': cross_feature_duplicates,
        'units': {'time':'ns', 'capacitance':'fF', 'variance':'squared corresponding units'},
        'checks': ['all 20 designs', 'unique public Q alias matching', 'actual D pins only',
                   'both endpoint polarities queried', 'SI-to-ns/fF consistency',
                   'arrival/slack arithmetic', 'period invariance on all designs',
                   'finite features and labels', 'complete exclusion accounting',
                   'unchanged family split', 'explicit BOG-only feature allowlist',
                   'input/code/tool/output checksums'],
        'limits': ['single SOG representation and one critical BOG path',
                   'no sampled-path max-loss or representation ensemble',
                   'no formal equivalence proof for the full 20-design set',
                   'post-synthesis labels with zero explicit wire RC',
                   'common-lowered register population, not a census of every pre-optimization RTL declaration',
                   'no model training or predictive evaluation in Step 5']}
    save(output / 'audit_summary.json', summary)
    lines = ['# Step 5 dataset audit', '', f"Status: **{summary['status']}**", '',
             f"{len(all_rows):,} rows, {len(all_exclusions):,} exclusions, 20 designs, {len(FEATURE_COLUMNS)} features.", '',
             '| Design | Split | Common FFs | Rows | Excluded | Coverage |',
             '|---|---|---:|---:|---:|---:|']
    for a in audits:
        lines.append(f"| {a['design']} | {a['split']} | {a['common_registers']} | {a['dataset_rows']} | {a['excluded_registers']} | {a['coverage_all_common']:.2%} |")
    lines += ['', 'Exclusions: `' + json.dumps(summary['exclusion_reasons']) + '`.', '',
              'All retained rows passed the recorded structural, timing, units, and split checks.',
              'No prediction scores were inspected. Missing timings were not converted into zero labels.', '',
              '## Limits', ''] + ['- ' + s for s in summary['limits']]
    (output / 'AUDIT.md').write_text('\n'.join(lines) + '\n')
    save(output / 'OUTPUTS.sha256.json', {p.name:sha(p) for p in output.glob('*')
                                        if p.is_file() and p.name != 'OUTPUTS.sha256.json'})
    print(json.dumps(summary, indent=2))
    print('STEP5 AUDIT PASS: dataset is ready for the training step; exclusions remain documented.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage', choices=['preflight', 'run', 'audit'])
    p.add_argument('--dataset-root', type=Path, default=Path(os.environ.get('P5_DATASET', ROOT.parent / 'step4-designs')))
    p.add_argument('--out', type=Path, default=ROOT / 'runs/run1')
    p.add_argument('--backend', choices=['openroad', 'sta'], default=os.environ.get('P5_BACKEND', 'openroad'))
    p.add_argument('--sta-bin', default=os.environ.get('P5_STA_BIN'))
    p.add_argument('--design', help='Canonical Step 4 design ID; run stage only')
    p.add_argument('--jobs', type=int, default=1)
    p.add_argument('--timeout', type=int, default=1200, help='Seconds per timing invocation')
    args = p.parse_args()
    need(args.jobs >= 1 and args.timeout > 0, 'jobs and timeout must be positive')
    need(not args.design or args.stage == 'run', '--design is only valid with run')
    ctx = preflight(args)
    if args.stage == 'run':
        run_all(ctx, args)
    elif args.stage == 'audit':
        audit_all(ctx)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, KeyError, ValueError, subprocess.SubprocessError) as error:
        print('STOP:', error, file=sys.stderr)
        sys.exit(1)
