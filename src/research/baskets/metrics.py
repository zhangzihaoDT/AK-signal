"""Observation-only metrics for the basket comparison.

The metrics describe the structural index, relative strength and breadth. They
are not a portfolio backtest and do not feed any strategy or allocation rule.
"""
from __future__ import annotations

from typing import Any, Iterable

import pandas as pd

from .nav import AlignedAssets

WINDOW_ORDER = ('all', '1y', 'ytd', '3m')
WINDOW_LABELS = {
    'all': '完整区间',
    '1y': '近一年',
    'ytd': 'YTD',
    '3m': '近三个月',
}


def relative_strength(nav: pd.DataFrame) -> pd.Series:
    """Basket/benchmark relative NAV, normalized to 100 at the path start."""
    if nav.empty or 'basket' not in nav or 'benchmark' not in nav:
        return pd.Series(dtype=float)
    basket = nav['basket'].astype(float)
    benchmark = nav['benchmark'].astype(float)
    if basket.iloc[0] == 0 or benchmark.iloc[0] == 0:
        raise ValueError('NAV base must be positive')
    return (basket / basket.iloc[0]) / (benchmark / benchmark.iloc[0]) * 100


def rs_20d_pct(nav: pd.DataFrame) -> float | None:
    """RS change over the last 20 benchmark sessions, independent of any window."""
    rs = relative_strength(nav)
    if len(rs) < 21:
        return None
    base = float(rs.iloc[-21])
    if base == 0 or pd.isna(base):
        return None
    return float(100 * (rs.iloc[-1] / base - 1))


def _first_index(dates: pd.DatetimeIndex, target: pd.Timestamp) -> int:
    position = int(dates.searchsorted(target, side='left'))
    return min(max(position, 0), len(dates) - 1)


def window_definitions(dates: Iterable[Any]) -> dict[str, dict[str, Any]]:
    index = pd.DatetimeIndex(pd.to_datetime(list(dates))).sort_values()
    if index.empty:
        raise ValueError('empty date index')
    last = index[-1]
    targets = {
        'all': index[0],
        '1y': last - pd.DateOffset(years=1),
        'ytd': pd.Timestamp(year=last.year, month=1, day=1),
        '3m': last - pd.DateOffset(months=3),
    }
    out: dict[str, dict[str, Any]] = {}
    for key in WINDOW_ORDER:
        start_index = _first_index(index, pd.Timestamp(targets[key]))
        out[key] = {
            'label': WINDOW_LABELS[key],
            'start': index[start_index].date().isoformat(),
            'end': last.date().isoformat(),
            'start_index': start_index,
            'end_index': len(index) - 1,
        }
    return out


def _status(excess_pp: float | None, participation: float | None) -> str:
    if excess_pp is None or participation is None:
        return '—'
    strong = excess_pp > 0
    broad = participation >= 0.5
    if strong and broad:
        return '强且广'
    if strong:
        return '强但窄'
    if broad:
        return '弱但广'
    return '弱且窄'


def _participation(
    panel: AlignedAssets,
    start_index: int,
    end_index: int,
) -> tuple[float | None, int, int]:
    valid = panel.has_price.iloc[start_index] & panel.has_price.iloc[end_index]
    if not bool(valid.any()):
        return None, 0, 0
    change = panel.relative.iloc[end_index] / panel.relative.iloc[start_index] - 1
    positive = valid & change.gt(0)
    valid_count = int(valid.sum())
    positive_count = int(positive.sum())
    return positive_count / valid_count, positive_count, valid_count


def _participation_20d(
    panel: AlignedAssets,
    end_index: int,
) -> tuple[float | None, int, int]:
    if end_index < 20:
        return None, 0, 0
    valid = panel.has_price.iloc[end_index - 20] & panel.has_price.iloc[end_index]
    if not bool(valid.any()):
        return None, 0, 0
    change = panel.relative.iloc[end_index] / panel.relative.iloc[end_index - 20] - 1
    positive = valid & change.gt(0)
    valid_count = int(valid.sum())
    positive_count = int(positive.sum())
    return positive_count / valid_count, positive_count, valid_count


