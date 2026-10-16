"""Module ownership: source and classification are independent across modules."""
import yaml
from src.common.paths import project_root


def source_policy():
    path=project_root()/'config/observation_sources.yaml'
    if not path.exists():
        raise ValueError('模块数据来源配置缺失，禁止隐式切回旧来源')
    policy=yaml.safe_load(path.read_text())
    if (not isinstance(policy,dict) or policy.get('version')!='observation-sources-v1'
        or policy.get('stocks',{}).get('provider')!='longbridge'
        or policy['stocks'].get('benchmark')!='000300.SH'
        or policy['stocks'].get('price_basis')!='Longbridge ForwardAdjust'
        or policy.get('industry',{}).get('provider')!='tdx'
        or set(policy.get('etfs',{}).get('providers',[]))!={'em','sina'}
        or policy['stocks'].get('cross_provider_fallback') is not False
        or policy['industry'].get('fallback') is not False):
        raise ValueError('模块数据来源契约不完整或含未授权回退')
    return policy


def excluded_stock(market, symbol):
    return any(str(r['market'])==str(market) and str(r['symbol']).zfill(5 if market=='HK' else 6)==str(symbol).zfill(5 if market=='HK' else 6)
               for r in source_policy().get('stocks',{}).get('excluded',[]))


def aligned_stock_rows(frame, target):
    """Explicit policy exclusions do not excuse missing data for another asset."""
    import pandas as pd
    aligned=(pd.to_datetime(frame.trade_date)==pd.Timestamp(target)) & (
        pd.to_datetime(frame.source_trade_date)==pd.Timestamp(target)) & frame.data_status.eq('current')
    excluded=frame.apply(lambda r: r.data_status=='excluded' and excluded_stock(r.market,r.symbol)
                        and pd.Timestamp(r.trade_date)==pd.Timestamp(target),axis=1)
    return aligned | excluded
