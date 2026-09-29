#!/usr/bin/env python3
"""Verify the frozen design split, qualify RTL, and partition later datasets."""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)


def need(ok, message):
    if not ok:
        raise RuntimeError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def save(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')


def verify(require_sources=True):
    lock = load('FREEZE.lock.json')
    for name, expected in lock['files_sha256'].items():
        need(digest(name) == expected, f'Frozen file changed: {name}. Create an explicitly documented new dataset version.')
    m, split = load('designs.json'), load('splits.json')['splits']
    designs = {d['design_id']: d for d in m['designs']}
    need(len(designs) == 20, 'Expected 20 unique selected designs')
    need({k:len(v) for k,v in split.items()} == {'train':12,'validation':4,'test':4}, 'Expected 12/4/4 split')
    flattened = [d for values in split.values() for d in values]
    need(len(flattened) == len(set(flattened)) == 20 and set(flattened) == set(designs), 'Missing, duplicated, or unknown split IDs')
    families, source_users = defaultdict(set), defaultdict(set)
    for d in designs.values():
        need(d['design_id'] in split[d['split']], f"Split inconsistency: {d['design_id']}")
        families[d['family']].add(d['split'])
        for name in d['files']:
            source_users[(d['repository'],name)].add(d['split'])
    need(all(len(s)==1 for s in families.values()), 'A family crosses splits')
    need(all(n==4 for n in Counter(d['category'] for d in designs.values()).values()), 'Expected four designs per category')
    hash_splits = defaultdict(set)
    for source in m['source_files']:
        path = Path('sources') / source['repository'] / source['path']
        if require_sources:
            need(path.exists() and digest(path)==source['sha256'], f'Missing/modified pinned source: {path}')
        hash_splits[source['sha256']].update(source_users.get((source['repository'],source['path']),set()))
    need(all(len(s)<=1 for s in hash_splits.values()), 'Identical RTL file reused across splits')
    print(f'SPLIT CHECK PASS: 20 designs; 12 train / 4 validation / 4 test; {len(families)} family groups')
    return m


def fetch():
    m = verify(require_sources=False)
    for source in m['source_files']:
        target = Path('sources')/source['repository']/source['path']
        if target.exists():
            need(digest(target)==source['sha256'], f'Refusing to overwrite a modified source: {target}')
            continue
        repo = m['repositories'][source['repository']]
        slug = repo['url'].removeprefix('https://github.com/')
        url = f"https://raw.githubusercontent.com/{slug}/{repo['commit']}/{source['path']}"
        request = urllib.request.Request(url, headers={'User-Agent':'rtl-timing-dataset-v1'})
        content = urllib.request.urlopen(request, timeout=60).read()
        need(hashlib.sha256(content).hexdigest()==source['sha256'], f'Download hash mismatch: {target}')
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(content)
        print('Fetched',target)
    verify()


def q(value):
    return '"'+str(value).replace('\\','\\\\').replace('"','\\"')+'"'


def run_yosys(script, log, timeout):
    with Path(log).open('w') as output:
        p = subprocess.run(['yosys','-s',str(script)], stdout=output, stderr=subprocess.STDOUT, timeout=timeout)
    need(p.returncode==0, f'Yosys failed; inspect {log}')


def screen_one(d, full_lib, sog_lib, timeout):
    id = d['design_id']; started=time.monotonic()
    base=Path('build')/id; base.mkdir(parents=True,exist_ok=True)
    root=Path('sources')/d['repository']
    inc=' '.join('-I'+q(root/x) for x in d['include_dirs'])
    files=' '.join(q(root/x) for x in d['files'])
    defines=' '.join('-D'+x for x in d['defines'])
    params=' '.join(f'-set {key} {value}' for key,value in d['parameters'].items())
    change=f"chparam {params} {d['top']}\n" if params else ''
    common=f"""read_verilog -sv {defines} {inc} {files}
{change}hierarchy -check -top {d['top']}
rename -top {d['top']}
synth -top {d['top']} -flatten -noabc
# Remove hierarchy metadata, retaining all circuit cells and net names.
delete t:$scopeinfo
dffunmap
clean
check -assert
write_rtlil {base}/common.il
write_json {base}/common.json
"""
    script=base/'common.ys';script.write_text(common)
    run_yosys(script,base/'common.log',timeout)
    module=load(base/'common.json')['modules'][d['top']]
    clock=module['ports'][d['clock_port']]
    need(clock['direction']=='input' and len(clock['bits'])==1, f'{id}: invalid clock declaration')
    for reset in d['resets']:
        port=module['ports'][reset['port']]
        need(port['direction']=='input' and len(port['bits'])==1,f'{id}: invalid reset declaration')
    ffs=[]
    for name,cell in module['cells'].items():
        t=cell['type']
        need('LATCH' not in t and 'MEM' not in t.upper(),f'{id}: latch/unmapped memory: {name} {t}')
        if 'DFF' in t:
            need(cell['connections'].get('C')==clock['bits'],f'{id}: generated/additional clock at {name}')
            # First polarity character denotes the active clock edge.
            polarity=re.match(r'^\$_[A-Z]+_([PN])',t)
            need(polarity and polarity.group(1)=='P',f'{id}: unsupported FF clock polarity: {t}')
            ffs.append(cell)
    need(len(ffs)>=16,f'{id}: fewer than 16 register bits after lowering')
    results={'design_id':id,'split':d['split'],'family':d['family'],'status':'PASS',
             'generic_register_bits':len(ffs),'generic_cells':len(module['cells']),
             'clock_check':'one declared rising-edge clock','label_status':'not_generated'}
    for branch,lib in [('mapped',full_lib),('bog',sog_lib)]:
        script=base/f'{branch}.ys'
        script.write_text(f"""read_liberty -lib {q(lib)}
read_rtlil {base}/common.il
dfflibmap -liberty {q(lib)}
abc -liberty {q(lib)}
clean
hierarchy -check -top {d['top']}
check -assert -mapped
rename -enumerate
stat -liberty {q(lib)}
write_verilog -noattr -noexpr {base}/{branch}.v
write_json {base}/{branch}.json
""")
        run_yosys(script,base/f'{branch}.log',timeout)
        cells=load(base/f'{branch}.json')['modules'][d['top']]['cells']
        need(all(not c['type'].startswith('$') for c in cells.values()),f'{id}: unimplemented cell in {branch}')
        results[branch+'_cells']=len(cells)
        results[branch+'_register_bits']=sum(c['type'].startswith('DFF') for c in cells.values())
    results['seconds']=round(time.monotonic()-started,2)
    save(base/'screening.json',results)
    return results


def screen(args):
    m=verify()
    need(shutil.which('yosys'),'Yosys is not in PATH. Use your existing Codespace terminal.')
    platform=Path(os.environ.get('ORFS_ROOT','/OpenROAD-flow-scripts'))/'flow/platforms/nangate45'
    full=Path(os.environ.get('FULL_LIB',str(platform/'lib/NangateOpenCellLibrary_typical.lib'))).resolve()
    sog=Path(os.environ.get('SOG_LIB',str(ROOT.parent/'part3-pipeline/external/rtl-timer/vlg2bog/scr_ys/lib/nangate45_sog.lib'))).resolve()
    for path in [full,sog]: need(path.is_file(),f'Missing {path}; set FULL_LIB or SOG_LIB to the corresponding Part 3 Liberty file.')
    need(digest(full)==m['protocol']['full_lib_sha256'], 'Full Liberty differs from the frozen reference; resolve/version the library change explicitly.')
    need(digest(sog)==m['protocol']['sog_lib_sha256'], 'SOG Liberty differs from the frozen reference; use the pinned Part 3 library.')
    selected=[d for d in m['designs'] if not args.design or d['design_id']==args.design]
    need(bool(selected),'Unknown design ID')
    rows=[]
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        pending={pool.submit(screen_one,d,full,sog,args.timeout):d for d in selected}
        for future in as_completed(pending):
            d=pending[future]
            try: row=future.result()
            except Exception as error: row={'design_id':d['design_id'],'status':'FAIL','reason':str(error)}
            rows.append(row); print(row,flush=True)
    report={'yosys':subprocess.check_output(['yosys','-V'],text=True).strip(),
            'libraries_sha256':{'full':digest(full),'sog':digest(sog)},
            'split_lock_sha256':digest('FREEZE.lock.json'),
            'scope':'synthesis/mapping/clock/memory checks only; no per-bit matching or STA label validation',
            'designs':sorted(rows,key=lambda r:r['design_id'])}
    report_name='screening/'+(args.design or 'all')+'.json'; save(report_name,report)
    need(all(r['status']=='PASS' for r in rows),f'Screening failures; inspect {report_name}. Frozen assignments were not changed.')
    print(f'SCREEN PASS: {len(rows)} designs. Split files unchanged.')


def partition(args):
    m=verify(); identity={d['design_id']:d for d in m['designs']}
    with open(args.input,newline='') as src:
        reader=csv.DictReader(src); fields=reader.fieldnames
        need(fields and args.design_column in fields,'Dataset is missing the design ID column')
        rows=list(reader)
    need(bool(rows),'Input dataset is empty')
    need('rtl_bit' in fields,'Expected rtl_bit identity column')
    groups={s:[] for s in ['train','validation','test']}; seen=set(); keys=set()
    for row in rows:
        id=row[args.design_column]; need(id in identity,f'Unknown design ID {id}; pilot/backup rows are not in v1')
        key=(id,row['rtl_bit']); need(key not in keys,f'Duplicate endpoint row {key}; this partitioner expects one row per endpoint')
        keys.add(key); seen.add(id); groups[identity[id]['split']].append(row)
    need(seen==set(identity),f'Missing selected designs: {sorted(set(identity)-seen)}. Resolve failures or version an explicitly reduced dataset.')
    out=Path(args.out); out.mkdir(parents=True,exist_ok=True)
    for name,part in groups.items():
        target=out/f'{name}.csv'; need(not target.exists(),f'Refusing to overwrite {target}; choose a new --out directory')
    for name,part in groups.items():
        with (out/f'{name}.csv').open('w',newline='') as dest:
            writer=csv.DictWriter(dest,fieldnames=fields);writer.writeheader();writer.writerows(part)
    save(out/'partition_manifest.json',{'input_sha256':digest(args.input),'split_lock_sha256':digest('FREEZE.lock.json'),
         'rows':{s:len(r) for s,r in groups.items()},'designs':load('splits.json')['splits']})
    print('PARTITION PASS:',{s:len(r) for s,r in groups.items()})


def main():
    p=argparse.ArgumentParser(description=__doc__); sub=p.add_subparsers(dest='cmd',required=True)
    sub.add_parser('check');sub.add_parser('fetch')
    s=sub.add_parser('screen');s.add_argument('--jobs',type=int,default=2);s.add_argument('--timeout',type=int,default=300);s.add_argument('--design')
    s=sub.add_parser('partition');s.add_argument('input');s.add_argument('--out',default='partitioned');s.add_argument('--design-column',default='design')
    args=p.parse_args()
    if args.cmd=='check': verify()
    elif args.cmd=='fetch': fetch()
    elif args.cmd=='screen': screen(args)
    else: partition(args)


if __name__=='__main__':
    try:main()
    except (RuntimeError,FileNotFoundError,KeyError) as error:
        print('STOP:',error,file=sys.stderr);sys.exit(1)