def window_stats(
    nav: pd.DataFrame,
    panel: AlignedAssets,
    start_index: int,
    end_index: int,
    rs_20d: float | None = None,
) -> dict[str, Any]:
    basket_start = float(nav['basket'].iloc[start_index])
    basket_end = float(nav['basket'].iloc[end_index])
    benchmark_start = float(nav['benchmark'].iloc[start_index])
    benchmark_end = float(nav['benchmark'].iloc[end_index])
    if basket_start <= 0 or benchmark_start <= 0:
        raise ValueError('non-positive NAV base')

    return_pct = (basket_end / basket_start - 1) * 100
    benchmark_return_pct = (benchmark_end / benchmark_start - 1) * 100
    excess_pp = return_pct - benchmark_return_pct
    return_20d_pct = None
    if end_index >= 20:
        base = float(nav['basket'].iloc[end_index - 20])
        if base > 0:
            return_20d_pct = (basket_end / base - 1) * 100

    interval, interval_positive, interval_valid = _participation(
        panel, start_index, end_index,
    )
    participation_20d, positive_20d, valid_20d = _participation_20d(panel, end_index)
    return {
        'return_pct': float(return_pct),
        'return_20d_pct': None if return_20d_pct is None else float(return_20d_pct),
        'benchmark_return_pct': float(benchmark_return_pct),
        'excess_pp': float(excess_pp),
        'rs_20d_pct': rs_20d,
        'interval_participation': interval,
        'interval_positive': interval_positive,
        'interval_valid': interval_valid,
        'interval_total': len(panel.keys),
        'participation_20d': participation_20d,
        'participation_20d_positive': positive_20d,
        'participation_20d_valid': valid_20d,
        'participation_20d_total': len(panel.keys),
        'status': _status(excess_pp, interval),
    }


