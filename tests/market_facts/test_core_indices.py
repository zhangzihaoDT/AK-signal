from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pandas as pd
import pytest
import yaml

from src.market_facts import core_indices as m
from src.market_facts.calendar import SessionCalendar
from src.market_facts.contract import build_snapshot
from src.observation.core_indices import panels, markdown

ROOT = Path(__file__).resolve().parents[2]
DATE = '20261027'


@pytest.fixture
def root(tmp_path):
    (tmp_path / 'config/market_calendars').mkdir(parents=True)
    for name in ('core_indices.yaml', 'observation_sources.yaml', 'market_calendars/XSHG.json'):
        shutil.copyfile(ROOT / 'config' / name, tmp_path / 'config' / name)
    return tmp_path


def snapshot(item=None, count=100, omit=None, fetched=None):
    item = item or m.definitions(ROOT)[0][0]
    calendar = SessionCalendar.load(ROOT / 'config/market_calendars/XSHG.json')
    days = [d for d in calendar.days if d <= pd.Timestamp(DATE).date()][-count:]
    bars = [dict(source_trade_date=d.isoformat(), date_evidence='source_dated_daily',
                 open=1000+i, high=1002+i, low=998+i, close=1001+i, volume=100, turnover=1000)
            for i, d in enumerate(days) if i != omit]
    return build_snapshot(m.identity(item), bars, pd.Timestamp(DATE).date(),
                          fetched or datetime(2026, 10, 27, 9, tzinfo=timezone.utc))


def observe(s):
    return m.observe(m.definitions(ROOT)[0][0], s, SessionCalendar.load(ROOT/'config/market_calendars/XSHG.json'), DATE)


def test_fixed_registry_and_native_index_returns():
    items, _ = m.definitions(ROOT)
    assert len(items) == 9 and sum(i.category == 'broad' for i in items) == 8
    assert [i.code for i in items] == ['000300', '000905', '000852', '399006', '000688', '932000', '000985', '000510', 'H30269']
    row = observe(snapshot())
    assert row['status'] == 'AVAILABLE' and row['trend_state'] == 'RISING'
    assert row['return_20d'] == pytest.approx(1100/1080-1)
    assert row['ma20'] == pytest.approx(sum(range(1081, 1101))/20)
    assert row['source_trade_date'] == '2026-10-27'
    assert row['price_basis'] == 'index_level' and row['price_basis_verified'] is False
    assert not any('rps' in k.lower() or 'fund_code' in k for k in row)


def test_window_gaps_are_not_bridged_and_shorter_returns_stay_valid():
    row = observe(snapshot(omit=60))
    assert row['return_20d'] is not None
    assert row['return_60d'] is None and row['return_60d_status'] == 'DATA_GAP'
    assert row['status'] == 'UNAVAILABLE' and row['trend_state'] == 'UNKNOWN'
    short = observe(snapshot(count=21))
    assert short['status'] == 'INSUFFICIENT_HISTORY'
    assert short['return_20d'] is not None and short['return_60d'] is None


@pytest.mark.parametrize('source,reason', [
    (lambda: snapshot(omit=99), 'MISSING_TARGET'),
    (lambda: snapshot(fetched=datetime(2026,10,27,3,tzinfo=timezone.utc)), 'SESSION_INCOMPLETE'),
])
def test_stale_or_intraday_prices_never_become_daily_facts(source, reason):
    row = observe(source())
    assert row['status'] == 'UNAVAILABLE' and row['reason'] == reason
    assert all(row[k] is None for k in m.VALUES)


def test_identity_and_invalid_ohlc_are_rejected():
    s = snapshot()
    s['identity']['asset_type'] = 'etf'
    with pytest.raises(ValueError, match='hash mismatch'):
        observe(s)
    s = snapshot()
    s['bars'][-1]['low'] = s['bars'][-1]['high'] + 1
    with pytest.raises(ValueError, match='invalid OHLC'):
        observe(s)
    i = m.definitions(ROOT)[0][0]
    other = replace(m.identity(i), asset_type='stock')
    s = build_snapshot(other, snapshot()['bars'], pd.Timestamp(DATE).date(), datetime(2026,10,27,9,tzinfo=timezone.utc))
    with pytest.raises(ValueError, match='identity/date'):
        observe(s)


def test_offline_keeps_all_rows_and_does_not_read_etf_or_network(root, monkeypatch):
    monkeypatch.setattr(m, 'quote_context', lambda *_a: pytest.fail('offline network request'))
    document = m.build(DATE, root=root)
    assert len(document['rows']) == 9
    assert document['module']['status'] == 'unavailable'
    assert document['rows'][5]['reason'] == 'MISSING_TARGET_BATCH'
    assert document['rows'][0]['reason'] == 'MISSING_TARGET_BATCH'
    m.validate_document(document, DATE, root)
    html = panels(document)
    assert html.count('<tr data-index-key=') == 9
    assert html.index('沪深300') < html.index('中证2000') < html.index('中证A500') < html.index('红利低波')
    assert 'UNAVAILABLE' in html and '缺少目标日指数快照' in html
    assert '<details' not in html
    assert all(i.name in markdown(document) for i in m.definitions(root)[0])


