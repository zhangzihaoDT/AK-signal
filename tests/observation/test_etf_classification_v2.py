import pandas as pd

from src.observation.report import classification, classified_mask


def test_two_dimensions_determine_scope_independent_of_legacy_bucket():
    frame = pd.DataFrame({
        'asset_bucket': ['factor_style', 'overseas_equity', 'industry'],
        'investment_market': ['待核实', '美国', '境内'],
        'investment_direction': ['跨行业策略', '宽基', '待核实'],
        'classification_status': ['unclassified', 'classified_unverified', 'unclassified'],
    })
    assert classified_mask(frame).tolist() == [False, True, False]
    result = classification(frame)
    assert result['classified'] == 1
    assert result['unclassified'] == 2
    assert result['mismatched_marker'] == 0
