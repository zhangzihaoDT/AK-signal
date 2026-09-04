"""
Opportunity Radar — Candidate Direction Taxonomy spec loader（研究级 YAML）

唯一规则真源：config/research/opportunity_directions_v1.yaml（版本化可编辑，不设 FROZEN 门槛）。
放 config/research/ → 不进 config_hash（非 Strategy Spec），调词不触发 replay parity。

与 theme_registry 的关系：本表是「draft 候选方向」（已注册 Theme 之外），供人工注册 theme_registry；
不覆盖/不取代 match_theme 的已注册映射。

结构（V1.1）：
  scope_inference.hk / .overseas   market-scope 关键词（未命中 → a_share）
  broad_beta.keywords              宽基/区域 Market Beta（非 Theme，单列）
  directions[]                     语义方向（key/label/bucket/keywords/note），有序首命中
聚合：candidate_theme_key = direction.key + "." + market_scope（跨市场自动拆开）。
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml

from src.common.paths import config_dir

RULE_ID = "OPPORTUNITY_DIRECTION_TAXONOMY_V1"
STATUS = "RESEARCH_TAXONOMY"
SPEC_FILENAME = "opportunity_directions_v1.yaml"
# 与 scanner / current_eval 输出同款相对路径写法（rule_spec_source）
SPEC_REL_PATH = f"config/research/{SPEC_FILENAME}"


def direction_spec_path() -> Path:
    return config_dir() / "research" / SPEC_FILENAME


@functools.lru_cache(maxsize=None)
def load_direction_spec(path: Path | None = None) -> dict[str, Any]:
    """加载 Candidate Direction Taxonomy（版本化可编辑）。

    校验 rule_id / status（仿 repair_retest / trend_transition_state loader）：
    字段缺失或不符 → 报错（防用错文件 / 未定稿前被当规则消费）。
    版本化更新：编辑 YAML + bump version 即可，不抛 FROZEN 错误。
    """
    p = path or direction_spec_path()
    with p.open(encoding="utf-8") as f:
        spec = yaml.safe_load(f) or {}
    if spec.get("rule_id") != RULE_ID:
        raise ValueError(f"invalid direction taxonomy rule_id: {p} (expect {RULE_ID})")
    if spec.get("status") != STATUS:
        raise ValueError(f"invalid direction taxonomy status: {p} (expect {STATUS})")
    if not spec.get("directions") or "scope_inference" not in spec:
        raise ValueError(f"direction taxonomy missing directions/scope_inference: {p}")
    return spec


def taxonomy_provenance(spec: dict[str, Any] | None = None) -> dict[str, Any]:
    """输出侧 provenance（radar JSON 顶层 / HTML 页脚复用）。

    key 命名与 scanner.py 一致：rule_id / rule_status / rule_spec_source；
    额外带 taxonomy_version（版本化可编辑模型）。rule_spec_source 为相对路径。
    """
    spec = spec or load_direction_spec()
    return {
        "rule_id": spec.get("rule_id", RULE_ID),
        "rule_status": spec.get("status", STATUS),
        "taxonomy_version": spec.get("version"),
        "rule_spec_source": SPEC_REL_PATH,
    }
