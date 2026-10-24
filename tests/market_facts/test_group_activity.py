from copy import deepcopy
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from src.market_facts import group_activity as ga
from src.market_facts.contract import build_snapshot
from src.market_facts.longbridge_source import identity_for
from src.trend_engine.asset import Asset


def panel(last, prior=None):
    keys = [('CN', f'{i:06d}') for i in range(len(last))]
    values = np.tile(np.where(np.arange(61) % 2, -.003, .003)[:, None], (1, len(last)))
    values[-1] = last
    if prior is not None:
        values[-2] = prior
    return pd.DataFrame(values, index=pd.bdate_range(end='2026-10-22', periods=61), columns=pd.MultiIndex.from_tuples(keys))


@pytest.mark.parametrize('sign,event', [(1, 'GROUP_UP'), (-1, 'GROUP_DOWN')])
def test_broad_move_both_directions_and_no_lookahead(sign, event):
    data = panel([sign*.04]*5)
    result = ga.describe_returns(data)
    assert event in result['events']
    assert result['baseline_count'] == 60
    assert result['baseline_end'] == data.index[-2].date().isoformat()
    assert result['baseline_mean'] == pytest.approx(0)
    assert result['baseline_std'] < .004
    assert result['group_threshold'] == .02


def test_one_stock_does_not_become_whole_theme_and_flat_is_not_up():
    result = ga.describe_returns(panel([.15, 0, 0, 0, 0], prior=[0]*5))
    assert result['events'] == ['MEMBER_MOVE']
    assert result['positive_count'] == 1
    assert result['median_return_1d'] == 0
    assert result['movers'][0]['symbol'] == '000000'
    assert ga.describe_returns(panel([0]*5, prior=[0]*5))['events'] == []


@pytest.mark.parametrize('last,prior,event', [([.001]*5, [-.001]*5, 'BREADTH_UP'),
                                           ([-.001]*5, [.001]*5, 'BREADTH_DOWN')])
def test_breadth_detects_count_change_without_large_price_move(last, prior, event):
    data = panel(last, prior)
    data.iloc[:-1] = prior
    result = ga.describe_returns(data)
    assert result['events'] == [event]
    assert abs(result['breadth_delta']) == 1


def test_routine_breadth_flips_do_not_trigger_attention():
    # All members flip signs every day: a large raw count change is routine here.
    result = ga.describe_returns(panel([.001]*5, prior=[-.001]*5))
    assert 'BREADTH_UP' not in result['events']


def test_small_group_and_wide_historical_volatility_do_not_manufacture_group_move():
    assert 'GROUP_UP' not in ga.describe_returns(panel([.04]*2))['events']
    data = panel([.04]*5)
    data.iloc[:-1] *= 20
    assert 'GROUP_UP' not in ga.describe_returns(data)['events']


@pytest.fixture
def input_data():
    calendars = {m: ga.SessionCalendar.load(ga.project_root()/f'config/market_calendars/{name}.json')
                 for m, name in [('CN', 'XSHG'), ('HK', 'XHKG')]}
    target = pd.Timestamp('2026-10-22')
    members = [dict(market='CN', symbol=f'60000{i}', name=f'股票{i}', group='test') for i in range(3)]
    groups = [dict(key='test', label='测试分组', role='mainline', members=members)]
    snapshots = {}
    for member in members:
        days = [d for d in calendars['CN'].days if d <= target.date()][-100:]
        values = 20 * np.cumprod(1 + np.where(np.arange(len(days)) % 2, -.003, .003))
        values[-1] = values[-2] * 1.04
        bars = [dict(source_trade_date=d.isoformat(), date_evidence='source_dated_daily',
                     open=float(v), high=float(v+1), low=float(v-1), close=float(v), volume=1000.)
                for d, v in zip(days, values)]
        ident = identity_for(Asset(member['symbol'], member['name'], 'CN', exchange='SSE', currency='CNY'))
        snapshots[('CN', member['symbol'])] = build_snapshot(ident, bars, target.date(), datetime(2026,10,23,tzinfo=timezone.utc))
    from dataclasses import replace
    ident = replace(identity_for(Asset('000300', '沪深300', 'CN', exchange='SSE', currency='CNY')),
                    asset_type='index', price_basis='index_level')
    snapshots[('CN', '_benchmark')] = build_snapshot(ident,
        [dict(b, open=100., high=101., low=99., close=100.) for b in bars], target.date(), datetime(2026,10,23,tzinfo=timezone.utc))
    return target, snapshots, calendars, groups


