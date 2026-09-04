"""
Opportunity Radar V1.1 — Candidate Direction Taxonomy 测试

覆盖：
  - spec loader（rule_id/status 校验、版本化可读、provenance 对齐 house）
  - 逐只归属断言：2026-09-02 快照 81 NEW_THEME_CANDIDATE → 期望 candidate_theme_key / beta
    （词表定稿依据；改 YAML 词表若改变归属 → 测试失败，提示需人工确认）
  - market_scope（真实市场）与 market_beta（宽基布尔）分离语义
  - candidate_themes 聚合：同方向合并、跨市场拆分、Market Beta 单列、代表=amount 最大、
    UNCLASSIFIED 不进方向
  - 回归：已注册 Theme 仍走 match_theme（Case 1/6 语义）；不产生 action/recommended
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.opportunity_radar import radar as radar_engine
from src.opportunity_radar.spec import (
    RULE_ID,
    STATUS,
    load_direction_spec,
    taxonomy_provenance,
)
from src.opportunity_radar.taxonomy import (
    aggregate_directions,
    classify_candidate,
    infer_market_scope,
    is_market_beta,
    match_direction,
)

# ── 2026-09-02 快照逐只归属断言（fund_code → candidate_theme_key 或 "beta"）─
# 来源：outputs/opportunity_radar/opportunity_radar_20260902.json 的 81 NEW_THEME_CANDIDATE
# 按 config/research/opportunity_directions_v1.yaml 断言定稿。
EXPECTED_20260902 = {
    "159102": "pharma.hk", "159131": "hk_tech.hk", "159137": "pharma.hk",
    "159167": "pharma.hk", "159185": "hk_tech.hk", "159196": "hk_tech.hk",
    "159198": "hk_tech.hk", "159227": "defense.a_share", "159241": "defense.a_share",
    "159263": "dividend.a_share", "159267": "defense.a_share", "159281": "dividend.hk",
    "159307": "dividend.a_share", "159309": "oilgas.a_share", "159321": "gold.a_share",
    "159366": "pharma.hk", "159502": "pharma.overseas", "159509": "overseas_tech.overseas",
    "159518": "oilgas.overseas", "159531": "beta", "159532": "beta",
    "159545": "dividend.hk", "159547": "dividend.a_share", "159562": "gold.a_share",
    "159615": "pharma.hk", "159687": "beta", "159691": "dividend.hk",
    "159718": "pharma.hk", "159825": "agri.a_share", "159842": "sec.a_share",
    "159865": "agri.a_share", "159892": "pharma.hk", "159930": "energy_chem.a_share",
    "159981": "energy_chem.a_share", "159985": "agri.a_share", "510210": "beta",
    "510230": "cn_finance.a_share", "510720": "dividend.a_share", "510760": "beta",
    "510880": "dividend.a_share", "510910": "beta", "510980": "beta",
    "512070": "sec.a_share", "512700": "bank.a_share", "512710": "defense.a_share",
    "512820": "bank.a_share", "512890": "dividend.a_share", "513090": "hk_fin.hk",
    "513120": "pharma.hk", "513190": "hk_fin.hk", "513240": "hk_tech.hk",
    "513280": "pharma.hk", "513290": "pharma.overseas", "513350": "oilgas.overseas",
    "513530": "dividend.hk", "513630": "dividend.hk", "513690": "dividend.hk",
    "513750": "hk_fin.hk", "513780": "pharma.hk", "513820": "dividend.hk",
    "513910": "dividend.hk", "513920": "dividend.hk", "513930": "pharma.hk",
    "515020": "bank.a_share", "515080": "dividend.a_share", "515100": "dividend.a_share",
    "515180": "dividend.a_share", "515210": "coal_steel.a_share", "515220": "coal_steel.a_share",
    "515300": "dividend.a_share", "515450": "dividend.a_share", "515630": "sec.a_share",
    "516310": "bank.a_share", "516810": "agri.a_share", "517400": "gold.a_share",
    "517520": "gold.a_share", "561360": "oilgas.a_share", "561580": "dividend.a_share",
    "562660": "beta", "563020": "dividend.a_share", "563300": "beta",
}


@pytest.fixture(scope="module")
def direction_spec():
    return load_direction_spec()


def test_spec_loader_valid(direction_spec):
    assert direction_spec["rule_id"] == RULE_ID
    assert direction_spec["status"] == STATUS
    assert direction_spec["version"] >= 1
    # provenance 对齐 house：study + source_artifact（artifact 路径）
    assert "provenance" in direction_spec
    assert "source_artifact" in direction_spec["provenance"]
    assert direction_spec["scope_inference"]["hk"]
    assert direction_spec["broad_beta"]["keywords"]
    assert len(direction_spec["directions"]) >= 10


def test_spec_loader_invalid_rule_id(tmp_path):
    import yaml
    bad = {"rule_id": "WRONG", "status": STATUS, "directions": []}
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump(bad), encoding="utf-8")
    with pytest.raises(ValueError):
        load_direction_spec(p)


def test_spec_loader_invalid_status(tmp_path):
    import yaml
    bad = {"rule_id": RULE_ID, "status": "FROZEN_RESEARCH_HYPOTHESIS", "directions": []}
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump(bad), encoding="utf-8")
    with pytest.raises(ValueError):
        load_direction_spec(p)


def test_taxonomy_provenance(direction_spec):
    # 输出侧 key 命名与 scanner 一致：rule_id / rule_status / rule_spec_source（相对路径）
    prov = taxonomy_provenance(direction_spec)
    assert prov["rule_id"] == RULE_ID
    assert prov["rule_status"] == STATUS
    assert prov["rule_spec_source"] == "config/research/opportunity_directions_v1.yaml"
    assert prov["taxonomy_version"] == direction_spec["version"]


# ── scope / direction / candidate_theme_key 判定 ─────────────────────

def test_infer_market_scope_only(direction_spec):
    # market_scope 只返回真实市场：宽基 beta 仍是 a_share/overseas（beta 由 market_beta 单独承载）
    assert infer_market_scope("红利低波ETF华泰柏瑞", direction_spec) == "a_share"
    assert infer_market_scope("港股红利ETF博时", direction_spec) == "hk"
    assert infer_market_scope("恒生生物科技ETF华夏", direction_spec) == "hk"
    assert infer_market_scope("标普生物科技ETF嘉实", direction_spec) == "overseas"
    assert infer_market_scope("上证指数ETF富国", direction_spec) == "a_share"
    assert infer_market_scope("亚太精选ETF南方", direction_spec) == "overseas"


def test_is_market_beta(direction_spec):
    assert is_market_beta("上证指数ETF富国", direction_spec) is True
    assert is_market_beta("中证2000ETF华夏", direction_spec) is True
    assert is_market_beta("亚太精选ETF南方", direction_spec) is True
    assert is_market_beta("红利低波ETF华泰柏瑞", direction_spec) is False


def test_match_direction_first_hit(direction_spec):
    # bank ≠ sec：银行只归 bank
    bank = match_direction("银行ETF易方达", direction_spec)
    assert bank is not None and bank["key"] == "bank"
    sec = match_direction("券商ETF银华", direction_spec)
    assert sec is not None and sec["key"] == "sec"
    # 港股科技走在 generic pharma 之前（港股通信息技术 不含医药词，仍归 hk_tech）
    hk = match_direction("港股通信息技术ETF华宝", direction_spec)
    assert hk is not None and hk["key"] == "hk_tech"


def test_classify_candidate_theme_key(direction_spec):
    # 跨市场拆开：A股红利 ≠ 港股红利
    a = classify_candidate("红利低波ETF华泰柏瑞", direction_spec)
    h = classify_candidate("港股红利ETF博时", direction_spec)
    assert a["candidate_theme_key"] == "dividend.a_share"
    assert h["candidate_theme_key"] == "dividend.hk"
    assert a["direction_key"] == h["direction_key"] == "dividend"
    assert a["market_scope"] == "a_share" and h["market_scope"] == "hk"
    assert a["market_beta"] is False and a["classified"] is True


def test_classify_broad_beta(direction_spec):
    c = classify_candidate("中证2000ETF华夏", direction_spec)
    assert c["market_beta"] is True and c["candidate_theme_key"] is None
    # market_scope 保留真实市场（宽基仍是 a_share），不是假 scope="beta"
    assert c["market_scope"] == "a_share"


def test_classify_overseas_broad_beta(direction_spec):
    c = classify_candidate("亚太精选ETF南方", direction_spec)
    assert c["market_beta"] is True and c["candidate_theme_key"] is None
    assert c["market_scope"] == "overseas"


def test_classify_unclassified(direction_spec):
    c = classify_candidate("某完全未知方向ETF", direction_spec)
    assert c["classified"] is False and c["candidate_theme_key"] is None and c["market_beta"] is False


# ── 逐只归属断言（2026-09-02 快照全部 81 只）────────────────────────

def test_snapshot_20260902_per_etf_attribution():
    """重放 2026-09-02 缓存数据 → 81 NEW_THEME_CANDIDATE 归属必须与断言表一致。

    依赖仓库内已落盘产物（data/etf_signal/daily·signals / outputs/etf_signal/three_lane），
    只读不联网；缺文件时 skip（不回退其他日期）。
    """
    from pathlib import Path

    rot = Path("data/etf_signal/daily/rotation_20260902.parquet")
    acc = Path("data/etf_signal/signals/account_candidates_20260902.parquet")
    lane = Path("outputs/etf_signal/three_lane_20260902.parquet")
    master = Path("data/etf_signal/master/etf_master.parquet")
    if not (rot.exists() and acc.exists() and lane.exists() and master.exists()):
        pytest.skip("2026-09-02 缓存产物缺失（data/etf_signal 或 outputs/etf_signal）")

    from src.common.spec.loaders import load_etf_selection_spec
    payload = radar_engine.build_radar(
        rotation_df=pd.read_parquet(rot),
        account_df=pd.read_parquet(acc),
        master_df=pd.read_parquet(master),
        lane_df=pd.read_parquet(lane),
        min_amount=float(load_etf_selection_spec().min_amount),
        direction_spec=load_direction_spec(),
    )
    assert payload["summary"]["new_theme_count"] == len(EXPECTED_20260902)

    got: dict[str, str] = {}
    for o in payload["opportunities"]:
        if o.get("classification") != "NEW_THEME_CANDIDATE":
            continue
        got[o["fund_code"]] = o.get("candidate_theme_key") or ("beta" if o.get("market_beta") else "UNCLASS")
    assert got == EXPECTED_20260902

    # 方向数 / Market Beta 数与断言一致
    assert payload["summary"]["candidate_theme_count"] == 17
    assert payload["summary"]["broad_beta_count"] == 9
    assert payload["summary"]["unclassified_count"] == 0
    assert len(payload["candidate_themes"]) == 17
    assert len(payload["broad_beta"]) == 9


# ── candidate_themes 聚合 ────────────────────────────────────────────

def _mk_rows():
    # 四只「银行」+ 两只「红利低波」→ bank.a_share 一个方向、dividend.a_share 一个方向
    data = [
        {"fund_code": "B1", "fund_name": "银行ETF华夏", "rps15": 98.1, "amount": 9e8},
        {"fund_code": "B2", "fund_name": "银行ETF南方", "rps15": 92.2, "amount": 2e8},
        {"fund_code": "B3", "fund_name": "银行ETF易方达", "rps15": 97.9, "amount": 5e8},
        {"fund_code": "D1", "fund_name": "红利低波ETF华泰柏瑞", "rps15": 95.9, "amount": 8e8},
        {"fund_code": "D2", "fund_name": "红利ETF易方达", "rps15": 91.6, "amount": 3e8},
    ]
    return data


def test_aggregate_directions_grouping(direction_spec):
    cls_rows = []
    for d in _mk_rows():
        c = classify_candidate(d["fund_name"], direction_spec)
        cls_rows.append({**c, **d})
    agg = aggregate_directions(cls_rows)["candidate_themes"]
    keys = {g["candidate_theme_key"] for g in agg}
    assert keys == {"bank.a_share", "dividend.a_share"}
    bank = next(g for g in agg if g["candidate_theme_key"] == "bank.a_share")
    assert bank["n_etfs"] == 3
    assert bank["representative"]["fund_code"] == "B1"  # amount 最大
    assert set(bank["members"]) == {"B1", "B2", "B3"}
    div = next(g for g in agg if g["candidate_theme_key"] == "dividend.a_share")
    assert div["n_etfs"] == 2
    assert div["representative"]["fund_code"] == "D1"


def test_aggregate_excludes_beta_and_unclassified(direction_spec):
    rows = [
        {"fund_code": "X1", "fund_name": "上证指数ETF富国", "rps15": 84.5, "amount": 1e9},
        {"fund_code": "X2", "fund_name": "完全未知ETF", "rps15": 90.0, "amount": 1e9},
        {"fund_code": "B1", "fund_name": "银行ETF华夏", "rps15": 98.1, "amount": 9e8},
    ]
    cls_rows = []
    for d in rows:
        c = classify_candidate(d["fund_name"], direction_spec)
        cls_rows.append({**c, **d})
    agg = aggregate_directions(cls_rows)["candidate_themes"]
    assert [g["candidate_theme_key"] for g in agg] == ["bank.a_share"]


# ── 回归：不改 Selection / 不产生决策字段 ─────────────────────────

def test_direction_payload_no_decision_fields(direction_spec):
    rows = _mk_rows()
    cls_rows = []
    for d in rows:
        c = classify_candidate(d["fund_name"], direction_spec)
        cls_rows.append({**c, **d})
    agg = aggregate_directions(cls_rows)
    assert "action" not in agg and "recommended" not in agg
