from datetime import date
from types import SimpleNamespace
import logging

import pandas as pd
import pytest

from src.common.source_session import SourceSession
from src.trend_engine import engine, fetch_data
from src.trend_engine.asset import Asset
from src.trend_engine.data_provider import AKShareProvider


def bars(dates):
    return pd.DataFrame({'date': pd.to_datetime(dates), 'open': 10., 'high': 11.,
                         'low': 9., 'close': 10., 'volume': 100.})


def test_source_open_is_session_scoped_and_empty_does_not_trip():
    session = SourceSession()
    for _ in range(6):
        session.record('sina', False, empty=True)
    assert session.available('sina')
    for _ in range(3):
        session.record('em', False)
    assert not session.available('em')
    session.record('em', True)
    assert not session.available('em')
    assert SourceSession().available('em')


def test_failed_source_not_retried_for_remaining_stocks(tmp_path, monkeypatch):
    monkeypatch.setattr(engine.time, 'sleep', lambda *a: None)
    calls = []
    class Provider:
        def get_daily(self, asset, source=None, **kwargs):
            calls.append((asset.symbol, source))
            if source == 'em':
                raise ConnectionError('Connection aborted')
            return bars(['2026-10-12'])
    session = SourceSession()
    for i in range(7):
        engine.load_or_fetch_daily(Asset(str(i), 'x', 'CN'), Provider(), tmp_path,
                                   logging.getLogger('test'), session=session)
    assert sum(src == 'em' for _, src in calls) == 3
    assert sum(src == 'sina' for _, src in calls) == 7


def test_provider_exception_does_not_invoke_same_request_twice():
    calls = []
    def source(symbol):
        calls.append(symbol)
        raise ConnectionError('broken')
    with pytest.raises(ConnectionError):
        AKShareProvider._call_ak(source, symbol='002050')
    assert calls == ['002050']


def test_target_cache_hit_and_future_bar_does_not_mask_gap(tmp_path):
    path = tmp_path / 'CN_002050.csv'
    asset = Asset('002050', '三花', 'CN')
    def mode():
        return engine.decide_refresh_mode(asset, 'daily', pd.DataFrame(columns=engine.ASSET_STATE_COLUMNS),
                                          path, date(2026, 10, 13), False, False, False,
                                          target_date=date(2026, 10, 12))
    bars(['2026-10-09', '2026-10-12', '2026-10-13']).to_csv(path, index=False)
    assert mode()[0] == 'cache'
    bars(['2026-10-09', '2026-10-13']).to_csv(path, index=False)
    assert mode() == ('online_incremental', '20261010', '20261012')


@pytest.mark.parametrize('failed', [False, True])
def test_cross_theme_fetch_once_preserves_registration_rows(tmp_path, monkeypatch, failed):
    monkeypatch.setattr(engine, 'common_raw_dir', lambda: tmp_path / 'raw')
    monkeypatch.setattr(engine, 'common_processed_dir', lambda: tmp_path / 'processed')
    monkeypatch.setattr(engine, 'asset_state_path', lambda: tmp_path / 'state.csv')
    monkeypatch.setattr(engine.time, 'sleep', lambda *a: None)
    monkeypatch.setattr(fetch_data, 'load_or_fetch_hs300', lambda **kw: pd.DataFrame())
    calls = []
    def fetch(asset, provider, **kwargs):
        calls.append(asset.symbol)
        if failed:
            raise RuntimeError('fetch failed and no cache')
        return bars(['2026-10-12']), 'online'
    monkeypatch.setattr(engine, 'load_or_fetch_daily', fetch)
    monkeypatch.setattr(engine, 'analyze_asset', lambda **kw: {
        'symbol': kw['asset'].symbol, 'market': 'CN', 'score_trend': 50, 'score': 50,
        'note': kw['note'], 'data_source': 'online', 'watch_level': 'B',
        'relative_strength_20d': 0.0, 'name': '三花'})
    items = [SimpleNamespace(asset=Asset('002050', '三花', 'CN', category='global_ev_components'),
                             note='tradeable', update_policy='daily'),
             SimpleNamespace(asset=Asset('002050', '三花', 'CN', category='auto_thermal_ai_cooling'),
                             note='monitor_only', update_policy='daily')]
    result = engine.compute_trends(items, as_of_date='20261012')
    assert calls == ['002050']
    assert len(result) == 2
    assert set(result.note) == {'tradeable', 'monitor_only'}
    assert result.attrs['source_audit']['unique_assets'] == 1
    if failed:
        state = engine.load_asset_state(tmp_path / 'state.csv')
        assert state.iloc[0]['fail_count'] == '1'


def test_benchmark_cache_requires_target_bar(tmp_path, monkeypatch):
    path = tmp_path / '_benchmark_sh000300.csv'
    bars(['2026-09-24']).to_csv(path, index=False)
    calls = []
    monkeypatch.setattr(fetch_data, 'fetch_hs300_daily', lambda cfg: calls.append(cfg.end_date) or bars(['2026-10-12']))
    result = fetch_data.load_or_fetch_hs300(tmp_path, fetch_data.FetchConfig(end_date='20261012'),
                                          logging.getLogger('test'), target_date='20261012')
    assert calls == ['20261012']
    assert result.date.max() == pd.Timestamp('2026-10-12')


def test_same_final_prices_keep_scores_identical_online_and_offline(tmp_path, monkeypatch):
    raw = tmp_path / 'raw'
    raw.mkdir()
    monkeypatch.setattr(engine, 'common_raw_dir', lambda: raw)
    monkeypatch.setattr(engine, 'common_processed_dir', lambda: tmp_path / 'processed')
    monkeypatch.setattr(engine, 'asset_state_path', lambda: tmp_path / 'state.csv')
    monkeypatch.setattr(engine.time, 'sleep', lambda *a: None)
    frame = bars(pd.bdate_range(end='2026-10-12', periods=90))
    frame['close'] = range(10, 100)
    frame['open'] = frame['close']
    frame['high'] = frame['close'] + 1
    frame['low'] = frame['close'] - 1
    monkeypatch.setattr(fetch_data, 'load_or_fetch_hs300', lambda **kw: frame.copy())
    monkeypatch.setattr(AKShareProvider, 'get_daily', lambda *a, **kw: frame.copy())
    item = SimpleNamespace(asset=Asset('002050', '三花', 'CN', category='global_ev_components'),
                           note='tradeable', update_policy='daily')
    online = engine.compute_trends([item], as_of_date='20261012')
    offline = engine.compute_trends([item], as_of_date='20261012', offline=True)
    columns = ['close', 'score_trend', 'watch_level', 'relative_strength_20d', 'action', 'technical_diagnostics']
    pd.testing.assert_frame_equal(online[columns], offline[columns])
