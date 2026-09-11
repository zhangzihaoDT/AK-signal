"""Leader Breakout Event V1 — 主题未确认时龙头个股单点突破的前瞻收益研究。

独立研究模块，只读已落盘事实（processed 个股行情 + 申万行业 metrics），
不联网、不改任何 Layer①②③ 生产逻辑。规则真源见
`config/research/leader_breakout_v1.yaml`（冻结）。
"""

from __future__ import annotations

from src.common.paths import outputs_dir

STUDY_DIR = outputs_dir() / "research" / "leader_breakout"

RULE_ID = "LEADER_BREAKOUT_V1"
RULE_STATUS = "FROZEN_RESEARCH_HYPOTHESIS"
SPEC_SOURCE = "config/research/leader_breakout_v1.yaml"

__all__ = ["STUDY_DIR", "RULE_ID", "RULE_STATUS", "SPEC_SOURCE"]
