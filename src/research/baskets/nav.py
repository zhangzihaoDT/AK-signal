"""Comparable basket curves from an explicit reviewed adjusted-price snapshot.

Offline only. Original caches and legacy basket calculations are never mutated.
Fixed group/asset weights; late listings hold their assigned capital as cash until
first observed closing price. Values use local currencies without FX conversion.
"""
from __future__ import annotations


import pandas as pd



def fixed_weight_nav(prices, constituents, benchmark, weighting_method='group_equal'):
    """Return NAV and fixed initial weights; no implicit reweighting at IPO dates."""
    if weighting_method not in {'group_equal', 'asset_equal'}:
        raise ValueError(f'unsupported weighting: {weighting_method}')
    dates = pd.DatetimeIndex(pd.to_datetime(benchmark['date'])).sort_values()
    if dates.empty or dates.has_duplicates:
        raise ValueError('benchmark calendar empty or duplicated')
    group_sizes = {}
    for a in constituents:
        group_sizes[a['group']] = group_sizes.get(a['group'], 0) + 1
    weights, curves = [], []
    for a in constituents:
        key = (a['market'], a['symbol'])
        close = prices[key].set_index('date')['close'].sort_index().astype(float)
        close.index = pd.DatetimeIndex(pd.to_datetime(close.index))
        if close.empty or close.index.has_duplicates or close.isna().any() or (close <= 0).any():
            raise ValueError(f'invalid prices: {key}')
        close = close[(close.index >= dates.min()) & (close.index <= dates.max())]
        if close.empty:
            raise ValueError(f'no prices in window: {key}')
        weight = (1 / len(constituents) if weighting_method == 'asset_equal'
                  else 1 / len(group_sizes) / group_sizes[a['group']])
        # Include HK-only trading sessions before carrying marks onto HS300 dates.
        timeline = close.index.union(dates).sort_values()
        relative = (close / close.iloc[0]).reindex(timeline).ffill().reindex(dates)
        # This represents uninvested capital, never a synthetic pre-IPO price.
        relative.loc[dates < close.index.min()] = 1.0
        if relative.isna().any():
            raise ValueError(f'unmarked prices: {key}')
        curves.append(relative * weight)
        weights.append({**a, 'initial_weight': weight,
                        'first_price_date': str(close.index.min().date()),
                        'cash_until_first_close': bool(close.index.min() > dates.min()),
                        'contribution_pp': float((relative.iloc[-1] - 1) * weight * 100)})
    nav = pd.DataFrame({'basket': sum(curves) * 100}, index=dates)
    base = benchmark.copy().set_index('date')['close']
    base.index = pd.DatetimeIndex(pd.to_datetime(base.index))
    base = base.reindex(dates).astype(float)
    if base.isna().any() or (base <= 0).any():
        raise ValueError('invalid benchmark')
    nav['benchmark'] = base / base.iloc[0] * 100
    nav.index.name = 'date'
    assert abs(sum(a['initial_weight'] for a in weights) - 1) < 1e-10
    return nav, weights
