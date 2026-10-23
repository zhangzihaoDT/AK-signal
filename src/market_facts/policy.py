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
    if not {'trade_date','source_trade_date','data_status'}.issubset(frame.columns):
        return pd.Series(False,index=frame.index)
    aligned=(pd.to_datetime(frame.trade_date)==pd.Timestamp(target)) & (
        pd.to_datetime(frame.source_trade_date)==pd.Timestamp(target)) & frame.data_status.eq('current')
    excluded=frame.apply(lambda r: r.data_status=='excluded' and excluded_stock(r.get('market'),r.get('symbol'))
                        and pd.Timestamp(r.trade_date)==pd.Timestamp(target),axis=1)
    from .calendar import SessionCalendar
    calendars={}
    def verified_closure(row):
        if row.data_status != 'market_closed' or row.get('market') not in ('CN','HK'):
            return False
        try:
            if row.market not in calendars:
                name='XHKG' if row.market=='HK' else 'XSHG'
                calendars[row.market]=SessionCalendar.load(project_root()/f'config/market_calendars/{name}.json')
            calendar=calendars[row.market]
            expected=calendar.expected_session(pd.Timestamp(target).date())
            return (expected != pd.Timestamp(target).date()
                    and pd.Timestamp(row.trade_date)==pd.Timestamp(target)
                    and pd.Timestamp(row.source_trade_date)==pd.Timestamp(expected)
                    and row.get('expected_source_trade_date')==expected.isoformat()
                    and row.get('market_session_status')=='closed'
                    and row.get('calendar_sha256')==calendar.digest
                    and row.get('calendar_version')==calendar.version
                    and row.get('calendar_authority')==calendar.authority
                    and pd.notna(row.get('snapshot_id')))
        except (ValueError, OSError, KeyError, TypeError):
            return False
    return aligned | excluded | frame.apply(verified_closure,axis=1)
