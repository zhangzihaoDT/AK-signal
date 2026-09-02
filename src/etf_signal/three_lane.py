"""三 Lane 合成表（three_lane）——纯排版/归集层，不制造新事实。

把每日三份已落盘的 Lane 事实按 fund_code 左外 join 成一张表，
回答「趴在底部 → 刚离底部 → 切换中 → 趋势建立 → 强势资产」这条路径。

输入（全部为既有每日产物，不做任何重算）：
  - Lane 1 趋势  watchlist_{date}.parquet            → trend_state（OUT_OF_SCOPE/WATCH/STRONG_WATCH/BUY_CANDIDATE）
  - Lane 2 底部  v1_signal_daily.parquet（过滤 trade_date）→ long_term_bottom / bottom_state / target_stage / reliable_360 / pos120
  - Lane 3 迁移  trend_transition_state_{date}.parquet → transition_state / days_since_first_exit / confirmed_long_term_bottom

口径（用户锁定）：
  - 「Lane 2 底部 = 是/否」用 **raw long_term_bottom**（price_map 口径），
    不改成 3C 的 confirmed_long_term_bottom（状态机防抖口径，语义边界不混淆；
    允许边缘日「Lane 2 仍是底部、Lane 3 已进入确认切换」的真实差异）。
  - 三条 Lane 各自保持原始枚举，本模块只 join + 选列，不重新判断任何东西。
  - 展示层翻译（底部→刚离底部→…）在 report renderer，不进本模块。

产物（etf_signal_output_dir()）：
  three_lane_{date}.parquet / .csv
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

from src.common.paths import etf_signal_output_dir, etf_signal_signals_dir
from src.research.trend_transition import STUDY_DIR as TREND_STUDY_DIR
from src.research.etf_bottom.backtest_v1 import _backtest_dir

logger = logging.getLogger("etf_signal.three_lane")

# Lane 3 状态 → 展示层路径阶段（report renderer 消费；本模块保留机器枚举）
PATH_STAGE = {
    "BOTTOM": "底部",
    "FIRST_EXIT": "刚离底部",
    "TRANSITION_EARLY": "切换中·早期",
    "TRANSITION_ACTIVE": "切换中",
    "TRANSITION_ESTABLISHED": "趋势建立",
    "RETEST": "回到底部",
    "POST_TRANSITION": "已完成",
    "UNRELIABLE": "数据不足",
}

# Lane 1 trend_state → 展示层
TREND_CN = {
    "BUY_CANDIDATE": "买入候选",
    "STRONG_WATCH": "强势关注",
    "WATCH": "观察",
    "OUT_OF_SCOPE": "趋势不足",
}

# 活跃路径判定（§用户锁定：Lane 2 底部 ∪ Lane 3 非 POST/UNRELIABLE ∪ Lane 1 非 OUT_OF_SCOPE）
_TRANSITION_ACTIVE = ("FIRST_EXIT", "TRANSITION_EARLY", "TRANSITION_ACTIVE",
                      "TRANSITION_ESTABLISHED", "RETEST")


def _v1_path() -> Path:
    return _backtest_dir() / "v1_signal_daily.parquet"


def load_watchlist(trade_date: pd.Timestamp) -> pd.DataFrame:
    """读取当日 watchlist（Lane 1 trend_state）。

    优先精确日期文件；缺 → 回退到 <= 目标日的最新 watchlist（run-day 数据滞后容错），
    并在结果上带 _watchlist_date 列供审计。缺文件 → 空表（不阻塞 join）。
    """
    target = pd.Timestamp(trade_date)
    files = sorted(etf_signal_signals_dir().glob("watchlist_*.parquet"))
    best_path = None
    best_date: pd.Timestamp | None = None
    for p in files:
        try:
            df = pd.read_parquet(p, columns=["trade_date"])
            td = pd.Timestamp(df["trade_date"].dropna().max())
        except Exception:
            continue
        if td <= target and (best_date is None or td > best_date):
            best_date, best_path = td, p
    if best_path is None:
        logger.warning("no watchlist <= %s", target.date())
        return pd.DataFrame(columns=["fund_code", "fund_name", "trend_state", "_watchlist_date"])
    df = pd.read_parquet(best_path)
    df["_watchlist_date"] = pd.Timestamp(best_date)
    if best_date != target:
        logger.warning("watchlist fallback: requested %s, using %s", target.date(), best_date.date())
    return df[["fund_code", "fund_name", "trend_state", "_watchlist_date"]].drop_duplicates(subset=["fund_code"])


def load_v1_at(trade_date: pd.Timestamp) -> pd.DataFrame:
    """Lane 2 底部事实：v1_signal_daily 过滤到 trade_date。"""
    v1 = pd.read_parquet(_v1_path())
    v1["trade_date"] = pd.to_datetime(v1["trade_date"])
    day = pd.Timestamp(trade_date)
    sub = v1[v1["trade_date"] == day]
    return sub[["fund_code", "fund_name", "etf_type", "long_term_bottom",
                "bottom_state", "target_stage", "reliable_360", "pos120"]]


def load_state(trade_date: pd.Timestamp) -> pd.DataFrame:
    """Lane 3 迁移状态：trend_transition_state_{date}.parquet。"""
    date_str = pd.Timestamp(trade_date).strftime("%Y%m%d")
    p = TREND_STUDY_DIR / f"trend_transition_state_{date_str}.parquet"
    if not p.exists():
        logger.warning("trend_transition_state %s not found", date_str)
        return pd.DataFrame(columns=["fund_code", "transition_state", "days_since_first_exit",
                                     "confirmed_long_term_bottom"])
    df = pd.read_parquet(p)
    return df[["fund_code", "transition_state", "days_since_first_exit",
               "confirmed_long_term_bottom", "lane1_leadership_state"]]


def build_three_lane(trade_date: pd.Timestamp | str | None = None,
                     watchlist: pd.DataFrame | None = None,
                     v1_day: pd.DataFrame | None = None,
                     state: pd.DataFrame | None = None) -> pd.DataFrame:
    """三 Lane 左外 join（以全市场 watchlist ∪ v1 ∪ state 的 fund 并集为 universe）。

    trade_date 缺省 = 最新可用（v1_signal_daily max）。
    各输入可注入（测试用）；缺省自动从磁盘读。
    """
    if trade_date is None:
        v1 = pd.read_parquet(_v1_path())
        trade_date = pd.Timestamp(pd.to_datetime(v1["trade_date"]).max())
    trade_date = pd.Timestamp(trade_date)

    wl = watchlist if watchlist is not None else load_watchlist(trade_date)
    v1d = v1_day if v1_day is not None else load_v1_at(trade_date)
    st = state if state is not None else load_state(trade_date)

    # 全市场 universe：三份 fund 并集
    funds = set(wl["fund_code"]) | set(v1d["fund_code"]) | set(st["fund_code"])
    base = pd.DataFrame({"fund_code": sorted(funds)})

    out = base.merge(
        wl[["fund_code", "fund_name", "trend_state"]], on="fund_code", how="left"
    )
    # fund_name 优先取 watchlist / v1（state 无 name）
    out = out.merge(
        v1d[["fund_code", "fund_name", "etf_type", "long_term_bottom", "bottom_state",
             "target_stage", "reliable_360", "pos120"]]
        .rename(columns={"fund_name": "_v1_name"}),
        on="fund_code", how="left",
    )
    out["fund_name"] = out["fund_name"].fillna(out["_v1_name"])
    out.drop(columns=["_v1_name"], inplace=True)
    out = out.merge(
        st[["fund_code", "transition_state", "days_since_first_exit",
            "confirmed_long_term_bottom"]],
        on="fund_code", how="left",
    )

    out["trade_date"] = trade_date
    if "_watchlist_date" in wl.columns:
        wl_date_map = wl.set_index("fund_code")["_watchlist_date"]
        out["_watchlist_date"] = out["fund_code"].map(wl_date_map)
    else:
        out["_watchlist_date"] = pd.NaT
    out = out.rename(columns={
        "long_term_bottom": "lane2_long_term_bottom",
        "bottom_state": "lane2_bottom_state",
        "target_stage": "lane2_target_stage",
        "reliable_360": "lane2_reliable_360",
        "pos120": "lane2_pos120",
        "transition_state": "lane3_transition_state",
        "days_since_first_exit": "lane3_days_since_first_exit",
        "confirmed_long_term_bottom": "lane3_confirmed_long_term_bottom",
        "trend_state": "lane1_trend_state",
    })
    # 列顺序固定
    cols = ["trade_date", "fund_code", "fund_name", "etf_type",
            "lane2_long_term_bottom", "lane2_bottom_state", "lane2_target_stage",
            "lane2_reliable_360", "lane2_pos120",
            "lane3_transition_state", "lane3_days_since_first_exit",
            "lane3_confirmed_long_term_bottom",
            "lane1_trend_state", "_watchlist_date"]
    return out[cols].sort_values("fund_code").reset_index(drop=True)


def is_active_path(row: pd.Series | dict[str, Any]) -> bool:
    """§用户锁定：活跃路径子集 = Lane 2 底部 ∪ Lane 3 非 POST/UNRELIABLE ∪ Lane 1 非 OUT_OF_SCOPE。"""
    l2 = bool(row.get("lane2_long_term_bottom") is True)
    l3 = str(row.get("lane3_transition_state", "")) in _TRANSITION_ACTIVE
    l1 = str(row.get("lane1_trend_state", "")) not in ("OUT_OF_SCOPE", "", "nan", "None")
    return l2 or l3 or l1


def write_products(df: pd.DataFrame, out_dir: Path | None = None) -> tuple[Path, Path]:
    """写 three_lane_{date}.parquet + .csv（date-stamped，不写 _latest）。"""
    out_dir = out_dir or etf_signal_output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    date_str = pd.Timestamp(df["trade_date"].iloc[0]).strftime("%Y%m%d")
    pq = out_dir / f"three_lane_{date_str}.parquet"
    csv = out_dir / f"three_lane_{date_str}.csv"
    df.to_parquet(pq, index=False)
    df.to_csv(csv, index=False, encoding="utf-8-sig")
    return pq, csv


def latest_trade_date() -> pd.Timestamp:
    v1 = pd.read_parquet(_v1_path())
    return pd.Timestamp(pd.to_datetime(v1["trade_date"]).max())
