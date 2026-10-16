import json

import pandas as pd
import pytest

from src.observation import cli
from src.sw_industry_rps import storage


@pytest.mark.parametrize('source,evidence,valid,status', [
    ('realtime', None, False, 'UNKNOWN'),
    ('realtime', '2026-10-14', False, 'UNKNOWN'),
    ('realtime', '2026-10-15', True, 'COMPLETE'),
    ('swsresearch', None, True, 'CONFIRMED'),
])
def test_final_validation_checks_price_dates(tmp_path, monkeypatch, source, evidence, valid, status):
    from src.market_facts import policy
    original_policy = policy.source_policy()
    monkeypatch.setattr(policy, 'source_policy', lambda: {**original_policy, 'industry': {'provider':'sw'}})
    monkeypatch.setattr(cli, 'outputs_dir', lambda: tmp_path)
    monkeypatch.setattr(cli, 'stock_metrics_dir', lambda: tmp_path)
    monkeypatch.setattr('src.final_validation.cli.data_quality_warnings', lambda _: [])
    monkeypatch.setattr('src.observation.report.identity_issues', lambda _: [])
    industry = pd.DataFrame({'industry_code': ['801081.SI'], 'trade_date': ['2026-10-15'],
                             'source': [source], 'data_status': ['complete' if source == 'realtime' else 'confirmed']})
    if evidence:
        industry['source_trade_date'] = evidence
    monkeypatch.setattr(storage, 'load_metrics', lambda _: industry)
    folder = tmp_path / 'observation'
    folder.mkdir()
    for stem in ('etf_trends', 'asset_directions', 'industry_rotation', 'industry_directions', 'stock_positions', 'etf_positions'):
        frame = industry if stem == 'industry_rotation' else pd.DataFrame({'trade_date': ['2026-10-15'], 'bottom_change': ['UNCHANGED']})
        frame.to_parquet(folder / f'{stem}_20261015.parquet')
    pd.DataFrame({'trade_date': ['2026-10-15'], 'source_trade_date': ['2026-10-15'],
                  'data_status': ['current']}).to_parquet(tmp_path / 'stock_metrics_20261015.parquet')
    if valid:
        result = cli.validate('20261015')
        assert result['industry_price_dates_verified'] is True
    else:
        with pytest.raises(ValueError, match='独立源交易日'):
            cli.validate('20261015')
    receipt = json.loads((tmp_path / 'run_day/final_check_20261015.json').read_text())
    assert receipt['status'] == status
    assert receipt['ok'] is valid
