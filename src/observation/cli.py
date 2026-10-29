"""Core observation product: offline facts, reports and validation."""
from __future__ import annotations
import argparse
import os
import json
import re
import hashlib
from datetime import datetime
from pathlib import Path
import numpy as np
import pandas as pd
from src.common.paths import (outputs_dir, raw_dir, stock_metrics_dir, selection_universe_path,
    etf_signal_daily_dir, sw_industry_processed_dir, sw_industry_raw_dir, config_dir, project_root)

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
    from src.market_facts import core_indices
    index_facts = core_indices.build(date, online=os.environ.get('AKSIGNAL_OFFLINE') == '0')
    from src.market_facts import derivatives
    derivative_facts = derivatives.build(date, index_facts, online=os.environ.get('AKSIGNAL_OFFLINE') == '0')
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
    for frame, stem in ((pd.DataFrame(index_facts['rows']), 'core_indices'),
                        (pd.DataFrame(derivative_facts['rows']), 'derivatives'),
                        (trend,'etf_trends'), (assets,'asset_directions'),
                        (industries,'industry_rotation'), (industry_directions,'industry_directions'),
                        (mark_identity(stock_positions(date),date),'stock_positions'), (etf_positions(date),'etf_positions')):
        write_frame(frame, stem, date)
    folder = outputs_dir()/'observation'
    index_path = folder/f'core_indices_{date}.json'
    index_path.write_text(json.dumps(index_facts, ensure_ascii=False, indent=2, allow_nan=False))
    derivative_path = folder/f'derivatives_{date}.json'
    derivative_path.write_text(json.dumps(derivative_facts, ensure_ascii=False, indent=2, allow_nan=False))
    from src.observation.report import render_html, quality_lines, REPORT_LAYOUT_VERSION, PRODUCT_VERSION
    from src.market_facts.group_activity import build_from_daily
    group_activity = build_from_daily(date, stocks, stock_metrics_dir())
    activity_path = folder/f'stock_group_activity_{date}.json'
    activity_path.write_text(json.dumps(group_activity, ensure_ascii=False, indent=2, allow_nan=False))
    quality = quality_lines(date, trend, industries, stocks)
    from src.observation.core_indices import quality_line as index_quality_line
    quality.append(index_quality_line(index_facts))
    from src.observation.derivatives import quality_line as derivative_quality_line
    quality.append(derivative_quality_line(derivative_facts))
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
    from src.observation.industry_events import (CANDIDATE, compare_events, snapshots as registered_snapshots,
                                                 snapshot_rows, write_candidate)
    from src.common.paths import project_root
    etf_positions_frame = pd.read_parquet(folder/f'etf_positions_{date}.parquet')
    # Build computes; it never writes the official registry. A batch that can
    # still fail must not leave a registered snapshot behind, so the day is only
    # offered as a candidate that validate checks and publication commits.
    registry_root = project_root()
    industry_events = None
    snapshot_receipt = None
    candidate_path = outputs_dir()/'run_day'/CANDIDATE
    if industry_available:
        own_path = folder/f'industry_rotation_{date}.parquet'
        histories, problems = registered_snapshots(registry_root)
        # Only earlier published snapshots are history; a stale same-date entry
        # belongs to an unfinished attempt and is rewritten at publication.
        problems = [p for p in problems if date not in p]
        histories = [(d, p) for d, p in histories if d < date]
        industry_events = compare_events(snapshot_rows(industries, date), date, (histories, problems))
        try:
            snapshot_receipt = write_candidate(candidate_path.parent, date, own_path,
                                               run_id=os.environ.get('AKSIGNAL_RUN_ID', ''))
        except OSError as exc:
            quality.append(f'行业快照候选写入失败：{type(exc).__name__}；本批无法在发布时登记，下次运行无法对照，不补造。')
            snapshot_receipt = None
    else:
        candidate_path.unlink(missing_ok=True)
    events_path = folder/f'industry_events_{date}.json'
    events_path.write_text(json.dumps(industry_events, ensure_ascii=False, indent=2, allow_nan=False) if industry_events else '{}')
    # One receipt set drives both the rendered coverage cards and the JSON summary.
    modules = {
        'industry_rotation': industry_module,
        'core_indices': index_facts['module'],
        'derivatives': derivative_facts['module'],
        'market_direction': {'status': 'available', 'rows': len(assets)},
        'etf_trends': {'status': 'available', 'rows': len(trend)},
        'stock_trends': {'status': 'available', 'rows': int(stocks.drop_duplicates(['market','symbol']).shape[0]),
                         'provider': source_policy().get('stocks',{}).get('provider'),
                         'excluded_count': int(stocks.drop_duplicates(['market','symbol']).get('data_status',pd.Series(dtype=str)).eq('excluded').sum())},
        'etf_positions': {'status': 'available', 'rows': len(pd.read_parquet(folder/f'etf_positions_{date}.parquet'))},
        'stock_positions': {'status': 'available', 'rows': len(pd.read_parquet(folder/f'stock_positions_{date}.parquet'))},
    }
    if 'classification_input_status' in trend:
        from src.etf_signal.classification_facts import coverage as classification_coverage
        modules['etf_classification'] = classification_coverage(trend)
        quality.append('ETF 分类读取已发布配置；推断与待核实状态分别保留，方向待核实的产品不参与 RPS 排名。')

    html = render_html(date, assets, industries, trend, stocks,
                       etf_positions_frame,
                       pd.read_parquet(folder/f'stock_positions_{date}.parquet'), quality,
                       module_status=modules, group_activity=group_activity,
                       core_indices=index_facts, derivatives=derivative_facts,
                       industry_events=industry_events)
    (folder/f'observation_{date}.html').write_text(html)
    summary = {'trade_date':date, 'scope':'observation_only','generated_at':datetime.now().isoformat(),
               'etf_count':len(trend),'active_etf_count':int(trend.trend_active.sum()),'industry_count':len(industries),
               'stock_count':len(stocks.drop_duplicates(['market','symbol'])),
               'missing':(['industry_rotation'] if not industry_available else []) +
                         (['core_indices'] if index_facts['module']['status'] != 'available' else []) +
                         (['derivatives'] if derivative_facts['module']['status'] != 'available' else []) +
                         (['etf_classification'] if modules.get('etf_classification', {}).get('status', 'available') != 'available' else []),
               'modules': modules,
               'data_sources': source_policy(),
               'regions':['market_direction','active_trends','bottom_position'],
               'data_quality':quality, 'report_layout_version':REPORT_LAYOUT_VERSION, 'product_version':PRODUCT_VERSION}
    from src.observation.contract import contract
    summary['display_contract'] = contract()
    summary['industry_events'] = ({
        'file': events_path.name,
        'sha256': hashlib.sha256(events_path.read_bytes()).hexdigest(),
        'version': industry_events['version'],
        'comparable': industry_events['comparable'],
        'previous_date': industry_events['previous_date'],
        'counts': {'first_entry': len(industry_events['first_entry']),
                   'first_formed': len(industry_events['first_formed']),
                   'relapse': len(industry_events['relapse'])},
        'problems': industry_events['problems'],
    } if industry_events else None)
    summary['industry_snapshot'] = snapshot_receipt
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
    summary['core_indices'] = {'file': index_path.name,
                               'sha256': hashlib.sha256(index_path.read_bytes()).hexdigest(),
                               'registry_hash': index_facts['registry_hash'],
                               'rule_version': index_facts['rule_version']}
    summary['derivatives'] = {'file': derivative_path.name,
                              'sha256': hashlib.sha256(derivative_path.read_bytes()).hexdigest(),
                              'registry_hash': derivative_facts['registry_hash'],
                              'rule_version': derivative_facts['rule_version']}
    (folder/f'observation_{date}.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    (folder/f'observation_{date}.md').write_text(markdown_summary(date, assets, industries, trend, etf_positions_frame,
                                                                  quality, module_status={'industry_rotation': industry_module},
                                                                  index_facts=index_facts, derivative_facts=derivative_facts,
                                                                  industry_events=industry_events, group_attention=summary.get('group_attention')))