def metrics_for_windows(
    nav: pd.DataFrame,
    panel: AlignedAssets,
    windows: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    rs_20d = rs_20d_pct(nav)
    return {
        name: window_stats(
            nav, panel,
            int(definition['start_index']),
            int(definition['end_index']),
            rs_20d,
        )
        for name, definition in windows.items()
    }


def concentration(panel: AlignedAssets) -> dict[str, Any]:
    """Structural stock concentration using target weights, cash sleeve excluded."""
    target = panel.target_weights
    cash_keys = {key for key in panel.keys if panel.cash_until_first_close.get(key, False)}
    cash_weight = float(sum(target[key] for key in cash_keys))
    invested = {key: float(weight) for key, weight in target.items() if key not in cash_keys}
    stock_total = float(sum(invested.values()))
    if stock_total <= 0:
        raise ValueError('non-positive invested stock target weight')
    stock_weights = {key: float(weight / stock_total) for key, weight in invested.items()}
    largest_key = max(stock_weights, key=stock_weights.get)
    largest_row = next(
        row for row in panel.constituents
        if (str(row['market']), str(row['symbol'])) == largest_key
    )
    groups: dict[str, list[tuple[str, str]]] = {}
    for row in panel.constituents:
        groups.setdefault(str(row['group']), []).append(
            (str(row['market']), str(row['symbol']))
        )
    single_groups = [group for group, members in groups.items() if len(members) == 1]
    group_labels = {
        str(row['group']): str(row.get('group_label', row['group']))
        for row in panel.constituents
    }
    hhi = sum(weight * weight for weight in stock_weights.values())
    return {
        'stock_count': len(panel.keys),
        'invested_stock_count': len(invested),
        'cash_stock_count': len(cash_keys),
        'group_count': len(groups),
        'single_stock_groups': len(single_groups),
        'single_stock_group_keys': single_groups,
        'single_stock_group_labels': [group_labels.get(group, group) for group in single_groups],
        'cash_sleeve_weight': cash_weight,
        'max_target_weight': float(stock_weights[largest_key]),
        'largest_stock': {
            'market': largest_key[0],
            'symbol': largest_key[1],
            'name': str(largest_row.get('name', largest_key[1])),
        },
        'effective_stock_count': float(1 / hhi) if hhi > 0 else None,
        'top3_weight': float(sum(sorted(stock_weights.values(), reverse=True)[:3])),
    }


def participation_timeseries(panel: AlignedAssets) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for index, day in enumerate(panel.dates):
        participation, positive, valid = _participation_20d(panel, index)
        rows.append({
            'date': day.date().isoformat(),
            'participation_20d': participation,
            'positive_20d': positive,
            'valid_20d': valid,
            'total': len(panel.keys),
        })
    return pd.DataFrame(rows)


def overlap_diagnostics(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Shared-stock and pairwise overlap diagnostics across the observation baskets."""
    if not results:
        return {'shared_stocks': [], 'pairs': [], 'per_basket': {}, 'mainline_keys': []}
    name_by_key: dict[tuple[str, str], str] = {}
    by_stock: dict[tuple[str, str], list[dict[str, Any]]] = {}
    basket_keys: dict[str, set[tuple[str, str]]] = {}
    basket_weights: dict[str, dict[tuple[str, str], float]] = {}
    labels = {r['key']: r['label'] for r in results}
    roles = {r['key']: r.get('role', 'mainline') for r in results}

    for result in results:
        panel = result['panel']
        key = result['key']
        basket_keys[key] = set(panel.keys)
        basket_weights[key] = dict(panel.target_weights)
        for row in panel.constituents:
            identity = (str(row['market']), str(row['symbol']))
            name_by_key[identity] = str(row.get('name', identity[1]))
            by_stock.setdefault(identity, []).append({
                'key': key,
                'label': result['label'],
                'role': roles[key],
                'weight': float(panel.target_weights[identity]),
            })

    shared_stocks = []
    for identity, entries in by_stock.items():
        if len(entries) < 2:
            continue
        shared_stocks.append({
            'market': identity[0],
            'symbol': identity[1],
            'name': name_by_key.get(identity, identity[1]),
            'basket_count': len(entries),
            'baskets': entries,
            'total_weight': float(sum(entry['weight'] for entry in entries)),
        })
    shared_stocks.sort(key=lambda row: (-row['basket_count'], row['market'], row['symbol']))

    pairs = []
    keys = sorted(basket_keys)
    for i, key_a in enumerate(keys):
        for key_b in keys[i + 1:]:
            shared = basket_keys[key_a] & basket_keys[key_b]
            if not shared:
                continue
            weights_a = basket_weights[key_a]
            weights_b = basket_weights[key_b]
            pairs.append({
                'key_a': key_a,
                'key_b': key_b,
                'label_a': labels[key_a],
                'label_b': labels[key_b],
                'role_a': roles[key_a],
                'role_b': roles[key_b],
                'shared_count': len(shared),
                'shared_weight': float(sum(min(weights_a[k], weights_b[k]) for k in shared)),
                'share_a': float(sum(weights_a[k] for k in shared)),
                'share_b': float(sum(weights_b[k] for k in shared)),
                'symbols': sorted(name_by_key.get(k, k[1]) for k in shared),
            })
    pairs.sort(key=lambda row: (-row['shared_weight'], row['label_a'], row['label_b']))

    mainline_keys = [r['key'] for r in results if roles[r['key']] == 'mainline']
    per_basket: dict[str, dict[str, Any]] = {}
    for key in mainline_keys:
        own = basket_keys[key]
        other_keys = [other for other in mainline_keys if other != key]
        shared_any = set().union(*(basket_keys[other] for other in other_keys)) if other_keys else set()
        unique = own - shared_any
        own_weights = basket_weights[key]
        pair_rows = [row for row in pairs
                     if key in (row['key_a'], row['key_b'])
                     and row['role_a'] == 'mainline' and row['role_b'] == 'mainline']
        if pair_rows:
            strongest = max(
                pair_rows,
                key=lambda row: (row['shared_weight'],
                                 1 if key == row['key_a'] else 0),
            )
            other_key = strongest['key_b'] if strongest['key_a'] == key else strongest['key_a']
            other_label = labels[other_key]
            max_overlap_weight = strongest['shared_weight']
        else:
            other_key = None
            other_label = '—'
            max_overlap_weight = 0.0
        per_basket[key] = {
            'unique_weight': float(sum(own_weights[k] for k in unique)),
            'shared_weight': float(1 - sum(own_weights[k] for k in unique)),
            'max_overlap_key': other_key,
            'max_overlap_label': other_label,
            'max_overlap_weight': float(max_overlap_weight),
        }
    return {
        'shared_stocks': shared_stocks,
        'pairs': pairs,
        'per_basket': per_basket,
        'mainline_keys': mainline_keys,
    }
