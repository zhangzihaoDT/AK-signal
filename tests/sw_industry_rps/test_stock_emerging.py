"""报告侧「个股观察」两层视图测试（Observability，不改 Gate）。"""

from __future__ import annotations

import json

import pandas as pd

from src.sw_industry_rps import stock_emerging as se


def _row(score, wl="B", rs_negative=True, below_ma60=True):
    diag = {
        "trend": {"level": "WEAK", "flags": (["below_ma60"] if below_ma60 else [])},
        "momentum": {"level": "NORMAL", "flags": []},
        "relative_strength": {"level": "WEAK", "flags": (["rs_negative"] if rs_negative else [])},
    }
    return pd.Series({"score_trend": score, "watch_level": wl,
                      "technical_diagnostics": json.dumps(diag)})


def test_transitions_cross_70_and_rs_turn():
    rows = [
        _row(60, wl="B", rs_negative=True, below_ma60=True),
        _row(70, wl="A", rs_negative=False, below_ma60=False),
    ]
    reasons, age = se._transitions(rows)
    assert "cross_70" in reasons
    assert "rs_turn" in reasons
    assert "watch_up" in reasons
    assert "reclaim_ma60" in reasons
    assert age == 0  # 当日跃迁


def test_transitions_score_jump_requires_held():
    # 跳升后回落 → 不算「转强」
    decayed = [_row(25), _row(50), _row(15)]
    reasons_decayed, _ = se._transitions(decayed)
    assert "score_jump" not in reasons_decayed
    # 跳升后仍站稳 → 算
    held = [_row(25), _row(50), _row(60)]
    reasons_held, age_held = se._transitions(held)
    assert "score_jump" in reasons_held
    assert age_held == 1  # 跳升发生在 1 交易日前


def test_transitions_insufficient_window():
    reasons, age = se._transitions([_row(80, wl="A")])
    assert reasons == [] and age is None


def test_render_stock_watch_labels_and_disclaimer():
    view = {
        "main": {"symbol": "x", "name": "禾赛", "tier": "adas_lidar", "score": 90, "watch_level": "A"},
        "emerging": [{"symbol": "600104", "name": "上汽集团", "tier": "oem_global",
                      "score": 80, "watch_level": "A", "reasons": ["cross_70", "rs_turn"],
                      "age_days": 3}],
    }
    html = se.render_stock_watch(view)
    assert "主代表" in html and "禾赛" in html
    assert "显著转强" in html and "上汽集团" in html and "近3日" in html
    assert "不代表主题确认或买入推荐" in html


def test_build_theme_stock_view_deterministic(monkeypatch):
    """用合成 stock_metrics 验证：主代表 = 最高分；上汽类跃迁进入「显著转强」。"""
    def _mk(sym, name, score, wl, rs_neg, below60):
        diag = {"trend": {"level": "WEAK", "flags": (["below_ma60"] if below60 else [])},
                "momentum": {"level": "NORMAL", "flags": []},
                "relative_strength": {"level": "WEAK", "flags": (["rs_negative"] if rs_neg else [])}}
        return {"symbol": sym, "name": name, "score_trend": score, "watch_level": wl,
                "technical_diagnostics": json.dumps(diag)}

    day1 = pd.DataFrame([
        _mk("AAA", "伯特利", 90, "A", False, False),
        _mk("600104", "上汽集团", 60, "B", True, True),
    ])
    day2 = pd.DataFrame([
        _mk("AAA", "伯特利", 100, "A", False, False),
        _mk("600104", "上汽集团", 80, "A", False, False),
    ])
    monkeypatch.setattr(se, "_recent_metric_dates", lambda d, n: ["20260101", "20260102"])
    monkeypatch.setattr(se.trend_inputs, "load_stock_metrics",
                        lambda d: day1 if d == "20260101" else day2)
    monkeypatch.setattr(se, "_theme_members",
                        lambda: {"china_auto_global": [("AAA", "伯特利", "oem_global"),
                                                       ("600104", "上汽集团", "oem_global")]})
    view = se.build_theme_stock_view("20260102")
    auto = view["china_auto_global"]
    assert auto["main"]["name"] == "伯特利" and auto["main"]["score"] == 100
    emg = {e["name"]: e for e in auto["emerging"]}
    assert "上汽集团" in emg
    assert "cross_70" in emg["上汽集团"]["reasons"]
    assert "rs_turn" in emg["上汽集团"]["reasons"]
    assert emg["上汽集团"]["age_days"] == 0
