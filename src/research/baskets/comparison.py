"""Explicit Longbridge-only basket comparison, separate from daily stock metrics."""
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd

from src.common.paths import project_root
from src.market_facts.contract import build_snapshot, validate_snapshot
from src.market_facts.longbridge_source import quote_context, identity_for
from src.market_facts.policy import excluded_stock
from src.trend_engine.asset import Asset
from .config import load_baskets, expand_constituents, basket_config_hash
from .metrics import (
    WINDOW_ORDER,
    concentration,
    metrics_for_windows,
    overlap_diagnostics,
    participation_timeseries,
    window_definitions,
)
from .nav import align_assets, nav_from_panel


METHOD_QUARTERLY = 'quarterly'
METHOD_BUY_AND_HOLD = 'buy_and_hold'
METHODS = (METHOD_QUARTERLY, METHOD_BUY_AND_HOLD)
DEFAULT_METHOD = METHOD_QUARTERLY
METHODOLOGY_VERSION = 'basket_compare_v2'


def comparison_baskets():
    raw = load_baskets()
    baskets = {key: {**basket, 'role': basket.get('role', 'mainline')}
               for key, basket in raw.items()}
    baskets['high_cashflow'] = {
        'label': '高现金流资产',
        'role': 'reference',
        'source': {
            'type': 'theme',
            'key': 'high_cashflow',
            'include_groups': [
                'hydro_nuclear', 'telecom_operator', 'toll_road',
                'port_operator', 'cyclical_power_watch',
            ],
        },
        'weighting': {'method': 'group_equal', 'rebalance': 'quarterly'},
    }
    return baskets


def constituents(basket):
    members = []
    excluded = []
    for asset in expand_constituents(basket):
        asset = {**asset, 'symbol': asset['symbol'].zfill(5 if asset['market'] == 'HK' else 6)}
        (excluded if excluded_stock(asset['market'], asset['symbol']) else members).append(asset)
    if not members:
        raise ValueError('No included members')
    return members, excluded


def fetch_prices(start, end):
    """Immutable close-only projection; never certifies or repairs supplier OHLC."""
    from longbridge.openapi import Period, AdjustType
    from src.selection.universe import _infer_exchange
    ctx = quote_context(project_root() / '.env')
    members = {}
    for key, basket in comparison_baskets().items():
        for asset in constituents({**basket, 'key': key})[0]:
            members[(asset['market'], asset['symbol'])] = asset
    folder = project_root() / 'data/sources/longbridge/raw/baskets' / end.replace('-', '') / uuid4().hex
    folder.mkdir(parents=True)
    records = []
    requests = [(market, symbol, asset['name'], False)
                for (market, symbol), asset in members.items()] + [('CN', '000300', '沪深300', True)]
    for market, symbol, name, benchmark in requests:
        identity = identity_for(Asset(
            symbol, name, market,
            exchange='SSE' if benchmark else 'HKEX' if market == 'HK' else _infer_exchange(symbol),
            currency='HKD' if market == 'HK' else 'CNY',
        ))
        bars = ctx.history_candlesticks_by_date(
            identity.provider_symbol, Period.Day,
            AdjustType.NoAdjust if benchmark else AdjustType.ForwardAdjust,
            date.fromisoformat(start), date.fromisoformat(end),
        )
        tz = ZoneInfo('Asia/Hong_Kong' if market == 'HK' else 'Asia/Shanghai')
        rows = [{
            'source_trade_date': bar.timestamp.astimezone(tz).date().isoformat(),
            'source_timestamp': bar.timestamp.astimezone(tz).isoformat(),
            'date_evidence': 'source_dated_daily',
            'bar_kind': 'close_only',
            'open': None, 'high': None, 'low': None,
            'close': float(bar.close), 'volume': float(bar.volume),
            'turnover': float(bar.turnover),
        } for bar in bars]
        if benchmark:
            from dataclasses import replace
            identity = replace(identity, asset_type='index', price_basis='index_level')
        snapshot = build_snapshot(identity, rows, date.fromisoformat(end), datetime.now(tz))
        if not snapshot['quality']['target_source_date_verified']:
            raise ValueError(f'Missing target date: {market} {symbol}')
        filename = 'benchmark.json' if benchmark else f'{market}_{symbol}.json'
        (folder / filename).write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))
        records.append(dict(
            market=market, symbol=symbol, name=name, benchmark=benchmark,
            file=filename, snapshot_id=snapshot['snapshot_id'],
        ))
        print(f'Longbridge {len(records)}/{len(requests)} {market} {symbol}', flush=True)
        time.sleep(.6)
    (folder / 'manifest.json').write_text(json.dumps(dict(
        start=start, end=end, source='longbridge',
        price_basis='Longbridge ForwardAdjust; benchmark NoAdjust',
        projection='source close only; OHLC not used or repaired',
        records=records,
    ), ensure_ascii=False, indent=2))
    return folder


