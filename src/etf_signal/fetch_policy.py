"""ETF 行情获取策略 — 纯决策逻辑（Data Acquisition，Performance V1）。

职责边界（v0.9.2 锁定）：
  - 只回答「要不要抓 / 抓多少 / 数据够不够可信」，不做任何 Layer①②③ 计算或决策。
  - 相同最终行情 → Layer①②③ 结果不变。本模块任何改动不得触碰 RPS / trend gate /
    position / theme mapping / confirmation / selection 语义。

本模块是纯函数层，不联网、不读写 raw，只依赖输入的 DataFrame 与日期参数：
  - decide_update:         SKIP_UP_TO_DATE / INCREMENTAL / FULL_REFRESH
  - build_fetch_window:    增量/全量拉取窗口 [start, end]（YYYYMMDD 字符串）
  - validate_append:       append/merge 后 guardrail（date 唯一 + close/volume 完整）

配置（config/market_data.yaml）经 frozen loader 读取，生产路径无隐藏默认值。
该配置属于 Data Acquisition，不进 src/common/spec 的 strategy config_hash / rule_version。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

import pandas as pd

logger = logging.getLogger("etf_signal.fetch_policy")

MARKET_DATA_YAML = "market_data.yaml"

# validate_append 的必备字段：行情 bar 缺这些即视为数据不完整
REQUIRED_BAR_COLUMNS = ("close", "volume")


class UpdateDecision(str, Enum):
    """单只 ETF 在目标 trade_date 前的更新决策。

    - SKIP_UP_TO_DATE: raw 已含 target 完整 bar → 0 请求（CACHE HIT）
    - INCREMENTAL:      有历史但缺 target → 只抓 [last_bar+1, target]（FAST PATH）
    - FULL_REFRESH:     无历史（新上市/缺文件）→ 全量起点拉取（slow path）
    """

    SKIP_UP_TO_DATE = "skip_up_to_date"
    INCREMENTAL = "incremental"
    FULL_REFRESH = "full_refresh"


# ═══════════════════════════════════════════════════════════════════
# Typed config（frozen，本地 loader，不注册进 strategy config_hash）
# ═══════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class CircuitBreakerSpec:
    enabled: bool = True
    window: int = 20
    min_requests: int = 10
    failure_rate_threshold: float = 0.50


@dataclass(frozen=True)
class EastmoneyFetchSpec:
    circuit_breaker: CircuitBreakerSpec = field(default_factory=CircuitBreakerSpec)


@dataclass(frozen=True)
class SinaFetchSpec:
    workers: int = 8
    retry: int = 1


@dataclass(frozen=True)
class EtfFetchSpec:
    full_refresh_start: str = "20200101"
    em_backfill_limit: int = 30
    em_request_interval_s: float = 6.0
    eastmoney: EastmoneyFetchSpec = field(default_factory=EastmoneyFetchSpec)
    sina: SinaFetchSpec = field(default_factory=SinaFetchSpec)


@dataclass(frozen=True)
class MarketDataSpec:
    etf_fetch: EtfFetchSpec = field(default_factory=EtfFetchSpec)


def _read_market_data_yaml() -> dict[str, Any]:
    from src.common.paths import config_dir

    path: Path = config_dir() / MARKET_DATA_YAML
    if not path.exists():
        raise FileNotFoundError(f"config/{MARKET_DATA_YAML} missing (Data Acquisition spec)")
    try:
        import yaml
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("pyyaml required for market_data.yaml loader") from e
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


@lru_cache(maxsize=None)
def load_market_data_spec() -> MarketDataSpec:
    """读取 config/market_data.yaml → frozen MarketDataSpec。

    缺失关键参数直接抛错（生产路径无隐藏默认值）；可选字段给保守默认值。
    """
    node = (_read_market_data_yaml().get("etf_fetch") or {})
    em_node = (node.get("eastmoney") or {})
    cb_node = (em_node.get("circuit_breaker") or {})
    sina_node = (node.get("sina") or {})

    return MarketDataSpec(
        etf_fetch=EtfFetchSpec(
            full_refresh_start=str(node.get("full_refresh_start", "20200101")),
            em_backfill_limit=int(node.get("em_backfill_limit", 30)),
            em_request_interval_s=float(node.get("em_request_interval_s", 6.0)),
            eastmoney=EastmoneyFetchSpec(
                circuit_breaker=CircuitBreakerSpec(
                    enabled=bool(cb_node.get("enabled", True)),
                    window=int(cb_node.get("window", 20)),
                    min_requests=int(cb_node.get("min_requests", 10)),
                    failure_rate_threshold=float(cb_node.get("failure_rate_threshold", 0.50)),
                )
            ),
            sina=SinaFetchSpec(
                workers=int(sina_node.get("workers", 8)),
                retry=int(sina_node.get("retry", 1)),
            ),
        )
    )


# ═══════════════════════════════════════════════════════════════════
# 决策（纯函数）
# ═══════════════════════════════════════════════════════════════════


def _as_date_series(values: Any) -> pd.Series:
    """把 'date' 列统一成 datetime Series（stubs 下 pd.to_datetime 返回 Union，显式 cast）。"""
    return cast(pd.Series, pd.to_datetime(values, errors="coerce"))


def _date_values(df: pd.DataFrame) -> pd.Series:
    return _as_date_series(df["date"])


def has_target_bar(df: pd.DataFrame, target: date) -> bool:
    """raw 中是否真实存在 target 交易日的完整 K 线。

    与 data_source/cli 既有 guardrail 语义一致：必须按「精确日期存在」判断，
    不能仅比较 max(date) >= target —— 盘中 spot 会提前合并当日（> target）未收盘
    K 线，导致 max(date) 越过 target 但 target 当日 bar 仍缺失。
    """
    if df is None or df.empty or "date" not in df.columns:
        return False
    try:
        dates = _date_values(df)
        return bool((dates.dt.date == target).any())
    except Exception:
        return False


def latest_bar_date(df: pd.DataFrame) -> date | None:
    if df is None or df.empty or "date" not in df.columns:
        return None
    try:
        return _date_values(df).max().date()
    except Exception:
        return None


def decide_update(existing: pd.DataFrame, target: date) -> UpdateDecision:
    """根据本地 raw 状态决定单只 ETF 的更新策略。"""
    if has_target_bar(existing, target):
        return UpdateDecision.SKIP_UP_TO_DATE
    if existing is None or existing.empty or "date" not in existing.columns:
        return UpdateDecision.FULL_REFRESH
    return UpdateDecision.INCREMENTAL


def _incremental_start(existing: pd.DataFrame, target: date) -> date:
    """增量起点 = 本地最近 bar 的下一个日历日（源端会钳制到交易日，安全上不欠拉）。

    V1 语义与既有 cmd_update 一致（last_prior + 1 日历日）：周末/假期多出的
    非交易日由源端自然跳过，只多不少、绝不漏拉 target。
    """
    last = latest_bar_date(existing)
    if last is None:
        return target
    return last + timedelta(days=1)


def build_fetch_window(
    existing: pd.DataFrame,
    target: date,
    full_refresh_start: str = "20200101",
) -> tuple[str, str] | None:
    """返回待拉窗口 (start, end) 为 YYYYMMDD 字符串；SKIP 返回 None。

    - SKIP_UP_TO_DATE  → None（0 请求）
    - INCREMENTAL      → (last_bar + 1, target)
    - FULL_REFRESH     → (full_refresh_start, target)
    """
    decision = decide_update(existing, target)
    end = target.strftime("%Y%m%d")
    if decision == UpdateDecision.SKIP_UP_TO_DATE:
        return None
    if decision == UpdateDecision.FULL_REFRESH:
        return full_refresh_start, end
    return _incremental_start(existing, target).strftime("%Y%m%d"), end


# ═══════════════════════════════════════════════════════════════════
# Guardrail（append/merge 后校验）
# ═══════════════════════════════════════════════════════════════════


def validate_append(
    existing: pd.DataFrame,
    merged: pd.DataFrame,
    required: tuple[str, ...] = REQUIRED_BAR_COLUMNS,
) -> tuple[bool, list[str]]:
    """增量 append 后的三个 guardrail，失败返回 (False, issues)：

    ① date 唯一（重复 = 脏数据）
    ② required 列（close/volume）对新增行非空 —— 只检查「新增段」，历史不追溯
    ③ 必要列存在
    """
    issues: list[str] = []
    if merged is None or merged.empty:
        return True, issues
    if "date" not in merged.columns:
        return False, ["merged 缺 date 列"]

    dates = _as_date_series(merged["date"])
    n_dup = int(dates.duplicated().sum())
    if n_dup:
        issues.append(f"date 重复 {n_dup} 行")

    prior_max = latest_bar_date(existing)
    added_mask = dates.gt(pd.Timestamp(prior_max)) if prior_max is not None else pd.Series(True, index=merged.index)
    added = merged.loc[added_mask.to_numpy()]
    for col in required:
        if col not in merged.columns:
            issues.append(f"缺必备列 {col}")
            continue
        n_bad = int(added[col].isna().sum()) if not added.empty else 0
        if n_bad:
            issues.append(f"{col} 在新增行含 {n_bad} 个空值")

    return not issues, issues
