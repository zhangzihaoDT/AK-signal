"""v0.7.3 daily display contract.

This module fixes *what the daily report shows by default* for each module and
what moves into the detail expansion. It is a presentation contract only: it
never selects, recalculates or re-ranks facts. Facts, rule states and the
research-basket state machine are consumed exactly as produced upstream.

Reading order (three layers):
  1. Summary   — 今日市场速览: deterministic template over the facts below.
  2. Observation — 01–05 default tables.
  3. Evidence  — expanded detail per module, 指标与规则 and 数据质量技术详情.
"""
from __future__ import annotations

CONTRACT_VERSION = 'daily_display_contract_v1'

# (anchor, number, title, subtitle)
SECTIONS = (
    ('market-environment', '01', '市场环境', '宽基指数 · 风格 · IC 基差'),
    ('industry-rotation', '02', '行业轮动', '行业强弱 · 趋势阶段 · 变化'),
    ('etf-observation', '03', 'ETF 观察', '趋势活跃 · 底部位置 · 分类'),
    ('research-mainlines', '04', '研究主线', '研究篮子 · WATCH/EMERGING · 证据'),
    ('data-quality', '05', '数据质量', '覆盖率 · 异常 · 口径'),
)
SECTION_ANCHORS = tuple(section[0] for section in SECTIONS)

# Default columns per module panel; everything else is a detail field.
CORE_INDEX_DEFAULT = ('name', 'return_1d', 'return_5d', 'return_20d', 'return_60d', 'trend_state')
CORE_INDEX_DETAIL = ('close', 'provider', 'source_trade_date', 'status')
CORE_INDEX_AUDIT = ('issuer', 'code', 'provider_symbol', 'snapshot_id', 'calendar_version',
                    'identity_verified', 'price_basis_verified', 'history_days')
DERIVATIVE_DEFAULT = ('contract', 'basis_rate', 'open_interest')
DERIVATIVE_DETAIL = ('close', 'return_1d', 'settle', 'volume', 'basis_points',
                     'source_trade_date', 'status')
DERIVATIVE_AUDIT = ('security_id', 'previous_source_trade_date', 'previous_close', 'pre_settle',
                    'open_interest_change', 'close_vs_pre_settle', 'quote_unit', 'volume_unit',
                    'contract_multiplier_cny', 'source_url')
INDUSTRY_DEFAULT = ('industry_name', 'RPS15', 'RPS5', 'trend_stage')
INDUSTRY_DETAIL = ('RPS1', 'daily_return', 'parent_industry', 'valid_count_15d', 'valid_count_5d',
                   'source_trade_date')
INDUSTRY_AUDIT = ('industry_code', 'return_1d', 'return_5d', 'return_15d', 'return_20d',
                  'history_bars', 'data_status', 'source')
INDUSTRY_PREVIEW = 15
ETF_DIRECTION_DEFAULT = ('direction', 'etf_count', 'median_rps15', 'active_ratio', 'direction_state')
ETF_DIRECTION_DETAIL = ('median_rps5', 'median_rps1', 'change_5d', 'rep_name')
ETF_TREND_DEFAULT = ('fund_name', 'rps15', 'rps5', 'daily_return')
ETF_TREND_DETAIL = ('fund_code', 'rps1', 'investment_market', 'investment_direction', 'liquidity', 'price', 'ma20', 'ma60',
                    'tracking_index_status', 'classification_status', 'market_classification_status',
                    'direction_classification_status', 'classification_version', 'classification_basis')
ETF_POSITION_DEFAULT = ('fund_name', 'bottom_state', 'price_pos_120', 'bottom_change')
ETF_POSITION_DETAIL = ('fund_code', 'price_pos_60', 'price_pos_360', 'reliability',
                       'window_reason_120', 'window_reason_360', 'bottom_rule_note')
ETF_POSITION_PREVIEW = 20
MAINLINE_DEFAULT = ('label', 'phase', 'status', 'observation_change')
MAINLINE_DETAIL = ('event', 'priority_label', 'opportunity', 'rs_20d_pct', 'rs_20d_change_5d_pp',
                   'participation_5d', 'participation_10d', 'participation_20d', 'nav', 'ma5',
                   'ma20', 'return_5d', 'return_20d', 'trigger_channel', 'evidence_channel')
# Stock columns are identical on every group panel and live only in its detail.
STOCK_DETAIL = ('name', 'market_session_status', 'source_trade_date', 'trend_score', 'return_5d',
                'return_20d', 'bottom_state', 'price_pos_120', 'price_pos_360', 'reliability')
QUALITY_SUMMARY = ('交易日期', '核心指数可用', '行业样本', 'ETF趋势样本', '固定池股票')
# Quality lines that are limits/attention regardless of wording, by prefix.
QUALITY_ATTENTION_PREFIXES = ('市场休市', '身份错配', '复权审计', '复权验证', '缓存', '分组趋势启动观察',
                              '核心指数观察部分', '衍生品观察不可用', '行业轮动模块', 'ETF 身份元数据',
                              'ETF实际计算序列来源', '股票价格口径', '个股历史限制')
QUALITY_ATTENTION_WORDS = ('未分类', '缺跟踪指数', '尚未', '未知', '不完整', '部分不可用', 'price_basis_verified=false')


def contract():
    """Machine-readable copy written next to every daily report."""
    return {
        'version': CONTRACT_VERSION,
        'sections': [dict(anchor=a, number=n, title=t, subtitle=s) for a, n, t, s in SECTIONS],
        'modules': {
            'core_indices': dict(default=list(CORE_INDEX_DEFAULT), detail=list(CORE_INDEX_DETAIL), audit=list(CORE_INDEX_AUDIT)),
            'derivatives': dict(default=list(DERIVATIVE_DEFAULT), detail=list(DERIVATIVE_DETAIL), audit=list(DERIVATIVE_AUDIT)),
            'industry_rotation': dict(default=list(INDUSTRY_DEFAULT), detail=list(INDUSTRY_DETAIL),
                                      audit=list(INDUSTRY_AUDIT), preview=INDUSTRY_PREVIEW, search=True, expand='其余行业'),
            'etf_classification': dict(default=list(ETF_DIRECTION_DEFAULT), detail=list(ETF_DIRECTION_DETAIL),
                                       scope='已分类样本；未分类样本单列，不代表全部 ETF 市场'),
            'etf_trend': dict(default=list(ETF_TREND_DEFAULT), detail=list(ETF_TREND_DETAIL), preview='当前趋势活跃全部', search=True),
            'etf_position': dict(default=list(ETF_POSITION_DEFAULT), detail=list(ETF_POSITION_DETAIL),
                                 preview=ETF_POSITION_PREVIEW, search=True, expand='其余 ETF',
                                 note='底部标签沿用原 120/360 日低位规则；120日位置为中期位置观察，不改变底部定义'),
            'research_mainlines': dict(default=list(MAINLINE_DEFAULT), detail=list(MAINLINE_DETAIL),
                                       rename='已有机会→已有观察线索；新增机会→新增观察线索',
                                       stock_columns='每组详情；逐产品/逐股计算，不合并'),
            'data_quality': dict(summary=list(QUALITY_SUMMARY),
                                 attention='异常优先；正常来源与日期不逐行重复',
                                 detail='完整来源清单、价格口径、审计摘要、窗口门控与异常代码'),
        },
    }
