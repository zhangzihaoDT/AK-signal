"""Core observation product: offline facts, reports and validation."""
from __future__ import annotations
import argparse
import os
import json
import hashlib
from datetime import datetime
from pathlib import Path
import numpy as np
import pandas as pd
from src.common.paths import (outputs_dir, raw_dir, stock_metrics_dir, selection_universe_path,
    etf_signal_daily_dir, sw_industry_processed_dir, sw_industry_raw_dir)

WINDOWS = (60, 120, 360)


def position_snapshot(frame: pd.DataFrame, date: str, calendar: pd.DatetimeIndex) -> dict:
    """Stock-specific close-range position; gaps and unknown windows stay unknown.

    No ETF corporate-action detector is applied to stocks: a legitimate 20%
    move is not a fund split. Cache adjustment provenance is reported separately.
    """
    target = pd.Timestamp(date)
    d = frame.copy()
    d['date'] = pd.to_datetime(d['date'])
    d = d[d.date <= target].sort_values('date')
    if d.date.duplicated().any():
        return {'bottom_state': 'UNAVAILABLE', 'long_term_bottom': None, 'reliability': 'DUPLICATE_DATES'}
    calendar = pd.DatetimeIndex(calendar).sort_values().unique()
    calendar = calendar[calendar <= target]
    result = {'bottom_state': 'UNAVAILABLE', 'long_term_bottom': None,
              'reliability': 'MISSING_TARGET', 'history_days': len(d)}
    if d.empty or target not in set(d.date):
        return result
    series = pd.to_numeric(d.set_index('date').close, errors='coerce')
    series = series.reindex(calendar[calendar >= d.date.min()])
    for w in WINDOWS:
        win = series.iloc[-w:]
        reason = ('INSUFFICIENT_HISTORY' if len(win) < w else
                  'DATA_GAP' if win.isna().any() or (win <= 0).any() else
                  'FLAT_WINDOW' if win.max() == win.min() else 'OK')
        result[f'price_pos_{w}'] = float((win.iloc[-1]-win.min())/(win.max()-win.min())*100) if reason == 'OK' else None
        result[f'window_reason_{w}'] = reason
    if result['window_reason_120'] != 'OK' or result['window_reason_360'] != 'OK':
        result['reliability'] = result['window_reason_360'] if result['window_reason_360'] != 'OK' else result['window_reason_120']
        return result
    a, b, c = (result[f'price_pos_{w}'] for w in WINDOWS)
    low = b <= 20 and c <= 20
    result.update(reliability='OK', long_term_bottom=low,
        bottom_state=('DEEP_BOTTOM' if low and a is not None and a <= 20 else
                      'RECOVERING_FROM_BOTTOM' if low and a is not None else
                      'LONG_TERM_LOW' if low else
                      'RECENT_BOTTOM' if a is not None and a <= 20 and c > 20 else 'NORMAL'))
    return result


def position_change(current: dict, previous: dict) -> str:
    a, b = previous.get('long_term_bottom'), current.get('long_term_bottom')
    if a is None or b is None:
        return 'UNKNOWN'
    if a == b:
        return 'UNCHANGED'
    return 'ENTERED_BOTTOM' if b else 'LEFT_BOTTOM'


def stock_metrics(date: str, online: bool) -> None:
    from src.market_facts.policy import source_policy
    if source_policy().get('stocks',{}).get('provider') == 'longbridge':
        from src.market_facts.stock_daily import build_longbridge_stocks
        build_longbridge_stocks(date, online=online)
        return
    from src.selection.universe import load_universe_items
    from src.trend_engine.inputs import build_stock_metrics
    frame = build_stock_metrics(load_universe_items(selection_universe_path()),
                                trade_date=date, offline=not online)
    # Keep numerical trend facts, discard old recommendation-facing fields.
    frame.drop(columns=['action', 'watch_level'], errors='ignore').to_parquet(
        stock_metrics_dir()/f'stock_metrics_{date}.parquet', index=False)


