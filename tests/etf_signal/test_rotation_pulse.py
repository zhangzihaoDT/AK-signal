"""Layer① v0.7.0 Market Pulse 测试：RPS1（Today）/ ΔRPS15（Velocity）/ Liquidity。

验证：
  - compute_rotation_metrics 产出 rps1 / delta_rps15 / liquidity 三个观察列
  - 三个列的口径与全市场横截面参考计算一致
  - 防御性资产（货币/债券）不参与 RPS1/ΔRPS15 排名，但仍计算流动性
  - market_pulse 主题级四要素（今日热点 / 趋势龙头 / 加速 / 风险）
  - leader_lists 三张排行榜按各自指标降序
  - HTML 报告包含 Market Pulse 区块
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.etf_signal import rotation, rotation_report

EQUITY = {"A", "B", "C"}
ALL_CODES = ["A", "B", "C", "M"]


def _combined_df() -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-05", periods=40)
    paths = {
        # 加速上行：15 日前 RPS 低、今日 RPS 高；最后一根大阳
        "A": np.concatenate([np.linspace(100, 101, 25), np.linspace(101, 120, 15)]),
        # 先强后平：15 日前 RPS 高、今日回落
        "B": np.concatenate([np.linspace(100, 118, 25), np.linspace(118, 118, 15)]),
        # 横盘
        "C": np.linspace(100, 100, 40),
        # 货币型（不参与 RPS 排名）
        "M": np.linspace(1.0, 1.0, 40),
    }
    amounts = {
        "A": np.linspace(1e8, 5e8, 40),
        "B": np.linspace(5e8, 6e8, 40),
        "C": np.linspace(1e7, 1e7, 40),
        "M": np.linspace(2e9, 2e9, 40),
    }
    frames = []
    for code in ALL_CODES:
        df = pd.DataFrame({"date": dates, "close": paths[code], "amount": amounts[code]})
        df["fund_code"] = code
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def _master_df() -> pd.DataFrame:
    return pd.DataFrame({
        "fund_code": ALL_CODES,
        "fund_name": ["测试AIETF", "测试电力ETF", "测试ETF", "测试货币ETF"],
        "asset_bucket": ["industry", "industry", "industry", "money_market"],
    })


def _ref_rps(combined: pd.DataFrame, window: int, codes: list[str]) -> pd.Series:
    pivot = combined.pivot_table(index="date", columns="fund_code", values="close", aggfunc="last").sort_index()
    sub = pivot[codes]
    ret = sub / sub.shift(window) - 1
    rps = ret.rank(axis=1, pct=True, ascending=True) * 100
    return rps.iloc[-1]


class TestMarketPulseColumns:
    def test_columns_present_and_numeric(self):
        rot = rotation.compute_rotation_metrics(_combined_df(), _master_df())
        assert not rot.empty
        for col in ("rps1", "delta_rps15", "liquidity"):
            assert col in rot.columns
            assert pd.to_numeric(rot[col], errors="coerce").notna().any()

    def test_rps1_matches_cross_section(self):
        rot = rotation.compute_rotation_metrics(_combined_df(), _master_df())
        ref = _ref_rps(_combined_df(), 1, sorted(EQUITY))
        for code in sorted(EQUITY):
            assert rot.set_index("fund_code").loc[code, "rps1"] == pytest.approx(ref[code], abs=1e-6)

    def test_delta_rps15_matches_5day_change(self):
        combined = _combined_df()
        rot = rotation.compute_rotation_metrics(combined, _master_df())
        rps15 = combined.pivot_table(index="date", columns="fund_code", values="close", aggfunc="last").sort_index()
        sub = rps15[sorted(EQUITY)]
        ret15 = sub / sub.shift(15) - 1
        rps15_pct = ret15.rank(axis=1, pct=True, ascending=True) * 100
        delta_ref = rps15_pct.iloc[-1] - rps15_pct.shift(5).iloc[-1]
        idx = rot.set_index("fund_code")
        for code in sorted(EQUITY):
            assert idx.loc[code, "delta_rps15"] == pytest.approx(delta_ref[code], abs=1e-6)

    def test_liquidity_matches_amount_percentile(self):
        combined = _combined_df()
        rot = rotation.compute_rotation_metrics(combined, _master_df())
        amount = combined.pivot_table(index="date", columns="fund_code", values="amount", aggfunc="last").sort_index()
        avg = amount.iloc[-5:].mean()
        liq_ref = avg.rank(pct=True, ascending=True) * 100
        idx = rot.set_index("fund_code")
        for code in ALL_CODES:
            assert idx.loc[code, "liquidity"] == pytest.approx(liq_ref[code], abs=1e-6)

    def test_defensive_bucket_excluded_from_rps_but_has_liquidity(self):
        rot = rotation.compute_rotation_metrics(_combined_df(), _master_df())
        m = rot[rot["fund_code"] == "M"].iloc[0]
        assert pd.isna(m["rps1"])
        assert pd.isna(m["delta_rps15"])
        assert pd.notna(m["liquidity"])


class TestReportThreeQuestions:
    def test_html_renders_three_questions(self, tmp_path):
        rot = rotation.compute_rotation_metrics(_combined_df(), _master_df())
        # 注入 trend_state 以便三问三答消费
        rot["trend_state"] = "WATCH"
        path = rotation_report.render_rotation_report(
            rot, tmp_path, "20260220", coverage={"data_status": "confirmed"},
        )
        html = path.read_text(encoding="utf-8")
        # 三个问题
        assert "大类资产往哪里动" in html
        assert "趋势活跃 ETF" in html
        assert "我的主题 ETF" in html
        # 不再展示审计信息与宏观判断
        assert "Market Pulse" not in html
        assert "四维观察表" not in html
        assert "master_count" not in html
        assert "Today's Leaders" not in html
        assert "Fast Movers" not in html
        # 页脚审计行
        assert "异常 ETF" in html

    def test_cross_asset_direction_groups(self):
        rot = rotation.compute_rotation_metrics(_combined_df(), _master_df())
        rows = rotation.cross_asset_direction(rot)
        # A/B/C 是 industry（权益），M 是货币
        dirs = {r["direction"]: r for r in rows}
        assert "A股行业/主题" in dirs
        assert "现金/货币" in dirs

    def test_active_etf_representatives(self):
        rot = rotation.compute_rotation_metrics(_combined_df(), _master_df())
        rot["trend_state"] = "BUY_CANDIDATE"
        reps, total = rotation.active_etf_representatives(rot, _master_df(), top_n=5)
        assert total > 0
        assert all(r["trend_state"] == "BUY_CANDIDATE" for r in reps)


class TestDataQualityAnomaly:
    """P0-2：单日异常（份额折算/除权/异常行情）不参与横截面排名，原值保留并标记。"""

    def _combined_with_anomaly(self):
        dates = pd.bdate_range("2026-01-05", periods=40)
        close_a = np.linspace(100, 110, 40)                      # 干净
        close_b = np.concatenate([np.linspace(100, 105, 36), [105, 52.5, 53, 53.5]])  # offset3 -50%
        close_c = np.concatenate([np.linspace(100, 100, 39), [50]])                  # offset1 -50%
        frames = []
        for code, close in [("A", close_a), ("B", close_b), ("C", close_c)]:
            frames.append(pd.DataFrame({
                "date": dates, "close": close,
                "amount": np.linspace(1e8, 2e8, 40), "fund_code": code,
            }))
        return pd.concat(frames, ignore_index=True)

    def _master(self):
        return pd.DataFrame({"fund_code": ["A", "B", "C"],
                             "fund_name": ["AETF", "BETF", "CETF"],
                             "asset_bucket": ["industry"] * 3})

    def test_anomaly_flagged_and_excluded_from_rps(self):
        rot = rotation.compute_rotation_metrics(self._combined_with_anomaly(), self._master())
        idx = rot.set_index("fund_code")
        assert idx.loc["B", "data_quality_flag"] == "corporate_action"
        assert idx.loc["C", "data_quality_flag"] == "corporate_action"
        assert idx.loc["A", "data_quality_flag"] == ""
        # C（offset1）污染所有窗口；B（offset3）污染 15/20/60 但不污染 RPS1（今日）
        assert pd.isna(idx.loc["C", "rps15"])
        assert pd.isna(idx.loc["B", "rps15"])
        assert pd.notna(idx.loc["B", "rps1"])
        assert pd.isna(idx.loc["C", "rps1"])
        # 原值保留（未被清零或覆盖；-50% 单日 + 后两日修复 → 5 日 ≈ -49%）
        assert idx.loc["B", "return_5d"] < -40.0
        assert pd.notna(idx.loc["B", "return_20d"])
        # 干净资产不受影响
        assert idx.loc["A", "rps15"] > 0

    def test_clean_universe_unflagged(self):
        rot = rotation.compute_rotation_metrics(_combined_df(), _master_df())
        assert (rot["data_quality_flag"].astype(str) == "").all()


class TestCoverageCounts:
    """P0-3：数据口径统一命名（master / price_current / rps_eligible / trend_active）。"""

    def test_counts(self):
        rot = rotation.compute_rotation_metrics(_combined_df(), _master_df())
        wl = pd.DataFrame({"fund_code": ["A"], "trend_state": ["BUY_CANDIDATE"]})
        cov = rotation.coverage(rot, wl, master_count=4)
        assert cov["master_count"] == 4
        assert cov["price_current_count"] == int(rot["rps15"].notna().sum())
        assert cov["rps_eligible_count"] == 1
        assert cov["trend_active_count"] == 1


class TestWatchlistCarriesObservation:
    """P0-1：watchlist → account_candidates → card 链路必须携带 rps20/rps1/delta_rps15。"""

    def test_watchlist_columns(self):
        from src.etf_signal import signal as etf_signal
        ind = pd.DataFrame({
            "fund_code": ["A"], "price": [1.1], "ma20": [1.0], "ma60": [0.9],
            "rps15": [90.0], "rps20": [85.0], "rps60": [70.0],
            "rps1": [95.0], "delta_rps15": [5.0], "liquidity": [80.0],
            "return_5d": [3.0], "return_20d": [8.0], "return_60d": [20.0],
            "amount_ratio": [1.1],
        })
        master = pd.DataFrame({"fund_code": ["A"], "fund_name": ["测试AIETF"]})
        wl = etf_signal.build_trend_watchlist(ind, master)
        row = wl.iloc[0]
        assert row["rps20"] == 85.0
        assert row["rps1"] == 95.0
        assert row["delta_rps15"] == 5.0
        assert row["liquidity"] == 80.0

    def test_optional_value_none(self):
        from src.etf_signal import signal as etf_signal
        ind = pd.DataFrame({
            "fund_code": ["A"], "price": [1.1], "ma20": [1.0], "ma60": [0.9],
            "rps15": [90.0], "rps60": [70.0],
            "return_5d": [3.0], "return_20d": [8.0], "return_60d": [20.0],
            "amount_ratio": [1.1],
        })
        master = pd.DataFrame({"fund_code": ["A"], "fund_name": ["测试AIETF"]})
        wl = etf_signal.build_trend_watchlist(ind, master)
        assert wl["rps20"].iloc[0] is None
        assert wl["rps1"].iloc[0] is None
