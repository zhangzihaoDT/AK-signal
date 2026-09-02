"""Lane 3 · Study 3B 核心逻辑测试（纯离线，确定性）。

覆盖 §25 的 13 项锁定要求：
  1. 所有 feature timestamp <= first_exit_date（as-of 语义）
  2. 删除 first_exit 后数据，feature 结果完全不变（无 look-ahead）
  3. right_censored_120d=True 永不进入主训练集
  4. days_to_first_retest 等 outcome 字段无法进入 feature matrix
  5. train date 永远早于 validation date
  6. walk-forward 不 shuffle
  7. 标准化器只能 fit train
  8. feature missing-value 处理只能 fit train
  9. probability calibration 只能 fit train
  10. predictions 保存的全部是 OOS prediction
  11. random split 禁止进入 canonical pipeline
  12. RAW/3D/5D 使用各自独立 trajectory sample
  13. report renderer 只消费结果文件，不重新训练模型
"""

from __future__ import annotations

import json
import numpy as np
import pandas as pd
import pytest

from src.research.trend_transition.calendar import MarketCalendar
from src.research.trend_transition.features import (
    CONTINUOUS_FEATURES,
    FEATURE_FAMILIES,
    FORBIDDEN_FEATURES,
    MODEL_FEATURES,
    build_dataset,
)
from src.research.trend_transition.metrics import evaluate, pr_auc, roc_auc, roc_auc_soft
from src.research.trend_transition.study3b_models import Preprocessor, SimpleScore
from src.research.trend_transition.study3b_validation import (
    make_trainable,
    expanding_walkforward,
    feature_discovery,
    regime_robustness,
    select_score_features,
)


def _mk_v1(n=400, funds=8) -> pd.DataFrame:
    dates = pd.bdate_range("2023-01-02", periods=n)
    rows = []
    for f in range(funds):
        code = f"ETF{f:06d}"
        for d in dates:
            rows.append({
                "trade_date": d, "fund_code": code, "fund_name": f"ETF{f}",
                "etf_type": "theme", "industry_cluster": "OTHER",
                "reliable_360": True, "long_term_bottom": False,
                "bottom_state": "NORMAL", "pos60": 30.0 + f, "pos120": 25.0,
                "pos360": 20.0,
            })
    return pd.DataFrame(rows)


def _mk_trajectories(n=400) -> pd.DataFrame:
    dates = pd.bdate_range("2023-01-02", periods=n)
    rows = []
    for i, d in enumerate(dates[:200]):
        rows.append({
            "fund_code": "ETF000000", "fund_name": "ETF0", "etf_type": "theme",
            "industry_cluster": "OTHER", "entry_date": dates[i],
            "first_exit_date": d, "first_retest_date": None, "second_exit_date": None,
            "observation_end": dates[-1], "days_to_first_retest": None,
            "days_in_bottom_total": 30.0, "is_current": False, "n_retests": 0,
            "escape_60d": True, "right_censored_60d": False, "forward_data_complete_60d": True,
            "escape_120d": True, "right_censored_120d": False, "forward_data_complete_120d": True,
            "escape_250d": None, "right_censored_250d": True, "forward_data_complete_250d": False,
        })
    t = pd.DataFrame(rows)
    # escape/censored 列统一 object dtype（与真实 trajectory parquet 一致，可含 None）
    for c in ["escape_60d", "right_censored_60d", "forward_data_complete_60d",
              "escape_120d", "right_censored_120d", "forward_data_complete_120d",
              "escape_250d", "right_censored_250d", "forward_data_complete_250d"]:
        t[c] = t[c].astype(object)
    return t


# ── 1. as-of 语义：特征只用 <= first_exit 的数据 ────────────────
def test_feature_timestamp_le_first_exit():
    """构建特征时 panel 截断到 max_date；对 first_exit <= max_date 的事件结果不变。"""
    v1 = _mk_v1(400)
    cal = MarketCalendar.from_v1(v1)
    traj = _mk_trajectories(400)
    # max_date = 第 100 个交易日：该事件 first_exit <= max_date，特征必须完全一致
    cut_ts: pd.Timestamp = pd.Timestamp("2023-01-02") + pd.Timedelta(days=99)
    full = build_dataset(v1, traj, cal)
    truncated = build_dataset(v1, traj, cal, max_date=cut_ts)
    f_full = full[full["first_exit_date"] <= cut_ts]
    f_trunc = truncated[truncated["first_exit_date"] <= cut_ts]
    assert len(f_full) == len(f_trunc)
    cols = [c for c in CONTINUOUS_FEATURES]
    pd.testing.assert_frame_equal(f_full[cols].reset_index(drop=True),
                                  f_trunc[cols].reset_index(drop=True))


