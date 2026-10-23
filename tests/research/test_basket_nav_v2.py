import pandas as pd
import pytest

from src.research.baskets import nav
from src.research.baskets.metrics import (
    concentration,
    metrics_for_windows,
    overlap_diagnostics,
    participation_timeseries,
    rs_20d_pct,
    window_definitions,
)


def frame(dates, values):
    return pd.DataFrame({'date': pd.to_datetime(dates), 'close': values})


def basket_assets(groups):
    return [
        {'market': 'CN', 'symbol': symbol, 'name': symbol, 'group': group}
        for symbol, group in groups
    ]


def test_natural_quarter_ends_do_not_use_mid_quarter_data_end():
    days = [
        '2026-06-30', '2026-07-01', '2026-09-30', '2026-10-01',
        '2026-10-15', '2026-12-31',
    ]
    assert nav.natural_quarter_ends(days, '2026-12-31', '2026-07-01', '2026-10-15') == (
        pd.Timestamp('2026-09-30'),
    )
    assert nav.natural_quarter_ends(days, '2026-12-31', '2026-07-01', '2026-09-30') == (
        pd.Timestamp('2026-09-30'),
    )
    assert nav.natural_quarter_ends(days, '2026-12-31', '2026-07-01', '2026-12-31') == (
        pd.Timestamp('2026-09-30'),
        pd.Timestamp('2026-12-31'),
    )
    # Initial target weights are already in place when the window starts on a quarter-end.
    assert nav.natural_quarter_ends(days, '2026-12-31', '2026-09-30', '2026-10-15') == ()


def test_natural_quarter_ends_require_calendar_coverage():
    with pytest.raises(ValueError, match='calendar does not cover'):
        nav.natural_quarter_ends(
            ['2026-09-30', '2026-10-15'], '2026-10-31', '2026-07-01', '2026-10-15',
        )


def test_natural_quarter_ends_use_real_hs300_calendar():
    from src.common.paths import project_root
    from src.market_facts.calendar import SessionCalendar
    calendar = SessionCalendar.load(project_root() / 'config/market_calendars/XSHG.json')
    ends = nav.natural_quarter_ends(
        calendar.days, calendar.end, '2024-07-16', '2026-10-15',
    )
    assert ends[-1] == pd.Timestamp('2026-09-30')
    assert pd.Timestamp('2026-10-15') not in ends
    assert len(ends) == 9


def test_quarterly_rebalance_resets_weights_and_keeps_nav_continuous():
    dates = ['2025-03-28', '2025-03-31', '2025-04-01', '2025-04-02', '2025-06-30', '2025-07-01']
    prices = {
        ('CN', 'A'): frame(dates, [100, 100, 100, 200, 200, 200]),
        ('CN', 'B'): frame(dates, [100, 100, 100, 100, 100, 100]),
    }
    benchmark = frame(dates, [100] * len(dates))
    assets = basket_assets([('A', 'g'), ('B', 'g')])
    panel = nav.align_assets(prices, assets, benchmark)
    quarterly = nav.nav_from_panel(
        panel, rebalance='quarterly',
        calendar_days=['2025-03-31', '2025-06-30', '2025-09-30', '2025-12-31'],
        calendar_end='2025-12-31',
    )
    buy_and_hold = nav.nav_from_panel(panel, rebalance=None)

    assert quarterly.rebalance_dates == (pd.Timestamp('2025-03-31'), pd.Timestamp('2025-06-30'))
    assert quarterly.nav['basket'].tolist() == [100, 100, 100, 150, 150, 150]
    assert buy_and_hold.nav['basket'].tolist() == [100, 100, 100, 150, 150, 150]
    assert quarterly.ending_weights[('CN', 'A')] == pytest.approx(0.5)
    assert buy_and_hold.ending_weights[('CN', 'A')] == pytest.approx(2 / 3)
    assert quarterly.rebalance_turnover[0]['turnover'] == pytest.approx(0.0)
    assert quarterly.rebalance_turnover[1]['turnover'] == pytest.approx(1 / 6)
    assert sum(row['contribution_pp'] for row in quarterly.weights) == pytest.approx(50.0)
    assert sum(row['contribution_pp'] for row in buy_and_hold.weights) == pytest.approx(50.0)


def test_qr_and_bh_share_dates_benchmark_and_initial_weights():
    dates = pd.bdate_range('2025-03-20', periods=15).strftime('%Y-%m-%d').tolist()
    prices = {
        ('CN', 'A'): frame(dates, list(range(100, 115))),
        ('CN', 'B'): frame(dates, list(range(200, 215))),
    }
    benchmark = frame(dates, [100 + i for i in range(15)])
    assets = basket_assets([('A', 'g1'), ('B', 'g2')])
    panel = nav.align_assets(prices, assets, benchmark)
    quarterly = nav.nav_from_panel(
        panel, rebalance='quarterly',
        calendar_days=['2025-03-31', '2025-06-30'], calendar_end='2025-06-30',
    )
    buy_and_hold = nav.nav_from_panel(panel, rebalance=None)
    assert quarterly.nav.index.equals(buy_and_hold.nav.index)
    assert quarterly.nav['benchmark'].equals(buy_and_hold.nav['benchmark'])
    assert [row['initial_weight'] for row in quarterly.weights] == [
        row['initial_weight'] for row in buy_and_hold.weights
    ]


