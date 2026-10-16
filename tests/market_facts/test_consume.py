from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from src.market_facts.adapters import snapshot_from_frame, snapshot_from_cache
from src.market_facts.calendar import SessionCalendar
from src.market_facts.consume import observe_snapshot, snapshot_frame
from src.market_facts.contract import validate_snapshot
from src.market_facts.longbridge_source import identity_for


def fixture():
    dates = pd.bdate_range('2024-01-01', periods=450)
    values = np.linspace(100, 30, 450) + np.sin(np.arange(450))
    frame = pd.DataFrame({'date':dates, 'open':values, 'high':values+1, 'low':values-1,
                          'close':values, 'volume':1000.})
    days = tuple(d.date() for d in dates)
    c = SessionCalendar('XSHG',days[0],days[-1],days,
                        {d.isoformat():f'{d.isoformat()}T07:00:00+00:00' for d in days},
                        'synthetic-test-only','test_fixture','fixture-digest')
    i = identity_for(SimpleNamespace(market='CN',exchange='SSE',symbol='600900',currency='CNY'))
    s = snapshot_from_frame(frame,i,days[-1],datetime(2026,10,16,tzinfo=timezone.utc))
    return frame,c,s


def test_offline_consumption_and_existing_engine_parity(tmp_path):
    from src.trend_engine.engine import analyze_asset
    from src.trend_engine.asset import Asset
    frame,c,s=fixture()
    r=observe_snapshot(s,c)
    direct=analyze_asset(Asset('600900','sample','CN'),'', 'test',snapshot_frame(s),pd.DataFrame(),
                         tmp_path,{},False,date.fromisoformat(s['target_trade_date']),persist_processed=False)
    assert r['trend']['trend_score']==direct['score_trend']
    assert r['position']['reliability']=='OK'
    assert r['position']['price_pos_360'] is not None
    assert not r['production_eligible']
    assert 'action' not in r['trend'] and 'watch_level' not in r['trend']
    assert not list(tmp_path.iterdir())


def test_missing_session_blocks_trend_and_position():
    frame,c,s=fixture()
    s=snapshot_from_frame(frame.drop(index=420),identity_for(SimpleNamespace(
        market='CN',exchange='SSE',symbol='600900',currency='CNY')),
        date.fromisoformat(s['target_trade_date']),datetime(2026,10,16,tzinfo=timezone.utc))
    r=observe_snapshot(s,c)
    assert r['trend']['trend_score'] is None
    assert r['position']['price_pos_360'] is None
    assert r['position']['bottom_change']=='UNKNOWN'
    assert r['calendar']['windows']['120']=='DATA_GAP'


def test_hash_tampering_and_calendar_exchange_mismatch():
    _,c,s=fixture()
    bad=deepcopy(s);bad['bars'][-1]['close']+=0.01
    with pytest.raises(ValueError,match='hash mismatch'):
        observe_snapshot(bad,c)
    with pytest.raises(ValueError,match='exchange mismatch'):
        observe_snapshot(s,replace(c,exchange='XHKG'))


def test_calendar_coverage_not_replaced_by_observed_rows():
    _,c,s=fixture()
    r=observe_snapshot(s,replace(c,start=c.days[-200],days=c.days[-200:]))
    assert r['position']['window_reason_360']=='CALENDAR_UNCOVERED'
    assert r['position']['long_term_bottom'] is None
    assert observe_snapshot(s,None)['trend']['trend_score'] is None


def test_incomplete_session_and_non_session_target():
    _,c,s=fixture()
    bad=deepcopy(s);bad['fetched_at']=s['target_trade_date']+'T01:00:00+00:00'
    assert observe_snapshot(bad,c)['trend']['trend_score'] is None
    c=replace(c,days=c.days[:-1])
    assert observe_snapshot(s,c)['trend']['reason']=='TARGET_UNVERIFIED'