def rebuild(snapshot, bars=None, fetched_at=None, target=None):
    from src.market_facts.contract import SeriesIdentity
    return build_snapshot(SeriesIdentity(**snapshot['identity']), bars if bars is not None else snapshot['bars'],
                          pd.Timestamp(target or snapshot['target_trade_date']).date(),
                          fetched_at or datetime.fromisoformat(snapshot['fetched_at']))


def test_snapshot_calculation_and_missing_history_fail_closed(input_data):
    target, snapshots, calendars, groups = input_data
    result = ga.calculate(target, snapshots, calendars, groups)['groups'][0]
    assert result['status'] == 'WATCH'
    assert 'GROUP_UP' in result['events']
    for bars, reason in [(snapshots[('CN','600000')]['bars'][-30:], 'INSUFFICIENT_HISTORY'),
                         (snapshots[('CN','600000')]['bars'][:-3]+snapshots[('CN','600000')]['bars'][-2:], 'DATA_GAP_OR_INSUFFICIENT_HISTORY')]:
        changed = dict(snapshots)
        changed[('CN','600000')] = rebuild(changed[('CN','600000')], bars=bars)
        row = ga.calculate(target, changed, calendars, groups)['groups'][0]
        assert row['status'] == 'UNAVAILABLE'
        assert row['events'] == []
        assert row['reason'] == reason or reason in str(row.get('issues')) or row['reason'] in ('INSUFFICIENT_NATIVE_HISTORY','INSUFFICIENT_STRUCTURAL_HISTORY')


@pytest.mark.parametrize('kind', ['intraday', 'stale', 'source', 'tamper', 'missing', 'identity'])
def test_unverified_input_cannot_emit_an_event(input_data, kind):
    target, snapshots, calendars, groups = input_data
    snapshots = deepcopy(snapshots)
    key = ('CN','600000')
    rejected = {}
    if kind == 'intraday':
        snapshots[key] = rebuild(snapshots[key], fetched_at=datetime(2026,10,22,1,tzinfo=timezone.utc))
    elif kind == 'stale':
        snapshots[key] = rebuild(snapshots[key], bars=snapshots[key]['bars'][:-1], target='2026-10-21')
    elif kind == 'source':
        snapshots[key]['identity']['provider'] = 'other'
        snapshots[key] = rebuild(snapshots[key])
    elif kind == 'tamper':
        snapshots[key]['bars'][-1]['close'] = 1
    elif kind == 'identity':
        rejected[key] = 'IDENTITY_MISMATCH'
    else:
        snapshots.pop(key)
    row = ga.calculate(target, snapshots, calendars, groups, rejected)['groups'][0]
    assert row['status'] == 'UNAVAILABLE'
    assert row['events'] == []


def test_hk_closed_is_not_a_flat_or_fresh_movement(input_data):
    _, snapshots, calendars, groups = input_data
    s = next(iter(snapshots.values()))
    identity = identity_for(Asset('00883', '中国海洋石油', 'HK', currency='HKD'))
    days = [d for d in calendars['HK'].days if d <= pd.Timestamp('2026-10-19').date()][-100:]
    bars = [dict(s['bars'][0], source_trade_date=d.isoformat()) for d in days]
    hk = build_snapshot(identity, bars, pd.Timestamp('2026-10-19').date(), datetime(2026,10,20,tzinfo=timezone.utc))
    groups[0]['members'] = [dict(market='HK',symbol='00883',name='中国海洋石油')]
    row = ga.calculate('20261019', {('HK','00883'):hk}, calendars, groups)['groups'][0]
    assert row['status'] == 'UNAVAILABLE' and row['reason'] == 'MARKET_CLOSED'
    assert row['events'] == []


