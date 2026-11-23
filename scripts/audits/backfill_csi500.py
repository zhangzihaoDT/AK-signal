"""Independent archive of official CSI price-index history."""
import argparse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import time

import pandas as pd
import requests

from src.market_facts.calendar import SessionCalendar
from src.market_facts.csindex_source import ENDPOINT

INDICES = {
    '000905': ('中证500', date(2004, 12, 31), date(2007, 1, 15)),
    '000688': ('科创50', date(2019, 12, 31), date(2020, 7, 23)),
    '000852': ('中证1000', date(2004, 12, 31), date(2014, 10, 17)),
}
ROOT = Path('data/sources/csindex')


def save(path, content):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_bytes(content)
    temp.replace(path)


def save_json(path, value):
    save(path, json.dumps(value, ensure_ascii=False, indent=2).encode())


def fetch(folder, start, end, code, name):
    params = dict(indexCode=code, startDate=start.strftime('%Y%m%d'),
                  endDate=end.strftime('%Y%m%d'))
    key = f'{start}_{end}'
    raw, receipt = folder / f'{key}.json', folder / f'{key}.receipt.json'
    if not receipt.exists():
        for attempt in range(3):
            try:
                response = requests.get(ENDPOINT, params=params, timeout=25)
                response.raise_for_status()
                break
            except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
                if attempt == 2 or (isinstance(exc, requests.HTTPError)
                                    and exc.response.status_code < 500
                                    and exc.response.status_code != 429):
                    raise
                time.sleep(2 ** (attempt + 1))
        save(raw, response.content)
        save_json(receipt, dict(endpoint=ENDPOINT, params=params, http_status=response.status_code,
                                fetched_at=datetime.now(timezone.utc).isoformat(),
                                sha256=hashlib.sha256(response.content).hexdigest()))
    content = raw.read_bytes()
    info = json.loads(receipt.read_bytes())
    if info['sha256'] != hashlib.sha256(content).hexdigest() or info['params'] != params:
        raise ValueError(f'{key}: receipt mismatch')
    payload = json.loads(content)
    if str(payload.get('code')) != '200' or payload.get('success') is not True:
        raise ValueError(f'{key}: official business error')
    records = {}
    for row in payload['data']:
        day = datetime.strptime(row['tradeDate'], '%Y%m%d').date()
        if (row['indexCode'] != code or row['indexNameCn'] != name
                or not start <= day <= end or day in records):
            raise ValueError(f'{key}: invalid identity, date or duplicate on {day}')
        records[day] = row
    print(key, len(records), flush=True)
    return records, dict(info, file=raw.name, count=len(records),
                         first=str(min(records)) if records else None,
                         last=str(max(records)) if records else None)


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--end', required=True, type=date.fromisoformat)
    parser.add_argument('--index-code', choices=INDICES, default='000905')
    args = parser.parse_args()
    code = args.index_code
    name, base, launch = INDICES[code]
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', args.run_id):
        parser.error('invalid run ID')
    calendar = SessionCalendar.load(Path('config/market_calendars/XSHG.json'))
    if args.end not in calendar.days or datetime.now(timezone.utc) < datetime.fromisoformat(
        calendar.closes[args.end.isoformat()]
    ):
        parser.error('end must be a complete session in the project calendar')
    raw, processed, audits = [ROOT / layer / 'history' / code / args.run_id
                              for layer in ('raw', 'processed', 'audits')]
    for path in (raw, processed, audits):
        path.mkdir(parents=True, exist_ok=True)
    manifest = raw / 'run.json'
    scope = dict(start=str(base), end=str(args.end))
    if manifest.exists() and json.loads(manifest.read_bytes()) != scope:
        parser.error('run ID is already bound to a different date range')
    save_json(manifest, scope)
    receipts = []

    def get(start, end):
        rows, receipt = fetch(raw, start, end, code, name)
        receipts.append(receipt)
        return rows

    probes = [(base, base + timedelta(days=14)), (launch - timedelta(days=7), launch + timedelta(days=4)),
              (date(2015, 6, 1), date(2015, 6, 30)),
              (args.end - timedelta(days=30), args.end)]
    for start, end in probes:
        get(start, end)
    first = get(date(2025, 1, 1), date(2025, 6, 30))
    second = get(date(2025, 7, 1), date(2025, 12, 31))
    if not first or not second or first | second != get(date(2025, 1, 1), date(2025, 12, 31)):
        raise ValueError('2025 full-year and half-year responses differ')

    merged, copied_boundaries, boundary_differences = {}, [], []
    for year in range(base.year + 1, args.end.year + 1):
        start = max(base, date(year, 1, 1) - timedelta(days=7))
        end = min(args.end, date(year + 1, 1, 8))
        segment = get(start, end)
        # The website can copy the next session onto the requested start date.
        dates = sorted(segment)
        if len(dates) > 1 and dates[0] == start and start != base:
            a, b = (segment[d] for d in dates[:2])
            if {k: v for k, v in a.items() if k != 'tradeDate'} == {
                k: v for k, v in b.items() if k != 'tradeDate'
            }:
                copied_boundaries.append(str(start))
                del segment[start]
        previous_overlap = {d: row for d, row in merged.items() if d >= start}
        overlap = {d: row for d, row in segment.items() if year > base.year + 1
                   and d <= date(year, 1, 8)}
        common = previous_overlap.keys() & overlap.keys()
        boundary_differences.extend(map(str, sorted(previous_overlap.keys() ^ overlap.keys())))
        if any(previous_overlap[d] != overlap[d] for d in common):
            save_json(audits / 'conflict.json', dict(year=year, dates=sorted(map(str, {
                d for d in common
                if previous_overlap.get(d) != overlap.get(d)
            }))))
            raise ValueError(f'{year}: overlapping records differ')
        merged.update(segment)

    base_record = merged.get(base)
    excluded_base = None
    if base_record and base_record['close'] != 1000:
        earlier = get(base - timedelta(days=3), base + timedelta(days=7))
        first_session = min(d for d in merged if d > base)
        fields = lambda row: {k: v for k, v in row.items() if k != 'tradeDate'}
        if (base not in earlier and first_session in earlier
                and fields(base_record) == fields(earlier[first_session])):
            excluded_base = merged.pop(base)
        else:
            raise ValueError('unresolved discrepancy with published base value')

    expected = {d for d in calendar.days if base <= d <= args.end}
    unexpected = {d for d in merged if calendar.start <= d <= args.end and d not in expected}
    table, issues = [], {}
    for day, row in sorted(merged.items()):
        if day in unexpected:
            continue
        prices = {k: row.get(k) for k in ('open', 'high', 'low', 'close')}
        close_ok = positive(prices['close'])
        o, h, l, c = prices.values()
        if not close_ok:
            status = 'INVALID_CLOSE'
            prices = dict.fromkeys(prices)
        elif any(p is None for p in (o, h, l)):
            status = 'CLOSE_ONLY_MISSING_OHL'
        elif not all(map(positive, (o, h, l))) or not l <= min(o, c) <= max(o, c) <= h:
            status = 'CLOSE_ONLY_INVALID_OHLC'
        else:
            status = 'OHLC_VALID'
        if status != 'OHLC_VALID':
            issues[str(day)] = status
            prices.update(open=None, high=None, low=None)
        table.append(dict(trade_date=str(day), index_code=code, source='csindex',
                          price_basis='index_level', pre_launch=day < launch, **prices,
                          volume_source_value=row.get('tradingVol'),
                          amount_source_value=row.get('tradingValue'), price_status=status))
    valid_days = {date.fromisoformat(row['trade_date']) for row in table if row['close'] is not None}
    report = dict(**scope, first=str(min(merged)) if merged else None,
                  last=str(max(merged)) if merged else None, rows=len(table),
                  price_status=Counter(row['price_status'] for row in table),
                  excluded_non_session_records=[merged[d] for d in sorted(unexpected)],
                  base_record=base_record, excluded_copied_base_record=excluded_base, pre_launch_rows=sum(day < launch for day in merged),
                  calendar=dict(start=str(calendar.start), end=str(args.end),
                                authority=calendar.authority, sha256=calendar.digest,
                                missing_sessions=sorted(map(str, expected - valid_days)),
                                unexpected_sessions=sorted(map(str, unexpected))),
                  copied_start_dates=copied_boundaries, one_sided_overlap_dates=boundary_differences, annual_rows=Counter(d.year for d in merged.keys() - unexpected), issues=issues, requests=receipts,
                  volume_amount_units='unverified')
    save_json(audits / 'audit.json', report)
    if args.end not in valid_days:
        raise ValueError('target close missing')
    frame = pd.DataFrame(table)
    csv_path, parquet_path = processed / f'{code}.csv', processed / f'{code}.parquet'
    save(csv_path, frame.to_csv(index=False).encode())
    temp = parquet_path.with_suffix('.parquet.tmp')
    frame.to_parquet(temp, index=False)
    pd.testing.assert_frame_equal(frame, pd.read_parquet(temp))
    temp.replace(parquet_path)
    report['outputs_sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in (csv_path, parquet_path)}
    save_json(audits / 'audit.json', report)


if __name__ == '__main__':
    main()