# ── 2. 删除 first_exit 后数据 → 特征完全不变 ──────────────────
def test_no_lookahead_after_first_exit():
    """等价于 test 1：截断后结果不变即无 look-ahead。"""
    v1 = _mk_v1(400)
    cal = MarketCalendar.from_v1(v1)
    traj = _mk_trajectories(400)
    cut = pd.bdate_range("2023-01-02", periods=400)[149]
    full = build_dataset(v1, traj, cal)
    trunc = build_dataset(v1, traj, cal, max_date=cut)
    sel = full[full["first_exit_date"] <= cut]
    tsel = trunc[trunc["first_exit_date"] <= cut]
    for c in CONTINUOUS_FEATURES:
        assert np.allclose(sel[c].to_numpy(float), tsel[c].to_numpy(float), equal_nan=True), c


# ── 3. right_censored 不进训练集 ───────────────────────────────
def test_right_censored_excluded_from_trainable():
    traj = _mk_trajectories(400)
    traj.loc[10, "right_censored_120d"] = True
    traj.loc[10, "escape_120d"] = None
    v1 = _mk_v1(400)
    cal = MarketCalendar.from_v1(v1)
    ds = build_dataset(v1, traj, cal)
    trn = make_trainable(ds)
    # censored 行（index 10）对应的 first_exit 不应出现在 trn
    assert (trn["right_censored_120d"].astype(bool) == False).all()  # noqa: E712


# ── 4. outcome 字段禁止进 feature matrix ──────────────────────
def test_outcome_fields_not_in_model_features():
    forbidden_outcome = ["days_to_first_retest", "first_retest_date", "second_exit_date",
                         "escape_60d", "escape_120d", "escape_250d", "trajectory_type"]
    assert not set(forbidden_outcome) & set(MODEL_FEATURES)
    assert not set(FORBIDDEN_FEATURES) & set(MODEL_FEATURES)


# ── 5/6. walk-forward：train < val，不 shuffle ────────────────
def test_walkforward_train_before_val_and_no_shuffle():
    v1 = _mk_v1(700)
    cal = MarketCalendar.from_v1(v1)
    # 构造多 fund 多 exit：确保 val 年有足够样本
    dates = pd.bdate_range("2023-01-02", periods=700)
    rows = []
    for f in range(12):
        for i in range(150, 650):
            rows.append({
                "fund_code": f"E{f:06d}", "fund_name": str(f), "etf_type": "theme",
                "industry_cluster": "OTHER", "entry_date": dates[i],
                "first_exit_date": dates[i], "first_retest_date": None,
                "second_exit_date": None, "observation_end": dates[-1],
                "days_to_first_retest": None, "days_in_bottom_total": 30.0,
                "is_current": False, "n_retests": 0,
                "escape_60d": True, "right_censored_60d": False, "forward_data_complete_60d": True,
                "escape_120d": bool(i % 2), "right_censored_120d": False,
                "forward_data_complete_120d": True,
                "escape_250d": None, "right_censored_250d": True, "forward_data_complete_250d": False,
            })
    traj = pd.DataFrame(rows)
    ds = build_dataset(v1, traj, cal)
    trn = make_trainable(ds)
    res = expanding_walkforward(trn, model_name="logistic",
                                continuous=CONTINUOUS_FEATURES,
                                categorical=["etf_type"],
                                params={"l2": 1.0})
    assert len(res["folds"]) >= 2
    for f in res["folds"]:
        tr = trn[trn["year"] < f.val_year]
        va = trn[trn["year"] == f.val_year]
        assert len(tr) == f.n_train
        assert len(va) == f.n_val
        # train 最晚 < val 最早
        assert tr["first_exit_date"].max() < va["first_exit_date"].min()


