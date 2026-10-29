import pandas as pd
import pytest

from src.etf_signal.classifier import classify_all, classify_etf, load_config
from src.etf_signal.rotation import compute_rotation_metrics


@pytest.mark.parametrize('name,market,direction', [
    ('自由现金流ETF', '待核实', '跨行业策略'),
    ('港股现金流ETF', '港股', '跨行业策略'),
    ('A500ETF', '境内', '宽基'),
    ('A500红利低波ETF', '境内', '跨行业策略'),
    ('纳指ETF', '美国', '宽基'),
    ('纳指科技ETF', '美国', '科技'),
    ('纳指生物科技ETF', '美国', '医疗'),
    ('标普A股红利ETF', '境内', '跨行业策略'),
    ('标普油气ETF', '美国', '能源'),
    ('恒生科技ETF', '港股', '科技'),
    ('恒生国企ETF', '港股', '宽基'),
    ('港股银行红利ETF', '港股', '金融'),
    ('沪港深互联网ETF', '跨市场', '科技'),
    ('中概互联ETF', '跨市场', '科技'),
    ('中韩半导体ETF', '跨市场', '科技'),
    ('日经ETF', '其他海外', '宽基'),
    ('国债ETF', '待核实', '固收'),
    ('短融ETF', '待核实', '固收'),
    ('货币ETF', '待核实', '货币'),
    ('黄金ETF', '待核实', '商品'),
    ('黄金股ETF', '待核实', '其他行业'),
    ('原油期货ETF', '待核实', '商品'),
    ('新能源ETF', '待核实', '其他行业'),
    ('未知基金', '待核实', '待核实'),
    ('', '待核实', '待核实'),
    (None, '待核实', '待核实'),
])
def test_independent_dimensions_and_boundaries(name, market, direction):
    result = classify_etf(name)
    assert result['investment_market'] == market
    assert result['investment_direction'] == direction
    assert result['market_classification_status'] != 'verified'
    assert result['direction_classification_status'] != 'verified'


def test_index_name_precedes_conflicting_short_name():
    result = classify_etf('美国科技ETF', '中证沪港深消费指数')
    assert result['investment_market'] == '跨市场'
    assert result['investment_direction'] == '消费'
    assert result['classification_basis'] == 'tracking_index_name'


def test_refresh_existing_misclassification_and_nan_is_idempotent():
    source = pd.DataFrame({'fund_name': ['现金流ETF', '纳指ETF', pd.NA],
                           'tracking_index': [pd.NA, float('nan'), None],
                           'primary_bucket': ['money_market', pd.NA, ''],
                           'asset_bucket': ['money_market', '', ''], 'price': [1., 2., 3.]})
    before = source.copy(deep=True)
    result = classify_all(source)
    assert result.loc[0, 'asset_bucket'] == 'factor_style'
    assert result.loc[1, 'asset_bucket'] == 'overseas_equity'
    pd.testing.assert_frame_equal(result, classify_all(result))
    pd.testing.assert_frame_equal(source, before)
    pd.testing.assert_series_equal(source.price, result.price)


def test_cashflow_is_in_ranking_but_money_remains_excluded(monkeypatch):
    from src.etf_signal import classification_facts
    def fact(value):
        return dict(value=value, status='inferred', as_of='2020-01-01', scope='mandate', source_ids=['fixture'], reason='published')
    document = {'classification_version': 'fixture-v1', 'published_at': '2020-01-01T00:00:00+00:00',
                'records': [{'fund_code': code, 'investment_market': fact('境内'), 'investment_direction': fact(direction)}
                            for code, direction in [('cashflow', '跨行业策略'), ('equity', '宽基'), ('money', '货币')]]}
    monkeypatch.setattr(classification_facts, 'load_input', lambda: {'document': document, 'sha256': 'fixture', 'reason': ''})
    dates = pd.bdate_range('2026-01-01', periods=70)
    combined = pd.concat([pd.DataFrame({'date': dates, 'fund_code': code,
                                        'close': [100 + i * growth for i in range(70)]})
                          for code, growth in [('cashflow', 1), ('equity', .2), ('money', .001)]])
    master = pd.DataFrame({'fund_code': ['cashflow', 'equity', 'money'],
                           'fund_name': ['现金流ETF', 'A500ETF', '货币ETF'],
                           'asset_bucket': ['money_market', '', 'money_market']})
    result = compute_rotation_metrics(combined, master).set_index('fund_code')
    assert result.loc['cashflow', 'rps15'] == 100
    assert result.loc['equity', 'rps15'] == 50
    assert pd.isna(result.loc['money', 'rps15'])


def test_vocabulary_is_exact():
    cfg = load_config()
    assert cfg['investment_markets'] == ['境内', '港股', '美国', '其他海外', '跨市场', '待核实']
    assert cfg['investment_directions'] == ['宽基', '科技', '消费', '医疗', '金融', '能源', '其他行业', '跨行业策略', '固收', '商品', '货币', '待核实']


def test_audit_preserves_input_and_refuses_existing_destination(tmp_path):
    from scripts.audits.audit_etf_classification import audit
    source = tmp_path / 'source.csv'
    source.write_text('fund_code,fund_name,asset_bucket\n159001,现金流ETF,money_market\n159002,纳指ETF,\n')
    original = source.read_bytes()
    output = tmp_path / 'audit'
    summary = audit(source, output)
    assert summary['old_unclassified_direction_identified'] == 1
    assert summary['priority_groups']['现金流']['investment_direction'] == {'跨行业策略': 1}
    assert summary['market_pending'] == 1
    assert source.read_bytes() == original
    receipt = (output / 'classification_candidates.csv').read_bytes()
    with pytest.raises(FileExistsError):
        audit(source, output)
    assert (output / 'classification_candidates.csv').read_bytes() == receipt