def test_policy_exclusion_and_cross_group_membership(input_data):
    target, snapshots, calendars, groups = input_data
    groups[0]['members'].append(dict(market='HK',symbol='02525',name='禾赛'))
    groups.append(dict(groups[0], key='second'))
    rows = ga.calculate(target, snapshots, calendars, groups)['groups']
    assert all(r['status'] == 'WATCH' and r['total_count'] == 3 and r['excluded_count'] == 1 for r in rows)
    assert rows[0]['events'] == rows[1]['events']


def test_daily_reader_rejects_a_different_trend_snapshot(tmp_path, monkeypatch):
    from tests.market_facts.test_stock_daily import setup_root
    from src.market_facts import stock_daily
    items, _ = setup_root(tmp_path, monkeypatch)
    stocks = stock_daily.build_longbridge_stocks('20261015', online=True, items=items)
    path = tmp_path/'outputs/stock_metrics'
    stocks.loc[stocks.symbol.eq('600900'), 'snapshot_id'] = 'different'
    with pytest.raises(ValueError, match='同一长桥快照'):
        ga.build_from_daily('20261015', stocks, path)


def test_publication_document_gates(input_data, monkeypatch):
    target, snapshots, calendars, groups = input_data
    monkeypatch.setattr(ga, 'definitions', lambda: groups)
    document = ga.calculate(target, snapshots, calendars, groups)
    stocks = pd.DataFrame([dict(market=m,symbol=s,snapshot_id=x['snapshot_id']) for (m,s),x in snapshots.items()])
    ga.validate_document(document, '20261022', stocks)
    with pytest.raises(ValueError, match='日期'):
        ga.validate_document(document, '20261023', stocks)
    document['groups'][0]['snapshot_ids']['CN:600000'] = 'wrong'
    with pytest.raises(ValueError, match='快照'):
        ga.validate_document(document, '20261022', stocks)


def synthetic_history(returns, benchmark_returns=None):
    from src.research.baskets.nav import align_assets, nav_from_panel
    values = np.asarray(returns)
    dates = pd.bdate_range('2025-01-02', periods=len(values))
    keys = [('CN', f'60000{i}') for i in range(values.shape[1])]
    prices = {k:pd.DataFrame(dict(date=dates, close=100*np.cumprod(1+values[:,j]))) for j,k in enumerate(keys)}
    bench = np.zeros(len(values)) if benchmark_returns is None else benchmark_returns
    panel = align_assets(prices, [dict(market=m,symbol=s,group='g') for m,s in keys],
                         pd.DataFrame(dict(date=dates,close=100*np.cumprod(1+bench))))
    nav = nav_from_panel(panel).nav
    features = ga.trend_features(nav,panel)
    native = pd.DataFrame(values, index=dates, columns=pd.MultiIndex.from_tuples(keys))
    return ga.reconstruct_states(features,native), features,native


def test_flat_then_collective_jump_is_sudden_watch():
    values = np.zeros((150,5)); values[-1] = .04
    history, _, _ = synthetic_history(values)
    assert history[-1]['status'] == 'WATCH'
    assert history[-1]['first_trigger'] is True and history[-1]['path'] == 'SUDDEN'
    assert history[-1]['conditions']['structure'] is True


def test_gradual_participation_detected_without_extreme_day():
    values = np.zeros((160,5))
    for j in range(5): values[140+j:,j] = .002
    history, _, _ = synthetic_history(values)
    hits = [r for r in history if r.get('first_trigger')]
    assert len(hits) == 1 and hits[0]['path'] == 'GRADUAL'
    assert hits[0]['evidence']['median_return_1d'] < .005
    assert any(r['status'] == 'EMERGING' for r in history)
    assert history[-1]['first_trigger'] is False


