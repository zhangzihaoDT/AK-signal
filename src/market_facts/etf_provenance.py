"""ETF-only source lineage; metadata never changes prices or indicators."""
import pandas as pd


def tag_prices(frame, provider, endpoint):
    result=frame.copy()
    result['price_provider']=provider
    result['price_endpoint']=endpoint
    result['series_price_basis']='unadjusted'
    return result


def check_merge_basis(existing, new):
    known=set()
    for frame in (existing,new):
        if 'series_price_basis' in frame:
            known.update(v for v in frame.series_price_basis.dropna().astype(str).unique() if v!='unknown')
    if len(known)>1:
        raise ValueError('ETF价格口径不一致，禁止拼接不同调整序列')


def describe_series(frame, target):
    rows=[];cutoff=pd.Timestamp(target)
    d=frame[pd.to_datetime(frame.date)<=cutoff]
    for code,g in d.groupby('fund_code'):
        providers=g.get('price_provider',pd.Series('unknown_legacy',index=g.index)).fillna('unknown_legacy').astype(str)
        bases=g.get('series_price_basis',pd.Series('unknown',index=g.index)).fillna('unknown').astype(str)
        current=providers[pd.to_datetime(g.date)==cutoff]
        rows.append({'fund_code':str(code),'source':'+'.join(sorted(set(providers))),
                     'target_price_source':'+'.join(sorted(set(current))) if len(current) else 'missing',
                     'price_basis':'+'.join(sorted(set(bases))),
                     'price_basis_verified':False,
                     'series_basis_status':'legacy_unknown' if 'unknown' in set(bases) else
                                           'consistent' if len(set(bases))==1 else 'conflict'})
    return pd.DataFrame(rows,columns=['fund_code','source','target_price_source','price_basis','price_basis_verified','series_basis_status'])
