"""
共享账户模拟（v0.6 第一轮）。

相对 v0.5.2 的独立等名义本金，这里建立共享现金账户：
  initial_capital / max_positions / equal-weight 仓位 / max_weight_per_asset
  no_leverage / no_pyramiding / next_open 成交 / fee + slippage

多策略合并到一个账户时，允许主题权重（Mode B：AI 60% / HC 40%）。
"""

from __future__ import annotations

import logging
import math
from typing import Any

import pandas as pd

logger = logging.getLogger("backtest.portfolio.account")


def build_close_prices(cache: dict[str, Any]) -> dict[str, pd.Series]:
    """各实体 close 序列（ffill 处理停牌，用于每日盯市）。"""
    combined = cache.get("combined")
    if combined is None or combined.empty:
        return {}
    df = combined.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date")
    out: dict[str, pd.Series] = {}
    for code, g in df.groupby("fund_code"):
        s = g.set_index("date")["close"].sort_index()
        out[str(code)] = s.ffill()
    return out


class PortfolioAccount:
    """共享现金账户逐日模拟。"""

    def __init__(
        self,
        initial_capital: float = 1_000_000.0,
        max_positions: int = 5,
        max_weight_per_asset: float = 0.20,
        fee_pct: float = 0.05,
        slippage_pct: float = 0.05,
        label: str = "",
    ):
        self.initial_capital = float(initial_capital)
        self.max_positions = int(max_positions)
        self.max_weight_per_asset = float(max_weight_per_asset)
        self.fee_pct = float(fee_pct)
        self.slippage_pct = float(slippage_pct)
        self.label = label
        self.cash = self.initial_capital
        self.positions: dict[str, dict[str, Any]] = {}
        self.orders: list[dict[str, Any]] = []
        self.daily: list[dict[str, Any]] = []   # 完整交易日账户记录
        self.theme_pnl: dict[str, float] = {}      # 主题已实现盈亏
        self.theme_entries: dict[str, int] = {}     # 主题已成交笔数

    # ── 撮合 ────────────────────────────────────────────────────────

    def _buy(self, code: str, price: float, weight: float, date: str, theme: str = "") -> bool:
        if code in self.positions:
            self._reject(code, date, "no_pyramiding")
            return False
        if len(self.positions) >= self.max_positions:
            self._reject(code, date, "max_positions")
            return False
        equity = self.cash + sum(p["shares"] * p["last_price"] for p in self.positions.values())
        alloc = min(equity * weight / self.max_positions,
                    equity * self.max_weight_per_asset)
        buy_eff = price * (1.0 + self.slippage_pct / 100.0)
        shares = int(alloc / buy_eff)
        notional = shares * price
        cost = notional * (1.0 + self.slippage_pct / 100.0) + notional * (self.fee_pct / 100.0)
        if shares <= 0 or cost > self.cash:
            self._reject(code, date, "insufficient_cash")
            return False
        self.cash -= cost
        self.positions[code] = {
            "shares": shares, "entry_price": price, "entry_date": date,
            "weight": weight, "alloc_value": cost, "last_price": price, "theme": theme,
            "cost": cost,
        }
        self.theme_entries[theme] = self.theme_entries.get(theme, 0) + 1
        self.orders.append({"date": date, "code": code, "action": "buy",
                            "shares": shares, "price": price, "status": "filled"})
        return True

    def _sell(self, code: str, price: float, date: str) -> bool:
        pos = self.positions.get(code)
        if pos is None:
            return False
        notional = pos["shares"] * price
        proceeds = notional * (1.0 - self.slippage_pct / 100.0) - notional * (self.fee_pct / 100.0)
        pnl = proceeds - pos["cost"]
        theme = pos.get("theme", "")
        self.theme_pnl[theme] = self.theme_pnl.get(theme, 0.0) + pnl
        self.cash += proceeds
        self.orders.append({"date": date, "code": code, "action": "sell",
                            "shares": pos["shares"], "price": price, "status": "filled"})
        del self.positions[code]
        return True

    def _reject(self, code: str, date: str, reason: str) -> None:
        self.orders.append({"date": date, "code": code, "action": "buy",
                            "shares": 0, "price": None, "status": "rejected", "reason": reason})

    # ── 逐日模拟（完整交易日历） ───────────────────────────────────

    def run(self, trades: pd.DataFrame, closes: dict[str, pd.Series],
            calendar: list[str]) -> "PortfolioAccount":
        """按统一交易日历逐日推进，无交易日也记录账户权益（每日收盘盯市）。"""
        filled = trades[trades["entry_status"] == "filled"].copy()
        if filled.empty:
            for d in calendar:
                self._mark(d, closes)
                self._record(d)
            return self

        cal_end = calendar[-1] if calendar else ""
        entries: dict[str, list[pd.Series]] = {}
        exits: dict[str, list[pd.Series]] = {}
        for _, t in filled.iterrows():
            ed, xd = str(t["entry_fill_date"]), str(t["exit_fill_date"])
            if ed and ed <= cal_end:
                entries.setdefault(ed, []).append(t)
            # 超过研究窗口的退出（open_at_end 到数据末端）：持到窗口末，不再强制卖出
            if xd and xd <= cal_end:
                exits.setdefault(xd, []).append(t)

        for d in calendar:
            # 先卖后买：释放的现金可用于当日新仓
            for t in exits.get(d, []):
                if t["entity_code"] in self.positions:
                    self._sell(t["entity_code"], float(t["exit_fill_price"]), d)
            for t in sorted(entries.get(d, []), key=lambda x: str(x["trade_id"])):
                self._buy(t["entity_code"], float(t["entry_fill_price"]),
                          float(t.get("weight", 1.0)), d,
                          str(t.get("theme", "") or ""))
            self._mark(d, closes)
            self._record(d)
        return self

    def _mark(self, date: str, closes: dict[str, pd.Series]) -> None:
        for code, pos in self.positions.items():
            s = closes.get(str(code))
            px = pos["last_price"]
            if s is not None and not s.empty:
                ts = pd.Timestamp(date)
                if ts in s.index:
                    v = s.loc[ts]
                    if pd.notna(v):
                        px = float(v)
            pos["last_price"] = px

    def _record(self, date: str) -> None:
        mv = sum(p["shares"] * p["last_price"] for p in self.positions.values())
        equity = self.cash + mv
        self.daily.append({
            "date": date,
            "cash": round(self.cash, 2),
            "position_market_value": round(mv, 2),
            "equity": round(equity, 2),
            "gross_exposure": round(mv / equity, 4) if equity > 0 else 0.0,
            "position_count": int(len(self.positions)),
        })

    def nav_frame(self) -> pd.DataFrame:
        df = pd.DataFrame(self.daily)
        if not df.empty:
            df = df.sort_values("date").reset_index(drop=True)
            df["daily_return"] = (df["equity"].pct_change()).round(6)
            df["daily_return"] = df["daily_return"].fillna(0.0)
        return df

    def contribution(self) -> dict[str, dict[str, Any]]:
        """按主题的收益贡献（已实现 P&L + 期末未实现 + 成交笔数）。"""
        unreal: dict[str, float] = {}
        for pos in self.positions.values():
            theme = pos.get("theme", "")
            value = pos["shares"] * pos["last_price"] - pos["cost"]
            unreal[theme] = unreal.get(theme, 0.0) + value
        themes = set(self.theme_pnl) | set(unreal) | set(self.theme_entries)
        out: dict[str, dict[str, Any]] = {}
        for th in sorted(themes):
            realized = self.theme_pnl.get(th, 0.0)
            out[th or "未分类"] = {
                "n_trades": self.theme_entries.get(th, 0),
                "realized_pnl": round(realized, 2),
                "unrealized_pnl": round(unreal.get(th, 0.0), 2),
                "total_pnl": round(realized + unreal.get(th, 0.0), 2),
                "total_pnl_pct": round((realized + unreal.get(th, 0.0)) / self.initial_capital * 100, 2),
            }
        return out