def stock_positions(date: str) -> pd.DataFrame:
    from src.market_facts.policy import source_policy
    if source_policy().get('stocks',{}).get('provider') == 'longbridge':
        # Consume the exact same staged batch as stock trends, never old CSVs.
        path = stock_metrics_dir()/f'stock_position_inputs_{date}.parquet'
        if not path.exists():
            raise ValueError('缺少本批次长桥个股位置事实')
        frame = pd.read_parquet(path)
        if not (pd.to_datetime(frame.trade_date)==pd.Timestamp(date)).all() or not frame.source.eq('longbridge').all():
            raise ValueError('长桥个股位置来源或日期不一致')
        trends = pd.read_parquet(stock_metrics_dir()/f'stock_metrics_{date}.parquet')
        evidence = frame.merge(trends[['market','symbol','snapshot_id','data_status']],on=['market','symbol'],
                               how='left',suffixes=('_position','_trend'),validate='many_to_one')
        active = evidence.reliability.ne('EXCLUDED')
        if evidence.loc[active,'snapshot_id_position'].isna().any() or not evidence.loc[active,'snapshot_id_position'].eq(evidence.loc[active,'snapshot_id_trend']).all():
            raise ValueError('个股趋势与位置未消费同一长桥快照')
        return frame
    from src.selection.universe import load_universe_items
    from src.trend_engine.inputs import stock_items
    from src.trend_engine.engine import _raw_cache_path, _load_cached_raw
    items = stock_items(load_universe_items(selection_universe_path()))
    frames, calendars = {}, {}
    for item in items:
        key = (item.asset.market, item.asset.symbol)
        if key in frames:
            continue
        path = _raw_cache_path(raw_dir(), item.asset)
        frame = _load_cached_raw(path) if path.exists() else pd.DataFrame(columns=['date', 'close'])
        frames[key] = frame
        calendars.setdefault(key[0], set()).update(pd.to_datetime(frame.date[frame.date <= pd.Timestamp(date)]))
    rows = []
    for item in items:
        key = (item.asset.market, item.asset.symbol)
        calendar = pd.DatetimeIndex(sorted(calendars[key[0]]))
        current = position_snapshot(frames[key], date, calendar)
        prior = calendar[calendar < pd.Timestamp(date)]
        previous = position_snapshot(frames[key], str(prior[-1].date()), calendar) if len(prior) else {}
        rows.append({'market': key[0], 'symbol': key[1], 'name': item.asset.name,
                     'theme': item.theme, 'tier': item.tier, 'participation': item.participation,
                     'trade_date': pd.Timestamp(date), **current,
                     'bottom_change': position_change(current, previous),
                     'price_basis': 'cached_source_prices_not_certified',
                     'price_basis_verified': False,
                     'rule_version': 'stock_position_v1',
                     'calendar_basis': 'observed_market_dates_union'})
    return pd.DataFrame(rows)


def etf_positions(date: str) -> pd.DataFrame:
    from src.research.etf_bottom.price_map import build_price_map
    current = build_price_map(date)
    # Previous snapshot is read-only and independently computed; no lifecycle machine.
    from src.common.paths import etf_signal_raw_dir
    calendar = set()
    for p in etf_signal_raw_dir().glob('*.parquet'):
        d = pd.read_parquet(p, columns=['date'])
        calendar.update(pd.to_datetime(d.date[d.date < pd.Timestamp(date)]))
    previous = build_price_map(max(calendar)) if calendar else pd.DataFrame()
    prev = previous.set_index('fund_code').to_dict('index') if not previous.empty else {}
    current['trade_date'] = pd.Timestamp(date)
    def change(row):
        a = dict(row)
        if a.get('bottom_state') == 'UNRELIABLE' or any(a.get(f'window_reason_{w}') != 'OK' for w in (120,360)):
            a['long_term_bottom'] = None
        b = dict(prev.get(row.fund_code, {}))
        if b.get('bottom_state') == 'UNRELIABLE' or any(b.get(f'window_reason_{w}') != 'OK' for w in (120,360)):
            b['long_term_bottom'] = None
        return position_change(a, b)
    current['bottom_change'] = current.apply(change, axis=1)
    return current


