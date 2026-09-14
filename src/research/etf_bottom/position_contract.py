"""P0 数据契约：价格窗口位置（price_pos）的「未知不落地」契约。

背景（为什么需要这个模块）
--------------------------
`price_pos_N` = (close - low_N) / (high_N - low_N) × 100，是 Lane 2「底部/位置」
与 Lane 3「状态机」的共同底座。历史实现有一个危险缺口：

    未知（窗口不完整 / 有数据洞）──► 50.0（静默兜底）──► 被当作有效位置
        └─► long_term_bottom=False ─► confirmed_ltb=False ─► 伪造 first_exit / 状态机盲

2026-09-03 全市场 1598/1609 只 ETF 缺 bar，`50.0` 兜底把「数据洞」伪装成
「位置=50%」的合法观测，使 Lane2 全历史重算与 Lane3 状态机自次日失效数周。

契约（本模块是唯一真源）
------------------------
1. **只有完整、无洞、无折算、且有波动的窗口，才有位置值。**
2. **未知一律为 `None`，绝不落地为任何数值（尤其禁止 50.0）。**
3. **缺失的「原因」决定语义**，而不是缺行/缺值这种形式本身决定语义：

   ==================== ================================================== ==============
   reason               语义                                                 price_pos
   ==================== ================================================== ==============
   OK                   窗口 w 个 bar 齐全、hi>lo、无折算                       有值
   INSUFFICIENT_HISTORY 该标的自身 bar 数不足 w（新上市 / 历史太短）           None
   DATA_GAP             窗口内含「应存在但缺失」的 bar（取数 / 入库空洞）        None + 数据质量事件
   CORPORATE_ACTION     窗口内含疑似份额折算（|单日收益| ≥ 阈值）               None
   FLAT_WINDOW          窗口无波动（hi == lo，无从定义分位）                     None
   FLAT_PRICE_ASSET     货币/债券类近零波动资产，分位无意义（非错误）             None
   ==================== ================================================== ==============

   DATA_GAP 与 INSUFFICIENT_HISTORY 必须分开：前者是**数据管线事故**（要告警、
   要修数），后者是**标的自身生命周期**（正常）——两者都不产出位置值。

4. `daily_state_series`（Lane2 全历史）与 `price_map.compute_row`（Lane2 横截面快照）
   必须走同一套规则；`evaluate_window_position`（标量）与 `compute_position_columns`
   （向量化）互为镜像，由 parity 测试锁定。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

POS_LOW_THRESHOLD = 20.0        # price_pos <= 20 视为 LOW（沿用既有口径）
CA_RET_THRESHOLD = 0.20         # 单日 |ret| >= 20% 疑似份额折算/公司行为

REASON_OK = "OK"
REASON_INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
REASON_DATA_GAP = "DATA_GAP"
REASON_CORPORATE_ACTION = "CORPORATE_ACTION"
REASON_FLAT_WINDOW = "FLAT_WINDOW"
REASON_FLAT_PRICE_ASSET = "FLAT_PRICE_ASSET"

# 位置值为 None 的所有 reason（用于审计：哪些是错误、哪些是正常生命周期）
UNKNOWN_REASONS = (
    REASON_INSUFFICIENT_HISTORY,
    REASON_DATA_GAP,
    REASON_CORPORATE_ACTION,
    REASON_FLAT_WINDOW,
    REASON_FLAT_PRICE_ASSET,
)
# 属于「数据质量事故」的 reason（需要告警/修数，区别于正常的历史不足）
DATA_QUALITY_REASONS = (REASON_DATA_GAP,)


@dataclass(frozen=True)
class WindowPosition:
    """单窗口的位置契约结果。price_pos 为 None 表示 unknown（不落地）。"""

    low: float | None
    high: float | None
    price_pos: float | None
    distance_to_low: float | None
    reason: str
    n_valid: int

    @property
    def valid(self) -> bool:
        return self.price_pos is not None


def corp_action_flags(close: pd.Series) -> np.ndarray:
    """单日 |ret| >= 阈值 → 疑似份额折算/公司行为（审计标记，不作事实断言）。"""
    return (close.pct_change().abs() >= CA_RET_THRESHOLD).to_numpy(dtype=bool)


def window_ca_flags(ca: np.ndarray, window: int) -> np.ndarray:
    """窗口 i 的折算污染 = 窗口 [i-window+1, i] 内部跳变是否含折算。

    与既有 price_map 语义一致：折算点后短窗口先恢复、长窗口后恢复。
    """
    n = len(ca)
    out = np.zeros(n, dtype=bool)
    for i in range(n):
        lo = max(i - window + 2, 1)  # 窗口内第一个 pct_change index
        out[i] = bool(ca[lo:i + 1].any())
    return out


def evaluate_window_position(
    close: np.ndarray,
    i: int,
    window: int,
    ca_flags: np.ndarray,
) -> WindowPosition:
    """标量版：对 close[i] 的 window 长窗口套用契约（唯一规则真源之一）。

    绝不返回 50.0；unknown → price_pos=None + reason。
    """
    if i + 1 < window:
        return WindowPosition(None, None, None, None, REASON_INSUFFICIENT_HISTORY, 0)
    start = i - window + 1
    win = close[start:i + 1]
    n_valid = int(np.isfinite(win).sum())

    # 原因先于数值：先判定「为什么未知」，再决定要不要算
    if n_valid < window:
        return WindowPosition(None, None, None, None, REASON_DATA_GAP, n_valid)

    ca_lo = start + 1  # 窗口内第一个相对变化 index
    if i >= ca_lo and bool(ca_flags[ca_lo:i + 1].any()):
        return WindowPosition(None, None, None, None, REASON_CORPORATE_ACTION, n_valid)

    lo = float(win.min())
    hi = float(win.max())
    if not (hi > lo):
        return WindowPosition(lo, hi, None, None, REASON_FLAT_WINDOW, n_valid)
    cur = float(close[i])
    return WindowPosition(lo, hi, (cur - lo) / (hi - lo) * 100.0, cur / lo - 1.0, REASON_OK, n_valid)


def compute_position_columns(
    close: np.ndarray,
    ca_flags: np.ndarray,
    window: int,
) -> dict[str, np.ndarray]:
    """向量化版：与 `evaluate_window_position` 逐值等价（parity 由测试锁定）。

    返回 low / high / price_pos / distance_to_low / reason / n_valid 六个数组；
    unknown 位置一律 NaN（reason 显式记录原因），不再有 50.0。
    """
    s = pd.Series(np.asarray(close, dtype=float))
    n = len(s)
    n_rows = np.arange(1, n + 1)
    count = s.rolling(window, min_periods=window).count().to_numpy(dtype=float)
    lo = s.rolling(window, min_periods=window).min().to_numpy(dtype=float)
    hi = s.rolling(window, min_periods=window).max().to_numpy(dtype=float)
    ca_in = window_ca_flags(np.asarray(ca_flags, dtype=bool), window)

    enough_rows = n_rows >= window
    full_count = count == window
    observable = enough_rows & full_count & (~ca_in)
    has_range = observable & (hi > lo)

    with np.errstate(invalid="ignore", divide="ignore"):
        pos = (s.to_numpy(dtype=float) - lo) / (hi - lo) * 100.0
        dist = s.to_numpy(dtype=float) / lo - 1.0

    reason = np.where(
        ~enough_rows, REASON_INSUFFICIENT_HISTORY,
        np.where(~full_count, REASON_DATA_GAP,
                 np.where(ca_in, REASON_CORPORATE_ACTION,
                          np.where(~(hi > lo), REASON_FLAT_WINDOW, REASON_OK))),
    )
    return {
        "low": np.where(observable, lo, np.nan),
        "high": np.where(observable, hi, np.nan),
        "price_pos": np.where(has_range, pos, np.nan),
        "distance_to_low": np.where(has_range, dist, np.nan),
        "reason": reason,
        "n_valid": np.where(enough_rows, np.nan_to_num(count, nan=0.0), 0.0),
    }