def acquire_fixture(root, monkeypatch, fail=None):
    from src.market_facts import csindex_source
    items, _ = m.definitions(root)
    class Context:
        def static_info(self, symbols):
            return [SimpleNamespace(symbol=i.provider_symbol, name_cn=i.name, board='CNIX', exchange=i.exchange, currency='CNY')
                    for i in items if i.provider_symbol in symbols]
    monkeypatch.setattr(m, 'quote_context', lambda *_a: Context())
    monkeypatch.setattr(m.time, 'sleep', lambda *_a: None)
    def fetch(_ctx, identity, target, period, adjustment, start_date):
        assert str(adjustment).endswith('NoAdjust')
        assert (target-start_date).days == 365
        item = next(i for i in items if i.provider_symbol == identity.provider_symbol)
        if item.key == fail:
            raise RuntimeError('sensitive SDK text must not reach the audit')
        return snapshot(item)
    monkeypatch.setattr(m, 'fetch_snapshot', fetch)
    def official(identity, name, target, start, raw_path):
        item = next(i for i in items if i.provider == 'csindex' and i.provider_symbol == identity.provider_symbol)
        bars = snapshot(item)['bars']
        payload = {'code':'200', 'success':True, 'data':[
            {'tradeDate':b['source_trade_date'].replace('-',''), 'indexCode':item.code, 'indexNameCn':item.name,
             **{k:b[k] for k in ('open','high','low','close')}} for b in bars]}
        raw_path.write_text(json.dumps(payload))
        return csindex_source.from_payload(identity, name, payload, target, start, datetime(2026,10,27,9,tzinfo=timezone.utc))
    monkeypatch.setattr(csindex_source, 'fetch_snapshot', official)
    return m.build(DATE, online=True, root=root)


def test_one_failure_isolated_and_immutable_receipt_survives_pointer_change(root, monkeypatch):
    first = acquire_fixture(root, monkeypatch, fail='csi500')
    assert first['module']['available_count'] == 8
    assert first['rows'][1]['reason'] == 'FETCH_FAILED'
    assert 'sensitive' not in Path(first['input_receipt']['longbridge']['snapshot_manifest']).read_text()
    second = acquire_fixture(root, monkeypatch)
    assert second['module']['available_count'] == 9
    assert first['input_receipt'] != second['input_receipt']
    m.validate_document(first, DATE, root)
    m.validate_document(second, DATE, root)
    monkeypatch.setattr(m, 'quote_context', lambda *_a: pytest.fail('offline network'))
    assert m.build(DATE, root=root)['rows'] == second['rows']


def test_tampered_dates_prices_and_config_rejected(root, monkeypatch):
    document = acquire_fixture(root, monkeypatch)
    changed = deepcopy(document)
    changed['rows'][0]['return_20d'] = 0
    with pytest.raises(ValueError, match='differ'):
        m.validate_document(changed, DATE, root)
    with pytest.raises(ValueError, match='date/registry'):
        m.validate_document(document, '20261026', root)
    raw = Path(document['input_receipt']['longbridge']['snapshot_manifest']).parent / 'csi300.json'
    original = raw.read_bytes()
    changed = json.loads(original)
    changed['fetched_at'] = '2026-10-27T03:00:00+00:00'
    raw.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match='raw file hash'):
        m.validate_document(document, DATE, root)
    raw.write_bytes(original)
    config = root / 'config/core_indices.yaml'
    value = yaml.safe_load(config.read_text())
    value['indices'][0]['name'] = '配置已变化'
    config.write_text(yaml.safe_dump(value))
    with pytest.raises(ValueError, match='date/registry'):
        m.validate_document(document, DATE, root)


def test_unknown_static_identity_blocks_price_request(root, monkeypatch):
    from src.market_facts import csindex_source
    def no_official(*a, **kw):
        raise ValueError('mock unavailable')
    monkeypatch.setattr(csindex_source, 'fetch_snapshot', no_official)
    monkeypatch.setattr(m, 'quote_context', lambda *_a: SimpleNamespace(static_info=lambda _: []))
    monkeypatch.setattr(m, 'fetch_snapshot', lambda *_a, **_kw: pytest.fail('unverified symbol fetched'))
    document = m.build(DATE, online=True, root=root)
    assert document['rows'][0]['reason'] == 'IDENTITY_UNVERIFIED'
    assert document['module']['available_count'] == 0


def test_non_session_request_is_rejected_before_network(root, monkeypatch):
    monkeypatch.setattr(m, 'quote_context', lambda *_a: pytest.fail('network'))
    with pytest.raises(ValueError, match='complete calendar session'):
        m.build('20261025', online=True, root=root)


def test_falling_indices_remain_visible_in_fixed_order():
    s = snapshot()
    for i, bar in enumerate(s['bars']):
        bar.update(open=2000-i, high=2002-i, low=1998-i, close=2001-i)
    item = m.definitions(ROOT)[0][0]
    s = build_snapshot(m.identity(item), s['bars'], pd.Timestamp(DATE).date(), datetime(2026,10,27,9,tzinfo=timezone.utc))
    row = observe(s)
    assert row['trend_state'] == 'FALLING' and row['return_20d'] < 0
    html = panels({'rows': [row]})
    assert '沪深300' in html and '均线下行' in html
    assert '暂无符合' not in html and 'OUT_OF_SCOPE' not in html


def test_csindex_raw_binding_and_provider_failure_isolation(root, monkeypatch):
    from src.market_facts import csindex_source
    document = acquire_fixture(root, monkeypatch)
    m.validate_document(document, DATE, root)
    row = document['rows'][-1]
    assert row['provider'] == 'csindex' and row['status'] == 'AVAILABLE'
    raw = Path(document['input_receipt']['csindex']['snapshot_manifest']).parent/'dividend_low_vol.http.json'
    raw.write_text('{}')
    with pytest.raises(ValueError, match='raw response hash'):
        m.validate_document(document, DATE, root)
    def fail(*_a, **_kw):
        raise ValueError('official endpoint failed')
    monkeypatch.setattr(csindex_source, 'fetch_snapshot', fail)
    failed = m.build(DATE, online=True, root=root)
    assert failed['module']['available_count'] == 7
    assert failed['rows'][-1]['reason'] == 'FETCH_FAILED'
    assert all(r['status'] == 'AVAILABLE' for r in failed['rows'][:5])
