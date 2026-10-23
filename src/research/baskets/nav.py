"""Comparable basket curves from an explicit reviewed adjusted-price snapshot.

Offline only. Original caches and legacy basket calculations are never mutated.
Group/asset target weights are fixed; late listings hold their assigned capital
as cash until the first observed closing price. Quarterly rebalancing resets the
same target weights at natural quarter-end sessions only. Values use local
currencies without FX conversion.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import pandas as pd


@dataclass(frozen=True)
class AlignedAssets:
    """One observation object shared by quarterly-rebalanced and buy-and-hold paths."""

    dates: pd.DatetimeIndex
    benchmark: pd.Series
    relative: pd.DataFrame
    has_price: pd.DataFrame
    constituents: tuple[dict[str, Any], ...]
    keys: tuple[tuple[str, str], ...]
    target_weights: dict[tuple[str, str], float]
    first_price_dates: dict[tuple[str, str], str]
    cash_until_first_close: dict[tuple[str, str], bool]
    group_sizes: dict[str, int]


@dataclass(frozen=True)
class NavResult:
    nav: pd.DataFrame
    weights: list[dict[str, Any]]
    rebalance_dates: tuple[pd.Timestamp, ...]
    rebalance_turnover: tuple[dict[str, Any], ...]
    ending_weights: dict[tuple[str, str], float]


def _normalise_date(value: Any) -> pd.Timestamp:
    return pd.Timestamp(value).normalize()


def _quarter_end_calendar_date(value: Any) -> pd.Timestamp:
    return _normalise_date(pd.Timestamp(value).to_period('Q').end_time)


def _target_weights(
    constituents: Iterable[dict[str, Any]],
    weighting_method: str,
) -> tuple[dict[tuple[str, str], float], dict[str, int]]:
    rows = list(constituents)
    if not rows:
        raise ValueError('no constituents')
    if weighting_method not in {'group_equal', 'asset_equal'}:
        raise ValueError(f'unsupported weighting: {weighting_method}')
    group_sizes: dict[str, int] = {}
    for row in rows:
        group = str(row['group'])
        group_sizes[group] = group_sizes.get(group, 0) + 1
    weights: dict[tuple[str, str], float] = {}
    for row in rows:
        key = (str(row['market']), str(row['symbol']))
        weight = (1 / len(rows) if weighting_method == 'asset_equal'
                  else 1 / len(group_sizes) / group_sizes[str(row['group'])])
        weights[key] = float(weight)
    if abs(sum(weights.values()) - 1) > 1e-10:
        raise ValueError('target weights do not sum to 1')
    return weights, group_sizes


def align_assets(
    prices: dict[tuple[str, str], pd.DataFrame],
    constituents: Iterable[dict[str, Any]],
    benchmark: pd.DataFrame,
    weighting_method: str = 'group_equal',
) -> AlignedAssets:
    """Align all constituents to the benchmark calendar once.

    The returned object is the single observation object used by both the
    quarterly-rebalanced and buy-and-hold paths. Before a constituent's first
    observed close, its relative value is cash (1.0); no synthetic price is made.
    """
    rows = tuple(dict(row) for row in constituents)
    if not rows:
        raise ValueError('no constituents')

    dates = pd.DatetimeIndex(pd.to_datetime(benchmark['date'])).sort_values()
    if dates.empty or dates.has_duplicates:
        raise ValueError('benchmark calendar empty or duplicated')
    dates = pd.DatetimeIndex(dates).normalize()

    base = benchmark.copy().set_index('date')['close']
    base.index = pd.DatetimeIndex(pd.to_datetime(base.index)).normalize()
    base = base.reindex(dates).astype(float)
    if base.isna().any() or (base <= 0).any():
        raise ValueError('invalid benchmark')
    benchmark_nav = base / base.iloc[0] * 100

    target_weights, group_sizes = _target_weights(rows, weighting_method)
    relative: dict[tuple[str, str], pd.Series] = {}
    has_price: dict[tuple[str, str], pd.Series] = {}
    first_price_dates: dict[tuple[str, str], str] = {}
    cash_until_first_close: dict[tuple[str, str], bool] = {}

    for row in rows:
        key = (str(row['market']), str(row['symbol']))
        if key not in prices:
            raise ValueError(f'missing prices: {key}')
        close = prices[key].set_index('date')['close'].sort_index().astype(float)
        close.index = pd.DatetimeIndex(pd.to_datetime(close.index)).normalize()
        if close.empty or close.index.has_duplicates or close.isna().any() or (close <= 0).any():
            raise ValueError(f'invalid prices: {key}')
        close = close[(close.index >= dates.min()) & (close.index <= dates.max())]
        if close.empty:
            raise ValueError(f'no prices in window: {key}')

        first = close.index.min()
        timeline = close.index.union(dates).sort_values()
        rel = (close / close.iloc[0]).reindex(timeline).ffill().reindex(dates)
        # Uninvested capital, never a synthetic pre-IPO price.
        rel.loc[dates < first] = 1.0
        if rel.isna().any():
            raise ValueError(f'unmarked prices: {key}')

        held = close.reindex(dates, method='ffill').notna()
        relative[key] = rel.astype(float)
        has_price[key] = held.astype(bool)
        first_price_dates[key] = str(first.date())
        cash_until_first_close[key] = bool(first > dates.min())

    relative_frame = pd.DataFrame(relative, index=dates)
    has_price_frame = pd.DataFrame(has_price, index=dates).fillna(False).astype(bool)
    return AlignedAssets(
        dates=dates,
        benchmark=benchmark_nav.astype(float),
        relative=relative_frame,
        has_price=has_price_frame,
        constituents=rows,
        keys=tuple((str(r['market']), str(r['symbol'])) for r in rows),
        target_weights=target_weights,
        first_price_dates=first_price_dates,
        cash_until_first_close=cash_until_first_close,
        group_sizes=group_sizes,
    )


def natural_quarter_ends(
    calendar_days: Iterable[Any],
    calendar_end: Any,
    start: Any,
    end: Any,
) -> tuple[pd.Timestamp, ...]:
    """Return natural quarter-end sessions that are confirmed by the HS300 calendar.

    A quarter is complete only when the calendar covers the calendar quarter-end
    date. If the data ends mid-quarter, the last data date is never treated as a
    rebalance date. A rebalance exactly on ``start`` is omitted because the initial
    target weights are already in place.
    """
    start_ts, end_ts = _normalise_date(start), _normalise_date(end)
    calendar_end_ts = _normalise_date(calendar_end)
    if calendar_end_ts < _quarter_end_calendar_date(end_ts):
        raise ValueError('calendar does not cover the current natural quarter')

    days = sorted({_normalise_date(d) for d in calendar_days})
    quarters: dict[tuple[int, int], list[pd.Timestamp]] = {}
    for day in days:
        key = (day.year, (day.month - 1) // 3 + 1)
        quarters.setdefault(key, []).append(day)

    out: list[pd.Timestamp] = []
    for quarter_days in quarters.values():
        natural_end = max(quarter_days)
        if start_ts < natural_end <= end_ts:
            out.append(natural_end)
    return tuple(sorted(out))


def nav_from_panel(
    panel: AlignedAssets,
    rebalance: str | None = None,
    calendar_days: Iterable[Any] | None = None,
    calendar_end: Any | None = None,
) -> NavResult:
    """Run a fixed-target-weight path with optional natural quarter-end rebalancing."""
    if rebalance not in {None, 'quarterly'}:
        raise ValueError(f'unsupported rebalance: {rebalance}')
    rebalance_dates: tuple[pd.Timestamp, ...] = ()
    if rebalance == 'quarterly':
        if calendar_days is None or calendar_end is None:
            raise ValueError('quarterly rebalance requires the confirmed HS300 calendar')
        rebalance_dates = natural_quarter_ends(
            calendar_days, calendar_end, panel.dates[0], panel.dates[-1],
        )
        missing = [d for d in rebalance_dates if d not in panel.dates]
        if missing:
            raise ValueError(f'rebalance dates missing from benchmark calendar: {missing}')

    keys = panel.keys
    target = panel.target_weights
    current = dict(target)
    nav_values = [100.0]
    contributions = {key: 0.0 for key in keys}
    rebalance_rows: list[dict[str, Any]] = []
    rebalance_set = set(rebalance_dates)
    daily_returns = panel.relative.pct_change().fillna(0.0)

    for i in range(1, len(panel.dates)):
        day = panel.dates[i]
        returns = daily_returns.iloc[i]
        nav_prev = nav_values[-1]
        portfolio_return = sum(current[key] * float(returns[key]) for key in keys)
        if 1 + portfolio_return <= 0:
            raise ValueError('portfolio return at or below -100%')
        nav_new = nav_prev * (1 + portfolio_return)
        for key in keys:
            contributions[key] += current[key] * float(returns[key]) * nav_prev
        nav_values.append(nav_new)

        current = {
            key: current[key] * (1 + float(returns[key])) / (1 + portfolio_return)
            for key in keys
        }

        if day in rebalance_set:
            turnover = 0.5 * sum(abs(target[key] - current[key]) for key in keys)
            rebalance_rows.append({
                'date': day.date().isoformat(),
                'turnover': float(turnover),
                'assets': len(keys),
            })
            current = dict(target)

    nav = pd.DataFrame(
        {'basket': nav_values, 'benchmark': panel.benchmark.to_numpy()},
        index=panel.dates,
    )
    nav.index.name = 'date'
    if abs(nav['basket'].iloc[0] - 100) > 1e-8:
        raise ValueError('NAV does not start at 100')

    total_contribution = sum(contributions.values())
    if abs(total_contribution - (nav['basket'].iloc[-1] - 100)) > 1e-7:
        raise ValueError('NAV/contribution reconciliation failed')

    weights: list[dict[str, Any]] = []
    for row in panel.constituents:
        key = (str(row['market']), str(row['symbol']))
        weights.append({
            **row,
            'initial_weight': target[key],
            'target_weight': target[key],
            'ending_weight': float(current[key]),
            'first_price_date': panel.first_price_dates[key],
            'cash_until_first_close': panel.cash_until_first_close[key],
            'contribution_pp': float(contributions[key] / nav_values[0] * 100),
        })
    return NavResult(
        nav=nav,
        weights=weights,
        rebalance_dates=rebalance_dates,
        rebalance_turnover=tuple(rebalance_rows),
        ending_weights={key: float(current[key]) for key in keys},
    )


def fixed_weight_nav(
    prices: dict[tuple[str, str], pd.DataFrame],
    constituents: Iterable[dict[str, Any]],
    benchmark: pd.DataFrame,
    weighting_method: str = 'group_equal',
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Buy-and-hold comparison path using the shared observation object."""
    panel = align_assets(prices, constituents, benchmark, weighting_method)
    result = nav_from_panel(panel, rebalance=None)
    return result.nav, result.weights


def quarterly_rebalanced_nav(
    prices: dict[tuple[str, str], pd.DataFrame],
    constituents: Iterable[dict[str, Any]],
    benchmark: pd.DataFrame,
    weighting_method: str = 'group_equal',
    calendar_days: Iterable[Any] | None = None,
    calendar_end: Any | None = None,
) -> NavResult:
    """Default structural path: fixed target weights, rebalanced at natural quarter ends."""
    panel = align_assets(prices, constituents, benchmark, weighting_method)
    return nav_from_panel(
        panel,
        rebalance='quarterly',
        calendar_days=calendar_days,
        calendar_end=calendar_end,
    )
