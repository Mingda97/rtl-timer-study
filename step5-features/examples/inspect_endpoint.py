#!/usr/bin/env python3
"""Print one joined row and its actual BOG/mapped timing paths."""
import argparse
import csv
import gzip
import json
from pathlib import Path


def rows(path, delimiter=','):
    with path.open(newline='') as stream:
        return list(csv.DictReader(stream, delimiter=delimiter))


p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--run', type=Path, default=Path(__file__).resolve().parents[1] / 'runs/run1')
p.add_argument('--design', required=True)
p.add_argument('--rtl-bit')
args = p.parse_args()
base = args.run / 'designs' / args.design
data = rows(base / 'dataset.csv')
matches = [r for r in data if args.rtl_bit is None or r['rtl_bit'] == args.rtl_bit]
if not matches:
    raise SystemExit('No retained row matches. Inspect register_inventory.json and exclusions.csv.')
row = matches[0]
match = next(r for r in rows(base / 'register_map.csv') if r['rtl_bit'] == row['rtl_bit'])
print(json.dumps({'dataset_row': row, 'register_mapping': match}, indent=2))
for branch in ['bog', 'mapped']:
    directory = base / 'timing' / branch
    candidates = [r for r in rows(directory / 'index.tsv', '\t')
                  if r['endpoint_pin'] == match[branch + '_d_pin'] and r['status'] == 'ok']
    selected = max(candidates, key=lambda r: (float(r['arrival_ns']), r['edge']))
    with gzip.open(directory / 'paths.jsonl.gz', 'rt') as stream:
        report = next(obj for line in stream if (obj := json.loads(line))['file_id'] == selected['file_id'])
    path = report['path']
    points = [{'pin': v['pin'], 'arrival_ns': float(v['arrival']) * 1e9,
               'slew_ns': float(v['slew']) * 1e9,
               'cap_ff': float(v['capacitance']) * 1e15 if 'capacitance' in v else None}
              for v in path['source_path']]
    print(json.dumps({'branch': branch, 'selected_query': selected,
                      'startpoint': path['startpoint'], 'source_path': points}, indent=2))
