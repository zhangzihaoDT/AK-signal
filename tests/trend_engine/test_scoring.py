from __future__ import annotations

import pandas as pd

from src.trend_engine.scoring import (
    trend_bucket,
    build_risk_flags,
    build_technical_diagnostics,
    risk_flags_text,
    build_reason,
    score_latest_row,
    score_trend_label,
)


class TestTrendBucket:
    def test_strong(self):
        assert trend_bucket(80) == "strong"
        assert trend_bucket(70) == "strong"

    def test_observe(self):
        assert trend_bucket(50) == "observe"
        assert trend_bucket(30) == "observe"

    def test_weak(self):
        assert trend_bucket(20) == "weak"
        assert trend_bucket(0) == "weak"


class TestScoreTrendLabel:
    def test_strong_up(self):
        assert score_trend_label(80) == "强势上行"
        assert score_trend_label(70) == "强势上行"

    def test_above_average(self):
        assert score_trend_label(60) == "偏强"
        assert score_trend_label(50) == "偏强"

    def test_oscillating(self):
        assert score_trend_label(40) == "震荡"
        assert score_trend_label(30) == "震荡"

    def test_weak(self):
        assert score_trend_label(20) == "偏弱"
        assert score_trend_label(0) == "偏弱"


class TestBuildRiskFlags:
    def test_no_risks(self):
        row = pd.Series({
            "close": 105, "ma20": 100, "ma60": 98, "ma120": 95,
            "macd_hist": 1.0, "rsi14": 50, "relative_strength_20d": 0.05,
        })
        assert build_risk_flags(row) == []

    def test_below_ma20(self):
        row = pd.Series({
            "close": 95, "ma20": 100, "ma60": 98, "ma120": pd.NA,
            "macd_hist": 1.0, "rsi14": 50, "relative_strength_20d": pd.NA,
        })
        flags = build_risk_flags(row)
        assert "跌破MA20" in flags

    def test_macd_weak(self):
        row = pd.Series({
            "close": 105, "ma20": 100, "ma60": 98, "ma120": pd.NA,
            "macd_hist": -0.5, "rsi14": 50, "relative_strength_20d": pd.NA,
        })
        flags = build_risk_flags(row)
        assert "MACD转弱" in flags

    def test_rsi_hot(self):
        row = pd.Series({
            "close": 105, "ma20": 100, "ma60": 98, "ma120": pd.NA,
            "macd_hist": 1.0, "rsi14": 75, "relative_strength_20d": pd.NA,
        })
        flags = build_risk_flags(row)
        assert "RSI偏热" in flags

    def test_rsi_weak(self):
        row = pd.Series({
            "close": 105, "ma20": 100, "ma60": 98, "ma120": pd.NA,
            "macd_hist": 1.0, "rsi14": 30, "relative_strength_20d": pd.NA,
        })
        flags = build_risk_flags(row)
        assert "RSI偏弱" in flags

    def test_rs_negative(self):
        row = pd.Series({
            "close": 105, "ma20": 100, "ma60": 98, "ma120": pd.NA,
            "macd_hist": 1.0, "rsi14": 50, "relative_strength_20d": -0.05,
        })
        flags = build_risk_flags(row)
        assert "RS为负" in flags

    def test_flags_text_join(self):
        row = pd.Series({
            "close": 90, "ma20": 100, "ma60": 95, "ma120": pd.NA,
            "macd_hist": -1.0, "rsi14": 35, "relative_strength_20d": -0.1,
        })
        text = risk_flags_text(row)
        assert "跌破MA20" in text
        assert "MACD转弱" in text
        assert "RSI偏弱" in text


