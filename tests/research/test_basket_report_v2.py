import json
import re

import pandas as pd

from src.research.baskets import nav, report
from src.research.baskets.metrics import (
    concentration,
    metrics_for_windows,
    overlap_diagnostics,
    window_definitions,
)


def frame(dates, values):
    return pd.DataFrame({'date': pd.to_datetime(dates), 'close': values})


def make_result(key, label, role, prices, assets, benchmark, windows):
    panel = nav.align_assets(prices, assets, benchmark)
    result = nav.nav_from_panel(panel, rebalance=None)
    metrics = metrics_for_windows(result.nav, panel, windows)
    return {
        'key': key,
        'label': label,
        'role': role,
        'nav': result.nav,
        'nav_quarterly': result.nav,
        'nav_buy_and_hold': result.nav,
        'weights': result.weights,
        'weights_quarterly': result.weights,
        'weights_buy_and_hold': result.weights,
        'metrics': metrics,
        'metrics_quarterly': metrics,
        'metrics_buy_and_hold': metrics,
        'concentration': concentration(panel),
        'panel': panel,
        'rebalance_dates': [],
        'rebalance_turnover': [],
    }


def test_report_renders_qr_default_and_reference(monkeypatch, tmp_path):
    dates = pd.bdate_range('2025-01-02', periods=30)
    labels = dates.strftime('%Y-%m-%d').tolist()
    benchmark = frame(labels, [100 + i for i in range(30)])
    windows = window_definitions(dates)

    main_prices = {
        ('CN', 'A'): frame(labels, [100 + i for i in range(30)]),
        ('CN', 'B'): frame(labels, [100 - i for i in range(30)]),
    }
    ref_prices = {
        ('CN', 'C'): frame(labels, [100] * 30),
        ('CN', 'D'): frame(labels, [100 + i / 2 for i in range(30)]),
    }
    main_assets = [
        {'market': 'CN', 'symbol': 'A', 'name': 'A', 'group': 'g'},
        {'market': 'CN', 'symbol': 'B', 'name': 'B', 'group': 'g'},
    ]
    ref_assets = [
        {'market': 'CN', 'symbol': 'C', 'name': 'C', 'group': 'g'},
        {'market': 'CN', 'symbol': 'D', 'name': 'D', 'group': 'g'},
    ]
    results = [
        make_result('main', '研究主线', 'mainline', main_prices, main_assets, benchmark, windows),
        make_result('reference', '高现金流资产', 'reference', ref_prices, ref_assets, benchmark, windows),
    ]
    overlap = overlap_diagnostics(results)
    receipt = {
        'start': labels[0],
        'end': labels[-1],
        'rows': len(labels),
        'windows': windows,
        'default_method': 'quarterly',
        'methods': ['quarterly', 'buy_and_hold'],
        'methodology_version': 'basket_compare_v2',
        'overlap': overlap,
        'excluded': [],
        'rebalance_rule': {'timing': 'quarter-end close', 'effective': 'next session', 'costs': 'not deducted'},
        'source_snapshot': '/tmp/snapshot',
    }
    monkeypatch.setattr(report, 'get_plotlyjs', lambda: '/* plotly */')
    path = report.render_comparison(results, receipt, tmp_path / 'basket_compare.html')
    html = path.read_text()

    for text in (
        '观察篮子 · 主线强弱', '8 条研究主线 + 高现金流参照',
        '主线强弱', '主线定位', '结构与集中度', '跨篮子重叠',
        '参照：高现金流资产', '季度再平衡', '买入持有', 'RS20Δ',
        'comparison_nav_bh.csv', 'data-window-table="all"',
        'data-method="quarterly"', 'main-scatter',
    ):
        assert text in html
    assert '__DATA__' not in html
    assert '__TABLES__' not in html

    payload = json.loads(re.search(
        r'<script id="reviewed-data" type="application/json">(.*?)</script>',
        html, re.S,
    ).group(1))
    assert len(payload['baskets']) == 2
    assert payload['baskets'][0]['role'] == 'mainline'
    assert payload['baskets'][1]['role'] == 'reference'
    assert payload['windows']['all']['end_index'] == 29
    assert payload['baskets'][0]['metrics']['quarterly']['all']['rs_20d_pct'] is not None

    annotations = re.search(
        r"annotations:\[(.*?)\]\};Plotly.react\('main-scatter'", html, re.S,
    ).group(1)
    positions = {
        (x, y): text
        for x, y, text in re.findall(
            r"\{x:([0-9.]+),y:([0-9.]+),xref:'paper',yref:'paper',text:'([^']+)'",
            annotations,
        )
    }
    assert positions[('0.01', '0.99')] == '弱但广'
    assert positions[('0.99', '0.02')] == '强但窄'
