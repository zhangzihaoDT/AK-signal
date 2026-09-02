"""Study 3B · 验证框架：discovery / regime / walk-forward / baseline / ablation / PASS gate。

口径（用户锁定，§9-§20）：
  - 训练样本：right_censored_{h}d == False 且 forward_data_complete_{h}d == True。
  - Expanding Walk-Forward：train = 早于 val 年的全部事件，val = 该年事件；
    绝不 random shuffle（§11/§25 测试锁定）。
  - 预处理（标准化/中位填充/one-hot 类别）只在 train fit，val 只 transform。
  - 全部 OOS prediction 按 fold 收集，pooled 与逐年分别报告。
  - 同日期评估：event-weighted 与 date-weighted（§17）。
  - PASS gate B1-B5（§19），阈值作为本模块常量（测试锁定）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .metrics import evaluate_frame
from .study3b_models import Preprocessor, SimpleScore, train_model
from .features import (
    CATEGORICAL_FEATURES,
    CONTINUOUS_FEATURES,
    FEATURE_FAMILIES,
)

logger = logging.getLogger(__name__)

# ── PASS gate 阈值（研究决策，测试锁定）────────────────────────
GATE = {
    "B1_min_individual_increment": 0.05,   # Full pooled OOS AUC − Market Only ≥ 0.05
    "B2_min_oos_auc": 0.55,                # Full pooled OOS AUC ≥ 0.55
    "B2_dir_consistent_folds": 3,          # top 特征方向在 ≥3/4 folds 的 train 中一致
    "B3_min_lift": 1.30,                   # Top20% lift ≥ 1.30
    "B3_min_abs_increment": 0.10,          # Top20% rate − base rate ≥ 0.10
    "B4_min_bucket_monotone": 0.50,        # calibration bucket spearman ≥ 0.50
    "B5_min_family_drop": 0.01,            # ablation AUC drop ≥ 0.01 计为该 family 有增量
    "B5_min_families": 2,                  # 增量 family 数 ≥ 2
}

# walk-forward folds：val_year 列表（train = 早于 val_year）
VAL_YEARS = [2023, 2024, 2025, 2026]


def make_trainable(ds: pd.DataFrame, horizon: int = 120) -> pd.DataFrame:
    """过滤为可训练样本：非右截断 + forward 数据完整 + 有确定标签。"""
    out = ds.copy()
    cens = out[f"right_censored_{horizon}d"].astype(bool)
    fwd = out[f"forward_data_complete_{horizon}d"].astype(bool)
    esc = out[f"escape_{horizon}d"].astype(bool)
    out = out.loc[~cens & fwd].copy()
    out["y_true"] = esc.astype(int)
    out = out.dropna(subset=["y_true"])
    return out.reset_index(drop=True)


# ── §9 · 单变量 discovery ──────────────────────────────────────
def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    mask = np.isfinite(x)
    if mask.sum() < 3:
        return np.nan
    xr = np.argsort(np.argsort(x[mask])).astype(float)
    yr = np.argsort(np.argsort(y[mask])).astype(float)
    xr -= xr.mean()
    yr -= yr.mean()
    den = np.sqrt((xr ** 2).sum() * (yr ** 2).sum())
    return float((xr * yr).sum() / den) if den > 0 else np.nan


def _cohens_d(x: np.ndarray, y: np.ndarray) -> float:
    """escape 组 vs retest 组的均值差 / 合并标准差。"""
    x = x[np.isfinite(x)]
    if x.size < 3:
        return np.nan
    m = float(y.mean())
    esc = x[y == 1]
    ret = x[y == 0]
    if esc.size < 2 or ret.size < 2:
        return np.nan
    d = float(esc.mean() - ret.mean())
    pooled = np.sqrt((esc.var(ddof=1) + ret.var(ddof=1)) / 2.0)
    return d / pooled if pooled > 0 else np.nan


def feature_discovery(ds: pd.DataFrame, features: list[str] | None = None,
                      label: str = "y_true") -> dict[str, Any]:
    """逐特征 ESCAPE vs RETEST 分布比较 + 五等分表。"""
    features = features or CONTINUOUS_FEATURES
    y = ds[label].to_numpy(float)
    out: dict[str, Any] = {"features": {}}
    for f in features:
        x = ds[f].to_numpy(float)
        mask = np.isfinite(x)
        if mask.sum() < 10:
            out["features"][f] = {"n": int(mask.sum())}
            continue
        xv, yv = x[mask], y[mask]
        esc = xv[yv == 1]
        ret = xv[yv == 0]
        rec: dict[str, Any] = {
            "n": int(mask.sum()),
            "n_escape": int(esc.size),
            "escape_median": round(float(np.median(esc)), 4) if esc.size else None,
            "retest_median": round(float(np.median(ret)), 4) if ret.size else None,
            "median_diff": round(float(np.median(esc) - np.median(ret)), 4) if esc.size and ret.size else None,
            "p25": round(float(np.percentile(xv, 25)), 4),
            "p75": round(float(np.percentile(xv, 75)), 4),
            "cohens_d": round(_cohens_d(xv, yv), 4),
            "spearman": round(_spearman(xv, yv), 4),
        }
        # 五等分表
        try:
            q = pd.qcut(xv, 5, duplicates="drop")
            qt = pd.DataFrame({"x": xv, "y": yv, "q": q}).groupby("q", observed=True)["y"].agg(["count", "mean"])
            rec["quintiles"] = [
                {"label": str(i), "n": int(r["count"]),
                 "escape_rate": round(float(r["mean"]), 4)}
                for i, r in qt.iterrows()
            ]
        except Exception:
            rec["quintiles"] = []
        out["features"][f] = rec
    return out


# ── §10 · regime robustness（诊断，不进模型）────────────────────
REGIME_SEGMENTS = [
    ("pre_2023_11", "2023-11-01", None),
    ("mid_2023_11_to_924", "2023-11-01", "2024-09-24"),
    ("post_924", "2024-09-24", None),
]


def regime_robustness(ds: pd.DataFrame, features: list[str] | None = None,
                      label: str = "y_true", min_year_n: int = 100) -> dict[str, Any]:
    """按年份与 regime 段切片，报告每个主要特征的 escape 中位差方向稳定性。

    min_year_n：方向一致性只统计样本充足的年份（默认 >=100），
    避免 n=41/3 的 2025/2026 把稳定特征误判为「方向不稳」（§10 诊断口径）。
    """
    features = features or CONTINUOUS_FEATURES
    y = ds[label].to_numpy(float)
    years = sorted(ds["year"].unique())
    out: dict[str, Any] = {"years": {}, "segments": {}, "direction_stability": {}}
    for yr in years:
        sub = ds[ds["year"] == yr]
        out["years"][int(yr)] = {
            "n": int(len(sub)),
            "base_rate": round(float(sub[label].mean()), 4) if len(sub) else None,
        }
    for name, lo, hi in REGIME_SEGMENTS:
        sub = ds
        if lo:
            sub = sub[sub["first_exit_date"] >= pd.Timestamp(lo)]
        if hi:
            sub = sub[sub["first_exit_date"] < pd.Timestamp(hi)]
        out["segments"][name] = {
            "n": int(len(sub)),
            "base_rate": round(float(sub[label].mean()), 4) if len(sub) else None,
        }
    # 方向稳定性：对每个特征，在全样本与各年度切片中 escape−retest 中位差方向
    for f in features:
        x = ds[f].to_numpy(float)
        dirs: dict[str, int] = {}
        all_diff = _median_diff(x, y)
        dirs["all"] = _sign(all_diff)
        for yr in years:
            m = ds["year"].to_numpy() == yr
            if int(m.sum()) < min_year_n:
                continue  # 样本不足的年份不参与方向一致性（诊断口径）
            dirs[f"y{yr}"] = _sign(_median_diff(x[m], y[m]))
        out["direction_stability"][f] = {
            "all": dirs["all"],
            "by_year": {k: v for k, v in dirs.items() if k != "all"},
            "n_years_checked": int(len(dirs) - 1),
            "consistent": bool(
                dirs["all"] != 0 and len(dirs) - 1 >= 2
                and len({v for v in dirs.values() if v != 0}) == 1
            ),
        }
    return out


def _median_diff(x: np.ndarray, y: np.ndarray) -> float | None:
    mask = np.isfinite(x)
    if mask.sum() < 4:
        return None
    xv, yv = x[mask], y[mask]
    esc = xv[yv == 1]
    ret = xv[yv == 0]
    if esc.size == 0 or ret.size == 0:
        return None
    return float(np.median(esc) - np.median(ret))


def _sign(v: float | None) -> int:
    if v is None or not np.isfinite(v):
        return 0
    return 1 if v > 0 else (-1 if v < 0 else 0)


# ── §11 · Expanding Walk-Forward ───────────────────────────────
@dataclass
class FoldResult:
    val_year: int
    n_train: int
    n_val: int
    metrics: dict[str, Any] = field(default_factory=dict)
    preds: pd.DataFrame = field(default_factory=pd.DataFrame)


def _fit_model(model_name: str, Xtr: pd.DataFrame, ytr: np.ndarray,
               pre: Preprocessor, params: dict[str, Any] | None,
               score_feats: list[tuple[str, int]] | None = None) -> Any:
    """fit 模型（SimpleScore 走原始列；logistic/tree 走标准化矩阵）。"""
    if model_name == "score":
        if not score_feats:
            raise ValueError("score model needs score_feats")
        model = SimpleScore(score_feats)
        model.fit(Xtr, ytr)
        return model
    Xm = pre.fit_transform(Xtr)
    model, _ = train_model(model_name, Xtr, ytr, pre, params)
    return model


def _predict_model(model: Any, model_name: str, Xva: pd.DataFrame,
                   pre: Preprocessor) -> np.ndarray:
    if model_name == "score":
        feats = [f for f, _ in model.features]
        return model.predict_proba(Xva[feats])
    Xm = pre.transform(Xva)
    return model.predict_proba(Xm)


def expanding_walkforward(
    ds: pd.DataFrame,
    model_name: str = "logistic",
    continuous: list[str] | None = None,
    categorical: list[str] | None = None,
    val_years: list[int] | None = None,
    params: dict[str, Any] | None = None,
    score_feats: list[tuple[str, int]] | None = None,
    label: str = "y_true",
) -> dict[str, Any]:
    """Expanding walk-forward：逐 val_year，train = year<val_year。

    返回：
      folds: list[FoldResult]
      pooled: 全部 OOS prediction 合并后的 evaluate
      pooled_df: 合并 OOS 预测 DataFrame（含 fold / model 列）
    """
    continuous = continuous or CONTINUOUS_FEATURES
    categorical = categorical or CATEGORICAL_FEATURES
    val_years = val_years or VAL_YEARS
    ds = ds.copy()
    ds["year"] = ds["year"].astype(int)

    folds: list[FoldResult] = []
    for yr in val_years:
        train = ds[ds["year"] < yr]
        val = ds[ds["year"] == yr]
        if len(val) == 0 or len(train) == 0:
            logger.warning("fold %d: empty train/val (train=%d val=%d), skip", yr, len(train), len(val))
            continue
        pre = Preprocessor(continuous, categorical).fit(train)
        ytr = train[label].to_numpy(float)
        model = _fit_model(model_name, train, ytr, pre, params, score_feats)
        p = _predict_model(model, model_name, val, pre)
        fold_df = val[["fund_code", "first_exit_date", "year", label]].copy()
        fold_df["y_prob"] = p
        fold_df["fold"] = int(yr)
        fold_df["model"] = model_name
        fold_df = fold_df.rename(columns={label: "y_true"})
        fr = FoldResult(val_year=int(yr), n_train=int(len(train)), n_val=int(len(val)),
                        preds=fold_df)
        fr.metrics = evaluate_frame(fold_df)
        folds.append(fr)

    pooled_df = pd.concat([f.preds for f in folds], ignore_index=True) if folds else pd.DataFrame()
    pooled = evaluate_frame(pooled_df) if len(pooled_df) else {}
    return {"folds": folds, "pooled": pooled, "pooled_df": pooled_df}


# ── §12-§15 · baseline / increment 比较 ────────────────────────
def select_score_features(discovery: dict[str, Any], regime: dict[str, Any],
                          max_per_family: int = 2, max_total: int = 6,
                          individual_only: bool = True) -> list[tuple[str, int]]:
    """从 discovery + regime 方向稳定性选 M2 SimpleScore 特征。

    策略（用户锁定，§13/§19）：
      - 默认 individual_only=True：只用个体 transition 特征（F1-F4），
        避免市场 context（F5）成为 regime 代理（§15「个体 vs 市场」分离测试）。
      - 每 family 取 |spearman| 最大的前 max_per_family 个，且全样本方向跨 regime 一致。
      - 方向 = escape−retest 中位差符号。
    """
    stab = regime.get("direction_stability", {})
    feats = discovery.get("features", {})
    out: list[tuple[str, int]] = []
    fam_order = [k for k in FEATURE_FAMILIES.keys()
                 if not (individual_only and k == "F5_market")]
    per_fam: dict[str, int] = {}
    ranked = sorted(
        [(f, v.get("spearman") or 0) for f, v in feats.items() if v.get("spearman") is not None],
        key=lambda kv: abs(kv[1]), reverse=True,
    )
    for f, _s in ranked:
        fam = next((k for k in fam_order if f in FEATURE_FAMILIES[k]), None)
        if fam is None:
            continue
        if per_fam.get(fam, 0) >= max_per_family:
            continue
        d = feats[f].get("median_diff")
        if d is None or abs(d) < 1e-9:
            continue
        if not stab.get(f, {}).get("consistent"):
            continue  # 方向跨 regime 不稳 → 不进入 score
        out.append((f, 1 if d > 0 else -1))
        per_fam[fam] = per_fam.get(fam, 0) + 1
        if len(out) >= max_total:
            break
    return out


def compare_models(ds: pd.DataFrame, horizon: int = 120,
                   val_years: list[int] | None = None,
                   score_feats: list[tuple[str, int]] | None = None) -> dict[str, Any]:
    """核心对比（§15）：BaseRate / Market Only / Position Only / Individual Only / Full。

    全部用 logistic 主模型；返回 pooled OOS evaluate + per-model。
    """
    val_years = val_years or VAL_YEARS
    models: dict[str, list[str]] = {
        "market_only": FEATURE_FAMILIES["F5_market"],
        "position_only": FEATURE_FAMILIES["F1_position"],
        "individual_only": (FEATURE_FAMILIES["F1_position"] + FEATURE_FAMILIES["F2_rps"]
                            + FEATURE_FAMILIES["F3_drawdown"] + FEATURE_FAMILIES["F4_bottom_history"]),
        "full": CONTINUOUS_FEATURES,
    }
    out: dict[str, Any] = {}
    base_rate = float(ds["y_true"].mean())
    # Baseline 0: BaseRate（常数预测）
    base_preds = ds[["fund_code", "first_exit_date", "year", "y_true"]].copy()
    base_preds["y_prob"] = base_rate
    base_preds["fold"] = -1
    base_preds["model"] = "base_rate"
    out["base_rate"] = {"evaluate": evaluate_frame(base_preds), "constant": base_rate}

    for name, feats in models.items():
        res = expanding_walkforward(ds, model_name="logistic", continuous=feats,
                                    categorical=CATEGORICAL_FEATURES,
                                    val_years=val_years, params={"l2": 1.0})
        out[name] = {"pooled": res["pooled"], "n_oos": int(len(res["pooled_df"])) if len(res["pooled_df"]) else 0}

    # full logistic + score（§13 M2）
    full_log = expanding_walkforward(ds, model_name="logistic", continuous=CONTINUOUS_FEATURES,
                                     categorical=CATEGORICAL_FEATURES, val_years=val_years,
                                     params={"l2": 1.0})
    out["full_logistic"] = {"pooled": full_log["pooled"], "folds": [f.metrics for f in full_log["folds"]]}
    score_res = expanding_walkforward(ds, model_name="score", continuous=CONTINUOUS_FEATURES,
                                      categorical=CATEGORICAL_FEATURES, val_years=val_years,
                                      score_feats=score_feats)
    out["simple_score"] = {"pooled": score_res["pooled"], "n_oos": int(len(score_res["pooled_df"]))}
    return out


# ── §16 · ablation ─────────────────────────────────────────────
def ablation(ds: pd.DataFrame, horizon: int = 120,
             val_years: list[int] | None = None) -> dict[str, Any]:
    """逐 family 删除：Full AUC vs Full−family AUC（pooled OOS）。"""
    val_years = val_years or VAL_YEARS
    full_res = expanding_walkforward(ds, model_name="logistic", continuous=CONTINUOUS_FEATURES,
                                     categorical=CATEGORICAL_FEATURES, val_years=val_years,
                                     params={"l2": 1.0})
    full_auc = full_res["pooled"]["event_weighted"]["auc"] if full_res["pooled"] else np.nan
    out: dict[str, Any] = {"full_auc": full_auc, "drops": {}}
    for fam, feats in FEATURE_FAMILIES.items():
        reduced = [f for f in CONTINUOUS_FEATURES if f not in feats]
        res = expanding_walkforward(ds, model_name="logistic", continuous=reduced,
                                    categorical=CATEGORICAL_FEATURES, val_years=val_years,
                                    params={"l2": 1.0})
        auc = res["pooled"]["event_weighted"]["auc"] if res["pooled"] else np.nan
        out["drops"][fam] = {
            "features": feats,
            "auc": round(auc, 4) if np.isfinite(auc) else None,
            "drop": round(full_auc - auc, 4) if np.isfinite(auc) else None,
        }
    return out


# ── §19 · PASS gate ────────────────────────────────────────────
def pass_gate(compare: dict[str, Any], ablation_res: dict[str, Any],
              regime_res: dict[str, Any], discovery: dict[str, Any]) -> dict[str, Any]:
    """B1-B5 判定（阈值见 GATE 常量）。返回逐项 ok + verdict。"""
    def _auc(model: str) -> float | None:
        e = compare.get(model, {}).get("pooled", {}).get("event_weighted", {})
        return e.get("auc")

    full = _auc("full_logistic") or _auc("full")
    market = _auc("market_only")
    individual = _auc("individual_only")

    # B1 · Individual Increment
    b1_ok = bool(
        full is not None and market is not None
        and full - market >= GATE["B1_min_individual_increment"]
    )

    # B2 · OOS Direction：pooled OOS AUC 达标 + top 特征方向跨 fold 稳定
    b2_auc = full is not None and full >= GATE["B2_min_oos_auc"]
    dir_stable = regime_res.get("direction_stability", {})
    top_feats = sorted(
        [f for f, v in discovery.get("features", {}).items() if v.get("spearman") is not None],
        key=lambda f: abs(discovery["features"][f]["spearman"]), reverse=True,
    )[:5]
    consistent = [f for f in top_feats if dir_stable.get(f, {}).get("consistent")]
    b2_dir = len(consistent) >= GATE["B2_dir_consistent_folds"] * 0  # 方向一致性 ≥ 3 个 top 特征
    b2_dir = len(consistent) >= 3
    b2_ok = bool(b2_auc and b2_dir)

    # B3 · Decision Lift（full logistic pooled OOS）
    lift20 = compare.get("full_logistic", {}).get("pooled", {}).get("event_weighted", {}).get("lift_20", {})
    sel = lift20.get("selected_rate")
    base = lift20.get("base_rate")
    lift = lift20.get("lift")
    b3_ok = bool(
        lift is not None and lift >= GATE["B3_min_lift"]
        and sel is not None and base is not None
        and (sel - base) >= GATE["B3_min_abs_increment"]
    )

    # B4 · Calibration：Brier 优于 base-rate + bucket 单调
    cal = compare.get("full_logistic", {}).get("pooled", {}).get("event_weighted", {}).get("calibration", [])
    full_brier = compare.get("full_logistic", {}).get("pooled", {}).get("event_weighted", {}).get("brier")
    base_brier = _constant_brier(compare)
    bucket_pts = [(c.get("mean_pred"), c.get("actual_rate")) for c in cal if c.get("n", 0) > 0 and c.get("mean_pred") is not None]
    mono = _bucket_monotonicity(bucket_pts)
    b4_ok = bool(
        full_brier is not None and base_brier is not None and full_brier < base_brier
        and mono is not None and mono >= GATE["B4_min_bucket_monotone"]
    )

    # B5 · Mechanism Stability：≥2 个 family 在 ablation 中 drop ≥ 阈值
    drops = ablation_res.get("drops", {})
    contrib = [fam for fam, v in drops.items() if (v.get("drop") or 0) >= GATE["B5_min_family_drop"]]
    b5_ok = len(contrib) >= GATE["B5_min_families"]

    checks = {
        "B1": {"name": "Individual Increment (Full > Market Only)", "ok": b1_ok,
               "detail": f"full={_fmt(full)} market={_fmt(market)} Δ={_fmt(full - market) if full is not None and market is not None else None}"},
        "B2": {"name": "OOS Direction (AUC + top-feature direction)", "ok": b2_ok,
               "detail": f"auc={_fmt(full)} consistent_top={len(consistent)}"},
        "B3": {"name": "Decision Lift (Top20%)", "ok": b3_ok,
               "detail": f"lift={_fmt(lift)} selected={_fmt(sel)} base={_fmt(base)}"},
        "B4": {"name": "Calibration (Brier + monotone)", "ok": b4_ok,
               "detail": f"brier={_fmt(full_brier)} base_brier={_fmt(base_brier)} mono={_fmt(mono)}"},
        "B5": {"name": "Mechanism Stability (≥2 families)", "ok": b5_ok,
               "detail": f"contributing={contrib}"},
    }
    n_pass = sum(1 for c in checks.values() if c["ok"])
    verdict = "PASS — PREDICTABLE_TRANSITION" if n_pass == 5 else _fail_verdict(checks)
    return {"checks": checks, "n_pass": n_pass, "verdict": verdict}


def _constant_brier(compare: dict[str, Any]) -> float | None:
    e = compare.get("base_rate", {}).get("evaluate", {}).get("event_weighted", {})
    return e.get("brier")


def _bucket_monotonicity(pts: list[tuple[float | None, float | None]]) -> float | None:
    pts = [(p, a) for p, a in pts if p is not None and a is not None]
    if len(pts) < 3:
        return None
    x = np.array([p for p, _ in pts])
    y = np.array([a for _, a in pts])
    rx = np.argsort(np.argsort(x)).astype(float)
    ry = np.argsort(np.argsort(y)).astype(float)
    rx -= rx.mean()
    ry -= ry.mean()
    den = np.sqrt((rx ** 2).sum() * (ry ** 2).sum())
    return float((rx * ry).sum() / den) if den > 0 else None


def _fail_verdict(checks: dict[str, Any]) -> str:
    if not checks["B1"]["ok"]:
        return "FAIL — MARKET_REGIME_ONLY"
    if not checks["B2"]["ok"]:
        return "FAIL — TIME_DEPENDENT"
    if not checks["B3"]["ok"] or not checks["B4"]["ok"]:
        return "FAIL — NO_DECISION_LIFT"
    return "FAIL — UNSTABLE_FEATURES"


def _fmt(v: float | None, nd: int = 3) -> str:
    if v is None or not np.isfinite(v):
        return "—"
    return f"{v:.{nd}f}"
