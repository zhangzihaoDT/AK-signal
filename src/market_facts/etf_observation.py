"""ETF market observation facts: cross-sectional RPS and asset-direction summaries.

This module owns published market facts consumed by Observation. It does not
select candidate ETFs, generate trading signals, or render the retired Rotation report.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from src.common import themes as themes_cfg
from src.etf_signal import classifier, classification_facts

logger = logging.getLogger("market_facts.etf_observation")

ASSET_CLASS_LABEL: dict[str, str] = {
    "equity": "权益",
    "bond": "债券",
    "commodity": "商品",
    "cash": "现金",
    "overseas": "海外",
    "multi_asset": "跨资产",
}

BUCKET_LABELS: dict[str, str] = {
    k: v.get("label", k)
    for k, v in classifier.BUCKET_DEFINITIONS.items()
}

RPS5_WINDOW = 5

RANK_CHANGE_DAYS = 5

CROSS_SECTIONAL_TAIL = 21

LIQUIDITY_AVG_DAYS = 5

BUCKET_LABELS[""] = "未分类"

def _rps_windows() -> tuple[int, ...]:
    from src.common.spec.loaders import load_indicator_spec
    return load_indicator_spec().rps_windows

def _rps_today_window() -> int:
    from src.common.spec.loaders import load_indicator_spec
    return load_indicator_spec().rps_today_window

def _rps_velocity_window() -> int:
    from src.common.spec.loaders import load_indicator_spec
    return load_indicator_spec().rps_velocity_window

def _data_quality_threshold() -> float:
    from src.common.spec.loaders import load_indicator_spec
    return load_indicator_spec().data_quality_max_single_day_return

def _data_quality_window() -> int:
    from src.common.spec.loaders import load_indicator_spec
    return load_indicator_spec().data_quality_flag_window

def _pivot_values(
    combined: pd.DataFrame,
    value_col: str,
    date_col: str = "date",
    code_col: str = "fund_code",
) -> pd.DataFrame:
    """宽表：行 = 交易日，列 = fund_code，值 = value_col。"""
    if combined.empty or value_col not in combined.columns:
        return pd.DataFrame()
    pivot = combined.pivot_table(
        index=date_col, columns=code_col, values=value_col, aggfunc="last",
    ).sort_index()
    return pivot

def _pivot_closes(
    combined: pd.DataFrame,
    date_col: str = "date",
    code_col: str = "fund_code",
    price_col: str = "close",
) -> pd.DataFrame:
    """宽表：行 = 交易日，列 = fund_code，值 = close。"""
    return _pivot_values(combined, price_col, date_col=date_col, code_col=code_col)

def compute_cross_sectional(
    combined: pd.DataFrame,
    windows: tuple[int, ...] | None = None,
    tail: int = CROSS_SECTIONAL_TAIL,
    rank_codes: set[str] | None = None,
    rank_codes_by_window: dict[int, set[str]] | None = None,
) -> dict[int, dict[str, pd.DataFrame]]:
    """计算全市场逐日横截面 RPS 与 ordinal rank。

    仅在 rank_codes 指定的一组 ETF 内做横截面排名（默认：除货币/债券外的全部风险资产），
    避免货币类零收益标的结构性占优。

    Args:
        combined: 全市场日行情（date, fund_code, close）
        rank_codes: 参与排名的 fund_code 集合；为 None 时使用全部
        rank_codes_by_window: 按窗口的参与排名集合（v0.7.0 数据质量，窗口内单日异常
            资产不参与该窗口横截面排名）；命中该 dict 的窗口以其为准，否则用 rank_codes
        windows / tail: 排名窗口 / 尾部保留行数

    Returns:
        {
            window: {
                'returns': DataFrame(date × code)  窗口收益率（最近 tail 行）
                'rps':     DataFrame(date × code)  百分位排名 0-100
                'rank':    DataFrame(date × code)  ordinal 排名（1 = 最弱）
            }
        }
    """
    if windows is None:
        windows = _rps_windows()
    closes = _pivot_closes(combined)
    if closes.empty:
        return {}

    out: dict[int, dict[str, pd.DataFrame]] = {}
    for w in windows:
        # 仅保留尾部 tail+w 行，控制内存
        sub = closes.iloc[-(tail + w):]
        ret = sub / sub.shift(w) - 1
        recent = ret.iloc[-tail:]
        codes = rank_codes_by_window.get(w, rank_codes) if rank_codes_by_window else rank_codes
        if codes is not None:
            keep = [c for c in recent.columns if c in codes]
            recent = recent[keep]
        rps = recent.rank(axis=1, pct=True, ascending=True) * 100
        rnk = recent.rank(axis=1, method="min", ascending=True)
        out[w] = {"returns": recent, "rps": rps, "rank": rnk}
    return out

def _latest_returns(closes: pd.DataFrame, periods: tuple[int, ...]) -> dict[str, pd.Series]:
    """最新交易日各窗口收益率（百分比）。"""
    if len(closes) < 2:
        return {}
    last = closes.iloc[-1]
    out: dict[str, pd.Series] = {}
    for p in periods:
        if len(closes) > p:
            out[f"return_{p}d"] = (last / closes.iloc[-1 - p] - 1) * 100
    return out

def _compute_latest_rps(
    closes: pd.DataFrame,
    rank_codes: set[str] | None,
    window: int,
    tail: int = CROSS_SECTIONAL_TAIL,
) -> pd.Series:
    """最新交易日 window 日收益的横截面百分位（0-100）。

    仅 Observation：RPS1 标注「今天是不是热点」，RPS5 用于大类资产短期强度。
    不参与排序，也不进入 Selection。
    """
    if closes.empty or len(closes) <= window:
        return pd.Series(dtype=float)
    sub = closes.iloc[-(tail + window):]
    ret = sub / sub.shift(window) - 1
    recent = ret.iloc[-tail:]
    if rank_codes is not None:
        recent = recent[[c for c in recent.columns if c in rank_codes]]
    if recent.empty:
        return pd.Series(dtype=float)
    rps = recent.rank(axis=1, pct=True, ascending=True) * 100
    return rps.iloc[-1]

def _compute_liquidity(
    combined: pd.DataFrame,
    avg_days: int = LIQUIDITY_AVG_DAYS,
) -> pd.Series:
    """Liquidity（Observation）：最近 avg_days 个交易日成交额均值的横截面百分位（0-100）。

    本列只描述流动性强弱，不构成筛选或交易信号。
    """
    amount = _pivot_values(combined, value_col="amount")
    if amount.empty:
        return pd.Series(dtype=float)
    avg = amount.iloc[-min(avg_days, len(amount)):].mean(axis=0)
    return avg.rank(pct=True, ascending=True) * 100

def _detect_anomaly_offsets(
    closes: pd.DataFrame,
    flag_window: int,
    threshold_pct: float,
) -> dict[str, list[int]]:
    """检测回溯窗口内的单日异常收益（份额折算/除权/异常行情）。

    Args:
        closes: 宽表（date × fund_code，close）
        flag_window: 回溯交易日数
        threshold_pct: 单日 |收益| ≥ 该百分比 → 异常

    Returns:
        {fund_code: [offset, ...]}，offset 为距最新交易日的偏移（1=今日，越小越近）。
        例：折算发生在 3 个交易日前 → offset=[3]。
    """
    if len(closes) < 2:
        return {}
    n = min(flag_window + 1, len(closes))
    sub = closes.iloc[-n:]
    ret = sub.pct_change() * 100
    out: dict[str, list[int]] = {}
    for code in ret.columns:
        s = ret[code]
        bad = s.index[s.abs() >= threshold_pct]
        if not len(bad):
            continue
        # 日收益在第 i 行（1..n-1）实现，距最新交易日 offset = n - i
        offsets = sorted({int(n - ret.index.get_loc(i)) for i in bad})
        if offsets:
            out[code] = offsets
    return out

def match_theme(fund_name: str) -> str | None:
    """按 config/theme_registry.yaml 关键词匹配首个主题（v0.4.3 多主题）。"""
    return themes_cfg.match_theme(fund_name)

def compute_rotation_metrics(
    combined: pd.DataFrame,
    master: pd.DataFrame,
    windows: tuple[int, ...] | None = None,
) -> pd.DataFrame:
    """全市场 ETF 横截面事实（每只 ETF 一行）。

    Args:
        combined: 全市场日行情（date, fund_code, close）
        master:   ETF Master（fund_code, fund_name, asset_bucket, ...）

    Returns:
        DataFrame 列：
            fund_code, fund_name, asset_bucket, bucket_label, asset_class,
            is_tech,
            rps15, rps20, rps60,
            rps1, rps5, delta_rps15, liquidity,   # v0.7.0 Market Pulse（仅 Observation 展示）
            rank15, rank15_prev5, rank_change_5d,
            return_1d, return_5d, return_10d, return_15d, return_20d, return_60d,
            data_quality_flag              # 空=正常；corporate_action=单日异常/折算（原值保留）
    """
    if windows is None:
        windows = _rps_windows()
    today_window = _rps_today_window()
    velocity_window = _rps_velocity_window()
    dq_threshold = _data_quality_threshold()
    dq_window = _data_quality_window()
    closes = _pivot_closes(combined)
    if closes.empty:
        logger.warning("no close data for rotation")
        return pd.DataFrame()

    # Classifications are published inputs. Missing codes retain price facts without name fallback.
    identities = pd.DataFrame({'fund_code': list(closes.columns)})
    if not master.empty:
        identities = identities.merge(master.drop_duplicates('fund_code'), on='fund_code', how='left', validate='one_to_one')
    if 'fund_name' not in identities:
        identities['fund_name'] = ''
    master = classification_facts.attach(identities, closes.index[-1])
    rank_codes = set(master.loc[master.classification_rank_eligible, 'fund_code'])
    logger.info('rotation ranking universe: %d / %d ETFs from published classification', len(rank_codes), len(closes.columns))

    # v0.7.0 数据质量：回溯窗口内单日异常（份额折算/除权/异常行情）不参与对应窗口横截面排名
    anomaly_offsets = _detect_anomaly_offsets(closes, dq_window, dq_threshold)
    if anomaly_offsets:
        logger.warning("data-quality anomalies detected: %d ETFs (max_single_day_return>=%.0f%% in %dd)",
                       len(anomaly_offsets), dq_threshold, dq_window)
    rank_codes_by_window: dict[int, set[str]] = {}
    for w in windows:
        rank_codes_by_window[w] = {
            c for c in rank_codes
            if not any(o <= w for o in anomaly_offsets.get(c, []))
        }
    today_rank_codes = {
        c for c in rank_codes
        if not any(o <= today_window for o in anomaly_offsets.get(c, []))
    }
    rps5_rank_codes = {
        c for c in rank_codes
        if not any(o <= RPS5_WINDOW for o in anomaly_offsets.get(c, []))
    }

    xs = compute_cross_sectional(
        combined, windows=windows, rank_codes=rank_codes,
        rank_codes_by_window=rank_codes_by_window,
    )
    if not xs:
        return pd.DataFrame()

    returns = _latest_returns(closes, periods=(1, 5, 10, 15, 20, 60))

    # ordinal rank：今日 与 5 个交易日前
    rank_series = xs[windows[0]]["rank"]
    rank_today = rank_series.iloc[-1]
    rank_prev5 = rank_series.shift(RANK_CHANGE_DAYS).iloc[-1] if len(rank_series) > RANK_CHANGE_DAYS else pd.Series(index=rank_series.columns, dtype=float)

    # v0.7.0 Market Pulse 观察指标（仅展示，不参与排序/选择）
    today_rps = _compute_latest_rps(closes, today_rank_codes, window=today_window)
    rps5 = _compute_latest_rps(closes, rps5_rank_codes, window=RPS5_WINDOW)
    rps15_series = xs[windows[0]]["rps"]
    rps15_today = rps15_series.iloc[-1]
    rps15_prev = (
        rps15_series.shift(velocity_window).iloc[-1]
        if len(rps15_series) > velocity_window
        else pd.Series(index=rps15_series.columns, dtype=float)
    )
    liquidity_pct = _compute_liquidity(combined)

    codes = list(closes.columns)
    rows: list[dict[str, Any]] = []
    for code in codes:
        row: dict[str, Any] = {"fund_code": code}
        for w in windows:
            rps = xs[w]["rps"].iloc[-1].get(code)
            row[f"rps{w}"] = None if pd.isna(rps) else float(rps)
        r1 = today_rps.get(code)
        row["rps1"] = None if pd.isna(r1) else float(r1)
        r5 = rps5.get(code)
        row["rps5"] = None if pd.isna(r5) else float(r5)
        r15v = rps15_today.get(code)
        r15p = rps15_prev.get(code)
        if pd.notna(r15v) and pd.notna(r15p):
            row["delta_rps15"] = float(r15v - r15p)
        else:
            row["delta_rps15"] = None
        liq = liquidity_pct.get(code)
        row["liquidity"] = None if pd.isna(liq) else float(liq)
        r15 = rank_today.get(code)
        r15_prev = rank_prev5.get(code)
        row["rank15"] = None if pd.isna(r15) else float(r15)
        row["rank15_prev5"] = None if pd.isna(r15_prev) else float(r15_prev)
        if pd.notna(r15) and pd.notna(r15_prev):
            row["rank_change_5d"] = float(r15_prev - r15)
        else:
            row["rank_change_5d"] = None
        for label, s in returns.items():
            v = s.get(code)
            row[label] = None if pd.isna(v) else float(v)
        row["data_quality_flag"] = "corporate_action" if anomaly_offsets.get(code) else ""
        rows.append(row)

    etf = pd.DataFrame(rows)

    # 名称 / 桶信息
    if not master.empty and "fund_code" in master.columns:
        m_cols = [c for c in ["fund_code", "fund_name", "asset_bucket", "tracking_index",
                              "tracking_index_code", *classification_facts.COLUMNS] if c in master.columns]
        etf = etf.merge(master[m_cols].drop_duplicates(subset=["fund_code"]), on="fund_code", how="left")
    else:
        etf["fund_name"] = ""
        etf["asset_bucket"] = ""
        etf["tracking_index"] = ""
        etf["tracking_index_code"] = ""

    etf["asset_bucket"] = etf["asset_bucket"].fillna("").astype(str)
    etf["fund_name"] = etf["fund_name"].fillna("")
    for column in ("tracking_index", "tracking_index_code"):
        if column not in etf:
            etf[column] = ""
        etf[column] = etf[column].fillna("").astype(str).str.strip()
    etf["tracking_index_status"] = np.where(
        etf["tracking_index"].ne("") & etf["tracking_index_code"].ne(""),
        "present_unverified", "missing",
    )
    etf["classification_status"] = np.where(
        etf["asset_bucket"].ne(""), "classified_unverified", "unclassified",
    )
    if "investment_market" in etf and "investment_direction" in etf:
        complete = (etf["investment_market"].notna() & etf["investment_direction"].notna()
                    & etf["investment_market"].ne("待核实") & etf["investment_direction"].ne("待核实"))
        etf["classification_status"] = np.where(complete, "classified_unverified", "unclassified")
        verified = complete & etf.market_classification_status.eq('verified') & etf.direction_classification_status.eq('verified')
        etf.loc[verified, 'classification_status'] = 'verified'
    etf["bucket_label"] = etf["asset_bucket"].map(BUCKET_LABELS).fillna(etf["asset_bucket"])
    asset_class_map = {k: ASSET_CLASS_LABEL.get(v.get("asset_class", ""), "其他")
                       for k, v in classifier.BUCKET_DEFINITIONS.items()}
    etf["asset_class"] = etf["asset_bucket"].map(asset_class_map).fillna("未分类")
    etf["is_tech"] = etf["investment_direction"].eq('科技')
    # v0.4.3 多主题：按主题关键词打标（兼容旧 parquet 缺列场景）
    etf["theme"] = etf["fund_name"].apply(match_theme)

    numeric_cols = ["rps15", "rps20", "rps60", "rps1", "rps5", "delta_rps15", "liquidity",
                    "rank15", "rank15_prev5", "rank_change_5d",
                    "return_1d", "return_5d", "return_10d", "return_15d", "return_20d", "return_60d"]
    for c in numeric_cols:
        if c in etf.columns:
            etf[c] = pd.to_numeric(etf[c], errors="coerce")

    logger.info(
        "rotation metrics: %d ETFs (rps15 median=%.1f, tech=%d)",
        len(etf), etf["rps15"].median() if etf["rps15"].notna().any() else float("nan"),
        int(etf["is_tech"].sum()),
    )
    return etf

CROSS_ASSET_ORDER = ["A股宽基", "A股行业/主题", "港股", "海外权益", "债券", "商品/黄金", "现金/货币", "其他"]

def _cross_asset_direction(row: Any) -> str:
    """把单只 ETF 归到跨资产方向（消费侧分组，不改任何 Policy）。"""
    if "investment_market" in row and "investment_direction" in row:
        market, direction = row.get("investment_market"), row.get("investment_direction")
        if pd.isna(market) or pd.isna(direction) or market in ("", "待核实") or direction in ("", "待核实"):
            return "其他"
        return f"{market} · {direction}"
    asset_class = str(row.get("asset_class", "") or "")
    bucket = str(row.get("asset_bucket", "") or "")
    name = str(row.get("fund_name", "") or "")
    if asset_class == "现金" or bucket == "money_market":
        return "现金/货币"
    if asset_class == "商品" or bucket in ("commodity_gold", "commodity_futures"):
        return "商品/黄金"
    if asset_class == "债券" or bucket in ("bond_treasury", "bond_credit", "bond_convertible"):
        return "债券"
    if asset_class == "海外" or bucket == "overseas_equity":
        if any(k in name for k in ("港股", "恒生", "香港", "h股", "H股", "港通")):
            return "港股"
        return "海外权益"
    if asset_class == "权益" or bucket in ("industry", "theme", "factor_style"):
        if bucket == "broad_market":
            return "A股宽基"
        return "A股行业/主题"
    return "其他"

def _direction_state(median_rps15: float | None, change_5d: float | None) -> str:
    """把 (RPS15中位, 5日变化) 映射为当前方向描述（消费侧展示）。"""
    if median_rps15 is None:
        return "—"
    if median_rps15 >= 75 and change_5d is not None and change_5d >= 5:
        return "强势上行"
    if median_rps15 >= 60 and change_5d is not None and change_5d > 0:
        return "加速"
    if median_rps15 >= 45:
        return "横盘"
    return "弱势下行"

def cross_asset_direction(etf: pd.DataFrame) -> list[dict[str, Any]]:
    """① 全市场大类资产往哪里动：按跨资产方向聚合。

    每行一个方向，字段：direction / etf_count / median_rps15 / median_rps5 /
    median_rps1 / change_5d / active_ratio / rep_name / direction_state。
    不展示分位数 / 全市场数量。
    """
    if etf.empty:
        return []
    df = etf.copy()
    df["_dir"] = df.apply(_cross_asset_direction, axis=1)
    rows: list[dict[str, Any]] = []
    if "investment_market" in df and "investment_direction" in df:
        order = [f"{market} · {direction}" for market in classification_facts.MARKETS if market != "待核实"
                 for direction in classification_facts.DIRECTIONS if direction != "待核实"] + ["其他"]
    else:
        order = CROSS_ASSET_ORDER
    for d in order:
        sub = df[df["_dir"] == d]
        if sub.empty:
            continue
        rps = pd.to_numeric(sub["rps15"], errors="coerce").dropna()
        rps5 = pd.to_numeric(sub["rps5"], errors="coerce").dropna() if "rps5" in sub.columns else pd.Series(dtype=float)
        rps1 = pd.to_numeric(sub["rps1"], errors="coerce").dropna() if "rps1" in sub.columns else pd.Series(dtype=float)
        chg = pd.to_numeric(sub["rank_change_5d"], errors="coerce").dropna()
        n = len(sub)
        n_active = int((sub.get("trend_state", pd.Series(dtype=str)).astype(str) != "OUT_OF_SCOPE").sum()) \
            if "trend_state" in sub.columns else 0
        rep = sub.copy()
        if "liquidity" in rep.columns:
            rep = rep[pd.to_numeric(rep["liquidity"], errors="coerce").notna()]
        if not rep.empty:
            rep = rep.sort_values("liquidity", ascending=False)
        median = round(float(rps.median()), 1) if not rps.empty else None
        median_rps5 = round(float(rps5.median()), 1) if not rps5.empty else None
        median_rps1 = round(float(rps1.median()), 1) if not rps1.empty else None
        change = round(float(chg.median()), 1) if not chg.empty else None
        rows.append({
            "direction": d,
            "etf_count": n,
            "median_rps15": median,
            "median_rps5": median_rps5,
            "median_rps1": median_rps1,
            "change_5d": change,
            "active_ratio": round(n_active / n, 2) if n else 0,
            "rep_name": str(rep.iloc[0]["fund_name"]) if not rep.empty else "—",
            "direction_state": _direction_state(median, change),
        })
    return rows
