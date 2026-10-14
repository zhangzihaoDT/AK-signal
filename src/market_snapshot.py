"""Read-only editorial selection of published Market Facts; no indicator calculation."""
from pathlib import Path
import pandas as pd
from src.common import batch_release as release

VERSION = 'market_snapshot_v1'


def load(stage: Path, stem: str, date: str) -> pd.DataFrame:
    frame = pd.read_parquet(stage / 'observation' / f'{stem}_{date}.parquet')
    if 'trade_date' not in frame or not pd.to_datetime(frame.trade_date).eq(pd.Timestamp(date)).all():
        raise ValueError(f'{stem}: 事实日期不一致')
    return frame


def snapshot(root: Path, stage: Path, receipt: dict) -> dict:
    date = receipt['trade_date']
    assets = load(stage, 'asset_directions', date)
    directions = load(stage, 'industry_directions', date)
    industries = load(stage, 'industry_rotation', date).drop_duplicates('industry_code')
    etfs = load(stage, 'etf_trends', date).drop_duplicates('fund_code')
    positions = load(stage, 'etf_positions', date).drop_duplicates('fund_code')
    state, focus, changes = [], [], []
    broad = assets[assets.direction.eq('A股宽基')]
    if len(broad) == 1:
        r = broad.iloc[0]
        state.append(f"A股宽基：{r.direction_state}；趋势活跃 {r.active_ratio:.1%}。")
    else:
        state.append('宽基环境事实缺失，不能判断整体市场状态。')
    established = int(industries.trend_stage.eq('已形成趋势').sum())
    emerging = int(industries.trend_stage.eq('正在启动').sum())
    fading = int(industries.trend_stage.eq('正在退潮').sum())
    active = int(etfs.trend_active.eq(True).sum())
    state.append(f"行业：{established}/{len(industries)} 已形成趋势，{emerging} 正在启动，{fading} 正在退潮；ETF 活跃 {active}/{len(etfs)}。")
    # Editorial priority: broad participation first, then strength, stable name tie-break.
    # Only existing published strong direction labels qualify; no new trading signal.
    eligible = directions[directions.direction_state.eq('强势上行') & directions.active_count.ge(2)]
    leaders = eligible.sort_values(['active_count', 'active_ratio', 'median_rps15', 'parent_industry'], ascending=[False, False, False, True]).head(2)
    for _, r in leaders.iterrows():
        focus.append(f"{r.parent_industry}：{int(r.active_count)}/{int(r.industry_count)} 个行业趋势活跃（{r.active_ratio:.0%}），中位 RPS15 {r.median_rps15:.1f}；{r.direction_state}。")
    if not focus:
        focus.append('未见至少两个活跃行业支撑的强势方向；单点强势不概括为广泛机会。')
    # Position transitions are existing facts, not actionable entry signals.
    valid = positions[~positions.unreliable_360.fillna(True) & positions.full_360_sample.eq(True)]
    entered = int(valid.bottom_change.eq('ENTERED_BOTTOM').sum())
    left = int(valid.bottom_change.eq('LEFT_BOTTOM').sum())
    focus.append(f"位置观察：有效360日窗口内，{entered} 只 ETF 进入底部、{left} 只离开底部；低位不等于买入信号。")
    previous_date = None
    try:
        from src.common.trading_calendar import default_calendar
        calendar = default_calendar(root)
        # Inspect official calendar API rather than infer weekdays.
        dates = [d.strftime("%Y%m%d") for d in calendar.days]
        previous_date = max(d for d in dates if d < date)
        prior = release.published_batch(root, previous_date)
        if not prior:
            raise ValueError('上一交易日没有可核验的正式发布')
        prior_stage = root / '.run_day/runs' / previous_date / prior['run_id'] / 'outputs'
        old_industries = load(prior_stage, 'industry_rotation', previous_date).drop_duplicates('industry_code')
        old_etfs = load(prior_stage, 'etf_trends', previous_date).drop_duplicates('fund_code')
        common = industries.merge(old_industries, on='industry_code', suffixes=('', '_prior'), validate='one_to_one')
        comparable = common[common.source_status.eq(common.source_status_prior) & common.data_status.eq(common.data_status_prior)]
        if comparable.empty:
            raise ValueError('行业来源口径不可比')
        new = comparable[comparable.trend_stage.eq('已形成趋势') & ~comparable.trend_stage_prior.eq('已形成趋势')]
        out = comparable[~comparable.trend_stage.eq('已形成趋势') & comparable.trend_stage_prior.eq('已形成趋势')]
        def names(frame):
            return '、'.join(frame.sort_values(['delta_rps15_5d','RPS15','industry_name'],ascending=[False,False,True]).industry_name.head(3)) or '无'
        changes.append(f"对比 {previous_date}：新增趋势行业 {len(new)} 个（{names(new)}），退出 {len(out)} 个（{names(out)}）。")
        etf_common = etfs.merge(old_etfs, on='fund_code', suffixes=('', '_prior'), validate='one_to_one')
        etf_common = etf_common[etf_common.source.eq(etf_common.source_prior) & etf_common.data_status.eq(etf_common.data_status_prior)]
        if len(etf_common):
            before = int(etf_common.trend_active_prior.eq(True).sum())
            now = int(etf_common.trend_active.eq(True).sum())
            changes.append(f"同源可比 ETF {len(etf_common)} 只：趋势活跃 {before} → {now}（{now-before:+d}）；行业可比 {len(comparable)}/{len(industries)}。")
        else:
            changes.append('ETF 来源或样本不可比，不能判断活跃度变化。')
        if prior['result'].get('data_complete') != receipt['result'].get('data_complete'):
            changes.append('数据完整性标签发生变化，请先查看质量记录再解释市场变化。')
    except (OSError, ValueError, KeyError, AttributeError):
        changes = [f"{previous_date or '上一交易日'} 无可比正式事实；不能判断跨期重要变化。"]
    return {'version': VERSION, 'tradeDate': date, 'runId': receipt['run_id'],
            'previousDate': previous_date, 'sections': [
                {'title': '市场状态', 'rows': state}, {'title': '关注方向', 'rows': focus},
                {'title': '重要变化', 'rows': changes}],
            'method': '仅汇总正式事实；关注方向按参与广度、强度排序，非买卖建议。'}
