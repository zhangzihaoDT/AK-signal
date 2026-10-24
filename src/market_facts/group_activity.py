"""Daily section-04 attention facts, computed offline from its stock snapshots.

Trend states use Compare quarterly structural NAV. Median returns are only clues.
No gap fills, rankings, recommendations or cross-provider fallback are used here.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from src.common.paths import project_root
from .calendar import SessionCalendar
from .consume import snapshot_frame
from .contract import validate_snapshot
from .policy import excluded_stock

RULE_VERSION = 'stock_group_trend_start_v2'
RULES = dict(history_sessions=60, sigma=2.5, stock_sigma=3.0, breadth_sigma=2.0, group_floor=.02, stock_floor=.05,
             breadth_delta=.30, breadth_count_delta=2, min_group_size=3,
             directional_breadth=.60, rs_improvement_pp=.5, participation_expansion=.10,
             early_participation=.50, support_participation=.60, rs_rising_days=3,
             emerging_days=2, confirmed_days=10, confirmed_return_20d=.03,
             failure_loss=.03, failure_days=2, watch_expiry=10, existing_price_days=5,
             support_loss_days=2, restart_recovery=.01)
STATES = ('WATCH', 'EMERGING', 'CONFIRMED', 'NO_SETUP', 'UNAVAILABLE')
LIMITATIONS = ['候选参数未验证；当前成分与当前复权快照重建历史，非历史时点回测',
               'Longbridge ForwardAdjust 公司行为算法未认证；price_basis_verified=false',
               '结构指数不含汇率与费用，不代表真实组合收益']


def identity(market, symbol):
    return str(market), str(symbol).zfill(5 if market == 'HK' else 6)


def definitions():
    """Use exactly the same 8+1 registrations as the daily stock panels."""
    from src.research.baskets.comparison import comparison_baskets, constituents
    groups = []
    for key, basket in comparison_baskets().items():
        if basket['weighting'] != {'method': 'group_equal', 'rebalance': 'quarterly'}:
            raise ValueError('UNSUPPORTED_STRUCTURAL_WEIGHTING')
        members, excluded = constituents({**basket, 'key': key})
        if len({identity(r['market'], r['symbol']) for r in members}) != len(members):
            raise ValueError('DUPLICATE_GROUP_MEMBER')
        groups.append(dict(key=key, label=basket['label'], role=basket['role'],
                           weighting=basket['weighting'], members=members + excluded))
    return groups


def config_hash(groups):
    content = dict(groups=groups, excluded=[r for g in groups for r in g['members']
                                           if excluded_stock(r['market'], r['symbol'])])
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def describe_returns(returns: pd.DataFrame) -> dict:
    """Last observation vs preceding 60 sessions, excluding today from baseline."""
    count = RULES['history_sessions']
    if len(returns) < count + 1 or returns.isna().any().any():
        raise ValueError('INSUFFICIENT_HISTORY_OR_DATA_GAP')
    window = returns.iloc[-count-1:]
    today, prior = window.iloc[-1], window.iloc[-2]
    baseline = window.iloc[:-1]
    medians = baseline.median(axis=1)
    center, std = float(medians.mean()), float(medians.std(ddof=1))
    median = float(today.median())
    positive, previous_positive = int(today.gt(0).sum()), int(prior.gt(0).sum())
    n = len(today)
    breadth = positive / n
    delta_count = positive - previous_positive
    breadth_changes = baseline.gt(0).mean(axis=1).diff().dropna()
    breadth_mean, breadth_std = float(breadth_changes.mean()), float(breadth_changes.std(ddof=1))
    breadth_threshold = max(RULES['breadth_delta'], RULES['breadth_sigma'] * breadth_std)
    breadth_delta = delta_count / n
    events = []
    bound = max(RULES['group_floor'], RULES['sigma'] * std)
    group_move = (n >= RULES['min_group_size'] and abs(median) >= RULES['group_floor']
                  and abs(median - center) >= bound)
    if group_move and median > 0 and median > center and breadth >= RULES['directional_breadth']:
        events.append('GROUP_UP')
    if group_move and median < 0 and median < center and float(today.lt(0).mean()) >= RULES['directional_breadth']:
        events.append('GROUP_DOWN')
    if (n >= RULES['min_group_size'] and abs(delta_count) >= RULES['breadth_count_delta']
            and abs(breadth_delta) >= RULES['breadth_delta']
            and abs(breadth_delta - breadth_mean) >= breadth_threshold):
        events.append('BREADTH_UP' if delta_count > 0 else 'BREADTH_DOWN')
    movers = []
    for key in today.index:
        mean, sd = float(baseline[key].mean()), float(baseline[key].std(ddof=1))
        change = float(today[key])
        threshold = max(RULES['stock_floor'], RULES['stock_sigma'] * sd)
        if abs(change) >= RULES['stock_floor'] and abs(change - mean) >= threshold:
            movers.append(dict(market=key[0], symbol=key[1], return_1d=change,
                               baseline_mean=mean, baseline_std=sd, threshold=threshold))
    if movers:
        events.append('MEMBER_MOVE')
    return dict(events=events, median_return_1d=median, positive_count=positive,
                previous_positive_count=previous_positive, valid_count=n,
                breadth=breadth, breadth_delta=breadth_delta,
                breadth_baseline_mean=breadth_mean, breadth_baseline_std=breadth_std,
                breadth_baseline_count=len(breadth_changes), breadth_threshold=breadth_threshold,
                previous_observation_date=window.index[-2].date().isoformat(),
                baseline_start=baseline.index[0].date().isoformat(),
                baseline_end=baseline.index[-1].date().isoformat(),
                baseline_count=len(baseline), baseline_mean=center, baseline_std=std,
                group_threshold=bound, movers=movers)


def _returns(snapshot, calendar, target):
    """Native-market one-session returns; never turn holidays into zero returns."""
    snapshot = validate_snapshot(snapshot)
    if snapshot['target_trade_date'] != target.date().isoformat():
        raise ValueError('TARGET_MISMATCH')
    if (snapshot['identity']['provider'] != 'longbridge'
            or snapshot['identity']['price_basis'] != 'vendor_forward_adjusted'
            or not snapshot['quality']['source_dates_verified']):
        raise ValueError('SOURCE_UNVERIFIED')
    if calendar.expected_session(target.date()) != target.date():
        raise ValueError('MARKET_CLOSED')
    assessment = calendar.assess(snapshot)
    if assessment['session_complete'] is not True or not snapshot['quality']['target_source_date_verified']:
        raise ValueError('TARGET_UNVERIFIED')
    if assessment['unexpected_sessions']:
        raise ValueError('NON_SESSION_BARS')
    frame = snapshot_frame(snapshot)
    close = frame.set_index('date').close.astype(float)
    days = calendar.index()
    days = days[(days >= close.index.min()) & (days <= target)]
    # Reindex before pct_change: missing native sessions invalidate adjacent returns.
    return close.reindex(days).pct_change(fill_method=None)


def _close(snapshot, calendar, target, key, benchmark=False):
    snapshot = validate_snapshot(snapshot)
    ident = snapshot['identity']
    expected_symbol = '000300' if benchmark else key[1]
    if (ident['market'] != key[0] or ident['provider_symbol'].split('.')[0] != expected_symbol
            or (benchmark and (ident['provider_symbol'] != '000300.SH' or ident['asset_type'] != 'index'))):
        raise ValueError('IDENTITY_MISMATCH')
    if snapshot['target_trade_date'] != target.date().isoformat():
        raise ValueError('TARGET_MISMATCH')
    if (ident['provider'] != 'longbridge'
            or ident['price_basis'] != ('index_level' if benchmark else 'vendor_forward_adjusted')
            or not snapshot['quality']['source_dates_verified']):
        raise ValueError('SOURCE_UNVERIFIED')
    assessment = calendar.assess(snapshot)
    if assessment['unexpected_sessions']:
        raise ValueError('NON_SESSION_BARS')
    if calendar.expected_session(target.date()) == target.date():
        if assessment['session_complete'] is not True or not snapshot['quality']['target_source_date_verified']:
            raise ValueError('TARGET_UNVERIFIED')
    close = snapshot_frame(snapshot).set_index('date').close.astype(float)
    expected = calendar.index()
    expected = expected[(expected >= close.index.min()) & (expected <= target)]
    if close.index.min().date() < calendar.start or close.index.max() > target:
        raise ValueError('CALENDAR_OR_DATE_UNCOVERED')
    if close.reindex(expected).isna().any():
        raise ValueError('DATA_GAP_OR_INSUFFICIENT_HISTORY')
    return close


def trend_features(nav, aligned):
    """Compare definitions: RS20Δ is rs_20d_pct, NOT a change of that change.

    5/10-day participation uses the same endpoint-valid positive-return fraction.
    Additional acceleration is explicitly named rs_20d_change_5d_pp.
    """
    from src.research.baskets.metrics import relative_strength, _participation
    price = nav.basket
    f = pd.DataFrame(index=nav.index)
    f['nav'] = price
    for n in (5, 10, 20):
        f[f'ma{n}'] = price.rolling(n).mean()
        f[f'return_{n}d'] = price.pct_change(n, fill_method=None)
        participation = [_participation(aligned, i-n, i) if i >= n else (None, 0, 0)
                         for i in range(len(price))]
        f[f'participation_{n}d'] = [r[0] if r[2] == len(aligned.keys) else None for r in participation]
    f['rs'] = relative_strength(nav)
    f['rs_20d_pct'] = f.rs.pct_change(20, fill_method=None) * 100
    f['rs_20d_change_5d_pp'] = f.rs_20d_pct.diff(5)
    f['rs_rising_days'] = f.rs_20d_pct.diff().gt(0).rolling(5).sum()
    f['rs_change_5d'] = f.rs.pct_change(5, fill_method=None)
    f['participation_10d_change_5d'] = f.participation_10d.diff(5)
    f['ma5_change_3d'] = f.ma5.pct_change(3, fill_method=None)
    f['ma20_change_3d'] = f.ma20.pct_change(3, fill_method=None)
    f['nav_return_1d'] = price.pct_change(fill_method=None)
    return f


def reconstruct_states(features, native_returns):
    """Causal episodes: price/evidence qualify setups; reset + novelty deduplicate.

    There is no NO_SETUP waiting period. Member clues have their own identity and
    never count as a group WATCH. All counters freeze on unavailable group days.
    """
    history, previous = [], None
    state, path, origin, episode, entry = 'NO_SETUP', None, None, None, None
    trigger_channel, episode_status = None, 'NONE'
    age = watch_age = strong_run = established_run = price_run = weak_run = deterioration = 0
    support_loss = confirmation_loss = 0
    reset_seen, reset_low = False, None
    prior_candidate = prior_sudden = False
    prior_leaders = set()
    for position, (day, f) in enumerate(features.iterrows()):
        iso = day.date().isoformat()
        window = native_returns.loc[:day].tail(RULES['history_sessions'] + 1)
        native_valid = (day in native_returns.index and len(window) == RULES['history_sessions'] + 1
                        and not window.isna().any().any())
        evidence = describe_returns(window) if native_valid else None
        leaders = [dict(r, scope='MEMBER', first_clue=(r['market'], r['symbol']) not in prior_leaders)
                   for r in evidence['movers'] if r['return_1d'] > 0] if evidence else []
        if native_valid:
            prior_leaders = {(r['market'], r['symbol']) for r in leaders}
        reasons_unavailable = []
        if day not in native_returns.index:
            reasons_unavailable.append('MARKET_CLOSED')
        if len(window) < RULES['history_sessions'] + 1:
            reasons_unavailable.append('INSUFFICIENT_NATIVE_HISTORY')
        elif window.isna().any().any():
            initial_return_missing = window.index[0] == native_returns.index[0]
            reasons_unavailable.append('INSUFFICIENT_NATIVE_HISTORY' if initial_return_missing else 'NATIVE_HISTORY_GAP')
        if f.isna().any():
            reasons_unavailable.append('INSUFFICIENT_STRUCTURAL_HISTORY' if position < 25
                                       else 'INCOMPLETE_STRUCTURAL_METRICS')
        if reasons_unavailable:
            history.append(dict(date=iso, status='UNAVAILABLE', reason=reasons_unavailable[0],
                unavailable_reasons=reasons_unavailable, events=[], first_trigger=False,
                leader_clues=leaders, first_leader_clue=any(r['first_clue'] for r in leaders),
                previous_observation_date=previous['date'] if previous else None, data_complete=False))
            continue
        m = {k: float(v) for k, v in f.items()}
        price_turn = f.nav > f.ma5 and f.return_5d > 0 and f.ma5_change_3d > 0
        structure = f.nav > f.ma20 and f.ma5 > f.ma20 and f.ma20_change_3d >= 0 and f.return_5d > 0
        support = f.participation_10d >= RULES['support_participation'] and (
            f.rs_20d_pct > 0 or f.rs_20d_change_5d_pp >= RULES['rs_improvement_pp'])
        strong = structure and support
        established = (strong and f.return_20d >= RULES['confirmed_return_20d']
                       and f.participation_20d >= RULES['support_participation'] and f.rs_20d_pct >= 0)
        price_trend = f.nav > f.ma20 and f.ma20_change_3d > 0 and f.return_20d >= RULES['confirmed_return_20d']
        prior_established = price_run >= RULES['existing_price_days'] or (previous is None and price_trend)
        price_run = price_run + 1 if price_trend else 0
        strong_run = strong_run + 1 if strong else 0
        established_run = established_run + 1 if established else 0
        weak_run = weak_run + 1 if not structure else 0
        support_loss = support_loss + 1 if not support else 0
        confirmation_loss = confirmation_loss + 1 if not established else 0
        deteriorating = f.nav < f.ma10 and f.return_5d < 0
        deterioration = deterioration + 1 if deteriorating else 0
        background_eligible = not prior_established or (state == 'NO_SETUP' and episode is not None and reset_seen)
        upward_events = 'GROUP_UP' in evidence['events'] or 'BREADTH_UP' in evidence['events'] or bool(leaders)
        sudden = (upward_events and f.nav_return_1d > 0 and evidence['median_return_1d'] > 0
                  and evidence['breadth'] >= RULES['early_participation'] and background_eligible)
        rs_early = (f.rs_change_5d > 0 and f.rs_rising_days >= RULES['rs_rising_days']
                    and f.rs_20d_change_5d_pp >= RULES['rs_improvement_pp'])
        participation_early = (f.participation_5d >= RULES['early_participation']
                               and f.participation_10d_change_5d >= RULES['participation_expansion'])
        gradual = price_turn and (rs_early or participation_early) and background_eligible
        channel = ('COLLECTIVE' if sudden else 'RS_AND_PARTICIPATION' if rs_early and participation_early
                   else 'RS_IMPROVEMENT' if rs_early else 'PARTICIPATION_EXPANSION' if participation_early else None)
        candidate = sudden or gradual
        if state == 'NO_SETUP' and episode is not None:
            reset_low = min(float(f.nav), reset_low if reset_low is not None else float(f.nav))
            reset_seen = reset_seen or not price_turn or deteriorating
        new_impulse = (candidate and not prior_candidate) or (sudden and not prior_sudden)
        recovery = (reset_low is not None and f.nav / reset_low - 1 >= RULES['restart_recovery']
                    and f.nav_return_1d > 0)
        novel = episode is None or (reset_seen and (new_impulse or recovery))
        first, transition, reasons = False, 'CONTINUATION', []
        if state == 'NO_SETUP':
            if candidate and novel:
                state, first, transition = 'WATCH', True, 'FIRST_TRIGGER'
                path, origin = ('SUDDEN' if sudden else 'GRADUAL'), 'EARLY_CANDIDATE'
                episode, entry, age, watch_age = iso, float(f.nav), 0, 0
                reset_seen, reset_low = False, None
                trigger_channel, episode_status = channel, 'ACTIVE'
                reasons = ['集体单日变化与弱势背景' if sudden else
                           '价格转向，并有'+('RS改善及参与度扩散' if rs_early and participation_early else
                                           'RS改善' if rs_early else '参与度扩散')]
            elif prior_established and strong_run >= RULES['emerging_days']:
                state = 'CONFIRMED' if established_run >= RULES['confirmed_days'] else 'EMERGING'
                transition, path, origin = 'EXISTING_TREND_OBSERVED', None, 'EXISTING_TREND'
                episode, entry, age, watch_age = iso, None, 0, 0
                trigger_channel, episode_status = None, 'ACTIVE'
            else:
                transition = 'DUPLICATE_SUPPRESSED' if candidate else 'NO_CHANGE'
        else:
            age += 1
            watch_age = watch_age + 1 if state == 'WATCH' else 0
            failed = ((entry is not None and f.nav / entry - 1 <= -RULES['failure_loss'])
                      or deterioration >= RULES['failure_days'])
            expired = state == 'WATCH' and watch_age >= RULES['watch_expiry'] and not strong
            if failed or expired:
                state, transition = 'NO_SETUP', 'INVALIDATED' if failed else 'EXPIRED'
                reset_seen, reset_low = bool(failed or not price_turn or deteriorating), float(f.nav)
                episode_status = transition
                reasons = ['价格失效' if failed else 'WATCH观察到期仍未获得确认']
            elif state in ('EMERGING', 'CONFIRMED') and (weak_run >= RULES['failure_days']
                    or support_loss >= RULES['support_loss_days']):
                state, transition, watch_age = 'WATCH', 'DOWNGRADE', 0
                reasons = ['连续两次失去价格结构或RS/参与度支持']
            elif state == 'CONFIRMED' and confirmation_loss >= RULES['support_loss_days']:
                state, transition = 'EMERGING', 'DOWNGRADE'
                reasons = ['连续两次失去持续确认条件，价格转强与初步支持仍在']
            elif state == 'WATCH' and strong_run >= RULES['emerging_days']:
                state, transition = 'EMERGING', 'UPGRADE'
                reasons = ['连续两次价格结构转强，并获得参与度及RS支持']
            elif state == 'EMERGING' and established_run >= RULES['confirmed_days']:
                state, transition = 'CONFIRMED', 'UPGRADE'
                reasons = ['连续十次满足价格持续性及RS/参与度支持']
        changes = {} if previous is None else {k: m[k] - previous['metrics'][k] for k in
            ('nav', 'rs_20d_pct', 'participation_5d', 'participation_10d')}
        row = dict(date=iso, status=state, previous_status=previous['status'] if previous else None,
            reason='', events=evidence['events'], metrics=m, evidence=evidence,
            previous_observation_date=previous['date'] if previous else None, changes=changes,
            path=path, event_origin=origin, first_trigger=first, transition=transition,
            event_active=state!='NO_SETUP', episode_status=episode_status, trigger_channel=trigger_channel,
            episode_start=episode, episode_age=age, watch_age=watch_age, reasons=reasons,
            evidence_channel=channel, leader_clues=leaders,
            first_leader_clue=any(r['first_clue'] for r in leaders),
            alert_kind='FIRST_WATCH' if first else 'STATE_CHANGE' if transition in
                ('UPGRADE','DOWNGRADE','INVALIDATED','EXPIRED') else 'MEMBER_LEAD' if any(r['first_clue'] for r in leaders) else 'NONE',
            conditions=dict(price_turn=bool(price_turn), structure=bool(structure), support=bool(support),
                existing_price_trend=bool(prior_established), background_eligible=bool(background_eligible), sudden=bool(sudden), gradual=bool(gradual),
                rs_early=bool(rs_early), participation_early=bool(participation_early),
                new_impulse=bool(new_impulse), reset_seen=bool(reset_seen), recovery=bool(recovery), novel=bool(novel)),
            data_complete=True)
        history.append(row)
        previous = row
        prior_candidate, prior_sudden = bool(candidate), bool(sudden)
    return history


def group_history(group, closes, benchmark, calendars, reconstructor=None):
    from src.research.baskets.nav import align_assets, nav_from_panel, natural_quarter_ends
    members = [r for r in group['members'] if not excluded_stock(r['market'], r['symbol'])]
    keys = [identity(r['market'], r['symbol']) for r in members]
    start = max([benchmark.index.min()] + [closes[k].index.min() for k in keys])
    end = benchmark.index[-1]
    # A real quarter-end resets all weights, making the later path independent of
    # missing earlier prices. No arbitrary late history is treated as IPO cash.
    anchors = natural_quarter_ends(calendars['CN'].days, calendars['CN'].end,
                                  start - pd.Timedelta(days=1), end)
    anchors = [d for d in anchors if all(d in closes[k].index for k in keys)]
    if not anchors:
        raise ValueError('INSUFFICIENT_HISTORY')
    anchor = anchors[0]
    prices = {k: closes[k].rename('close').rename_axis('date').reset_index() for k in keys}
    bench = benchmark.loc[anchor:].rename('close').rename_axis('date').reset_index()
    panel = align_assets(prices, members, bench, group.get('weighting', {}).get('method', 'group_equal'))
    nav = nav_from_panel(panel, 'quarterly', calendars['CN'].days, calendars['CN'].end).nav
    # align_assets may carry valuations across verified exchange closures only:
    # _close has already rejected every missing native session. These are not
    # zero-return observations; candidate decisions use common-open native returns.
    native = pd.concat([closes[k].pct_change(fill_method=None).rename(k) for k in keys], axis=1, join='inner')
    history = (reconstructor or reconstruct_states)(trend_features(nav, panel), native)
    return history, nav, anchor


def calculate(trade_date, snapshots, calendars, groups=None, rejected=None):
    groups = definitions() if groups is None else groups
    target = pd.Timestamp(trade_date)
    rejected = rejected or {}
    closes, failures = {}, {}
    keys = {identity(r['market'], r['symbol']) for g in groups for r in g['members']
            if not excluded_stock(r['market'], r['symbol'])}
    for key in sorted(keys | {('CN', '_benchmark')}):
        try:
            if key in rejected:
                raise ValueError(rejected[key])
            if key not in snapshots:
                raise ValueError('MISSING_SNAPSHOT')
            closes[key] = _close(snapshots[key], calendars[key[0]], target, key, key[1] == '_benchmark')
        except (ValueError, KeyError) as exc:
            failures[key] = str(exc)
    rows = []
    for group in groups:
        members = [r for r in group['members'] if not excluded_stock(r['market'], r['symbol'])]
        member_keys = [identity(r['market'], r['symbol']) for r in members]
        row = dict(key=group['key'], label=group['label'], role=group['role'],
                   status='UNAVAILABLE', events=[], first_trigger=False, data_complete=False,
                   total_count=len(members), excluded_count=len(group['members'])-len(members),
                   snapshot_ids={f'{m}:{s}': snapshots[(m, s)]['snapshot_id'] for m, s in member_keys
                                 if (m, s) in snapshots},
                   issues=[dict(market=m, symbol=s, reason=failures[(m, s)])
                           for m, s in member_keys + [('CN', '_benchmark')] if (m, s) in failures],
                   limitations=LIMITATIONS)
        closed = any(calendars[m].expected_session(target.date()) != target.date() for m, _ in member_keys + [('CN', '_benchmark')])
        if closed:
            row['reason'] = 'MARKET_CLOSED'
        elif row['issues'] or not members:
            row['reason'] = 'MEMBER_OR_BENCHMARK_DATA_UNAVAILABLE'
        else:
            try:
                history, nav, anchor = group_history(group, closes, closes[('CN', '_benchmark')], calendars)
                row.update(history[-1])
                row['history'] = history
                row['structural_anchor'] = anchor.date().isoformat()
                row['valid_count'] = len(members) if row['status'] != 'UNAVAILABLE' else 0
                row['nav_history'] = {d.date().isoformat(): float(v) for d, v in nav.basket.items()}
            except ValueError as exc:
                row['reason'] = str(exc)
        rows.append(row)
    inputs = dict(config_hash=config_hash(groups), calendars={m: c.digest for m, c in calendars.items()},
                  snapshots={f'{m}:{s}': v['snapshot_id'] for (m, s), v in sorted(snapshots.items())},
                  rejected={f'{m}:{s}': v for (m, s), v in sorted(rejected.items())})
    return dict(rule_version=RULE_VERSION, rules=dict(RULES), trade_date=target.strftime('%Y%m%d'),
                source='longbridge', price_basis='Longbridge ForwardAdjust', price_basis_verified=False,
                input_fetched_at=max((v['fetched_at'] for v in snapshots.values()), default=None),
                metric_basis='basket_compare_v2_group_equal_quarterly', validation_status='UNVALIDATED',
                benchmark_snapshot_id=snapshots.get(('CN', '_benchmark'), {}).get('snapshot_id'),
                input_hash=hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest(),
                config_hash=inputs['config_hash'], calendars=inputs['calendars'], groups=rows)


def build_from_daily(trade_date, stocks, metrics_dir: Path):
    """Read the audited batch already consumed by section 04; no latest pointers."""
    snapshots, rejected = {}, {}
    audit_path = metrics_dir / f'stock_update_audit_{trade_date}.json'
    calendars = {m: SessionCalendar.load(project_root()/f'config/market_calendars/{c}.json')
                 for m, c in [('CN', 'XSHG'), ('HK', 'XHKG')]}
    if not audit_path.exists():
        result = calculate(trade_date, {}, calendars)
        result['input_error'] = 'MISSING_BATCH_AUDIT'
        return result
    audit = json.loads(audit_path.read_text())
    if audit['trade_date'] != trade_date or audit['source'] != 'longbridge':
        raise ValueError('分组异动批次来源或日期不一致')
    manifest_path = Path(audit['snapshot_manifest'])
    content = manifest_path.read_bytes()
    if hashlib.sha256(content).hexdigest() != audit['manifest_sha256']:
        raise ValueError('分组异动快照清单摘要不一致')
    manifest = json.loads(content)
    if manifest['trade_date'] != trade_date or manifest['source'] != 'longbridge':
        raise ValueError('分组异动快照清单来源或日期不一致')
    expected = {identity(r.market, r.symbol): r for r in stocks.itertuples()}
    seen = set()
    for record in manifest['records']:
        key = identity(record['market'], record['symbol'])
        if key == ('CN', '_benchmark'):
            if key in seen:
                raise ValueError('分组趋势基准快照重复')
            seen.add(key)
            if 'file' in record:
                snapshot = validate_snapshot(json.loads((manifest_path.parent/record['file']).read_text()))
                if snapshot['snapshot_id'] != record['snapshot_id']:
                    raise ValueError('分组趋势基准快照不一致')
                snapshots[key] = snapshot
            continue
        if key not in expected or excluded_stock(*key):
            continue
        if key in seen:
            raise ValueError('分组异动快照重复')
        seen.add(key)
        row = expected[key]
        if getattr(row, 'identity_status', '') == 'IDENTITY_MISMATCH':
            rejected[key] = 'IDENTITY_MISMATCH'
            continue
        if 'file' not in record:
            continue
        snapshot = validate_snapshot(json.loads((manifest_path.parent/record['file']).read_text()))
        if record.get('security_id') != snapshot['identity']['security_id']:
            raise ValueError('分组趋势快照股票身份不一致')
        if snapshot['snapshot_id'] != record['snapshot_id'] or snapshot['snapshot_id'] != row.snapshot_id:
            raise ValueError('分组异动与个股趋势未消费同一长桥快照')
        if (snapshot['identity']['market'] != key[0]
                or snapshot['identity']['provider_symbol'].split('.')[0] != key[1]):
            raise ValueError('分组异动快照股票身份不一致')
        if pd.Timestamp(row.trade_date) != pd.Timestamp(trade_date):
            raise ValueError('分组异动与个股趋势交易日不一致')
        if row.data_status not in ('current', 'market_closed'):
            rejected[key] = 'STOCK_FACT_UNAVAILABLE'
        snapshots[key] = snapshot
    from dataclasses import asdict
    from .stock_universe import observation_stock_items
    from .policy import source_policy
    registrations = observation_stock_items()
    assets = {(i.asset.market, i.asset.symbol): i.asset for i in registrations}
    signature = hashlib.sha256(json.dumps({'policy': source_policy()['stocks'],
        'assets': [asdict(a) for a in assets.values()]}, sort_keys=True).encode()).hexdigest()
    if manifest.get('signature') != signature:
        raise ValueError('分组趋势快照注册配置不一致')
    result = calculate(trade_date, snapshots, calendars, rejected=rejected)
    result['snapshot_manifest'] = str(manifest_path)
    result['manifest_sha256'] = audit['manifest_sha256']
    result['history_limitations'] = {f"{r['market']}:{r['symbol']}":r['history_limitation']
                                     for r in manifest['records'] if r.get('history_limitation')}
    return result


def validate_document(document, trade_date, stocks, metrics_dir=None):
    """Publication gates for dated, versioned and snapshot-bound attention facts."""
    if (document['trade_date'] != pd.Timestamp(trade_date).strftime('%Y%m%d')
            or document['rule_version'] != RULE_VERSION or document['rules'] != RULES
            or document['source'] != 'longbridge'
            or document['price_basis_verified'] is not False):
        raise ValueError('分组异动日期/规则/来源不一致')
    groups = definitions()
    if document['config_hash'] != config_hash(groups):
        raise ValueError('分组异动注册配置不一致')
    if [g['key'] for g in groups] != [g['key'] for g in document['groups']]:
        raise ValueError('分组异动覆盖不一致')
    expected = {':'.join(identity(r.market, r.symbol)): getattr(r, 'snapshot_id', None)
                for r in stocks.itertuples()}
    for group, row in zip(groups, document['groups']):
        members = {':'.join(identity(r['market'], r['symbol'])) for r in group['members']
                   if not excluded_stock(r['market'], r['symbol'])}
        if row['status'] not in STATES or row['total_count'] != len(members):
            raise ValueError('分组异动状态或成分数不一致')
        if row['status'] != 'UNAVAILABLE' and (set(row['snapshot_ids']) != members or row['valid_count'] != len(members)):
            raise ValueError('分组异动可用状态缺少完整成分')
        if row['status'] == 'UNAVAILABLE' and row['events']:
            raise ValueError('不可用分组含异动结论')
        for key, snapshot_id in row['snapshot_ids'].items():
            if key not in members or snapshot_id != expected.get(key):
                raise ValueError('分组异动与个股趋势快照不一致')

    for row in document['groups']:
        if row['status'] != 'UNAVAILABLE':
            if not row.get('data_complete') or not row.get('history'):
                raise ValueError('分组趋势缺少历史状态证据')
            for key, value in row['history'][-1].items():
                if row.get(key) != value:
                    raise ValueError('分组趋势状态与历史依据不一致')
            if row['first_trigger'] and (row['status'] != 'WATCH' or row['transition'] != 'FIRST_TRIGGER'):
                raise ValueError('分组趋势首次触发状态不一致')
    if metrics_dir is not None:
        expected_document = build_from_daily(trade_date, stocks, metrics_dir)
        if document != expected_document:
            raise ValueError('分组趋势与本批快照重建结果不一致')