def test_long_rally_big_day_is_tracking_not_first_start():
    values = np.full((160,5), .004); values[-1] = .05
    history, _, _ = synthetic_history(values)
    assert history[-1]['status'] == 'CONFIRMED'
    assert not any(r.get('first_trigger') for r in history)


def test_downtrend_rebound_only_watch_and_failure_rearms_after_reset():
    values = np.full((170,5), -.003)
    values[140] = .04
    values[141] = -.06
    values[142:150] = 0
    values[150] = .04
    history, _, _ = synthetic_history(values)
    assert history[140]['status'] == 'WATCH'
    assert history[141]['status'] == 'NO_SETUP' and history[141]['transition'] == 'INVALIDATED'
    assert history[150]['first_trigger'] is True
    assert history[150]['episode_start'] != history[140]['episode_start']


def test_single_leader_never_automatically_upgrades_group():
    values = np.zeros((170,5)); values[140,0] = .5
    history, _, _ = synthetic_history(values)
    assert history[140]['events'] == ['MEMBER_MOVE']
    assert history[-1]['status'] == 'NO_SETUP'
    assert not any(r['status'] == 'EMERGING' for r in history)


def test_improving_rs_cannot_override_deteriorating_structure():
    values = np.full((170,5), -.003); values[140] = .04
    benchmark = np.full(170,-.01)
    history, _, _ = synthetic_history(values,benchmark)
    assert history[140]['status'] == 'WATCH'
    assert not any(r['status'] in ('EMERGING','CONFIRMED') for r in history[140:])


def test_replay_is_deterministic_and_prefix_invariant():
    values = np.zeros((180,5))
    for j in range(5): values[140+j:,j] = .002
    history,f,n = synthetic_history(values)
    assert history == ga.reconstruct_states(f,n)
    for end in (145,150,165):
        assert history[:end] == ga.reconstruct_states(f.iloc[:end],n.iloc[:end])


def test_missing_benchmark_and_direct_snapshot_identity_mismatch(input_data):
    target, snapshots, calendars, groups = input_data
    missing = dict(snapshots); missing.pop(('CN','_benchmark'))
    assert ga.calculate(target,missing,calendars,groups)['groups'][0]['status'] == 'UNAVAILABLE'
    swapped = dict(snapshots); swapped[('CN','600000')] = snapshots[('CN','600001')]
    r = ga.calculate(target,swapped,calendars,groups)['groups'][0]
    assert r['status'] == 'UNAVAILABLE' and r['issues'][0]['reason'] == 'IDENTITY_MISMATCH'


def test_compare_metrics_and_reset_path_are_identical():
    from src.research.baskets.nav import align_assets, nav_from_panel
    from src.research.baskets.metrics import rs_20d_pct, _participation_20d
    dates = pd.bdate_range('2025-01-01','2025-12-31')
    prices = {('CN','600000'):pd.DataFrame(dict(date=dates,close=100*np.exp(np.arange(len(dates))*.001))),
              ('CN','600001'):pd.DataFrame(dict(date=dates,close=100*np.exp(np.arange(len(dates))*.003)))}
    members = [dict(market=m,symbol=s,group=s) for m,s in prices]
    benchmark = pd.DataFrame(dict(date=dates,close=100+np.arange(len(dates))*.1))
    panel = align_assets(prices,members,benchmark)
    full = nav_from_panel(panel,'quarterly', dates, '2025-12-31').nav
    features = ga.trend_features(full,panel)
    assert features.iloc[-1].rs_20d_pct == pytest.approx(rs_20d_pct(full))
    assert features.iloc[-1].participation_20d == pytest.approx(_participation_20d(panel,len(dates)-1)[0])
    partial = align_assets(prices,members,benchmark[benchmark.date >= '2025-06-30'])
    reset = nav_from_panel(partial,'quarterly',dates,'2025-12-31').nav
    expected = full.loc['2025-06-30':,'basket']; expected = expected/expected.iloc[0]*100
    np.testing.assert_allclose(reset.basket,expected,rtol=1e-12)


