"""v0.5.2 交易层回测单元测试。"""

from __future__ import annotations

import pandas as pd
import pytest

from src.backtest import metrics as bt_metrics
from src.backtest import trades as bt_trades
from src.backtest.execution.next_open import next_open
from src.backtest.strategy import entry as entry_mod
from src.backtest.strategy import exit as exit_mod


def _dates(n: int) -> list[str]:
    return [d.strftime("%Y%m%d") for d in pd.bdate_range("2026-07-01", periods=n)]


class TestNextOpen:
    def test_next_trading_day(self):
        s = pd.Series([10.0, 11.0], index=pd.to_datetime(["2026-07-01", "2026-07-02"]))
        d, p = next_open(s, "20260701")
        assert d == "20260702" and p == 11.0

    def test_no_next_day(self):
        s = pd.Series([10.0], index=pd.to_datetime(["2026-07-01"]))
        assert next_open(s, "20260701") == (None, None)

    def test_missing_date_insertion(self):
        s = pd.Series([10.0, 11.0, 12.0], index=pd.to_datetime(["2026-07-01", "2026-07-02", "2026-07-03"]))
        d, p = next_open(s, "20260701")  # exact 20260701 → next 07-02
        assert d == "20260702"


class TestEntry:
    def test_theme_confirmed_dates(self):
        signals = pd.DataFrame([
            {"trade_date": "20260701", "layer": "2", "theme": "ai_infrastructure",
             "confirmation_status": "观察"},
            {"trade_date": "20260702", "layer": "2", "theme": "ai_infrastructure",
             "confirmation_status": "弱势"},
        ])
        assert entry_mod.theme_confirmed_dates(signals, "ai_infrastructure") == {"20260701"}

    def test_entry_candidates_and_gate(self):
        dates = _dates(4)
        signals = pd.DataFrame([
            {"trade_date": dates[0], "entity_type": "etf", "entity_code": "512480",
             "theme": "ai_infrastructure", "layer": "1", "trend_state": "OUT_OF_SCOPE"},
            {"trade_date": dates[1], "entity_type": "etf", "entity_code": "512480",
             "theme": "ai_infrastructure", "layer": "1", "trend_state": "BUY_CANDIDATE"},
            {"trade_date": dates[2], "entity_type": "etf", "entity_code": "512480",
             "theme": "ai_infrastructure", "layer": "1", "trend_state": "BUY_CANDIDATE"},
            {"trade_date": dates[1], "layer": "2", "theme": "ai_infrastructure",
             "confirmation_status": "观察"},
        ])
        entries = entry_mod.entry_candidates(signals, entity_type="etf", theme="ai_infrastructure")
        assert len(entries) == 1
        assert entries.iloc[0]["trade_date"] == dates[1]
        gated = entry_mod.apply_theme_confirmation(entries, signals, "ai_infrastructure")
        assert len(gated) == 1  # 入场日 theme 确认成立


class TestExit:
    def test_signal_exit_date(self):
        dates = _dates(5)
        assert exit_mod.signal_exit_date(dates, dates[0]) == dates[1]
        assert exit_mod.signal_exit_date(dates, dates[4]) is None

    def test_ma20_exit_as_of(self):
        # 前 12 日上涨（close>ma20），后 13 日下跌跌破 ma20
        dates = _dates(25)
        closes = pd.Series(
            [100 + i for i in range(12)] + [111 - j * 4 for j in range(1, 14)],
            index=pd.to_datetime(dates),
        )
        d = exit_mod.ma20_exit_date(closes, dates[0])
        assert d is not None
        assert pd.Timestamp(d) >= pd.Timestamp(dates[12])  # 在下跌段触发，无 look-ahead

    def test_fixed_horizon_trading_days(self):
        dates = _dates(25)
        d = exit_mod.fixed_horizon_exit_signal_date(pd.DatetimeIndex(dates), dates[0], 20)
        assert d == dates[19]  # 入场成交日 + 19 个交易日 → 信号日，次日成交 = 持有 20 交易日


