"""
Opportunity Radar — Theme Mapping（复用 Selection 同源 helper）

两条原则（V1 冻结）：
1. Radar 的主题归属判定与 Selection 完全一致 —— 一律调用
   src/common/themes.match_theme（先 exclude 后 include、bucket 顺序优先），
   **不复制、不另写一套关键词匹配实现**。Radar 若与 Selection 对同一 ETF
   给出不同映射即为 bug。
2. POSSIBLE_MAPPING_GAP 只依赖已有结构化证据做「疑似」标记，不做语义推理：
     - fixed-pool 注册（selection_universe.yaml theme_etf / sub_industry_etf / watch_etf）
     - master exposure 分类（exposure_type / exposure_name / exposure_tags）
   Radar 只消费这些既有字段；没有足够结构化证据时不猜（保持 no_theme_mapping）。

注意：本文件不「重新决定」theme 归属，只做两件事——
  - theme_key(fund_name)  —— 与 Selection 相同的映射结果
  - mapping_gap_evidence(fund_row, master_row) —— 对已判 no_theme_mapping 的
    ETF 输出「疑似属于已注册 Theme」的结构化证据（供 classification 使用）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.common import themes as themes_cfg


def theme_key(fund_name: str | None) -> str | None:
    """与 Selection / Layer① 完全同源的 theme 归属（唯一事实源 helper）。"""
    return themes_cfg.match_theme(fund_name)


def registered_theme_keys() -> list[str]:
    return list(themes_cfg.load_themes().keys())


def _clean_tags(raw: Any) -> list[str]:
    """把 master exposure_tags 解析为词列表（列值形态各异：list/JSON 字符串/空）。"""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(t) for t in raw if t]
    s = str(raw).strip()
    if not s or s.lower() in {"nan", "none"}:
        return []
    try:
        v = json.loads(s)
        if isinstance(v, list):
            return [str(t) for t in v if t]
        return [s]
    except Exception:
        return [t for t in s.replace("[", "").replace("]", "").replace("'", "")
                .replace('"', "").split(",") if t.strip()]


def _master_evidence_fields(master_row: dict[str, Any] | None) -> list[str]:
    """master 里可作 POSSIBLE_MAPPING_GAP 结构化证据的字段值。

    仅收集已有分类结果（exposure_type / exposure_name / exposure_tags /
    asset_bucket），**不引入新语义**。
    """
    if not master_row:
        return []
    fields: list[str] = []
    for key in ("exposure_type", "exposure_name", "primary_bucket", "asset_bucket"):
        v = master_row.get(key)
        if v is not None and str(v).strip() and str(v).lower() not in {"nan", "none"}:
            fields.append(str(v).strip())
    fields.extend(_clean_tags(master_row.get("exposure_tags")))
    return fields


def mapping_gap_evidence(
    master_row: dict[str, Any] | None,
    fixed_pool_themes: list[str] | None = None,
) -> list[dict[str, str]]:
    """对 no_theme_mapping 的 ETF 输出「疑似属于已注册 Theme」的证据。

    Args:
        master_row: etf_master.parquet 中该 ETF 的结构化分类行（可缺）。
        fixed_pool_themes: 该 ETF 若在 selection_universe.yaml 的 ETF 固定资产池
            （theme_etf / sub_industry_etf / watch_etf）注册，列出其 theme keys。

    Returns:
        [{theme_key, evidence}]。空 = 没有结构化证据说明属于某已注册 Theme，
        Radar 应将其标为 NEW_THEME_CANDIDATE / UNCLASSIFIED，不做猜测。
    """
    out: list[dict[str, str]] = []
    # ① fixed-pool 注册（human-curated 资产池的显式归属）
    for tk in fixed_pool_themes or []:
        out.append({"theme_key": tk, "evidence": "fixed_pool"})

    # ② master 结构化分类与已注册 theme 的名称近似/别名匹配
    #    （不建第二套关键词表；用 theme 自身的 label / objective 词做近似命中）
    if master_row:
        evidence_fields = " ".join(_master_evidence_fields(master_row)).lower()
        if evidence_fields:
            for th in themes_cfg.load_themes().values():
                label = th.label.lower()
                if label and label in evidence_fields and th.key not in {e["theme_key"] for e in out}:
                    out.append({"theme_key": th.key, "evidence": "exposure_label"})
    return out


def fixed_pool_theme_map(path: Path | None = None) -> dict[str, list[str]]:
    """selection_universe.yaml 的 ETF 固定资产池：fund_code → [theme keys]。

    只收集 tier key ∈ {theme_etf, sub_industry_etf, watch_etf} 的 ETF 资产
    （theme ETF 表达池），个股资产（symbol 为 A股代码）不参与。
    用于 POSSIBLE_MAPPING_GAP 证据：该代码已被人工注册到某 theme 的 ETF 池，
    但 keyword 未命中 → 疑似 etf_keywords 覆盖漏洞。
    """
    import yaml
    from src.common.paths import selection_universe_path

    p = path or selection_universe_path()
    if not p.exists():
        return {}
    cfg = yaml.safe_load(open(p, "r", encoding="utf-8")) or {}
    pool: dict[str, list[str]] = {}
    etf_tier_keys = {"theme_etf", "sub_industry_etf", "watch_etf"}
    for theme_key, theme_cfg in (cfg.get("themes") or {}).items():
        for tier in theme_cfg.get("tiers") or []:
            if tier.get("key") not in etf_tier_keys:
                continue
            for asset in tier.get("assets") or []:
                sym = str(asset.get("symbol", "")).strip()
                if not sym:
                    continue
                pool.setdefault(sym, [])
                if theme_key not in pool[sym]:
                    pool[sym].append(theme_key)
    return pool
