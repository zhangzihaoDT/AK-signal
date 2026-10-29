"""Read-only published ETF classification. No name inference or network access."""
from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import re

import pandas as pd

SCHEMA = 'etf-classification-config-v1'
CURRENT = 'config/etf_classification_facts.json'
VERSIONS = 'config/etf_classification_versions'
MARKETS = ['境内', '港股', '美国', '其他海外', '跨市场', '待核实']
DIRECTIONS = ['宽基', '科技', '消费', '医疗', '金融', '能源', '其他行业', '跨行业策略', '固收', '商品', '货币', '待核实']
COLUMNS = (
    'investment_market', 'investment_direction', 'market_classification_status',
    'direction_classification_status', 'classification_version', 'classification_sha256',
    'classification_basis', 'market_classification_evidence', 'direction_classification_evidence',
    'classification_input_status', 'classification_temporal_basis', 'classification_published_at',
    'market_classification_reason', 'direction_classification_reason', 'classification_rank_eligible',
)


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def validate_document(doc: dict) -> None:
    if not isinstance(doc, dict) or doc.get('schema_version') != SCHEMA:
        raise ValueError('Invalid ETF classification schema')
    if not isinstance(doc.get('classification_version'), str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', doc['classification_version']):
        raise ValueError('Invalid classification version')
    if datetime.fromisoformat(doc['published_at']).utcoffset() is None:
        raise ValueError('Classification publication time needs timezone')
    date.fromisoformat(doc['as_of_date'])
    if doc.get('investment_markets') != MARKETS or doc.get('investment_directions') != DIRECTIONS:
        raise ValueError('Classification vocabulary mismatch')
    if not isinstance(doc.get('records'), list):
        raise ValueError('Classification records must be a list')
    codes = set()
    for row in doc['records']:
        if not isinstance(row, dict):
            raise ValueError('Classification record must be an object')
        code = row.get('fund_code')
        if not isinstance(code, str) or not re.fullmatch(r'\d{6}', code) or code in codes:
            raise ValueError('Duplicate/invalid ETF classification code')
        codes.add(code)
        for key, values in (('investment_market', MARKETS), ('investment_direction', DIRECTIONS)):
            fact = row[key]
            if not isinstance(fact, dict):
                raise ValueError(f'{code}: classification fact must be an object')
            if fact.get('value') not in values or fact.get('status') not in ('verified', 'inferred', 'pending'):
                raise ValueError(f'{code}: invalid {key}')
            if (fact['status'] == 'pending') != (fact['value'] == '待核实'):
                raise ValueError(f'{code}: inconsistent classification status')
            date.fromisoformat(fact['as_of'])
            if fact.get('scope') not in ('mandate', 'holdings_snapshot'):
                raise ValueError(f'{code}: invalid classification scope')
            for bound in ('valid_from', 'valid_to'):
                if fact.get(bound) is not None:
                    date.fromisoformat(fact[bound])
            if fact.get('valid_from') and fact.get('valid_to') and fact['valid_from'] >= fact['valid_to']:
                raise ValueError(f'{code}: empty classification interval')
            if not isinstance(fact.get('source_ids'), list) or not fact.get('reason'):
                raise ValueError(f'{code}: missing classification provenance')
            if fact['status'] != 'pending' and not fact['source_ids']:
                raise ValueError(f'{code}: classification needs evidence')


def read_input(path: Path, expected_sha: str | None = None) -> dict:
    """Missing/unusable current config degrades; a changed pinned input is an error."""
    try:
        content = path.read_bytes()
        if expected_sha and digest(content) != expected_sha:
            raise ValueError('Pinned ETF classification checksum mismatch')
        doc = json.loads(content)
        if expected_sha and isinstance(doc, dict) and doc.get('schema_version') == 'etf-classification-unavailable-v1':
            return {'document': None, 'sha256': digest(content), 'reason': doc['reason']}
        validate_document(doc)
        return {'document': doc, 'sha256': digest(content), 'reason': ''}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if expected_sha:
            raise ValueError(f'Pinned ETF classification unavailable: {exc}') from exc
        return {'document': None, 'sha256': '', 'reason': f'{type(exc).__name__}: {exc}'}


def load_input() -> dict:
    from src.common.paths import project_root
    path = Path(os.environ.get('AKSIGNAL_ETF_CLASSIFICATION_PATH') or project_root() / CURRENT)
    return read_input(path, os.environ.get('AKSIGNAL_ETF_CLASSIFICATION_SHA256'))


def pin_input(root: Path, folder: Path, existing: dict | None = None) -> dict:
    """Freeze once per RunDay batch; recovery never re-resolves current config."""
    from src.common.batch_release import atomic_json
    path = folder / 'inputs' / 'etf_classification.json'
    if existing:
        if existing.get('file') != str(path):
            raise ValueError('ETF classification checkpoint path mismatch')
        read_input(path, existing['sha256'])
        return existing
    if not path.exists():
        loaded = read_input(root / CURRENT)
        payload = loaded['document'] or {'schema_version': 'etf-classification-unavailable-v1', 'reason': loaded['reason']}
        atomic_json(path, payload)
    content = path.read_bytes()
    loaded = read_input(path, digest(content))
    doc = loaded['document'] or {}
    return {'file': str(path), 'sha256': digest(content), 'classification_version': doc.get('classification_version'),
            'published_at': doc.get('published_at'), 'status': 'available' if doc else 'unavailable',
            'reason': loaded['reason'], 'source': CURRENT}


def _usable(fact: dict, target: str) -> bool:
    # Unknown start does not grant applicability before the evidence's as_of date.
    start = fact.get('valid_from') or fact['as_of']
    return (start <= target and (not fact.get('valid_to') or target < fact['valid_to'])
            and (fact['scope'] != 'holdings_snapshot' or fact['as_of'] == target))


def attach(master: pd.DataFrame, trade_date, loaded: dict | None = None) -> pd.DataFrame:
    loaded = load_input() if loaded is None else loaded
    doc = loaded['document'] or {}
    records = {r['fund_code']: r for r in doc.get('records', [])}
    target = pd.Timestamp(trade_date).date().isoformat()
    rows = []
    for _, row in master.iterrows():
        item = row.to_dict()
        record = records.get(str(row['fund_code']))
        item.update(classification_version=doc.get('classification_version', ''),
                    classification_sha256=loaded['sha256'], classification_basis='published_config',
                    classification_published_at=doc.get('published_at', ''),
                    classification_temporal_basis=('unavailable' if not doc else
                        'retrospective' if doc['published_at'][:10] > target else 'published_by_target'),
                    classification_input_status='available' if record else ('missing_code' if doc else 'unavailable'))
        for key, prefix in (('investment_market', 'market'), ('investment_direction', 'direction')):
            fact = record.get(key) if record else None
            usable = fact is not None and _usable(fact, target)
            item[key] = fact['value'] if usable else '待核实'
            item[f'{prefix}_classification_status'] = fact['status'] if usable else 'pending'
            item[f'{prefix}_classification_evidence'] = json.dumps(fact['source_ids'] if fact else [], ensure_ascii=False)
            item[f'{prefix}_classification_reason'] = (fact['reason'] if usable else
                ('classification_not_effective' if fact else loaded['reason'] or 'classification_missing_code'))
            if fact and not usable:
                item['classification_input_status'] = 'not_effective'
        direction, market = item['investment_direction'], item['investment_market']
        # Deterministic compatibility mapping from published values, never from names/old buckets.
        bucket = {'宽基': 'broad_market', '跨行业策略': 'factor_style', '固收': 'bond_credit',
                  '商品': 'commodity_futures', '货币': 'money_market', '待核实': ''}.get(direction, 'industry')
        if market in ('港股', '美国', '其他海外') and direction not in ('固收', '商品', '货币', '待核实'):
            bucket = 'overseas_equity'
        item['asset_bucket'] = bucket
        item['classification_rank_eligible'] = direction not in ('固收', '货币', '待核实')
        rows.append(item)
    return pd.DataFrame(rows, index=master.index)


def coverage(frame: pd.DataFrame) -> dict:
    """Classifications can be published yet inferred or pending; count independently."""
    if 'classification_input_status' not in frame:
        return {'status': 'unavailable', 'reason': 'classification_input_not_recorded', 'rows': len(frame)}
    result = {'rows': len(frame), 'versions': sorted(frame.classification_version.dropna().unique().tolist()),
              'sha256': sorted(frame.classification_sha256.dropna().unique().tolist()),
              'input_status': frame.classification_input_status.value_counts().to_dict()}
    for prefix in ('market', 'direction'):
        result[prefix] = frame[f'{prefix}_classification_status'].value_counts().to_dict()
    result['status'] = ('unavailable' if frame.classification_input_status.eq('unavailable').all() else
                        'partial' if result['market'].get('pending', 0) or result['direction'].get('pending', 0) else 'available')
    return result