def test_rs_20d_is_window_independent_and_missing_without_history():
    dates = pd.bdate_range('2025-01-02', periods=40)
    nav_frame = pd.DataFrame({
        'basket': [100 + i for i in range(40)],
        'benchmark': [100 + i / 2 for i in range(40)],
    }, index=dates)
    assert rs_20d_pct(nav_frame) is not None
    short = nav_frame.iloc[:20]
    assert rs_20d_pct(short) is None

    prices = {
        ('CN', 'A'): frame(dates.strftime('%Y-%m-%d'), [100 + i for i in range(40)]),
        ('CN', 'B'): frame(dates.strftime('%Y-%m-%d'), [100 + i / 2 for i in range(40)]),
    }
    benchmark = frame(dates.strftime('%Y-%m-%d'), [100 + i / 2 for i in range(40)])
    panel = nav.align_assets(prices, basket_assets([('A', 'g'), ('B', 'g')]), benchmark)
    windows = window_definitions(dates)
    metrics = metrics_for_windows(nav_frame, panel, windows)
    values = [metrics[key]['rs_20d_pct'] for key in ('all', '1y', 'ytd', '3m')]
    assert values[0] is not None
    assert values.count(values[0]) == len(values)


def test_participation_and_concentration_with_late_listing():
    dates = pd.bdate_range('2025-01-02', periods=30)
    labels = dates.strftime('%Y-%m-%d').tolist()
    prices = {
        ('CN', 'A'): frame(labels, [100 + i for i in range(30)]),
        ('CN', 'B'): frame(labels, [100 - i for i in range(30)]),
        ('CN', 'C'): frame(labels, [100] * 30),
        ('CN', 'D'): frame(labels[10:], [100 + i for i in range(20)]),
    }
    benchmark = frame(labels, [100] * 30)
    assets = basket_assets([('A', 'g'), ('B', 'g'), ('C', 'g'), ('D', 'g')])
    panel = nav.align_assets(prices, assets, benchmark)
    windows = window_definitions(dates)
    nav_frame = pd.DataFrame({'basket': [100 + i for i in range(30)], 'benchmark': [100] * 30}, index=dates)
    metrics = metrics_for_windows(nav_frame, panel, windows)
    all_stats = metrics['all']
    assert all_stats['interval_positive'] == 1  # D has no price at the window start
    assert all_stats['interval_valid'] == 3
    assert all_stats['interval_total'] == 4
    assert all_stats['participation_20d_positive'] == 1  # A
    assert all_stats['participation_20d_valid'] == 3  # D lacks 20 sessions at the end

    conc = concentration(panel)
    assert conc['cash_sleeve_weight'] == pytest.approx(0.25)
    assert conc['cash_stock_count'] == 1
    assert conc['invested_stock_count'] == 3
    assert conc['max_target_weight'] == pytest.approx(1 / 3)
    assert conc['effective_stock_count'] == pytest.approx(3.0)

    series = participation_timeseries(panel)
    assert len(series) == 30
    assert series.iloc[-1]['valid_20d'] == 3


def test_overlap_diagnostics_flag_shared_stock_and_weights():
    dates = pd.bdate_range('2025-01-02', periods=10)
    labels = dates.strftime('%Y-%m-%d').tolist()
    prices = {
        ('CN', 'A'): frame(labels, [100] * 10),
        ('CN', 'B'): frame(labels, [100] * 10),
        ('CN', 'C'): frame(labels, [100] * 10),
    }
    benchmark = frame(labels, [100] * 10)
    first = nav.align_assets(prices, basket_assets([('A', 'g1'), ('B', 'g2')]), benchmark)
    second = nav.align_assets(prices, basket_assets([('B', 'g1'), ('C', 'g2')]), benchmark)
    overlap = overlap_diagnostics([
        {'key': 'first', 'label': 'First', 'role': 'mainline', 'panel': first},
        {'key': 'second', 'label': 'Second', 'role': 'mainline', 'panel': second},
    ])
    assert len(overlap['shared_stocks']) == 1
    assert overlap['shared_stocks'][0]['symbol'] == 'B'
    assert overlap['shared_stocks'][0]['basket_count'] == 2
    pair = overlap['pairs'][0]
    assert pair['shared_count'] == 1
    assert pair['shared_weight'] == pytest.approx(0.5)
    assert overlap['per_basket']['first']['unique_weight'] == pytest.approx(0.5)
    assert overlap['per_basket']['first']['max_overlap_key'] == 'second'
