"""
ETF 资产类别与风险暴露分类

职责：
  - 对 ETF 进行资产类别（primary_asset_class）和资产桶（primary_bucket）分类
  - 生成 exposure_tags 多标签
  - 区分 primary_asset_class：股票、债券、商品、现金、海外
  - 区分 primary_bucket：行业、宽基、红利、黄金、国债等
  - 支持多标签（exposure_tags）：AI、央企、高股息、科技成长等

P0-B 交付物
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

# 关键词 → primary_bucket + exposure_tags
KEYWORD_RULES: list[dict[str, Any]] = [
    # 货币
    {"keywords": ["货币", "短融", "现金"], "bucket": "money_market", "tags": ["货币"]},
    # 债券
    {"keywords": ["国债", "国开", "利率债", "政金债"], "bucket": "bond_treasury", "tags": ["利率债"]},
    {"keywords": ["公司债", "城投债", "科创债", "信用债"], "bucket": "bond_credit", "tags": ["信用债"]},
    {"keywords": ["可转债", "转债"], "bucket": "bond_convertible", "tags": ["可转债"]},
    # 商品（需含明确商品标识，避免与行业混淆）
    {"keywords": ["黄金"], "bucket": "commodity_gold", "tags": ["黄金"]},
    {"keywords": ["豆粕", "能源化工", "商品期货"], "bucket": "commodity_futures", "tags": ["商品期货"]},
    # 海外
    {"keywords": ["恒生", "h股", "港股", "纳斯达克", "标普", "日经", "德国", "亚太", "海外"],
     "bucket": "overseas_equity", "tags": ["海外"]},
    # 宽基
    {"keywords": ["沪深300", "中证500", "中证1000", "创业板", "科创50", "上证50"],
     "bucket": "broad_market", "tags": ["宽基"]},
    # 行业（按关键词匹配）
    {"keywords": ["银行", "证券", "保险", "非银"], "bucket": "industry", "tags": ["金融"]},
    {"keywords": ["医药", "医疗", "生物"], "bucket": "industry", "tags": ["医药"]},
    {"keywords": ["芯片", "半导体", "电子"], "bucket": "industry", "tags": ["科技", "半导体"]},
    {"keywords": ["通信", "5g", "计算机", "软件", "人工智能", "ai"], "bucket": "industry", "tags": ["科技", "TMT"]},
    {"keywords": ["新能源", "光伏", "风电", "电池", "新能车", "汽车"], "bucket": "industry", "tags": ["新能源"]},
    {"keywords": ["军工", "国防", "航天"], "bucket": "industry", "tags": ["军工"]},
    {"keywords": ["有色", "钢铁", "煤炭", "化工", "建材"], "bucket": "industry", "tags": ["周期"]},
    {"keywords": ["消费", "食品", "饮料", "白酒", "家电"], "bucket": "industry", "tags": ["消费"]},
    {"keywords": ["地产", "基建", "建筑"], "bucket": "industry", "tags": ["地产基建"]},
    # 策略/风格
    {"keywords": ["红利", "股息", "价值", "成长", "质量", "低波", "自由现金流"],
     "bucket": "factor_style", "tags": ["策略"]},
    # 主题兜底
    {"keywords": [], "bucket": "theme", "tags": ["主题"]},
]


def load_config(config_dir: Path) -> dict[str, Any]:
    path = config_dir / "etf_buckets.yaml"
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def classify_etf(fund_name: str, tracking_index: str = "") -> dict[str, Any]:
    """对单只 ETF 执行分类。

    Returns:
        {
            primary_asset_class: str,
            primary_bucket: str,
            exposure_tags: list[str],
            asset_bucket: str,        # 向下兼容
            exposure_type: str,       # 向下兼容
            exposure_name: str,       # 向下兼容
            market_scope: str,
        }
    """
    name = fund_name.lower()
    result: dict[str, Any] = {
        "primary_asset_class": "",
        "primary_bucket": "",
        "exposure_tags": [],
        "asset_bucket": "",
        "exposure_type": "",
        "exposure_name": "",
        "market_scope": "a_share",
    }

    for rule in KEYWORD_RULES:
        if any(kw in name for kw in rule["keywords"]):
            bucket = rule["bucket"]
            bucket_def = BUCKET_DEFINITIONS.get(bucket, {})
            result["primary_asset_class"] = bucket_def.get("asset_class", "")
            result["primary_bucket"] = bucket
            result["exposure_tags"] = rule["tags"]
            result["exposure_type"] = bucket_def.get("label", "")
            result["exposure_name"] = rule["tags"][0] if rule["tags"] else fund_name
            return result

    return result


def classify_all(master: pd.DataFrame, config: dict | None = None) -> pd.DataFrame:
    """对 Master 中所有未分类的 ETF 执行自动分类。"""
    if master.empty:
        return master

    df = master.copy()
    for idx, row in df.iterrows():
        if row.get("primary_bucket"):
            continue
        cls = classify_etf(row.get("fund_name", ""), row.get("tracking_index", ""))
        for k, v in cls.items():
            df.at[idx, k] = v if not isinstance(v, list) else str(v)

    return df
