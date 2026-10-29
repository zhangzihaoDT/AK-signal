"""Dated, unadjusted listed IC contracts from CFFEX's official daily files.

No continuous-contract stitching, trading API or leveraged-return calculation.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import csv
from io import StringIO
import json
import math
from pathlib import Path
import re
from uuid import uuid4

import pandas as pd
import requests
import yaml

from src.common.batch_release import atomic_json
from src.common.paths import project_root
from .calendar import SessionCalendar
from .core_indices import digest

RULE_VERSION = 'listed-derivative-facts-v1'
ENDPOINT = 'http://www.cffex.com.cn/sj/hqsj/rtj/{month}/{day}/{date}_1.csv'
NUMBERS = ('open', 'high', 'low', 'close', 'settle', 'pre_settle', 'volume',
           'open_interest', 'open_interest_change', 'return_1d', 'previous_close',
           'close_vs_pre_settle', 'basis_points', 'basis_rate')


def definitions(root):
    config = yaml.safe_load((root/'config/derivatives.yaml').read_text())
    policy = yaml.safe_load((root/'config/observation_sources.yaml').read_text()).get('derivatives', {})
    if (config.get('version') != 'derivatives-v1' or policy.get('provider') != 'cffex'
            or policy.get('price_basis') != 'unadjusted_contract_prices'
            or policy.get('daily_url') != ENDPOINT or policy.get('fallback') is not False
            or policy.get('universe_file') != 'config/derivatives.yaml'):
        raise ValueError('invalid derivatives source contract')
    products = config.get('products', [])
    # This first release deliberately supports the explicitly registered IC only.
    if len(products) != 1:
        raise ValueError('expected one registered derivative product')
    item = products[0]
    if (item.get('product') != 'IC' or item.get('name') != '中证500股指期货'
            or item.get('provider') != 'cffex' or item.get('exchange') != 'CFFEX'
            or item.get('series_kind') != 'listed_contracts' or item.get('expected_contract_count') != 4
            or item.get('spot_key') != 'csi500' or item.get('contract_multiplier_cny') != 200
            or item.get('quote_unit') != 'index_points' or item.get('volume_unit') != 'contracts'):
        raise ValueError('invalid IC contract definition')
    return item, digest({'registry': config, 'source': policy})


def source_url(date):
    return ENDPOINT.format(month=date[:6], day=date[6:], date=date)


def _dates(root, date):
    calendar = SessionCalendar.load(root/'config/market_calendars/XSHG.json')
    target = pd.Timestamp(date).date()
    if target not in calendar.days or datetime.now(timezone.utc) < datetime.fromisoformat(calendar.closes[target.isoformat()]):
        raise ValueError('derivatives target must be a complete session')
    prior = [d for d in calendar.days if d < target]
    if not prior:
        raise ValueError('derivatives previous session outside calendar')
    return calendar, prior[-1].strftime('%Y%m%d')


def parse_daily(content: bytes, date: str, item: dict) -> list[dict]:
    text = content.decode('gb18030')
    header = [c.strip() for c in next(csv.reader(StringIO(text)))]
    if len(set(header)) != len(header):
        raise ValueError('duplicate CFFEX column')
    frame = pd.read_csv(StringIO(text), dtype=str)
    frame.columns = frame.columns.str.strip()
    mapping = {'今开盘':'open', '最高价':'high', '最低价':'low', '今收盘':'close',
               '今结算':'settle', '前结算':'pre_settle', '成交量':'volume',
               '持仓量':'open_interest', '持仓变化':'open_interest_change'}
    if not {'合约代码', *mapping}.issubset(frame.columns) or frame.columns.duplicated().any():
        raise ValueError('CFFEX daily file schema mismatch')
    codes = frame['合约代码'].str.strip()
    if (codes.str.startswith(item['product'], na=False) & ~codes.str.fullmatch(item['product']+r'\d{4}', na=False)).any():
        raise ValueError('malformed IC contract code')
    selected = frame[codes.str.fullmatch(item['product']+r'\d{4}', na=False)].copy()
    selected['contract'] = codes.loc[selected.index]
    if selected.contract.duplicated().any() or len(selected) != item['expected_contract_count']:
        raise ValueError('IC listed contract coverage incomplete or duplicate')
    result = []
    target = pd.Timestamp(date)
    for _, source in selected.sort_values('contract').iterrows():
        contract = source['contract']
        month = contract[-4:]
        if not 1 <= int(month[2:]) <= 12 or month < target.strftime('%y%m'):
            raise ValueError('invalid or expired IC contract month')
        row = {'contract':contract}
        for native, key in mapping.items():
            value = float(source[native])
            if not math.isfinite(value) or (key != 'open_interest_change' and value < 0):
                raise ValueError('invalid CFFEX numeric field')
            if key in ('volume', 'open_interest', 'open_interest_change'):
                if value != int(value):
                    raise ValueError('CFFEX contract counts must be integers')
                value = int(value)
            elif value <= 0:
                raise ValueError('invalid CFFEX price')
            row[key] = value
        if not row['low'] <= min(row['open'],row['close']) <= max(row['open'],row['close']) <= row['high']:
            raise ValueError('invalid CFFEX OHLC range')
        result.append(row)
    return result


def acquire(root, date, previous_date, signature):
    folder = root/'data/sources/cffex/raw/daily/runs'/date/uuid4().hex
    folder.mkdir(parents=True, exist_ok=False)
    records = []
    for day in (previous_date, date):
        record = {'source_trade_date':day, 'url':source_url(day)}
        path = folder/(day+'_1.csv')
        try:
            response = requests.get(record['url'], timeout=25)
            path.write_bytes(response.content)
            record.update(file=path.name, sha256=hashlib.sha256(response.content).hexdigest(),
                          http_status=response.status_code)
            response.raise_for_status()
        except Exception as exc:
            record['error_type'] = type(exc).__name__
        records.append(record)
    manifest = {'trade_date':date, 'previous_trade_date':previous_date, 'registry_hash':signature,
                'provider':'cffex', 'generated_at':datetime.now(timezone.utc).isoformat(), 'records':records}
    path = folder/'manifest.json'
    atomic_json(path, manifest)
    receipt = {'snapshot_manifest':str(path), 'manifest_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    atomic_json(root/'data/sources/cffex/raw/daily/partitions'/f'{date}.json', receipt)
    return receipt


def _base(item, date, reason):
    return {'product':item['product'], 'name':item['name'], 'exchange':item['exchange'],
            'provider':item['provider'], 'price_basis':'unadjusted_contract_prices',
            'series_kind':item['series_kind'], 'contract_multiplier_cny':item['contract_multiplier_cny'],
            'quote_unit':item['quote_unit'], 'volume_unit':item['volume_unit'],
            'trade_date':pd.Timestamp(date).date().isoformat(), 'source_trade_date':None,
            'rule_version':RULE_VERSION, 'contract':None, 'security_id':None,
            'status':'UNAVAILABLE', 'reason':reason, 'is_main_open_interest':None,
            'previous_source_trade_date':None, 'return_status':reason, 'basis_status':reason,
            **dict.fromkeys(NUMBERS)}


def spot_reference(indices, item, date):
    matching = [r for r in indices['rows'] if r['key'] == item['spot_key']]
    if len(matching) != 1:
        raise ValueError('derivative spot index missing or duplicate')
    row = matching[0]
    fields = ('key','code','provider','status','source_trade_date','close','snapshot_id')
    ref = {k:row.get(k) for k in fields}
    ref['usable'] = (row['status'] == 'AVAILABLE' and row['source_trade_date'] == pd.Timestamp(date).date().isoformat()
                     and row.get('snapshot_id') is not None and row.get('close') is not None)
    return ref


def consume(root, date, item, signature, receipt, spot):
    if receipt is None:
        return [_base(item, date, 'MISSING_TARGET_BATCH')]
    path = Path(receipt['snapshot_manifest'])
    base = (root/'data/sources/cffex/raw/daily/runs'/date).resolve()
    if not path.resolve().is_relative_to(base) or path.name != 'manifest.json':
        raise ValueError('derivatives manifest outside target partition')
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != receipt['manifest_sha256']:
        raise ValueError('derivatives manifest hash mismatch')
    manifest = json.loads(content)
    calendar, previous_date = _dates(root, date)
    if (manifest.get('trade_date') != date or manifest.get('previous_trade_date') != previous_date
            or manifest.get('registry_hash') != signature or manifest.get('provider') != 'cffex'
            or [r['source_trade_date'] for r in manifest['records']] != [previous_date,date]):
        raise ValueError('derivatives manifest date/registry mismatch')
    fetched_at = datetime.fromisoformat(manifest['generated_at'])
    if fetched_at.tzinfo is None or fetched_at < datetime.fromisoformat(calendar.closes[pd.Timestamp(date).date().isoformat()]):
        return [_base(item, date, 'SESSION_INCOMPLETE')]
    frames = {}
    for record in manifest['records']:
        day = record['source_trade_date']
        if record.get('url') != source_url(day):
            raise ValueError('derivatives source date evidence mismatch')
        if 'file' in record:
            if record['file'] != day+'_1.csv':
                raise ValueError('derivatives source filename mismatch')
            raw = (path.parent/record['file']).read_bytes()
            if hashlib.sha256(raw).hexdigest() != record.get('sha256'):
                raise ValueError('derivatives raw file hash mismatch')
        if record.get('error_type') or record.get('http_status') != 200:
            return [_base(item, date, 'FETCH_FAILED')]
        try:
            frames[day] = parse_daily(raw, day, item)
        except (ValueError, KeyError, UnicodeError, TypeError):
            return [_base(item, date, 'INVALID_SOURCE_DATA')]
    previous = {r['contract']:r for r in frames[previous_date]}
    max_oi = max(r['open_interest'] for r in frames[date])
    rows = []
    for raw in frames[date]:
        row = _base(item, date, '')
        row.update(raw, security_id='CFFEX:'+raw['contract'], status='AVAILABLE', reason='',
                   source_trade_date=pd.Timestamp(date).date().isoformat(),
                   source_date_evidence='exchange_dated_filename', source_url=source_url(date),
                   fetched_at=manifest['generated_at'], is_main_open_interest=raw['open_interest'] == max_oi,
                   close_vs_pre_settle=raw['close']/raw['pre_settle']-1,
                   calendar_sha256=calendar.digest, calendar_version=calendar.version,
                   calendar_basis='CN_independent_rule_model_proxy_not_CFFEX_certified')
        prior = previous.get(raw['contract'])
        row.update(return_status='OK' if prior else 'INSUFFICIENT_HISTORY')
        if prior:
            if not math.isclose(raw['pre_settle'], prior['settle'], rel_tol=0, abs_tol=1e-8):
                return [_base(item, date, 'INCONSISTENT_PRE_SETTLE')]
            row.update(previous_close=prior['close'], return_1d=raw['close']/prior['close']-1,
                       previous_source_trade_date=pd.Timestamp(previous_date).date().isoformat())
            if raw['open_interest']-prior['open_interest'] != raw['open_interest_change']:
                return [_base(item, date, 'INCONSISTENT_OPEN_INTEREST_CHANGE')]
        row['basis_status'] = 'OK' if spot['usable'] else 'SPOT_UNAVAILABLE'
        if spot['usable']:
            row.update(basis_points=raw['close']-spot['close'], basis_rate=raw['close']/spot['close']-1)
        rows.append(row)
    return rows


def module_status(rows):
    available = sum(r['status'] == 'AVAILABLE' for r in rows)
    return {'status':'available' if available == len(rows) else 'unavailable', 'provider':'cffex',
            'product_count':1, 'available_contract_count':available, 'expected_contract_count':4}


def build(date, indices, *, online=False, root=None):
    root = root or project_root()
    date = pd.Timestamp(date).strftime('%Y%m%d')
    item, signature = definitions(root)
    _, previous = _dates(root, date)
    pointer = root/'data/sources/cffex/raw/daily/partitions'/f'{date}.json'
    receipt = (acquire(root, date, previous, signature) if online else
               json.loads(pointer.read_text()) if pointer.exists() else None)
    spot = spot_reference(indices, item, date)
    rows = consume(root, date, item, signature, receipt, spot)
    return {'rule_version':RULE_VERSION, 'trade_date':date, 'registry_hash':signature,
            'generated_at':datetime.now(timezone.utc).isoformat(), 'input_receipt':receipt,
            'spot_reference':spot, 'rows':rows, 'module':module_status(rows)}


def validate_document(document, date, indices, root=None):
    root = root or project_root()
    item, signature = definitions(root)
    if (document.get('trade_date') != date or document.get('rule_version') != RULE_VERSION
            or document.get('registry_hash') != signature):
        raise ValueError('derivatives document date/registry/rule mismatch')
    spot = spot_reference(indices, item, date)
    expected = consume(root, date, item, signature, document['input_receipt'], spot)
    if document['spot_reference'] != spot or document['rows'] != expected or document['module'] != module_status(expected):
        raise ValueError('derivatives facts differ from immutable inputs')
