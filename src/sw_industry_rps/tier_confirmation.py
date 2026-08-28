"""
Layer ② Tier-level confirmation（v0.9.1/v0.9.2）— Theme → Tier basket → 个股趋势

统一框架：所有配置了 tiers 的 theme（AI 基础设施 / 中国汽车全球化 / 高现金流资产）
都走「Theme → Tier basket → 个股趋势 → Theme confirmation → 申万行业 Evidence」：

    theme（如 ai_infrastructure / china_auto_global / high_cashflow）
      └─ tiers（每 Tier = 一篮子个股，universe_tiers 映射 selection_universe.yaml）
            └─ 个股趋势（trend_score / return / watch_level）

每个 Tier 自算（Observation，不伪造 RPS）：
  - tier_strength  加权复合分（0-100，非横截面 RPS）：
      0.5 × median(trend_score) + 0.3 × 上涨比例×100 + 0.2 × 强趋势占比×100
  - advance_ratio  上涨比例 = return_20d > 0 的股票占比
  - median_trend_score  成分股 trend_score 中位数
  - n_strong_trend  强趋势股票数量（trend_score ≥ strong_trend_min 且 watch_level ∈ {S,A}）
  - leader_contribution  龙头贡献度 = 龙头股 |return_20d| 占 Tier 全部 |return_20d| 之和的比例
  - leader_symbol / leader_name  龙头股

状态 taxonomy（v0.9.2）：
  Tier 层  STRONG / CONFIRMED / WATCH / UNCONFIRMED / UNAVAILABLE
    - WATCH 只表示「值得观察但未确认」，为什么观察由 reason_code 表达
      （near_threshold / breadth_insufficient / trend_emerging / single_name_only），
      展示层组合成「观察 · breadth不足」等，不把业务含义塞进 state。
  Theme 层 BROAD_CONFIRMED / CONFIRMED / NARROW_CONFIRMED / UNCONFIRMED / UNAVAILABLE
    - 不使用 WATCH；观察中 Tier 数（n_watch_tiers）单独输出，渲染为
      「未确认 · N 个 Tier 进入观察」。
  申万行业（confirmation parquet）保留为 Evidence，不再是确认 Gate。

产物：data/processed/sw_industry/tier_confirmation_{trade_date}.parquet
      （含 trade_date / run_date / generated_at / data_status / source 元数据）
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.common.paths import sw_industry_processed_dir
from src.common.spec.loaders import load_indicator_spec
from src.common import themes as themes_cfg

logger = logging.getLogger("sw_industry_rps.tier_confirmation")


def _tier_params() -> tuple[float, float, float, float]:
    """Tier 门控阈值（Observation，来自 config/indicator_spec.yaml tier_confirmation）。"""
    s = load_indicator_spec()
    return (s.tier_gate_strong, s.tier_gate_observe, s.tier_broad_fraction, s.tier_strong_trend_min)


TIER_GATE_STRONG, TIER_GATE_OBSERVE, TIER_BROAD_FRACTION, STRONG_TREND_MIN = _tier_params()

STRONG_STATES = {"S", "A"}

# 加权复合分权重（Observation 口径，与 indicator_spec.yaml 注释一致）
W_STRENGTH = 0.5   # median(trend_score)
W_ADVANCE = 0.3    # 上涨比例
W_STRONG = 0.2     # 强趋势占比

# ── Tier 状态枚举（v0.9.2 taxonomy，Observation）──────────────────────────
# WATCH 只表示「值得观察但尚未确认」，为什么观察由 reason_code 表达（不塞进 state）。
TIER_STATE_STRONG = "STRONG"          # 强势
TIER_STATE_CONFIRMED = "CONFIRMED"    # 已确认（进入确认门）
TIER_STATE_WATCH = "WATCH"            # 观察（出现信号但未满足确认条件）
TIER_STATE_UNCONFIRMED = "UNCONFIRMED"  # 未确认
TIER_STATE_UNAVAILABLE = "UNAVAILABLE"  # 数据不可用
# 兼容旧产物命名（v0.9.0/0.9.1 曾用 OBSERVE 表达「进入观察区」= 现 CONFIRMED）
TIER_STATE_LEGACY_OBSERVE = "OBSERVE"

TIER_STATE_LABELS = {
    TIER_STATE_STRONG: "强势",
    TIER_STATE_CONFIRMED: "已确认",
    TIER_STATE_WATCH: "观察",
    TIER_STATE_UNCONFIRMED: "未确认",
    TIER_STATE_UNAVAILABLE: "数据不可用",
}

# Tier WATCH 的细分原因（state 解耦，展示层 state + reason 组合）
TIER_WATCH_REASON_NEAR_THRESHOLD = "near_threshold"            # 观察 · 接近确认
TIER_WATCH_REASON_BREADTH_INSUFFICIENT = "breadth_insufficient"  # 观察 · breadth不足
TIER_WATCH_REASON_TREND_EMERGING = "trend_emerging"            # 观察 · 趋势启动
TIER_WATCH_REASON_SINGLE_NAME = "single_name_only"             # 观察 · 单点驱动

TIER_WATCH_REASON_LABELS = {
    TIER_WATCH_REASON_NEAR_THRESHOLD: "接近确认",
    TIER_WATCH_REASON_BREADTH_INSUFFICIENT: "breadth不足",
    TIER_WATCH_REASON_TREND_EMERGING: "趋势启动",
    TIER_WATCH_REASON_SINGLE_NAME: "单点驱动",
}

# 展示排序：确认优先（STRONG → CONFIRMED → WATCH → UNCONFIRMED → UNAVAILABLE），
# 同档按 tier_strength 降序
TIER_STATE_RANK = {
    TIER_STATE_STRONG: 0, TIER_STATE_CONFIRMED: 1, TIER_STATE_WATCH: 2,
    TIER_STATE_UNCONFIRMED: 3, TIER_STATE_UNAVAILABLE: 4,
}


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if not np.isnan(x) else None


def tier_metrics_for_theme(
    theme_key: str = "ai_infrastructure",
    stock_metrics: pd.DataFrame | None = None,
    universe_items: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """计算某主题下每个 Tier 的确认指标。

    Args:
        theme_key: 主题 key（默认 ai_infrastructure）
        stock_metrics: stock_metrics_{date}.parquet 内容（趋势指标）；为空则全部 unavailable
        universe_items: universe items（可空，从 selection_universe.yaml 现读）

    Returns:
        每 Tier 一行的 dict 列表（含 tier_strength / advance_ratio / median_trend_score /
        n_strong_trend / leader_contribution / confirmation_state 等）
    """
    theme = themes_cfg.load_themes().get(theme_key)
    if theme is None or not theme.tiers:
        return []

    # 从 universe 读取该主题的成分股 → tier 归属
    if universe_items is None:
        from src.selection.universe import load_universe_items
        from src.common.paths import selection_universe_path
        universe_items = load_universe_items(selection_universe_path())
    theme_items = [
        it for it in universe_items
        if it.theme == theme_key and it.tier not in ("theme_etf", "sub_industry_etf")
    ]

    metrics_by_symbol: dict[str, dict[str, Any]] = {}
    if stock_metrics is not None and not stock_metrics.empty and "symbol" in stock_metrics.columns:
        for _, r in stock_metrics.iterrows():
            metrics_by_symbol[str(r.get("symbol", ""))] = r.to_dict()

    rows: list[dict[str, Any]] = []
    for tt in theme.tiers:
        tier_symbols = [it.asset.symbol for it in theme_items if it.tier in tt.universe_tiers]
        tier_names = {it.asset.symbol: it.asset.name for it in theme_items if it.tier in tt.universe_tiers}
        stock_rows = [metrics_by_symbol.get(s) for s in tier_symbols if s in metrics_by_symbol]
        stock_rows = [m for m in stock_rows if m is not None]
        valid = [m for m in stock_rows if _f(m.get("trend_score")) is not None]

        n_total = len(tier_symbols)
        n_with_data = len(valid)
        n_missing = n_total - n_with_data

        if not valid:
            rows.append({
                "theme": theme_key,
                "tier": tt.key,
                "tier_label": tt.label,
                "n_total": n_total,
                "n_with_data": 0,
                "n_missing": n_missing,
                "tier_strength": None,
                "advance_ratio": None,
                "median_trend_score": None,
                "n_strong_trend": 0,
                "leader_contribution": None,
                "leader_symbol": "",
                "leader_name": "",
                "confirmation_state": TIER_STATE_UNAVAILABLE,
                "confirmation_breadth": "",
                "reason_code": "",
                "reason": "无个股数据",
                "data_status": "unavailable",
            })
            continue

        scores = pd.Series([_f(m["trend_score"]) for m in valid], dtype="float64").dropna()
        median_trend = round(float(scores.median()), 1) if not scores.empty else None

        ret20 = [_f(m.get("return_20d")) for m in valid]
        ret20_clean = [x for x in ret20 if x is not None]
        advance = float(sum(1 for x in ret20_clean if x > 0)) / len(ret20_clean) if ret20_clean else 0.0

        strong = [
            m for m in valid
            if (_f(m.get("trend_score")) or 0) >= STRONG_TREND_MIN
            and str(m.get("watch_level", "")).strip().upper() in STRONG_STATES
        ]
        n_strong = len(strong)
        strong_ratio = n_strong / len(valid) if valid else 0.0

        tier_strength = round(
            W_STRENGTH * (median_trend or 0.0)
            + W_ADVANCE * advance * 100.0
            + W_STRONG * strong_ratio * 100.0,
            1,
        ) if median_trend is not None else None

        # 龙头贡献度：|return_20d| 最大者占比
        leader_contribution = None
        leader_symbol = ""
        leader_name = ""
        if ret20_clean:
            abs_ret = [abs(x) for x in ret20_clean]
            total_abs = sum(abs_ret)
            if total_abs > 0:
                idx = int(np.argmax(abs_ret))
                leader_symbol = str(valid[idx].get("symbol", ""))
                leader_name = str(tier_names.get(leader_symbol, valid[idx].get("name", leader_symbol)))
                leader_contribution = round(abs_ret[idx] / total_abs, 3)

        state, reason_code, reason = _tier_state(tier_strength, n_strong, n_with_data)

        rows.append({
            "theme": theme_key,
            "tier": tt.key,
            "tier_label": tt.label,
            "n_total": n_total,
            "n_with_data": n_with_data,
            "n_missing": n_missing,
            "tier_strength": tier_strength,
            "advance_ratio": round(advance, 3),
            "median_trend_score": median_trend,
            "n_strong_trend": n_strong,
            "leader_contribution": leader_contribution,
            "leader_symbol": leader_symbol,
            "leader_name": leader_name,
            "confirmation_state": state,
            "confirmation_breadth": TIER_STATE_LABELS.get(state, ""),
            "reason_code": reason_code,
            "reason": reason,
            "data_status": "current",
        })

    rows.sort(key=lambda r: (TIER_STATE_RANK.get(r["confirmation_state"], 9), -(r["tier_strength"] or -1)))
    return rows


def _tier_state(
    tier_strength: float | None,
    n_strong: int,
    n_with_data: int,
) -> tuple[str, str, str]:
    """Tier 门控判定（Observation，v0.9.2 taxonomy）。

    - STRONG      tier_strength ≥ tier_gate_strong 且 ≥1 只强趋势 → 强势
    - CONFIRMED   tier_strength ≥ tier_gate_observe → 已确认（进入确认门）
    - WATCH       tier_strength ≥ 观察门一半 → 观察（值得关注但未确认），
                  为什么观察由 reason_code 表达（near_threshold / breadth_insufficient /
                  trend_emerging / single_name_only），不塞进 state
    - UNCONFIRMED 其余
    """
    if tier_strength is None or n_with_data == 0:
        return TIER_STATE_UNAVAILABLE, "", "无个股数据"
    if tier_strength >= TIER_GATE_STRONG and n_strong >= 1:
        return TIER_STATE_STRONG, "", f"Tier Strength {tier_strength}（≥{TIER_GATE_STRONG}）且 {n_strong} 只强趋势"
    if tier_strength >= TIER_GATE_OBSERVE:
        return TIER_STATE_CONFIRMED, "", f"Tier Strength {tier_strength} 进入确认门（≥{TIER_GATE_OBSERVE}）"
    if tier_strength >= TIER_GATE_OBSERVE * 0.5:
        reason_code, reason = _tier_watch_reason(tier_strength, n_strong)
        return TIER_STATE_WATCH, reason_code, reason
    return TIER_STATE_UNCONFIRMED, "", f"Tier Strength {tier_strength} 偏弱"


def _tier_watch_reason(tier_strength: float, n_strong: int) -> tuple[str, str]:
    """WATCH 的细分原因：为什么值得观察（展示层组合成「观察 · <reason>」）。

    - breadth_insufficient  有强趋势股但整 Tier 广度不足（少数拉动）
    - near_threshold        无强趋势股但整体已接近确认门
    - trend_emerging        早期迹象，强度尚低（趋势刚启动）
    - single_name_only      单标的驱动
    """
    if n_strong >= 1:
        return (TIER_WATCH_REASON_BREADTH_INSUFFICIENT,
                f"有 {n_strong} 只强趋势股但广度不足（Tier Strength {tier_strength}）")
    if tier_strength >= TIER_GATE_OBSERVE * 0.8:
        return (TIER_WATCH_REASON_NEAR_THRESHOLD,
                f"接近确认门（Tier Strength {tier_strength}）")
    if tier_strength >= TIER_GATE_OBSERVE * 0.65:
        return (TIER_WATCH_REASON_TREND_EMERGING,
                f"趋势启动迹象（Tier Strength {tier_strength}）")
    return (TIER_WATCH_REASON_SINGLE_NAME,
            f"单点驱动（Tier Strength {tier_strength}）")


def _tier_is_confirmed(state: str) -> bool:
    """Tier 是否进入确认（STRONG / CONFIRMED；兼容旧产物 OBSERVE）。"""
    return state in (TIER_STATE_STRONG, TIER_STATE_CONFIRMED, TIER_STATE_LEGACY_OBSERVE)


def _tier_is_watch(state: str) -> bool:
    return state == TIER_STATE_WATCH


def theme_confirmation_from_tiers(tier_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """由 Tier 确认结果聚合主题级确认状态（决策层消费的事实）。

    v0.9.1 四档状态（按 confirmed Tier 广度）：
      - BROAD_CONFIRMED  确认 Tier 数 ≥ 总 Tier 数 × broad_fraction
      - CONFIRMED        确认 Tier ≥ 2 但未达 broad_fraction（多链确认）
      - NARROW_CONFIRMED 恰好 1 个 Tier 进入观察区（单一链确认）
      - UNCONFIRMED      无 Tier 进入观察区
      - UNAVAILABLE      全部 Tier 无个股数据（不可判定）

    Theme 层不使用 WATCH：观察中的 Tier 数（watch_tier_count）单独输出，
    由展示层渲染为「未确认 · N 个 Tier 进入观察」。

    Returns:
        {
            theme, confirmed, confirmation_state, n_tiers, n_observe_tiers,
            n_watch_tiers, n_strong_tiers, broad_confirmed, tier_strength_median, reason
        }
    """
    active = [r for r in tier_rows if r.get("data_status") != "unavailable"]
    n_tiers = len(tier_rows)
    n_strong = sum(1 for r in tier_rows if r.get("confirmation_state") == TIER_STATE_STRONG)
    n_observe = sum(1 for r in tier_rows if _tier_is_confirmed(str(r.get("confirmation_state", ""))))
    n_watch = sum(1 for r in tier_rows if _tier_is_watch(str(r.get("confirmation_state", ""))))
    strengths: list[float] = []
    for r in tier_rows:
        if r.get("data_status") == "unavailable":
            continue
        v = r.get("tier_strength")
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if not np.isnan(f):
            strengths.append(f)
    median = round(float(float(np.median(strengths))), 1) if strengths else None

    confirmed = n_observe >= 1
    broad = confirmed and n_observe >= max(1, int(round(n_tiers * TIER_BROAD_FRACTION))) if n_tiers else False
    if not active:
        state = TIER_STATE_UNAVAILABLE
    elif confirmed:
        state = "BROAD_CONFIRMED" if broad else ("CONFIRMED" if n_observe >= 2 else "NARROW_CONFIRMED")
    else:
        state = TIER_STATE_UNCONFIRMED

    if not active:
        reason = "无 Tier 个股数据（不可判定）"
    elif confirmed:
        reason = f"{n_observe}/{n_tiers} 个 Tier 已确认（中位 Tier Strength {median}）"
    elif n_watch:
        reason = f"未确认 · {n_watch} 个 Tier 进入观察（中位 Tier Strength {median}）"
    else:
        reason = f"无 Tier 进入观察（中位 Tier Strength {median}）"

    return {
        "theme": tier_rows[0]["theme"] if tier_rows else "",
        "confirmed": confirmed,
        "confirmation_state": state,
        "n_tiers": n_tiers,
        "n_observe_tiers": n_observe,
        "n_watch_tiers": n_watch,
        "n_strong_tiers": n_strong,
        "broad_confirmed": broad,
        "tier_strength_median": median,
        "reason": reason,
    }


def build_tier_confirmation_parquet(
    trade_date: str,
    stock_metrics: pd.DataFrame | None = None,
    data_status: str = "confirmed",
    source: str = "stock_metrics",
    processed_dir: Path | None = None,
    universe_items: list[Any] | None = None,
) -> Path:
    """计算全部配置了 tiers 的主题（AI/高现金流/汽车）各 Tier 确认并落盘
    tier_confirmation_{date}.parquet。

    v0.9.1：统一框架——所有 theme 走 Theme → Tier basket → 个股趋势。
    成分股归属由 theme_registry.yaml 的 tiers.universe_tiers 映射 selection_universe.yaml。

    Args:
        trade_date: 信号日期 YYYYMMDD（文件名日期 = 目标 trade_date）
        stock_metrics: stock_metrics parquet 内容；为空 → Tier 全部 unavailable（不阻塞）
        data_status: confirmed / provisional
        source: 数据来源标签
        processed_dir: 落盘目录（默认 data/processed/sw_industry）
        universe_items: universe items（可空，从 selection_universe.yaml 现读）

    Returns:
        产物路径
    """
    rows: list[dict[str, Any]] = []
    for theme_key in themes_cfg.load_themes():
        rows.extend(tier_metrics_for_theme(theme_key, stock_metrics, universe_items))
    df = pd.DataFrame(rows) if rows else pd.DataFrame()
    if not df.empty:
        df["trade_date"] = pd.Timestamp(trade_date)
        df["run_date"] = pd.Timestamp(datetime.now().date())
        df["generated_at"] = pd.Timestamp(datetime.now(timezone.utc))
        df["data_status"] = data_status
        df["source"] = source
    processed_dir = processed_dir or sw_industry_processed_dir()
    processed_dir.mkdir(parents=True, exist_ok=True)
    out_path = processed_dir / f"tier_confirmation_{trade_date}.parquet"
    df.to_parquet(out_path, index=False)
    logger.info("tier confirmation saved: %d tiers across %d themes -> %s",
                len(df), df["theme"].nunique() if not df.empty else 0, out_path)
    return out_path


def load_tier_confirmation(trade_date: str, processed_dir: Path | None = None) -> pd.DataFrame:
    """读取 tier_confirmation_{date}.parquet（含全部主题的 Tier 确认）；缺失返回空 DataFrame。"""
    processed_dir = processed_dir or sw_industry_processed_dir()
    path = processed_dir / f"tier_confirmation_{trade_date}.parquet"
    if not path.exists():
        # 兼容旧命名（v0.9.0 ai_tier_confirmation）
        legacy = processed_dir / f"ai_tier_confirmation_{trade_date}.parquet"
        path = legacy if legacy.exists() else path
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(path)
    except Exception as e:
        logger.warning("failed to load tier confirmation %s: %s", path, e)
        return pd.DataFrame()