def _hash_json(value) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _validate_pair(quarterly, buy_and_hold, panel) -> None:
    """QR and BH must share the same observation object; only rebalancing differs."""
    if not quarterly.nav.index.equals(buy_and_hold.nav.index):
        raise ValueError('QR/BH date index mismatch')
    if not quarterly.nav['benchmark'].equals(buy_and_hold.nav['benchmark']):
        raise ValueError('QR/BH benchmark mismatch')
    q_weights = {key: row['initial_weight'] for key, row in _weight_map(quarterly.weights).items()}
    b_weights = {key: row['initial_weight'] for key, row in _weight_map(buy_and_hold.weights).items()}
    if q_weights != b_weights:
        raise ValueError('QR/BH initial weight mismatch')
    if set(q_weights) != set(panel.keys):
        raise ValueError('QR/BH constituent mismatch')
    q_dates = {key: row['first_price_date'] for key, row in _weight_map(quarterly.weights).items()}
    b_dates = {key: row['first_price_date'] for key, row in _weight_map(buy_and_hold.weights).items()}
    if q_dates != b_dates:
        raise ValueError('QR/BH first-price date mismatch')
    q_cash = {key: row['cash_until_first_close'] for key, row in _weight_map(quarterly.weights).items()}
    b_cash = {key: row['cash_until_first_close'] for key, row in _weight_map(buy_and_hold.weights).items()}
    if q_cash != b_cash:
        raise ValueError('QR/BH cash-rule mismatch')


def _weight_map(weights):
    return {
        (str(row['market']), str(row['symbol'])): row
        for row in weights
    }


def _assign_ranks(results) -> None:
    mainlines = [row for row in results if row.get('role') == 'mainline']
    for method in METHODS:
        for window in WINDOW_ORDER:
            values = pd.Series({
                row['key']: row[f'metrics_{method}'][window]['excess_pp']
                for row in mainlines
            }, dtype=float)
            ranks = values.rank(method='min', ascending=False)
            for row in mainlines:
                value = ranks.get(row['key'])
                row[f'metrics_{method}'][window]['rank'] = None if pd.isna(value) else int(value)
            for row in results:
                if row.get('role') != 'mainline':
                    row[f'metrics_{method}'][window]['rank'] = None


def _metrics_rows(results, windows) -> list[dict]:
    rows = []
    for result in results:
        for method in METHODS:
            for window in WINDOW_ORDER:
                stats = result[f'metrics_{method}'][window]
                rows.append({
                    'key': result['key'],
                    'label': result['label'],
                    'role': result['role'],
                    'method': method,
                    'window': window,
                    'start': windows[window]['start'],
                    'end': windows[window]['end'],
                    **stats,
                })
    return rows


def _weights_frame(weights) -> pd.DataFrame:
    columns = [
        'market', 'symbol', 'name', 'group', 'group_label',
        'initial_weight', 'target_weight', 'ending_weight',
        'first_price_date', 'cash_until_first_close', 'contribution_pp',
    ]
    frame = pd.DataFrame(weights)
    for column in columns:
        if column not in frame:
            frame[column] = None
    return frame[columns]