def test_holiday_is_skipped_without_resetting_or_fabricating_return():
    values = np.zeros((170,5)); values[140:] = .004
    history,f,n = synthetic_history(values)
    closed = n.index[150]
    n = n.drop(closed)
    h = ga.reconstruct_states(f,n)
    assert h[150]['status'] == 'UNAVAILABLE' and not h[150]['first_trigger']
    assert h[151]['previous_observation_date'] == f.index[149].date().isoformat()
    assert sum(r.get('first_trigger',False) for r in h) == 1


def test_history_tamper_is_rejected(input_data,monkeypatch):
    target,snapshots,calendars,groups = input_data
    monkeypatch.setattr(ga,'definitions',lambda:groups)
    doc=ga.calculate(target,snapshots,calendars,groups)
    stocks=pd.DataFrame([dict(market=m,symbol=s,snapshot_id=x['snapshot_id']) for (m,s),x in snapshots.items()])
    doc['groups'][0]['status']='CONFIRMED'
    with pytest.raises(ValueError,match='历史依据'):
        ga.validate_document(doc,'20261022',stocks)


def test_downgrade_and_expiry_are_not_new_episodes():
    values=np.zeros((190,5)); values[140:160]=.004
    h,f,n=synthetic_history(values)
    assert h[159]['status']=='CONFIRMED'
    # Lose MA20 structure while preserving MA10 and entry price: downgrade, not stop.
    f.loc[f.index[160:],'ma20']=f.loc[f.index[160:],'nav']*1.01
    f.loc[f.index[160:],'ma20_change_3d']=-.001
    h=ga.reconstruct_states(f,n)
    assert h[161]['status']=='WATCH' and h[161]['transition']=='DOWNGRADE'
    assert h[161]['episode_start']==h[140]['episode_start']
    assert not h[161]['first_trigger']
    assert h[170]['status']=='WATCH'
    assert h[171]['status']=='NO_SETUP' and h[171]['transition']=='EXPIRED'


def test_price_uptrend_is_existing_even_if_benchmark_is_stronger():
    values=np.full((170,5),.004);values[-1]=.06
    h,_,_=synthetic_history(values,np.full(170,.006))
    assert h[-1]['conditions']['existing_price_trend']
    assert not any(r.get('first_trigger') for r in h)


def test_evaluation_outcomes_ignore_candidate_states_and_censor_tail():
    from scripts.validation.check_group_activity import price_events,evaluate_group
    values=np.zeros((185,5));values[140:]=.005
    h,f,n=synthetic_history(values)
    nav=f.nav
    events=price_events(pd.Series(nav.values,index=[d.date().isoformat() for d in nav.index]))
    assert events and events[0]['index'] >= 140
    row=dict(key='test',label='测试',history=h,structural_anchor='2025-01-02',
             nav_history={d.date().isoformat():float(v) for d,v in nav.items()})
    result=evaluate_group(row)
    assert result['captured_events'] >= 1
    assert result['mean_lead_sessions'] > 0
    short=dict(row,history=h[:145],nav_history=dict(list(row['nav_history'].items())[:145]))
    result=evaluate_group(short)
    assert result['censored_alerts']==1
    assert result['false_start_rate'] is None


