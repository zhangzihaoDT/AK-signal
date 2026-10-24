"""Fixed-candidate event replay on audited daily snapshots; no tuning/publication."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import pandas as pd
from src.common.paths import stock_metrics_dir
from src.market_facts import group_activity as activity

EVENT_RULE = dict(window=20, rise=.08, retention_days=5, retention_gain=.04, separation=20)


def price_events(nav, event_rule=None):
    event_rule = EVENT_RULE if event_rule is None else event_rule
    """Independent outcome: 20-session +8%, final 5 closes all above base +4%.

    No MA, RS, breadth, activity event or candidate state participates in labels.
    Confirmations at least 20 HS300 sessions apart prevent overlapping episodes.
    """
    events, last = [], -event_rule['separation']
    for i in range(event_rule['window'], len(nav)):
        base = float(nav.iloc[i-event_rule['window']])
        if (i-last >= event_rule['separation'] and nav.iloc[i]/base-1 >= event_rule['rise']
                and nav.iloc[i-event_rule['retention_days']+1:i+1].min()/base-1 >= event_rule['retention_gain']):
            onset = int(nav.iloc[i-event_rule['window']:i+1].argmin()) + i-event_rule['window']
            events.append(dict(index=i, date=nav.index[i], onset_index=onset, onset=nav.index[onset]))
            last = i
    return events


def evaluate_group(row, event_rule=None):
    if 'nav_history' not in row:
        return dict(key=row['key'], status='UNAVAILABLE', reason=row.get('reason'))
    nav = pd.Series(row['nav_history'], dtype=float)
    records = {r['date']: r for r in row['history']}
    valid = [i for i, d in enumerate(nav.index) if records[d]['status'] != 'UNAVAILABLE']
    if not valid:
        return dict(key=row['key'], status='UNAVAILABLE', reason='NO_VALID_OBSERVATIONS')
    signals = [i for i in valid if records[nav.index[i]]['first_trigger']]
    all_events = price_events(nav, event_rule)
    events = [e for e in all_events if e['index']-20 >= valid[0]]
    # Keep 20-day pre-event coverage outside the warm-up for an honest denominator.
    for event in events:
        candidates = [i for i in signals if max(event['index']-20, event['onset_index']-5) <= i < event['index']]
        event['captured'] = bool(candidates)
        event['first_watch'] = nav.index[min(candidates)] if candidates else None
        event['lead_sessions'] = event['index']-min(candidates) if candidates else None
        event['miss_context'] = None if candidates else [
            {k:records[d].get(k) for k in ('date','status','path','transition','conditions')}
            for d in nav.index[event['index']-5:event['index']+1]]
    observations = []
    for i in signals:
        r = records[nav.index[i]]
        matured = i+20 < len(nav)
        matched = [e['date'] for e in all_events if i < e['index'] <= i+20]
        lifecycle = [q for q in row['history'][i+1:i+21]
                     if q.get('episode_start') == nav.index[i] and q.get('transition') in ('INVALIDATED','EXPIRED')]
        lifecycle_end = lifecycle[0] if lifecycle else None
        price_outcome = 'CONFIRMED' if matched else 'UNCONFIRMED' if matured else 'IMMATURE'
        observations.append(dict(price_outcome=price_outcome,
            lifecycle_outcome=lifecycle_end['transition'] if lifecycle_end else 'NO_TERMINATION_WITHIN_WINDOW',
            lifecycle_date=lifecycle_end['date'] if lifecycle_end else None,
            date=nav.index[i], path=r['path'], matured_20d=matured,
            confirmed_events=matched, false_start=(not matched) if matured else None,
            forward_returns={str(n): float(nav.iloc[i+n]/nav.iloc[i]-1) if i+n < len(nav) else None for n in (5,10,20)},
            mae_20d=float(min(0, nav.iloc[i+1:i+21].min()/nav.iloc[i]-1)) if matured else None))
    matured = [r for r in observations if r['matured_20d']]
    leads = [e['lead_sessions'] for e in events if e['captured']]
    def average(values):
        values = [v for v in values if v is not None]
        return float(sum(values)/len(values)) if values else None
    return dict(key=row['key'], label=row['label'], status='EVALUATED', structural_anchor=row['structural_anchor'],
        first_valid=nav.index[valid[0]], last_valid=nav.index[valid[-1]], valid_sessions=len(valid),
        unavailable_sessions=len(nav)-len(valid),
        unavailable_reasons=dict(Counter(q.get('reason','UNKNOWN') for q in row['history'] if q['status']=='UNAVAILABLE')),
        unavailable_reason_memberships=dict(Counter(reason for q in row['history'] if q['status']=='UNAVAILABLE'
            for reason in q.get('unavailable_reasons',[q.get('reason','UNKNOWN')]))),
        outcomes=dict(Counter(q['price_outcome'] for q in observations)),
        lifecycle_cross=dict(Counter(q['price_outcome']+' / '+q['lifecycle_outcome'] for q in observations)),
        states=dict(Counter(records[nav.index[i]]['status'] for i in valid)),
        events=events, event_count=len(events), captured_events=len(leads),
        capture_rate=len(leads)/len(events) if events else None, mean_lead_sessions=average(leads),
        alerts=len(signals), alerts_per_100_valid_sessions=100*len(signals)/len(valid),
        mature_alerts=len(matured), censored_alerts=len(signals)-len(matured),
        false_starts=sum(r['false_start'] for r in matured),
        false_start_rate=average([r['false_start'] for r in matured]),
        mean_forward_returns={str(n): average([r['forward_returns'][str(n)] for r in observations]) for n in (5,10,20)},
        forward_counts={str(n): sum(r['forward_returns'][str(n)] is not None for r in observations) for n in (5,10,20)},
        mean_mae_20d=average([r['mae_20d'] for r in matured]),
        worst_mae_20d=min([r['mae_20d'] for r in matured], default=None), signals=observations)


def replay(trade_date, metrics_dir=None):
    folder = Path(metrics_dir) if metrics_dir else stock_metrics_dir()
    stocks = pd.read_parquet(folder/f'stock_metrics_{trade_date}.parquet')
    facts = activity.build_from_daily(trade_date, stocks, folder)
    activity.validate_document(facts, trade_date, stocks)
    rows = [evaluate_group(r) for r in facts['groups']]
    return facts, dict(trade_date=trade_date, rule_version=activity.RULE_VERSION, rules=activity.RULES,
        event_rule=EVENT_RULE, input_hash=facts['input_hash'], manifest_sha256=facts.get('manifest_sha256'),
        validation_status='UNVALIDATED', limitations=activity.LIMITATIONS + [
            '开发期固定参数探索性复算，未用前瞻收益调参；非独立样本外有效性认证',
            '当前成分/复权产生幸存者与事后调整偏差；缺少历史成分及历史时点价格版本',
            '重叠分组与重叠前瞻窗口非独立样本；事件标签仅为一种上涨定义',
            '末尾未满20日的提示不计假启动；最大不利波动只使用收盘价',
            '不同组起始季度不同；休市端点/预热日期不作为有效观察'], groups=rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', required=True)
    parser.add_argument('--metrics-dir', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    facts, evaluation = replay(args.date, args.metrics_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evaluation, ensure_ascii=False, indent=2, allow_nan=False))
    (args.output.parent/f'stock_group_activity_{args.date}.json').write_text(
        json.dumps(facts, ensure_ascii=False, indent=2, allow_nan=False))
    for r in evaluation['groups']:
        print(r['key'], {k:r.get(k) for k in ('event_count','captured_events','alerts','false_start_rate','mean_lead_sessions')})
