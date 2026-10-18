import pandas as pd
import pytest
from src.research.baskets.nav import fixed_weight_nav


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
    from src.hub.baskets import pin_comparison, comparison_view
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
    from src.hub.baskets import pin_comparison
    with pytest.raises(ValueError):
        pin_comparison(tmp_path, tmp_path / 'untrusted')


def test_longbridge_baskets_include_cashflow_and_exclude_hesai():
    from src.research.baskets.comparison import comparison_baskets, constituents
    baskets=comparison_baskets()
    assert len(baskets)==9
    included=set();excluded=set()
    for key,basket in baskets.items():
        rows,omitted=constituents({**basket,'key':key})
        included.update((r['market'],r['symbol']) for r in rows)
        excluded.update((r['market'],r['symbol']) for r in omitted)
    assert len(included)==68
    assert ('HK','02525') in excluded
    assert ('HK','02525') not in included
    assert ('HK','02498') in included


def test_longbridge_fetch_uses_shanghai_benchmark_and_close_only(tmp_path,monkeypatch):
    from src.research.baskets import comparison as lb
    from datetime import datetime,timezone
    from types import SimpleNamespace
    import json
    calls=[]
    class Context:
        def history_candlesticks_by_date(self,symbol,*args):
            calls.append(symbol)
            return [SimpleNamespace(timestamp=datetime(2026,10,15,tzinfo=timezone.utc),close=10,volume=1,turnover=10)]
    monkeypatch.setattr(lb,'project_root',lambda:tmp_path)
    monkeypatch.setattr(lb,'quote_context',lambda _:Context())
    monkeypatch.setattr(lb.time,'sleep',lambda _:None)
    monkeypatch.setattr(lb,'comparison_baskets',lambda:{'test':{}})
    monkeypatch.setattr(lb,'constituents',lambda _:([{'market':'CN','symbol':'600900','name':'test'}],[]))
    folder=lb.fetch_prices('2026-10-15','2026-10-15')
    assert calls==['600900.SH','000300.SH']
    snapshot=json.loads((folder/'benchmark.json').read_text())
    assert snapshot['identity']['price_basis']=='index_level'
    assert snapshot['bars'][0]['bar_kind']=='close_only'


def test_hub_update_preserves_pinned_start_and_pins_only_after_render(tmp_path, monkeypatch):
    from datetime import datetime
    from pathlib import Path
    from src.research.baskets import hub_update

    events = []
    class Calendar:
        def complete_day(self, at):
            assert at == datetime(2026, 10, 16)
            return "20261015"

    monkeypatch.setattr(hub_update, "default_calendar", lambda root: Calendar())
    monkeypatch.setattr(hub_update, "comparison_view", lambda root: ({"startDate": "2024-07-16"}, {}))
    monkeypatch.setattr(hub_update.comparison, "fetch_prices", lambda start, end: events.append(("fetch", start, end)) or tmp_path / "snapshot")
    def build(snapshot, output, start, end):
        events.append(("build", start, end))
        assert output.is_relative_to(tmp_path / ".run_day/runs/20261015")
        return ([{"key": "example"}], {"start": start, "end": end})
    monkeypatch.setattr(hub_update.comparison, "build_longbridge_comparison", build)
    def render(results, receipt, report):
        events.append(("render", receipt["end"]))
        return report
    monkeypatch.setattr(hub_update, "render_comparison", render)
    monkeypatch.setattr(hub_update, "pin_comparison", lambda root, output: events.append(("pin", output)))

    result = hub_update.update(tmp_path, datetime(2026, 10, 16))

    assert result["startDate"] == "2024-07-16"
    assert result["endDate"] == "2026-10-15"
    assert [event[0] for event in events] == ["fetch", "build", "render", "pin"]


def test_hub_update_failure_keeps_existing_pointer(tmp_path, monkeypatch):
    from datetime import datetime
    from src.research.baskets import hub_update

    class Calendar:
        def complete_day(self, at):
            return "20261015"

    pinned = []
    monkeypatch.setattr(hub_update, "default_calendar", lambda root: Calendar())
    monkeypatch.setattr(hub_update, "comparison_view", lambda root: ({"startDate": "2024-07-16"}, {}))
    monkeypatch.setattr(hub_update.comparison, "fetch_prices", lambda start, end: (_ for _ in ()).throw(ValueError("source not ready")))
    monkeypatch.setattr(hub_update, "pin_comparison", lambda *args: pinned.append(args))

    with pytest.raises(ValueError, match="source not ready"):
        hub_update.update(tmp_path, datetime(2026, 10, 16))

    assert pinned == []
