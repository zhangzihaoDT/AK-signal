import pandas as pd
import pytest
from src.market_facts.etf_provenance import describe_series, tag_prices, check_merge_basis


def test_lineage_labels_do_not_rewrite_legacy_prices():
    old=pd.DataFrame({'fund_code':['510300'],'date':['2026-10-14'],'close':[4.]})
    new=tag_prices(pd.DataFrame({'fund_code':['510300'],'date':['2026-10-15'],'close':[4.1]}),'sina','fund_etf_hist_sina')
    check_merge_basis(old,new)
    d=pd.concat([old,new],ignore_index=True)
    r=describe_series(d,'2026-10-15').iloc[0]
    assert r.source=='sina+unknown_legacy'
    assert r.target_price_source=='sina'
    assert r.series_basis_status=='legacy_unknown'
    assert d.close.tolist()==[4.,4.1]


def test_incompatible_known_bases_cannot_be_merged():
    a=pd.DataFrame({'series_price_basis':['unadjusted']})
    b=pd.DataFrame({'series_price_basis':['vendor_forward_adjusted']})
    with pytest.raises(ValueError,match='禁止拼接'):
        check_merge_basis(a,b)
