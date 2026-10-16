"""
Layer② 观测来源与完整性语义（V0.1）

把两个正交概念分开，避免此前 `data_status=provisional` 把「用了兜底源」和
「观测不完整」混为一谈：

  - data_status（观测完整性）:
      confirmed  申万官方确认（完整收盘）
      complete   兜底源、但目标交易日已完整收盘
      partial    兜底源、且目标交易日盘中未收盘（真正无需作为完整观测）
  - source_status（数据源）:
      primary    申万官方日线 L1（analysis_daily）/ hist_sw 逐行业
      fallback   realtime 基底 / 同花顺增强兜底

仅做纯分类，不读取磁盘，便于独立回归测试与跨层复用。
"""

from __future__ import annotations

from typing import Iterable

import pandas as pd

# 申万官方主源 tokens（data_source / metrics 里出现的 source 值，小写匹配）
PRIMARY_SOURCES = frozenset({"swsresearch", "swsresearch_analysis", "analysis_daily", "hist_sw"})


def classify_source_status(sources: Iterable[str] | None) -> str:
    """source_status：primary | fallback。

    只要存在任一申万官方主源 token 即判 primary；否则（realtime/同花顺兜底）→ fallback。
    """
    srcs = {str(s).strip().lower() for s in (sources or [])}
    if srcs & PRIMARY_SOURCES:
        return "primary"
    return "fallback"


def classify_data_status(source_status: str, is_complete: bool) -> str:
    """data_status：confirmed / complete / partial。

    主源确认 → confirmed（官方日线必然是完整收盘）。
    兜底源 → 完整收盘交易日 complete；盘中未收盘 → partial。
    """
    if source_status == "primary":
        return "confirmed"
    return "complete" if is_complete else "partial"


def price_source_dates(frame: pd.DataFrame) -> pd.Series:
    """独立价格日期；旧主源日线的 trade_date 本身来自源端日期。

    实时/未知来源不能用本地指定的 trade_date 充当源日期。显式但错误的
    source_trade_date 不回退，避免把日期不一致洗成已验证。
    """
    dates = pd.to_datetime(frame.get("source_trade_date", pd.Series(pd.NaT, index=frame.index)), errors="coerce")
    source = frame.get("source", pd.Series("", index=frame.index)).fillna("").astype(str).str.lower()
    missing = frame.get("source_trade_date", pd.Series(pd.NA, index=frame.index)).isna()
    primary = source.isin(PRIMARY_SOURCES) & missing
    return dates.where(~primary, pd.to_datetime(frame["trade_date"], errors="coerce"))


def verified_price_dates(frame: pd.DataFrame) -> pd.Series:
    """逐行判断价格源日期是否等于分区日期，空值一律不通过。"""
    if "trade_date" not in frame:
        return pd.Series(False, index=frame.index)
    return price_source_dates(frame).eq(pd.to_datetime(frame["trade_date"], errors="coerce"))
