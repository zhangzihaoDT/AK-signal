"""Aug2026 研究：固定池 + 全市场面板构建。

收益源规则（v1 固化，来自用户确认）：
- 特征源：2026-07-31 processed / historical_signals（trend_score/MA/RSI/momentum）
- 收益源：2026-07-31 close → raw qfq；2026-08-28 close → raw qfq（processed close 不参与主收益）
- 复权：一律 qfq，写入 provenance
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.common.paths import processed_dir, raw_dir
from src.trend_engine import engine as te
from src.research.expression_regime.history import score_row

from . import STUDY_DIR, WINDOW_START, WINDOW_END, ADJUST

logger = logging.getLogger("research.aug2026.panel")

FIXED_POOL_FEATURE_DATE = "2026-07-31"


# ── 收益价格：raw qfq（+ tx 补缺） ──────────────────────────────

def load_raw_close(code: str) -> pd.DataFrame:
    """读取 data/raw/CN_{code}.csv 的 date/close（前复权序列）。"""
    p = raw_dir() / f"CN_{code}.csv"
    if not p.exists():
        return pd.DataFrame(columns=["date", "close"])
    try:
        df = pd.read_csv(p, parse_dates=["date"])
        df = df[["date", "close"]].dropna(subset=["close"])
        return df.sort_values("date").reset_index(drop=True)
    except Exception as e:
        logger.warning("load_raw_close %s failed: %s", code, e)
        return pd.DataFrame(columns=["date", "close"])


def tx_close_for(code: str) -> pd.DataFrame:
    """用 tx 源抓取该股票的 close 序列（qfq），供 raw 缺 8/28 时补。"""
    import akshare as ak

    sym = code
    if code.startswith(("60", "68", "51", "52", "56", "58")) or code.startswith("6"):
        sym = f"sh{code}"
    elif code.startswith(("00", "30", "20")) or code.startswith(("0", "1", "2", "3")):
        sym = f"sz{code}"
    try:
        df = ak.stock_zh_a_hist_tx(
            symbol=sym, start_date="20260701", end_date="20260828", adjust="qfq"
        )
        if df is None or df.empty:
            return pd.DataFrame(columns=["date", "close"])
        return pd.DataFrame({
            "date": pd.to_datetime(df["date"]),
            "close": pd.to_numeric(df["close"], errors="coerce"),
        }).dropna()
    except Exception as e:  # noqa: BLE001
        logger.warning("tx_close_for %s failed: %s", code, e)
        return pd.DataFrame(columns=["date", "close"])


def _resolve_close(code: str, target: str, tx_fallback: dict[str, pd.DataFrame]) -> float | None:
    """优先 raw，缺则 tx 补。返回 target 日期 close。"""
    df = load_raw_close(code)
    hit = df[df["date"] == pd.Timestamp(target)]
    if not hit.empty:
        v = float(hit["close"].iloc[0])
        if not np.isnan(v):
            return v
    tx = tx_fallback.get(code)
    if tx is not None and not tx.empty:
        hit = tx[tx["date"] == pd.Timestamp(target)]
        if not hit.empty:
            return float(hit["close"].iloc[0])
    return None


def fixed_pool_returns(codes: list[str]) -> pd.DataFrame:
    """固定池 8 月收益：close_8/28 ÷ close_7/31 − 1（tx qfq，与全市场同源）。

    收益源统一为 tx（akshare stock_zh_a_hist_tx qfq），raw 仅用于交叉验证记录。
    质量标记：start/end_trade_date、stale_days、full_month_sample、suspended_end。
    """
    rows: list[dict[str, Any]] = []
    for code in codes:
        tx = tx_close_for(code)
        if tx.empty:
            rows.append({"code": code, "return_aug": None, "data_status": "missing_price"})
            continue

        # raw 交叉验证记录（同 code 有 raw 时记录差异，用于审计）
        raw = load_raw_close(code)
        raw_28 = None
        if not raw.empty:
            hit = raw[raw["date"] == pd.Timestamp(WINDOW_END)]
            if not hit.empty:
                raw_28 = float(hit["close"].iloc[0])

        start_row = tx[tx["date"] == pd.Timestamp(WINDOW_START)]
        end_row = tx[tx["date"] == pd.Timestamp(WINDOW_END)]
        if start_row.empty or end_row.empty:
            rows.append({"code": code, "return_aug": None, "data_status": "incomplete_window"})
            continue
        start = float(start_row["close"].iloc[0])
        end = float(end_row["close"].iloc[0])

        in_window = tx[(tx["date"] >= pd.Timestamp(WINDOW_START)) & (tx["date"] <= pd.Timestamp(WINDOW_END))]
        n_dates = len(in_window)
        last_date = tx["date"].max()
        end_stale_days = 0
        suspended_end = False
        if last_date < pd.Timestamp(WINDOW_END):
            end_stale_days = int(np.busday_count(
                np.datetime64(last_date.date()), np.datetime64(WINDOW_END), weekmask="1111100"))
            suspended_end = True

        rows.append({
            "code": code,
            "start_close": start,
            "end_close": end,
            "raw_close_8_28": raw_28,
            "raw_vs_tx_pct": round(raw_28 / end - 1.0, 4) if raw_28 else None,
            "start_trade_date": WINDOW_START,
            "end_trade_date": WINDOW_END,
            "return_aug": end / start - 1.0,
            "n_dates_in_window": int(n_dates),
            "end_stale_days": int(end_stale_days),
            "full_month_sample": bool(n_dates >= 15),
            "suspended_end": bool(suspended_end),
            "data_status": "ok",
        })
    return pd.DataFrame(rows)


# ── 特征：7/31（replay + processed 补算） ──────────────────────

def _processed_row(code: str, target: str = FIXED_POOL_FEATURE_DATE) -> pd.Series | None:
    p = processed_dir() / f"CN_{code}.csv"
    if not p.exists():
        return None
    try:
        df = pd.read_csv(p, parse_dates=["date"])
    except Exception:
        return None
    sub = df[df["date"] <= pd.Timestamp(target)]
    if sub.empty:
        return None
    return sub.iloc[-1]


def compute_feature_for(code: str) -> dict[str, Any]:
    """从 processed CSV 的 7/31 行补算 trend_score/watch_level 等特征。"""
    row = _processed_row(code)
    if row is None:
        return {"code": code, "feature_status": "missing"}

    def _f(col: str) -> float | None:
        v = row.get(col)
        try:
            if v is None or pd.isna(v):
                return None
            return float(v)
        except Exception:
            return None

    rs = _f("relative_strength_20d")
    close, ma60 = _f("close"), _f("ma60")
    bias = (close / ma60 - 1.0) * 100.0 if (close is not None and ma60 and ma60 > 0) else None
    position = None
    if bias is not None:
        position = "LOW" if bias <= -5 else ("HIGH" if bias >= 10 else "MID")
    s = score_row(row)
    wl = te.calc_watch_level(s, rs, _f("ma20"), _f("ma60"), _f("volume_ratio"))
    return {
        "code": code,
        "feature_status": "computed",
        "trend_score": float(s),
        "watch_level": wl,
        "return_20d": _f("return_20d"),
        "rsi14": _f("rsi14"),
        "ma20": _f("ma20"),
        "ma60": _f("ma60"),
        "close_7_31": close,
        "ma60_bias_pct": round(bias, 2) if bias is not None else None,
        "position_level": position,
    }


def enrich_position_features(panel: pd.DataFrame) -> pd.DataFrame:
    """为固定池全部 51 只补充 7/31 的 MA60-bias / position / 20D动量 / RSI。

    这些指标 replay parquet 不含，统一从 processed CSV 7/31 行读取（与 production 同源）。
    """
    out = panel.copy()
    rows: list[dict[str, Any]] = []
    for code in panel["code"]:
        row = _processed_row(code)
        base = {"code": code}

        def _f(col: str) -> float | None:
            if row is None:
                return None
            v = row.get(col)
            try:
                if v is None or pd.isna(v):
                    return None
                return float(v)
            except Exception:
                return None

        close, ma60 = _f("close"), _f("ma60")
        bias = (close / ma60 - 1.0) * 100.0 if (close is not None and ma60 and ma60 > 0) else None
        position = None
        if bias is not None:
            position = "LOW" if bias <= -5 else ("HIGH" if bias >= 10 else "MID")
        base["ma60_bias_pct"] = round(bias, 2) if bias is not None else None
        base["position_level"] = position
        base["return_20d_7_31"] = _f("return_20d")
        base["rsi14_7_31"] = _f("rsi14")
        base["close_7_31"] = close
        base["ma20_7_31"] = _f("ma20")
        base["ma60_7_31"] = ma60
        rows.append(base)
    feat = pd.DataFrame(rows)
    for col in ["ma60_bias_pct", "position_level", "return_20d_7_31", "rsi14_7_31",
                "close_7_31", "ma20_7_31", "ma60_7_31"]:
        mapped = out["code"].map(feat.set_index("code")[col])
        out[col] = out[col].fillna(mapped) if col in out.columns else mapped
    return out


def build_fixed_pool_panel(
    codes: list[str],
    replay_features: pd.DataFrame | None,
) -> pd.DataFrame:
    """固定池面板：收益（raw qfq）+ 7/31 特征（replay 48 + processed 补 3）。

    Args:
        codes: selection_universe 的 51 只 CN 个股
        replay_features: historical_signals parquet 的 stock 行（entity_code → trend_score/trend_state）
    """
    ret = fixed_pool_returns(codes)

    feat_rows: list[dict[str, Any]] = []
    replay_by_code = {}
    if replay_features is not None and not replay_features.empty:
        for _, r in replay_features.iterrows():
            replay_by_code[str(r["entity_code"])] = {
                "trend_score": r.get("trend_score"),
                "watch_level": r.get("trend_state"),  # replay 用 trend_state（S/A/B/C）表等级
                "selection_status": r.get("selection_status"),
                "recommended_action": r.get("recommended_action"),
            }

    for code in codes:
        base = {"code": code}
        rp = replay_by_code.get(code)
        if rp is not None:
            base.update(rp)
            base["feature_status"] = "replay"
        else:
            base.update(compute_feature_for(code))
        feat_rows.append(base)
    feat = pd.DataFrame(feat_rows)

    panel = ret.merge(feat, on="code", how="left")
    return panel


# ── 全市场面板 ─────────────────────────────────────────────────

def build_market_panel(daily: pd.DataFrame) -> pd.DataFrame:
    """全市场面板：每只股票的 8 月收益 + 质量标记。

    daily: market_daily parquet（tx qfq，含 code/date/close）。
    """
    if daily is None or daily.empty:
        return pd.DataFrame()
    df = daily.copy()
    df["date"] = pd.to_datetime(df["date"])

    start = df[df["date"] == pd.Timestamp(WINDOW_START)][["code", "close"]].rename(columns={"close": "start_close"})
    end = df[df["date"] == pd.Timestamp(WINDOW_END)][["code", "close"]].rename(columns={"close": "end_close"})

    # 每只股票的 8 月交易日数
    inw = df[(df["date"] >= pd.Timestamp(WINDOW_START)) & (df["date"] <= pd.Timestamp(WINDOW_END))]
    nd = inw.groupby("code")["date"].nunique().rename("n_dates_in_window")
    # 每只股票最后交易日期
    lastd = df.groupby("code")["date"].max().rename("last_trade_date")
    # 首次出现日期（用于判断次新）
    firstd = df.groupby("code")["date"].min().rename("first_trade_date")

    panel = start.merge(end, on="code", how="outer").merge(nd, on="code", how="left").merge(lastd, on="code", how="left").merge(firstd, on="code", how="left")
    panel["return_aug"] = panel["end_close"] / panel["start_close"] - 1.0
    panel["start_trade_date"] = WINDOW_START
    panel["end_trade_date"] = WINDOW_END
    panel["full_month_sample"] = panel["n_dates_in_window"] >= 15
    panel["ipo_during_month"] = (panel["first_trade_date"] > pd.Timestamp(WINDOW_START)) | (panel["n_dates_in_window"] < 15)
    panel["suspended_end"] = panel["last_trade_date"] < pd.Timestamp(WINDOW_END)
    panel["end_stale_days"] = panel.apply(
        lambda r: int(np.busday_count(np.datetime64(r["last_trade_date"].date()), np.datetime64(WINDOW_END), weekmask="1111100"))
        if pd.notna(r["last_trade_date"]) and r["last_trade_date"] < pd.Timestamp(WINDOW_END) else 0, axis=1)
    panel["source"] = "tx"
    panel["adjust"] = ADJUST
    return panel


def save_panel(df: pd.DataFrame, name: str) -> Path:
    out = STUDY_DIR / f"{name}.parquet"
    df.to_parquet(out, index=False)
    logger.info("saved %s: %s (%d rows)", name, out, len(df))
    return out
