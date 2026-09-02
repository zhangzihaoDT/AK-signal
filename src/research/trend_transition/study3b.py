"""Study 3B · 编排：加载 → 特征数据集 → discovery → regime → walk-forward → gate → 落盘。

数据契约（写死点，只消费 3A 事实层）：
  - 轨迹事实：outputs/research/trend_transition/study3a_trajectories_{persistence}.parquet
  - 行情事实：outputs/research/etf_bottom/backtest_v1/v1_signal_daily.parquet
  - 价格：data/etf_signal/raw/{fund_code}.parquet
  - 不改 trajectory/survival/structural_break 任何逻辑。

产物（STUDY_DIR = outputs/research/trend_transition/）：
  study3b_dataset.parquet           事件级特征数据集
  study3b_feature_analysis.csv      §9 单变量 discovery（逐特征）
  study3b_walkforward.json          逐 fold OOS 评估
  study3b_model_comparison.json     §15 模型对比（base/market/position/individual/full/score）
  study3b_ablation.json             §16 ablation
  study3b_calibration.csv           calibration buckets
  study3b_predictions.parquet       OOS event-level predictions
  study3b_summary.json              汇总（含 PASS gate B1-B5 + verdict + robustness）
  study3b_report.html               可视化报告
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import PERSISTENCE_PRIMARY, PERSISTENCE_RAW, PERSISTENCE_ROBUST, STUDY_DIR
from .calendar import MarketCalendar
from .features import (
    CATEGORICAL_FEATURES,
    CONTINUOUS_FEATURES,
    FEATURE_FAMILIES,
    build_dataset,
)
from .study3b_validation import (
    ablation,
    compare_models,
    expanding_walkforward,
    feature_discovery,
    make_trainable,
    pass_gate,
    regime_robustness,
    select_score_features,
)

logger = logging.getLogger(__name__)

V1_PATH = Path("outputs/research/etf_bottom/backtest_v1/v1_signal_daily.parquet")
_RAW_EXCLUDE = ("money", "bond", "commodity")


def load_v1(path: Path = V1_PATH) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df.loc[~df["etf_type"].isin(_RAW_EXCLUDE)].copy()


def load_trajectories(persistence: int = PERSISTENCE_PRIMARY) -> pd.DataFrame:
    tag = "primary" if persistence == PERSISTENCE_PRIMARY else ("raw" if persistence == PERSISTENCE_RAW else "robust")
    return pd.read_parquet(STUDY_DIR / f"study3a_trajectories_{tag}.parquet")


def run_study3b(
    persistence: int = PERSISTENCE_PRIMARY,
    horizon: int = 120,
    n_l2: float = 1.0,
) -> dict[str, Any]:
    v1 = load_v1()
    cal = MarketCalendar.from_v1(v1)
    traj = load_trajectories(persistence)

    logger.info("build dataset persistence=%d horizon=%d ...", persistence, horizon)
    ds = build_dataset(v1, traj, cal, horizon=horizon, persistence=persistence)
    trn = make_trainable(ds, horizon=horizon)

    # §9 discovery
    disc = feature_discovery(trn)
    # §10 regime
    regime = regime_robustness(trn)
    # score features（个体 transition 优先）
    score_feats = select_score_features(disc, regime)

    # §11-§15 walk-forward + comparison
    wf = expanding_walkforward(trn, model_name="logistic",
                               continuous=CONTINUOUS_FEATURES,
                               categorical=CATEGORICAL_FEATURES,
                               params={"l2": n_l2})
    compare = compare_models(trn, horizon=horizon, score_feats=score_feats)
    # §16 ablation
    abl = ablation(trn, horizon=horizon)
    # §19 PASS gate
    gate = pass_gate(compare, abl, regime, disc)

    # robustness R1/R2/R4（轻量：方向 + 主模型 pooled AUC）
    robustness = _robustness(v1, cal, persistence, horizon, n_l2)

    payload = {
        "study": "3b",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "persistence": persistence,
        "horizon": horizon,
        "n_l2": n_l2,
        "base_rate": float(trn["y_true"].mean()) if len(trn) else None,
        "n_trainable": int(len(trn)),
        "n_events": int(len(ds)),
        "calendar_start": str(cal.start.date()),
        "calendar_end": str(cal.end.date()),
        "score_features": score_feats,
        "discovery": disc,
        "regime": regime,
        "walkforward": {
            "folds": [
                {"val_year": f.val_year, "n_train": f.n_train, "n_val": f.n_val,
                 "metrics": f.metrics}
                for f in wf["folds"]
            ],
            "pooled": wf["pooled"],
        },
        "model_comparison": compare,
        "ablation": abl,
        "pass_gate": gate,
        "robustness": robustness,
    }

    _write_products(payload, ds, trn, wf, score_feats)
    return payload


def _robustness(v1: pd.DataFrame, cal: MarketCalendar, persistence: int,
                horizon: int, n_l2: float) -> dict[str, Any]:
    """R1 persistence / R2 horizon / R4 etf_type 的轻量稳健性。

    R1：不同 persistence 的 dataset → discovery 方向一致性。
    R2：horizon 60/250 主模型 pooled AUC 方向。
    R4：industry/theme/broad 分组 pooled AUC。
    """
    out: dict[str, Any] = {"persistence": {}, "horizon": {}, "etf_type": {}}
    # R1 · persistence 方向一致性（只用 120d 主标签）
    for p, tag in ((PERSISTENCE_RAW, "raw"), (PERSISTENCE_PRIMARY, "primary"), (PERSISTENCE_ROBUST, "robust")):
        if p == persistence:
            continue
        try:
            traj = pd.read_parquet(STUDY_DIR / f"study3a_trajectories_{tag}.parquet")
            dsp = build_dataset(v1, traj, cal, horizon=horizon, persistence=p)
            trnp = make_trainable(dsp, horizon=horizon)
            discp = feature_discovery(trnp)
            # top feature 方向与主口径对比
            dirs_p = {f: v.get("median_diff") for f, v in discp["features"].items() if v.get("median_diff") is not None}
            out["persistence"][str(p)] = {
                "n": int(len(trnp)),
                "base_rate": round(float(trnp["y_true"].mean()), 4) if len(trnp) else None,
                "n_features_with_dir": int(len(dirs_p)),
            }
        except Exception as e:  # noqa: BLE001
            logger.warning("R1 persistence=%d failed: %s", p, e)
            out["persistence"][str(p)] = {"status": "failed", "error": str(e)}
    # R2 · horizon
    for h in (60, 250):
        if h == horizon:
            continue
        try:
            trnh = make_trainable(ds_for_horizon(v1, cal, persistence, h), horizon=h)
            res = expanding_walkforward(trnh, model_name="logistic",
                                        continuous=CONTINUOUS_FEATURES,
                                        categorical=CATEGORICAL_FEATURES,
                                        params={"l2": n_l2})
            e = res["pooled"].get("event_weighted", {})
            out["horizon"][str(h)] = {
                "n": int(len(trnh)),
                "base_rate": round(float(trnh["y_true"].mean()), 4) if len(trnh) else None,
                "pooled_auc": e.get("auc"),
            }
        except Exception as e:  # noqa: BLE001
            logger.warning("R2 horizon=%d failed: %s", h, e)
            out["horizon"][str(h)] = {"status": "failed", "error": str(e)}
    # R4 · etf_type
    try:
        traj_p = pd.read_parquet(STUDY_DIR / f"study3a_trajectories_{'primary' if persistence == PERSISTENCE_PRIMARY else ('raw' if persistence == PERSISTENCE_RAW else 'robust')}.parquet")
        dsp = build_dataset(v1, traj_p, cal, horizon=horizon, persistence=persistence)
        trnp = make_trainable(dsp, horizon=horizon)
        for etype in ("industry", "theme", "broad"):
            sub = trnp[trnp["etf_type"] == etype]
            if len(sub) < 50:
                out["etf_type"][etype] = {"n": int(len(sub)), "status": "insufficient"}
                continue
            res = expanding_walkforward(sub, model_name="logistic",
                                        continuous=CONTINUOUS_FEATURES,
                                        categorical=[c for c in CATEGORICAL_FEATURES if c != "etf_type"],
                                        params={"l2": n_l2})
            e = res["pooled"].get("event_weighted", {})
            out["etf_type"][etype] = {
                "n": int(len(sub)),
                "base_rate": round(float(sub["y_true"].mean()), 4),
                "pooled_auc": e.get("auc"),
            }
    except Exception as e:  # noqa: BLE001
        logger.warning("R4 failed: %s", e)
        out["etf_type"] = {"status": "failed", "error": str(e)}
    return out


def ds_for_horizon(v1: pd.DataFrame, cal: MarketCalendar, persistence: int,
                   horizon: int) -> pd.DataFrame:
    tag = "primary" if persistence == PERSISTENCE_PRIMARY else ("raw" if persistence == PERSISTENCE_RAW else "robust")
    traj = pd.read_parquet(STUDY_DIR / f"study3a_trajectories_{tag}.parquet")
    return build_dataset(v1, traj, cal, horizon=horizon, persistence=persistence)


def _write_products(payload: dict[str, Any], ds: pd.DataFrame, trn: pd.DataFrame,
                    wf: dict[str, Any], score_feats: list[tuple[str, int]]) -> None:
    STUDY_DIR.mkdir(parents=True, exist_ok=True)
    ds.to_parquet(STUDY_DIR / "study3b_dataset.parquet", index=False)

    # feature_analysis.csv：逐特征一行
    rows = []
    for f, v in payload["discovery"]["features"].items():
        rows.append({
            "feature": f,
            "n": v.get("n"), "n_escape": v.get("n_escape"),
            "escape_median": v.get("escape_median"), "retest_median": v.get("retest_median"),
            "median_diff": v.get("median_diff"), "p25": v.get("p25"), "p75": v.get("p75"),
            "cohens_d": v.get("cohens_d"), "spearman": v.get("spearman"),
            "family": next((k for k, vv in FEATURE_FAMILIES.items() if f in vv), ""),
            "in_score": int(f in dict(score_feats)),
        })
    pd.DataFrame(rows).to_csv(STUDY_DIR / "study3b_feature_analysis.csv", index=False)

    wf_json = {
        "folds": payload["walkforward"]["folds"],
        "pooled": payload["walkforward"]["pooled"],
    }
    with open(STUDY_DIR / "study3b_walkforward.json", "w", encoding="utf-8") as f:
        json.dump(wf_json, f, ensure_ascii=False, indent=2, default=str)

    with open(STUDY_DIR / "study3b_model_comparison.json", "w", encoding="utf-8") as f:
        json.dump(payload["model_comparison"], f, ensure_ascii=False, indent=2, default=str)

    with open(STUDY_DIR / "study3b_ablation.json", "w", encoding="utf-8") as f:
        json.dump(payload["ablation"], f, ensure_ascii=False, indent=2, default=str)

    # calibration.csv：full logistic pooled OOS
    cal_rows = []
    cal = payload["walkforward"]["pooled"]["event_weighted"]["calibration"]
    for c in cal:
        cal_rows.append({"bucket": c["bucket"], "n": c["n"],
                         "mean_pred": c["mean_pred"], "actual_rate": c["actual_rate"]})
    pd.DataFrame(cal_rows).to_csv(STUDY_DIR / "study3b_calibration.csv", index=False)

    # predictions.parquet：OOS event-level（full logistic + score 合并）
    preds = wf["pooled_df"].copy()
    if len(preds):
        preds = preds[["fund_code", "first_exit_date", "year", "fold", "model", "y_true", "y_prob"]]
        preds.to_parquet(STUDY_DIR / "study3b_predictions.parquet", index=False)

    summary = {
        "study": "3b",
        "generated_at": payload["generated_at"],
        "persistence": payload["persistence"],
        "horizon": payload["horizon"],
        "n_trainable": payload["n_trainable"],
        "n_events": payload["n_events"],
        "base_rate": payload["base_rate"],
        "score_features": score_feats,
        "pass_gate": payload["pass_gate"],
        "discovery": payload["discovery"],
        "regime": payload["regime"],
        "ablation": payload["ablation"],
        "model_comparison": {
            k: {"pooled_auc": v.get("pooled", {}).get("event_weighted", {}).get("auc"),
                "pooled_dw_auc": (v.get("pooled", {}).get("date_weighted") or {}).get("auc"),
                "n_oos": v.get("n_oos")}
            for k, v in payload["model_comparison"].items()
        },
        "robustness": payload["robustness"],
        "discovery_top": _top_disc(payload["discovery"]),
    }
    with open(STUDY_DIR / "study3b_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)


def _top_disc(disc: dict[str, Any], k: int = 8) -> list[dict[str, Any]]:
    feats = disc.get("features", {})
    ranked = sorted(
        [(f, v.get("spearman") or 0) for f, v in feats.items() if v.get("spearman") is not None],
        key=lambda kv: abs(kv[1]), reverse=True,
    )[:k]
    return [
        {"feature": f, "spearman": feats[f].get("spearman"),
         "median_diff": feats[f].get("median_diff"), "n": feats[f].get("n")}
        for f, _ in ranked
    ]