def markdown_summary(date, assets, industries, etfs, etf_positions, quality, module_status=None,
                     index_facts=None, derivative_facts=None, industry_events=None, group_attention=None):
    """Markdown mirror of the HTML display contract: summary, default tables, quality."""
    from src.observation.report import TAGLINE, PRINCIPLE, _quality_parts, _clue_wording
    from src.observation.contract import (ETF_DIRECTION_DEFAULT, ETF_TREND_DEFAULT, INDUSTRY_PREVIEW)
    from src.observation.core_indices import TRENDS as INDEX_TRENDS
    def number(value, percent=False):
        if value is None or pd.isna(value):
            return '—'
        return f'{value:+.2%}' if percent else f'{value:,.2f}'
    lines = [f'# AKsignal 市场观察日报 · {date}', '', TAGLINE, '', PRINCIPLE, '', '## 今日市场速览', '']
    if index_facts is not None:
        by_code = {r['code']: r for r in index_facts['rows']}
        for code in ('000300', '932000', 'H30269'):
            row = by_code.get(code)
            if row:
                lines.append(f'- {row["name"]}：{number(row["return_1d"], True)}（当日）')
    if group_attention:
        lead = [g for g in group_attention['groups'] if g['priority'] in ('优先研究', '重点观察')]
        lines.append(f'- 研究主线：{len(lead)} 条优先观察')
        for block in group_attention['summary_sections']:
            for item in block['items']:
                lines.append(f'  - {_clue_wording(block["kind"])}：{item["label"]}（{item["reason"]}）')
    lines += ['', '## 01 市场环境', '', '### 核心宽基观察', '',
              '默认展示：指数、当日、5日、20日、60日涨跌与均线状态。收盘点位、来源、实际行情日与数据状态在 HTML 详情中。', '',
              '| 指数 | 当日 | 5日 | 20日 | 60日 | 均线状态 |', '|---|---:|---:|---:|---:|---|']
    for row in (index_facts or {}).get('rows', []):
        if row['category'] != 'broad':
            continue
        cells = [row['name'] + (' · 同源基准 000300.SH' if row['code'] == '000300' else '')]
        cells += [number(row[f'return_{n}d'], True) for n in (1, 5, 20, 60)]
        cells.append(INDEX_TRENDS[row['trend_state']])
        lines.append('| ' + ' | '.join(cells) + ' |')
    lines += ['', '### IC 衍生品 · 中证500股指期货', '', '来源：中金所官方日行情。基差 = 期货收盘 − 同批中证500现货收盘；OHLC、结算、成交量与基差点数在 HTML 详情中。', '',
              '| 合约 | 基差率 | 持仓量（手） |', '|---|---:|---:|']
    for r in (derivative_facts or {}).get('rows', []):
        name = (r['contract'] or r['product']) + (' · 持仓最多' if r['is_main_open_interest'] else '')
        oi = f"{r['open_interest']:,}" if r['open_interest'] is not None else '—'
        lines.append(f'| {name} | {number(r["basis_rate"], True)} | {oi} |')
    lines += ['', '### 风格参照：红利低波', '']
    for row in (index_facts or {}).get('rows', []):
        if row['category'] == 'style':
            cells = [row['name']] + [number(row[f'return_{n}d'], True) for n in (1, 5, 20, 60)]
            lines.append('| 指数 | 当日 | 5日 | 20日 | 60日 | 均线状态 |')
            lines.append('|---|---:|---:|---:|---:|---|')
            lines.append('| ' + ' | '.join(cells + [INDEX_TRENDS[row['trend_state']]]) + ' |')
    lines += ['', '## 02 行业轮动', '']
    state = (module_status or {}).get('industry_rotation') or {}
    if state.get('status') == 'unavailable':
        lines.append('UNAVAILABLE：通达信目标日更新或质量门控未通过；本批不展示旧行情，不使用申万替代。')
    elif industries is not None and not industries.empty:
        if industry_events:
            if industry_events.get('comparable'):
                lines.append(f'**行业变化观察 · 较上一已发布快照 {industry_events["previous_date"]}**：'
                             f'首次进入 RPS15 前20 {len(industry_events["first_entry"])} 个，'
                             f'首次转入已形成趋势 {len(industry_events["first_formed"])} 个，'
                             f'趋势退潮 {len(industry_events["relapse"])} 个。')
            else:
                lines.append('行业变化观察：无法判定（' + '；'.join(industry_events.get('problems', [])) + '）。不补造前一日排名或阶段。')
        view = industries.sort_values([c for c in ('RPS15', 'RPS5', 'industry_code') if c in industries],
                                      ascending=False, na_position='last').head(INDUSTRY_PREVIEW)
        lines += ['', f'默认展示前 {INDUSTRY_PREVIEW} 个行业（RPS15 降序）；其余行业与当日 RPS、当日涨幅见 HTML 详情。', '',
                  '| 行业 | RPS15 | RPS5 | 阶段 |', '|---|---:|---:|---|']
        for r in view.itertuples():
            lines.append(f'| {r.industry_name} | {r.RPS15:.1f} | {r.RPS5:.1f} | {r.trend_stage} |')
    lines += ['', '## 03 ETF 观察', '']
    etfs = etfs.copy()
    etfs['daily_return'] = etfs.get('return_1d', pd.Series(index=etfs.index, dtype=float)) / 100
    unclassified = int(etfs.classification_status.eq('unclassified').sum()) if 'classification_status' in etfs else 0
    lines.append(f'已分类样本 {len(etfs) - unclassified} 只；未分类样本 {unclassified} 只另计，分类结果不代表全部 ETF 市场。')
    if assets is not None and not assets.empty and all(c in assets for c in ETF_DIRECTION_DEFAULT):
        lines += ['', '### ETF 分类概览（已分类样本）', '', '| ETF 分类 | 产品数 | RPS15 中位 | 趋势活跃比例 | 方向状态 |', '|---|---:|---:|---:|---|']
        for r in assets.itertuples():
            median = f'{r.median_rps15:.1f}' if pd.notna(r.median_rps15) else '—'
            ratio = f'{r.active_ratio*100:.1f}%' if pd.notna(r.active_ratio) else '—'
            lines.append(f'| {r.direction} | {r.etf_count} | {median} | {ratio} | {r.direction_state} |')
    active = etfs[etfs.trend_active] if 'trend_active' in etfs else etfs.iloc[0:0]
    if not active.empty:
        keys = [c for c in ('rps15', 'liquidity', 'fund_code') if c in active]
        active = active.sort_values(keys, ascending=[c != 'fund_code' for c in keys], na_position='last')
        columns = [c for c in ETF_TREND_DEFAULT if c in active]
        lines += ['', f'### 趋势活跃 ETF · {len(active)} 只', '']
        lines.append('| ' + ' | '.join({'fund_name': 'ETF', 'rps15': 'RPS15', 'rps5': 'RPS5',
                                        'daily_return': '当日涨跌'}[c] for c in columns) + ' |')
        lines.append('|' + '|'.join('---' if c == 'fund_name' else '---:' for c in columns) + '|')
        for _, row in active.iterrows():
            cells = []
            for c in columns:
                value = row[c]
                if c == 'fund_name':
                    cells.append(f'{value}（{row.get("fund_code", "")}）')
                elif c in ('rps15', 'rps5'):
                    cells.append(f'{value:.1f}' if pd.notna(value) else '—')
                else:
                    cells.append(f'{value*100:+.1f}%' if pd.notna(value) else '—')
            lines.append('| ' + ' | '.join(cells) + ' |')
        lines += ['', '价格、MA20、MA60 与分类明细见 HTML 详情；同主题产品按名称连续展示，标注为名称推断分组，未合并计算。']
    if etf_positions is not None and not etf_positions.empty:
        counts = etf_positions.bottom_state.value_counts()
        low = int(sum(counts.get(s, 0) for s in ('DEEP_BOTTOM', 'RECOVERING_FROM_BOTTOM', 'LONG_TERM_LOW')))
        lines += ['', f'### ETF 位置观察 · 长期低位 {low} 只', '',
                  '底部标签沿用原 120/360 日低位规则；120日位置为中期位置观察，60日/360日位置在 HTML 详情中。', '',
                  '| ETF | 底部状态 | 120日位置 | 底部变化 |', '|---|---|---:|---|']
        for r in etf_positions.head(20).itertuples():
            position = f'{r.price_pos_120:.1f}' if pd.notna(r.price_pos_120) else '—'
            lines.append(f'| {r.fund_name}（{r.fund_code}） | {r.bottom_state} | {position} | {r.bottom_change} |')
    lines += ['', '## 04 研究主线', '']
    if group_attention:
        lines += ['| 研究主线 | 趋势阶段 | 启动状态 | 观察变化 | 线索 |', '|---|---|---|---|---|']
        for r in group_attention['groups']:
            lines.append(f'| {r["label"]} | {r["phase"]} | {r["status"]} · {_clue_wording(r["event"])} | '
                         f'{_clue_wording(r["today"])} | {r["priority_label"]} |')
        lines += ['', '趋势阶段描述当前结构，观察变化描述相对上一有效观察日的变化；完整量化证据见 HTML 详情。']
    else:
        lines.append('无法判定：数据缺失或日期/规则版本不一致；未作今日判定。')
    lines += ['', '## 05 数据质量', '', '### 本次需关注的质量限制', '']
    _, attention, normal, _ = _quality_parts(quality)
    lines += [f'- {line}' for _, _, line in attention]
    lines += ['', '<details><summary>技术详情 · 来源清单、价格口径与审计摘要</summary>', '']
    lines += [f'- {line}' for _, _, line in normal]
    lines += ['', '</details>', '', f'完整观察事实见 observation_{date}.html 及同日 parquet/CSV。']
    return '\n'.join(lines) + '\n'


