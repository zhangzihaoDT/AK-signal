from types import SimpleNamespace
import json

import pandas as pd
import pytest

from src import daily_run
from src.final_validation import cli as validation


def test_every_stage_is_pinned_and_offline_has_no_acquisition():
    plan = daily_run.steps('20261012')
    for command in plan:
        assert '20261012' in command
    offline = daily_run.steps('20261012', offline=True)
    assert not any(c[1] == 'update' for c in offline)
    assert not any('--allow-online-fetch' in c for c in offline)


def test_partition_checks_all_rows_not_just_latest(tmp_path):
    path = tmp_path / 'facts.parquet'
    pd.DataFrame({'trade_date': pd.to_datetime(['2026-10-09', '2026-10-12'])}).to_parquet(path)
    with pytest.raises(ValueError, match='日期不一致'):
        daily_run.check_partition(path, '20261012')


def test_stale_stock_listed_and_confirmation_blocked(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(daily_run, 'stock_metrics_dir', lambda: tmp_path)
    pd.DataFrame({'trade_date': [pd.Timestamp('2026-10-12')],
                  'source_trade_date': [pd.Timestamp('2026-10-09')],
                  'data_status': ['stale'], 'market': ['CN'], 'symbol': ['002050'],
                  'name': ['三花智控']}).to_parquet(tmp_path / 'stock_metrics_20261012.parquet')
    with pytest.raises(ValueError, match='停止主题确认'):
        daily_run.check_stock_date('20261012')
    assert '002050 三花智控' in capsys.readouterr().out


def test_current_stock_requires_same_day_benchmark(tmp_path, monkeypatch):
    monkeypatch.setattr(daily_run, 'stock_metrics_dir', lambda: tmp_path)
    pd.DataFrame({'trade_date': [pd.Timestamp('2026-10-12')],
                  'source_trade_date': [pd.Timestamp('2026-10-12')],
                  'data_status': ['current'], 'market': ['CN'], 'symbol': ['002050'],
                  'name': ['三花智控']}).to_parquet(tmp_path / 'stock_metrics_20261012.parquet')
    audit = tmp_path / 'stock_update_audit_20261012.json'
    audit.write_text(json.dumps({'benchmark_trade_date': '2026-09-24'}))
    with pytest.raises(ValueError, match='沪深300'):
        daily_run.check_stock_date('20261012')
    audit.write_text(json.dumps({'benchmark_trade_date': '2026-10-12'}))
    daily_run.check_stock_date('20261012')


def test_final_check_reports_etf_and_named_stock_holes(tmp_path, monkeypatch):
    raw = tmp_path / 'raw'
    raw.mkdir()
    diag = tmp_path / 'diagnostics'
    diag.mkdir()
    monkeypatch.setattr(validation, 'etf_signal_raw_dir', lambda: raw)
    monkeypatch.setattr(validation, 'stock_metrics_dir', lambda: tmp_path)
    (diag / 'source_audit_20261012.json').write_text(json.dumps({'failed_codes': ['560650']}))
    pd.DataFrame({'source_trade_date': [pd.NaT], 'data_status': ['missing'],
                  'market': ['CN'], 'symbol': ['002050'], 'name': ['三花智控']}).to_parquet(
                      tmp_path / 'stock_metrics_20261012.parquet')
    messages = [r['message'] for r in validation.data_quality_warnings('20261012')]
    assert any('560650' in s for s in messages)
    assert any('002050 三花智控' in s for s in messages)


def test_failed_stage_never_reaches_confirmation(monkeypatch, tmp_path):
    commands = []
    monkeypatch.setattr('sys.argv', ['daily_run', '--date', '20261012'])
    monkeypatch.setattr(daily_run, 'check_step', lambda c, td: None)
    monkeypatch.setattr(daily_run, 'project_root', lambda: tmp_path)
    monkeypatch.setattr(daily_run, 'step_artifacts', lambda c, td: [])
    monkeypatch.setattr(daily_run, 'check_generated_reports', lambda *a: None)
    def run(cmd, *args):
        commands.append(cmd)
        return 1 if 'stock-metrics' in cmd else 0
    monkeypatch.setattr(daily_run, 'run_step', run)
    with pytest.raises(SystemExit) as error:
        daily_run.main()
    assert error.value.code == 1
    assert not any('confirm' in c for c in commands)


def test_daily_bottom_scan_excludes_stale_prices_but_research_retains_them(tmp_path, monkeypatch):
    from src.research.etf_bottom import price_map
    monkeypatch.setattr(price_map, 'etf_signal_raw_dir', lambda: tmp_path)
    monkeypatch.setattr(price_map, 'load_master_names', lambda: pd.DataFrame(
        {'fund_code': ['000001', '000002'], 'fund_name': ['A', 'B'], 'primary_bucket': ['theme', 'theme']}))
    monkeypatch.setattr(price_map, 'compute_row', lambda code, *a: {
        'fund_code': code, 'bottom_state': 'NORMAL', 'price_pos_360': 50.})
    for code, end in [('000001', '2026-10-12'), ('000002', '2026-10-09')]:
        pd.DataFrame({'date': pd.bdate_range(end=end, periods=80), 'close': 10.}).to_parquet(
            tmp_path / f'{code}.parquet')
    monkeypatch.delenv('AKSIGNAL_TRADE_DATE', raising=False)
    assert len(price_map.build_price_map('2026-10-12')) == 2
    monkeypatch.setenv('AKSIGNAL_TRADE_DATE', '20261012')
    assert list(price_map.build_price_map('2026-10-12').fund_code) == ['000001']


def test_offline_enrichment_never_fetches_missing_cache(monkeypatch):
    from src.sw_industry_rps import constituents
    from src.common import market_data
    monkeypatch.setenv('AKSIGNAL_OFFLINE', '1')
    monkeypatch.setattr(constituents.requests, 'get', lambda *a, **kw: pytest.fail('network called'))
    assert constituents.fetch_constituent_list('801101.SI').empty
    assert market_data.fetch_cn_daily('002050') is None


def test_json_partition_checks_internal_date(tmp_path, monkeypatch):
    monkeypatch.setattr(daily_run, 'outputs_dir', lambda: tmp_path)
    folder = tmp_path / 'selection'
    folder.mkdir()
    path = folder / 'tradable_candidates_20261012.json'
    path.write_text(json.dumps({'date': '20261009'}))
    with pytest.raises(ValueError, match='日期不一致'):
        daily_run.check_step(['select', 'run', '--date', '20261012'], '20261012')
    path.write_text(json.dumps({'date': '20261012'}))
    daily_run.check_step(['select', 'run', '--date', '20261012'], '20261012')
