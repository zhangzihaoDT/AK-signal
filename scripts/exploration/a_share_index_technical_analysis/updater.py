"""Manually extend the research snapshots through the preceding CN session."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date, datetime, timedelta
import fcntl
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
from zoneinfo import ZoneInfo

# Support both direct invocation and python -m from the repository.
REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pandas as pd

from scripts.audits.backfill_csi500 import INDICES, fetch, positive, save_json
from src.market_facts.calendar import SessionCalendar

TOPIC = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def target_session(calendar, as_of, now):
    if as_of > now.date():
        raise ValueError('as-of must not be in the future')
    target = calendar.expected_session(as_of - timedelta(days=1))
    if now < datetime.fromisoformat(calendar.closes[target.isoformat()]):
        raise ValueError('target session has not closed')
    return target


def load_frame(root, scope, code):
    source = root / scope['source']
    if digest(source) != scope['source_sha256']:
        raise ValueError(f'{code}: input CSV hash mismatch')
    frame = pd.read_csv(source, dtype={'trade_date': str, 'index_code': str}, float_precision='round_trip')
    if (not frame.trade_date.is_unique or not frame.trade_date.is_monotonic_increasing
            or not frame.index_code.eq(code).all() or not frame.source.eq('csindex').all()
            or not frame.price_basis.eq('index_level').all()):
        raise ValueError(f'{code}: invalid source identity or dates')
    selected = frame.loc[frame.trade_date.between(scope['start'], scope['end'])]
    if (selected.empty or len(selected) != scope['bars']
            or selected.trade_date.iloc[0] != scope['start']
            or frame.trade_date.iloc[-1] != scope['end']):
        raise ValueError(f'{code}: source range differs from manifest')
    audit_path = root / scope['source_audit']
    audit = json.loads(audit_path.read_bytes())
    if audit['outputs_sha256'][source.name] != digest(source):
        raise ValueError(f'{code}: source audit mismatch')
    inputs = {'source': scope['source'], 'source_sha256': digest(source),
              'audit': scope['source_audit'], 'audit_sha256': digest(audit_path)}
    if 'pe_ttm' not in frame:
        enriched = source.parent.with_name(source.parent.name + '-pe-ttm') / source.name
        enriched_audit = audit_path.parent.with_name(audit_path.parent.name + '-pe-ttm') / audit_path.name
        metadata = json.loads(enriched_audit.read_bytes())
        if digest(enriched) != metadata['outputs_sha256'][source.name]:
            raise ValueError(f'{code}: PE-TTM seed hash mismatch')
        pe_frame = pd.read_csv(enriched, dtype={'trade_date': str, 'index_code': str}, float_precision='round_trip')
        pd.testing.assert_frame_equal(pe_frame.drop(columns='pe_ttm'), frame, check_exact=True)
        frame = pe_frame
        inputs['pe_seed'] = {'source': str(enriched.relative_to(root)),
                             'sha256': digest(enriched),
                             'audit': str(enriched_audit.relative_to(root)),
                             'audit_sha256': digest(enriched_audit)}
    return frame, inputs


def parse_row(code, day, record):
    prices = {key: record[key] for key in ('open', 'high', 'low', 'close')}
    o, h, l, c = prices.values()
    if not all(map(positive, prices.values())) or not l <= min(o, c) <= max(o, c) <= h:
        raise ValueError(f'{code} {day}: invalid OHLC')
    pe = record['peg']
    if pe is not None and (type(pe) not in (int, float) or not math.isfinite(pe)):
        raise ValueError(f'{code} {day}: invalid PE-TTM')
    for field in ('tradingVol', 'tradingValue'):
        value = record.get(field)
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
            raise ValueError(f'{code} {day}: invalid {field}')
    return dict(trade_date=str(day), index_code=code, source='csindex', price_basis='index_level',
                pre_launch=day < INDICES[code][2], **prices,
                volume_source_value=record.get('tradingVol'),
                amount_source_value=record.get('tradingValue'), price_status='OHLC_VALID', pe_ttm=pe)


def extend(root, scope, code, target, calendar, batch, now):
    frame, inputs = load_frame(root, scope, code)
    last = date.fromisoformat(scope['end'])
    if last > target:
        raise ValueError(f'{code}: current data extend beyond requested T-1')
    if last == target and 'pe_seed' not in inputs:
        return scope
    paths = {layer: root / 'data/sources/csindex' / layer / 'history' / code / batch
             for layer in ('raw', 'processed', 'audits')}
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    receipts = []
    while last < target:
        # Bound long catch-ups and overlap on a genuine session to avoid copied start records.
        end = calendar.expected_session(min(target, last + timedelta(days=365)))
        records, receipt = fetch(paths['raw'], last, end, code, INDICES[code][0])
        receipt['file'] = str((paths['raw'] / receipt['file']).relative_to(root))
        receipts.append(receipt)
        expected = {day for day in calendar.days if last <= day <= end}
        if set(records) != expected:
            raise ValueError(f'{code}: incomplete daily range, missing={sorted(expected - records.keys())}, '
                             f'unexpected={sorted(records.keys() - expected)}')
        rows = [parse_row(code, day, records[day]) for day in sorted(records)]
        overlap = pd.DataFrame(rows[:1], columns=frame.columns)
        pd.testing.assert_frame_equal(frame.tail(1).reset_index(drop=True), overlap,
                                      check_dtype=False, check_exact=True, obj=f'{code} overlap')
        frame = pd.concat([frame, pd.DataFrame(rows[1:], columns=frame.columns)], ignore_index=True)
        last = end
    for column in ('pe_ttm', 'volume_source_value', 'amount_source_value'):
        frame[column] = frame[column].astype('float64')
    csv_path = paths['processed'] / f'{code}.csv'
    parquet_path = paths['processed'] / f'{code}.parquet'
    frame.to_csv(csv_path, index=False)
    frame.to_parquet(parquet_path, index=False)
    pd.testing.assert_frame_equal(frame, pd.read_parquet(parquet_path))
    pd.testing.assert_frame_equal(frame, pd.read_csv(csv_path, dtype={'trade_date': str, 'index_code': str}, float_precision='round_trip'),
                                  check_dtype=False, check_exact=False, rtol=1e-14)
    selected = frame.loc[frame.trade_date.between(scope['start'], str(target))]
    audit_path = paths['audits'] / 'audit.json'
    save_json(audit_path, dict(index_code=code, generated_at=now.isoformat(), target_trade_date=str(target),
                              inputs=inputs, requests=receipts, rows=len(frame),
                              calendar_sha256=calendar.digest, calendar_authority=calendar.authority,
                              pe_ttm_source_field='peg', pe_ttm_units='multiple',
                              pe_ttm_share_basis='calculation_shares',
                              pe_ttm_null_rows=int(frame.pe_ttm.isna().sum()),
                              outputs_sha256={p.name: digest(p) for p in (csv_path, parquet_path)}))
    return dict(scope, source=str(csv_path.relative_to(root)), source_sha256=digest(csv_path),
                source_audit=str(audit_path.relative_to(root)), end=str(target), bars=len(selected))


def update(root, manifest_path, as_of=None, now=None):
    now = now or datetime.now(ZoneInfo('Asia/Shanghai'))
    now = now.astimezone(ZoneInfo('Asia/Shanghai'))
    calendar = SessionCalendar.load(root / 'config/market_calendars/XSHG.json')
    target = target_session(calendar, as_of or now.date(), now)
    lock_path = root / 'data/audits/a_share_index_technical_analysis/updater.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('another updater is running') from exc
        original = manifest_path.read_bytes()
        manifest = json.loads(original)
        if manifest['provider'] != 'csindex' or manifest['price_basis'] != 'index_level':
            raise ValueError('unsupported source contract')
        candidate = deepcopy(manifest)
        batch = f'research-{target:%Y%m%d}-{uuid4().hex[:12]}'
        for code, scope in manifest['indices'].items():
            candidate['indices'][code] = extend(root, scope, code, target, calendar, batch, now)
        if candidate != manifest:
            if manifest_path.read_bytes() != original:
                raise ValueError('sources.json changed during update')
            candidate['update_batch'] = batch
            candidate['updated_at'] = now.isoformat()
            save_json(manifest_path, candidate)
        for code, scope in candidate['indices'].items():
            print(f"{code}: {scope['start']} → {scope['end']}, {scope['bars']} rows", flush=True)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--as-of', type=date.fromisoformat, help='Shanghai reference date, default today')
    parser.add_argument('--data-only', action='store_true', help='Skip HTML and ZIP regeneration')
    args = parser.parse_args()
    try:
        update(REPO, TOPIC / 'sources.json', args.as_of)
        if not args.data_only:
            subprocess.run([sys.executable, str(TOPIC / 'generate.py'), '--repo-root', str(REPO), '--package'],
                           check=True, cwd=REPO)
            subprocess.run([sys.executable, str(REPO / 'dashboards/update_catalog.py')],
                           check=True, cwd=REPO)
    except (ValueError, RuntimeError, AssertionError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f'Update failed: {exc}\n')


if __name__ == '__main__':
    main()