def write_frame(frame: pd.DataFrame, stem: str, date: str) -> None:
    folder = outputs_dir()/'observation'
    folder.mkdir(parents=True, exist_ok=True)
    frame = frame.copy()
    # Preserve original Observation values and audit metadata when present.
    if 'trade_date' not in frame:
        frame['trade_date'] = pd.Timestamp(date)
    if 'run_date' not in frame:
        frame['run_date'] = pd.Timestamp.now(tz='Asia/Shanghai').date().isoformat()
    if 'generated_at' not in frame:
        frame['generated_at'] = pd.Timestamp.now(tz='UTC').isoformat()
    frame.to_parquet(folder/f'{stem}_{date}.parquet', index=False)
    frame.to_csv(folder/f'{stem}_{date}.csv', index=False)


def build(date: str) -> None:
    from src.sw_industry_rps import storage
    from src.sw_industry_rps.report import classify_trend_stage
    from src.sw_industry_rps.metrics import cross_industry_direction
    from src.etf_signal.rotation import cross_asset_direction
    rotation = pd.read_parquet(etf_signal_daily_dir()/f'rotation_{date}.parquet')
    indicators = pd.read_parquet(etf_signal_daily_dir()/f'daily_indicators_{date}.parquet')
    stocks = pd.read_parquet(stock_metrics_dir()/f'stock_metrics_{date}.parquet')
    from src.market_facts.policy import source_policy
    industry_policy = source_policy().get('industry',{})
    tdx_pending = industry_policy.get('provider') == 'tdx'
    industry_error = ''
    if tdx_pending:
        industries = pd.DataFrame(columns=['industry_code','industry_name','parent_industry','RPS15','RPS5',
                                          'trend_stage','source_status','data_status','close','source_trade_date','source'])
        try:
            from src.market_facts.tdx_industry import load
            industries = load(date, online=os.environ.get('AKSIGNAL_OFFLINE') == '0')
        except Exception as exc:
            industry_error = f'{type(exc).__name__}: {exc}'
    else:
        try:
            industries = storage.load_metrics(sw_industry_processed_dir())
            industries = industries[pd.to_datetime(industries.trade_date) == pd.Timestamp(date)].copy()
        except (OSError, ValueError, KeyError, TypeError):
            industries = pd.DataFrame()
    if any(d.empty for d in (rotation, indicators, stocks)):
        raise ValueError('核心观察输入为空')
    from src.observation.report import mark_identity
    stocks=mark_identity(stocks,date)
    # Numerical trend predicates, without BUY/WATCH/Timing policy labels.
    trend = rotation.merge(indicators[['fund_code','price','ma20','ma60','ma20_slope','ma60_slope']], on='fund_code', validate='one_to_one')
    trend['above_ma20'] = trend.price > trend.ma20
    trend['above_ma60'] = trend.price > trend.ma60
    trend['trend_active'] = trend.above_ma20 & trend.above_ma60 & (trend.ma20_slope > 0) & (trend.ma60_slope > 0)
    assets = pd.DataFrame(cross_asset_direction(trend))
    # The old helper expects policy trend_state; replace breadth with factual MA breadth.
    from src.etf_signal.rotation import _cross_asset_direction
    direction = trend.apply(_cross_asset_direction, axis=1)
    if not assets.empty:
        assets['active_ratio'] = assets.direction.map(trend.groupby(direction).trend_active.mean())
    from src.sw_industry_rps.source_status import classify_source_status, classify_data_status
    from src.sw_industry_rps.cli import _observation_falls_on_closed_day
    from src.sw_industry_rps.source_status import price_source_dates, verified_price_dates
    try:
        active_codes = [] if tdx_pending else storage.compute_active_codes(sw_industry_raw_dir())[0]
    except (OSError, ValueError, KeyError, TypeError):
        active_codes = []
    if tdx_pending:
        from src.market_facts.tdx_industry import universe
        expected_codes = set(universe().tdx_code)
    else:
        expected_codes = set(active_codes)
    actual_codes = set(industries.get('industry_code', pd.Series(dtype=str)).dropna().astype(str))
    verified_rows = verified_price_dates(industries) if not industries.empty else pd.Series(dtype=bool)
    industry_available = bool(
        expected_codes
        and actual_codes == expected_codes
        and not industries.get('industry_code', pd.Series(dtype=str)).duplicated().any()
        and len(verified_rows) == len(industries)
        and bool(verified_rows.all())
    )
    valid_codes = set(
        industries.loc[verified_rows, 'industry_code'].dropna().astype(str)
    ) & expected_codes if not industries.empty and 'industry_code' in industries else set()
    industry_module = {
        'status': 'available' if industry_available else 'unavailable',
        'trade_date': date,
        'verified_count': len(valid_codes),
        'expected_count': len(expected_codes),
        'missing_count': max(0, len(expected_codes) - len(valid_codes)),
        'reason': '' if industry_available else (
            'industry_master_missing' if not expected_codes else 'target_date_missing_or_unverified'
        ),
    }
    if tdx_pending:
        industry_module.update(provider='tdx', classification='TDX Research Industry',
                               classification_version=industry_policy.get('classification_snapshot'),
                               reason=industry_error or industry_module['reason'], no_cross_provider_fallback=True,
                               status_code='AVAILABLE' if industry_available else 'UNAVAILABLE')
    if industry_available:
        industries['source_trade_date'] = price_source_dates(industries)
        industries['source_status'] = 'primary' if tdx_pending else industries.source.map(lambda source: classify_source_status([source]))
        industries['data_status'] = industries.source_status.map(lambda status: classify_data_status(status, _observation_falls_on_closed_day(pd.Timestamp(date))))
        industries['generated_at'] = pd.Timestamp.now(tz='UTC').isoformat()
        industries['trend_stage'] = industries.apply(classify_trend_stage, axis=1)
        industries['strength_level'] = np.where(industries.RPS15 >= 80, '观察', '—')
        industry_directions = pd.DataFrame(cross_industry_direction(industries))
        industries.drop(columns=['strength_level'], inplace=True)
    else:
        # An industry cross-section is all-or-nothing: never rank a partial universe
        # or let a previous trade_date masquerade as today's industry observation.
        industries = industries.iloc[0:0].copy()
        industry_directions = pd.DataFrame(columns=[
            'parent_industry', 'industry_count', 'median_rps15', 'median_rps1',
            'median_delta_rps15_5d', 'active_count', 'active_ratio',
            'representative_industry', 'direction_state',
        ])
    for frame, stem in ((trend,'etf_trends'), (assets,'asset_directions'),
                        (industries,'industry_rotation'), (industry_directions,'industry_directions'),
                        (mark_identity(stock_positions(date),date),'stock_positions'), (etf_positions(date),'etf_positions')):
        write_frame(frame, stem, date)
    folder = outputs_dir()/'observation'
    from src.observation.report import render_html, quality_lines, REPORT_LAYOUT_VERSION, PRODUCT_VERSION
    from src.market_facts.group_activity import build_from_daily
    group_activity = build_from_daily(date, stocks, stock_metrics_dir())
    activity_path = folder/f'stock_group_activity_{date}.json'
    activity_path.write_text(json.dumps(group_activity, ensure_ascii=False, indent=2, allow_nan=False))
    quality = quality_lines(date, trend, industries, stocks)
    available_groups = sum(r['status']!='UNAVAILABLE' for r in group_activity['groups'])
    quality.append(f'分组趋势启动观察：{available_groups}/{len(group_activity["groups"])} 组可判定；复用本批长桥快照，缺失、休市或历史不足不判为无启动候选。规则 {group_activity["rule_version"]}；仅为阅读提示。')
    if tdx_pending:
        quality.append('行业数据契约：通达信官方 .day · TDX Research Industry 20261016 · 128 个二级行业；未复权指数；AKsignal 独立收益率及 RPS；不使用申万替代。')
        if industry_available:
            quality.append(f'行业历史门控：120日有效 {int(industries.valid_count_120d.iloc[0])}/128；250日有效 {int(industries.valid_count_250d.iloc[0])}/128；历史不足指标标记 INSUFFICIENT_HISTORY。')
    if not industry_available:
        quality.append(
            f"行业轮动模块降级：目标日价格已核验 {industry_module['verified_count']}/"
            f"{industry_module['expected_count']}；本批隐藏行业结论，不使用旧日期或不完整横截面。"
        )
    html = render_html(date, assets, industries, trend, stocks,
                       pd.read_parquet(folder/f'etf_positions_{date}.parquet'),
                       pd.read_parquet(folder/f'stock_positions_{date}.parquet'), quality,
                       module_status={'industry_rotation': industry_module}, group_activity=group_activity)
    (folder/f'observation_{date}.html').write_text(html)
    modules = {
        'market_direction': {'status': 'available', 'rows': len(assets)},
        'etf_trends': {'status': 'available', 'rows': len(trend)},
        'industry_rotation': industry_module,
        'stock_trends': {'status': 'available', 'rows': int(stocks.drop_duplicates(['market','symbol']).shape[0]),
                         'provider': source_policy().get('stocks',{}).get('provider'),
                         'excluded_count': int(stocks.drop_duplicates(['market','symbol']).get('data_status',pd.Series(dtype=str)).eq('excluded').sum())},
        'etf_positions': {'status': 'available', 'rows': len(pd.read_parquet(folder/f'etf_positions_{date}.parquet'))},
        'stock_positions': {'status': 'available', 'rows': len(pd.read_parquet(folder/f'stock_positions_{date}.parquet'))},
    }
    summary = {'trade_date':date, 'scope':'observation_only','generated_at':datetime.now().isoformat(),
               'etf_count':len(trend),'active_etf_count':int(trend.trend_active.sum()),'industry_count':len(industries),
               'stock_count':len(stocks.drop_duplicates(['market','symbol'])),
               'missing':['industry_rotation'] if not industry_available else [],
               'modules': modules,
               'data_sources': source_policy(),
               'regions':['market_direction','active_trends','bottom_position'],
               'data_quality':quality, 'report_layout_version':REPORT_LAYOUT_VERSION, 'product_version':PRODUCT_VERSION}
    summary['group_activity'] = {'file':activity_path.name,
                               'sha256':hashlib.sha256(activity_path.read_bytes()).hexdigest(),
                               'available_count':available_groups,
                               'total_count':len(group_activity['groups']),
                               'rule_version':group_activity['rule_version'],
                               'input_hash':group_activity['input_hash'],
                               'states':[{k:r.get(k) for k in ('key','status','first_trigger','path','event_origin','transition','reasons','metrics','leader_clues','evidence_channel','alert_kind','event_active','episode_status','trigger_channel')}
                                         for r in group_activity['groups']]}
    from src.observation.group_attention import attention_view
    summary['group_attention'] = attention_view(group_activity)
    (folder/f'observation_{date}.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    from src.observation.report import PRINCIPLE
    markdown = f'# AKsignal 观察日报 · {date}\n\n{PRINCIPLE}\n\n## 市场方向\n\n大类资产与行业轮动。\n\n## 趋势活跃\n\nETF 与固定池个股。\n\n## 底部位置\n\nETF 与固定池个股的底部状态、变化和窗口可靠性。\n\n## 数据质量\n\n'+ '\n'.join('- '+line for line in quality) + f'\n\n完整观察事实见 observation_{date}.html 及同日 parquet/CSV。\n'
    (folder/f'observation_{date}.md').write_text(markdown)



def validate(date: str) -> dict:
    from src.market_facts.policy import aligned_stock_rows
    folder = outputs_dir()/'observation'
    errors = []
    summary_path = folder/f'observation_{date}.json'
    summary = json.loads(summary_path.read_text(encoding='utf-8')) if summary_path.exists() else {}
    modules = summary.get('modules') or {}
    industry_module = modules.get('industry_rotation')
    industry_unavailable = isinstance(industry_module, dict) and industry_module.get('status') == 'unavailable'
    warnings = []
    for stem in ('etf_trends','asset_directions','industry_rotation','industry_directions','stock_positions','etf_positions'):
        p = folder/f'{stem}_{date}.parquet'
        if not p.exists():
            errors.append(f'缺少 {p.name}')
            continue
        d = pd.read_parquet(p)
        if d.empty and not (industry_unavailable and stem in ('industry_rotation', 'industry_directions')):
            errors.append(f'{p.name} 为空')
        if industry_unavailable and stem in ('industry_rotation', 'industry_directions') and not d.empty:
            errors.append(f'{p.name} 含行业模块降级前的旧事实')
        if stem not in ('asset_directions','industry_directions') and ('trade_date' not in d or not (pd.to_datetime(d.trade_date)==pd.Timestamp(date)).all()):
            errors.append(f'{p.name} 日期未对齐')
    from src.final_validation.cli import data_quality_warnings
    warnings.extend(x['message'] for x in data_quality_warnings(date))
    warnings.append('观察池个股价格依据独立记录；供应商复权算法未独立认证，不标为已验证')
    from src.observation.report import identity_issues
    for issue in identity_issues(date):
        warnings.append(f'IDENTITY_MISMATCH: {issue["market"]} {issue["symbol"]} 配置名 {issue["configured_name"]}，实际 {issue["observed_name"]}；正文已排除')
    industries = pd.read_parquet(folder/f'industry_rotation_{date}.parquet') if (folder/f'industry_rotation_{date}.parquet').exists() else pd.DataFrame()
    states = set(industries.get('data_status',pd.Series(dtype=str)).dropna())
    status = 'PARTIAL' if industry_unavailable else ('PARTIAL' if 'partial' in states else 'COMPLETE' if states & {'complete','provisional'} else 'CONFIRMED' if states == {'confirmed'} else 'UNKNOWN')
    if industry_unavailable:
        if not industries.empty:
            errors.append('行业模块标记 unavailable 但事实产物仍含行业行')
        warnings.append(
            f"行业轮动模块不可用：目标日已核验 {industry_module.get('verified_count', 0)}/"
            f"{industry_module.get('expected_count', 0)}；其余观察模块已独立验收"
        )
    if not industries.empty:
        from src.sw_industry_rps.source_status import verified_price_dates
        if not verified_price_dates(industries).all():
            errors.append('行业价格缺少独立源交易日证据或日期未对齐')
            status = 'UNKNOWN'
        from src.sw_industry_rps import storage
        from src.market_facts.policy import source_policy
        if source_policy()['industry']['provider'] == 'tdx':
            from src.market_facts.tdx_industry import universe
            expected = set(universe().tdx_code)
        else:
            history = storage.load_metrics(sw_industry_processed_dir())
            expected = set(history.industry_code.dropna().astype(str))
        actual = set(industries.industry_code.dropna().astype(str))
        if actual != expected or industries.industry_code.duplicated().any():
            errors.append('行业覆盖不完整或有重复代码')
    stocks_path = stock_metrics_dir()/f'stock_metrics_{date}.parquet'
    if not stocks_path.exists():
        errors.append('观察池个股趋势缺失')
    else:
        stocks = pd.read_parquet(stocks_path)
        if stocks.empty or not {'trade_date','source_trade_date','data_status'}.issubset(stocks.columns):
            errors.append('个股趋势缺少日期证据')
        elif not aligned_stock_rows(stocks,date).all():
            errors.append('个股趋势未对齐')
    from src.observation.report import REPORT_LAYOUT_VERSION
    if summary.get('report_layout_version') not in (None, REPORT_LAYOUT_VERSION):
        errors.append('日报布局版本过期，须重建展示产物')
    if summary.get('report_layout_version') == REPORT_LAYOUT_VERSION:
        from src.market_facts.group_activity import validate_document
        activity_path = folder/f'stock_group_activity_{date}.json'
        try:
            content = activity_path.read_bytes()
            receipt = summary.get('group_activity', {})
            if receipt.get('file') != activity_path.name or receipt.get('sha256') != hashlib.sha256(content).hexdigest():
                raise ValueError('分组趋势启动观察产物摘要不一致')
            activity = json.loads(content)
            validate_document(activity, date, pd.read_parquet(stocks_path), stock_metrics_dir())
            from src.observation.group_attention import attention_view
            if summary.get('group_attention') != attention_view(activity):
                raise ValueError('日报JSON展示解释与事实不一致')
            from src.observation.report import validate_activity_rendering
            validate_activity_rendering(date, activity, receipt, (folder/f'observation_{date}.html').read_text())
            if any(r['status'] == 'UNAVAILABLE' for r in activity['groups']):
                warnings.append('分组趋势启动观察部分不可用；已区分休市、历史不足与无启动候选，不影响其他事实模块')
        except (OSError, ValueError, KeyError) as exc:
            errors.append(f'分组趋势启动观察验收失败：{exc}')
    for stem in ('stock_positions','etf_positions'):
        path=folder/f'{stem}_{date}.parquet'
        if path.exists():
            frame=pd.read_parquet(path)
            eligible=frame[frame.reliability.ne('EXCLUDED')] if 'reliability' in frame else frame
            n=int(eligible.bottom_change.eq('UNKNOWN').sum())
            if n:
                warnings.append(f'{stem}: {n} 条位置变化未知（历史不足、缺口或无适用位置）')
    if status == 'UNKNOWN':
        errors.append('行业来源完整性状态未知')
    if status == 'COMPLETE':
        warnings.append('FALLBACK_SOURCE')
    result = {'ok':not errors,'trade_date':date,'scope':'observation_only','status':status,'warnings':warnings,'errors':errors,'data_complete':not warnings,
              'modules': modules,
              'industry_price_dates_verified':not industries.empty and bool(verified_price_dates(industries).all())}
    target=outputs_dir()/'run_day'; target.mkdir(parents=True,exist_ok=True)
    (target/f'final_check_{date}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    if errors:
        raise ValueError('; '.join(errors))
    return result


def main():
    parser=argparse.ArgumentParser(description='趋势与底部位置观察系统')
    parser.add_argument('command',choices=['stock-metrics','build','check','summary'])
    parser.add_argument('--date',default='')
    parser.add_argument('--allow-online-fetch',action='store_true')
    args=parser.parse_args()
    date=args.date
    if not date and args.command in ('check','summary'):
        path=outputs_dir()/'run_day/publication.json'
        if path.exists():
            date=json.loads(path.read_text())['trade_date']
    if not date:
        from src.daily_run import resolve_date
        date=resolve_date()
    date=pd.Timestamp(date).strftime('%Y%m%d')
    if args.command=='summary':
        print((outputs_dir()/'observation'/f'observation_{date}.md').read_text())
        return
    {'stock-metrics':lambda:stock_metrics(date,args.allow_online_fetch),
     'build':lambda:build(date),'check':lambda:print(json.dumps(validate(date),ensure_ascii=False,indent=2))}[args.command]()
