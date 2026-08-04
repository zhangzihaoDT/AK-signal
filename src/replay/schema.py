"""
historical_signals schema（v0.5 Replay 产物契约）。

统一记录「某 trade_date，各层（Layer① ETF / Layer② 行业 / Layer③ 标的）产生什么状态」，
同时保留状态与决策依据，并携带 rule_version / config_hash 以隔离配置漂移。

与 daily pipeline 的映射：
  Layer1  entity_type=etf      trend_state  ← watchlist/account_candidates
  Layer2  entity_type=industry confirmation_status  ← confirmation.strength_level
  Layer3  entity_type=stock/etf selection_status / recommended_action  ← selection candidates
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pandas as pd

from src.common.paths import config_dir

# 规则版本：与产生这些信号的规则集对齐（selection v0.4.3）
RULE_VERSION = "v0.4.3"

# 参与 config_hash 的配置文件（决定主题/资产池/阈值规则）
CONFIG_FILES = [
    ("themes_two_directions.yaml", "themes_two_directions.yaml"),
    ("stock_universe.yaml", "stock_universe.yaml"),
    ("sw_industry_rps.yaml", "sw_industry_rps.yaml"),
    ("guojin_tradable_blacklist.csv", "guojin_tradable_blacklist.csv"),
]

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


def config_hash() -> str:
    """对决定信号规则的配置文件做 sha256 指纹。

    任一配置（主题/资产池/阈值/黑名单）变化 → config_hash 变化，
    可解释同一历史区间为何产出不同信号。
    """
    h = hashlib.sha256()
    h.update(f"rule_version:{RULE_VERSION}".encode("utf-8"))
    root = config_dir()
    for _k, rel in CONFIG_FILES:
        p: Path = root / rel
        if p.exists():
            try:
                h.update(p.read_bytes())
            except Exception:
                pass
        else:
            h.update(f"missing:{rel}".encode("utf-8"))
    return h.hexdigest()[:16]


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
