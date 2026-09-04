"""
Final Validation CLI — run-day 末端校验。

职责：
  1. 读取 Layer ① ETF rotation / Layer ② SW confirmation / Layer ③ selection 产物
  2. 汇总各层 trade_date / data_status / action 与 run 警告（outputs/run_warnings_{date}.json）
  3. 输出 run-day 最终结果：
       成功 → "Run completed successfully"（含 trade_date / status / action / warnings）
       失败 → "Run completed with errors"（含 errors 明细），退出码 1

子命令：
  run-day-check   对指定 trade_date（默认各层最新）执行最终校验
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime
from typing import Any

import pandas as pd

from src.common import warnings as run_warnings
from src.common.paths import (
    etf_signal_daily_dir, sw_industry_confirmation_dir, outputs_dir,
)


def build_logger(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("final_validation")
    if logger.handlers:
        return logger
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    return logger


# ---------------------------------------------------------------------------
# 纯判定逻辑（不依赖磁盘路径，便于测试）
# ---------------------------------------------------------------------------

def evaluate(
    *,
    trade_date: str,
    layers: dict[str, Any] | None,
    alignment: dict[str, Any] | None,
    action_level: str | None,
    warnings: list[dict[str, Any]],
    coverage: dict[str, Any] | None = None,
    selection_exists: bool = True,
    rotation_exists: bool = True,
    confirmation_exists: bool = True,
    transition_state_exists: bool = True,
    lane1_lag_days: int | None = None,
) -> dict[str, Any]:
    """汇总各层证据，给出 run-day 校验结论。

    lane1_lag_days：three_lane 的 Lane 1 实际 watchlist 日期相对 trade_date 的滞后天数。
    None / 0 = 对齐（_watchlist_date == trade_date）；>0 = 用了上一交易日 fallback，
    必须显式标记，避免 FIRST_EXIT × Lane 1 交叉分析把「一天时间差」误读成状态先后关系。

    Returns:
        {ok, trade_date, status, action, warnings, errors}
    """
    errors: list[str] = []
    warns: list[str] = []

    if not selection_exists:
        errors.append("selection candidates 缺失（select 未产出 JSON）")
    if not rotation_exists:
        errors.append(f"ETF rotation 缺失（rotation_{trade_date}.parquet）")
    if not confirmation_exists:
        errors.append(f"SW confirmation 缺失（confirmation_{trade_date}.parquet）")
    if not transition_state_exists:
        errors.append(f"Lane 3 transition_state 缺失（trend_transition_state_{trade_date}.parquet）")

    # Lane 1 数据日对齐：要求 _watchlist_date == trade_date；
    # 允许上一交易日 fallback 时，必须显式标记滞后，避免把时间差误读为状态先后
    if isinstance(lane1_lag_days, int) and lane1_lag_days > 0:
        warns.append(f"Lane 1 watchlist 滞后 {lane1_lag_days} 交易日（_watchlist_date < trade_date）"
                     f"——FIRST_EXIT × Lane 1 交叉分析需按日错位解读，勿把滞后当状态先后")

    # 层证据状态（V0.1，来源/完整性分离）：
    #   confirmed = 申万官方主源完全确认 → CONFIRMED
    #   complete  = 兜底源但目标交易日已完整收盘 → COMPLETE（不再降级 PROVISIONAL）
    #   partial   = 兜底源且盘中未收盘 → PARTIAL（不得标 COMPLETE）
    #   legacy provisional（旧产物）→ 视同 complete
    #   etf 与 sw_industry 两者取最保守档；缺失/不一致 → UNKNOWN
    etf_status = ((layers or {}).get("etf") or {}).get("data_status", "")
    sw_status = ((layers or {}).get("sw_industry") or {}).get("data_status", "")
    sw_status_norm = "complete" if sw_status == "provisional" else sw_status
    statuses = [s for s in (etf_status, sw_status_norm) if s]
    if "partial" in statuses:
        status = "PARTIAL"
    elif "complete" in statuses:
        status = "COMPLETE"
        if sw_status_norm == "complete":
            warns.append(
                "Layer②: COMPLETE · FALLBACK_SOURCE —— "
                "Awaiting SW L1 primary-source verification."
            )
    elif statuses and all(s == "confirmed" for s in statuses):
        status = "CONFIRMED"
    elif not statuses:
        status = "UNKNOWN"
        warns.append("layer data_status 缺失（selection meta.layers 不完整）")
    else:
        status = "UNKNOWN"
        warns.append(f"layer data_status 不一致：etf={etf_status or '-'}, sw_industry={sw_status or '-'}")

    # 对齐警告
    align_status = (alignment or {}).get("alignment_status", "")
    if align_status and align_status != "aligned":
        lag = (alignment or {}).get("industry_lag_days")
        lag_txt = f"（industry_lag_days={lag}）" if lag is not None else ""
        warns.append(f"层对齐异常：{align_status}{lag_txt}")

    # 配置降级警告
    if isinstance(layers, dict) and layers.get("degraded"):
        warns.append("配置降级：asset pool 存在未注册 theme，资产未进入候选")

    # Selection 输入覆盖降级
    if coverage:
        degraded = coverage.get("degraded_assets") or []
        if degraded:
            warns.append(f"selection 输入降级：{len(degraded)} 个资产不可用（{'、'.join(degraded[:8])}）")
        pct = coverage.get("selection_coverage_pct")
        if isinstance(pct, (int, float)) and pct < 100:
            warns.append(f"selection 覆盖率 {coverage.get('selection_coverage', '—')}（{pct}%）")

    # run 警告文件
    for w in warnings:
        msg = str(w.get("message", "")).strip()
        if msg:
            warns.append(msg)

    return {
        "ok": not errors,
        "trade_date": trade_date,
        "status": status,
        "action": action_level or "UNKNOWN",
        "warnings": warns,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# 产物加载
# ---------------------------------------------------------------------------

def _latest_selection_date() -> str | None:
    """从 outputs/selection/ 找出最新 tradable_candidates_*.json 的日期。"""
    out_dir = outputs_dir() / "selection"
    if not out_dir.exists():
        return None
    dates: list[str] = []
    for p in out_dir.glob("tradable_candidates_*.json"):
        m = re.search(r"(\d{8})", p.name)
        if m:
            dates.append(m.group(1))
    return max(dates) if dates else None


def _load_selection(trade_date: str) -> dict[str, Any] | None:
    path = outputs_dir() / "selection" / f"tradable_candidates_{trade_date}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _fmt_date(date_str: str) -> str:
    """YYYYMMDD → YYYY-MM-DD（展示用）。"""
    try:
        return datetime.strptime(str(date_str), "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return str(date_str)


def _three_lane_lane1_lag_days(trade_date: str) -> int | None:
    """three_lane_{trade_date}.parquet 的 Lane 1 实际 watchlist 日期相对 trade_date 的滞后天数。

    读产物 `_watchlist_date` 列（Lane 1 watchlist 数据日）。相同时返回 0（对齐）；
    产物缺失 / 列缺失 / 解析失败返回 None（不判滞后，避免误报）。滞后以自然日为差，
    工程口径：只要不是同日即视为滞后，须显式标记。
    """
    p = outputs_dir() / "etf_signal" / f"three_lane_{trade_date}.parquet"
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p, columns=["_watchlist_date"])
    except Exception:
        return None
    if "_watchlist_date" not in df.columns:
        return None
    dates = df["_watchlist_date"].dropna()
    if dates.empty:
        return None
    eff = pd.to_datetime(dates.max()).date()
    try:
        target = datetime.strptime(str(trade_date), "%Y%m%d").date()
    except ValueError:
        return None
    if eff > target:
        return 0
    return (target - eff).days


def cmd_run_day_check(args: argparse.Namespace) -> int:
    logger = build_logger(args.log_level)
    logger.info("=" * 60)
    logger.info("FINAL VALIDATION: run-day 结果校验")
    logger.info("=" * 60)

    requested = getattr(args, "date", "") or ""
    trade_date = requested or _latest_selection_date() or ""
    if not trade_date:
        logger.error("no selection candidates found — run `make select` first")
        print("Run completed with errors")
        print("errors     : no selection candidates found")
        return 1

    logger.info("validating trade_date=%s", trade_date)

    selection = _load_selection(trade_date)
    selection_exists = selection is not None
    layer3 = (selection or {}).get("layer3") or {}
    layers = (selection or {}).get("layers")
    alignment = (selection or {}).get("alignment")
    action_level = (layer3.get("action") or {}).get("level")
    if selection_exists and "layer3" not in selection:
        logger.error("selection JSON missing layer3 — publish failed")
        print("Run completed with errors")
        print(f"trade_date : {_fmt_date(trade_date)}")
        print("errors     : selection candidates 缺少 layer3 结构")
        return 1

    rotation_exists = (etf_signal_daily_dir() / f"rotation_{trade_date}.parquet").exists()
    confirmation_exists = (sw_industry_confirmation_dir() / f"confirmation_{trade_date}.parquet").exists()
    transition_state_exists = (
        outputs_dir() / "research" / "trend_transition" / f"trend_transition_state_{trade_date}.parquet"
    ).exists()

    warns = run_warnings.load_warnings(trade_date)
    lane1_lag_days = _three_lane_lane1_lag_days(trade_date)
    result = evaluate(
        trade_date=trade_date,
        layers=layers,
        alignment=alignment,
        action_level=action_level,
        warnings=warns,
        coverage=(selection or {}).get("coverage"),
        selection_exists=selection_exists,
        rotation_exists=rotation_exists,
        confirmation_exists=confirmation_exists,
        transition_state_exists=transition_state_exists,
        lane1_lag_days=lane1_lag_days,
    )

    if result["ok"]:
        print("Run completed successfully")
    else:
        print("Run completed with errors")
    print(f"trade_date : {_fmt_date(result['trade_date'])}")
    print(f"status     : {result['status']}")
    print(f"action     : {result['action']}")
    if result["errors"]:
        print("errors     : " + "; ".join(result["errors"]))
    if result["warnings"]:
        print("warnings   : " + "; ".join(result["warnings"]))
    else:
        print("warnings   : -")

    for w in result["warnings"]:
        logger.warning("warning: %s", w)
    for e in result["errors"]:
        logger.error("error: %s", e)

    return 0 if result["ok"] else 1


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="run-day 末端 Final Validation")
    p.add_argument("--date", default="", help="目标 trade_date YYYYMMDD（默认各层最新）")
    p.add_argument("--log-level", default="INFO")
    return p


def main() -> None:
    args = build_arg_parser().parse_args()
    sys.exit(cmd_run_day_check(args))
