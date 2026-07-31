from __future__ import annotations

import pandas as pd
import pytest

from src.trend_engine.engine import calc_change, calc_watch_level, calc_action


class TestCalcChange:
    def test_no_previous(self):
        assert calc_change(80, None, False) == "无变化"

    def test_prev_missing_no_score(self):
        assert calc_change(20, None, True) == "无变化"

    def test_new_strong(self):
        assert calc_change(80, None, True) == "新增强趋势"

    def test_new_observe(self):
        assert calc_change(50, None, True) == "新增观察"

    def test_maintain_strong(self):
        assert calc_change(80, 85, True) == "维持强趋势"

    def test_maintain_observe(self):
        assert calc_change(50, 55, True) == "维持观察"

    def test_no_change_weak(self):
        assert calc_change(20, 10, True) == "无变化"

    def test_upgrade_to_strong(self):
        assert calc_change(80, 40, True) == "新增强趋势"

    def test_downgrade(self):
        assert calc_change(40, 80, True) == "降级"

    def test_exit_observe(self):
        assert calc_change(20, 50, True) == "退出观察"

    def test_enter_observe(self):
        assert calc_change(50, 20, True) == "新增观察"


class TestCalcWatchLevel:
    def test_score_below_30(self):
        assert calc_watch_level(20, None, None, None, None) == "C"

    def test_score_below_70(self):
        assert calc_watch_level(50, None, None, None, None) == "B"

    def test_no_rs_returns_B(self):
        assert calc_watch_level(80, None, 100, 90, 1.0) == "B"

    def test_level_S(self):
        assert calc_watch_level(80, 0.2, 100, 90, 1.5) == "S"

    def test_level_A(self):
        assert calc_watch_level(80, 0.1, 100, 90, 1.0) == "A"

    def test_level_B_rs_negative(self):
        assert calc_watch_level(80, -0.05, 100, 90, 1.0) == "B"


class TestCalcAction:
    def test_risk_warning_drawdown(self):
        assert calc_action(80, "A", 0.1, 1.0, True, 0.2, "维持强趋势") == "风险警戒"

    def test_remove_low_score(self):
        assert calc_action(20, "C", None, None, None, None, "无变化") == "剔除观察"

    def test_continue_tracking_high_volume(self):
        assert calc_action(80, "S", 0.1, 2.0, True, 0.05, "维持强趋势") == "继续跟踪"

    def test_focus_new_strong(self):
        assert calc_action(80, "S", 0.1, 1.0, True, 0.05, "新增强趋势") == "重点观察"

    def test_focus_positive_rs(self):
        assert calc_action(80, "A", 0.05, 1.0, True, 0.05, "维持强趋势") == "重点观察"

    def test_wait_breakthrough(self):
        assert calc_action(50, "B", 0.0, 1.0, True, 0.05, "维持观察") == "等待突破"

    def test_observe_wait(self):
        assert calc_action(50, "B", 0.0, 1.0, False, 0.05, "维持观察") == "观察等待"


class TestLoadUniverseItems:
    def test_empty_universe(self, tmp_path):
        p = tmp_path / "empty.yaml"
        p.write_text("themes: {}\n")
        from src.selection.universe import load_universe_items
        assert load_universe_items(p) == []

    def test_missing_file_returns_empty(self, tmp_path):
        from src.selection.universe import load_universe_items
        assert load_universe_items(tmp_path / "nope.yaml") == []

    def test_placeholder_skipped(self, tmp_path):
        p = tmp_path / "u.yaml"
        p.write_text(
            "themes:\n"
            "  t:\n"
            "    label: T\n"
            "    tiers:\n"
            "      - key: leader\n"
            "        label: L\n"
            "        assets:\n"
            "          - {symbol: TBD, name: x}\n"
            "          - {symbol: \"000001\", name: 平安银行, market: CN}\n"
        )
        from src.selection.universe import load_universe_items
        items = load_universe_items(p)
        assert len(items) == 1
        assert items[0].asset.symbol == "000001"
        assert items[0].theme == "t"
        assert items[0].tier == "leader"
