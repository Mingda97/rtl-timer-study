"""Narrow signed-declaration adapter for screened, mapped Yosys netlists.

Only an STA input copy is edited. Yosys checks that the before/after copies
have identical cell instances, parameters, ports, and bit connectivity.
Never use keyword removal on RTL before synthesis.
"""
import hashlib
import json
from pathlib import Path
import re
import shutil

REVISION = 'signed-declarations-v1'
TOKEN = re.compile(
    r'(?P<space>\s+)|(?P<comment>//[^\n]*|/\*[\s\S]*?\*/)'
    r'|(?P<escape>\\[^\s]+)|(?P<string>"(?:\\.|[^"\\])*")'
    r'|(?P<word>[A-Za-z_$][A-Za-z0-9_$]*)|(?P<other>.)')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(ok, message):
    if not ok:
        raise RuntimeError('STA netlist adapter: ' + message)


def normalize(text):
    """Remove only declaration signed tokens, preserving lines and names."""
    tokens = [m for m in TOKEN.finditer(text) if m.lastgroup not in ('space', 'comment')]
    edits = []
    for i, token in enumerate(tokens):
        if token.lastgroup != 'word' or token.group() != 'signed':
            continue
        previous = tokens[i-1] if i else None
        require(previous is not None and previous.lastgroup == 'word'
                and previous.group() in ('wire', 'input', 'output', 'inout'),
                'signed keyword outside a supported structural declaration; no conversion permitted')
        edits.append({'line': text.count('\n', 0, token.start()) + 1,
                      'start': token.start(), 'end': token.end(),
                      'declaration': previous.group()})
    result = text
    for edit in reversed(edits):
        result = result[:edit['start']] + ' ' * (edit['end']-edit['start']) + result[edit['end']:]
    return result, edits


def canonical(module):
    """Canonical structural graph; independent of JSON integer bit numbering.

    Signed declarations and source/formatting attributes are intentionally
    excluded. Their effects on elaborated connectivity are NOT excluded.
    Every explicitly declared wire, including unused wires, participates.
    Yosys can introduce hidden $indirect aliases for signed cell connections;
    those aliases must resolve to an explicitly declared bit as well.
    """
    require(not module.get('memories') and not module.get('processes'),
            'expected a fully mapped structural module')
    declared = {name: net for name, net in module['netnames'].items() if not net.get('hide_name', 0)}
    aliases = {}
    for name, net in sorted(declared.items()):
        for i, bit in enumerate(net['bits']):
            if isinstance(bit, int):
                aliases.setdefault(bit, (name, i))

    def bits(values):
        result = []
        for bit in values:
            if isinstance(bit, int):
                require(bit in aliases, f'bit {bit} has no wire alias')
                result.append(['net', *aliases[bit]])
            else:
                result.append(['constant', bit])
        return result

    ports = {name: {'direction': port['direction'], 'bits': bits(port['bits']),
                    'offset': port.get('offset', 0), 'upto': port.get('upto', 0)}
             for name, port in module['ports'].items()}
    for net in module['netnames'].values():
        bits(net['bits'])  # Reject any hidden bit without an explicit alias.
    nets = {name: {'bits': bits(net['bits']), 'offset': net.get('offset', 0),
                   'upto': net.get('upto', 0)} for name, net in declared.items()}
    cells = {}
    for name, cell in module['cells'].items():
        require(not cell['type'].startswith('$'), f'non-library cell remains: {name}: {cell["type"]}')
        cells[name] = {'type': cell['type'], 'parameters': cell.get('parameters', {}),
                       'port_directions': cell.get('port_directions', {}),
                       'connections': {pin: bits(conn) for pin, conn in cell['connections'].items()}}
    return {'ports': ports, 'netnames': nets, 'cells': cells,
            'parameter_default_values': module.get('parameter_default_values', {})}


def ys_quote(path):
    text = str(path)
    require('\n' not in text and '\r' not in text, 'newline in path')
    return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'


def prepare(source, target, top, liberty, timeout, run_command, cwd):
    source, target, liberty = map(Path, (source, target, liberty))
    original = source.read_bytes()
    converted, edits = normalize(original.decode('utf-8'))
    converted = converted.encode('utf-8')
    target.parent.mkdir(parents=True, exist_ok=True)
    report_path = target.with_suffix('.adapter.json')
    signature = {'revision': REVISION, 'source': str(source), 'source_sha256': digest(original),
                 'output_sha256': digest(converted), 'top': top,
                 'liberty_sha256': digest(liberty.read_bytes())}
    # The period-invariance run uses the already validated identical input copy.
    if target.exists() and report_path.exists():
        report = json.loads(report_path.read_text())
        if (all(report.get(k) == v for k, v in signature.items())
                and digest(target.read_bytes()) == signature['output_sha256']
                and report.get('status') == 'PASS'):
            return target
    # Discard only this adapter's own stale success marker before any rerun.
    report_path.unlink(missing_ok=True)
    target.write_bytes(converted)
    report = dict(signature, status='PASS', declarations_changed=len(edits), edits=edits,
                  verification='byte-identical input copy')
    if edits:
        yosys = shutil.which('yosys')
        require(yosys is not None, 'Yosys is not available')
        require(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_$]*', top), 'unsupported top identifier')
        parsed = []
        outputs = []
        for tag, netlist in [('before', source), ('after', target)]:
            output = target.with_suffix('.' + tag + '.json')
            script = target.with_suffix('.' + tag + '.ys')
            script.write_text(
                f'read_liberty -lib {ys_quote(liberty)}\n'
                f'read_verilog {ys_quote(netlist)}\n'
                f'hierarchy -check -top {top}\n'
                'check -assert -mapped\n'
                f'write_json {ys_quote(output)}\n')
            run_command([yosys, '-s', script], target.with_suffix('.' + tag + '.log'), cwd, timeout)
            data = json.loads(output.read_text())
            live = {n for n, m in data['modules'].items()
                    if int(str(m.get('attributes', {}).get('blackbox', '0')), 2) == 0}
            require(live == {top}, f'expected one flattened module, found {sorted(live)}')
            parsed.append(canonical(data['modules'][top]))
            outputs.append(output)
        require(parsed[0] == parsed[1],
                f'normalization changed elaborated connectivity in {source}; STA was NOT run. '
                'Keep the before/after JSON files for diagnosis.')
        report.update(verification='Yosys structural graph equality before/after; mapped checks passed',
                      graph_sha256=digest(json.dumps(parsed[0], sort_keys=True).encode()),
                      cells=len(parsed[0]['cells']), ports=len(parsed[0]['ports']),
                      named_nets=len(parsed[0]['netnames']),
                      roundtrip_json_sha256={p.name: digest(p.read_bytes()) for p in outputs})
        for output in outputs:
            output.unlink()  # Logs, scripts, hashes, and the STA copy are retained.
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    return target
