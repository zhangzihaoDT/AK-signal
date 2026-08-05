"""
historical_signals schema（v0.5 Replay 产物契约）。

统一记录「某 trade_date，各层（Layer① ETF / Layer② 行业 / Layer③ 标的）产生什么状态」，
同时保留状态与决策依据，并携带 rule_version / config_hash 以隔离配置漂移。

rule_version / config_hash 来自统一 Strategy Specification（src/common/spec）：
config_hash 覆盖主题/资产池/策略/指标/执行/组合全部策略配置。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.common.spec.hash import config_hash
from src.common.spec.model import RULE_VERSION

# 规则版本：与产生这些信号的规则集对齐（统一 Strategy Specification v0.6.1）
RULE_VERSION = RULE_VERSION

# 统一 schema 列
SIGNAL_COLUMNS = [
    "trade_date",           # 信号分区日（最近完整交易日）YYYYMMDD
    "signal_origin",        # replayed / observed
    "entity_type",          # etf / industry / stock
    "entity_code",
    "theme",
    "layer",                # 1 / 2 / 3
    "rps15",
    "trend_score",
    "trend_state",          # Layer1: BUY_CANDIDATE/STRONG_WATCH/WATCH/OUT_OF_SCOPE
    "confirmation_status",  # Layer2: 强势/观察/中性/弱势
    "selection_status",     # Layer3: RECOMMENDED/QUALIFIED/WATCH/UNAVAILABLE
    "recommended_action",   # Layer3: BUY/OBSERVE/WAIT（当日方向）
    "signal_reason",
    "source_trade_date",    # 实际数据日期（YYYYMMDD）
    "data_status",          # 数据质量：confirmed/provisional/current/stale/missing
    "rule_version",
    "config_hash",
]



def _fmt_date(v: Any) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return ""
    try:
        return str(pd.Timestamp(v).strftime("%Y%m%d"))
    except Exception:
        return str(v)


def new_row(**kw: Any) -> dict[str, Any]:
    """构造一条符合 SIGNAL_COLUMNS 的行（缺失列填空，date 归一化为 YYYYMMDD）。"""
    row: dict[str, Any] = {c: "" for c in SIGNAL_COLUMNS}
    for k, v in kw.items():
        if k in ("trade_date", "source_trade_date") and v not in (None, ""):
            row[k] = _fmt_date(v)
        else:
            row[k] = v
    return row


def empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=SIGNAL_COLUMNS)
