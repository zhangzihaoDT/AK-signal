"""Study 2B — Bottom Episode Clustering。

把同产业中高度同步的 ETF 底部事件合并成独立产业周期 episode。

研究问题：
  1. 每个产业历史上到底经历过几轮真正的底部？
  2. 「15 只 ETF 历史支持」在去除同产业 + 同周期重复暴露后，还剩多少独立证据？

两层合并：
  Layer 1 ETF 低位期合并：同一 ETF 相邻 entry 间隔 < ETF_MERGE_DAYS → 合并为同一低位期
    （解决 off→on 反复触发把同一底部周期拆碎的问题）
  Layer 2 产业 episode 合并：同产业内多 ETF 低位期时间重叠/相邻 → 合并为一个产业 episode

口径（用户锁定）：
  ETF_MERGE_DAYS = 40 交易日
  EPISODE_OVERLAP_DAYS = 20 交易日
  产业簇：游戏传媒 / 汽车产业链(智能汽车+智能网联+汽车国泰+汽车广发+港股通汽车) / 软件大数据 / 消费文旅 / 周期 / 军工航空
  episode 上涨判定：参与 ETF 的 ret_120 中位 > 0（不用 mean）
  当前 2026 episode 单独标记 is_current_episode，不参与「历史上涨比例」
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.common.paths import etf_signal_raw_dir

from . import STUDY_DIR, HORIZONS
from .price_map import build_price_map
from .returns import ClosePanel
from .state_odds import daily_state_series

logger = logging.getLogger(__name__)

ETF_MERGE_DAYS = 40        # 交易日：同一 ETF 相邻 entry 间隔小于此值视为同一低位期
EPISODE_OVERLAP_DAYS = 20  # 交易日：产业内 ETF 低位期重叠/相邻合并为同一 episode

# 产业簇（研究专用硬编码，汽车链合并为一个大簇）
INDUSTRY_CLUSTERS: dict[str, list[str]] = {
    "游戏传媒": ["517770", "516010", "159869", "159855", "516620", "512980", "159805"],
    "汽车产业链": ["159795", "159888", "159889", "515250", "159872", "516110", "159512", "159323",
                 "562700", "515030"],  # 2026-08-31: 加入汽车零部件/新能源车 ETF（产业 beta observable）
    "软件大数据": ["159852", "561010", "562930", "516700"],
    "消费文旅": ["159793", "159728", "159766", "562510"],
    "周期": ["516750", "159745", "515210"],
    "军工航空": ["512710", "159378"],
}
CLUSTER_CODE_SET = {c for codes in INDUSTRY_CLUSTERS.values() for c in codes}


def _biz_days_between(a, b) -> int:
    try:
        s = pd.Timestamp(a).strftime("%Y-%m-%d")
        e = pd.Timestamp(b).strftime("%Y-%m-%d")
        return int(np.busday_count(np.datetime64(s), np.datetime64(e), weekmask="1111100"))
    except Exception:
        return 0


def _build_panel(codes: list[str]) -> ClosePanel:
    pivots = []
    for code in codes:
        d = pd.read_parquet(f"data/etf_signal/raw/{code}.parquet", columns=["date", "close"])
        pivots.append(pd.DataFrame({code: d.set_index("date")["close"]}))
    pivot = pd.concat(pivots, axis=1).sort_index()
    pivot = pivot[pivot.index.notna()]
    return ClosePanel(pivot)


def _etf_low_periods(states: pd.DataFrame, merge_days: int = ETF_MERGE_DAYS,
                     panel: ClosePanel | None = None) -> list[dict[str, Any]]:
    """Layer 1：单只 ETF 的独立低位期（合并相邻 entry）。

    states 需含 date/long_term_bottom/fund_code；从 off→on 提取 entry，再按 merge_days 合并。
    若传入 panel，为每个低位期计算 20/60/120D 前向收益（以低位期起始日为事件日）。
    """
    lt = states["long_term_bottom"].to_numpy(bool)
    n = len(states)
    # 提取每个低位段（off→on 连续段）
    segments: list[dict[str, Any]] = []
    i = 0
    while i < n:
        if not lt[i]:
            i += 1
            continue
        seg_start = i
        j = i
        while j < n and lt[j]:
            j += 1
        seg_end = j
        is_current = seg_end >= n
        segments.append({
            "entry_date": states["date"].iloc[seg_start],
            "exit_date": states["date"].iloc[seg_end - 1],
            "days_in_state": seg_end - seg_start,
            "is_current": bool(is_current),
            "seg_start_idx": seg_start,
            "seg_end_idx": seg_end - 1,
        })
        i = seg_end

    # 合并相邻 entry（间隔 < merge_days）为同一低位期
    if not segments:
        return []
    periods: list[dict[str, Any]] = []
    cur = dict(segments[0])
    cur["n_segments"] = 1
    for seg in segments[1:]:
        gap = _biz_days_between(cur["exit_date"], seg["entry_date"])
        if gap < merge_days:
            # 合并：延长到当前段，累积 segments
            cur["exit_date"] = seg["exit_date"]
            cur["days_in_state"] += seg["days_in_state"]
            cur["is_current"] = seg["is_current"]
            cur["n_segments"] += 1
        else:
            periods.append(cur)
            cur = dict(seg)
            cur["n_segments"] = 1
    periods.append(cur)

    code = states["fund_code"].iloc[0]
    out = []
    for p in periods:
        rec = {
            "fund_code": code,
            "start": p["entry_date"],
            "end": p["exit_date"],
            "days_in_state": p["days_in_state"],
            "n_segments": p["n_segments"],
            "is_current": p["is_current"],
        }
        if panel is not None:
            rets = panel.forward_returns(code, p["entry_date"], HORIZONS)
            for h in HORIZONS:
                rec[f"ret_{h}"] = rets.get(h)
        out.append(rec)
    return out


def _cluster_episodes(periods: pd.DataFrame, cluster: str,
                      overlap_days: int = EPISODE_OVERLAP_DAYS) -> list[dict[str, Any]]:
    """Layer 2：产业内 ETF 低位期合并为产业 episode。

    periods 需含 fund_code/start/end/is_current。同一 episode 内任意 ETF 低位期起始间隔 < overlap_days 视为重叠。
    """
    df = periods.sort_values("start").reset_index(drop=True)
    if df.empty:
        return []
    episodes: list[dict[str, Any]] = []
    cur: dict[str, Any] = {
        "industry_cluster": cluster,
        "start": df.loc[0, "start"],
        "end": df.loc[0, "end"],
        "fund_codes": [df.loc[0, "fund_code"]],
        "is_current": bool(df.loc[0, "is_current"]),
        "n_periods": int(len(df)),
    }
    for _, row in df.iterrows():
        gap = _biz_days_between(cur["end"], row["start"])
        if gap < overlap_days or cur["start"] <= row["start"] <= cur["end"]:
            # 重叠或相邻 → 并入当前 episode
            cur["end"] = max(cur["end"], row["end"])
            if row["fund_code"] not in cur["fund_codes"]:
                cur["fund_codes"].append(row["fund_code"])
            cur["is_current"] = cur["is_current"] or bool(row["is_current"])
        else:
            episodes.append(cur)
            cur = {
                "industry_cluster": cluster,
                "start": row["start"],
                "end": row["end"],
                "fund_codes": [row["fund_code"]],
                "is_current": bool(row["is_current"]),
                "n_periods": int(len(df)),
            }
    episodes.append(cur)
    for ep in episodes:
        ep["fund_codes"] = sorted(set(ep["fund_codes"]))
        ep["n_etfs_participating"] = len(ep["fund_codes"])
    return episodes


def _episode_returns(episode: dict, period_returns: dict[tuple[str, pd.Timestamp], dict]) -> dict[str, Any]:
    """计算 episode 后续收益（参与 ETF 各自低位期的 ret_120 中位，用户口径）。"""
    rets = []
    for code in episode["fund_codes"]:
        # 该 ETF 在该 episode 时间窗内的低位期前向收益
        for (c, start), rr in period_returns.items():
            if c == code and episode["start"] <= start <= episode["end"]:
                if rr.get("ret_120") is not None:
                    rets.append(rr["ret_120"])
    out = {
        "n_etfs_with_ret120": len(rets),
        "ret120_mean": round(float(np.mean(rets)), 4) if rets else None,
        "ret120_median": round(float(np.median(rets)), 4) if rets else None,
        "ret120_win_rate": round(float((np.array(rets) > 0).mean()), 4) if rets else None,
        "episode_up": bool(np.median(rets) > 0) if rets else None,
    }
    return out


def run_episodes(study_dir: Path | None = None) -> dict:
    """Study 2B 编排：产业簇 → ETF 低位期 → 产业 episode → 收益 → 汇总 → 落盘。"""
    study_dir = study_dir or STUDY_DIR
    study_dir.mkdir(parents=True, exist_ok=True)

    # 面板（29 只产业簇 ETF 的 close，用于低位期前向收益）
    panel = _build_panel(sorted(CLUSTER_CODE_SET))

    # 逐 ETF 计算低位期（带各自起始日的前向收益）
    all_periods: list[dict[str, Any]] = []
    for code in CLUSTER_CODE_SET:
        d = pd.read_parquet(f"data/etf_signal/raw/{code}.parquet",
                            columns=["date", "close", "fund_code"])
        states = daily_state_series(d)
        periods = _etf_low_periods(states, ETF_MERGE_DAYS, panel=panel)
        all_periods.extend(periods)
    periods_df = pd.DataFrame(all_periods)
    period_returns = {(r["fund_code"], pd.Timestamp(r["start"])): r for r in all_periods}

    # 产业 episode
    cluster_episodes: dict[str, list[dict]] = {}
    for cluster, codes in INDUSTRY_CLUSTERS.items():
        sub = periods_df[periods_df["fund_code"].isin(codes)]
        eps = _cluster_episodes(sub, cluster)
        for ep in eps:
            ep["cluster_size"] = len(codes)
            ep["participation_ratio"] = round(ep["n_etfs_participating"] / len(codes), 3)
            ep["returns"] = _episode_returns(ep, period_returns)
        cluster_episodes[cluster] = eps

    # 汇总：每产业独立周期数 + 支持独立度
    summary: dict[str, dict] = {}
    for cluster, eps in cluster_episodes.items():
        hist = [e for e in eps if not e["is_current"]]
        up = [e for e in hist if e["returns"].get("episode_up") is True]
        summary[cluster] = {
            "n_episodes_total": len(eps),
            "n_episodes_historical": len(hist),
            "n_episodes_up": len(up),
            "up_ratio_historical": round(len(up) / max(len(hist), 1), 3),
            "is_current_episode": any(e["is_current"] for e in eps),
        }

    payload = {
        "study": "Study 2B Bottom Episode Clustering",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "params": {
            "etf_merge_days": ETF_MERGE_DAYS,
            "episode_overlap_days": EPISODE_OVERLAP_DAYS,
            "up_rule": "median_ret120 > 0",
        },
        "summary": summary,
        "clusters": cluster_episodes,
        "etf_low_periods": periods_df.to_dict("records"),
        "n_etf_low_periods": int(len(periods_df)),
    }
    out = study_dir / "bottom_episodes.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # 落盘明细 parquet
    ep_rows = []
    for cluster, eps in cluster_episodes.items():
        for e in eps:
            ep_rows.append({
                "industry_cluster": cluster,
                "start": e["start"], "end": e["end"],
                "n_etfs_participating": e["n_etfs_participating"],
                "participation_ratio": e["participation_ratio"],
                "fund_codes": ",".join(e["fund_codes"]),
                "is_current": e["is_current"],
                "ret120_mean": e["returns"].get("ret120_mean"),
                "ret120_median": e["returns"].get("ret120_median"),
                "ret120_win_rate": e["returns"].get("ret120_win_rate"),
                "episode_up": e["returns"].get("episode_up"),
            })
    ep_df = pd.DataFrame(ep_rows)
    ep_path = study_dir / "bottom_episodes.parquet"
    ep_df.to_parquet(ep_path, index=False)

    logger.info("study 2B episodes -> %s", out)
    return payload
