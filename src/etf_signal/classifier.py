"""
ETF 投资市场 / 投资方向两个平行维度，及既有计算桶的兼容映射。
配置真源：config/etf_classification.yaml；名称推断不等于成分股核验。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

logger = logging.getLogger("etf_signal.classifier")

# 一级资产类
ASSET_CLASSES = {
    "equity": "股票",
    "bond": "债券",
    "commodity": "商品",
    "cash": "现金",
    "overseas": "海外",
    "multi_asset": "跨资产",
}

# 二级资产桶（primary_bucket）
BUCKET_DEFINITIONS: dict[str, dict[str, Any]] = {
    "broad_market": {"asset_class": "equity", "label": "宽基"},
    "industry": {"asset_class": "equity", "label": "行业"},
    "theme": {"asset_class": "equity", "label": "主题"},
    "factor_style": {"asset_class": "equity", "label": "策略/风格"},
    "bond_treasury": {"asset_class": "bond", "label": "利率债"},
    "bond_credit": {"asset_class": "bond", "label": "信用债"},
    "bond_convertible": {"asset_class": "bond", "label": "可转债"},
    "commodity_gold": {"asset_class": "commodity", "label": "黄金"},
    "commodity_futures": {"asset_class": "commodity", "label": "商品期货"},
    "money_market": {"asset_class": "cash", "label": "货币"},
    "overseas_equity": {"asset_class": "overseas", "label": "海外权益"},
    "overseas_bond": {"asset_class": "overseas", "label": "海外债券"},
}

# 这两个字段是产品分类；旧桶只用于兼容既有计算消费者。
CLASSIFICATION_COLUMNS = (
    "investment_market", "investment_direction", "market_classification_status",
    "direction_classification_status", "classification_version", "classification_basis",
    "market_classification_evidence", "direction_classification_evidence",
)


def load_config(config_dir: Path | None = None) -> dict[str, Any]:
    if config_dir is None:
        from src.common.paths import config_dir as default_config_dir
        config_dir = default_config_dir()
    with (config_dir / "etf_classification.yaml").open(encoding="utf-8") as handle:
        cfg = yaml.safe_load(handle)
    for key in ("version", "investment_markets", "investment_directions", "market_rules", "direction_rules"):
        if not cfg.get(key):
            raise ValueError(f"ETF classification config missing {key}")
    for rules, values in (("market_rules", "investment_markets"),
                          ("direction_rules", "investment_directions")):
        for rule in cfg[rules]:
            if rule["value"] not in cfg[values] or not rule.get("keywords"):
                raise ValueError(f"Invalid ETF classification rule: {rule}")
    return cfg


def _text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in ("nan", "none", "<na>") else text


def _match(text: str, rules: list[dict]) -> tuple[str, str]:
    normalized = text.casefold().replace(" ", "")
    for rule in rules:
        for keyword in rule["keywords"]:
            if rule["value"] == "货币" and "现金流" in normalized:
                continue
            if keyword == "能源ETF" and "新能源" in normalized:
                continue
            if keyword == "国企" and any(k in normalized for k in ("恒生国企", "港股国企")):
                continue
            if keyword.casefold() in normalized:
                return rule["value"], keyword
    return "待核实", ""


def classify_etf(fund_name: str, tracking_index: str = "", *, config: dict | None = None) -> dict[str, Any]:
    """独立识别投资市场和方向；指数名称优先，简称仅作未核验推断。

    不读取/改写价格，不用代码前缀推断市场，不把名称推断认证为真实持仓。
    """
    cfg = config if config is not None else load_config()
    name, index = _text(fund_name), _text(tracking_index)
    # 指数名称存在时不拼接可能冲突的基金简称。
    evidence = index or name
    market, market_keyword = _match(evidence, cfg["market_rules"])
    direction, direction_keyword = _match(evidence, cfg["direction_rules"])
    bucket = ""
    if direction == "货币":
        bucket = "money_market"
    elif direction == "固收":
        if any(k in evidence for k in ("可转债", "转债")):
            bucket = "bond_convertible"
        elif any(k in evidence for k in ("国债", "国开", "利率债", "政金债", "地方债")):
            bucket = "bond_treasury"
        else:
            bucket = "bond_credit"
    elif direction == "商品":
        bucket = "commodity_gold" if any(k in evidence for k in ("黄金", "上海金")) else "commodity_futures"
    elif direction != "待核实":
        if market in ("港股", "美国", "其他海外"):
            bucket = "overseas_equity"
        elif direction == "宽基":
            bucket = "broad_market"
        elif direction == "跨行业策略":
            bucket = "factor_style"
        else:
            bucket = "industry"
    # 市场明确但方向不明：保留权益身份，方向仍待核实。
    elif market in ("港股", "美国", "其他海外", "跨市场"):
        bucket = "overseas_equity" if market != "跨市场" else "theme"
    definition = BUCKET_DEFINITIONS.get(bucket, {})
    return {
        "primary_asset_class": definition.get("asset_class", ""),
        "primary_bucket": bucket, "asset_bucket": bucket,
        "exposure_tags": [] if direction == "待核实" else [direction],
        "exposure_type": definition.get("label", ""),
        "exposure_name": "" if direction == "待核实" else direction,
        "market_scope": {"境内": "a_share", "港股": "hk", "美国": "us",
                         "其他海外": "other_overseas", "跨市场": "cross_market"}.get(market, ""),
        "investment_market": market, "investment_direction": direction,
        "market_classification_status": "unverified" if market_keyword else "pending",
        "direction_classification_status": "unverified" if direction_keyword else "pending",
        "classification_version": cfg["version"],
        "classification_basis": "tracking_index_name" if index else ("fund_name" if name else "missing"),
        "market_classification_evidence": market_keyword,
        "direction_classification_evidence": direction_keyword,
    }


def classify_all(master: pd.DataFrame, config: dict | None = None) -> pd.DataFrame:
    """重新识别所有行（包括旧货币误分），不改变输入或持久化原始 Master。

    无新识别依据时只保留旧计算桶，并明确两项产品分类待核实。
    """
    if master.empty:
        return master.copy()
    cfg = config if config is not None else load_config()
    rows = []
    for _, row in master.iterrows():
        result = row.to_dict()
        cls = classify_etf(row.get("fund_name"), row.get("tracking_index"), config=cfg)
        if not cls["asset_bucket"]:
            # 兼容无名称的历史计算输入；不得据此补造新分类。
            old_bucket = _text(row.get("asset_bucket")) or _text(row.get("primary_bucket"))
            cls["asset_bucket"] = cls["primary_bucket"] = old_bucket
            cls["primary_asset_class"] = BUCKET_DEFINITIONS.get(old_bucket, {}).get("asset_class", "")
        result.update({k: str(v) if isinstance(v, list) else v for k, v in cls.items()})
        rows.append(result)
    return pd.DataFrame(rows, index=master.index)
