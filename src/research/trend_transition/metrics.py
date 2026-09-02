"""Study 3B · 评估指标（手写，无 sklearn）。

指标（用户锁定，§14）：
  - Discrimination：ROC-AUC / PR-AUC（AP 定义）。
  - Calibration：Brier Score + calibration buckets。
  - Decision Lift：Top 10/20/30% 实际 ESCAPE rate / base rate。

口径：
  - event-weighted：每个 first-exit 事件一票。
  - date-weighted：先按 first_exit_date 聚合成日期级均值，再在同一日期级样本上算指标，
    避免单日几十只 exit 支配结论（§17 同日期评估）。
  - 所有函数接受 y_true(0/1) 与 y_prob；date 参数可选，给定时输出两种口径。
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def roc_auc(y: np.ndarray, p: np.ndarray) -> float:
    """手写 ROC-AUC（rank-based Mann-Whitney U，平局平均秩）。

    对二进制 y；若 y 是 0-1 软标签（date-weighted），用 roc_auc_soft。
    """
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    mask = ~np.isnan(p)
    y, p = y[mask], p[mask]
    if len(y) == 0 or np.all(y == 0) or np.all(y == 1):
        return np.nan
    # 用 argsort 分配秩（含平局平均秩）
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(len(p), dtype=float)
    ranks[order] = np.arange(1, len(p) + 1)
    # 处理平局：同值共享平均秩
    inv = np.argsort(order)
    sorted_p = p[order]
    i = 0
    while i < len(sorted_p):
        j = i
        while j + 1 < len(sorted_p) and sorted_p[j + 1] == sorted_p[i]:
            j += 1
        if j > i:
            avg = (i + 1 + j + 1) / 2.0
            ranks[order[i:j + 1]] = avg
        i = j + 1
    n_pos = int(y.sum())
    n_neg = int(len(y) - n_pos)
    if n_neg == 0:
        return np.nan
    u = ranks[y == 1].sum() - n_pos * (n_pos + 1) / 2.0
    return float(u / (n_pos * n_neg))


def roc_auc_soft(y: np.ndarray, p: np.ndarray) -> float:
    """软标签（0-1 连续）AUC：只在 y 值不等的 pair 上计算 concordance。

    date-weighted 用（每日期一行，y=当日 escape 均值）。退化到二进制时与 roc_auc 一致。
    """
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    mask = ~np.isnan(p) & ~np.isnan(y)
    y, p = y[mask], p[mask]
    if len(y) < 2:
        return np.nan
    # pair (i,j) 仅当 y_i != y_j 时计数
    i_idx, j_idx = np.triu_indices(len(y), k=1)
    yi, yj = y[i_idx], y[j_idx]
    pi, pj = p[i_idx], p[j_idx]
    keep = yi != yj
    yi, yj, pi, pj = yi[keep], yj[keep], pi[keep], pj[keep]
    if len(yi) == 0:
        return np.nan
    concordant = ((yi > yj) & (pi > pj)) | ((yi < yj) & (pi < pj))
    return float(concordant.mean())


def pr_auc(y: np.ndarray, p: np.ndarray) -> float:
    """手写 PR-AUC（average precision，trapezoid-free AP 积分）。"""
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    mask = ~np.isnan(p)
    y, p = y[mask], p[mask]
    if len(y) == 0 or np.all(y == 0) or np.all(y == 1):
        return np.nan
    order = np.argsort(-p, kind="mergesort")
    y = y[order]
    pos = np.cumsum(y)
    recall = pos / pos[-1] if pos[-1] > 0 else np.zeros_like(pos)
    precision = pos / np.arange(1, len(y) + 1)
    # AP = sum(P_r * ΔR)
    ap = float(np.sum(precision * np.diff(np.concatenate([[0.0], recall]))))
    return ap


def brier(y: np.ndarray, p: np.ndarray) -> float:
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    mask = ~np.isnan(p)
    return float(np.mean((y[mask] - p[mask]) ** 2))


def _calibration_buckets(y: np.ndarray, p: np.ndarray,
                         edges: tuple[float, ...] = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)) -> list[dict[str, Any]]:
    """按预测概率分桶，返回每桶 mean_pred / actual_rate / n。"""
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    out: list[dict[str, Any]] = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        m = (p >= lo) & (p < hi) if i < len(edges) - 2 else (p >= lo) & (p <= hi)
        n = int(m.sum())
        out.append({
            "bucket": f"{lo:.0%}–{hi:.0%}",
            "n": n,
            "mean_pred": float(p[m].mean()) if n else None,
            "actual_rate": float(y[m].mean()) if n else None,
        })
    return out


def decision_lift(y: np.ndarray, p: np.ndarray, top: float = 0.20) -> dict[str, Any]:
    """Top {top} 高分组的实际 escape rate 与 lift（vs base rate）。"""
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    mask = ~np.isnan(p)
    y, p = y[mask], p[mask]
    if len(y) == 0:
        return {"top": top, "n": 0, "selected_rate": None, "base_rate": None, "lift": None, "n_selected": 0}
    base = float(y.mean())
    k = max(1, int(round(len(y) * top)))
    order = np.argsort(-p, kind="mergesort")[:k]
    sel = float(y[order].mean())
    return {
        "top": top,
        "n": int(len(y)),
        "n_selected": int(k),
        "selected_rate": round(sel, 4),
        "base_rate": round(base, 4),
        "lift": round(sel / base, 4) if base > 0 else None,
    }


def _date_weighted_frame(df: pd.DataFrame) -> pd.DataFrame:
    """按 first_exit_date 聚合成日期级均值（y_true 与 y_prob 各一票）。"""
    g = df.groupby("first_exit_date", sort=True)
    return g.agg(y_true=("y_true", "mean"), y_prob=("y_prob", "mean")).reset_index()


def evaluate(y_true: np.ndarray, y_prob: np.ndarray,
             date: np.ndarray | None = None) -> dict[str, Any]:
    """主评估入口：event-weighted + date-weighted（date 给出时）两套口径。"""
    y = np.asarray(y_true, dtype=float)
    p = np.asarray(y_prob, dtype=float)
    mask = ~np.isnan(p)
    y, p = y[mask], p[mask]

    event: dict[str, Any] = {
        "auc": roc_auc(y, p),
        "pr_auc": pr_auc(y, p),
        "brier": brier(y, p),
        "n": int(len(y)),
        "base_rate": float(y.mean()) if len(y) else None,
        "calibration": _calibration_buckets(y, p),
        "lift_10": decision_lift(y, p, 0.10),
        "lift_20": decision_lift(y, p, 0.20),
        "lift_30": decision_lift(y, p, 0.30),
    }

    out: dict[str, Any] = {"event_weighted": event, "date_weighted": None}
    if date is not None:
        df = pd.DataFrame({"first_exit_date": pd.to_datetime(date)[mask], "y_true": y, "y_prob": p})
        if len(df) > 1:
            dw = _date_weighted_frame(df)
            yw = dw["y_true"].to_numpy(float)
            pw = dw["y_prob"].to_numpy(float)
            out["date_weighted"] = {
                "auc": roc_auc_soft(yw, pw),
                "pr_auc": pr_auc(yw, pw),
                "brier": brier(yw, pw),
                "n_dates": int(len(dw)),
                "base_rate": float(yw.mean()),
                "calibration": _calibration_buckets(yw, pw),
                "lift_10": decision_lift(yw, pw, 0.10),
                "lift_20": decision_lift(yw, pw, 0.20),
                "lift_30": decision_lift(yw, pw, 0.30),
            }
    return out


def evaluate_frame(df: pd.DataFrame) -> dict[str, Any]:
    """对含 y_true / y_prob / first_exit_date 列的 DataFrame 评估。"""
    return evaluate(df["y_true"].to_numpy(float), df["y_prob"].to_numpy(float),
                    df["first_exit_date"].to_numpy() if "first_exit_date" in df.columns else None)
