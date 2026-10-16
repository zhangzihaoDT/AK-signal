"""Reproduce the bounded TDX second-level industry package acceptance audit.

Run from repository root; never modifies the input archive or production settings.
"""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import struct
import zipfile
import zlib


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive', type=Path, required=True)
    p.add_argument('--evidence', type=Path, default=Path('docs/validation/tdx_package_20261016/evidence'))
    p.add_argument('--output', type=Path, default=Path('docs/validation/tdx_package_20261016'))
    args = p.parse_args()
    registry = Path('docs/validation/tdx_research_industry_universe_20261016.csv')
    universe = [r for r in csv.DictReader(registry.open()) if len(r['taxonomy_key']) == 5]
    assert len(universe) == 128
    official = {e['name'].replace('\\', '/').split('/')[-1]: e for e in json.loads((args.evidence / 'official_hsjday_directory.json').read_text())}
    reference_indices = json.loads((args.evidence / 'reference_indices.json').read_text())
    # API indices is a list of code/name records.
    reference_names = {r['code']: r['name'] for r in reference_indices}
    snapshots = [json.loads(f.read_text()) for f in sorted(args.evidence.glob('aksignal_rps_2026-*.json'))]
    coverage, comparisons, failures = [], [], []
    with zipfile.ZipFile(args.archive) as archive:
        assert archive.testzip() is None, 'Archive CRC failure'
        members = {n.replace('\\', '/').split('/')[-1]: n for n in archive.namelist()}
        for row in universe:
            code = row['tdx_code']
            filename = 'sh' + code + '.day'
            raw = archive.read(members[filename])
            assert len(raw) % 32 == 0, filename
            bars = list(struct.iter_unpack('<IIIIIfII', raw))
            dates = [b[0] for b in bars]
            invalid = sum(not (0 < b[3] <= min(b[1], b[4]) <= max(b[1], b[4]) <= b[2]) or not math.isfinite(b[5]) or b[5] < 0 for b in bars)
            ordered = dates == sorted(set(dates))
            crc_match = zlib.crc32(raw) == official[filename]['crc32']
            name_match = reference_names.get(code) == row['industry_name']
            if invalid or not ordered or not crc_match or not name_match:
                failures.append(code)
            coverage.append(dict(code=code, name=row['industry_name'], records=len(bars), first_date=dates[0], last_date=dates[-1], invalid_rows=invalid, unique_ordered=ordered, official_crc_match=crc_match, reference_name_match=name_match, sha256=hashlib.sha256(raw).hexdigest(), eligible_120=len(bars)>120, eligible_250=len(bars)>250))
            lookup = {date: i for i, date in enumerate(dates)}
            for snapshot in snapshots:
                records = snapshot['records']
                assert len(records) == len({r['code'] for r in records}) == 128
                ref = {r['code']: r for r in records}[code]
                i = lookup[int(snapshot['anchor'].replace('-', ''))]
                for window in (1, 5, 10, 15):
                    assert i >= window
                    calculated = bars[i][4] / bars[i-window][4] - 1
                    expected = ref['ret' + str(window)]
                    error = abs(calculated - expected)
                    comparisons.append(dict(code=code, anchor=snapshot['anchor'], window=window, calculated=calculated, reference=expected, absolute_error=error, passed=error <= 1e-6))
    args.output.mkdir(parents=True, exist_ok=True)
    for name, rows in [('coverage.csv', coverage), ('returns_comparison.csv', comparisons)]:
        with (args.output / name).open('w') as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    summary = dict(universe=128, covered=len(coverage), package_entries=len(members), archive_sha256=hashlib.sha256(args.archive.read_bytes()).hexdigest(), official_crc_matches=sum(r['official_crc_match'] for r in coverage), parse_failures=failures, comparison_count=len(comparisons), comparison_passed=sum(r['passed'] for r in comparisons), return_tolerance_decimal=1e-6, max_return_error=max(r['absolute_error'] for r in comparisons), reference_dates=sorted({r['anchor'] for r in comparisons}), latest_price_dates=sorted({r['last_date'] for r in coverage}), short_histories=[r for r in coverage if not r['eligible_120']], daily_stability='NOT_ESTABLISHED', production_gate='PENDING_DAILY_STABILITY')
    (args.output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    assert not failures and all(r['passed'] for r in comparisons)


if __name__ == '__main__':
    main()
