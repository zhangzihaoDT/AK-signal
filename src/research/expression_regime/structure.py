"""
表达方式结构输入源（v0.10 混合方案）— 可插拔的「市场结构 → expression」判定。

生产 decide_expression 用行业内部结构（participation_rate / hhi / top3_share）：
  broad            = 中位 participation_rate ≥ 0.60（行业广泛上涨）
  leader_dominated = 中位 hhi ≥ 0.15 或 中位 top3_share ≥ 0.60（龙头集中）
  映射：broad 且非龙头 → ETF_PRIORITY；龙头 → LEADER_PRIORITY；其余 → CORE_PLUS_LEADER

历史数据限制：行业结构（Enrichment，来自成分股 drilldown）只有几周覆盖。
为做 20/60/120D 历史事件研究，提供第二个结构输入源 —— Tier 篮子结构（可从
universe 个股价格 2018+ 历史重放）：
  broad            = 中位 advance_ratio ≥ 阈值（篮子内多数个股上涨，类比行业参与率）
  leader_dominated = 中位 leader_contribution ≥ 阈值（龙头贡献集中，类比 HHI/Top3）

两个输入源输出同一映射（broad / leader_dominated → expression），可插拔切换。
阈值与生产 decide_expression 语义对齐，集中在本模块便于审计。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

# 表达方式（与 selection.EXPRESSION_LABELS 保持一致）
ETF_PRIORITY = "ETF_PRIORITY"
LEADER_PRIORITY = "LEADER_PRIORITY"
CORE_PLUS_LEADER = "ETF_CORE_PLUS_LEADER"
WATCHLIST_ONLY = "WATCHLIST_ONLY"


@dataclass(frozen=True)
class ExpressionRegimeSpec:
    """结构 → expression 的映射阈值（研究侧 Policy 参数，非 Observation）。

    默认值与生产 decide_expression / config/strategy_spec.yaml 语义对齐：
      broad 参与率 ≥ 0.60；leader HHI ≥ 0.15 / Top3 ≥ 0.60；Tier 近似阈值见注释。
    """
    broad_participation: float = 0.60       # Industry: median participation_rate
    leader_hhi: float = 0.15                # Industry: median hhi
    leader_top3: float = 0.60               # Industry: median top3_share
    tier_broad_advance: float = 0.60        # Tier: 中位 advance_ratio（上涨比例）
    tier_leader_contribution: float = 0.50  # Tier: 中位 leader_contribution（龙头收益占比）


def expression_from_structures(
    broad: bool | None,
    leader_dominated: bool | None,
) -> tuple[str, str]:
    """由结构特征合成表达方式（与生产 decide_expression 映射一致）。

    Returns:
        (expression, reason)
    """
    if broad is None and leader_dominated is None:
        return CORE_PLUS_LEADER, "结构信息不足，按扩散期处理（ETF 核心 + 龙头卫星）"
    broad_f = bool(broad)
    leader_f = bool(leader_dominated)
    if broad_f and not leader_f:
        return ETF_PRIORITY, "广泛上涨（结构分散），ETF 完整承接行业 Beta"
    if leader_f:
        return LEADER_PRIORITY, "龙头贡献集中，优先龙头个股，ETF 作低风险替代"
    return CORE_PLUS_LEADER, "扩散形成中，ETF 作核心、龙头作卫星"


def _med(vals: list[float | None]) -> float | None:
    clean = [float(v) for v in vals if v is not None and not (isinstance(v, float) and v != v)]
    if not clean:
        return None
    return float(np.median(clean))


class TierStructureInput:
    """Tier 篮子结构输入源（历史重放可用，2018+）。

    消费 tier_confirmation 行的篮子特征：advance_ratio（上涨比例）、
    leader_contribution（龙头贡献度）。中位聚合后映射 broad / leader_dominated。
    语义是「主题个股篮子」结构，与行业内部结构（参与率/HHI）同构但不等价——
    属于研究近似的显式标注，非生产判定。
    """

    def __init__(self, spec: ExpressionRegimeSpec | None = None):
        self.spec = spec or ExpressionRegimeSpec()

    def features(self, theme: str, tier_rows: list[dict[str, Any]]) -> dict[str, Any]:
        active = [r for r in tier_rows
                  if r.get("theme") == theme and r.get("data_status") != "unavailable"]
        if not active:
            return {"broad": None, "leader_dominated": None,
                    "median_advance_ratio": None, "median_leader_contribution": None}
        adv = _med([r.get("advance_ratio") for r in active])
        lc = _med([r.get("leader_contribution") for r in active])
        return {
            "broad": adv is not None and adv >= self.spec.tier_broad_advance,
            "leader_dominated": lc is not None and lc >= self.spec.tier_leader_contribution,
            "median_advance_ratio": round(adv, 3) if adv is not None else None,
            "median_leader_contribution": round(lc, 3) if lc is not None else None,
            "n_tiers_active": len(active),
        }

    def expression(self, theme: str, tier_rows: list[dict[str, Any]]) -> tuple[str, str, dict[str, Any]]:
        feat = self.features(theme, tier_rows)
        expr, reason = expression_from_structures(feat.get("broad"), feat.get("leader_dominated"))
        reason = f"{reason}（Tier 结构近似：上涨比例中位={feat.get('median_advance_ratio')}, " \
                 f"龙头贡献中位={feat.get('median_leader_contribution')}）"
        return expr, reason, feat


class IndustryStructureInput:
    """行业内部结构输入源（生产确认产物）。

    消费 evaluate_themes 输出的 median_participation / median_hhi / median_top3_share
    （来自 confirmation parquet 的 drilldown 字段），与生产 decide_expression 同源。
    当前 Enrichment 历史覆盖有限（几周），用于近期冒烟与生产对照。
    """

    def __init__(self, spec: ExpressionRegimeSpec | None = None):
        self.spec = spec or ExpressionRegimeSpec()

    def features(self, theme_meta: dict[str, Any]) -> dict[str, Any]:
        part = theme_meta.get("median_participation")
        hhi = theme_meta.get("median_hhi")
        top3 = theme_meta.get("median_top3_share")
        broad = part is not None and float(part) >= self.spec.broad_participation
        leader = (hhi is not None and float(hhi) >= self.spec.leader_hhi) or \
                 (top3 is not None and float(top3) >= self.spec.leader_top3)
        return {"broad": broad, "leader_dominated": leader,
                "median_participation": part, "median_hhi": hhi, "median_top3_share": top3}

    def expression(self, theme_meta: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
        feat = self.features(theme_meta)
        expr, reason = expression_from_structures(feat.get("broad"), feat.get("leader_dominated"))
        return expr, reason, feat


def build_structure_input(source: str) -> TierStructureInput | IndustryStructureInput:
    src = (source or "tier").strip().lower()
    if src in ("industry", "industry_structure"):
        return IndustryStructureInput()
    if src in ("tier", "tier_structure"):
        return TierStructureInput()
    raise ValueError(f"unknown structure source: {source} (tier | industry)")
