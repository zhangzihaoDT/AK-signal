"""Study 1 Universe：729 只 FULL ETF 锁定 + ETF 类型校准（旁路，不动生产 classifier）。

生产 classifier.py 的 KEYWORD_RULES 存在顺序问题（宽基在行业之前，
导致「创业板新能源」被划为 broad_market）。本研究不修改生产代码，
只在本研究内用校准后的 research-only taxonomy 做异质性分层，并输出审计清单。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from src.common.paths import etf_signal_master_dir, etf_signal_raw_dir

from . import STUDY_DIR

logger = logging.getLogger(__name__)

# ── research-only taxonomy（行业优先于宽基，修正生产 classifier 的顺序缺陷） ──

_INDUSTRY_KEYWORDS = [
    "半导体", "芯片", "电子", "软件", "计算机", "通信", "5g", "云计算", "大数据",
    "人工智能", "ai", "机器人", "数字经济", "信息技术", "互联网", "游戏",
    "新能源", "光伏", "风电", "电池", "储能", "锂电", "新能车", "汽车", "智能驾驶",
    "军工", "国防", "航天", "航空", "船舶",
    "银行", "证券", "券商", "保险", "金融", "非银",
    "医药", "医疗", "生物", "创新药", "疫苗",
    "消费", "食品", "饮料", "白酒", "家电", "农业", "养殖", "畜牧",
    "有色", "稀土", "钢铁", "煤炭", "化工", "石油", "建材", "水泥",
    "地产", "基建", "建筑", "机械", "高端装备", "工程",
    "电力", "电网", "公用事业", "环保", "水务",
    "传媒", "影视", "旅游", "酒店", "零售", "商贸",
    "航运", "物流", "交运", "铁路", "公路", "港口", "机场",
    "地产链", "城镇化", "建筑建材",
]

_BROAD_KEYWORDS = [
    "沪深300", "中证500", "中证1000", "中证2000", "中证800", "中证a500", "a500",
    "中证a50", "a50", "上证50", "上证180", "深证100", "深证成指", "中证100",
    "创业板", "科创50", "科创100", "双创", "中证全指", "中证流通", "msci", "富时中国",
    "沪港深", "中证红利", "中证500增强", "中证1000增强",
]

_DIVIDEND_KEYWORDS = ["红利", "股息", "高股息", "低波红利", "央企股东回报", "红利低波"]

_CROSS_BORDER_KEYWORDS = [
    "港股", "恒生", "h股", "纳指", "纳斯达克", "标普", "道琼斯", "日经", "德国",
    "法国", "欧洲", "亚太", "东南亚", "越南", "印度", "海外", "全球", "中概",
    "美股", "美国", "日本", "亚洲",
]

_BOND_KEYWORDS = ["国债", "国开", "政金", "利率债", "信用债", "城投", "公司债", "可转债", "转债", "短融"]
_COMMODITY_KEYWORDS = ["黄金", "白银", "豆粕", "能源化工", "商品", "原油", "有色矿产", "贵金属"]
_MONEY_KEYWORDS = ["货币", "现金", "短融", "场内货币"]


def calibrate_etf_type(fund_name: str, orig_bucket: str) -> dict[str, Any]:
    """研究专用 ETF 类型校准（不改生产 classifier）。

    优先级：货币 > 债券 > 商品 > 红利 > 跨境 > 行业 > 宽基 > 主题。
    关键：关键词只匹配 ETF 标识之前的片段（如「创业板新能源ETF富国」→ 新能源在 ETF 之前 → 行业；
    「中证500ETF中银证券」→ 证券在 ETF 之后为经理名后缀 → 忽略 → 宽基）。
    返回校准后类型 + 是否与原 bucket 冲突。
    """
    text = str(fund_name).lower()

    def _exposure_segment() -> str:
        # 返回 ETF 标记之前的片段；无 ETF 标记则用全文
        idx = text.find("etf")
        return text if idx < 0 else text[:idx]

    seg = _exposure_segment()

    def _hit(kws: list[str], s: str) -> bool:
        return any(k in s for k in kws)

    if _hit(_MONEY_KEYWORDS, seg):
        t = "money"
    elif _hit(_BOND_KEYWORDS, seg):
        t = "bond"
    elif _hit(_COMMODITY_KEYWORDS, seg):
        t = "commodity"
    elif _hit(_DIVIDEND_KEYWORDS, seg):
        t = "dividend"
    elif _hit(_CROSS_BORDER_KEYWORDS, seg):
        t = "cross_border"
    elif _hit(_INDUSTRY_KEYWORDS, seg):
        t = "industry"
    elif _hit(_BROAD_KEYWORDS, seg):
        t = "broad"
    else:
        t = "theme"

    conflict = False
    if t == "industry" and orig_bucket in ("broad_market", "factor_style", "theme"):
        conflict = True
    if t == "broad" and orig_bucket == "industry":
        conflict = True
    return {"calibrated_type": t, "conflict": conflict}


def load_full_etf_universe() -> pd.DataFrame:
    """锁定 729 只 FULL ETF：raw 历史 ≥ P_WINDOW（756 交易日）。

    以本地 raw 实际行数为准（同一只 ETF 可能同时覆盖两所，按代码唯一）。
    """
    master = pd.read_parquet(etf_signal_master_dir() / "etf_master.parquet")
    raw_dir = etf_signal_raw_dir()
    rows: list[dict[str, Any]] = []
    for path in raw_dir.glob("*.parquet"):
        code = path.stem
        try:
            d = pd.read_parquet(path, columns=["date", "close"])
        except Exception as e:
            logger.warning("unreadable raw %s: %s", code, e)
            continue
        if len(d) < 756:
            continue
        m = master.loc[master["fund_code"].astype(str).str.zfill(6) == code]
        rows.append({
            "fund_code": code,
            "fund_name": str(m["fund_name"].iloc[0]) if len(m) else "",
            "exchange": str(m["exchange"].iloc[0]) if len(m) else "",
            "orig_bucket": str(m["primary_bucket"].iloc[0]) if len(m) else "",
            "listing_date": (pd.Timestamp(m["listing_date"].iloc[0]).date() if len(m) and pd.notna(m["listing_date"].iloc[0]) else None),
            "hist_days": len(d),
            "start_date": d["date"].min().date(),
            "end_date": d["date"].max().date(),
        })
    df = pd.DataFrame(rows).drop_duplicates("fund_code").reset_index(drop=True)
    cal = df["fund_name"].map(lambda n: calibrate_etf_type(n, ""))
    df["etf_type"] = [c["calibrated_type"] for c in cal]
    logger.info("FULL ETF universe: %d (hist>=756)", len(df))
    logger.info("type distribution: %s", df["etf_type"].value_counts().to_dict())
    return df


def taxonomy_audit(universe: pd.DataFrame) -> pd.DataFrame:
    """对比校准类型与原 bucket，标记疑似错分，输出审计清单（不阻塞研究）。"""
    cal = universe["fund_name"].map(lambda n: calibrate_etf_type(n, ""))
    universe = universe.copy()
    universe["calibrated_type"] = [c["calibrated_type"] for c in cal]
    universe["taxonomy_conflict"] = [
        calibrate_etf_type(universe.loc[i, "fund_name"], universe.loc[i, "orig_bucket"])["conflict"]
        for i in universe.index
    ]
    conflict_cols = ["fund_code", "fund_name", "orig_bucket", "calibrated_type", "taxonomy_conflict"]
    conflicts = universe.loc[universe["taxonomy_conflict"], conflict_cols]
    audit = {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "total": int(len(universe)),
        "n_conflicts": int(len(conflicts)),
        "conflict_rate": round(len(conflicts) / len(universe), 4) if len(universe) else 0,
        "examples": conflicts.head(50).to_dict("records"),
    }
    out = STUDY_DIR / "taxonomy_audit.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("taxonomy audit: %d conflicts of %d (%.1f%%)", len(conflicts), len(universe), 100 * len(conflicts) / max(len(universe), 1))
    return universe
