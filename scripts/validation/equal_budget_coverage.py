"""Equal-budget coverage for the frozen V1/V2 comparison. In-sample exploratory only.

Capture rate alone is not comparable across versions with different alert counts:
it rises monotonically with alert volume and saturates at 100%. This script holds the
alert budget fixed and asks whether the same number of alerts would still cover the
same events. It also reports per-alert productivity, the composition of new alert
dates and the share of each event already realised when the first alert fires.

Reads only the audited comparison artifacts; writes one JSON next to them.
"""
import argparse
import hashlib
import json
import random
from pathlib import Path

SEED = 20261024
DRAWS = 2000
POOLED_DRAWS = 1000
BUDGETS = (40, 60, 80, 100, 113)
VERSIONS = ('v1', 'v2')


def average(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def load(audit_dir):
    comparison = json.loads((audit_dir / 'comparison.json').read_text())
    navigation = {name: {g['key']: g['nav_history'] for g in
                         json.loads((audit_dir / f'{name}_facts.json').read_text())['groups']}
                  for name in VERSIONS}
    return comparison, navigation


def series(comparison, navigation):
    """Session index, alert positions and capture windows in the audited index space."""
    out = {}
    for name in VERSIONS:
        groups = {}
        for row in comparison['evaluations']['main_8pct']['versions'][name]:
            nav = navigation[name][row['key']]
            dates = list(nav)
            index = {date: i for i, date in enumerate(dates)}
            groups[row['key']] = dict(
                nav=nav, dates=dates,
                alerts=[index[s['date']] for s in row['signals']], signals=row['signals'],
                events=row['events'],
                windows=[(max(e['index'] - 20, e['onset_index'] - 5), e['index']) for e in row['events']])
        out[name] = groups
    return out


def verify_capture(groups):
    """Recompute captured with the rule in check_group_activity and require exact agreement."""
    checked = 0
    for name, rows in groups.items():
        for key, row in rows.items():
            for event, (start, end) in zip(row['events'], row['windows']):
                hit = any(start <= i < end for i in row['alerts'])
                assert hit == event['captured'], (name, key, event['date'])
                checked += 1
    return checked


def coverage(row, positions):
    return sum(1 for start, end in row['windows'] if any(start <= i < end for i in positions))


def equal_budget(groups, budget, draws=DRAWS, seed=SEED):
    """Budget spread across groups in proportion to each version's own alert count."""
    rng = random.Random(seed)
    total = sum(len(row['alerts']) for row in groups.values())
    values = []
    for _ in range(draws):
        covered = 0
        for row in groups.values():
            size = min(len(row['alerts']), max(1, round(budget * len(row['alerts']) / total)))
            covered += coverage(row, rng.sample(row['alerts'], size))
        values.append(covered)
    return average(values)


def pooled_budget(groups, budget, draws=POOLED_DRAWS, seed=SEED):
    """Budget allocated globally, so a version may spend it wherever it chose to alert."""
    rng = random.Random(seed)
    pool = [(key, i) for key, row in groups.items() for i in row['alerts']]
    values = []
    for _ in range(draws):
        sample = rng.sample(pool, budget)
        by_key = {}
        for key, i in sample:
            by_key.setdefault(key, []).append(i)
        values.append(sum(coverage(groups[key], positions) for key, positions in by_key.items()))
    return average(values)


def productivity(groups):
    out = {}
    for name, rows in groups.items():
        alerts = [s for row in rows.values() for s in row['signals']]
        linked = [s for s in alerts if s['confirmed_events']]
        empty = [s for s in alerts if not s['confirmed_events']]
        out[name] = dict(
            alerts=len(alerts), event_linked=len(linked), empty=len(empty),
            event_linked_share=len(linked) / len(alerts),
            empty_return_20d=average([s['forward_returns']['20'] for s in empty]),
            empty_mae_20d=average([s['mae_20d'] for s in empty]))
    return out


def new_alert_split(comparison):
    """Where the new alert dates went: re-timing an already captured event, new event, or nothing."""
    main = comparison['evaluations']['main_8pct']
    new_events = {(e['key'], e['date']) for e in main['newly_captured_events']}
    mild_confirmed = {(row['key'], s['date']): bool(s['confirmed_events'])
                      for row in comparison['evaluations']['mild_4pct']['versions']['v2']
                      for s in row['signals']}
    buckets = dict(new_event=[], old_event=[], empty=[])
    for alert in main['new_alerts']:
        referenced = set(alert['confirmed_events'] or [])
        if any((alert['key'], date) in new_events for date in referenced):
            buckets['new_event'].append(alert)
        elif referenced:
            buckets['old_event'].append(alert)
        else:
            buckets['empty'].append(alert)
    empty_mild = [a for a in buckets['empty'] if mild_confirmed.get((a['key'], a['date']))]
    return dict(
        new_alerts=len(main['new_alerts']),
        new_event_alerts=len(buckets['new_event']),
        new_events=len(main['newly_captured_events']),
        retimed_alerts=len(buckets['old_event']),
        empty_alerts=len(buckets['empty']),
        empty_without_mild_confirmation=len(buckets['empty']) - len(empty_mild),
        new_event_return_20d=average([a['forward_returns']['20'] for a in buckets['new_event']]),
        new_event_mae_20d=average([a['mae_20d'] for a in buckets['new_event']]),
        retimed_return_20d=average([a['forward_returns']['20'] for a in buckets['old_event']]),
        retimed_mae_20d=average([a['mae_20d'] for a in buckets['old_event']]),
        empty_return_20d=average([a['forward_returns']['20'] for a in buckets['empty']]),
        empty_mae_20d=average([a['mae_20d'] for a in buckets['empty']]))


def realized_fraction(groups):
    """Share of the trough-to-confirmation move already priced when the first alert fires.

    Paired on events both versions capture, so the comparison is not driven by the
    larger V2 event set. Negative never: the alert always precedes confirmation.
    """
    v1, v2 = groups['v1'], groups['v2']
    rows = []
    for key, row in v1.items():
        other = v2[key]
        for event, (start, end) in zip(row['events'], row['windows']):
            if not event['captured']:
                continue
            same = next((e for e in other['events'] if e['date'] == event['date']), None)
            if same is None or not same['captured']:
                continue
            base, confirm = row['nav'][event['onset']], row['nav'][event['date']]
            if confirm <= base:
                continue
            first = [i for i in row['alerts'] if start <= i < end][0]
            cross = max(same['index'] - 20, same['onset_index'] - 5)
            second = [i for i in sorted(other['alerts']) if cross <= i < same['index']][0]
            rows.append((event['date'],
                         (row['nav'][row['dates'][first]] - base) / (confirm - base),
                         (other['nav'][other['dates'][second]] - base) / (confirm - base)))
    return dict(
        paired_events=len(rows),
        v1_mean=average([r[1] for r in rows]), v2_mean=average([r[2] for r in rows]),
        v1_median=sorted(r[1] for r in rows)[len(rows) // 2],
        v2_median=sorted(r[2] for r in rows)[len(rows) // 2],
        v2_earlier=sum(1 for r in rows if r[2] < r[1]),
        v2_later=sum(1 for r in rows if r[2] > r[1]),
        equal=sum(1 for r in rows if r[2] == r[1]))


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(audit_dir, output):
    comparison, navigation = load(audit_dir)
    groups = series(comparison, navigation)
    checked = verify_capture(groups)
    result = dict(
        validation_status=comparison['validation_status'], trade_date=comparison['trade_date'],
        seed=SEED, draws=DRAWS, pooled_draws=POOLED_DRAWS, budgets=list(BUDGETS),
        capture_window_check=dict(events=checked, result='ok'),
        equal_budget={name: {str(b): equal_budget(groups[name], b) for b in BUDGETS} for name in VERSIONS},
        pooled_budget={name: {str(b): pooled_budget(groups[name], b) for b in BUDGETS} for name in VERSIONS},
        productivity=productivity(groups),
        new_alert_split=new_alert_split(comparison),
        realized_fraction=realized_fraction(groups),
        sources={f: sha256(audit_dir / f) for f in ('comparison.json', 'v1_facts.json', 'v2_facts.json')},
        script_sha256=sha256(Path(__file__)))
    Path(output).write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--audit-dir', type=Path,
                   default=Path('data/audits/group_activity/20261023/trend_start_v2'))
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    output = a.output or a.audit_dir / 'equal_budget_coverage.json'
    r = run(a.audit_dir, output)
    print('| 提示预算 | ' + ' | '.join(f'{n.upper()} 捕获' for n in VERSIONS) + ' |')
    print('| ---: | ---: | ---: |')
    for b in BUDGETS:
        print(f'| {b} | {r["equal_budget"]["v1"][str(b)]:.1f} | {r["equal_budget"]["v2"][str(b)]:.1f} |')
    print(json.dumps({k: r[k] for k in ('productivity', 'new_alert_split', 'realized_fraction')},
                     ensure_ascii=False, indent=2))