def receipt_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


KIND_LABEL_FOR_VERIFY = {'FIRST_TOP20': '首次进入 RPS15 前20', 'FIRST_FORMED': '首次转入已形成趋势', 'RELAPSE': '趋势退潮'}


def verify_industry_events(events: dict) -> None:
    """Every event must cross the documented boundary between two verified snapshots."""
    if events.get('version') != 'industry_change_events_v1':
        raise ValueError('行业变化观察版本未知')
    if not events.get('comparable'):
        if events.get('events') or events.get('first_entry') or events.get('first_formed') or events.get('relapse'):
            raise ValueError('不可对照时仍输出变化事件')
        if not events.get('problems'):
            raise ValueError('不可对照时未说明原因')
        return
    if events.get('previous_date') is None or events['previous_date'] >= events['trade_date']:
        raise ValueError('变化对照日期不早于本交易日')
    if events.get('previous_source_date') != events['previous_date'] or events.get('current_source_date') != events['trade_date']:
        raise ValueError('源交易日与报告日期不一致')
    top = events['top_rank']
    for event in events['events']:
        if not isinstance(event.get('rps15'), (int, float)) or not isinstance(event.get('rank15'), (int, float)):
            raise ValueError('行业变化事件缺少排名证据')
        if event['kind'] == 'FIRST_TOP20' and not (event['rank15'] <= top < event['previous_rank15']):
            raise ValueError('首次进入前20不成立')
        if event['kind'] == 'FIRST_FORMED' and not (event['stage'] == '已形成趋势' and event['previous_stage'] != '已形成趋势'):
            raise ValueError('首次转入已形成趋势不成立')
        if event['kind'] == 'RELAPSE' and not (event['stage'] == '正在退潮' and event['previous_stage'] != '正在退潮'):
            raise ValueError('趋势退潮事件不成立')
    for key, kind in (('first_entry', 'FIRST_TOP20'), ('first_formed', 'FIRST_FORMED'), ('relapse', 'RELAPSE')):
        if len(events[key]) != sum(e['kind'] == kind for e in events['events']):
            raise ValueError(f'{kind} 计数与事件列表不一致')


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
        from src.market_facts.core_indices import validate_document as validate_indices
        from src.observation.core_indices import panels as index_panels, markdown as index_markdown
        try:
            index_path = folder/f'core_indices_{date}.json'
            content = index_path.read_bytes()
            receipt = summary.get('core_indices', {})
            if receipt.get('file') != index_path.name or receipt.get('sha256') != hashlib.sha256(content).hexdigest():
                raise ValueError('指数观察产物摘要不一致')
            indices = json.loads(content)
            validate_indices(indices, date)
            if (modules.get('core_indices') != indices['module']
                    or receipt.get('registry_hash') != indices['registry_hash']
                    or receipt.get('rule_version') != indices['rule_version']):
                raise ValueError('指数观察回执不一致')
            expected = pd.DataFrame(indices['rows'])
            actual = pd.read_parquet(folder/f'core_indices_{date}.parquet')
            pd.testing.assert_frame_equal(actual[expected.columns], expected, check_dtype=False)
            html_text = (folder/f'observation_{date}.html').read_text()
            md_text = (folder/f'observation_{date}.md').read_text()
            from src.observation.report import core_index_panel
            from html import escape as html_escape
            titles = {'broad': '核心宽基观察', 'style': '风格参照：红利低波'}
            for category in ('broad', 'style'):
                panel = core_index_panel(indices, category, titles[category])
                if panel not in html_text:
                    raise ValueError(f'核心指数{category}面板与事实不一致')
                for row in (r for r in indices['rows'] if r['category'] == category):
                    alias = row['name'] + (' · 风格参照' if category == 'style' else
                                           ' · 同源基准 000300.SH' if row['code'] == '000300' else '')
                    if html_escape(alias) not in html_text or html_escape(row['code']) not in html_text:
                        raise ValueError(f"核心指数 {row['name']} 未按契约展示")
                    if row['category'] == 'style' and row['name'] not in md_text:
                        raise ValueError('风格参照未在 Markdown 中展示')
                    for n in (1, 5, 20, 60):
                        value = row[f'return_{n}d']
                        if value is not None and f'{value:+.2%}' not in html_text:
                            raise ValueError(f"核心指数 {row['name']} {n}日涨跌与事实不一致")
            if indices['module']['status'] != 'available':
                status = 'PARTIAL' if status != 'UNKNOWN' else status
                warnings.append(f"核心指数观察部分不可用：{indices['module']['available_count']}/{indices['module']['expected_count']}；固定行及缺失原因已保留")
        except (OSError, ValueError, KeyError, TypeError, AssertionError) as exc:
            errors.append(f'核心指数观察验收失败：{exc}')
        from src.market_facts.derivatives import validate_document as validate_derivatives
        from src.observation.derivatives import markdown as derivative_markdown
        from src.observation.report import derivative_panel
        from html import escape as html_escape
        try:
            derivative_path = folder/f'derivatives_{date}.json'
            content = derivative_path.read_bytes()
            receipt = summary.get('derivatives', {})
            if receipt.get('file') != derivative_path.name or receipt.get('sha256') != hashlib.sha256(content).hexdigest():
                raise ValueError('衍生品观察产物摘要不一致')
            derivative_doc = json.loads(content)
            index_doc = json.loads((folder/f'core_indices_{date}.json').read_bytes())
            validate_derivatives(derivative_doc, date, index_doc)
            if (modules.get('derivatives') != derivative_doc['module']
                    or receipt.get('registry_hash') != derivative_doc['registry_hash']
                    or receipt.get('rule_version') != derivative_doc['rule_version']):
                raise ValueError('衍生品回执不一致')
            expected = pd.DataFrame(derivative_doc['rows'])
            actual = pd.read_parquet(folder/f'derivatives_{date}.parquet')
            pd.testing.assert_frame_equal(actual[expected.columns], expected, check_dtype=False)
            html = (folder/f'observation_{date}.html').read_text()
            if derivative_panel(derivative_doc) not in html or not html.index('id="core-indices-broad"') < html.index('id="derivatives-observation"') < html.index('id="core-indices-style"'):
                raise ValueError('衍生品展示与事实或顺序不一致')
            if '### IC 衍生品 · 中证500股指期货' not in md_text:
                raise ValueError('衍生品Markdown与事实不一致')
            for row in derivative_doc['rows']:
                name = row['contract'] or row['product']
                if name not in md_text:
                    raise ValueError(f'衍生品合约 {name} 未在 Markdown 中展示')
            for row in derivative_doc['rows']:
                name = row['contract'] or row['product']
                if html_escape(name) not in html:
                    raise ValueError(f'衍生品合约 {name} 未按契约展示')
                if row['basis_rate'] is not None and f'{row["basis_rate"]:+.2%}' not in html:
                    raise ValueError(f'衍生品合约 {name} 基差率与事实不一致')
                if row['open_interest'] is not None and f'{row["open_interest"]:,}' not in html:
                    raise ValueError(f'衍生品合约 {name} 持仓量与事实不一致')
            if derivative_doc['module']['status'] != 'available':
                status = 'PARTIAL' if status != 'UNKNOWN' else status
                warnings.append('衍生品观察不可用；IC固定展示缺失原因')
        except (OSError, ValueError, KeyError, TypeError, AssertionError) as exc:
            errors.append(f'衍生品观察验收失败：{exc}')
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
        from src.observation.report import category_rows, classification
        etf_frame = pd.read_parquet(folder/f'etf_trends_{date}.parquet')
        if 'classification_input_status' in etf_frame:
            from src.etf_signal.classification_facts import coverage as classification_coverage, attach, read_input, load_input, COLUMNS
            try:
                classification_module = classification_coverage(etf_frame)
                if summary.get('modules', {}).get('etf_classification') != classification_module:
                    raise ValueError('分类覆盖回执与事实不一致')
                batch_dir = outputs_dir().resolve().parent
                state_path = batch_dir / 'state.json'
                if state_path.exists():
                    pinned = json.loads(state_path.read_text())['etf_classification']
                    loaded = read_input(Path(pinned['file']), pinned['sha256'])
                else:
                    loaded = load_input()
                expected = attach(etf_frame[['fund_code', 'fund_name']], pd.Timestamp(date), loaded)
                pd.testing.assert_frame_equal(etf_frame[list(COLUMNS)].reset_index(drop=True),
                                              expected[list(COLUMNS)].reset_index(drop=True), check_dtype=False)
                if classification_module['status'] != 'available':
                    status = 'PARTIAL' if status != 'UNKNOWN' else status
                    warnings.append('ETF 分类仍有待核实/缺失：保留行情，分类范围与 RPS 样本受限，不执行名称兜底')
            except (OSError, KeyError, ValueError, TypeError, AssertionError) as exc:
                errors.append(f'ETF 分类配置消费验收失败：{exc}')
        direction_frame = pd.read_parquet(folder/f'asset_directions_{date}.parquet')
        scope = classification(etf_frame)
        listed = int(pd.to_numeric(category_rows(direction_frame).get('etf_count'), errors='coerce').fillna(0).sum())
        if listed != scope['classified']:
            errors.append(f'ETF 分类概览合计 {listed} 与已分类样本 {scope["classified"]} 不一致')
        if '<td>其他</td>' in html:
            errors.append('ETF 分类概览仍包含未分类“其他”行')
        from src.observation.contract import contract
        if summary.get('display_contract') != contract():
            errors.append('日报展示契约与回执不一致')
        from src.observation.contract import SECTION_ANCHORS, CORE_INDEX_DEFAULT, INDUSTRY_DEFAULT, ETF_TREND_DEFAULT, ETF_POSITION_DEFAULT
        if not all(f'<section id="{anchor_}"' in html for anchor_ in SECTION_ANCHORS) or not all(
                f'<a href="#{anchor_}">' in html for anchor_ in SECTION_ANCHORS):
            errors.append('日报五段结构与契约不一致')
        for header in ('>当日</th>', '>5日</th>', '>20日</th>', '>60日</th>', '>均线状态</th>',
                       '>RPS15</th>', '>RPS5</th>', '>阶段</th>', '>基差率</th>', '>持仓量（手）</th>',
                       '>趋势阶段</th>', '>启动状态</th>'):
            if header not in html:
                errors.append(f'日报缺少契约字段 {header.strip("></th")}')
        market_html = html[html.index('id="market-environment"'):html.index('id="industry-rotation"')]
        for detail_only in ('>收盘点位</th>', '>MA20</th>', '>MA60</th>', '>当日涨幅</th>'):
            visible = re.sub(r'<details.*?</details>', '', market_html, flags=re.S)
            if detail_only in visible:
                errors.append(f'详情字段 {detail_only.strip("></th")} 出现在默认表')
        try:
            events_path = folder/f'industry_events_{date}.json'
            receipt = summary.get('industry_events')
            if receipt is None:
                if industry_module.get('status') != 'unavailable':
                    raise ValueError('缺少行业变化观察产物')
            else:
                content = events_path.read_bytes()
                if receipt.get('file') != events_path.name or receipt.get('sha256') != hashlib.sha256(content).hexdigest():
                    raise ValueError('行业变化观察产物摘要不一致')
                events = json.loads(content)
                if events.get('trade_date') != date:
                    raise ValueError('行业变化观察日期未对齐')
                verify_industry_events(events)
                # Validate only checks the batch's own candidate. The registry
                # is written by publication, so its same-date entry cannot be
                # evidence about this batch; history must still resolve because
                # it is what the change observation compares against.
                from src.observation.industry_events import (CANDIDATE, snapshots as registered,
                                                             verify_candidate)
                receipt = summary.get('industry_snapshot') or {}
                candidate_path = outputs_dir()/'run_day'/CANDIDATE
                if not candidate_path.is_file():
                    raise ValueError(f'本交易日 {date} 缺少行业快照候选，发布后无法登记')
                content = candidate_path.read_bytes()
                if receipt.get('file') != candidate_path.name or receipt.get('sha256') != hashlib.sha256(content).hexdigest():
                    raise ValueError('行业快照候选回执与产物摘要不一致')
                entry = verify_candidate(json.loads(content), date, folder)
                if (receipt.get('entry') or {}) != entry:
                    raise ValueError('行业快照候选回执与登记条目不一致')
                # Cross-date change is a downstream comparison, not a condition
                # of the accepted facts: unusable history downgrades it to
                # "无法判定" and never fails the batch.
                _, history_problems = registered()
                history_problems = [p for p in history_problems if date not in p]
                if history_problems:
                    warnings.append('行业变化观察无法对照：' + '；'.join(history_problems))
                if events['comparable'] and history_problems:
                    raise ValueError('变化观察声称可对照，但快照登记表给出了问题')
                if events['comparable'] and html.count('行业变化观察 · 较上一已发布快照') != 1:
                    raise ValueError('行业变化观察未按契约展示')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(f'行业变化观察验收失败：{exc}')
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
