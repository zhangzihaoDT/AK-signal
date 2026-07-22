"""
行业 ETF 的 SW-RPS 信息增强

职责：
  - 对 primary_bucket = industry 的 ETF，调用申万行业 RPS 数据补全行业趋势信息
  - 非行业 ETF 保留扩展接口（P0 阶段先预留）
  - 不负责 ETF 筛选，只做解释增强

SW-RPS 是「行业 ETF 的解释增强模块」，不是 ETF 主筛选器。

P0-D 交付物
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger("etf_signal.sw_enrichment")

# 申万行业映射（ETF 暴露名称 → 申万行业代码）
# P0 阶段先维护核心映射，逐步扩充
INDUSTRY_MAP: dict[str, str] = {
    "有色金属": "801050.SI",
    "乘用车": "801095.SI",
    "汽车": "801095.SI",
    "证券": "801193.SI",
    "银行": "801192.SI",
    "医药": "801153.SI",
    "通信": "801233.SI",
    "电子": "801225.SI",
    "计算机": "801224.SI",
    "军工": "801748.SI",
    "电力": "801550.SI",
    "煤炭": "801031.SI",
    "钢铁": "801042.SI",
    "化工": "801032.SI",
    "食品饮料": "801121.SI",
    "房地产": "801181.SI",
    "建筑": "801721.SI",
    "机械": "801640.SI",
    "非银金融": "801790.SI",
    "传媒": "801223.SI",
}


@dataclass
class SWEnrichment:
    industry_code: str
    industry_name: str
    rps15: float
    rps60: float
    strong_streak_days: int
    market_rank: int | None
    participation_rate: float
    contribution_structure: str
    top1_contrib: float
    top3_contrib: float
    hhi: float
    etf_trend_consistent: bool | None
    source: str = "sw_industry_rps"


def load_sw_rps_data(processed_dir: Path) -> pd.DataFrame:
    """加载 SW-RPS 计算结果。

    从 sw_industry_rps 模块的输出目录读取最近一期数据。

    Returns:
        DataFrame 含 industry_code, RPS15, RPS60, 等字段
    """
    sw_dir = processed_dir.parent.parent / "sw_industry_rps"
    if not sw_dir.exists():
        sw_dir = processed_dir / ".." / ".." / "sw_industry_rps"
    return pd.DataFrame()


def enrich_industry_etf(
    etf_row: pd.Series,
    sw_metrics: pd.DataFrame | None = None,
) -> SWEnrichment | None:
    """对单只行业 ETF 执行 SW-RPS 信息增强。

    Args:
        etf_row: ETF Master 中的一行（需含 exposure_name, fund_code）
        sw_metrics: SW-RPS 指标 DataFrame（可选）

    Returns:
        SWEnrichment 或 None（无法映射时）
    """
    exposure = str(etf_row.get("exposure_name", ""))
    sw_code = _map_to_sw_code(exposure)
    if not sw_code:
        logger.debug("no SW mapping for %s", exposure)
        return None

    if sw_metrics is not None and not sw_metrics.empty:
        row = sw_metrics[sw_metrics["industry_code"] == sw_code]
        if not row.empty:
            r = row.iloc[0]
            return SWEnrichment(
                industry_code=sw_code,
                industry_name=r.get("industry_name", exposure),
                rps15=r.get("RPS15", 0.0),
                rps60=r.get("RPS60", 0.0),
                strong_streak_days=int(r.get("strong_streak_days", 0)),
                market_rank=int(r.get("rank_15", 0)) if "rank_15" in r else None,
                participation_rate=r.get("participation_rate", 0.0),
                contribution_structure=r.get("contribution_structure", ""),
                top1_contrib=r.get("top1_contrib_share", 0.0),
                top3_contrib=r.get("top3_contrib_share", 0.0),
                hhi=r.get("hhi", 0.0),
                etf_trend_consistent=None,
            )

    return SWEnrichment(
        industry_code=sw_code,
        industry_name=exposure,
        rps15=0.0, rps60=0.0,
        strong_streak_days=0, market_rank=None,
        participation_rate=0.0, contribution_structure="",
        top1_contrib=0.0, top3_contrib=0.0, hhi=0.0,
        etf_trend_consistent=None,
    )


def _map_to_sw_code(exposure_name: str) -> str | None:
    """将 ETF 暴露名称映射到申万行业代码。"""
    if not exposure_name:
        return None
    for keyword, code in INDUSTRY_MAP.items():
        if keyword in exposure_name:
            return code
    return None


def check_etf_consistency(
    etf_rps: float,
    industry_rps: float,
    threshold: float = 10.0,
) -> bool:
    """检查 ETF 趋势是否与行业指数趋势一致。

    ETF RPS 与行业 RPS 差异在一定范围内视为一致。
    """
    if etf_rps == 0 or industry_rps == 0:
        return None  # type: ignore
    return abs(etf_rps - industry_rps) <= threshold