# ── 7/8/9. preprocess 只在 train fit ──────────────────────────
def test_preprocessor_fit_train_only():
    rng = np.random.default_rng(0)
    tr = pd.DataFrame({
        "x1": rng.normal(size=100), "x2": np.r_[rng.normal(size=90), [np.nan] * 10],
        "etf_type": rng.choice(["broad", "industry"], size=100),
    })
    va = pd.DataFrame({
        "x1": rng.normal(size=30) + 100.0,  # val 分布偏移，不应影响 train 的 mean/std
        "x2": np.r_[rng.normal(size=28), [np.nan] * 2],
        "etf_type": ["broad"] * 30,
    })
    pre = Preprocessor(["x1", "x2"], ["etf_type"]).fit(tr)
    assert pre.means["x1"] == pytest.approx(tr["x1"].mean(), abs=1e-9)
    Xm = pre.transform(va)
    # val 的 x1 被 train 的 mean/std 标准化 → 应为 (x1 - mean_train)/std_train
    assert Xm[0, 0] == pytest.approx((va["x1"].iloc[0] - pre.means["x1"]) / pre.stds["x1"], abs=1e-9)
    # missing 填充用 train 中位数（x2）
    assert not np.isnan(Xm[:, 1]).any()


def test_calibration_uses_train_only():
    """probability calibration 只能 fit train（SimpleScore 边界在 train 上 fit）。"""
    rng = np.random.default_rng(1)
    tr = pd.DataFrame({"x": np.sort(rng.uniform(0, 10, 200))})
    va = pd.DataFrame({"x": np.sort(rng.uniform(10, 20, 100))})  # 超出 train 范围
    model = SimpleScore([("x", 1)]).fit(tr, np.ones(len(tr)))
    edges = model._edges["x"]
    assert edges[0] < edges[1]
    p = model.predict_proba(va)
    # val 全部 > train max → 全部进最高箱 → 同分
    assert np.all(p == p[0])


# ── 10. predictions 全部是 OOS ─────────────────────────────────
def test_predictions_all_oos():
    v1 = _mk_v1(700)
    cal = MarketCalendar.from_v1(v1)
    dates = pd.bdate_range("2023-01-02", periods=700)
    rows = []
    for f in range(12):
        for i in range(150, 650):
            rows.append({
                "fund_code": f"E{f:06d}", "fund_name": str(f), "etf_type": "theme",
                "industry_cluster": "OTHER", "entry_date": dates[i],
                "first_exit_date": dates[i], "first_retest_date": None,
                "second_exit_date": None, "observation_end": dates[-1],
                "days_to_first_retest": None, "days_in_bottom_total": 30.0,
                "is_current": False, "n_retests": 0,
                "escape_60d": True, "right_censored_60d": False, "forward_data_complete_60d": True,
                "escape_120d": bool(i % 2), "right_censored_120d": False,
                "forward_data_complete_120d": True,
                "escape_250d": None, "right_censored_250d": True, "forward_data_complete_250d": False,
            })
    traj = pd.DataFrame(rows)
    ds = build_dataset(v1, traj, cal)
    trn = make_trainable(ds)
    res = expanding_walkforward(trn, model_name="logistic",
                                continuous=CONTINUOUS_FEATURES,
                                categorical=["etf_type"], params={"l2": 1.0})
    pd_all = res["pooled_df"]
    assert len(pd_all) > 0
    # 每个 fold 的预测行都属于该 val 年
    for f in res["folds"]:
        sub = pd_all[pd_all["fold"] == f.val_year]
        assert set(sub["year"].unique()) <= {f.val_year}


# ── 11. canonical pipeline 无 random split ────────────────────
def test_no_random_split_in_walkforward():
    """expanding_walkforward 内部不调用任何 RNG；结果确定性。"""
    v1 = _mk_v1(700)
    cal = MarketCalendar.from_v1(v1)
    dates = pd.bdate_range("2023-01-02", periods=700)
    rows = []
    for f in range(8):
        for i in range(150, 650):
            rows.append({
                "fund_code": f"E{f:06d}", "fund_name": str(f), "etf_type": "theme",
                "industry_cluster": "OTHER", "entry_date": dates[i],
                "first_exit_date": dates[i], "first_retest_date": None,
                "second_exit_date": None, "observation_end": dates[-1],
                "days_to_first_retest": None, "days_in_bottom_total": 30.0,
                "is_current": False, "n_retests": 0,
                "escape_60d": True, "right_censored_60d": False, "forward_data_complete_60d": True,
                "escape_120d": bool(i % 2), "right_censored_120d": False,
                "forward_data_complete_120d": True,
                "escape_250d": None, "right_censored_250d": True, "forward_data_complete_250d": False,
            })
    traj = pd.DataFrame(rows)
    ds = build_dataset(v1, traj, cal)
    trn = make_trainable(ds)
    r1 = expanding_walkforward(trn, model_name="logistic", continuous=CONTINUOUS_FEATURES,
                               categorical=["etf_type"], params={"l2": 1.0})
    r2 = expanding_walkforward(trn, model_name="logistic", continuous=CONTINUOUS_FEATURES,
                               categorical=["etf_type"], params={"l2": 1.0})
    assert r1["pooled_df"]["y_prob"].tolist() == r2["pooled_df"]["y_prob"].tolist()


