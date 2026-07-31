"""
ETF Master 数据管理

职责：
  - ETF Master 的本地持久化（Parquet）
  - 加载、保存、查询
  - 核心 ETF Universe 筛选（阶段 A：最小可运行历史池）
  - Master 与 AKShare 原始数据的字段映射与清洗

P0-A 交付物
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger("etf_signal.master")

# 资产桶优先级（用于核心 Universe 排序，数值越小优先级越高）
BUCKET_PRIORITY: dict[str, int] = {
    "broad_market": 1,
    "industry": 2,
    "factor_style": 3,
    "commodity_gold": 4,
    "bond_treasury": 5,
    "bond_credit": 6,
    "bond_convertible": 7,
    "theme": 8,
    "overseas_equity": 9,
    "commodity_futures": 10,
    "money_market": 11,
}

# 核心 Universe 默认参数
CORE_UNIVERSE_CONFIG = {
    "min_amount": 1_000_000,        # 20 日均成交额 >= 100 万
    "min_fund_size": 0,             # 阶段 A 暂不设规模门控，后续收紧
    "max_count": 500,
    "min_count": 200,
}


def load_master(master_dir: Path) -> pd.DataFrame:
    """从本地加载 ETF Master。"""
    path = master_dir / "etf_master.parquet"
    if path.exists():
        return pd.read_parquet(path)
    logger.info("no local master found at %s", path)
    return pd.DataFrame()


def save_master(df: pd.DataFrame, master_dir: Path) -> Path:
    """保存 ETF Master 到本地。"""
    master_dir.mkdir(parents=True, exist_ok=True)
    path = master_dir / "etf_master.parquet"
    df.to_parquet(path, index=False)
    logger.info("master saved: %d ETFs -> %s", len(df), path)
    return path


def load_core_universe(master_dir: Path) -> pd.DataFrame:
    """加载核心 ETF Universe。"""
    path = master_dir / "core_universe.parquet"
    if path.exists():
        return pd.read_parquet(path)
    logger.info("no core universe at %s", path)
    return pd.DataFrame()


def save_core_universe(df: pd.DataFrame, master_dir: Path) -> Path:
    """保存核心 ETF Universe。"""
    master_dir.mkdir(parents=True, exist_ok=True)
    path = master_dir / "core_universe.parquet"
    df.to_parquet(path, index=False)
    logger.info("core universe saved: %d ETFs -> %s", len(df), path)
    return path


# ── 核心 Universe 筛选 ──────────────────────────────────────────

def build_core_universe(
    master: pd.DataFrame,
    whitelist_path: Path | None = None,
    config: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """从全量 Master 中筛选核心 ETF Universe（阶段 A）。

    策略：
    1. 首批纳入：同时满足成交额 + 规模门控的 ETF
    2. 补足：若首批不足 min_count，放宽门控补足到 min_count
    3. 排序：按 (优先级, 成交额, 规模) 降序
    4. 截断：最多保留 max_count 只

    注：国金可交易采用黑名单机制（默认全部可交易），
        故核心 Universe 构建不再依赖国金白名单加分。

    Args:
        master: 全量 ETF Master（需含 amount, fund_size, primary_bucket 等字段）
        whitelist_path: 已废弃（保留参数以兼容旧调用）
        config: 配置参数

    Returns:
        核心 Universe DataFrame
    """
    cfg = {**CORE_UNIVERSE_CONFIG, **(config or {})}
    min_amount = cfg["min_amount"]
    min_fund_size = cfg["min_fund_size"]
    max_count = cfg["max_count"]
    min_count = cfg["min_count"]

    if master.empty:
        return pd.DataFrame()

    df = master.copy()

    # 填充 primary_bucket
    if "primary_bucket" not in df.columns:
        df["primary_bucket"] = df.get("asset_bucket", "")

    # 构建排序分数
    df["_bucket_rank"] = df["primary_bucket"].map(BUCKET_PRIORITY).fillna(20)

    amount_col = df.get("amount", pd.Series(0, index=df.index))
    df["_amount"] = amount_col.fillna(0).astype(float)

    size_col = df.get("fund_size", pd.Series(0, index=df.index))
    df["_fund_size"] = size_col.fillna(0).astype(float)

    # 首批：同时满足金额 + 规模门控
    passed_amount = df["_amount"] >= min_amount
    passed_size = df["_fund_size"] >= min_fund_size
    tier1 = df[passed_amount & passed_size].copy()

    # 补足数量
    if len(tier1) < min_count:
        logger.info("tier1 only %d ETFs, relaxing gates to reach %d", len(tier1), min_count)
        need = min_count - len(tier1)
        tier2 = df[~(passed_amount & passed_size)].copy()
        tier2 = tier2.sort_values(
            ["_bucket_rank", "_amount", "_fund_size"],
            ascending=[True, False, False],
        ).head(need)
        candidates = pd.concat([tier1, tier2], ignore_index=True)
    else:
        candidates = tier1

    # 排序并截断
    candidates = candidates.sort_values(
        ["_bucket_rank", "_amount", "_fund_size"],
        ascending=[True, False, False],
    )
    core = candidates.head(max_count).reset_index(drop=True)

    # 清理辅助列
    drop_cols = [c for c in core.columns if c.startswith("_")]
    core = core.drop(columns=drop_cols)

    # 分桶统计
    if "primary_bucket" in core.columns:
        for bucket, count in core["primary_bucket"].value_counts().items():
            logger.info("  %s: %d", bucket, count)

    logger.info(
        "core universe: %d ETFs (from %d total, %s amount, %s size)",
        len(core), len(master),
        f"≥{min_amount/1e6:.0f}M" if min_amount else "no threshold",
        f"≥{min_fund_size/1e8:.0f}亿" if min_fund_size else "no threshold",
    )
    return core


def build_master_from_akshare(raw: pd.DataFrame) -> pd.DataFrame:
    """将 AKShare 原始数据映射为标准 ETF Master 格式。

    Args:
        raw: AKShare fund_etf_spot_em 返回的原始 DataFrame

    Returns:
        标准化的 etf_master DataFrame，字段见 docs
    """
    if raw.empty:
        return pd.DataFrame()

    cols_lower = {c.lower(): c for c in raw.columns}

    def _col(*keys: str) -> str | None:
        for k in keys:
            if k in cols_lower:
                return cols_lower[k]
            for c in cols_lower:
                if k in c:
                    return cols_lower[c]
        return None

    code_col = _col("代码")
    name_col = _col("名称")
    amount_col = _col("成交额")
    scale_col = _col("基金规模")

    rows: list[dict[str, Any]] = []
    for _, row in raw.iterrows():
        fund_code = str(row.get(code_col, "")) if code_col else ""
        fund_name = str(row.get(name_col, "")) if name_col else ""
        if not fund_code or not fund_name:
            continue
        exchange = "SSE" if fund_code.startswith(("51", "56")) else "SZSE"

        rows.append({
            "fund_code": fund_code,
            "fund_name": fund_name,
            "exchange": exchange,
            "amount": _safe_float(row, amount_col),
            "fund_size": _safe_float(row, scale_col),
            "asset_bucket": "",
            "exposure_type": "",
            "exposure_name": "",
            "market_scope": "",
            "tracking_index": "",
            "tracking_index_code": "",
            "primary_asset_class": "",
            "primary_bucket": "",
            "exposure_tags": "",
            "listing_date": None,
            "is_qdii": False,
            "is_active": True,
            "guojin_tradable": False,
        })

    return pd.DataFrame(rows)


def _safe_float(row: pd.Series, col: str | None) -> float | None:
    if col is None or col not in row:
        return None
    try:
        v = row[col]
        if pd.isna(v):
            return None
        return float(v)
    except (ValueError, TypeError):
        return None
