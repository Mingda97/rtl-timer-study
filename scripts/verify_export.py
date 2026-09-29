#!/usr/bin/env python3
"""Verify an exported snapshot without training, loading models, or reading test labels."""
import hashlib
import json
from pathlib import Path
import sys


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def main():
    root = Path(__file__).resolve().parents[1]
    record = json.loads((root / 'EXPORT_SHA256.json').read_text())
    bad = []
    for name, expected in record.items():
        p = root / name
        if p.is_symlink() or not p.is_file() or digest(p) != expected:
            bad.append(name)
    if bad:
        print('FAIL: missing/changed export files (or Git LFS pointers not downloaded):')
        print('\n'.join(bad))
        return 1
    manifest = json.loads((root / 'EXPORT_MANIFEST.json').read_text())
    print(f'EXPORT CHECK PASS: {len(record)} files match the exported snapshot.')
    for name, info in manifest['experiments'].items():
        print(f"{name}: depth={info['max_depth']}, learning_rate={info['learning_rate']}, trees={info['trees']}")
    print('Final test included:', manifest['final_test_included'])
    print('This is an integrity check, not a new training or timing run.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
