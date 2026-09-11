"""Leader Breakout Event V1 — event construction.

纯只读：从 processed 个股行情 + 申万行业 metrics 重建逐日事实，构造
「主题未确认时龙头个股单点突破」事件及其描述性标签。无 look-ahead：
所有信号只用事件日及以前的数据（滚动窗口按 symbol 顺序向前计算）。

生产一致性：score / watch_level / action 复用 `src.trend_engine` 的算法
（`_score_row` 与 `scoring.score_latest_row` 权重一致，仅跳过 reason 字符串）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.common.paths import processed_dir, raw_dir, selection_universe_path, sw_industry_processed_dir
from src.selection.universe import load_universe_items
from src.sw_industry_rps import confirmation as sw_confirmation
from src.sw_industry_rps import storage as sw_storage
from src.trend_engine import engine as te
from src.trend_engine import inputs as trend_inputs

from .spec import LeaderBreakoutSpec

logger = logging.getLogger("research.leader_breakout.events")

# 算法常量（非策略参数）：事件日之前至少需要的行情行数（保证均线有效）
MIN_HISTORY_ROWS = 20


# ── 生产一致的逐行评分（与 scoring.score_latest_row 权重完全一致） ──
def _f(row: pd.Series, col: str) -> Any:
    v = row.get(col)
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except TypeError:
        return v
    return v


def _as_float(row: pd.Series, col: str) -> float | None:
    v = _f(row, col)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _as_bool(row: pd.Series, col: str) -> bool | None:
    v = _f(row, col)
    if v is None:
        return None
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes")
    try:
        return bool(v)
    except (TypeError, ValueError):
        return None


def _score_row(row: pd.Series) -> int:
    close = _as_float(row, "close")
    if close is None:
        return 0
    score = 0
    for ma_col, w in (("ma20", 15), ("ma60", 15), ("ma120", 10)):
        v = _as_float(row, ma_col)
        if v is not None and close >= v:
            score += w
    for slope_col, w in (("ma20_slope", 10), ("ma60_slope", 10)):
        v = _as_float(row, slope_col)
        if v is not None and v > 0:
            score += w
    hist = _as_float(row, "macd_hist")
    if hist is not None and hist > 0:
        score += 15
    rsi = _as_float(row, "rsi14")
    if rsi is not None:
        if 50 <= rsi <= 70:
            score += 15
        elif rsi > 70:
            score += 5
        elif 40 <= rsi < 50:
            score += 5
    rs = _as_float(row, "relative_strength_20d")
    if rs is not None and rs > 0:
        score += 10
    return score


# ── 历史构建 ─────────────────────────────────────────────────────
def build_stock_history(stock_items: list[Any]) -> dict[str, pd.DataFrame]:
    """对每个股票资产构建逐日 _score / _watch_level / _action。"""
    out: dict[str, pd.DataFrame] = {}
    for item in stock_items:
        asset = item.asset
        path = processed_dir() / f"{asset.market}_{asset.symbol}.csv"
        if not path.exists():
            continue
        try:
            df = pd.read_csv(path, parse_dates=["date"])
        except Exception as e:  # noqa: BLE001
            logger.warning("read processed %s failed: %s", path.name, e)
            continue
        if df.empty:
            continue
        df = df.sort_values("date").reset_index(drop=True)
        scores: list[int] = []
        wls: list[str] = []
        acts: list[str] = []
        prev: int | None = None
        for _, row in df.iterrows():
            s = _score_row(row)
            wl = te.calc_watch_level(
                s, _as_float(row, "relative_strength_20d"),
                _as_float(row, "ma20"), _as_float(row, "ma60"), _as_float(row, "volume_ratio"))
            change = te.calc_change(s, prev, prev is not None)
            act = te.calc_action(
                s, wl, _as_float(row, "relative_strength_20d"), _as_float(row, "volume_ratio"),
                _as_bool(row, "price_near_ma20"), _as_float(row, "drawdown_from_high"), change)
            scores.append(s)
            wls.append(wl)
            acts.append(act)
            prev = s
        df["_score"] = scores
        df["_watch_level"] = wls
        df["_action"] = acts
        out[asset.symbol] = df
    logger.info("stock history built: %d symbols", len(out))
    return out


def _theme_memberships(spec: LeaderBreakoutSpec) -> dict[str, list[tuple[str, str, str]]]:
    items = load_universe_items(selection_universe_path())
    stocks = trend_inputs.stock_items(items)
    out: dict[str, list[tuple[str, str, str]]] = {t: [] for t in spec.event.themes}
    seen: dict[str, set[str]] = {t: set() for t in spec.event.themes}
    for it in stocks:
        theme = it.theme
        if theme not in out:
            continue
        if it.participation in spec.event.exclude_participation:
            continue
        sym = it.asset.symbol
        if sym in seen[theme]:
            continue
        seen[theme].add(sym)
        out[theme].append((sym, it.asset.name, it.tier))
    return out


def theme_confirmation_series(
    spec: LeaderBreakoutSpec,
    metrics: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """主题确认（行业 Gate 历史重建）：theme_confirmed = 任一焦点行业 strength ∈ confirm_states。"""
    if metrics is None:
        metrics = sw_storage.load_metrics(sw_industry_processed_dir())
    if metrics.empty:
        return pd.DataFrame(columns=["theme", "trade_date", "theme_confirmed"])
    code2theme: dict[str, str] = {}
    for fi in sw_confirmation.FOCUS_INDUSTRIES:
        if fi["theme"] in spec.event.themes:
            code2theme[fi["code"]] = fi["theme"]
    m = metrics[metrics["industry_code"].isin(code2theme)].copy()
    if m.empty:
        return pd.DataFrame(columns=["theme", "trade_date", "theme_confirmed"])
    m["theme"] = m["industry_code"].map(code2theme)
    m["strength"] = m["RPS15"].map(sw_confirmation.industry_strength_level)
    m["confirmed"] = m["strength"].isin(spec.theme_state.confirm_states)
    conf = (m.groupby(["theme", "trade_date"], as_index=False)["confirmed"].any()
            .rename(columns={"confirmed": "theme_confirmed"}))
    logger.info("theme confirmation series: %d (theme,date) rows", len(conf))
    return conf


# ── 价格簿（open/close per symbol，逐 symbol 日历） ──────────────
class PricePanel:
    def __init__(self, symbols: list[str]):
        self._open: dict[str, np.ndarray] = {}
        self._close: dict[str, np.ndarray] = {}
        self._dates: dict[str, np.ndarray] = {}
        for sym in symbols:
            d = _load_ohlc(sym)
            if d is None or d.empty:
                continue
            self._dates[sym] = d["date"].to_numpy()
            self._open[sym] = d["open"].to_numpy(dtype=float)
            self._close[sym] = d["close"].to_numpy(dtype=float)

    def has(self, sym: str) -> bool:
        return sym in self._close

    def _idx_at_or_before(self, sym: str, date: pd.Timestamp) -> int | None:
        arr = self._dates.get(sym)
        if arr is None:
            return None
        i = int(np.searchsorted(arr, np.datetime64(date, "ns"), side="right") - 1)
        return i if i >= 0 else None

    def forward(self, sym: str, event_date: pd.Timestamp, horizon: int) -> dict[str, Any]:
        """T+1 开盘入场，持有 horizon 个交易日；返回收益/路径/日期。"""
        out: dict[str, Any] = {"ret": None, "mfe": None, "mae": None,
                               "entry_date": None, "exit_date": None}
        if sym not in self._close:
            return out
        i = self._idx_at_or_before(sym, event_date)
        if i is None:
            return out
        e = i + 1
        x = e + horizon
        if x >= len(self._close[sym]):
            return out
        entry_open = self._open[sym][e]
        if not np.isfinite(entry_open) or entry_open <= 0:
            return out
        window = self._close[sym][e:x + 1]
        window = window[np.isfinite(window)]
        if window.size < 2:
            return out
        out["ret"] = float(self._close[sym][x] / entry_open - 1.0)
        out["mfe"] = float(window.max() / entry_open - 1.0)
        out["mae"] = float(window.min() / entry_open - 1.0)
        out["entry_date"] = pd.Timestamp(self._dates[sym][e])
        out["exit_date"] = pd.Timestamp(self._dates[sym][x])
        return out

    def daily_returns(self, sym: str, entry_date: pd.Timestamp, exit_date: pd.Timestamp) -> pd.Series:
        """持仓区间内的逐日 close-to-close 收益（index=日期）。"""
        if sym not in self._close:
            return pd.Series(dtype=float)
        d = pd.DataFrame({"date": self._dates[sym], "close": self._close[sym]}).set_index("date")
        d = d.loc[pd.Timestamp(entry_date):pd.Timestamp(exit_date)]
        if len(d) < 2:
            return pd.Series(dtype=float)
        return d["close"].pct_change().dropna()


def _load_ohlc(symbol: str) -> pd.DataFrame | None:
    for market in ("CN", "HK", "US"):
        p = raw_dir() / f"{market}_{symbol}.csv"
        if p.exists():
            try:
                d = pd.read_csv(p, parse_dates=["date"])
            except Exception:  # noqa: BLE001
                return None
            if d.empty or "open" not in d.columns or "close" not in d.columns:
                return None
            return d.sort_values("date").reset_index(drop=True)
    return None


# ── 事件构造 ─────────────────────────────────────────────────────
def _drawdown_bucket(x: float | None, buckets: tuple[float, ...]) -> str:
    if x is None or not np.isfinite(x):
        return "na"
    for b in sorted(buckets):
        if x < b:
            return f"<{int(b * 100)}%"
    return f">={int(max(buckets) * 100)}%"


def build_events(
    spec: LeaderBreakoutSpec,
    *,
    history: dict[str, pd.DataFrame] | None = None,
    conf: pd.DataFrame | None = None,
    panel: PricePanel | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    memberships = _theme_memberships(spec)
    all_symbols = sorted({s for members in memberships.values() for s, _, _ in members})
    if history is None:
        items = load_universe_items(selection_universe_path())
        history = build_stock_history(trend_inputs.stock_items(items))
    if conf is None:
        conf = theme_confirmation_series(spec)
    if panel is None:
        panel = PricePanel(all_symbols)

    conf_map: dict[tuple[str, pd.Timestamp], bool] = {}
    if not conf.empty:
        for r in conf.itertuples(index=False):
            conf_map[(str(r.theme), pd.Timestamp(r.trade_date))] = bool(r.theme_confirmed)

    # ── 构建 (theme, symbol) 长面板 ──
    rows: list[pd.DataFrame] = []
    for theme, members in memberships.items():
        for sym, name, tier in members:
            h = history.get(sym)
            if h is None or h.empty:
                continue
            sub = h[["date", "open", "close", "ma20", "ma60", "ma120",
                     "relative_strength_20d", "drawdown_from_high", "volume_ratio",
                     "price_near_ma20", "_score", "_watch_level", "_action"]].copy()
            sub["symbol"] = sym
            sub["name"] = name
            sub["theme"] = theme
            sub["tier"] = tier
            rows.append(sub)
    if not rows:
        return pd.DataFrame(), {"n_stocks": 0, "themes": list(memberships)}
    panel_df = pd.concat(rows, ignore_index=True)
    panel_df = panel_df.sort_values(["theme", "symbol", "date"]).reset_index(drop=True)

    # 横截面排名 / 广度（按 theme,date）
    panel_df["rank_trend"] = (panel_df.groupby(["theme", "date"])["_score"]
                              .rank(ascending=False, method="min"))
    breadth_thr = spec.tags.breadth_score_threshold
    grp = panel_df.groupby(["theme", "date"])
    panel_df["theme_breadth"] = grp["_score"].transform(lambda s: float((s >= breadth_thr).mean()))
    panel_df["theme_median_score"] = grp["_score"].transform("median")
    panel_df["n_theme_stocks"] = grp["_score"].transform("size")

    # 逐 symbol 滚动标签（无 look-ahead）
    panel_df = panel_df.sort_values(["symbol", "date"]).reset_index(drop=True)
    panel_df["_above_ma60"] = panel_df["close"] >= panel_df["ma60"]
    prev_above = (panel_df.groupby("symbol")["_above_ma60"].shift(1)
                  .fillna(False).astype(bool))
    panel_df["_cross_ma60"] = panel_df["_above_ma60"].astype(bool) & (~prev_above)
    panel_df["ma60_cross_within_10d"] = (
        panel_df.groupby("symbol")["_cross_ma60"]
        .transform(lambda s: s.rolling(spec.tags.ma60_cross_window, min_periods=1).max().astype(bool)))
    panel_df["above_ma60"] = panel_df["_above_ma60"]
    panel_df["above_ma120"] = panel_df["close"] >= panel_df["ma120"]
    panel_df["_high20"] = panel_df.groupby("symbol")["close"].transform(lambda s: s.rolling(20, min_periods=1).max())
    panel_df["new_high_20d"] = panel_df["close"] >= panel_df["_high20"]
    panel_df["_prev_score"] = panel_df.groupby("symbol")["_score"].shift(1)
    panel_df["_row_idx"] = panel_df.groupby("symbol").cumcount()
    panel_df = panel_df.sort_values(["theme", "symbol", "date"]).reset_index(drop=True)

    thr = spec.event.score_threshold
    events: list[dict[str, Any]] = []
    for (_theme, _sym), g in panel_df.groupby(["theme", "symbol"], sort=False):
        g = g.sort_values("date")
        prev_scores = g["_prev_score"].to_numpy()
        scores = g["_score"].to_numpy()
        ranks = g["rank_trend"].to_numpy()
        rs = pd.to_numeric(g["relative_strength_20d"], errors="coerce").to_numpy()
        dates = g["date"].to_numpy()
        row_idx = g["_row_idx"].to_numpy()
        confirmed = np.array([conf_map.get((str(_theme), pd.Timestamp(d)), None) for d in dates], dtype=object)
        last_event_i: int | None = None
        reset_seen = True
        for i in range(len(g)):
            if last_event_i is not None and np.isfinite(scores[i]) and scores[i] < thr:
                reset_seen = True
            if row_idx[i] < MIN_HISTORY_ROWS:
                continue
            if not np.isfinite(prev_scores[i]) or not np.isfinite(scores[i]):
                continue
            crossing = prev_scores[i] < thr <= scores[i]
            unconfirmed = confirmed[i] is False
            if not (crossing and ranks[i] == spec.event.rank_trend and unconfirmed):
                continue
            if spec.event.require_rs20_positive and not (np.isfinite(rs[i]) and rs[i] > 0):
                continue
            gap_ok = last_event_i is None or (i - last_event_i) >= spec.event.cooldown_trading_days
            reset_ok = (not spec.event.cooldown_reset_required) or reset_seen
            if not (gap_ok and reset_ok):
                continue
            row = g.iloc[i]
            events.append({
                "event_date": pd.Timestamp(row["date"]),
                "symbol": str(_sym),
                "name": str(row["name"]),
                "theme": str(_theme),
                "tier": str(row["tier"]),
                "close": float(row["close"]),
                "score_trend": int(scores[i]),
                "prev_score": int(prev_scores[i]),
                "rs20": float(rs[i]) if np.isfinite(rs[i]) else None,
                "rank_trend": int(ranks[i]),
                "watch_level": str(row["_watch_level"]),
                "action": str(row["_action"]),
                "drawdown_from_high": float(row["drawdown_from_high"]) if pd.notna(row["drawdown_from_high"]) else None,
                "drawdown_bucket": _drawdown_bucket(
                    float(row["drawdown_from_high"]) if pd.notna(row["drawdown_from_high"]) else None,
                    spec.tags.drawdown_buckets),
                "above_ma60": bool(row["above_ma60"]),
                "ma60_cross_within_10d": bool(row["ma60_cross_within_10d"]),
                "above_ma120": bool(row["above_ma120"]),
                "new_high_20d": bool(row["new_high_20d"]),
                "theme_breadth": float(row["theme_breadth"]) if pd.notna(row["theme_breadth"]) else None,
                "theme_median_score": float(row["theme_median_score"]) if pd.notna(row["theme_median_score"]) else None,
                "n_theme_stocks": int(row["n_theme_stocks"]),
            })
            last_event_i = i
            reset_seen = False

    if not events:
        return pd.DataFrame(), {"n_stocks": len(all_symbols), "themes": list(memberships)}

    ev = pd.DataFrame(events)
    # 跨主题注册去重：同一 (symbol,event_date) 只留一条（优先主题中位分更高者）
    ev = ev.sort_values(["symbol", "event_date", "theme_median_score"], ascending=[True, True, False])
    ev = ev.drop_duplicates(subset=["symbol", "event_date"], keep="first").reset_index(drop=True)

    if start_date:
        ev = ev[ev["event_date"] >= pd.Timestamp(start_date)]
    if end_date:
        ev = ev[ev["event_date"] <= pd.Timestamp(end_date)]
    ev = ev.sort_values("event_date").reset_index(drop=True)

    # ── 前向收益 + 分层基准 ──
    horizons = spec.returns.horizons
    primary = spec.returns.primary_horizon
    theme_syms: dict[str, list[str]] = {t: [s for s, _, _ in m] for t, m in memberships.items()}

    ret_rows: list[dict[str, Any]] = []
    for _, e in ev.iterrows():
        rec: dict[str, Any] = {}
        sym = str(e["symbol"])
        theme = str(e["theme"])
        ed = pd.Timestamp(e["event_date"])
        for h in horizons:
            fwd = panel.forward(sym, ed, h)
            rec[f"ret_{h}"] = fwd["ret"]
            if h == primary:
                rec["mae_primary"] = fwd["mae"]
                rec["mfe_primary"] = fwd["mfe"]
                rec["entry_date"] = fwd["entry_date"]
                rec["exit_date"] = fwd["exit_date"]
        # 分层基准：同主题个股池中位
        for h in horizons:
            vals = []
            for m in theme_syms.get(theme, []):
                v = panel.forward(m, ed, h)["ret"]
                if v is not None:
                    vals.append(v)
            rec[f"bench_{h}"] = float(np.median(vals)) if vals else None
            rec[f"exc_{h}"] = (rec.get(f"ret_{h}") - rec[f"bench_{h}"]) if (rec.get(f"ret_{h}") is not None and rec[f"bench_{h}"] is not None) else None
        ret_rows.append(rec)

    ret_df = pd.DataFrame(ret_rows)
    ev = pd.concat([ev, ret_df], axis=1)

    # ── Q2：等待主题确认 ──
    ev = _attach_wait_confirmation(ev, conf, panel, spec)
    meta = {
        "n_stocks": len(all_symbols),
        "themes": list(memberships),
        "n_events": int(len(ev)),
        "n_symbols": int(ev["symbol"].nunique()),
    }
    logger.info("events built: %d events / %d symbols", meta["n_events"], meta["n_symbols"])
    return ev, meta


def _attach_wait_confirmation(
    ev: pd.DataFrame,
    conf: pd.DataFrame,
    panel: PricePanel,
    spec: LeaderBreakoutSpec,
) -> pd.DataFrame:
    """事件后首个主题确认日 → 从确认次日开盘入场的同 Horizon 收益。"""
    primary = spec.returns.primary_horizon
    out_confirm: list[Any] = []
    out_ret: list[Any] = []
    out_entry: list[Any] = []
    out_exit: list[Any] = []
    if conf.empty:
        ev = ev.copy()
        ev["confirm_date"] = None
        ev["wait_ret_primary"] = None
        ev["wait_entry_date"] = None
        ev["wait_exit_date"] = None
        ev["has_confirm_within_lookahead"] = False
        return ev
    conf_by_theme: dict[str, np.ndarray] = {}
    for theme, g in conf[conf["theme_confirmed"]].groupby("theme"):
        conf_by_theme[str(theme)] = np.sort(g["trade_date"].to_numpy())
    for _, e in ev.iterrows():
        theme = str(e["theme"])
        ed = pd.Timestamp(e["event_date"])
        arr = conf_by_theme.get(theme)
        cd = None
        if arr is not None and len(arr):
            j = int(np.searchsorted(arr, np.datetime64(ed, "ns"), side="right"))
            if j < len(arr):
                cand = pd.Timestamp(arr[j])
                gap = int(np.busday_count(ed.date(), cand.date()))
                if gap <= spec.theme_state.confirm_lookahead_days:
                    cd = cand
        out_confirm.append(cd)
        wr = None
        we = None
        wx = None
        if cd is not None:
            fwd = panel.forward(str(e["symbol"]), cd, primary)
            wr = fwd["ret"]
            we = fwd["entry_date"]
            wx = fwd["exit_date"]
        out_ret.append(wr)
        out_entry.append(we)
        out_exit.append(wx)
    ev = ev.copy()
    ev["confirm_date"] = out_confirm
    ev["wait_ret_primary"] = out_ret
    ev["wait_entry_date"] = out_entry
    ev["wait_exit_date"] = out_exit
    ev["has_confirm_within_lookahead"] = ev["confirm_date"].notna()
    return ev


def build_price_panel(symbols: list[str]) -> PricePanel:
    return PricePanel(symbols)
