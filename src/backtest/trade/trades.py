"""
逐笔交易模拟（v0.5.2 第一轮）。

模型：独立等名义本金，每笔交易 1 个单位，不建立共享现金账户。
只回答「同一入场规则下，哪种退出策略更有效」。

冻结约束：
  - 持仓期间再次出现 entry 不重复开仓；
  - Exit 是 Strategy Policy 动作，不是 Selection 的 SELL；
  - 信号日无下一交易日价格 → 订单标记 unfilled；
  - 停牌 / 缺失开盘价 / 不可成交显式记录；
  - fixed_horizon 按交易日；MA20 只用判断日当日及以前数据；
  - 手续费 / 滑点保留配置字段（第一版默认 0）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.common.paths import raw_dir
from src.research.replay import engine as replay_engine
from .strategy import entry as entry_mod
from .strategy import exit as exit_mod
from .execution.next_open import next_open

TRADE_COLUMNS = [
    "trade_id", "entity_code", "entity_name", "theme", "exit_policy",
    "entry_signal_date", "entry_fill_date", "entry_fill_price",
    "entry_status", "entry_unfilled_reason",
    "exit_signal_date", "exit_fill_date", "exit_fill_price",
    "exit_status", "exit_reason",
    "return_pct", "holding_days", "fee_pct", "slippage_pct",
    "universe_mode", "universe_size", "universe_config_hash", "universe_hash",
    "entry_score", "strategy_id",
]


def build_etf_pivots(cache: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    """ETF open/close pivot（全市场一次性构建，避免逐实体过滤 combined）。"""
    combined = cache.get("combined")
    if combined is None or combined.empty:
        return None
    combined = combined.copy()
    combined["date"] = pd.to_datetime(combined["date"], errors="coerce")
    combined = combined.dropna(subset=["date"])
    open_pivot = combined.pivot_table(index="date", columns="fund_code", values="open", aggfunc="last")
    close_pivot = combined.pivot_table(index="date", columns="fund_code", values="close", aggfunc="last")
    return open_pivot, close_pivot


def load_entity_prices(
    entity_type: str,
    code: str,
    *,
    etf_pivots: tuple[pd.DataFrame, pd.DataFrame] | None = None,
) -> tuple[pd.Series, pd.Series, pd.Index] | None:
    """返回 (open_series, close_series, trading_dates)。"""
    if entity_type == "etf":
        if etf_pivots is None:
            return None
        open_pivot, close_pivot = etf_pivots
        if str(code) not in open_pivot.columns:
            return None
        return open_pivot[str(code)], close_pivot[str(code)], open_pivot.index
    if entity_type == "stock":
        path: Path = raw_dir() / f"CN_{code}.csv"
        if not path.exists():
            return None
        df = pd.read_csv(path, parse_dates=["date"]).dropna(subset=["date"]).sort_values("date")
        if df.empty:
            return None
        return (df.set_index("date")["open"], df.set_index("date")["close"], df["date"].values)
    return None


def build_exit_map(signals: pd.DataFrame) -> dict[str, list[str]]:
    """全实体退出事件日期（一次提取，避免逐实体重复计算）。"""
    from src.research.event_study.events import extract_events
    ev = extract_events(signals, layers="1")
    ev = ev[ev["event_type"] == "exit"] if not ev.empty else ev
    out: dict[str, list[str]] = {}
    if not ev.empty:
        for code, g in ev.groupby("entity_code"):
            out[str(code)] = sorted(g["trade_date"].astype(str).tolist())
    return out


def _biz_days_between(a: Any, b: Any) -> int:
    try:
        s = pd.Timestamp(a).strftime("%Y-%m-%d")
        e = pd.Timestamp(b).strftime("%Y-%m-%d")
        return int(np.busday_count(np.datetime64(s), np.datetime64(e), weekmask="1111100"))
    except Exception:
        return 0


def _num(v: Any) -> float | None:
    if v is None or (isinstance(v, float) and v != v):
        return None
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None


def _trade_return(buy_price: float, sell_price: float, fee: float, slippage: float) -> float:
    buy_eff = buy_price * (1.0 + slippage)
    sell_eff = sell_price * (1.0 - slippage)
    # fee 单位为 % of notional（单边），双边合计扣除 fee * 2（百分点）
    return round((sell_eff / buy_eff - 1.0) * 100.0 - fee * 2.0, 4)


def _unfilled_entry(
    tid: int, code: str, name: str, theme: str, policy: str, esd: str, reason: str,
    fee: float, slippage: float,
    uni_mode: str = "", uni_size: int = 0, uni_cfg_hash: str = "",
    uni_hash: str = "", strategy_id: str = "",
) -> dict[str, Any]:
    return {
        "trade_id": tid, "entity_code": code, "entity_name": name, "theme": theme,
        "exit_policy": policy,
        "entry_signal_date": esd, "entry_fill_date": "", "entry_fill_price": None,
        "entry_status": "unfilled", "entry_unfilled_reason": reason,
        "exit_signal_date": "", "exit_fill_date": "", "exit_fill_price": None,
        "exit_status": "", "exit_reason": "",
        "return_pct": None, "holding_days": None, "fee_pct": fee, "slippage_pct": slippage,
        "universe_mode": uni_mode, "universe_size": uni_size,
        "universe_config_hash": uni_cfg_hash, "universe_hash": uni_hash,
        "entry_score": None, "strategy_id": strategy_id,
    }


def build_exit_configs(
    policies: tuple[str, ...],
    *,
    horizon: int = 20,
    ma_window: int = 20,
    fee: float = 0.0,
    slippage: float = 0.0,
) -> list[dict[str, Any]]:
    """把策略名展开为参数化配置（label/policy/params/fee/slippage）。"""
    out: list[dict[str, Any]] = []
    for p in policies:
        if p == "signal_exit":
            out.append({"label": "signal_exit", "policy": "signal_exit",
                        "params": {}, "fee": fee, "slippage": slippage})
        elif p == "ma_exit":
            out.append({"label": f"ma{ma_window}_exit", "policy": "ma_exit",
                        "params": {"window": ma_window}, "fee": fee, "slippage": slippage})
        elif p == "ma20_exit":  # 兼容别名
            out.append({"label": "ma20_exit", "policy": "ma_exit",
                        "params": {"window": 20}, "fee": fee, "slippage": slippage})
        elif p == "fixed_horizon":
            out.append({"label": f"fixed_{horizon}", "policy": "fixed_horizon",
                        "params": {"horizon": horizon}, "fee": fee, "slippage": slippage})
        else:
            raise ValueError(f"unknown exit policy: {p} (options: {exit_mod.EXIT_POLICIES})")
    return out


def run_backtest(
    signals: pd.DataFrame,
    *,
    theme: str,
    entity_type: str = "etf",
    exit_policies: tuple[str, ...] = ("signal_exit",),
    exit_configs: list[dict[str, Any]] | None = None,
    horizon: int = 20,
    ma_window: int = 20,
    fee: float = 0.0,
    slippage: float = 0.0,
    universe_mode: str = "configured",
    entry_params: dict[str, Any] | None = None,
    strategy_id: str = "",
    cache: dict[str, Any] | None = None,
    start_date: str = "",
    end_date: str = "",
) -> pd.DataFrame:
    """对指定主题/实体类型执行逐笔交易模拟（每个退出配置独立跑）。

    exit_configs 优先（扫描用，可含不同 horizon/window/fee）；否则由
    exit_policies + horizon/ma_window/fee/slippage 展开默认配置。
    universe_mode: configured（资产池，默认）/ theme-matched（全市场关键词）。
    entry_params: 来自策略 Entry Spec（如 {"rps15_min": 80}）。

    Returns:
        TRADE_COLUMNS DataFrame
    """
    if signals is None or signals.empty:
        return pd.DataFrame(columns=TRADE_COLUMNS)

    entries = entry_mod.entry_candidates(
        signals, entity_type=entity_type, theme=theme, universe_mode=universe_mode,
        rps15_min=(entry_params or {}).get("rps15_min"))
    if entries.empty:
        return pd.DataFrame(columns=TRADE_COLUMNS)
    if start_date:
        entries = entries[entries["trade_date"].astype(str) >= start_date]
    if end_date:
        entries = entries[entries["trade_date"].astype(str) <= end_date]
    if theme:
        entries = entry_mod.apply_theme_confirmation(entries, signals, theme)
    if entries.empty:
        return pd.DataFrame(columns=TRADE_COLUMNS)

    cache = cache or replay_engine.build_replay_cache()
    master = cache.get("master")
    name_map = dict(zip(master["fund_code"].astype(str), master["fund_name"])) \
        if master is not None and not master.empty else {}
    exit_map = build_exit_map(signals)
    etf_pivots = build_etf_pivots(cache) if entity_type == "etf" else None

    configs = exit_configs or build_exit_configs(
        exit_policies, horizon=horizon, ma_window=ma_window, fee=fee, slippage=slippage)

    uni_size = entry_mod.universe_size(signals, theme, universe_mode)
    uni_cfg_hash = entry_mod.universe_config_hash(universe_mode)
    uni_hash = entry_mod.universe_hash(signals, theme, universe_mode)

    trades: list[dict[str, Any]] = []
    tid = 0

    for cfg in configs:
        policy = cfg["policy"]
        label = cfg["label"]
        params = cfg.get("params", {}) or {}
        cfee = float(cfg.get("fee", fee))
        cslippage = float(cfg.get("slippage", slippage))
        for code, g in entries.groupby("entity_code"):
            name = str(name_map.get(str(code), code))
            prices = load_entity_prices(entity_type, str(code), etf_pivots=etf_pivots)
            if prices is None:
                for entry in g.sort_values("trade_date").itertuples():
                    tid += 1
                    trades.append(_unfilled_entry(
                        tid, str(code), name, theme, label, str(entry.trade_date),
                        "no_price_data", cfee, cslippage,
                        universe_mode, uni_size, uni_cfg_hash, uni_hash, strategy_id))
                continue
            open_s, close_s, dates = prices
            exit_dates = exit_map.get(str(code), []) if policy == "signal_exit" else []

            last_exit_fill: str | None = None  # 上一笔已了结持仓的退出成交日（None=无持仓）
            for entry in g.sort_values("trade_date").itertuples():
                esd = str(entry.trade_date)
                if last_exit_fill is not None and esd < last_exit_fill:
                    continue  # 持仓期间再次 entry 不重复开仓（含持有到数据末端的持仓）

                # 入场：T 日信号 → T+1 开盘
                fill_date, fill_price = next_open(open_s, esd)
                if fill_price is None:
                    tid += 1
                    trades.append(_unfilled_entry(
                        tid, str(code), name, theme, label, esd,
                        "no_next_open", cfee, cslippage,
                        universe_mode, uni_size, uni_cfg_hash, uni_hash, strategy_id))
                    continue

                # 退出：策略独立决定退出信号日
                exit_sig: str | None = None
                if policy == "signal_exit":
                    exit_sig = exit_mod.signal_exit_date(exit_dates, esd)
                elif policy == "ma_exit":
                    exit_sig = exit_mod.ma_exit_date(
                        close_s, fill_date, window=int(params.get("window", 20)))
                elif policy == "fixed_horizon":
                    exit_sig = exit_mod.fixed_horizon_exit_signal_date(
                        pd.DatetimeIndex(dates), fill_date, int(params.get("horizon", 20)))

                exd, xprice, xstatus, xreason = _resolve_exit(exit_sig, open_s, close_s, label)
                sell_price = float(xprice) if xprice is not None else None
                ret = _trade_return(fill_price, sell_price, cfee, cslippage) if sell_price else None
                holding_days = _biz_days_between(fill_date, exd) if exd else None
                last_exit_fill = exd

                tid += 1
                trades.append({
                    "trade_id": tid, "entity_code": str(code), "entity_name": name,
                    "theme": theme, "exit_policy": label,
                    "entry_signal_date": esd, "entry_fill_date": fill_date,
                    "entry_fill_price": round(fill_price, 4), "entry_status": "filled",
                    "entry_unfilled_reason": "",
                    "exit_signal_date": exit_sig or "", "exit_fill_date": exd or "",
                    "exit_fill_price": round(sell_price, 4) if sell_price is not None else None,
                    "exit_status": xstatus, "exit_reason": xreason,
                    "return_pct": ret, "holding_days": holding_days,
                    "fee_pct": cfee, "slippage_pct": cslippage,
                    "universe_mode": universe_mode, "universe_size": uni_size,
                    "universe_config_hash": uni_cfg_hash, "universe_hash": uni_hash,
                    "entry_score": _num(getattr(entry, "rps15", None)),
                    "strategy_id": strategy_id,
                })

    df = pd.DataFrame(trades, columns=TRADE_COLUMNS)
    return df


def _resolve_exit(
    exit_sig: str | None,
    open_s: pd.Series,
    close_s: pd.Series,
    policy: str,
) -> tuple[str | None, float | None, str, str]:
    """退出信号日 → (退出成交日, 退出价, exit_status, exit_reason)。

    - 正常：exit_sig 下一交易日开盘价成交 → closed
    - 退出信号日无下一开盘（数据末端/停牌）→ 按当日收盘计价，标记 unfilled_exit
    - 无退出信号（持有到数据末端）→ 按最后收盘计价，标记 open_at_end
    """
    if exit_sig is None:
        if close_s is not None and not close_s.empty:
            return (close_s.index[-1].strftime("%Y%m%d"), float(close_s.iloc[-1]),
                    "open_at_end", "open_at_end")
        return None, None, "open_at_end", "open_at_end"
    exd, xprice = next_open(open_s, exit_sig)
    if xprice is None:
        ts = pd.Timestamp(exit_sig)
        if close_s is not None and ts in close_s.index:
            return exit_sig, float(close_s.at[ts]), "unfilled_exit", "no_next_open"
        return exit_sig, None, "unfilled_exit", "no_next_open"
    return exd, xprice, "closed", policy