class TestBuildTechnicalDiagnostics:
    """v0.10 语义重构：技术诊断三维归组 + 等级判定。

    risk_flags 是六个平铺 flag 的 legacy 展平（事实不变）；
    technical_diagnostics 把它们按 trend / momentum / relative_strength 归组并判 level。
    """

    def _row(self, **kw):
        base = {"close": 105, "ma20": 100, "ma60": 98, "ma120": 95,
                "macd_hist": 1.0, "rsi14": 55, "relative_strength_20d": 0.05}
        base.update(kw)
        return pd.Series(base)

    def test_strong_all_dims(self):
        d = build_technical_diagnostics(self._row())
        assert d["trend"]["level"] == "STRONG"
        assert d["momentum"]["level"] == "STRONG"
        assert d["relative_strength"]["level"] == "STRONG"
        assert d["trend"]["flags"] == [] and d["momentum"]["flags"] == []

    def test_weak_all_dims_grouped(self):
        d = build_technical_diagnostics(self._row(
            close=90, ma20=100, ma60=95, ma120=93, macd_hist=-1.0, rsi14=35,
            relative_strength_20d=-0.1))
        assert d["trend"]["level"] == "WEAK"
        assert set(d["trend"]["flags"]) == {"below_ma20", "below_ma60", "below_ma120"}
        assert d["momentum"]["level"] == "WEAK"
        assert set(d["momentum"]["flags"]) == {"macd_weak", "rsi_weak"}
        assert d["relative_strength"]["level"] == "WEAK"
        assert d["relative_strength"]["flags"] == ["rs_negative"]

    def test_rsi_hot_is_not_weak_momentum(self):
        d = build_technical_diagnostics(self._row(rsi14=78))
        assert d["momentum"]["level"] == "STRONG"  # 过热是信息性 flag，不计弱动量
        assert "rsi_hot" in d["momentum"]["flags"]

    def test_partial_ma_history_normal_not_weak(self):
        d = build_technical_diagnostics(self._row(close=102, ma120=pd.NA))
        assert d["trend"]["level"] == "NORMAL"  # ma120 缺失仍按已有均线判
        assert d["trend"]["flags"] == []

    def test_insufficient_history_unknown(self):
        d = build_technical_diagnostics(self._row(
            close=10, ma20=pd.NA, ma60=pd.NA, ma120=pd.NA, macd_hist=pd.NA,
            rsi14=pd.NA, relative_strength_20d=pd.NA))
        assert d["trend"]["level"] == "UNKNOWN"
        assert d["momentum"]["level"] == "UNKNOWN"
        assert d["relative_strength"]["level"] == "UNKNOWN"

    def test_legacy_risk_flags_unchanged(self):
        """重构后 risk_flags 输出与旧逻辑完全一致（fact 不可变）。"""
        row = self._row(close=90, ma20=100, ma60=95, ma120=93, macd_hist=-1.0, rsi14=35,
                        relative_strength_20d=-0.1)
        assert build_risk_flags(row) == ["跌破MA20", "跌破MA60", "跌破MA120",
                                         "MACD转弱", "RSI偏弱", "RS为负"]


class TestBuildReason:
    def test_above_mas(self):
        row = pd.Series({
            "close": 110, "ma20": 100, "ma60": 95, "ma120": 90,
            "ma20_slope": 0.5, "ma60_slope": 0.3,
            "macd_hist": 2.0, "rsi14": 55,
            "volume": 1_500_000, "vol_ma20": 1_000_000,
            "relative_strength_20d": 0.05,
        })
        reason = build_reason(row)
        assert isinstance(reason, str)
        assert "结构：" in reason
        assert "量能：" in reason

    def test_below_mas(self):
        row = pd.Series({
            "close": 80, "ma20": 100, "ma60": 95, "ma120": 90,
            "ma20_slope": -0.5, "ma60_slope": -0.3,
            "macd_hist": -1.0, "rsi14": 30,
            "volume": 500_000, "vol_ma20": 1_000_000,
            "relative_strength_20d": -0.05,
        })
        reason = build_reason(row)
        assert isinstance(reason, str)
        assert "风险点：" in reason

    def test_reason_contains_all_sections(self):
        row = pd.Series({
            "close": 105, "ma20": 100, "ma60": 98, "ma120": pd.NA,
            "ma20_slope": 0.5, "ma60_slope": pd.NA,
            "macd_hist": 1.0, "rsi14": 55,
            "volume": 1_000_000, "vol_ma20": 1_000_000,
            "relative_strength_20d": 0.02,
        })
        reason = build_reason(row)
        assert "结构：" in reason
        assert "量能：" in reason
        assert "相对强度：" in reason
        assert "风险点：" in reason


class TestScoreLatestRow:
    def test_empty_df(self):
        df = pd.DataFrame()
        score, details = score_latest_row(df)
        assert score == 0
        assert details["reason"] == "empty"

    def test_score_range(self, sample_ohlcv):
        from src.trend_engine.indicators import add_indicators
        df = add_indicators(sample_ohlcv)
        score, details = score_latest_row(df)
        assert 0 <= score <= 100

    def test_details_contains_keys(self, sample_ohlcv):
        from src.trend_engine.indicators import add_indicators
        df = add_indicators(sample_ohlcv)
        score, details = score_latest_row(df)
        assert "score" in details
        assert "reason" in details
        assert "close" in details
        assert "macd_pos" in details

    def test_uptrend_scores_higher(self):
        up = pd.DataFrame({
            "date": pd.date_range("2026-01-01", periods=60, freq="B"),
            "open": [100 + i * 1.0 for i in range(60)],
            "high": [102 + i * 1.0 for i in range(60)],
            "low": [98 + i * 1.0 for i in range(60)],
            "close": [101 + i * 1.0 for i in range(60)],
            "volume": 1_000_000,
        })
        down = pd.DataFrame({
            "date": pd.date_range("2026-01-01", periods=60, freq="B"),
            "open": [200 - i * 1.0 for i in range(60)],
            "high": [202 - i * 1.0 for i in range(60)],
            "low": [198 - i * 1.0 for i in range(60)],
            "close": [199 - i * 1.0 for i in range(60)],
            "volume": 1_000_000,
        })
        from src.trend_engine.indicators import add_indicators
        up_score, _ = score_latest_row(add_indicators(up))
        down_score, _ = score_latest_row(add_indicators(down))
        assert up_score > down_score
