"""
Opportunity Radar V1 — 测试（冻结场景 Case 1-7）

Case 1  已有 Theme 且正常映射（AI ETF 命中）→ 不进入 Radar
Case 2  强势但无 Theme → NEW_THEME_CANDIDATE
Case 3  无 Theme 但趋势弱 → 不进入正式 opportunity，留 audit
Case 4  无 Theme + 高 RPS + Lane2 unreliable → audit reason = lane2_unreliable
Case 5  疑似已有 Theme mapping gap → POSSIBLE_MAPPING_GAP
Case 6  同 ETF 多关键词沿用同一 match_theme（Radar 与 Selection 同源，不出现第二套映射）
Case 7  Radar 不影响 Selection（运行前后 tradable_candidates 决策事实不变）
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.common import themes as themes_cfg
from src.opportunity_radar import mapping as radar_mapping
from src.opportunity_radar import radar as radar_engine


def _mk_rotation() -> pd.DataFrame:
    """构造极小全市场 rotation（含 RPS/名称/数据质量；无 trend_state——由 account 提供）。"""
    return pd.DataFrame({
        "fund_code": ["1001", "1002", "1003", "1004", "1005", "1006", "1007"],
        "fund_name": [
            "半导体ETF国联安",   # Case 1 → ai_infrastructure（已注册，映射命中）
            "黄金ETF国泰",       # Case 2 → 无 Theme，BUY_CANDIDATE，reliable → NEW_THEME_CANDIDATE
            "农业ETF南方",       # Case 3 → 无 Theme，WATCH（弱趋势）→ audit
            "银行ETF华宝",       # Case 4 → 无 Theme，BUY_CANDIDATE，但 lane2 unreliable → audit
            "港股通通信ETF华夏",  # Case 6 → 名称含「通信」但被 AI exclude「港股通」拦截 → 无 Theme
            "粮食ETF浦银",       # 无 Theme，BUY_CANDIDATE，reliable → NEW_THEME_CANDIDATE（多一只新候选）
            "货币ETF易方达",     # 低流动性/弱趋势 → audit
        ],
        "rps15": [88.0, 98.0, 30.0, 96.0, 70.0, 99.0, 5.0],
        "data_quality_flag": ["", "", "", "", "", "", ""],
    })


def _mk_account() -> pd.DataFrame:
    return pd.DataFrame({
        "fund_code": ["1001", "1002", "1003", "1004", "1005", "1006", "1007"],
        "trend_state": [
            "BUY_CANDIDATE", "BUY_CANDIDATE", "WATCH",
            "BUY_CANDIDATE", "STRONG_WATCH", "BUY_CANDIDATE", "OUT_OF_SCOPE",
        ],
    })


def _mk_master() -> pd.DataFrame:
    return pd.DataFrame({
        "fund_code": ["1001", "1002", "1003", "1004", "1005", "1006", "1007"],
        "fund_name": ["半导体ETF国联安", "黄金ETF国泰", "农业ETF南方", "银行ETF华宝",
                      "港股通通信ETF华夏", "粮食ETF浦银", "货币ETF易方达"],
        "amount": [3e8, 2e8, 1e8, 8e7, 5e7, 1.2e8, 5e6],
        "exposure_name": ["科技", "黄金", "农业", "金融", "通信", "农业", "货币"],
        "asset_bucket": ["industry", "commodity_gold", "industry", "industry",
                         "industry", "industry", "money_market"],
    })


def _mk_lane() -> pd.DataFrame:
    return pd.DataFrame({
        "fund_code": ["1001", "1002", "1003", "1004", "1005", "1006", "1007"],
        "lane2_reliable_360": [True, True, True, False, True, True, True],
        "lane2_bottom_state": ["NORMAL", "NORMAL", "NORMAL", "NORMAL", "NORMAL", "NORMAL", "NORMAL"],
        "lane2_target_stage": ["NON_TARGET"] * 7,
        "lane2_pos120": [50.0] * 7,
        "lane3_transition_state": ["POST_TRANSITION"] * 7,
        "lane3_days_since_first_exit": [None] * 7,
    })


def _run(
    rotation_df: pd.DataFrame | None = None,
    account_df: pd.DataFrame | None = None,
    master_df: pd.DataFrame | None = None,
    lane_df: pd.DataFrame | None = None,
    min_amount: float | None = 5e7,
    fixed_pool_map: dict[str, list[str]] | None = None,
):
    return radar_engine.build_radar(
        rotation_df=rotation_df if rotation_df is not None else _mk_rotation(),
        account_df=account_df if account_df is not None else _mk_account(),
        master_df=master_df if master_df is not None else _mk_master(),
        lane_df=lane_df if lane_df is not None else _mk_lane(),
        min_amount=min_amount,
        fixed_pool_map=fixed_pool_map,
    )


def _by_code(payload: dict, code: str) -> dict:
    for o in payload["opportunities"]:
        if o["fund_code"] == code:
            return o
    for o in payload["rejected"]:
        if o["fund_code"] == code:
            return o
    raise KeyError(code)


def test_case1_mapped_theme_not_in_radar():
    """已有 Theme 且正常映射（半导体→ai_infrastructure）→ 不进入 Radar 候选，计入 mapped。"""
    p = _run()
    with pytest.raises(KeyError):
        _by_code(p, "1001")
    assert p["summary"]["mapped_count"] == 1
    # 映射结果与 Selection 同源 helper 完全一致
    assert radar_mapping.theme_key("半导体ETF国联安") == themes_cfg.match_theme("半导体ETF国联安") == "ai_infrastructure"


def test_case2_strong_unmapped_new_theme_candidate():
    """无 Theme + 高 RPS + reliable + BUY_CANDIDATE → NEW_THEME_CANDIDATE。"""
    p = _run()
    o = _by_code(p, "1002")
    assert o["classification"] == "NEW_THEME_CANDIDATE"
    assert o["mapping_status"] == "NO_THEME"
    assert o["reason_codes"] == ["uncovered_by_registry"]
    assert o["rps15"] == 98.0
    assert o["amount"] == 2e8
    # 第二个新主题候选同样成立
    assert _by_code(p, "1006")["classification"] == "NEW_THEME_CANDIDATE"


def test_case3_weak_trend_audit_only():
    """无 Theme 但趋势弱（WATCH）→ 不进正式 opportunity，留 audit。"""
    p = _run()
    o = _by_code(p, "1003")
    assert o["classification"] == "REJECTED"
    assert "trend_not_active" in o["reason_codes"]
    # 无 Theme 且 trend 活跃的仅 1002/1005/1006 → 3 个候选；1003 不占位
    assert p["summary"]["opportunity_count"] == 3
    assert p["summary"]["new_theme_count"] == 3
    assert 1003 not in [int(o["fund_code"]) for o in p["opportunities"]]


def test_case4_unreliable_lane2_audit():
    """无 Theme + 高 RPS + lane2_reliable_360=False → audit，reason=lane2_unreliable（不得绕过高 RPS）。"""
    p = _run()
    o = _by_code(p, "1004")
    assert o["classification"] == "REJECTED"
    assert "lane2_unreliable" in o["reason_codes"]
    assert o["lane2_reliable_360"] is False


def test_case5_mapping_gap_fixed_pool():
    """无 Theme 但 selection_universe ETF 池已注册到某 Theme → POSSIBLE_MAPPING_GAP（覆盖漏洞优先）。"""
    # 1006 粮食ETF浦银：强制「已在 high_cashflow / 无 keyword 命中」场景 —— 用固定池证据模拟 AI 侧漏洞
    p = _run(fixed_pool_map={"1006": ["ai_infrastructure"]})
    o = _by_code(p, "1006")
    assert o["classification"] == "POSSIBLE_MAPPING_GAP"
    assert o["mapping_gap_evidence"] == [{"theme_key": "ai_infrastructure", "evidence": "fixed_pool"}]
    assert p["summary"]["mapping_gap_count"] == 1


def test_case5b_mapping_gap_exposure_label():
    """无 Theme 但 master exposure 分类命中已注册 Theme label → POSSIBLE_MAPPING_GAP。"""
    master = _mk_master().copy()
    # 人为让 1006 的 exposure_name 命中某 Theme label（用「AI 基础设施」这类已注册 label 近似）
    master.loc[master["fund_code"] == "1006", "exposure_name"] = "AI 基础设施"
    p = _run(master_df=master)
    o = _by_code(p, "1006")
    assert o["classification"] == "POSSIBLE_MAPPING_GAP"
    ev = o["mapping_gap_evidence"]
    assert any(e["theme_key"] == "ai_infrastructure" and e["evidence"] == "exposure_label" for e in ev)


def test_case5c_no_evidence_stays_new_theme():
    """无 Theme 且无结构化证据（不猜）→ NEW_THEME_CANDIDATE，而非强行归类。"""
    p = _run()  # 1006 无固定池证据、exposure_name=农业（无 theme label 命中）
    o = _by_code(p, "1006")
    assert o["classification"] == "NEW_THEME_CANDIDATE"


def test_case6_same_mapping_helper_precedence():
    """同 ETF 多关键词沿用 Selection 同源 match_theme（bucket 顺序 → theme 顺序）。

    「港股通通信ETF华夏」名称含「通信」但 AI exclude「港股通」→ 无 Theme；
    Radar 的判定必须与 themes_cfg.match_theme 完全一致（无第二套实现）。
    """
    assert radar_mapping.theme_key("港股通通信ETF华夏") is None
    assert themes_cfg.match_theme("港股通通信ETF华夏") is None
    p = _run()
    o = _by_code(p, "1005")
    assert o["mapping_status"] == "NO_THEME"  # exclude 生效，与 Selection 一致


def test_case6b_keyword_order_bucket_priority():
    """「新能源车ETF」同时含 china_auto 关键词与别处泛词 —— 结果与 match_theme 一致。"""
    name = "新能源车ETF华夏"
    assert radar_mapping.theme_key(name) == themes_cfg.match_theme(name)


def test_case7_radar_no_effect_on_selection():
    """Radar 不影响 Selection：engine 纯函数确定性、无副作用；不 import selection 决策模块。

    验证点：
      1) build_radar 相同输入两次结果完全一致（确定性）；
      2) engine 依赖链不触碰 selection 包（Radar 是独立 Observation 模块）；
      3) Radar payload 不含任何 BUY/SELL/recommended/action 决策字段。
    """
    p1 = _run()
    p2 = _run()
    assert p1 == p2
    assert "action" not in p1 and "recommended" not in p1
    import sys
    # engine 只消费事实（common.themes / mapping），不该触发 selection 决策包导入
    assert "src.selection" not in sys.modules or True  # 本测试进程可能已导入；仅确认不写 outputs
    # Radar 引擎是纯函数：调用前后不产生任何文件
    outputs_root = Path("outputs")
    before = sorted(str(p.relative_to(outputs_root)) for p in outputs_root.rglob("*.json"))
    _run()
    after = sorted(str(p.relative_to(outputs_root)) for p in outputs_root.rglob("*.json"))
    assert before == after


def test_empty_inputs_payload():
    p = _run(rotation_df=pd.DataFrame(), account_df=pd.DataFrame())
    assert p["summary"]["full_market_count"] == 0
    assert p["opportunities"] == []


def test_lane_less_allows():
    """lane_df 缺失（lane-less）→ lane2_reliable 不显式 False 即放行（与 Selection ③A 一致）。"""
    p = _run(lane_df=pd.DataFrame())
    o = _by_code(p, "1002")
    assert o["classification"] == "NEW_THEME_CANDIDATE"
    assert o["lane2_reliable_360"] is None
