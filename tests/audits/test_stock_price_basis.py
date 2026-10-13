import pandas as pd
from scripts.summarize_stock_price_basis import compare_close
from scripts.audit_stock_price_basis import reconcile


def frame():
    d=pd.bdate_range('2024-01-01',periods=400)
    return pd.DataFrame({'date':d,'open':range(1,401),'high':range(1,401),'low':range(1,401),'close':range(1,401)})


def test_basis_mismatch_detected_but_missing_reference_never_compresses_window():
    a=frame();date=str(a.date.iloc[-1].date())
    assert reconcile(a,a,date)['status']=='MATCH_CURRENT_QFQ'
    assert compare_close(a,a,date)['position_delta_360']==0
    b=a.copy();b.loc[399,'close']=100
    assert compare_close(a,b,date)['long_term_bottom_changed'] is True
    result=compare_close(a,a.drop(index=380),date)
    assert result['missing_reference_dates']==1
    assert result['position_delta_360'] is None
    assert result['long_term_bottom_changed'] is None