def test_input_gap_on_hk_only_session_cannot_hide_in_alignment():
    calendars={m:ga.SessionCalendar.load(ga.project_root()/f'config/market_calendars/{name}.json')
               for m,name in [('CN','XSHG'),('HK','XHKG')]}
    days=[d for d in calendars['HK'].days if pd.Timestamp('2025-10-01').date()<=d<=pd.Timestamp('2026-10-22').date()]
    missing=next(d for d in days if d not in calendars['CN'].days)
    bars=[dict(source_trade_date=d.isoformat(),date_evidence='source_dated_daily',open=10.,high=11.,low=9.,close=10.,volume=100.) for d in days if d!=missing]
    ident=identity_for(Asset('00883','中国海洋石油','HK',currency='HKD'))
    snapshot=build_snapshot(ident,bars,pd.Timestamp('2026-10-22').date(),datetime(2026,10,23,tzinfo=timezone.utc))
    # Remove an interior HK-only session (not the unobservable first date).
    if missing==days[0]:
        bars.insert(0,dict(bars[0],source_trade_date=missing.isoformat()))
        missing=next(d for d in days[1:] if d not in calendars['CN'].days)
        snapshot=build_snapshot(ident,[b for b in bars if b['source_trade_date']!=missing.isoformat()],pd.Timestamp('2026-10-22').date(),datetime(2026,10,23,tzinfo=timezone.utc))
    with pytest.raises(ValueError,match='DATA_GAP'):
        ga._close(snapshot,calendars['HK'],pd.Timestamp('2026-10-22'),('HK','00883'))


def test_v2_fast_second_start_without_five_no_setup_days():
    values=np.zeros((175,5));values[140]=.04;values[141]=-.06;values[142]=.05
    h,_,_=synthetic_history(values)
    assert h[140]['first_trigger']
    assert h[141]['transition']=='INVALIDATED'
    assert h[142]['first_trigger'] and h[142]['episode_start']!=h[140]['episode_start']
    assert h[142]['conditions']['novel']


@pytest.mark.parametrize('channel',['rs','participation'])
def test_v2_gradual_watch_accepts_either_explanatory_channel(channel):
    values=np.zeros((165,5))
    for j in range(5):values[140+j:,j]=.002
    _,f,n=synthetic_history(values)
    if channel=='rs':
        f.loc[f.index[140:],'participation_10d_change_5d']=0
    else:
        f.loc[f.index[140:],'rs_change_5d']=-.01
        f.loc[f.index[140:],'rs_20d_pct']=-1
        f.loc[f.index[140:],'rs_20d_change_5d_pp']=-.5
    h=ga.reconstruct_states(f,n)
    first=next(q for q in h if q['first_trigger'])
    assert first['path']=='GRADUAL'
    assert first['evidence_channel']==('RS_IMPROVEMENT' if channel=='rs' else 'PARTICIPATION_EXPANSION')
    if channel=='participation':
        assert not any(q['status']=='EMERGING' for q in h)


def test_v2_leader_clue_survives_without_group_diffusion_and_is_deduplicated():
    values=np.zeros((170,5));values[140:143,0]=.08
    h,_,_=synthetic_history(values)
    assert h[140]['status']=='NO_SETUP' and not h[140]['first_trigger']
    assert h[140]['leader_clues'][0]['scope']=='MEMBER'
    assert h[140]['first_leader_clue']
    assert not h[141]['first_leader_clue']
    assert not any(q['status']=='EMERGING' for q in h)


def test_v2_confirmed_loses_participation_with_price_structure_intact():
    values=np.zeros((190,5));values[140:]=.004
    h,f,n=synthetic_history(values)
    assert h[159]['status']=='CONFIRMED'
    f.loc[f.index[160:],'participation_10d']=.2
    h=ga.reconstruct_states(f,n)
    assert h[161]['status']=='WATCH' and h[161]['transition']=='DOWNGRADE'
    assert h[161]['conditions']['structure']
    assert not h[161]['first_trigger']


def test_v2_confirmed_loses_twenty_day_support_but_remains_emerging():
    values=np.zeros((190,5));values[140:]=.004
    _,f,n=synthetic_history(values)
    f.loc[f.index[160:],'participation_20d']=.2
    h=ga.reconstruct_states(f,n)
    assert h[161]['status']=='EMERGING'
    assert h[161]['transition']=='DOWNGRADE'
    assert not h[161]['first_trigger']


def test_v2_tracking_is_an_event_origin_not_a_path_or_state():
    h,_,_=synthetic_history(np.full((170,5),.004))
    assert h[-1]['status']=='CONFIRMED'
    assert h[-1]['event_origin']=='EXISTING_TREND'
    assert h[-1]['path'] is None and not h[-1]['first_trigger']