def test_cache_evidence_missing_turnover_and_unknown_provider(tmp_path):
    frame,c,s=fixture()
    path=tmp_path/'raw.csv';frame.to_csv(path,index=False)
    identity=replace(identity_for(SimpleNamespace(market='CN',exchange='SSE',symbol='600900',currency='CNY')),
                     provider='unknown',price_basis='unknown')
    cache=snapshot_from_cache(path,identity,c.days[-1],datetime.now(timezone.utc))
    assert cache['bars'][-1]['turnover'] is None
    assert cache['bars'][-1]['date_evidence']=='legacy_date_unverified'
    assert cache['provenance']['original_fetched_at']=='unknown'
    assert not cache['provenance']['provider_history_verified']
    assert validate_snapshot(cache)['snapshot_id']==cache['snapshot_id']


def test_no_lookahead_in_benchmark():
    frame,c,s=fixture()
    bench=frame[['date','close']].copy()
    x=observe_snapshot(s,c,bench)
    future=pd.DataFrame({'date':[pd.Timestamp(c.days[-1])+pd.Timedelta(days=1)],'close':[99999.]})
    assert x==observe_snapshot(s,c,pd.concat([bench,future],ignore_index=True))
    assert observe_snapshot(s,c,bench.iloc[:-1])['trend']['benchmark_status']=='UNAVAILABLE'


def test_close_only_projection_can_supply_position_but_not_ohlcv_trend():
    frame,c,s=fixture()
    identity=identity_for(SimpleNamespace(market='CN',exchange='SSE',symbol='600900',currency='CNY'))
    close_only=snapshot_from_frame(frame[['date','close']],identity,c.days[-1],
                                  datetime.now(timezone.utc),close_only=True)
    r=observe_snapshot(close_only,c)
    assert r['position']['reliability']=='OK'
    assert r['trend']['reason']=='OHLCV_MISSING'
    assert r['trend']['trend_score'] is None
    assert close_only['provenance']['excluded_fields']==['open','high','low']
    assert not r['source_dates_verified']


def test_unknown_units_and_canonical_hk_identity():
    i=identity_for(SimpleNamespace(market='HK',symbol='2498',currency='HKD'))
    assert i.security_id=='HKEX:02498'
    assert i.volume_unit=='unknown'


def test_sw_source_dates_do_not_promote_undated_realtime(tmp_path):
    frame,c,_=fixture()
    identity=replace(identity_for(SimpleNamespace(market='CN',exchange='SSE',symbol='600900',currency='CNY')),
                     asset_type='industry_index',classification_version='SW2021:test')
    f=frame.tail(2).rename(columns={'date':'trade_date'})
    f['source']=['sw_daily','sw_realtime']
    # The primary-source allowlist comes from the existing SW date contract.
    from src.sw_industry_rps.source_status import PRIMARY_SOURCES
    f.loc[f.index[0],'source']=next(iter(PRIMARY_SOURCES))
    path=tmp_path/'industry.csv';f.to_csv(path,index=False)
    s=snapshot_from_cache(path,identity,c.days[-1],datetime.now(timezone.utc),
                          date_column='trade_date',close_only=True)
    assert s['bars'][0]['date_evidence']=='source_dated_daily'
    assert s['bars'][1]['date_evidence']=='legacy_date_unverified'
    assert not s['quality']['source_dates_verified']
    assert s['bars'][1]['legacy_source']=='sw_realtime'


@pytest.mark.parametrize('provider,endpoint,symbol',[('em','stock_zh_a_hist','600900'),
                                                   ('sina','stock_zh_a_daily','sh600900')])
def test_explicit_akshare_source_adapter(provider,endpoint,symbol):
    from src.market_facts.adapters import snapshot_from_akshare_frame
    frame,c,_=fixture()
    asset=SimpleNamespace(market='CN',exchange='SSE',symbol='600900',currency='CNY')
    s=snapshot_from_akshare_frame(frame,asset,provider,'qfq',c.days[-1],datetime.now(timezone.utc))
    assert s['identity']['provider']==provider
    assert s['identity']['adapter']=='akshare'
    assert s['identity']['endpoint']==endpoint
    assert s['identity']['provider_symbol']==symbol
    assert s['quality']['target_source_date_verified']
    assert not s['quality']['price_basis_verified']
