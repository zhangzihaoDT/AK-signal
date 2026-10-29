"""Native CSI price-index history, using the official website's named fields."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path

import requests

from .contract import build_snapshot

ENDPOINT = 'https://www.csindex.com.cn/csindex-home/perf/index-perf'


def from_payload(identity, name, payload, target, start, fetched_at):
    if payload.get('success') is not True or str(payload.get('code')) != '200' or not isinstance(payload.get('data'), list):
        raise ValueError('csindex response unsuccessful or schema invalid')
    rows = []
    for record in payload['data']:
        if record.get('indexCode') != identity.provider_symbol or record.get('indexNameCn') != name:
            raise ValueError('csindex index identity mismatch')
        day = datetime.strptime(record['tradeDate'], '%Y%m%d').date()
        if not start <= day <= target:
            raise ValueError('csindex bar outside requested range')
        # Preserve unknown volume/amount units in the raw response. They are not
        # needed by these price observations and are not relabelled as yuan/lots.
        for key in ('tradingVol', 'tradingValue'):
            value = record.get(key)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                      or not math.isfinite(value) or value < 0):
                raise ValueError('csindex invalid volume/amount')
        prices = {k: record.get(k) for k in ('open', 'high', 'low', 'close')}
        rows.append({'source_trade_date': day.isoformat(), 'date_evidence': 'source_dated_daily',
                     **prices, 'volume': None, 'turnover': None})
    return build_snapshot(identity, rows, target, fetched_at)


def fetch_snapshot(identity, name, target, start, raw_path: Path):
    response = requests.get(ENDPOINT, params={'indexCode': identity.provider_symbol,
                                            'startDate': start.strftime('%Y%m%d'),
                                            'endDate': target.strftime('%Y%m%d')}, timeout=25)
    raw_path.write_bytes(response.content)
    response.raise_for_status()
    return from_payload(identity, name, json.loads(response.content), target, start,
                        datetime.now(timezone.utc))
