import pandas as pd
import pytest
from src.research.baskets.comparison import fixed_weight_nav


def frame(dates, values):
    return pd.DataFrame({'date': pd.to_datetime(dates), 'close': values})


def test_late_listing_uses_cash_without_artificial_nav_jump():
    dates = ['2025-01-02', '2025-01-03', '2025-01-06']
    assets = [{'market': 'CN', 'symbol': 'A', 'group': 'g'},
              {'market': 'CN', 'symbol': 'B', 'group': 'g'}]
    prices = {('CN', 'A'): frame(dates, [10, 20, 20]),
              ('CN', 'B'): frame(dates[1:], [50, 100])}
    nav, weights = fixed_weight_nav(prices, assets, frame(dates, [100, 100, 100]))
    assert nav.basket.tolist() == [100, 150, 200]
    assert weights[1]['cash_until_first_close']
    assert sum(a['contribution_pp'] for a in weights) == 100


def test_hk_only_session_close_is_used_on_next_cn_session():
    assets = [{'market': 'HK', 'symbol': 'A', 'group': 'g'}]
    prices = {('HK', 'A'): frame(['2025-01-02', '2025-01-03'], [10, 12])}
    nav, _ = fixed_weight_nav(prices, assets, frame(['2025-01-02', '2025-01-06'], [100, 100]))
    assert nav.basket.tolist() == [100, 120]


def test_group_weights_and_split_adjusted_continuity():
    dates = ['2026-07-09', '2026-07-10']
    assets = [{'market': 'HK', 'symbol': 'A', 'group': 'g1'},
              {'market': 'CN', 'symbol': 'B', 'group': 'g2'},
              {'market': 'CN', 'symbol': 'C', 'group': 'g2'}]
    prices = {('HK', 'A'): frame(dates, [128 / 8, 16]),
              ('CN', 'B'): frame(dates, [10, 20]), ('CN', 'C'): frame(dates, [10, 10])}
    nav, weights = fixed_weight_nav(prices, assets, frame(dates, [100, 100]))
    assert [r['initial_weight'] for r in weights] == [0.5, 0.25, 0.25]
    assert nav.basket.tolist() == [100, 125]


def test_duplicate_and_zero_prices_are_rejected():
    assets = [{'market': 'CN', 'symbol': 'A', 'group': 'g'}]
    bm = frame(['2025-01-02', '2025-01-03'], [100, 100])
    for price in [frame(['2025-01-02'] * 2, [10, 11]), frame(['2025-01-02'], [0])]:
        with pytest.raises(ValueError):
            fixed_weight_nav({('CN', 'A'): price}, assets, bm)


def test_hub_preview_pins_report_and_rejects_tampered_data(tmp_path):
    import json
    import hashlib
    from src.research.baskets.hub_view import pin_comparison, comparison_view
    folder = tmp_path / '.run_day/runs/20261014/example/outputs/research/baskets'
    folder.mkdir(parents=True)
    curves = pd.DataFrame({'date': ['2024-07-16', '2026-10-14'],
                           'example': [100, 120], 'hs300': [100, 110]})
    path = folder / 'comparison_nav.csv'
    curves.to_csv(path, index=False)
    (folder / 'comparison_manifest.json').write_text(json.dumps({
        'start': '2024-07-16', 'end': '2026-10-14',
        'data_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'baskets': [{'key': 'example', 'label': 'Example'}]}))
    (folder / 'basket_compare.html').write_text('<h1>Report</h1>')
    pin_comparison(tmp_path, folder)
    preview, artifact = comparison_view(tmp_path)
    assert preview['series'][0]['values'] == [100, 120]
    assert artifact['runId'].startswith('research-')
    assert artifact['targetDate'] == '20261014'
    path.write_text('tampered')
    with pytest.raises(ValueError, match='hash mismatch'):
        comparison_view(tmp_path)


def test_hub_does_not_expand_existing_attachment_scope(tmp_path):
    from src.research.baskets.hub_view import pin_comparison
    with pytest.raises(ValueError):
        pin_comparison(tmp_path, tmp_path / 'untrusted')
