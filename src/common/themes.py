"""
多主题框架配置加载（v0.4.3）

单一事实源：config/themes_two_directions.yaml（当前范围为两个方向：
  Core · AI 基础设施（长期增长） / Quality · 高现金流资产（稳定现金流防守））。
结构：bucket（组合意图） → theme（市场方向） → industries（申万二级行业）+ etf_keywords。

消费方：
  Layer ①  etf_signal.rotation      每主题 ETF 焦点组
  Layer ②  sw_industry_rps.confirm  每主题行业群共振
  Layer ③  selection                每主题候选资产（ETF + 个股观察池）
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .paths import config_dir

RELEVANCE_LABEL = {"core": "核心", "related": "相关"}


@dataclass(frozen=True)
class ThemeIndustry:
    code: str
    name: str
    relevance: str  # core / related


@dataclass(frozen=True)
class ThemeTier:
    key: str
    label: str
    universe_tiers: tuple[str, ...]  # 对应 stock_universe.yaml 的 tier key


@dataclass(frozen=True)
class Theme:
    key: str
    label: str
    signal_model: str
    maturity: str
    objective: str
    industries: tuple[ThemeIndustry, ...]
    etf_keywords: tuple[str, ...]
    tiers: tuple[ThemeTier, ...] = ()

    def industry_codes(self) -> list[str]:
        return [ind.code for ind in self.industries]

    def tier_keys(self) -> list[str]:
        return [t.key for t in self.tiers]

    def tier(self, tier_key: str) -> ThemeTier | None:
        for t in self.tiers:
            if t.key == tier_key:
                return t
        return None


@dataclass(frozen=True)
class Bucket:
    key: str
    label: str
    objective: str
    order: int
    themes: tuple[Theme, ...]

    def theme(self, theme_key: str) -> Theme | None:
        for th in self.themes:
            if th.key == theme_key:
                return th
        return None


_cfg: dict[str, Any] | None = None


def themes_config_path() -> Path:
    return config_dir() / "themes_two_directions.yaml"


def load_themes_config(path: Path | None = None) -> dict[str, Any]:
    """加载 themes_two_directions.yaml 原始 dict（内存缓存）。"""
    global _cfg
    if _cfg is not None and path is None:
        return _cfg
    p = path or themes_config_path()
    if not p.exists():
        return {"buckets": {}}
    with open(p, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    if path is None:
        _cfg = cfg
    return cfg


def load_buckets(path: Path | None = None) -> list[Bucket]:
    """按 order 排序的 bucket 列表（含各 bucket 下的 themes）。"""
    cfg = load_themes_config(path)
    out: list[Bucket] = []
    for bkey, bcfg in (cfg.get("buckets") or {}).items():
        themes: list[Theme] = []
        for tkey, tcfg in (bcfg.get("themes") or {}).items():
            industries = tuple(
                ThemeIndustry(
                    code=str(ind.get("code", "")).strip(),
                    name=str(ind.get("name", "")).strip(),
                    relevance=str(ind.get("relevance", "related")).strip().lower(),
                )
                for ind in (tcfg.get("industries") or [])
                if str(ind.get("code", "")).strip()
            )
            themes.append(Theme(
                key=str(tkey),
                label=str(tcfg.get("label", tkey)),
                signal_model=str(tcfg.get("signal_model", "equity_theme")),
                maturity=str(tcfg.get("maturity", "LIVE")).upper(),
                objective=str(tcfg.get("objective", "")),
                industries=industries,
                etf_keywords=tuple(str(k).lower() for k in (tcfg.get("etf_keywords") or [])),
                tiers=tuple(
                    ThemeTier(
                        key=str(tk),
                        label=str(tv.get("label", tk)),
                        universe_tiers=tuple(str(u) for u in (tv.get("universe_tiers") or [])),
                    )
                    for tk, tv in (tcfg.get("tiers") or {}).items()
                ),
            ))
        out.append(Bucket(
            key=str(bkey),
            label=str(bcfg.get("label", bkey)),
            objective=str(bcfg.get("objective", "")),
            order=int(bcfg.get("order", 0)),
            themes=tuple(themes),
        ))
    out.sort(key=lambda b: b.order)
    return out


def load_themes(path: Path | None = None) -> dict[str, Theme]:
    """theme_key → Theme（跨全部 bucket 扁平化）。"""
    return {th.key: th for b in load_buckets(path) for th in b.themes}


def load_buckets_map(path: Path | None = None) -> dict[str, Bucket]:
    return {b.key: b for b in load_buckets(path)}


def industry_to_theme(path: Path | None = None) -> dict[str, str]:
    """SW 二级行业代码 → theme key。"""
    return {ind.code: th.key for th in load_themes(path).values() for ind in th.industries}


def industry_to_bucket(path: Path | None = None) -> dict[str, str]:
    """SW 二级行业代码 → bucket key。"""
    out: dict[str, str] = {}
    for b in load_buckets(path):
        for th in b.themes:
            for ind in th.industries:
                out[ind.code] = b.key
    return out


def match_theme(fund_name: str, buckets: list[Bucket] | None = None) -> str | None:
    """按 ETF 名称关键词匹配首个 theme（bucket order → theme 顺序优先）。"""
    if not fund_name:
        return None
    n = str(fund_name).lower()
    for b in buckets if buckets is not None else load_buckets():
        for th in b.themes:
            for kw in th.etf_keywords:
                if kw and kw in n:
                    return th.key
    return None


def theme_label(theme_key: str, buckets: list[Bucket] | None = None) -> str:
    th = (buckets is not None and next((th for b in buckets for th in b.themes if th.key == theme_key), None)) \
        or load_themes().get(theme_key)
    return th.label if th else theme_key


def theme_bucket_map(path: Path | None = None) -> dict[str, tuple[str, str]]:
    """theme_key → (bucket_key, bucket_label)。供 asset pool 等按主题反查 bucket。"""
    return {
        th.key: (b.key, b.label)
        for b in load_buckets(path)
        for th in b.themes
    }


def bucket_label(bucket_key: str, buckets: list[Bucket] | None = None) -> str:
    b = next((b for b in (buckets if buckets is not None else load_buckets()) if b.key == bucket_key), None)
    return b.label if b else bucket_key
