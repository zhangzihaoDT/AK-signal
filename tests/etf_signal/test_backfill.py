"""缺口分类（③）与 checkpoint/resume（④）纯逻辑测试，不联网。"""

import json
from datetime import date

import pytest

from src.etf_signal.backfill import (
    BackfillCheckpoint,
    GapClassification,
    CATEGORY_NOT_IN_MASTER,
    CATEGORY_RATE_LIMITED,
    CATEGORY_SOURCE_STALE,
    CATEGORY_TERMINATED,
    CATEGORY_TRUE_MISSING,
    classify_gap,
    format_elapsed,
)

TARGET = date(2026, 8, 20)


def _rate_limit_error() -> Exception:
    return ConnectionError("Connection aborted. RemoteDisconnected('Remote end closed connection without response')")


class TestClassifyGap:
    def test_not_in_master(self) -> None:
        cls = classify_gap("159082", TARGET, in_master=False, raw_latest=None,
                           fetch_errors=[], nav_latest=None)
        assert cls.category == CATEGORY_NOT_IN_MASTER
        assert cls.retryable is False

    def test_terminated_when_nav_stopped_long_ago(self) -> None:
        cls = classify_gap("560650", TARGET, in_master=True, raw_latest=date(2026, 7, 1),
                           fetch_errors=[], nav_latest=date(2026, 7, 2))
        assert cls.category == CATEGORY_TERMINATED
        assert cls.retryable is False

    def test_source_stale_when_raw_lagging(self) -> None:
        cls = classify_gap("511970", TARGET, in_master=True, raw_latest=date(2026, 8, 19),
                           fetch_errors=[], nav_latest=TARGET)
        assert cls.category == CATEGORY_SOURCE_STALE
        assert cls.retryable is True

    def test_rate_limited_when_connection_error(self) -> None:
        cls = classify_gap("510050", TARGET, in_master=True, raw_latest=date(2026, 8, 19),
                           fetch_errors=[_rate_limit_error()], nav_latest=TARGET)
        assert cls.category == CATEGORY_RATE_LIMITED
        assert cls.retryable is True

    def test_rate_limited_when_fetch_returns_empty_but_info_shows_transient(self) -> None:
        # fetch_etf_hist 捕获网络异常返回空 DF，异常不会抛出；必须从 fetch_info 判断。
        fetch_info = {
            "primary_error_type": "TransientNetworkError",
            "fallback_reason": "('Connection aborted.', RemoteDisconnected(...))",
        }
        cls = classify_gap("511970", TARGET, in_master=True, raw_latest=date(2026, 8, 19),
                           fetch_errors=[], nav_latest=TARGET, fetch_info=fetch_info)
        assert cls.category == CATEGORY_RATE_LIMITED
        assert cls.retryable is True

    def test_true_missing_when_no_clue(self) -> None:
        cls = classify_gap("589999", TARGET, in_master=True, raw_latest=None,
                           fetch_errors=[RuntimeError("other failure")], nav_latest=None)
        assert cls.category == CATEGORY_TRUE_MISSING
        assert cls.retryable is False

    def test_terminated_takes_priority_over_stale(self) -> None:
        cls = classify_gap("560650", TARGET, in_master=True, raw_latest=date(2026, 7, 1),
                           fetch_errors=[], nav_latest=date(2026, 7, 2))
        assert cls.category == CATEGORY_TERMINATED


class TestBackfillCheckpoint:
    def test_roundtrip_and_resume(self, tmp_path) -> None:
        path = tmp_path / "backfill_20260820.json"
        cp = BackfillCheckpoint(path)
        cp.mark_ok("588220", "sina", 1, 320)
        cp.mark_failed("560650", CATEGORY_TERMINATED, "基金已终止", 150)
        cp.save()

        reloaded = BackfillCheckpoint(path)
        assert reloaded.is_done("588220") is True
        assert reloaded.is_done("560650") is False
        assert reloaded.done_codes() == {"588220"}
        assert reloaded.failed_codes() == {"560650"}
        assert reloaded.data["codes"]["560650"]["category"] == CATEGORY_TERMINATED

    def test_resume_skips_done(self, tmp_path) -> None:
        path = tmp_path / "cp.json"
        cp = BackfillCheckpoint(path)
        cp.mark_ok("588220", "sina", 1, 100)
        cp.save()
        reloaded = BackfillCheckpoint(path)
        assert reloaded.is_done("588220") is True


class TestFormatElapsed:
    def test_seconds(self) -> None:
        assert format_elapsed(32) == "0m32s"

    def test_minutes(self) -> None:
        assert format_elapsed(4 * 60 + 32) == "4m32s"

    def test_hours(self) -> None:
        assert format_elapsed(2 * 3600 + 60 * 3 + 5) == "2h03m05s"