def test_v2_expired_unreset_episode_does_not_alert_every_day():
    values=np.zeros((185,5));values[140:]=.002
    _,f,n=synthetic_history(values)
    # Preserve a qualifying RS-led setup without enough breadth for EMERGING.
    f.loc[f.index[140:],'participation_10d']=.2
    f.loc[f.index[140:],'participation_10d_change_5d']=0
    f.loc[f.index[140:],'rs_20d_change_5d_pp']=1
    f.loc[f.index[140:],'rs_rising_days']=4
    f.loc[f.index[140:],'return_20d']=.01
    h=ga.reconstruct_states(f,n)
    assert sum(q.get('first_trigger',False) for q in h)==1
    assert any(q.get('transition')=='DUPLICATE_SUPPRESSED' for q in h)


def test_v2_outcomes_separate_price_confirmation_lifecycle_and_censoring():
    from scripts.validation.check_group_activity import evaluate_group
    values=np.zeros((165,5));values[140]=.04;values[141:]=-.004
    h,f,n=synthetic_history(values)
    row=dict(key='x',label='x',structural_anchor='2025-01-02',history=h,
        nav_history={d.date().isoformat():float(v) for d,v in f.nav.items()})
    result=evaluate_group(row)
    first=result['signals'][0]
    assert first['price_outcome']=='UNCONFIRMED'
    assert first['lifecycle_outcome']=='INVALIDATED'
    assert result['unavailable_reasons']


def test_v1_frozen_control_has_old_cooldown_and_or_change_isolated():
    from scripts.validation.compare_group_trend_rules import frozen_v1
    old=frozen_v1()
    assert old.RULE_VERSION=='stock_group_trend_start_v1' and old.RULES['rearm_days']==5
    values=np.zeros((175,5));values[140]=.04;values[141]=-.06;values[142]=.05
    _,f,n=synthetic_history(values)
    h=old.reconstruct_states(f,n)
    assert h[140]['first_trigger'] and not h[142]['first_trigger']


def test_coverage_initial_pct_change_is_warmup_not_a_real_gap():
    values=np.zeros((100,5))
    _,f,n=synthetic_history(values)
    n.iloc[0]=np.nan
    h=ga.reconstruct_states(f,n)
    assert not any('NATIVE_HISTORY_GAP' in q.get('unavailable_reasons',[]) for q in h)
    assert h[60]['reason']=='INSUFFICIENT_NATIVE_HISTORY'


def test_compare_held_valuation_on_verified_holiday_is_not_a_native_return():
    from src.research.baskets.nav import align_assets,nav_from_panel
    calendars={m:ga.SessionCalendar.load(ga.project_root()/f'config/market_calendars/{name}.json')
               for m,name in [('CN','XSHG'),('HK','XHKG')]}
    dates={m:c.index()[(c.index()>='2026-01-01')&(c.index()<='2026-10-23')] for m,c in calendars.items()}
    prices={('CN','600000'):pd.DataFrame(dict(date=dates['CN'],close=100+np.arange(len(dates['CN'])))),
            ('HK','00883'):pd.DataFrame(dict(date=dates['HK'],close=100+np.arange(len(dates['HK']))))}
    members=[dict(market=m,symbol=s,group='g') for m,s in prices]
    panel=align_assets(prices,members,pd.DataFrame(dict(date=dates['CN'],close=100.)))
    closed=pd.Timestamp('2026-10-19')
    assert closed not in dates['HK'] and panel.has_price.loc[closed,('HK','00883')]
    nav=nav_from_panel(panel).nav
    f=ga.trend_features(nav,panel)
    native=pd.concat([p.set_index('date').close.pct_change(fill_method=None).rename(k) for k,p in prices.items()],axis=1,join='inner')
    h=ga.reconstruct_states(f,native)
    row=next(q for q in h if q['date']=='2026-10-19')
    assert row['status']=='UNAVAILABLE' and row['reason']=='MARKET_CLOSED'
    assert pd.notna(f.loc[closed,'participation_10d'])