def build_longbridge_comparison(snapshot, output, start, end):
    output = Path(output)
    snapshot = Path(snapshot)
    manifest = json.loads((snapshot / 'manifest.json').read_text())
    if start < manifest['start'] or end > manifest['end'] or manifest['source'] != 'longbridge':
        raise ValueError('Snapshot range/source mismatch')

    prices = {}
    benchmark = None
    benchmark_calendar = None
    calendar_audit = []
    for record in manifest['records']:
        payload = validate_snapshot(json.loads((snapshot / record['file']).read_text()))
        if payload['identity']['provider'] != 'longbridge' or payload['snapshot_id'] != record['snapshot_id']:
            raise ValueError('Invalid Longbridge snapshot identity/hash')
        from src.market_facts.calendar import SessionCalendar
        calendar = SessionCalendar.load(
            project_root() / f"config/market_calendars/{'XHKG' if record['market'] == 'HK' else 'XSHG'}.json"
        )
        assessment = calendar.assess(payload)
        calendar_audit.append(dict(
            market=record['market'], symbol=record['symbol'], benchmark=record['benchmark'],
            missing_sessions=assessment['missing_sessions'],
            unexpected_sessions=assessment['unexpected_sessions'],
            calendar_authority=assessment['calendar_authority'],
            calendar_sha256=assessment['calendar_sha256'],
        ))
        if record['benchmark'] and (assessment['missing_sessions'] or assessment['unexpected_sessions']):
            raise ValueError('Benchmark calendar gap')
        frame = pd.DataFrame(payload['bars'])
        frame['date'] = pd.to_datetime(frame.source_trade_date)
        frame = frame[(frame.date >= start) & (frame.date <= end)][['date', 'close']]
        frame = frame.sort_values('date').reset_index(drop=True)
        if frame.empty or frame.date.max() != pd.Timestamp(end):
            raise ValueError('Stale prices')
        if record['benchmark']:
            benchmark = frame
            benchmark_calendar = calendar
        else:
            prices[(record['market'], record['symbol'])] = frame

    if benchmark is None or benchmark.date.min() != pd.Timestamp(start):
        raise ValueError('Benchmark boundary mismatch')
    if benchmark_calendar is None:
        raise ValueError('Benchmark calendar missing')

    # Late listing cash is permitted only for the documented IPO, never for arbitrary missing history.
    for identity, frame in prices.items():
        if frame.date.min() > pd.Timestamp(start) and identity != ('CN', '688449'):
            raise ValueError(f'Unexplained late history: {identity}')

    windows = window_definitions(benchmark['date'])
    results = []
    panels = []
    exclusions = []
    for key, basket in comparison_baskets().items():
        members, excluded = constituents({**basket, 'key': key})
        exclusions.extend(excluded)
        panel = align_assets(prices, members, benchmark, basket['weighting']['method'])
        quarterly = nav_from_panel(
            panel,
            rebalance='quarterly',
            calendar_days=benchmark_calendar.days,
            calendar_end=benchmark_calendar.end,
        )
        buy_and_hold = nav_from_panel(panel, rebalance=None)
        _validate_pair(quarterly, buy_and_hold, panel)

        metrics_quarterly = metrics_for_windows(quarterly.nav, panel, windows)
        metrics_buy_and_hold = metrics_for_windows(buy_and_hold.nav, panel, windows)
        result = {
            'key': key,
            'label': basket['label'],
            'role': basket.get('role', 'mainline'),
            'config_hash': basket_config_hash({k: v for k, v in basket.items() if k != 'role'}),
            'nav': quarterly.nav,
            'nav_quarterly': quarterly.nav,
            'nav_buy_and_hold': buy_and_hold.nav,
            'weights': quarterly.weights,
            'weights_quarterly': quarterly.weights,
            'weights_buy_and_hold': buy_and_hold.weights,
            'metrics': metrics_quarterly,
            'metrics_quarterly': metrics_quarterly,
            'metrics_buy_and_hold': metrics_buy_and_hold,
            'rebalance_dates': [day.date().isoformat() for day in quarterly.rebalance_dates],
            'rebalance_turnover': list(quarterly.rebalance_turnover),
            'concentration': concentration(panel),
            'panel': panel,
        }
        results.append(result)
        panels.append({
            'key': key,
            'label': basket['label'],
            'role': result['role'],
            'panel': panel,
        })

    _assign_ranks(results)
    overlap = overlap_diagnostics(panels)
    output.mkdir(parents=True, exist_ok=True)

    wide_quarterly = pd.DataFrame(
        {result['key']: result['nav_quarterly']['basket'] for result in results}
    )
    wide_quarterly['hs300'] = benchmark.set_index('date').close / benchmark.close.iloc[0] * 100
    wide_buy_and_hold = pd.DataFrame(
        {result['key']: result['nav_buy_and_hold']['basket'] for result in results}
    )
    wide_buy_and_hold['hs300'] = wide_quarterly['hs300']
    wide_quarterly.to_csv(output / 'comparison_nav.csv')
    wide_buy_and_hold.to_csv(output / 'comparison_nav_bh.csv')

    for result in results:
        result['nav_quarterly'].to_csv(output / f"{result['key']}_nav.csv")
        result['nav_buy_and_hold'].to_csv(output / f"{result['key']}_nav_bh.csv")
        _weights_frame(result['weights_quarterly']).to_csv(output / f"{result['key']}_weights.csv", index=False)
        _weights_frame(result['weights_buy_and_hold']).to_csv(output / f"{result['key']}_weights_bh.csv", index=False)

    metrics_frame = pd.DataFrame(_metrics_rows(results, windows))
    metrics_frame.to_csv(output / 'basket_metrics.csv', index=False)

    participation_frames = []
    for result in results:
        frame = participation_timeseries(result['panel'])
        frame.insert(0, 'key', result['key'])
        frame.insert(1, 'label', result['label'])
        frame.insert(2, 'role', result['role'])
        participation_frames.append(frame)
    participation_frame = pd.concat(participation_frames, ignore_index=True) if participation_frames else pd.DataFrame()
    participation_frame.to_csv(output / 'basket_participation.csv', index=False)

    overlap_frame = pd.DataFrame(overlap['pairs'])
    if not overlap_frame.empty:
        overlap_frame['symbols'] = overlap_frame['symbols'].apply(lambda values: '|'.join(values))
    overlap_frame.to_csv(output / 'basket_overlap.csv', index=False)
    shared_frame = pd.DataFrame(overlap['shared_stocks'])
    if not shared_frame.empty:
        shared_frame['baskets'] = shared_frame['baskets'].apply(
            lambda entries: '|'.join(
                f"{entry['label']}:{entry['weight'] * 100:.2f}%" for entry in entries
            )
        )
    shared_frame.to_csv(output / 'basket_shared_stocks.csv', index=False)

    control = {
        'start': start,
        'end': end,
        'difference': 'quarterly_rebalance_only',
        'cash_rule': 'prelisting_cash_until_first_close',
        'calendar_sha256': benchmark_calendar.digest,
        'snapshot_manifest_sha256': hashlib.sha256((snapshot / 'manifest.json').read_bytes()).hexdigest(),
        'constituents_sha256': _hash_json({
            result['key']: sorted([f'{market}:{symbol}' for market, symbol in result['panel'].keys])
            for result in results
        }),
        'initial_weights_sha256': _hash_json({
            result['key']: {
                f'{market}:{symbol}': round(weight, 12)
                for (market, symbol), weight in result['panel'].target_weights.items()
            }
            for result in results
        }),
        'validated': True,
    }

    receipt = {
        'start': start,
        'end': end,
        'rows': len(wide_quarterly),
        'source': 'longbridge',
        'source_snapshot': str(snapshot),
        'methodology_version': METHODOLOGY_VERSION,
        'methods': list(METHODS),
        'default_method': DEFAULT_METHOD,
        'windows': windows,
        'rebalance_rule': {
            'frequency': 'quarterly',
            'timing': 'last confirmed HS300 session of Mar/Jun/Sep/Dec',
            'effective': 'next session',
            'costs': 'not deducted',
            'cash_sleeve': 'rebalanced to target',
            'incomplete_quarter': 'no rebalance at the last data date',
        },
        'participation_definition': {
            'interval': 'positive stock return over selected window / valid stocks',
            'twenty_day': 'positive stock return over last 20 HS300 sessions / valid stocks',
            'weighting': 'stock-count equal, independent of target weights',
            'late_listing': 'excluded until enough observed closes',
        },
        'concentration_definition': {
            'basis': 'full-window initial target weights, cash sleeve excluded and stock weights renormalized',
            'effective_stock_count': '1 / sum(w_i^2)',
            'cash_sleeve': 'reported separately, not included in stock HHI',
        },
        'overlap_definition': {
            'shared_stock': 'same normalized market/symbol in two or more baskets',
            'pair_weight': 'sum(min(weight_a, weight_b)) over shared stocks',
            'effect': 'diagnostic only; weights are not adjusted',
        },
        'comparison_control': control,
        'calendar_audit': calendar_audit,
        'excluded': exclusions,
        'price_policy': 'Longbridge ForwardAdjust close-only projection; local currencies, no FX/costs; not verified total return.',
        'admission_policy': 'Excluded assets removed before group/asset equal target weights; documented late IPO holds target capital as cash until first close; QR rebalances cash sleeve to target, BH does not rebalance.',
        'calendar_policy': 'HS300 dates; last actual HK close carried for valuation, no synthetic source bars; quarterly rebalance only at calendar-confirmed natural quarter ends.',
        'snapshot_manifest_sha256': control['snapshot_manifest_sha256'],
        'data_sha256': hashlib.sha256((output / 'comparison_nav.csv').read_bytes()).hexdigest(),
        'data_sha256_bh': hashlib.sha256((output / 'comparison_nav_bh.csv').read_bytes()).hexdigest(),
        'metrics_sha256': hashlib.sha256((output / 'basket_metrics.csv').read_bytes()).hexdigest(),
        'participation_sha256': hashlib.sha256((output / 'basket_participation.csv').read_bytes()).hexdigest(),
        'overlap_sha256': hashlib.sha256((output / 'basket_overlap.csv').read_bytes()).hexdigest(),
        'shared_stocks_sha256': hashlib.sha256((output / 'basket_shared_stocks.csv').read_bytes()).hexdigest(),
        'overlap': overlap,
        'baskets': [{
            'key': result['key'],
            'label': result['label'],
            'role': result['role'],
            'config_hash': result['config_hash'],
            'metrics_quarterly': result['metrics_quarterly'],
            'metrics_buy_and_hold': result['metrics_buy_and_hold'],
            'weights_quarterly': result['weights_quarterly'],
            'weights_buy_and_hold': result['weights_buy_and_hold'],
            'concentration': result['concentration'],
            'rebalance_dates': result['rebalance_dates'],
            'rebalance_turnover': result['rebalance_turnover'],
        } for result in results],
    }
    (output / 'comparison_manifest.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    return results, receipt
