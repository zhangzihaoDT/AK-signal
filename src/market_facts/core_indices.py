"""Fixed native-index observations, independent of ETF products and rankings.

Acquisition creates immutable, dated batches for each registered provider. Consumption verifies
their receipt and computes calendar-bounded facts; unavailable rows stay visible.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import re
import time
from uuid import uuid4

import pandas as pd
import yaml

from src.common.batch_release import atomic_json
from src.common.paths import project_root
from .calendar import SessionCalendar
from .contract import SeriesIdentity, validate_snapshot
from .longbridge_source import fetch_snapshot, quote_context

RULE_VERSION = 'core-index-facts-v1'
HISTORY_CALENDAR_DAYS = 365  # 65 sessions required; do not certify older history.
RETURNS = (1, 5, 20, 60)
VALUES = ('close', 'return_1d', 'return_5d', 'return_20d', 'return_60d',
          'ma20', 'ma60', 'ma20_change_5d', 'ma60_change_5d')


@dataclass(frozen=True)
class Index:
    key: str
    name: str
    code: str
    issuer: str
    category: str
    provider_symbol: str | None
    exchange: str
    provider: str = 'longbridge'


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def definitions(root: Path) -> tuple[list[Index], str]:
    config = yaml.safe_load((root / 'config/core_indices.yaml').read_text())
    policy = yaml.safe_load((root / 'config/observation_sources.yaml').read_text()).get('indices', {})
    if (config.get('version') != 'core-indices-v1' or set(policy.get('providers', [])) != {'longbridge', 'csindex'}
            or policy.get('price_basis') != 'index_level' or policy.get('fallback') is not False
            or policy.get('universe_file') != 'config/core_indices.yaml'):
        raise ValueError('invalid core index source contract')
    items = [Index(**item) for item in config['indices']]
    if not items or len({i.key for i in items}) != len(items) or len({(i.issuer, i.code) for i in items}) != len(items):
        raise ValueError('duplicate or empty core index registry')
    symbols = [(i.provider, i.provider_symbol) for i in items if i.provider_symbol]
    if len(set(symbols)) != len(symbols):
        raise ValueError('duplicate provider index symbol')
    for item in items:
        suffix = {'SSE': 'SH', 'SZSE': 'SZ'}.get(item.exchange)
        if (not re.fullmatch(r'[a-z][a-z0-9_]*', item.key) or not item.name or item.issuer not in {'CSI', 'SSE', 'SZSE'}
                or item.category not in {'broad', 'style'} or suffix is None
                or not re.fullmatch(r'[A-Z0-9]{6}', item.code)
                or item.provider not in {'longbridge', 'csindex'}
                or (item.provider == 'csindex' and (item.issuer != 'CSI' or item.provider_symbol != item.code))
                or (item.provider == 'longbridge' and item.provider_symbol is not None and item.provider_symbol != f'{item.code}.{suffix}')):
            raise ValueError('invalid core index identity')
    return items, digest({'registry': config, 'source': policy, 'history_calendar_days': HISTORY_CALENDAR_DAYS})


def identity(item: Index) -> SeriesIdentity:
    if not item.provider_symbol:
        raise ValueError('unverified provider symbol')
    from .csindex_source import ENDPOINT
    official = item.provider == 'csindex'
    return SeriesIdentity(f'{item.issuer}:{item.code}', 'CN', item.exchange, 'CNY',
                          'index', item.provider, 'csindex_http' if official else 'longbridge_sdk',
                          ENDPOINT if official else 'history_candlesticks_by_date', item.provider_symbol,
                          'index_level', 'not_applicable_price_index' if official else 'NoAdjust')


def _static_record(info) -> dict:
    return {key: str(getattr(info, key, '') or '') for key in
            ('symbol', 'name_cn', 'board', 'exchange', 'currency')}


def _matches(item: Index, info: dict) -> bool:
    if item.provider == 'csindex':
        return (info.get('symbol') == item.code and info.get('name_cn') == item.name
                and info.get('source') == 'official_indexCode_and_name')
    return (info.get('symbol') == item.provider_symbol and info.get('name_cn') == item.name
            and info.get('board', '').split('.')[-1] == 'CNIX'
            and info.get('currency') in ('', 'CNY')
            and info.get('exchange') in ('', item.exchange))


def acquire(root: Path, date: str, items: list[Index], signature: str) -> dict:
    """Only this boundary performs network requests. Errors never expose SDK text."""
    from longbridge.openapi import Period, AdjustType
    provider = items[0].provider
    if any(i.provider != provider for i in items):
        raise ValueError('acquisition batch must have one provider')
    base = root / f'data/sources/{provider}/raw/core_indices'
    folder = base / 'runs' / date / uuid4().hex
    folder.mkdir(parents=True, exist_ok=False)
    records = []
    ctx, info, connection_error = None, {}, None
    if provider == 'longbridge':
        try:
            ctx = quote_context(root / '.env' if (root / '.env').exists() else None)
            info = {_static_record(r)['symbol']: _static_record(r)
                    for r in ctx.static_info([i.provider_symbol for i in items if i.provider_symbol])}
        except Exception as exc:
            connection_error = type(exc).__name__
    else:
        info = {i.provider_symbol: {'symbol': i.code, 'name_cn': i.name, 'source': 'official_indexCode_and_name'} for i in items}
    for item in items:
        record = {'key': item.key}
        if not item.provider_symbol:
            record['reason'] = 'PROVIDER_SYMBOL_UNVERIFIED'
        elif connection_error:
            record.update(reason='FETCH_FAILED', error_type=connection_error)
        elif not _matches(item, info.get(item.provider_symbol, {})):
            record.update(reason='IDENTITY_UNVERIFIED', identity_evidence=info.get(item.provider_symbol, {}))
        else:
            record['identity_evidence'] = info[item.provider_symbol]
            try:
                target = pd.Timestamp(date).date()
                if provider == 'longbridge':
                    snapshot = fetch_snapshot(ctx, identity(item), target, Period.Day, AdjustType.NoAdjust,
                                              start_date=target-timedelta(days=HISTORY_CALENDAR_DAYS))
                else:
                    from .csindex_source import fetch_snapshot as fetch_official
                    snapshot = fetch_official(identity(item), item.name, target,
                                              target-timedelta(days=HISTORY_CALENDAR_DAYS), folder/(item.key+'.http.json'))
                name = item.key + '.json'
                atomic_json(folder / name, snapshot)
                record.update(file=name, snapshot_id=snapshot['snapshot_id'], reason='',
                              sha256=hashlib.sha256((folder / name).read_bytes()).hexdigest())
            except Exception as exc:
                record.update(reason='FETCH_FAILED', error_type=type(exc).__name__)
            raw = folder/(item.key+'.http.json')
            if raw.exists():
                record.update(raw_file=raw.name, raw_sha256=hashlib.sha256(raw.read_bytes()).hexdigest())
            time.sleep(.6)
        records.append(record)
    manifest = {'trade_date': date, 'registry_hash': signature, 'provider': provider,
                'history_calendar_days': HISTORY_CALENDAR_DAYS,
                'generated_at': datetime.now(timezone.utc).isoformat(), 'records': records}
    path = folder / 'manifest.json'
    atomic_json(path, manifest)
    receipt = {'snapshot_manifest': str(path), 'manifest_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    atomic_json(base / 'partitions' / f'{date}.json', receipt)
    return receipt


def _manifest(root: Path, date: str, signature: str, items: list[Index], receipt: dict) -> dict:
    path = Path(receipt['snapshot_manifest'])
    provider = items[0].provider
    base = (root / f'data/sources/{provider}/raw/core_indices/runs' / date).resolve()
    if not path.resolve().is_relative_to(base) or path.name != 'manifest.json':
        raise ValueError('index manifest outside target partition')
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != receipt['manifest_sha256']:
        raise ValueError('index manifest hash mismatch')
    manifest = json.loads(content)
    if (manifest.get('trade_date') != date or manifest.get('registry_hash') != signature
            or manifest.get('history_calendar_days') != HISTORY_CALENDAR_DAYS
            or manifest.get('provider') != provider
            or [r.get('key') for r in manifest['records']] != [i.key for i in items]):
        raise ValueError('index manifest date/registry mismatch')
    return manifest


def _empty(item: Index, date: str, reason: str) -> dict:
    return {**asdict(item), 'trade_date': pd.Timestamp(date).date().isoformat(),
            'security_id': f'{item.issuer}:{item.code}', 'asset_type': 'index', 'market': 'CN',
            'adapter': 'csindex_http' if item.provider == 'csindex' else 'longbridge_sdk',
            'endpoint': identity(item).endpoint if item.provider_symbol else 'history_candlesticks_by_date',
            'adjustment_requested': 'not_applicable_price_index' if item.provider == 'csindex' else 'NoAdjust',
            'identity_verified': False,
            'provider': item.provider, 'price_basis': 'index_level', 'price_basis_verified': False,
            'rule_version': RULE_VERSION, 'status': 'UNAVAILABLE', 'reason': reason,
            'history_calendar_days': HISTORY_CALENDAR_DAYS,
            'source_trade_date': None, 'snapshot_id': None, 'fetched_at': None,
            'trend_state': 'UNKNOWN', 'history_days': 0,
            **dict.fromkeys(VALUES), **{f'return_{n}d_status': reason for n in RETURNS}}


def observe(item: Index, snapshot: dict, calendar: SessionCalendar, date: str) -> dict:
    snapshot = validate_snapshot(snapshot)
    target = pd.Timestamp(date).date()
    if snapshot['identity'] != asdict(identity(item)) or snapshot['target_trade_date'] != target.isoformat():
        raise ValueError('index snapshot identity/date mismatch')
    row = _empty(item, date, 'MISSING_TARGET')
    bars = snapshot['bars']
    row.update(snapshot_id=snapshot['snapshot_id'], fetched_at=snapshot['fetched_at'],
               history_days=len(bars), source_trade_date=bars[-1]['source_trade_date'] if bars else None,
               calendar_version=calendar.version, calendar_sha256=calendar.digest,
               calendar_authority=calendar.authority)
    gate = calendar.assess(snapshot)
    reason = ('CALENDAR_UNCOVERED' if not calendar.start <= target <= calendar.end else
              'TARGET_NOT_SESSION' if gate['target_is_session'] is not True else
              'SESSION_INCOMPLETE' if gate['session_complete'] is not True else
              'MISSING_TARGET' if not snapshot['quality']['target_source_date_verified'] else
              'SOURCE_DATE_UNVERIFIED' if not snapshot['quality']['source_dates_verified'] else
              'NON_SESSION_BARS' if gate['unexpected_sessions'] else '')
    if reason:
        row.update(reason=reason, **{f'return_{n}d_status': reason for n in RETURNS})
        return row
    values = {pd.Timestamp(b['source_trade_date']): b['close'] for b in bars}
    sessions = calendar.index()[calendar.index() <= pd.Timestamp(date)]
    series = pd.Series(values).reindex(sessions)
    first = min(values)

    def window(n):
        days = sessions[-n:]
        if len(days) < n:
            return 'CALENDAR_UNCOVERED'
        if days[0] < first:
            return 'INSUFFICIENT_HISTORY'
        return 'DATA_GAP' if series.loc[days].isna().any() else 'OK'

    row['close'] = float(series.iloc[-1])
    for n in RETURNS:
        status = window(n + 1)
        row[f'return_{n}d_status'] = status
        if status == 'OK':
            row[f'return_{n}d'] = float(series.iloc[-1] / series.iloc[-n-1] - 1)
    for n in (20, 60):
        if window(n) == 'OK':
            row[f'ma{n}'] = float(series.iloc[-n:].mean())
        if window(n + 5) == 'OK':
            row[f'ma{n}_change_5d'] = float(row[f'ma{n}'] / series.iloc[-n-5:-5].mean() - 1)
    trend_gate = window(65)
    row.update(status='AVAILABLE' if trend_gate == 'OK' else
               'INSUFFICIENT_HISTORY' if trend_gate == 'INSUFFICIENT_HISTORY' else 'UNAVAILABLE',
               reason='' if trend_gate == 'OK' else trend_gate)
    if trend_gate == 'OK':
        rising = all(row['close'] > row[f'ma{n}'] and row[f'ma{n}_change_5d'] > 0 for n in (20, 60))
        falling = all(row['close'] < row[f'ma{n}'] and row[f'ma{n}_change_5d'] < 0 for n in (20, 60))
        row['trend_state'] = 'RISING' if rising else 'FALLING' if falling else 'MIXED'
    return row


def _consume_provider(root: Path, date: str, items: list[Index], signature: str, receipt: dict | None) -> list[dict]:
    if receipt is None:
        return [_empty(i, date, 'MISSING_TARGET_BATCH' if i.provider_symbol else 'PROVIDER_SYMBOL_UNVERIFIED') for i in items]
    manifest = _manifest(root, date, signature, items, receipt)
    calendar = SessionCalendar.load(root / 'config/market_calendars/XSHG.json')
    folder = Path(receipt['snapshot_manifest']).parent
    rows = []
    for item, record in zip(items, manifest['records']):
        if 'file' not in record:
            if record.get('reason') not in {'PROVIDER_SYMBOL_UNVERIFIED', 'FETCH_FAILED', 'IDENTITY_UNVERIFIED'}:
                raise ValueError('index missing snapshot without reason')
            rows.append(_empty(item, date, record['reason']))
            continue
        if record['file'] != item.key + '.json' or not _matches(item, record.get('identity_evidence', {})):
            raise ValueError('index snapshot identity evidence mismatch')
        content = (folder / record['file']).read_bytes()
        if hashlib.sha256(content).hexdigest() != record.get('sha256'):
            raise ValueError('index raw file hash mismatch')
        snapshot = json.loads(content)
        if snapshot['snapshot_id'] != record.get('snapshot_id'):
            raise ValueError('index snapshot receipt mismatch')
        if item.provider == 'csindex':
            from .csindex_source import from_payload
            if record.get('raw_file') != item.key+'.http.json':
                raise ValueError('csindex raw receipt missing')
            raw = (folder/record['raw_file']).read_bytes()
            if hashlib.sha256(raw).hexdigest() != record.get('raw_sha256'):
                raise ValueError('csindex raw response hash mismatch')
            target = pd.Timestamp(date).date()
            native = from_payload(identity(item), item.name, json.loads(raw), target,
                                  target-timedelta(days=HISTORY_CALENDAR_DAYS),
                                  datetime.fromisoformat(snapshot['fetched_at']))
            if native['snapshot_id'] != snapshot['snapshot_id']:
                raise ValueError('csindex snapshot differs from raw response')
        rows.append(observe(item, snapshot, calendar, date))
    return rows


def consume(root: Path, date: str, items: list[Index], signature: str, receipt: dict | None) -> list[dict]:
    receipt = receipt or {}
    providers = {i.provider for i in items}
    if set(receipt) - providers:
        raise ValueError('unexpected index provider receipt')
    rows = {}
    for provider in sorted(providers):
        subset = [i for i in items if i.provider == provider]
        rows.update({r['key']: r for r in _consume_provider(root, date, subset, signature, receipt.get(provider))})
    return [rows[i.key] for i in items]


def module_status(rows: list[dict]) -> dict:
    available = sum(r['status'] == 'AVAILABLE' for r in rows)
    return {'status': 'available' if available == len(rows) else 'partial' if available else 'unavailable',
            'provider': 'mixed' if len({r['provider'] for r in rows}) > 1 else rows[0]['provider'],
            'providers': sorted({r['provider'] for r in rows}), 'price_basis': 'index_level',
            'available_count': available, 'expected_count': len(rows),
            'broad_count': sum(r['category'] == 'broad' for r in rows),
            'style_count': sum(r['category'] == 'style' for r in rows)}


def build(date: str, *, online=False, root: Path | None = None) -> dict:
    root = root or project_root()
    date = pd.Timestamp(date).strftime('%Y%m%d')
    items, signature = definitions(root)
    calendar = SessionCalendar.load(root / 'config/market_calendars/XSHG.json')
    target = pd.Timestamp(date).date()
    if (target not in calendar.days or datetime.now(timezone.utc) < datetime.fromisoformat(calendar.closes[target.isoformat()])):
        raise ValueError('core index target must be a complete calendar session')
    receipt = {}
    for provider in sorted({i.provider for i in items}):
        pointer = root / f'data/sources/{provider}/raw/core_indices/partitions' / f'{date}.json'
        subset = [i for i in items if i.provider == provider]
        receipt[provider] = (acquire(root, date, subset, signature) if online else
                             json.loads(pointer.read_text()) if pointer.exists() else None)
    rows = consume(root, date, items, signature, receipt)
    return {'rule_version': RULE_VERSION, 'trade_date': date, 'registry_hash': signature,
            'generated_at': datetime.now(timezone.utc).isoformat(), 'input_receipt': receipt,
            'rows': rows, 'module': module_status(rows)}


def validate_document(document: dict, date: str, root: Path | None = None) -> None:
    root = root or project_root()
    items, signature = definitions(root)
    if (document.get('rule_version') != RULE_VERSION or document.get('trade_date') != date
            or document.get('registry_hash') != signature):
        raise ValueError('core index document date/registry/rule mismatch')
    expected = consume(root, date, items, signature, document['input_receipt'])
    if document['rows'] != expected or document['module'] != module_status(expected):
        raise ValueError('core index facts differ from immutable inputs')
