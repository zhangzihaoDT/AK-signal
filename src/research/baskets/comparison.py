"""Comparable basket curves from an explicit reviewed adjusted-price snapshot.

Offline only. Original caches and legacy basket calculations are never mutated.
Fixed group/asset weights; late listings hold their assigned capital as cash until
first observed closing price. Values use local currencies without FX conversion.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from .config import basket_config_hash, expand_constituents, load_baskets
from src.common.paths import raw_dir


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


def build_comparison(snapshot: Path, output: Path, start: str, end: str):
    """Validate snapshot receipts, calculate curves, save reviewable data + manifest."""
    snapshot, output = Path(snapshot), Path(output)
    manifest = json.loads((snapshot / 'manifest.json').read_text())
    if manifest['errors']:
        raise ValueError('snapshot has unresolved fetch errors')
    if start < manifest['start'] or end > manifest['end']:
        raise ValueError('requested window outside reviewed snapshot')
    lookup = {(r['market'], r['symbol']): r for r in manifest['assets']}
    benchmark_path = raw_dir() / '_benchmark_sh000300.csv'
    benchmark = pd.read_csv(benchmark_path, parse_dates=['date'])
    benchmark = benchmark[(benchmark.date >= start) & (benchmark.date <= end)].copy()
    if benchmark.empty or str(benchmark.date.min().date()) != start or str(benchmark.date.max().date()) != end:
        raise ValueError('benchmark does not cover requested boundaries')
    baskets = load_baskets()
    prices = {}
    for b in baskets.values():
        for a in expand_constituents(b):
            key = (a['market'], a['symbol'])
            if key in prices:
                continue
            rec = lookup[key]
            path = snapshot / f'{key[0]}_{key[1]}.csv'
            if hashlib.sha256(path.read_bytes()).hexdigest() != rec['sha256']:
                raise ValueError(f'snapshot hash mismatch: {key}')
            df = pd.read_csv(path, parse_dates=['date'])
            df = df[(df.date >= start) & (df.date <= end)].copy()
            if df.empty or str(df.date.max().date()) != end:
                raise ValueError(f'stale constituent: {key}')
            # No arbitrary price-gap filling: adjusted references must contain every
            # existing raw trading bar. Disclosed suspension bars are absent in both.
            raw = pd.read_csv(raw_dir() / f'{key[0]}_{key[1]}.csv', parse_dates=['date'])
            raw = raw[(raw.date >= start) & (raw.date <= end)]
            if set(raw.date) - set(df.date):
                raise ValueError(f'reference missing known bars: {key}')
            prices[key] = df
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for key, b in baskets.items():
        constituents = expand_constituents(b)
        nav, weights = fixed_weight_nav(prices, constituents, benchmark, b['weighting']['method'])
        if abs(nav.basket.iloc[0] - 100) > 1e-8:
            raise ValueError(f'initial NAV not 100: {key}')
        total = float(nav.basket.iloc[-1] - 100)
        if abs(sum(a['contribution_pp'] for a in weights) - total) > 1e-8:
            raise ValueError(f'contributions do not reconcile: {key}')
        metrics = {'return_pct': total, 'benchmark_return_pct': float(nav.benchmark.iloc[-1] - 100),
                   'excess_pp': float(nav.basket.iloc[-1] - nav.benchmark.iloc[-1]),
                   'max_drawdown_pct': float((nav.basket / nav.basket.cummax() - 1).min() * 100),
                   'assets': len(weights), 'start': start, 'end': end}
        nav.to_csv(output / f'{key}_nav.csv')
        pd.DataFrame(weights).to_csv(output / f'{key}_weights.csv', index=False)
        results.append({'key': key, 'label': b['label'], 'metrics': metrics,
                        'weights': weights, 'config_hash': basket_config_hash(b), 'nav': nav})
    wide = pd.DataFrame({r['key']: r['nav'].basket for r in results})
    wide['hs300'] = results[0]['nav'].benchmark
    wide.to_csv(output / 'comparison_nav.csv')
    receipt = {'start': start, 'end': end, 'rows': len(wide),
               'price_policy': 'Current supplier qfq price performance; local currency, no FX, no costs; not verified total return or PIT backtest.',
               'admission_policy': 'Fixed group/asset initial capital, cash before listing, invest at first close; then buy and hold.',
               'calendar_policy': 'HS300 dates; use last actual HK close including HK-only sessions. No synthetic raw bars.',
               'benchmark_sha256': hashlib.sha256(benchmark_path.read_bytes()).hexdigest(),
               'snapshot_manifest_sha256': hashlib.sha256((snapshot / 'manifest.json').read_bytes()).hexdigest(),
               'data_sha256': hashlib.sha256((output / 'comparison_nav.csv').read_bytes()).hexdigest(),
               'baskets': [{k: v for k, v in r.items() if k != 'nav'} for r in results]}
    (output / 'comparison_manifest.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    return results, receipt
