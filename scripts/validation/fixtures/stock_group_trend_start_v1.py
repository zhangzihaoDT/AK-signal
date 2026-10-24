"""Frozen V1 acceptance control; never imported by production.
Original group_activity.py SHA256: efe38760412746f9515849614c7c10c570d6e7fc757a872755726ca292706b25
"""
import pandas as pd

RULE_VERSION = 'stock_group_trend_start_v1'

RULES = dict(history_sessions=60, sigma=2.5, stock_sigma=3.0, breadth_sigma=2.0, group_floor=.02, stock_floor=.05,
             breadth_delta=.30, breadth_count_delta=2, min_group_size=3,
             directional_breadth=.60, rs_improvement_pp=.5, participation_expansion=.10,
             early_participation=.50, support_participation=.60, rs_rising_days=3,
             emerging_days=2, confirmed_days=10, confirmed_return_20d=.03,
             failure_loss=.03, failure_days=2, watch_expiry=10, rearm_days=5)

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
    """Causal deterministic replay. Closed/invalid observations freeze the state.

    No previous output files are inputs. Initial established trends are tracking,
    never new opportunities. A fresh episode requires five reset observations.
    """
    history = []
    state, path, episode, entry, age = 'NO_SETUP', None, None, None, 0
    quiet = strong_run = established_run = price_run = weak_run = deterioration = watch_age = 0
    previous = None
    for day, f in features.iterrows():
        iso = day.date().isoformat()
        window = native_returns.loc[:day].tail(RULES['history_sessions'] + 1)
        if (f.isna().any() or day not in native_returns.index or len(window) < RULES['history_sessions'] + 1
                or window.isna().any().any()):
            history.append(dict(date=iso, status='UNAVAILABLE', reason='HISTORY_OR_CLOSED_ENDPOINT',
                                events=[], first_trigger=False, previous_observation_date=previous['date'] if previous else None))
            continue
        evidence = describe_returns(window)
        m = {k: float(v) for k, v in f.items()}
        price_turn = f.nav > f.ma5 and f.return_5d > 0 and f.ma5_change_3d > 0
        structure = f.nav > f.ma20 and f.ma5 > f.ma20 and f.ma20_change_3d >= 0 and f.return_5d > 0
        support = f.participation_10d >= RULES['support_participation'] and (
            f.rs_20d_pct > 0 or f.rs_20d_change_5d_pp >= RULES['rs_improvement_pp'])
        strong = structure and support
        established = (strong and f.return_20d >= RULES['confirmed_return_20d']
                       and f.participation_20d >= RULES['support_participation'] and f.rs_20d_pct >= 0)
        prior_established = price_run >= RULES['rearm_days']
        price_trend = f.nav > f.ma20 and f.ma20_change_3d > 0 and f.return_20d >= RULES['confirmed_return_20d']
        price_run = price_run + 1 if price_trend else 0
        strong_run = strong_run + 1 if strong else 0
        established_run = established_run + 1 if established else 0
        weak_run = weak_run + 1 if not structure else 0
        deteriorating = f.nav < f.ma10 and f.return_5d < 0
        deterioration = deterioration + 1 if deteriorating else 0
        upward_events = ('GROUP_UP' in evidence['events'] or 'BREADTH_UP' in evidence['events']
                         or any(r['return_1d'] > 0 for r in evidence['movers']))
        sudden = (upward_events and f.nav_return_1d > 0 and evidence['median_return_1d'] > 0
                  and evidence['breadth'] >= RULES['early_participation'] and not prior_established)
        gradual = (price_turn and f.rs_change_5d > 0
                   and f.rs_rising_days >= RULES['rs_rising_days']
                   and f.rs_20d_change_5d_pp >= RULES['rs_improvement_pp']
                   and f.participation_5d >= RULES['early_participation']
                   and f.participation_10d_change_5d >= RULES['participation_expansion']
                   and not prior_established)
        first = False
        transition = 'CONTINUATION'
        reasons = []
        if state == 'NO_SETUP':
            # Detect established trends even at the left boundary, without claiming discovery.
            if prior_established and strong_run >= RULES['emerging_days']:
                state = 'CONFIRMED' if established_run >= RULES['confirmed_days'] else 'EMERGING'
                transition, path = 'EXISTING_TREND', 'TRACKING'
                episode, entry, age = None, None, 0
            elif quiet >= RULES['rearm_days'] and (sudden or gradual):
                state, first, transition = 'WATCH', True, 'FIRST_TRIGGER'
                path = 'SUDDEN' if sudden else 'GRADUAL'
                episode, entry, age, watch_age = iso, float(f.nav), 0, 0
                reasons = ['弱势/横盘背景中的集体单日变化' if sudden else '价格止跌、相对强度持续改善且参与面扩大']
            else:
                transition = 'NO_CHANGE'
        else:
            age += 1
            watch_age = watch_age + 1 if state == 'WATCH' else 0
            failed = ((entry is not None and f.nav / entry - 1 <= -RULES['failure_loss'])
                      or deterioration >= RULES['failure_days'])
            expired = state == 'WATCH' and watch_age >= RULES['watch_expiry'] and not strong
            if failed or expired:
                state, transition = 'NO_SETUP', 'INVALIDATED' if failed else 'EXPIRED'
                reasons = ['跌破启动收盘价3%或连续两次价格恶化' if failed else '十次有效观察仍未形成转强结构']
                quiet = 0
            elif state == 'WATCH' and strong_run >= RULES['emerging_days']:
                state, transition = 'EMERGING', 'UPGRADE'
                reasons = ['连续两次价格结构转强，并获得参与度及相对强度支持']
            elif state == 'EMERGING' and established_run >= RULES['confirmed_days']:
                state, transition = 'CONFIRMED', 'UPGRADE'
                reasons = ['连续十次具备上行结构、20日涨幅及广泛参与支持']
            elif state in ('EMERGING', 'CONFIRMED') and weak_run >= RULES['failure_days']:
                state, transition, watch_age = 'WATCH', 'DOWNGRADE', 0
                reasons = ['连续两次失去价格转强结构，保留原启动事件跟踪']
        quiet = quiet + 1 if state == 'NO_SETUP' and not price_trend else 0
        changes = {} if previous is None else {
            k: m[k] - previous['metrics'][k] for k in
            ('nav', 'rs_20d_pct', 'participation_5d', 'participation_10d')}
        row = dict(date=iso, status=state, previous_status=previous['status'] if previous else None,
                   reason='', events=evidence['events'], metrics=m, evidence=evidence,
                   previous_observation_date=previous['date'] if previous else None,
                   changes=changes, path=path, first_trigger=first, transition=transition,
                   episode_start=episode, episode_age=age, watch_age=watch_age, reasons=reasons,
                   conditions=dict(price_turn=bool(price_turn), structure=bool(structure),
                                   support=bool(support), existing_price_trend=bool(prior_established), sudden=bool(sudden), gradual=bool(gradual)),
                   data_complete=True)
        history.append(row)
        previous = row
    return history
