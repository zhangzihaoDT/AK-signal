"""run-level CircuitBreaker 状态机 + Sina 有限并发 batch 测试（Performance V1，不联网）。

用户锁定的状态机：CLOSED → OPEN；OPEN → CLOSED 仅显式 reset() 或新 run 新实例。
不实现 HALF_OPEN / 自动恢复。
"""

import threading
import time

import pandas as pd

from src.etf_signal.data_source import (
    CircuitBreaker,
    fetch_etf_hist_sina_batch,
    reset_em_circuit_breakers,
)


def _breaker(window=5, min_requests=3, threshold=0.5):
    return CircuitBreaker("eastmoney", window=window, min_requests=min_requests,
                          failure_rate_threshold=threshold)


class TestCircuitStateMachine:
    def test_default_closed(self):
        cb = _breaker()
        assert cb.state == "CLOSED"
        assert cb.is_open is False

    def test_no_open_below_min_requests(self):
        cb = _breaker(window=20, min_requests=10, threshold=0.5)
        for _ in range(9):
            cb.record_failure()
        assert cb.is_open is False

    def test_open_when_failure_rate_reached(self):
        cb = _breaker(window=10, min_requests=3, threshold=0.5)
        cb.record_failure()
        cb.record_failure()
        opened = cb.record_failure()
        assert opened is True
        assert cb.is_open is True
        assert cb.state == "OPEN"

    def test_stays_closed_when_rate_below_threshold(self):
        cb = _breaker(window=10, min_requests=4, threshold=0.5)
        # 1 fail / 5 ok → 1/6 ≈ 0.17 < 0.5，requests=6 ≥ min_requests=4 → 仍 CLOSED
        cb.record_failure()
        for _ in range(5):
            cb.record_success()
        assert cb.is_open is False

    def test_no_auto_recovery_when_open(self):
        cb = _breaker(window=5, min_requests=3, threshold=0.5)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open is True
        cb.record_success()  # OPEN 后成功不自动恢复（无 HALF_OPEN）
        cb.record_success()
        assert cb.is_open is True
        assert cb.state == "OPEN"

    def test_reset_closes_and_zeroes(self):
        cb = _breaker()
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open is True
        cb.reset()
        assert cb.is_open is False
        assert cb.state == "CLOSED"
        snap = cb.snapshot()
        assert snap["requests"] == 0
        assert snap["success"] == 0
        assert snap["failed"] == 0

    def test_closed_to_open_to_reset_cycle(self):
        # 用户例：CLOSED → OPEN → reset → CLOSED（V1 完整生命周期）
        cb = _breaker(window=20, min_requests=10, threshold=0.5)
        assert cb.state == "CLOSED"
        for _ in range(10):
            cb.record_failure()
        assert cb.state == "OPEN"
        cb.reset()
        assert cb.state == "CLOSED"

    def test_snapshot_structure(self):
        cb = _breaker(window=20, min_requests=10, threshold=0.5)
        cb.record_success()
        cb.record_failure()
        snap = cb.snapshot()
        assert snap["requests"] == 2
        assert snap["success"] == 1
        assert snap["failed"] == 1
        assert snap["state"] == "CLOSED"
        assert snap["circuit_opened"] is False
        assert snap["circuit_opened_after"] is None
        assert "failure_rate" in snap

    def test_snapshot_records_opened_after(self):
        cb = _breaker(window=20, min_requests=10, threshold=0.5)
        for _ in range(10):
            cb.record_failure()
        snap = cb.snapshot()
        assert snap["circuit_opened"] is True
        assert snap["circuit_opened_after"] == 10


class TestModuleBreaker:
    def test_reset_function_resets_module_breaker(self):
        reset_em_circuit_breakers()
        from src.etf_signal import data_source
        for _ in range(10):
            data_source.em_breaker().record_failure()
        assert data_source.em_breaker().is_open is True
        reset_em_circuit_breakers()
        assert data_source.em_breaker().is_open is False


class TestSinaBatchConcurrency:
    def test_parallel_within_bounded_workers(self, monkeypatch):
        """有限并发：8 任务 workers=4 → 并发峰值 >1 且 <=4。"""
        from src.etf_signal import data_source

        active = 0
        peak = 0
        lock = threading.Lock()

        def fake_fetch_etf_hist(code, start_date=None, end_date=None, **kw):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return pd.DataFrame({"date": [pd.Timestamp("2026-08-31")], "close": [1.0], "volume": [100.0]})

        monkeypatch.setattr(data_source, "fetch_etf_hist", fake_fetch_etf_hist)
        tasks = [(f"1590{i:03d}", "20260830", "20260831") for i in range(8)]
        results = fetch_etf_hist_sina_batch(tasks, workers=4, retry=0)
        assert len(results) == 8
        assert peak > 1, "batch 应并发，而不是串行"
        assert peak <= 4, f"worker 上限 4 被突破: peak={peak}"

    def test_empty_tasks(self):
        assert fetch_etf_hist_sina_batch([], workers=4) == {}
