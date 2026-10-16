"""Date evidence belongs to the source, never to the requested partition."""
from datetime import datetime
import argparse

import pandas as pd
import pytest

from src.sw_industry_rps import cli, storage, metrics
from src.sw_industry_rps.source_status import verified_price_dates
from tests.sw_industry_rps.test_ths_mapping import _rt_df, _sw_raw_df


@pytest.mark.parametrize('target,now', [
    ('20260731', datetime(2026, 8, 3, 10)),
    ('20260803', datetime(2026, 8, 3, 16)),
    ('20260730', datetime(2026, 8, 3, 10)),
])
def test_undated_realtime_never_becomes_target_price(tmp_path, target, now):
    storage.save_industry_raw(_sw_raw_df(), tmp_path, '801081.SI')
    frame = _rt_df().drop(columns=['prev_close_trade_date', 'close_trade_date'])
    assert cli._build_realtime_provisional(frame, tmp_path, ['801081.SI'], target, now).empty
    # Reading the fallback does not change the old cache.
    assert storage.load_industry_raw(tmp_path, '801081.SI').trade_date.max() == pd.Timestamp('2026-07-30')


def test_prev_close_needs_its_own_matching_date(tmp_path):
    storage.save_industry_raw(_sw_raw_df(), tmp_path, '801081.SI')
    frame = _rt_df()
    frame['prev_close_trade_date'] = '2026-07-30'
    frame['close_trade_date'] = '2026-07-31'
    assert cli._build_realtime_provisional(frame, tmp_path, ['801081.SI'], '20260731',
                                         datetime(2026, 8, 3, 10)).empty


@pytest.mark.parametrize('source,evidence,expected', [
    ('swsresearch', None, True),
    ('swsresearch_analysis', None, True),
    ('hist_sw', None, True),
    ('realtime', None, False),
    ('swsresearch_realtime', None, False),
    ('unknown', None, False),
    ('realtime', '2026-10-15', True),
    ('realtime', '2026-10-14', False),
    ('swsresearch', '2026-10-14', False),
    ('swsresearch', 'invalid', False),
])
def test_price_date_evidence(source, evidence, expected):
    frame = pd.DataFrame({'trade_date': ['2026-10-15'], 'source': [source]})
    if evidence is not None:
        frame['source_trade_date'] = evidence
    assert bool(verified_price_dates(frame).iloc[0]) is expected


@pytest.mark.parametrize('hist_target_ready', [True, False])
def test_update_revalidates_legacy_target_and_checkpoint(tmp_path, monkeypatch, hist_target_ready):
    raw = tmp_path / 'raw'
    code = '801081.SI'
    storage.save_master(pd.DataFrame({'industry_code': [code], 'industry_name': ['半导体']}), raw)
    storage.save_active_snapshot([code], raw)
    cached = pd.DataFrame({'trade_date': pd.to_datetime(['2026-10-14', '2026-10-15']),
                           'close': [100., 110.], 'source': ['swsresearch', 'realtime']})
    storage.save_industry_raw(cached, raw, code)
    storage.save_checkpoint(raw, {'target_date': '20261015', 'completed_codes': [code], 'failed_codes': {}})
    monkeypatch.setattr(cli, 'sw_industry_raw_dir', lambda: raw)
    monkeypatch.setattr(cli, '_probe_analysis_daily', lambda d: ('', None, 0))
    monkeypatch.setattr(cli, 'load_config', lambda: {})
    monkeypatch.setattr(cli.time, 'sleep', lambda _: None)
    monkeypatch.setattr(cli.data_source, 'fetch_industry_realtime',
                        lambda **kw: _rt_df().drop(columns=['prev_close_trade_date', 'close_trade_date']))
    calls = []
    def history(c, start_date, end_date):
        calls.append((c, start_date, end_date))
        return pd.DataFrame({'trade_date': pd.to_datetime(['2026-10-15' if hist_target_ready else '2026-10-14']),
                             'close': [105.], 'source': ['swsresearch']})
    monkeypatch.setattr(cli.data_source, 'fetch_industry_hist', history)
    result = cli.cmd_update(argparse.Namespace(log_level='INFO', target_date='20261015'))
    assert calls == [(code, '20261015', '20261015')]
    assert result.target_ready is hist_target_ready
    assert result.raw_covered == int(hist_target_ready)
    assert result.source_latest_common_date == ("20261015" if hist_target_ready else "20261014")
    assert storage.load_update_status(raw)["latest_available_date"] == ("20261015" if hist_target_ready else "20261014")
    assert cli.validate_raw_completeness(raw, '20261015', [code])[0] == int(hist_target_ready)
    if hist_target_ready:
        row = storage.load_industry_raw(raw, code).iloc[-1]
        assert row.close == 105.
        assert row.source == 'swsresearch'


def test_dated_primary_metrics_parity():
    # Adding audit evidence must leave every existing fact numerically unchanged.
    frame = pd.DataFrame([
        {'trade_date': day, 'industry_code': code, 'close': 100 + n * slope, 'source': 'swsresearch'}
        for code, slope in [('A', 1), ('B', 2)]
        for n, day in enumerate(pd.bdate_range('2026-07-01', periods=30))
    ])
    before = metrics.compute_all_metrics(frame)
    frame['source_trade_date'] = frame.trade_date
    after = metrics.compute_all_metrics(frame)
    pd.testing.assert_frame_equal(before, after[before.columns])
