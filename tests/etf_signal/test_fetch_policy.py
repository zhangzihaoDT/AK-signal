"""Data Acquisition 决策纯逻辑测试（Performance V1，不联网）。

验证增量更新决策 + guardrail，回归锁：任何改动不得触碰 Layer①②③ 计算语义。
"""

from datetime import date

import pandas as pd
import pytest

from src.etf_signal.fetch_policy import (
    REQUIRED_BAR_COLUMNS,
    UpdateDecision,
    build_fetch_window,
    decide_update,
    has_target_bar,
    latest_bar_date,
    load_market_data_spec,
    validate_append,
)

TARGET = date(2026, 8, 31)


def _df(dates, close=None, volume=None):
    """构造最小 raw DataFrame（date + close + volume）。"""
    d = {"date": pd.to_datetime(dates)}
    if close is not None:
        d["close"] = close
    if volume is not None:
        d["volume"] = volume
    return pd.DataFrame(d)


def _hist_ending(prev_day: date):
    """历史到 prev_day（不含 target）。"""
    return _df(
        [date(2026, 8, 24), date(2026, 8, 25), date(2026, 8, 26), prev_day],
        close=[1.0, 1.1, 1.2, 1.3], volume=[100, 110, 120, 130],
    )


class TestHasTargetBar:
    def test_exact_bar_present(self):
        df = _hist_ending(date(2026, 8, 28))
        df = pd.concat([df, _df([TARGET], close=[1.4], volume=[140])], ignore_index=True)
        assert has_target_bar(df, TARGET) is True

    def test_empty(self):
        assert has_target_bar(pd.DataFrame(), TARGET) is False

    def test_intraday_max_greater_but_target_missing(self):
        # 盘中 spot 合并当日（09-01 > target 08-31），但 target bar 缺失 → 必须判定缺
        df = _df([date(2026, 8, 28), date(2026, 9, 1)], close=[1.3, 1.5], volume=[130, 150])
        assert has_target_bar(df, TARGET) is False

    def test_target_older_than_history(self):
        df = _df([date(2026, 9, 1)], close=[1.5], volume=[150])
        assert has_target_bar(df, date(2026, 8, 31)) is False


class TestDecideUpdate:
    def test_skip_when_covered(self):
        df = _df([TARGET], close=[1.4], volume=[140])
        assert decide_update(df, TARGET) == UpdateDecision.SKIP_UP_TO_DATE

    def test_full_refresh_when_empty(self):
        assert decide_update(pd.DataFrame(), TARGET) == UpdateDecision.FULL_REFRESH

    def test_incremental_when_history_without_target(self):
        df = _hist_ending(date(2026, 8, 28))
        assert decide_update(df, TARGET) == UpdateDecision.INCREMENTAL

    def test_incremental_even_with_old_gap(self):
        # 本地滞后很久也走增量：源端返回区间全部交易日，自然补齐 gap
        df = _hist_ending(date(2026, 8, 14))
        assert decide_update(df, TARGET) == UpdateDecision.INCREMENTAL


class TestBuildFetchWindow:
    def test_none_when_skip(self):
        df = _df([TARGET], close=[1.4], volume=[140])
        assert build_fetch_window(df, TARGET) is None

    def test_incremental_start_after_last_bar(self):
        df = _hist_ending(date(2026, 8, 28))
        window = build_fetch_window(df, TARGET)
        assert window is not None
        start, end = window
        assert start == "20260829"
        assert end == "20260831"

    def test_full_refresh_default_start_when_empty(self):
        window = build_fetch_window(pd.DataFrame(), TARGET)
        assert window is not None
        start, end = window
        assert start == "20200101"
        assert end == "20260831"

    def test_full_refresh_custom_start(self):
        window = build_fetch_window(pd.DataFrame(), TARGET, full_refresh_start="20240101")
        assert window is not None
        start, _end = window
        assert start == "20240101"


class TestLatestBarDate:
    def test_latest(self):
        assert latest_bar_date(_hist_ending(date(2026, 8, 28))) == date(2026, 8, 28)

    def test_empty_is_none(self):
        assert latest_bar_date(pd.DataFrame()) is None


class TestValidateAppend:
    def test_clean_append(self):
        existing = _hist_ending(date(2026, 8, 28))
        merged = pd.concat([existing, _df([TARGET], close=[1.4], volume=[140])], ignore_index=True)
        ok, issues = validate_append(existing, merged)
        assert ok is True
        assert issues == []

    def test_duplicate_date_flagged(self):
        existing = _hist_ending(date(2026, 8, 28))
        merged = pd.concat(
            [existing, _df([TARGET, TARGET], close=[1.4, 1.4], volume=[140, 140])],
            ignore_index=True,
        )
        ok, issues = validate_append(existing, merged)
        assert ok is False
        assert any("重复" in i for i in issues)

    def test_new_row_missing_close_flagged(self):
        existing = _hist_ending(date(2026, 8, 28))
        merged = pd.concat(
            [existing, _df([TARGET], close=[None], volume=[140])], ignore_index=True)
        ok, issues = validate_append(existing, merged)
        assert ok is False
        assert any("close" in i for i in issues)

    def test_missing_required_column_flagged(self):
        existing = _hist_ending(date(2026, 8, 28))
        merged = pd.concat([existing, _df([TARGET], close=[1.4])], ignore_index=True)
        ok, issues = validate_append(existing, merged, required=REQUIRED_BAR_COLUMNS)
        assert ok is False
        assert any("volume" in i for i in issues)

    def test_historical_nan_not_retroactive(self):
        # 只查新增段：历史行 close 为 NaN 不应触发
        existing = _df([date(2026, 8, 25)], close=[None], volume=[110])
        merged = pd.concat(
            [existing, _df([date(2026, 8, 28)], close=[1.3], volume=[130])], ignore_index=True)
        ok, issues = validate_append(existing, merged)
        assert ok is True

    def test_empty_merged_ok(self):
        ok, issues = validate_append(pd.DataFrame(), pd.DataFrame())
        assert ok is True


class TestMarketDataSpec:
    def test_loader_reads_config(self):
        spec = load_market_data_spec()
        etf = spec.etf_fetch
        assert etf.full_refresh_start == "20200101"
        assert etf.eastmoney.circuit_breaker.window == 20
        assert etf.eastmoney.circuit_breaker.min_requests == 10
        assert etf.eastmoney.circuit_breaker.failure_rate_threshold == 0.50
        assert etf.sina.workers == 8
        assert etf.sina.retry == 1

    def test_spec_frozen(self):
        spec = load_market_data_spec()
        with pytest.raises(Exception):
            spec.etf_fetch.sina.workers = 16  # type: ignore[misc]
