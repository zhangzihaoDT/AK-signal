"""Study 3B · as-of 特征工程（first_exit 时点可观测，无 look-ahead）。

职责：
  - 从 Lane 2 事实（v1_signal_daily 的 pos60/120/360 + long_term_bottom +
    reliable_360 + bottom_state + etf_type）与 raw 价格面板构建 3B 事件级特征。
  - 每行 = 一次 first_exit 事件；所有 feature_timestamp <= first_exit_date。
  - 5 个 family（F1-F5），见 FEATURE_FAMILIES；categorical 单独列出。

口径（用户锁定，写死点）：
  - 全市场交易日 = MarketCalendar（与 3A 同源）。
  - 点值特征用 per-fund as-of（ffill 后取当日）；窗口特征用对齐数组 nanmin。
  - delta 特征 = first_exit 当日值 − 前 5 / 20 个市场交易日值。
  - RPS = 当日横截面百分位（0-100），横截面 = raw-eligible（剔除 money/bond/commodity）。
  - 禁止进入模型矩阵的列（year/month/date 数值/post_924/...）只在 dataset 保留
    供诊断/切片，不进 MODEL_FEATURES。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import PERSISTENCE_PRIMARY
from .calendar import MarketCalendar

RAW_DIR = Path("data/etf_signal/raw")
_RAW_EXCLUDE = ("money", "bond", "commodity")
_DD_WINDOW = 252           # 峰值跟踪窗口（交易日），rolling max
_DD_CURRENT_WINDOW = 20    # current drawdown 窗口
_DD_PREV_WINDOW = 40       # previous drawdown 窗口（当前 20d 窗口之前 40 个交易日）

# family → 连续特征（顺序即模型矩阵列顺序）
FEATURE_FAMILIES: dict[str, list[str]] = {
    "F1_position": [
        "pos60", "pos120", "pos360",
        "delta_pos60_5d", "delta_pos60_20d",
        "delta_pos120_5d", "delta_pos120_20d",
        "delta_pos360_20d",
    ],
    "F2_rps": [
        "rps20", "rps60",
        "delta_rps20_5d", "delta_rps20_20d",
        "delta_rps60_20d",
    ],
    "F3_drawdown": [
        "max_drawdown_20d", "max_drawdown_60d",
        "previous_drawdown_depth", "current_drawdown_depth",
        "drawdown_shallowing_ratio",
    ],
    "F4_bottom_history": [
        "days_in_bottom_before_exit", "entry_to_exit_days",
        "entry_pos60", "entry_pos120", "entry_pos360",
        "n_prior_bottom_episodes",
    ],
    "F5_market": [
        "market_ltb_breadth", "market_ltb_breadth_delta_5d",
        "market_ltb_breadth_delta_20d",
        "etf_market_return_20d", "etf_market_return_60d",
        "etf_type_breadth", "etf_type_breadth_delta_20d",
    ],
}

CONTINUOUS_FEATURES: list[str] = [f for fam in FEATURE_FAMILIES.values() for f in fam]
CATEGORICAL_FEATURES: list[str] = ["etf_type", "exit_bottom_state"]

# 禁止作为预测变量的诊断字段（可在 dataset 保留，不进模型矩阵）
FORBIDDEN_FEATURES: list[str] = [
    "year", "month", "calendar_date_numeric", "post_924",
    "days_since_924", "y2025_dummy", "y2026_dummy",
]

MODEL_FEATURES: list[str] = CONTINUOUS_FEATURES + CATEGORICAL_FEATURES


def load_close_panel(v1: pd.DataFrame) -> pd.DataFrame:
    """宽面板：index=市场交易日（对齐 v1 日期并集），columns=fund_code，值=close。

    无 raw 文件 / 无历史价格的 fund 以全 NaN 列占位（as-of 特征返回 NaN，
    不抛错），保证 RPS/return/drawdown 计算对缺失 fund 安全降级。
    """
    trade_dates = pd.to_datetime(v1["trade_date"]).dropna().unique()
    dates = pd.DatetimeIndex(sorted(trade_dates))
    series: dict[str, pd.Series] = {}
    for code in sorted(v1["fund_code"].astype(str).unique()):
        p = RAW_DIR / f"{code}.parquet"
        if not p.exists():
            continue
        df = pd.read_parquet(p, columns=["date", "close"])
        s = df.dropna(subset=["close"]).set_index("date")["close"]
        s = s[~s.index.duplicated()].sort_index()
        series[str(code)] = s
    panel = pd.DataFrame(series).reindex(dates).sort_index()
    # 补齐无 raw 数据的 fund 列为全 NaN
    for code in sorted(v1["fund_code"].astype(str).unique()):
        if code not in panel.columns:
            panel[code] = np.nan
    return panel


class FeatureBuilder:
    """把 v1 事实 + raw 面板转成 first_exit 事件级特征。

    事件日期用 MarketCalendar 锚定；所有取值只看 <= 目标日的行（无 look-ahead）。
    每只 fund 保存一组对齐到市场交易日历的 as-of 序列（点值 ffill，窗口保留 NaN）。
    """

    def __init__(self, v1: pd.DataFrame, panel: pd.DataFrame, cal: MarketCalendar):
        self.cal = cal
        self.dates = list(cal.dates)                     # list[datetime.date]
        self._dpos = {d: i for i, d in enumerate(self.dates)}
        v1 = v1.copy()
        v1["trade_date"] = pd.to_datetime(v1["trade_date"])
        self.v1 = v1
        self.funds = sorted(v1["fund_code"].unique())
        self.panel = panel.reindex(pd.DatetimeIndex(self.dates)).sort_index()

        eligible = v1[~v1["etf_type"].isin(_RAW_EXCLUDE)]
        # RPS 横截面（raw-eligible）
        close_elig = self.panel[eligible["fund_code"].unique()]
        ret20 = close_elig / close_elig.shift(20) - 1.0
        ret60 = close_elig / close_elig.shift(60) - 1.0
        rps20 = ret20.rank(axis=1, pct=True) * 100
        rps60 = ret60.rank(axis=1, pct=True) * 100

        # 市场级 breadth / 市场收益
        rel = v1[v1["reliable_360"]]
        mkt_breadth = rel.groupby("trade_date")["long_term_bottom"].mean().reindex(self.panel.index).ffill()
        mkt_ret20 = ret20.mean(axis=1)
        mkt_ret60 = ret60.mean(axis=1)
        type_breadth = rel.groupby(["trade_date", "etf_type"])["long_term_bottom"].mean().unstack().reindex(self.panel.index).ffill()

        # per-fund aligned as-of series
        pos = eligible.pivot_table(index="trade_date", columns="fund_code",
                                   values=["pos60", "pos120", "pos360"], aggfunc="last")
        bottom_state = eligible.pivot_table(index="trade_date", columns="fund_code",
                                            values="bottom_state", aggfunc="last")
        dd = self.panel / self.panel.rolling(_DD_WINDOW, min_periods=1).max() - 1.0
        dd20 = dd.rolling(_DD_CURRENT_WINDOW, min_periods=1).min()
        dd60 = dd.rolling(_DD_CURRENT_WINDOW + _DD_PREV_WINDOW, min_periods=1).min()

        idx = pd.DatetimeIndex(self.dates)
        self._s: dict[str, dict[str, pd.Series]] = {}
        for fund in self.funds:
            if fund not in self.panel.columns:
                continue
            col_s: dict[str, pd.Series] = {}
            for c in ("pos60", "pos120", "pos360"):
                if c in pos and fund in pos[c].columns:
                    col_s[c] = pos[c][fund].reindex(idx).ffill()
            if fund in rps20.columns:
                col_s["rps20"] = rps20[fund].reindex(idx).ffill()
            if fund in rps60.columns:
                col_s["rps60"] = rps60[fund].reindex(idx).ffill()
            col_s["dd"] = dd[fund].reindex(idx)
            col_s["dd20"] = dd20[fund].reindex(idx)
            col_s["dd60"] = dd60[fund].reindex(idx)
            if fund in bottom_state.columns:
                col_s["bottom_state"] = bottom_state[fund].reindex(idx).ffill()
            self._s[fund] = col_s

        self._mkt_breadth = mkt_breadth
        self._mkt_ret20 = mkt_ret20
        self._mkt_ret60 = mkt_ret60
        self._type_breadth = type_breadth
        self._etf_type_of = dict(zip(v1["fund_code"], v1["etf_type"]))

    def _v(self, fund: str, key: str, idx: int) -> float:
        s = self._s.get(fund, {}).get(key)
        if s is None or idx < 0 or idx >= len(s):
            return np.nan
        v = s.iloc[idx]
        return float(v) if pd.notna(v) else np.nan

    def _v_str(self, fund: str, key: str, idx: int) -> str | None:
        s = self._s.get(fund, {}).get(key)
        if s is None or idx < 0 or idx >= len(s):
            return None
        v = s.iloc[idx]
        return str(v) if pd.notna(v) else None

    def _mv(self, ser: pd.Series | None, idx: int) -> float:
        if ser is None or idx < 0 or idx >= len(ser):
            return np.nan
        v = ser.iloc[idx]
        return float(v) if pd.notna(v) else np.nan

    def features_for_event(
        self, fund: str, exit_ts: pd.Timestamp,
        entry_ts: pd.Timestamp, days_in_bottom: float,
        n_prior: int,
    ) -> dict[str, Any]:
        idx = self._dpos.get(pd.Timestamp(exit_ts).date(), -1)
        idx5 = idx - 5
        idx20 = idx - 20
        eidx = self._dpos.get(pd.Timestamp(entry_ts).date(), -1)
        out: dict[str, Any] = {}

        # F1 position
        for c in ("pos60", "pos120", "pos360"):
            out[c] = self._v(fund, c, idx)
        out["delta_pos60_5d"] = out["pos60"] - self._v(fund, "pos60", idx5)
        out["delta_pos60_20d"] = out["pos60"] - self._v(fund, "pos60", idx20)
        out["delta_pos120_5d"] = out["pos120"] - self._v(fund, "pos120", idx5)
        out["delta_pos120_20d"] = out["pos120"] - self._v(fund, "pos120", idx20)
        out["delta_pos360_20d"] = out["pos360"] - self._v(fund, "pos360", idx20)

        # F2 rps
        out["rps20"] = self._v(fund, "rps20", idx)
        out["rps60"] = self._v(fund, "rps60", idx)
        out["delta_rps20_5d"] = out["rps20"] - self._v(fund, "rps20", idx5)
        out["delta_rps20_20d"] = out["rps20"] - self._v(fund, "rps20", idx20)
        out["delta_rps60_20d"] = out["rps60"] - self._v(fund, "rps60", idx20)

        # F3 drawdown（深度取正数：-dd）
        out["max_drawdown_20d"] = -self._v(fund, "dd20", idx)
        out["max_drawdown_60d"] = -self._v(fund, "dd60", idx)
        dd_ser = self._s.get(fund, {}).get("dd")
        if dd_ser is not None and idx > 0:
            arr = dd_ser.to_numpy(float)
            lo = max(0, idx - _DD_PREV_WINDOW - _DD_CURRENT_WINDOW)
            prev = arr[lo:idx - _DD_CURRENT_WINDOW + 1]
            cur = arr[max(0, idx - _DD_CURRENT_WINDOW + 1):idx + 1]
            prev_finite = prev[np.isfinite(prev)]
            cur_finite = cur[np.isfinite(cur)]
            out["previous_drawdown_depth"] = -float(np.nanmin(prev_finite)) if prev_finite.size else np.nan
            out["current_drawdown_depth"] = -float(np.nanmin(cur_finite)) if cur_finite.size else np.nan
        else:
            out["previous_drawdown_depth"] = np.nan
            out["current_drawdown_depth"] = np.nan
        prev = out["previous_drawdown_depth"]
        if prev is not None and np.isfinite(prev) and prev > 0:
            out["drawdown_shallowing_ratio"] = out["current_drawdown_depth"] / prev
        else:
            out["drawdown_shallowing_ratio"] = np.nan

        # F4 bottom history
        out["days_in_bottom_before_exit"] = days_in_bottom
        out["entry_to_exit_days"] = days_in_bottom
        for c in ("pos60", "pos120", "pos360"):
            out[f"entry_{c}"] = self._v(fund, c, eidx)
        out["n_prior_bottom_episodes"] = float(n_prior)
        out["exit_bottom_state"] = self._v_str(fund, "bottom_state", idx) or "NORMAL"

        # F5 market context
        out["market_ltb_breadth"] = self._mv(self._mkt_breadth, idx)
        out["market_ltb_breadth_delta_5d"] = out["market_ltb_breadth"] - self._mv(self._mkt_breadth, idx5)
        out["market_ltb_breadth_delta_20d"] = out["market_ltb_breadth"] - self._mv(self._mkt_breadth, idx20)
        out["etf_market_return_20d"] = self._mv(self._mkt_ret20, idx)
        out["etf_market_return_60d"] = self._mv(self._mkt_ret60, idx)
        out["etf_type"] = str(self._etf_type_of.get(fund, "theme"))
        out["etf_type_breadth"] = self._mv(self._type_breadth.get(out["etf_type"]) if isinstance(self._type_breadth, pd.DataFrame) else None, idx) if isinstance(self._type_breadth, pd.DataFrame) else np.nan
        out["etf_type_breadth_delta_20d"] = out["etf_type_breadth"] - (self._mv(self._type_breadth.get(out["etf_type"]), idx20) if isinstance(self._type_breadth, pd.DataFrame) else np.nan)
        return out


def build_dataset(
    v1: pd.DataFrame,
    trajectories: pd.DataFrame,
    cal: MarketCalendar,
    horizon: int = 120,
    persistence: int = PERSISTENCE_PRIMARY,
    max_date: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """把轨迹表扩展为事件级特征数据集（每行 = 一次 first_exit）。

    max_date：截断所有输入到 <= max_date（测试无 look-ahead 用；None=全量）。
    返回 DataFrame 含：id 列 / 诊断列（year 等禁止进模型的列）/ 全部特征 / 标签。
    """
    panel = load_close_panel(v1)
    if max_date is not None:
        v1 = v1[v1["trade_date"] <= max_date]
        panel = panel.loc[panel.index <= max_date]
    builder = FeatureBuilder(v1, panel, cal)

    traj = trajectories.copy()
    traj["first_exit_date"] = pd.to_datetime(traj["first_exit_date"])
    traj["entry_date"] = pd.to_datetime(traj["entry_date"])
    events = traj[traj["first_exit_date"].notna()].copy()

    # n_prior_bottom_episodes：同 fund 在 entry 之前已有的已完成 first_exit 段数
    prior_counts: dict[tuple[str, pd.Timestamp], int] = {}
    for _code, g in events.sort_values("entry_date").groupby("fund_code", sort=False):
        g = g.sort_values("entry_date")
        seen = 0
        for _, r in g.iterrows():
            prior_counts[(str(r["fund_code"]), pd.Timestamp(r["entry_date"]))] = seen
            if pd.notna(r.get("first_exit_date")):
                seen += 1

    rows: list[dict[str, Any]] = []
    for _, r in events.iterrows():
        fund = str(r["fund_code"])
        fe = pd.Timestamp(r["first_exit_date"])
        en = pd.Timestamp(r["entry_date"])
        post924 = fe >= pd.Timestamp("2024-09-24")
        days_since = (fe - pd.Timestamp("2024-09-24")).days if post924 else -1
        f = builder.features_for_event(
            fund, fe, en,
            days_in_bottom=float(r["days_in_bottom_total"]) if pd.notna(r.get("days_in_bottom_total")) else np.nan,
            n_prior=int(prior_counts.get((fund, en), 0)),
        )
        row: dict[str, Any] = {
            "fund_code": fund,
            "fund_name": str(r.get("fund_name", "")),
            "etf_type": f["etf_type"],
            "industry_cluster": str(r.get("industry_cluster", "OTHER")),
            "entry_date": en,
            "first_exit_date": fe,
            "year": fe.year, "month": fe.month,
            "calendar_date_numeric": fe.toordinal(),
            "post_924": bool(post924),
            "days_since_924": days_since,
            "y2025_dummy": int(fe.year == 2025),
            "y2026_dummy": int(fe.year == 2026),
            "persistence": persistence,
        }
        for k, v in f.items():
            row[k] = v
        for h in (60, 120, 250):
            row[f"escape_{h}d"] = r.get(f"escape_{h}d")
            row[f"right_censored_{h}d"] = r.get(f"right_censored_{h}d")
            row[f"forward_data_complete_{h}d"] = r.get(f"forward_data_complete_{h}d")
        row["days_to_first_retest"] = r.get("days_to_first_retest")
        row["first_retest_date"] = r.get("first_retest_date")
        row["is_current"] = bool(r.get("is_current", False))
        rows.append(row)

    return pd.DataFrame(rows)
