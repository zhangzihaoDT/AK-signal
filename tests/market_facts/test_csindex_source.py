from datetime import date, datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.market_facts import csindex_source as c
from src.market_facts.core_indices import definitions, identity, observe
from src.market_facts.calendar import SessionCalendar

ROOT = Path(__file__).resolve().parents[2]
ITEM = next(i for i in definitions(ROOT)[0] if i.code == 'H30269')
TARGET = date(2026, 10, 27)
START = date(2026, 10, 20)
FETCHED = datetime(2026, 10, 28, tzinfo=timezone.utc)


def payload():
    # Deliberately use a different object key order from AKShare's positional map.
    return {'success': True, 'code': '200', 'data': [
        {'close': 11171.65, 'indexCode': 'H30269', 'tradeDate': '20261027',
         'indexNameCn': '红利低波', 'low': 11019.31, 'high': 11227.98, 'open': 11036.97,
         'tradingVol': 4189715455., 'tradingValue': 464.76}]}


def test_official_endpoint_named_fields_raw_preservation_and_unknown_units(tmp_path, monkeypatch):
    content = json.dumps(payload()).encode()
    def get(url, params, timeout):
        assert url == c.ENDPOINT
        assert params == {'indexCode': 'H30269', 'startDate': '20261020', 'endDate': '20261027'}
        assert timeout == 25
        return SimpleNamespace(content=content, raise_for_status=lambda: None)
    monkeypatch.setattr(c.requests, 'get', get)
    raw = tmp_path/'raw.json'
    s = c.fetch_snapshot(identity(ITEM), ITEM.name, TARGET, START, raw)
    assert raw.read_bytes() == content
    assert s['identity']['provider'] == 'csindex'
    assert s['identity']['price_basis'] == 'index_level'
    assert s['bars'][0]['close'] == 11171.65
    assert s['bars'][0]['turnover'] is None and s['bars'][0]['volume'] is None
    assert s['identity']['turnover_unit'] == 'unknown'


@pytest.mark.parametrize('mutation,match', [
    (lambda j: j.update(success=False), 'unsuccessful'),
    (lambda j: j['data'][0].update(indexCode='H20269'), 'identity mismatch'),
    (lambda j: j['data'][0].update(indexNameCn='其他指数'), 'identity mismatch'),
    (lambda j: j['data'][0].update(tradeDate='20261028'), 'outside requested range'),
    (lambda j: j['data'][0].update(low=20000), 'invalid OHLC'),
    (lambda j: j['data'][0].update(open=None), 'invalid open'),
    (lambda j: j['data'][0].update(tradingValue=-1), 'volume/amount'),
    (lambda j: j['data'].append(dict(j['data'][0])), 'duplicate source date'),
])
def test_bad_official_facts_are_rejected_without_repair(mutation, match):
    j = payload()
    mutation(j)
    with pytest.raises(ValueError, match=match):
        c.from_payload(identity(ITEM), ITEM.name, j, TARGET, START, FETCHED)


def test_official_stale_target_is_unavailable_not_relabelled():
    j = payload()
    j['data'][0]['tradeDate'] = '20261026'
    s = c.from_payload(identity(ITEM), ITEM.name, j, TARGET, START, FETCHED)
    calendar = SessionCalendar.load(ROOT/'config/market_calendars/XSHG.json')
    row = observe(ITEM, s, calendar, '20261027')
    assert row['status'] == 'UNAVAILABLE' and row['reason'] == 'MISSING_TARGET'
    assert row['source_trade_date'] == '2026-10-26' and row['close'] is None


def test_http_failure_keeps_raw_evidence_without_fabricating_snapshot(tmp_path, monkeypatch):
    content = b'{"success":false}'
    def fail():
        raise c.requests.HTTPError('503')
    monkeypatch.setattr(c.requests, 'get', lambda *_a, **_kw: SimpleNamespace(content=content, raise_for_status=fail))
    raw = tmp_path/'response.json'
    with pytest.raises(c.requests.HTTPError):
        c.fetch_snapshot(identity(ITEM), ITEM.name, TARGET, START, raw)
    assert raw.read_bytes() == content
