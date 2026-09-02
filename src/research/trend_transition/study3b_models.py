"""Study 3B · 三类模型（手写，无 sklearn）。

M0 · LogisticRegressionL2  主模型（标准化连续变量 + L2 岭，IRLS/牛顿迭代）。
M1 · ShallowTree          max_depth<=3 的 shallow CART（Gini），仅作非线性稳健性。
M2 · SimpleScore          根据稳定特征构造 0/1/2 打分规则（最终冻结候选）。

口径（用户锁定，§13）：
  - 连续变量标准化（mean/std）只 fit train；missing 只 fit train（中位填充）。
  - categorical（etf_type / exit_bottom_state）one-hot 编码，类别以 train 为准。
  - 所有模型提供 fit / predict_proba 接口，train/val 分离由 validation 层保证。
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

# ── 预处理：Standardizer（fit train only）─────────────────────
# 缺失值用 train 中位数填充；连续列标准化；categorical one-hot。

CATEGORICAL_VALUES: dict[str, list[str]] = {
    "etf_type": ["broad", "industry", "theme", "dividend", "cross_border"],
    "exit_bottom_state": ["NORMAL", "RECENT_BOTTOM", "RECOVERING_FROM_BOTTOM", "DEEP_BOTTOM", "UNRELIABLE"],
}


class Preprocessor:
    """标准化的 fit/transform：统计量只从 train 学，transform 不重新 fit。"""

    def __init__(self, continuous: list[str], categorical: list[str]):
        self.continuous = continuous
        self.categorical = categorical
        self.medians: dict[str, float] = {}
        self.means: dict[str, float] = {}
        self.stds: dict[str, float] = {}
        self.categories: dict[str, list[str]] = {}
        self.n_features: int = 0
        self.feature_names: list[str] = []

    def fit(self, X: pd.DataFrame) -> "Preprocessor":
        for c in self.continuous:
            vals = X[c].to_numpy(float)
            vals = vals[np.isfinite(vals)]
            med = float(np.median(vals)) if len(vals) else 0.0
            mean = float(np.nanmean(vals)) if len(vals) else 0.0
            std = float(np.nanstd(vals)) if len(vals) else 0.0
            if not np.isfinite(med):
                med = 0.0
            if not np.isfinite(mean):
                mean = 0.0
            if not np.isfinite(std) or std == 0:
                std = 1.0
            self.medians[c] = med
            self.means[c] = mean
            self.stds[c] = std
        for c in self.categorical:
            seen = X[c].dropna().unique().tolist()
            cats = [v for v in CATEGORICAL_VALUES.get(c, []) if v in seen]
            if not cats:
                cats = sorted(seen)
            self.categories[c] = cats
        self.feature_names = self._names()
        self.n_features = len(self.feature_names)
        return self

    def _names(self) -> list[str]:
        names = list(self.continuous)
        for c in self.categorical:
            names += [f"{c}__{v}" for v in self.categories.get(c, [])]
        return names

    def transform(self, X: pd.DataFrame) -> np.ndarray:
        cols: list[np.ndarray] = []
        for c in self.continuous:
            arr = X[c].to_numpy(float).astype(float)
            arr = np.where(np.isfinite(arr), arr, self.medians.get(c, 0.0))
            arr = (arr - self.means.get(c, 0.0)) / self.stds.get(c, 1.0)
            cols.append(arr)
        for c in self.categorical:
            for v in self.categories.get(c, []):
                cols.append((X[c] == v).to_numpy().astype(float))
        if not cols:
            raise ValueError("no features")
        return np.column_stack(cols)

    def fit_transform(self, X: pd.DataFrame) -> np.ndarray:
        self.fit(X)
        return self.transform(X)


# ── M0 · Logistic Regression（L2，IRLS）────────────────────────
class LogisticRegressionL2:
    """二分类 logistic + L2 岭；IRLS 求解（Newton）。"""

    def __init__(self, l2: float = 1.0, max_iter: int = 200, tol: float = 1e-8):
        self.l2 = l2
        self.max_iter = max_iter
        self.tol = tol
        self.coef_: np.ndarray | None = None
        self.intercept_: float = 0.0

    def fit(self, X: np.ndarray, y: np.ndarray) -> "LogisticRegressionL2":
        X = np.asarray(X, dtype=float)
        y = np.asarray(y, dtype=float)
        n, p = X.shape
        # 加截距列
        Xb = np.column_stack([np.ones(n), X])
        w = np.zeros(p + 1)
        lam = np.ones(p + 1) * self.l2
        lam[0] = 0.0  # 截距不惩罚
        for _ in range(self.max_iter):
            z = Xb @ w
            mu = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
            wgt = mu * (1.0 - mu)
            W = Xb * wgt[:, None]
            grad = Xb.T @ (mu - y) + lam * w
            # Hessian + ridge
            H = Xb.T @ (Xb * wgt[:, None]) + np.diag(lam)
            try:
                step = np.linalg.solve(H, grad)
            except np.linalg.LinAlgError:
                step = np.linalg.lstsq(H, grad, rcond=None)[0]
            w_new = w - step
            if np.max(np.abs(step)) < self.tol:
                w = w_new
                break
            w = w_new
        self.intercept_ = float(w[0])
        self.coef_ = w[1:]
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if self.coef_ is None:
            raise RuntimeError("fit first")
        X = np.asarray(X, dtype=float)
        z = X @ self.coef_ + self.intercept_
        return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


# ── M1 · Shallow Tree（Gini CART，max_depth<=3）────────────────
class ShallowTree:
    """浅层 CART（max_depth 受限、min_samples_leaf 较高），只做非线性稳健性。"""

    def __init__(self, max_depth: int = 3, min_samples_leaf: int = 40):
        self.max_depth = max_depth
        self.min_samples_leaf = min_samples_leaf
        self._tree: dict[str, Any] | None = None

    @staticmethod
    def _gini(y: np.ndarray) -> float:
        if len(y) == 0:
            return 0.0
        p1 = float(y.mean())
        return 2.0 * p1 * (1.0 - p1)

    def _split(self, X: np.ndarray, y: np.ndarray, depth: int) -> dict[str, Any]:
        node: dict[str, Any] = {"count": int(len(y)), "prob": float(y.mean())}
        if depth >= self.max_depth or len(y) < 2 * self.min_samples_leaf:
            return node
        best: tuple[float, int, float] | None = None
        for j in range(X.shape[1]):
            col = X[:, j]
            uniq = np.unique(col)
            if uniq.size < 2:
                continue
            # 分位候选阈值
            cands = np.percentile(uniq, [25, 50, 75])
            for thr in cands:
                if thr == uniq[0] or thr == uniq[-1]:
                    continue
                left = col <= thr
                n_l, n_r = int(left.sum()), int(len(left) - left.sum())
                if n_l < self.min_samples_leaf or n_r < self.min_samples_leaf:
                    continue
                imp = (n_l * self._gini(y[left]) + n_r * self._gini(y[~left])) / len(y)
                gain = self._gini(y) - imp
                if best is None or gain > best[0]:
                    best = (gain, j, thr)
        if best is None or best[0] <= 0:
            return node
        _, j, thr = best
        left = X[:, j] <= thr
        node["feature"] = int(j)
        node["threshold"] = float(thr)
        node["left"] = self._split(X[left], y[left], depth + 1)
        node["right"] = self._split(X[~left], y[~left], depth + 1)
        return node

    def fit(self, X: np.ndarray, y: np.ndarray) -> "ShallowTree":
        self._tree = self._split(np.asarray(X, dtype=float), np.asarray(y, dtype=float), 0)
        return self

    def _proba_row(self, node: dict[str, Any], x: np.ndarray) -> float:
        while "feature" in node:
            if x[node["feature"]] <= node["threshold"]:
                node = node["left"]
            else:
                node = node["right"]
        return float(node["prob"])

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if self._tree is None:
            raise RuntimeError("fit first")
        X = np.asarray(X, dtype=float)
        return np.array([self._proba_row(self._tree, x) for x in X])


# ── M2 · Simple Score（0/1/2 规则打分）─────────────────────────
class SimpleScore:
    """根据一组「方向已知的稳定特征」构造打分。

    每特征在 train 上按分位三等分 → 0/1/2（方向由 direction 决定，direction=+1
    表示特征高 → escape 倾向高）；分数归一化到 [0,1] 作为伪概率。
    最终冻结 V1 优先冻结这类低参数量规则，而不是 logistic 系数。
    """

    def __init__(self, features: list[tuple[str, int]], n_bins: int = 3):
        self.features = features          # [(feature_name, direction)]  direction∈{+1,-1}
        self.n_bins = n_bins
        self._edges: dict[str, list[float]] = {}
        self._scale = float(n_bins * len(features))

    def fit(self, X: pd.DataFrame, y: pd.Series | np.ndarray | None = None) -> "SimpleScore":
        for feat, _dir in self.features:
            vals = X[feat].to_numpy(float)
            vals = vals[np.isfinite(vals)]
            if len(vals) < self.n_bins:
                self._edges[feat] = [float(np.nan)] * (self.n_bins - 1)
                continue
            edges = list(np.percentile(vals, [100 * (i + 1) / self.n_bins for i in range(self.n_bins - 1)]))
            self._edges[feat] = [float(e) for e in edges]
        return self

    def _score_one(self, X: pd.DataFrame) -> np.ndarray:
        total = np.zeros(len(X), dtype=float)
        for feat, direction in self.features:
            edges = self._edges.get(feat)
            if not edges or not np.isfinite(edges).all():
                continue
            vals = X[feat].to_numpy(float)
            # 分箱 → 0..n_bins-1
            b = np.searchsorted(edges, vals, side="right")
            b = np.clip(b, 0, self.n_bins - 1).astype(float)
            if direction < 0:
                b = self.n_bins - 1 - b
            total += b
        return total / self._scale

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        return self._score_one(X)


def build_preprocessor(continuous: list[str], categorical: list[str]) -> Preprocessor:
    return Preprocessor(continuous, categorical)


def train_model(name: str, Xtr: pd.DataFrame, ytr: np.ndarray,
                pre: Preprocessor, params: dict[str, Any] | None = None) -> tuple[Any, np.ndarray]:
    """统一训练入口：返回 (模型, train_proba)。params 覆盖默认超参。"""
    Xm = pre.fit_transform(Xtr)
    params = params or {}
    if name == "logistic":
        model = LogisticRegressionL2(**params)
        model.fit(Xm, ytr)
        proba = model.predict_proba(Xm)
    elif name == "tree":
        model = ShallowTree(**params)
        model.fit(Xm, ytr)
        proba = model.predict_proba(Xm)
    elif name == "score":
        # SimpleScore 需要原始 DataFrame 特征（不打标准矩阵）；传列子集
        feats: list[str] = [f for f, _d in params.get("features", [])]
        model = SimpleScore(params.get("features", []))
        model.fit(Xtr, ytr)
        proba = model.predict_proba(Xtr[feats].copy())
    else:
        raise ValueError(name)
    return model, proba
