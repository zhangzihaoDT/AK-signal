"""
ETF 技术指标计算

职责：
  - 对所有 ETF 计算统一指标集
  - 支持类别专属指标扩展
  - 输入：日行情 DataFrame → 输出：指标 DataFrame

P0-C 交付物
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger("etf_signal.indicators")


def compute_indicators(
    daily: pd.DataFrame,
    price_col: str = "close",
    date_col: str = "date",
    group_col: str = "fund_code",
) -> pd.DataFrame:
    """计算单只或多只 ETF 的统一技术指标。

    Args:
        daily: 日行情数据（至少含 fund_code, date, close）
        price_col: 价格列名
        date_col: 日期列名
        group_col: 分组列名

    Returns:
        指标 DataFrame，每行代表某日某 ETF 的指标向量
    """
    if daily.empty:
        return pd.DataFrame()

    df = daily.sort_values([group_col, date_col]).copy()
    results: list[pd.DataFrame] = []

    for code, group in df.groupby(group_col):
        group = group.reset_index(drop=True)
        prices = group[price_col].values
        dates = group[date_col].values

        if len(prices) < 60:
            continue

        indicators: dict[str, Any] = {
            group_col: code,
            date_col: dates[-1],
            "price": prices[-1],
        }

        # MA
        for ma in [20, 60]:
            if len(prices) >= ma:
                indicators[f"ma{ma}"] = np.mean(prices[-ma:])
                indicators[f"ma{ma}_slope"] = (prices[-1] / np.mean(prices[-ma:]) - 1) * 100
            else:
                indicators[f"ma{ma}"] = np.nan
                indicators[f"ma{ma}_slope"] = np.nan

        # 收益率
        for period, label in [(5, "5d"), (20, "20d"), (60, "60d")]:
            if len(prices) >= period:
                indicators[f"return_{label}"] = (prices[-1] / prices[-period] - 1) * 100
            else:
                indicators[f"return_{label}"] = np.nan

        # 截面排名用原始收益率（非百分比）
        if len(prices) >= 20:
            indicators["return"] = prices[-1] / prices[-20] - 1

        # 成交额变化（最近 5 日 vs 前 5 日均值）
        if "amount" in group.columns:
            amounts = group["amount"].values
            if len(amounts) >= 10:
                recent = np.mean(amounts[-5:])
                prior = np.mean(amounts[-10:-5])
                indicators["amount_ratio"] = recent / prior if prior > 0 else 1.0
            else:
                indicators["amount_ratio"] = 1.0

        # 波动率（20 日年化）
        if len(prices) >= 21:
            log_returns = np.diff(np.log(prices[-21:]))
            indicators["volatility_20d"] = np.std(log_returns) * np.sqrt(252) * 100
        else:
            indicators["volatility_20d"] = np.nan

        # ATR
        if len(prices) >= 20:
            highs = group["high"].values[-20:]
            lows = group["low"].values[-20:]
            tr = np.maximum(highs[1:] - lows[1:],
                            np.abs(highs[1:] - prices[-20:-1]),
                            np.abs(lows[1:] - prices[-20:-1]))
            indicators["atr"] = np.mean(tr)
            indicators["atr_pct"] = (np.mean(tr) / prices[-1]) * 100
        else:
            indicators["atr"] = np.nan
            indicators["atr_pct"] = np.nan

        # 距近期高点距离
        if len(prices) >= 60:
            high_60d = np.max(prices[-60:])
            indicators["dist_from_high_60d"] = (prices[-1] / high_60d - 1) * 100
        else:
            indicators["dist_from_high_60d"] = np.nan

        # 最大回撤（60 日）
        if len(prices) >= 60:
            peak = np.maximum.accumulate(prices[-60:])
            drawdown = (prices[-60:] / peak - 1) * 100
            indicators["max_drawdown_60d"] = np.min(drawdown)
        else:
            indicators["max_drawdown_60d"] = np.nan

        results.append(pd.DataFrame([indicators]))

    if not results:
        return pd.DataFrame()
    return pd.concat(results, ignore_index=True)