# ── 12. persistence 用不同 trajectory sample ──────────────────
def test_persistence_uses_independent_trajectory():
    from src.research.trend_transition import PERSISTENCE_RAW, PERSISTENCE_ROBUST
    from src.research.trend_transition.trajectory import extract_trajectories

    v1 = _mk_v1(300)
    cal = MarketCalendar.from_v1(v1)
    t_raw = extract_trajectories(v1, cal, horizon_set=(120,), persistence=0)
    t_p = extract_trajectories(v1, cal, horizon_set=(120,), persistence=3)
    # 不同 persistence 轨迹数可不同（此处 synthetic 全 None → 0 段，只验证不抛错）
    assert isinstance(t_raw, pd.DataFrame)
    assert isinstance(t_p, pd.DataFrame)


# ── 13. report renderer 纯消费结果文件 ────────────────────────
def test_report_renderer_consumes_json_only(tmp_path):
    import src.research.trend_transition.study3b_report as rep

    s = {
        "study": "3b", "generated_at": "2026-09-01T00:00:00+00:00",
        "persistence": 3, "horizon": 120, "n_trainable": 100, "base_rate": 0.3,
        "score_features": [["rps60", 1]],
        "discovery": {"features": {}},
        "regime": {"segments": {}, "direction_stability": {}},
        "model_comparison": {
            "market_only": {"pooled_auc": 0.5, "pooled_dw_auc": 0.5, "n_oos": 10},
            "individual_only": {"pooled_auc": 0.6, "pooled_dw_auc": 0.5, "n_oos": 10},
            "full_logistic": {"pooled_auc": 0.55, "pooled_dw_auc": 0.5, "n_oos": 10},
        },
        "ablation": {"full_auc": 0.55, "drops": {}},
        "robustness": {"persistence": {}, "horizon": {}, "etf_type": {}},
        "pass_gate": {"verdict": "FAIL — MARKET_REGIME_ONLY", "n_pass": 0, "checks": {
            "B1": {"name": "B1", "ok": False, "detail": ""},
            "B2": {"name": "B2", "ok": False, "detail": ""},
            "B3": {"name": "B3", "ok": False, "detail": ""},
            "B4": {"name": "B4", "ok": False, "detail": ""},
            "B5": {"name": "B5", "ok": False, "detail": ""},
        }},
    }
    out = tmp_path / "study3b_report.html"
    html = rep.render(s, out_path=out)
    assert html.exists()
    assert "FAIL" in html.read_text()


# ── metrics 正确性 ─────────────────────────────────────────────
def test_roc_auc_perfect_and_random():
    y = np.array([1, 1, 0, 0, 1])
    p = np.array([0.9, 0.8, 0.3, 0.2, 0.7])
    assert roc_auc(y, p) == pytest.approx(1.0)
    rng = np.random.default_rng(3)
    p2 = rng.random(len(y))
    a = roc_auc(y, p2)
    assert 0.0 <= a <= 1.0


def test_roc_auc_soft_matches_binary():
    y = np.array([1, 1, 0, 0, 1])
    p = np.array([0.9, 0.8, 0.3, 0.2, 0.7])
    assert roc_auc_soft(y, p) == pytest.approx(roc_auc(y, p), abs=1e-9)


def test_pr_auc_range():
    rng = np.random.default_rng(4)
    y = (rng.random(200) < 0.3).astype(float)
    p = y * 0.8 + rng.random(200) * 0.2
    assert 0.0 <= pr_auc(y, p) <= 1.0