def _synthetic_signals(entries_at: list[str], confirmed_dates: set[str]) -> pd.DataFrame:
    """构建 512480 的 layer1/layer2 信号：entry 事件在指定日期。"""
    dates = _dates(30)
    rows = []
    on = False
    for d in dates:
        if d in entries_at and not on:
            on = True
        state = "BUY_CANDIDATE" if on else "OUT_OF_SCOPE"
        rows.append({"trade_date": d, "entity_type": "etf", "entity_code": "512480",
                     "theme": "ai_infrastructure", "layer": "1", "trend_state": state})
        if d in entries_at and on:
            on = False  # 单日 entry，次日回到 off（构造两个 entry 事件）
        rows.append({"trade_date": d, "layer": "2", "theme": "ai_infrastructure",
                     "confirmation_status": "观察" if d in confirmed_dates else "弱势"})
    return pd.DataFrame(rows)


class TestRunBacktest:
    def _cache(self):
        dates = pd.bdate_range("2026-07-01", periods=30)
        closes = [100.0 + i * 2 for i in range(30)]  # 单调上涨（MA20 退出不触发）
        opens = [100.0 + i * 2 for i in range(30)]
        combined = pd.DataFrame({
            "date": dates, "fund_code": ["512480"] * 30,
            "open": opens, "close": closes,
        })
        master = pd.DataFrame({"fund_code": ["512480"], "fund_name": ["半导体ETF国联安"]})
        return {"combined": combined, "master": master}

    def test_holding_skips_second_entry(self):
        # 两个 entry 事件；单调上涨 → ma20 不触发 → 持仓期间第二个 entry 被跳过 → 1 笔
        dates = _dates(30)
        signals = _synthetic_signals(entries_at=[dates[1], dates[5]],
                                     confirmed_dates=set(dates[1:]))
        trades = bt_trades.run_backtest(signals, theme="ai_infrastructure", entity_type="etf",
                                        exit_policies=("ma20_exit",), cache=self._cache())
        filled = trades[trades["entry_status"] == "filled"]
        assert len(filled) == 1
        assert filled.iloc[0]["exit_status"] == "open_at_end"

    def test_signal_exit_closed(self):
        # 短持有：entry 后趋势 off → signal_exit 平仓
        dates = _dates(30)
        rows = []
        for i, d in enumerate(dates):
            state = "BUY_CANDIDATE" if 2 <= i < 8 else "OUT_OF_SCOPE"
            rows.append({"trade_date": d, "entity_type": "etf", "entity_code": "512480",
                         "theme": "ai_infrastructure", "layer": "1", "trend_state": state})
            rows.append({"trade_date": d, "layer": "2", "theme": "ai_infrastructure",
                         "confirmation_status": "观察"})
        signals = pd.DataFrame(rows)
        trades = bt_trades.run_backtest(signals, theme="ai_infrastructure", entity_type="etf",
                                        exit_policies=("signal_exit",), cache=self._cache())
        filled = trades[trades["entry_status"] == "filled"]
        assert len(filled) == 1
        assert filled.iloc[0]["exit_status"] == "closed"
        assert filled.iloc[0]["return_pct"] is not None

    def test_fixed_horizon_holds_n_days(self):
        dates = _dates(30)
        signals = _synthetic_signals(entries_at=[dates[1]], confirmed_dates=set(dates))
        trades = bt_trades.run_backtest(signals, theme="ai_infrastructure", entity_type="etf",
                                        exit_policies=("fixed_horizon",), horizon=20,
                                        cache=self._cache())
        filled = trades[trades["entry_status"] == "filled"]
        assert filled.iloc[0]["holding_days"] == 20
        assert filled.iloc[0]["exit_status"] == "closed"


class TestMetrics:
    def test_compute_metrics(self):
        trades = pd.DataFrame([
            {"trade_id": 1, "exit_policy": "signal_exit", "entry_status": "filled",
             "exit_status": "closed", "return_pct": 5.0, "holding_days": 10},
            {"trade_id": 2, "exit_policy": "signal_exit", "entry_status": "filled",
             "exit_status": "closed", "return_pct": -2.0, "holding_days": 8},
            {"trade_id": 3, "exit_policy": "signal_exit", "entry_status": "unfilled",
             "entry_unfilled_reason": "no_next_open", "return_pct": None, "holding_days": None},
        ])
        m = bt_metrics.compute_metrics(trades)
        rec = m["policies"]["signal_exit"]
        assert rec["n_closed"] == 2
        assert rec["n_unfilled"] == 1
        assert rec["win_rate"] == 0.5
        assert abs(rec["mean_return_pct"] - 1.5) < 1e-6
        assert rec["profit_factor"] == pytest.approx(5.0 / 2.0)
