"""
驱动模式展示层（machine semantics → human semantics）。

独立于 confirmation Policy 与任何事实层：本模块只做「机器代码 → 中文综合语义」
的纯展示映射，不制造/不重算任何事实，不 import confirmation/contribution 的 Policy。

被主报告（report.py 第二问）与第三问详情（confirmation_sections）共同消费，
避免「全市场行业报告为显示文案反向依赖主题确认模块」的耦合。

核心职责：
  - composite_drive_label(cs, bs): 4×4 组合 → 一句话综合语义（主报告/概览用）
  - drive_detail(cs, bs, top1_share, participation): 双维 + 数值详情（详情页用）
  - unknown / missing 一律显式 fallback，不静默为空
"""

from __future__ import annotations

from typing import Any

# ── 机器代码 → 单维中文标签 ────────────────────────────────
CONTRIBUTION_LABELS = {
    "single_core": "单核主导",
    "leader_concentrated": "集中领涨",
    "multi_leader": "多龙头带动",
    "distributed": "分散上涨",
}

BREADTH_LABELS = {
    "broad": "广泛上涨",
    "moderate": "中度扩散",
    "narrow": "少数带动",
    "divergent": "明显分化",
}

# ── 4×4 综合语义映射（主报告/概览的一行文案） ──────────────
# 键 (contribution, breadth) → 综合语义。未覆盖组合视为 missing，显式 fallback。
_COMPOSITE_DRIVE: dict[tuple[str, str], str] = {
    ("single_core", "broad"): "龙头拉动普涨",
    ("single_core", "moderate"): "龙头领涨、温和扩散",
    ("single_core", "narrow"): "龙头独涨",
    ("single_core", "divergent"): "龙头拉动、内部分化",
    ("leader_concentrated", "broad"): "龙头集中普涨",
    ("leader_concentrated", "moderate"): "龙头集中、温和扩散",
    ("leader_concentrated", "narrow"): "龙头集中、少数带动",
    ("leader_concentrated", "divergent"): "龙头集中、内部分化",
    ("multi_leader", "broad"): "多股共振普涨",
    ("multi_leader", "moderate"): "多股共振、温和扩散",
    ("multi_leader", "narrow"): "多股带动、部分参与",
    ("multi_leader", "divergent"): "多股带动、内部分化",
    ("distributed", "broad"): "分散普涨",
    ("distributed", "moderate"): "分散上涨、温和扩散",
    ("distributed", "narrow"): "分散、少数参与",
    ("distributed", "divergent"): "分散上涨、内部分化",
}

_UNKNOWN_LABEL = "驱动信息不足"


def composite_drive_label(contribution: Any, breadth: Any) -> str:
    """机器代码 → 综合语义标签。

    Args:
        contribution: contribution_structure 机器代码（single_core / ...）
        breadth: breadth_structure 机器代码（broad / ...）

    Returns:
        综合中文语义；未知 / 缺失 / 数据不足 一律返回「驱动信息不足」（显式 fallback）。
    """
    cs = str(contribution) if contribution is not None and str(contribution) else ""
    bs = str(breadth) if breadth is not None and str(breadth) else ""
    if not cs or not bs:
        return _UNKNOWN_LABEL
    if cs in ("数据不足", "insufficient", "failed") or bs in ("数据不足", "insufficient", "failed"):
        return _UNKNOWN_LABEL
    return _COMPOSITE_DRIVE.get((cs, bs), _UNKNOWN_LABEL)


def drive_detail(
    contribution: Any,
    breadth: Any,
    top1_share: Any = None,
    participation_rate: Any = None,
) -> str:
    """双维 + 数值详情（详情页用）。

    Returns:
        如「贡献：单核主导（Top1=67%）· 参与：广泛上涨（84%）」；
        未知维度显式标注「—」。
    """
    cs = str(contribution) if contribution is not None and str(contribution) else ""
    bs = str(breadth) if breadth is not None and str(breadth) else ""
    c_label = CONTRIBUTION_LABELS.get(cs, "—")
    b_label = BREADTH_LABELS.get(bs, "—")

    top1_txt = _pct(top1_share)
    part_txt = _pct(participation_rate)

    c_part = f"贡献：{c_label}"
    if top1_txt != "—":
        c_part += f"（Top1={top1_txt}）"
    b_part = f"参与：{b_label}"
    if part_txt != "—":
        b_part += f"（{part_txt}）"
    return f"{c_part} · {b_part}"


def _pct(v: Any) -> str:
    try:
        val = float(v)
        if val != val:  # NaN
            return "—"
        return f"{val * 100:.0f}%"
    except (TypeError, ValueError):
        return "—"
