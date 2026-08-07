"""
Layer ② 行业内部结构产物（sw_industry_structure_{date}.parquet）— v0.8.0

定位：行业内部结构 Observation 事实（「行业内部由谁推动」）。
范围：趋势行业 ∪ 主题焦点行业，独立于 confirmation parquet 落盘，避免把
      非焦点行业混入「主题确认事实」，杜绝事实来源分叉。

三类事实边界（Layer② 数据模型）：
  - metrics（industry_daily_metrics / latest_snapshot）  : 全 124 二级行业趋势（Observation）
  - structure（本模块）                                  : 趋势 ∪ 焦点 的内部结构（Observation）
  - confirmation                                         : 仅主题焦点行业的主题确认事实（Decision 消费）

structure_status 区分四态，可审计、不静默空值：
  available    计算成功（contribution_structure ∈ 有效集合）
  insufficient 已计算但数据不足（重构质量差 / Top1 未获取 / 无成分股行情）
  failed       计算失败（无成分股 / 无行业历史 / 无焦点归属）
  not_in_scope 不在范围（趋势 ∪ 焦点 之外，仅在全量对照时出现）
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import pandas as pd

from src.common import themes as themes_cfg

logger = logging.getLogger("sw_industry_rps.structure")

STRUCTURE_SCOPE: list[str] = []  # 在 compute_structure_scope 填充
_TREND_STATES = {"强势", "观察"}


def focus_codes() -> list[str]:
    """主题焦点行业代码（从 themes_two_directions.yaml 推导）。"""
    return [ind.code for b in themes_cfg.load_buckets() for th in b.themes for ind in th.industries]


def compute_structure_scope(
    snapshot: pd.DataFrame,
    trend_states: set[str] | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """计算 structure 范围。

    返回 (scope_codes, trend_codes, focus_codes)：
      - trend_codes: strength_level ∈ {强势, 观察} 的二级行业
      - focus_codes: 主题焦点行业
      - scope_codes: trend ∪ focus（去重，保序）
    """
    if trend_states is None:
        trend_states = _TREND_STATES
    focus = focus_codes()
    if snapshot is None or snapshot.empty:
        return sorted(set(focus)), [], focus
    if "strength_level" in snapshot.columns:
        trend = snapshot[
            snapshot["strength_level"].astype(str).isin(list(trend_states))
        ]["industry_code"].tolist()
    else:
        trend = []
    scope = list(dict.fromkeys(trend + focus))
    return scope, trend, focus


def _structure_status_from_drilldown(dd: Any) -> tuple[str, str]:
    """把 DrilldownResult 映射为 structure_status + driver_mode。

    Returns (status, driver_mode_text)。
    """
    cs = getattr(dd, "contribution_structure", "")
    rq = getattr(dd, "reconstruction_quality", "")
    if not cs or cs == "数据不足":
        return "insufficient", ""
    if str(rq).startswith("poor") or cs == "数据不足":
        return "insufficient", ""
    return "available", cs


def _format_drive_text(cs: str, bs: str) -> str:
    from src.sw_industry_rps.confirmation import _format_drive
    return _format_drive(cs, bs)


def build_structure_parquet(
    snapshot: pd.DataFrame,
    raw_dir: Path,
    processed_dir: Path,
    date_str: str,
    window: int = 10,
    sleep_between: float = 4.0,
    offline: bool = True,
    allow_online: bool = False,
) -> pd.DataFrame:
    """生成 sw_industry_structure_{date_str}.parquet。

    Args:
        snapshot: latest_date 的 metrics 横截面（含 industry_code/name/parent/RPS 等）
        raw_dir: 行业原始数据目录（读行业历史）
        processed_dir: 产物输出目录
        date_str: 信号日期 YYYYMMDD（= trade_date）
        window: 结构穿透回看窗口
        sleep_between: 行业间抓取间隔（秒），offline=True 且全部命中缓存时仍保留小间隔
        offline: 仅读缓存（禁联网），命中不了则记 failed
        allow_online: 允许在线补数（仅当 offline=False 生效）

    Returns:
        structure_df: 产物 DataFrame（每 scope 行业一行）
    """
    from src.sw_industry_rps import contribution as sw_contribution
    from src.sw_industry_rps import constituents as sw_constituents
    from src.sw_industry_rps import storage

    scope, trend, focus = compute_structure_scope(snapshot)
    if not scope:
        logger.warning("structure scope empty — nothing to compute")
        return pd.DataFrame()

    name_map = {}
    parent_map = {}
    if not snapshot.empty and "industry_code" in snapshot.columns:
        name_map = dict(zip(snapshot["industry_code"], snapshot["industry_name"]))
        if "parent_industry" in snapshot.columns:
            parent_map = dict(zip(snapshot["industry_code"], snapshot["parent_industry"]))

    rows: list[dict[str, Any]] = []
    for idx, code in enumerate(scope):
        row_metrics = snapshot[snapshot["industry_code"] == code]
        if not row_metrics.empty:
            m = row_metrics.iloc[0]
            strength = m.get("strength_level", "")
            rotation = m.get("rotation_state", "")
        else:
            strength, rotation = "", ""
        name = name_map.get(code, "")
        parent = parent_map.get(code, "")

        record: dict[str, Any] = {
            "trade_date": pd.Timestamp(date_str),
            "industry_code": code,
            "industry_name": name,
            "parent_industry": parent,
            "strength_level": strength,
            "rotation_state": rotation,
            "structure_status": "",
            "driver_mode": "",
            "contribution_structure": "",
            "breadth_structure": "",
            "participation_rate": None,
            "hhi": None,
            "top1_share": None,
            "top3_share": None,
            "reconstruction_quality": "",
            "weight_coverage": None,
            "count_coverage": None,
            "median_stock_return": None,
            "equal_weight_return": None,
            "top_contributors": "",
        }

        ind_hist = storage.load_industry_raw(raw_dir, code)
        if ind_hist.empty:
            record["structure_status"] = "failed"
            record["driver_mode"] = "无行业历史"
            rows.append(record)
            continue

        const_df = sw_constituents.fetch_constituent_list_cached(code, raw_dir)
        if const_df.empty:
            record["structure_status"] = "failed"
            record["driver_mode"] = "无成分股"
            rows.append(record)
            continue

        if idx > 0 and sleep_between > 0:
            time.sleep(sleep_between)

        try:
            dd = sw_contribution.compute_drilldown(
                industry_code=code,
                industry_name=name,
                breakout_date=date_str,
                constituents=const_df,
                industry_hist=ind_hist,
                window=window,
                offline=offline,
            )
        except Exception as e:
            logger.warning("drilldown failed for %s: %s", code, e)
            record["structure_status"] = "failed"
            record["driver_mode"] = "计算异常"
            rows.append(record)
            continue

        status, _ = _structure_status_from_drilldown(dd)
        record["structure_status"] = status
        record["contribution_structure"] = dd.contribution_structure
        record["breadth_structure"] = dd.breadth_structure
        record["driver_mode"] = _format_drive_text(dd.contribution_structure, dd.breadth_structure)
        record["participation_rate"] = dd.participation_rate
        record["hhi"] = dd.hhi
        record["top1_share"] = dd.top1_share
        record["top3_share"] = dd.top3_share
        record["reconstruction_quality"] = dd.reconstruction_quality
        record["weight_coverage"] = dd.weight_coverage
        record["count_coverage"] = dd.count_coverage
        if dd.top_contributors:
            record["top_contributors"] = ";".join(
                f"{c.stock_name}:{c.contribution_pct}" for c in dd.top_contributors[:5]
            )
            rets = [c.stock_return_pct for c in dd.top_contributors if c.stock_return_pct is not None]
            if rets:
                record["median_stock_return"] = round(float(pd.Series(rets).median()), 2)
                record["equal_weight_return"] = round(float(sum(rets) / len(rets)), 2)
        rows.append(record)

    df = pd.DataFrame(rows)
    if not df.empty:
        df["structure_scope"] = df["industry_code"].apply(
            lambda c: "trend" if c in trend else ("focus" if c in focus else "both")
        )
        df = df.sort_values("structure_status").reset_index(drop=True)

    processed_dir.mkdir(parents=True, exist_ok=True)
    out_path = processed_dir / f"sw_industry_structure_{date_str}.parquet"
    df.to_parquet(out_path, index=False)
    logger.info("structure saved: %d industries (%s) -> %s", len(df), date_str, out_path)
    return df


def load_structure(processed_dir: Path, date_str: str) -> pd.DataFrame:
    path = processed_dir / f"sw_industry_structure_{date_str}.parquet"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)