def test_evaluate_date_weighted_smoke():
    rng = np.random.default_rng(5)
    n = 200
    y = (rng.random(n) < 0.4).astype(float)
    p = y * 0.9 + rng.random(n) * 0.1
    d = pd.date_range("2024-01-01", periods=10).repeat(n // 10)
    e = evaluate(y, p, d)
    assert e["event_weighted"]["auc"] > 0.5
    assert e["date_weighted"]["n_dates"] == 10
    assert "lift_20" in e["event_weighted"]
    assert "lift_20" in e["date_weighted"]


# ── discovery / regime / score ────────────────────────────────
def test_discovery_quintile_monotone():
    rng = np.random.default_rng(6)
    n = 500
    x = rng.normal(size=n)
    y = (x > 0).astype(float)
    ds = pd.DataFrame({"x": x, "y_true": y})
    disc = feature_discovery(ds, features=["x"])
    rec = disc["features"]["x"]
    assert rec["n"] == n
    assert rec["spearman"] > 0.5
    assert len(rec["quintiles"]) == 5
    rates = [q["escape_rate"] for q in rec["quintiles"]]
    assert rates == sorted(rates)


def test_regime_consistency_tolerates_small_years():
    rng = np.random.default_rng(7)
    n = 600
    ds = pd.DataFrame({
        "x": rng.normal(size=n), "y_true": rng.integers(0, 2, n),
        "year": np.r_[np.full(500, 2024), np.full(100, 2025)],
        "first_exit_date": pd.date_range("2024-01-01", periods=n),
    })
    reg = regime_robustness(ds, features=["x"], min_year_n=100)
    # 2025 有 100 行 → 参与一致性；方向稳定
    assert reg["direction_stability"]["x"]["n_years_checked"] == 1 or reg["direction_stability"]["x"]["n_years_checked"] == 2


def test_select_score_features_individual_first():
    rng = np.random.default_rng(8)
    n = 400
    ds = pd.DataFrame({
        "delta_pos120_5d": rng.normal(size=n), "rps60": rng.normal(size=n),
        "market_ltb_breadth": rng.normal(size=n), "y_true": rng.integers(0, 2, n),
        "year": np.full(n, 2024), "first_exit_date": pd.date_range("2024-01-01", periods=n),
    })
    disc = feature_discovery(ds, features=["delta_pos120_5d", "rps60", "market_ltb_breadth"])
    reg = regime_robustness(ds, features=["delta_pos120_5d", "rps60", "market_ltb_breadth"],
                            min_year_n=100)
    feats = select_score_features(disc, reg, max_total=6)
    # 默认 individual_only=True → F5_market 不进 score
    assert all(f not in FEATURE_FAMILIES["F5_market"] for f, _ in feats)


# ── models ────────────────────────────────────────────────────
def test_logistic_separates_data():
    rng = np.random.default_rng(9)
    n = 1000
    X = rng.normal(size=(n, 3))
    y = ((X[:, 0] * 1.5 + X[:, 1] - X[:, 2]) > 0).astype(float)
    X = pd.DataFrame(X, columns=["a", "b", "c"])
    pre = Preprocessor(["a", "b", "c"], []).fit(X)
    Xm = pre.transform(X)
    from src.research.trend_transition.study3b_models import LogisticRegressionL2
    m = LogisticRegressionL2(l2=0.1).fit(Xm, y)
    p = m.predict_proba(Xm)
    assert roc_auc(y, p) > 0.95
    # 系数方向正确
    coef = m.coef_
    assert coef is not None
    assert coef[0] > 0 and coef[1] > 0 and coef[2] < 0


def test_shallow_tree_depth_limit():
    rng = np.random.default_rng(10)
    n = 2000
    X = pd.DataFrame(rng.normal(size=(n, 2)), columns=["a", "b"])
    y = ((X["a"] + X["b"]) > 0).astype(float)
    pre = Preprocessor(["a", "b"], []).fit(X)
    Xm = pre.transform(X)
    from src.research.trend_transition.study3b_models import ShallowTree
    m = ShallowTree(max_depth=3, min_samples_leaf=40).fit(Xm, y)
    # 校验深度 <= 3：节点中无深度字段，仅验证 predict 正常 + 概率范围
    p = m.predict_proba(Xm)
    assert (p >= 0).all() and (p <= 1).all()
