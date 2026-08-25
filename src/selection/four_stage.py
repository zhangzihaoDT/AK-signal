"""
Layer③ 四段选筹（v0.9.0）— 纯函数 + 离线价格历史读取。

四段：trend（有没有趋势）→ leadership（主题内相对地位）→ position（历史位置/赔率）
      → signal（三者组合的最终行动信号）。

纪律：
  - position 从不单独产生买入信号。趋势不成立（trend=NOT_QUALIFIED）的标的，
    即使处于 20% 历史分位，仍然 WAIT（fallback）。
  - HIGH 位置只输出 HOLD，不追高。
  - position 数据缺失/未启用时按中性 MID 匹配（既不被低估升级，也不被高位压制），
    并显式保留 position_level=UNKNOWN / position_pct=None 供审计。

价格历史全部离线读取（个股=data/processed/{market}_{symbol}.csv；
ETF=data/etf_signal/raw/{code}.parquet），并按 trade_date 截断，避免 look-ahead。
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger("selection.four_stage")

POSITION_LEVELS = ("LOW", "MID", "HIGH", "UNKNOWN")
NEUTRAL_POSITION = "MID"  # 数据不足/未启用时按中性位置匹配


def classify_leadership(
    rank: int | None,
    leader_rank_max: int,
    core_rank_max: int,
) -> str:
    """② 主题内相对地位：rank≤leader_rank_max → LEADER；≤core_rank_max → CORE；其余 NON_CORE。

    rank 缺失/非正 → NON_CORE（不参与龙头判定）。
    """
    if rank is None or rank <= 0:
        return "NON_CORE"
    if rank <= leader_rank_max:
        return "LEADER"
    if rank <= core_rank_max:
        return "CORE"
    return "NON_CORE"


def price_percentile(closes: list[float], lookback_days: int | None = None) -> float | None:
    """③ 当前价在过去窗口内的分位（0-100）。

    当前价 = 序列最后一个；分位 = 窗口内（不含当前）严格低于当前价的点占比 × 100。
    少于 2 个价格点 → None（数据不足）。
    """
    vals = [c for c in closes if c is not None and not pd.isna(c)]
    if len(vals) < 2:
        return None
    if lookback_days is not None and lookback_days > 0:
        vals = vals[-lookback_days:]
    cur = vals[-1]
    below = sum(1 for c in vals[:-1] if c < cur)
    return round(below / (len(vals) - 1) * 100, 1)


def classify_position(pct: float | None, low_max: float, mid_max: float) -> str:
    """③ 历史位置：≤low_max → LOW；≤mid_max → MID；>mid_max → HIGH；None → UNKNOWN。"""
    if pct is None:
        return "UNKNOWN"
    if pct <= low_max:
        return "LOW"
    if pct <= mid_max:
        return "MID"
    return "HIGH"


def effective_position(position_level: str) -> str:
    """把 UNKNOWN / 空（数据不足或未启用）归一为中性位置，避免数据缺口造成假信号。"""
    return position_level if position_level in POSITION_LEVELS[:3] else NEUTRAL_POSITION


def match_signal(
    trend_level: str,
    leadership_level: str,
    position_level: str,
    rules: list[dict],
    fallback_signal: str,
) -> str:
    """④ 信号规则顺序匹配：条件全部命中（缺省=通配）则输出；未命中 → fallback。

    position=UNKNOWN 按中性 MID 参与匹配。
    rules: [{"signal":..., "trend":..., "leadership":..., "position":...}, ...]
    """
    pos = effective_position(position_level)
    for r in rules:
        if r.get("trend") and r["trend"] != trend_level:
            continue
        if r.get("leadership") and r["leadership"] != leadership_level:
            continue
        if r.get("position") and r["position"] != pos:
            continue
        return r["signal"]
    return fallback_signal


# ── 离线价格历史读取（均按 trade_date 截断，防 look-ahead） ─────────────

def load_stock_close_history(
    market: str,
    symbol: str,
    trade_date: str | None = None,
    lookback_days: int | None = None,
) -> list[float]:
    """个股收盘价历史（data/processed/{market}_{symbol}.csv）。文件缺失/无 close → 空列表。"""
    from src.common.paths import processed_dir
    path = processed_dir() / f"{market}_{symbol}.csv"
    if not path.exists():
        logger.debug("position history missing: %s", path)
        return []
    try:
        df = pd.read_csv(path)
        if "close" not in df.columns:
            return []
        if "date" in df.columns:
            if trade_date is not None:
                df = df[pd.to_datetime(df["date"]) <= pd.to_datetime(trade_date)]
            df = df.sort_values("date")
    except Exception as e:  # pragma: no cover - 读取异常降级
        logger.warning("position history read failed %s: %s", path, e)
        return []
    closes = [float(c) for c in df["close"].tolist()]
    return closes[-lookback_days:] if lookback_days else closes


def load_etf_close_history(
    fund_code: str,
    trade_date: str | None = None,
    lookback_days: int | None = None,
) -> list[float]:
    """ETF 收盘价历史（data/etf_signal/raw/{code}.parquet）。文件缺失/无 close → 空列表。"""
    from src.common.paths import etf_signal_raw_dir
    path = etf_signal_raw_dir() / f"{fund_code}.parquet"
    if not path.exists():
        logger.debug("position history missing: %s", path)
        return []
    try:
        df = pd.read_parquet(path)
        if "close" not in df.columns:
            return []
        if "date" in df.columns:
            if trade_date is not None:
                df = df[pd.to_datetime(df["date"]) <= pd.to_datetime(trade_date)]
            df = df.sort_values("date")
    except Exception as e:  # pragma: no cover - 读取异常降级
        logger.warning("position history read failed %s: %s", path, e)
        return []
    closes = [float(c) for c in df["close"].tolist()]
    return closes[-lookback_days:] if lookback_days else closes


def evaluate_position(
    closes: list[float],
    lookback_days: int,
    low_max: float,
    mid_max: float,
    *,
    enabled: bool = True,
) -> tuple[str, float | None]:
    """历史位置评估：返回 (position_level, position_pct)。

    未启用 → ("", None)（信号匹配按中性 MID 处理）；数据不足 → ("UNKNOWN", None)。
    """
    if not enabled:
        return "", None
    pct = price_percentile(closes, lookback_days)
    if pct is None:
        return "UNKNOWN", None
    return classify_position(pct, low_max, mid_max), pct
