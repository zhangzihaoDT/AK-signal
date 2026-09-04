"""Strategy Specification — Schema 校验。

生产路径不允许依赖隐藏默认值：影响信号/交易/回测结果的参数必须在配置中显式存在，
缺失或非法 → 运行开始阶段即抛错。
"""

from __future__ import annotations

from typing import Any

CONFIRMATION_POLICIES = {"trend_confirmation"}
EXIT_POLICIES = {"signal_exit", "ma_exit", "fixed_horizon"}
UNIVERSE_MODES = {"configured", "theme-matched"}


class SpecValidationError(ValueError):
    pass


def _require(cfg: dict[str, Any], path: str, msg: str) -> Any:
    node: Any = cfg
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            raise SpecValidationError(f"{path}: {msg}")
        node = node[key]
    return node


def _num_in_range(v: Any, lo: float, hi: float, path: str) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise SpecValidationError(f"{path}: must be numeric, got {v!r}")
    if not (lo <= f <= hi):
        raise SpecValidationError(f"{path}: must be in [{lo}, {hi}], got {f}")
    return f


def validate_strategy(cfg: dict[str, Any], strategy_id: str) -> None:
    entry = _require(cfg, "entry", "must define entry policy")
    exit_ = _require(cfg, "exit", "must define exit policy")
    _num_in_range(entry.get("rps15_min"), 0, 100, f"{strategy_id}.entry.rps15_min")
    _num_in_range(entry.get("trend_score_min"), 0, 100, f"{strategy_id}.entry.trend_score_min")
    if entry.get("policy", "") not in CONFIRMATION_POLICIES:
        raise SpecValidationError(
            f"{strategy_id}.entry.policy unsupported: {entry.get('policy')!r}")
    states = entry.get("allowed_trend_states")
    if not isinstance(states, list) or not states:
        raise SpecValidationError(f"{strategy_id}.entry.allowed_trend_states required")

    policy = exit_.get("policy", "")
    if policy not in EXIT_POLICIES:
        raise SpecValidationError(f"{strategy_id}.exit.policy unsupported: {policy!r}")
    if policy == "fixed_horizon" and exit_.get("horizon") is None:
        raise SpecValidationError(f"{strategy_id}.exit: fixed_horizon requires horizon")
    if policy == "ma_exit" and exit_.get("ma_window") is None:
        raise SpecValidationError(f"{strategy_id}.exit: ma_exit requires ma_window")


def validate_strategies(raw: dict[str, Any], themes: set[str]) -> None:
    """整体校验：strategy_id 唯一、theme 存在、universe_mode 合法、配置层不串。"""
    seen: dict[str, str] = {}
    strategies = raw.get("strategies", {})
    if not strategies:
        raise SpecValidationError("strategies: empty")
    for key, cfg in strategies.items():
        sid = cfg.get("strategy_id", "")
        if not sid:
            raise SpecValidationError(f"strategies.{key}: missing strategy_id")
        if sid in seen:
            raise SpecValidationError(f"duplicate strategy_id {sid!r} ({seen[sid]} & {key})")
        seen[sid] = key
        theme = cfg.get("theme", "")
        if theme not in themes:
            raise SpecValidationError(f"{sid}: theme {theme!r} not in themes")
        if cfg.get("universe_mode", "") not in UNIVERSE_MODES:
            raise SpecValidationError(f"{sid}: universe_mode {cfg.get('universe_mode')!r} invalid")
        validate_strategy(cfg, sid)


def validate_indicators(cfg: dict[str, Any]) -> None:
    rps = _require(cfg, "rps", "must define rps windows")
    for w in ("short_window", "medium_window", "long_window", "today_window", "velocity_window"):
        if int(rps.get(w, 0)) <= 0:
            raise SpecValidationError(f"indicators.rps.{w}: must be > 0")
    dq = cfg.get("data_quality")
    if not isinstance(dq, dict):
        raise SpecValidationError("indicators.data_quality: required (max_single_day_return / flag_window)")
    _num_in_range(dq.get("max_single_day_return"), 0, 100, "indicators.data_quality.max_single_day_return")
    if int(dq.get("flag_window", 0)) <= 0:
        raise SpecValidationError("indicators.data_quality.flag_window: must be > 0")
    gates = _require(cfg, "signal_gates", "must define signal_gates")
    _num_in_range(gates["etf"].get("strong_threshold"), 0, 100, "signal_gates.etf.strong_threshold")
    _num_in_range(gates["etf"].get("watch_threshold"), 0, 100, "signal_gates.etf.watch_threshold")
    conf = _require(cfg, "confirmation", "must define confirmation thresholds")
    _num_in_range(conf.get("strong_threshold"), 0, 100, "confirmation.strong_threshold")
    _num_in_range(conf.get("observe_threshold"), 0, 100, "confirmation.observe_threshold")
    _num_in_range(conf.get("broad_fraction"), 0, 1, "confirmation.broad_fraction")
    _num_in_range(conf.get("watch_proximity"), 0, 100, "confirmation.watch_proximity")
    tc = _require(cfg, "tier_confirmation", "must define tier_confirmation thresholds")
    _num_in_range(tc.get("tier_gate_strong"), 0, 100, "tier_confirmation.tier_gate_strong")
    _num_in_range(tc.get("tier_gate_observe"), 0, 100, "tier_confirmation.tier_gate_observe")
    _num_in_range(tc.get("broad_fraction"), 0, 1, "tier_confirmation.broad_fraction")
    _num_in_range(tc.get("strong_trend_min"), 0, 100, "tier_confirmation.strong_trend_min")


SIGNALS = {"STRONG_BUY", "BUY", "WATCH", "HOLD", "WAIT"}
TREND_LEVELS = {"QUALIFIED", "NOT_QUALIFIED"}
LEADERSHIP_LEVELS = {"LEADER", "CORE", "NON_CORE"}
POSITION_LEVELS = {"LOW", "MID", "HIGH", "BREAKDOWN", "UNKNOWN"}
LEADERSHIP_METHODS = {"theme_rank"}


def _validate_leadership(node: dict[str, Any], prefix: str) -> None:
    if not isinstance(node, dict):
        raise SpecValidationError(f"{prefix}: must define leadership")
    if node.get("method", "theme_rank") not in LEADERSHIP_METHODS:
        raise SpecValidationError(f"{prefix}.method unsupported: {node.get('method')!r}")
    if "leader_rank_max" in node:
        # 个股风格：leader_rank_max（LEADER 上界）+ core_rank_max（CORE 上界）
        leader = int(node.get("leader_rank_max", 1))
        core = int(node.get("core_rank_max", leader))
    else:
        # ETF 风格：core_rank_max（LEADER 上界）+ satellite_rank_max（CORE 上界）
        leader = int(node.get("core_rank_max", 1))
        core = int(node.get("satellite_rank_max", leader))
    if leader < 1:
        raise SpecValidationError(f"{prefix}: LEADER rank upper bound must be >= 1")
    if core < leader:
        raise SpecValidationError(f"{prefix}: CORE rank upper bound must be >= LEADER bound")


POSITION_METRICS = {"price_percentile", "ma60_deviation"}


def _validate_historical_position(node: dict[str, Any], prefix: str) -> None:
    if not isinstance(node, dict):
        raise SpecValidationError(f"{prefix}: must define historical_position")
    metric = node.get("metric", "price_percentile")
    if metric not in POSITION_METRICS:
        raise SpecValidationError(f"{prefix}.metric unsupported: {metric!r}")
    if metric == "price_percentile":
        if int(node.get("lookback_days", 0)) < 20:
            raise SpecValidationError(f"{prefix}.lookback_days must be >= 20")
        low = _num_in_range(node.get("low_max"), 0, 100, f"{prefix}.low_max")
        mid = _num_in_range(node.get("mid_max"), 0, 100, f"{prefix}.mid_max")
        if mid <= low:
            raise SpecValidationError(f"{prefix}.mid_max must be > low_max")
    else:  # ma60_deviation
        if int(node.get("ma_window", 0)) < 20:
            raise SpecValidationError(f"{prefix}.ma_window must be >= 20")
        bd = _num_in_range(node.get("breakdown_pct"), -100, 100, f"{prefix}.breakdown_pct")
        low = _num_in_range(node.get("low_below_pct"), -100, 100, f"{prefix}.low_below_pct")
        high = _num_in_range(node.get("high_above_pct"), -100, 100, f"{prefix}.high_above_pct")
        if not (bd < low < high):
            raise SpecValidationError(
                f"{prefix}: must satisfy breakdown_pct < low_below_pct < high_above_pct")


def _validate_signal_policy(node: dict[str, Any], prefix: str) -> None:
    if not isinstance(node, dict):
        raise SpecValidationError(f"{prefix}: must define signal_policy")
    rules = node.get("rules")
    if not isinstance(rules, list) or not rules:
        raise SpecValidationError(f"{prefix}.rules: non-empty rules list required")
    for i, r in enumerate(rules):
        if not isinstance(r, dict) or "signal" not in r:
            raise SpecValidationError(f"{prefix}.rules[{i}]: must define signal")
        if r["signal"] not in SIGNALS:
            raise SpecValidationError(f"{prefix}.rules[{i}].signal unsupported: {r.get('signal')!r}")
        if r.get("trend") is not None and r["trend"] not in TREND_LEVELS:
            raise SpecValidationError(f"{prefix}.rules[{i}].trend unsupported: {r.get('trend')!r}")
        if r.get("leadership") is not None and r["leadership"] not in LEADERSHIP_LEVELS:
            raise SpecValidationError(f"{prefix}.rules[{i}].leadership unsupported: {r.get('leadership')!r}")
        if r.get("position") is not None and r["position"] not in POSITION_LEVELS:
            raise SpecValidationError(f"{prefix}.rules[{i}].position unsupported: {r.get('position')!r}")
    fb = node.get("fallback_signal", "WAIT")
    if fb not in SIGNALS:
        raise SpecValidationError(f"{prefix}.fallback_signal unsupported: {fb!r}")


def validate_etf_selection(cfg: dict[str, Any]) -> None:
    """Layer③ ETF 候选策略校验（③A 门槛 / ③B vehicle 适配度 / ③C 四段）。"""
    es = _require(cfg, "etf_selection", "must define etf_selection policy")
    trend = _require(es, "trend", "etf_selection.trend required")
    states = trend.get("allowed_trend_states")
    if not isinstance(states, list) or not states:
        raise SpecValidationError("etf_selection.trend.allowed_trend_states required")
    _num_in_range(trend.get("min_amount"), 0, 1e15, "etf_selection.trend.min_amount")
    # ③B vehicle：weights（amount）+ amount_score log 口径
    veh = _require(es, "vehicle", "etf_selection.vehicle required (v0.12.0 Selection V2)")
    vw = _require(veh, "weights", "etf_selection.vehicle.weights required")
    for key in vw:
        if key not in ("amount", "fixed_pool"):
            raise SpecValidationError(f"etf_selection.vehicle.weights unsupported key: {key!r}")
    _num_in_range(vw.get("amount", 0), 0, 1e6, "etf_selection.vehicle.weights.amount")
    amt = _require(veh, "amount_score", "etf_selection.vehicle.amount_score required")
    if amt.get("method", "") != "log_threshold":
        raise SpecValidationError(f"etf_selection.vehicle.amount_score.method unsupported: {amt.get('method')!r}")
    _num_in_range(amt.get("floor"), 0, 1e15, "etf_selection.vehicle.amount_score.floor")
    _num_in_range(amt.get("reference"), 0, 1e15, "etf_selection.vehicle.amount_score.reference")
    _num_in_range(amt.get("cap"), 0, 1e6, "etf_selection.vehicle.amount_score.cap")
    _validate_leadership(es.get("leadership") or {}, "etf_selection.leadership")
    _validate_historical_position(es.get("historical_position") or {}, "etf_selection.historical_position")


def validate_stock_selection(cfg: dict[str, Any]) -> None:
    """Layer③ 个股准入 + 主题门控 + 四段信号校验。"""
    ss = _require(cfg, "stock_selection", "must define stock_selection policy")
    trend = _require(ss, "trend", "stock_selection.trend required")
    _num_in_range(trend.get("qualified_score"), 0, 100, "stock_selection.trend.qualified_score")
    _num_in_range(trend.get("rps15_min", 80), 0, 100, "stock_selection.trend.rps15_min")
    states = trend.get("allowed_trend_states")
    if not isinstance(states, list) or not states:
        raise SpecValidationError("stock_selection.trend.allowed_trend_states required")
    confirm = ss.get("theme_confirm_states")
    if not isinstance(confirm, list) or not confirm:
        raise SpecValidationError("stock_selection.theme_confirm_states required")
    _validate_leadership(ss.get("leadership") or {}, "stock_selection.leadership")
    _validate_historical_position(ss.get("historical_position") or {}, "stock_selection.historical_position")
    _validate_signal_policy(ss.get("signal_policy") or {}, "stock_selection.signal_policy")


def validate_execution(cfg: dict[str, Any]) -> None:
    ex = _require(cfg, "execution", "must define execution")
    if ex.get("model", "") != "next_open":
        raise SpecValidationError(f"execution.model unsupported: {ex.get('model')!r}")
    _num_in_range(ex.get("fee_bps"), 0, 10000, "execution.fee_bps")
    _num_in_range(ex.get("slippage_bps"), 0, 10000, "execution.slippage_bps")


def validate_portfolio(cfg: dict[str, Any]) -> None:
    p = _require(cfg, "portfolio", "must define portfolio")
    if int(p.get("max_positions", 0)) <= 0:
        raise SpecValidationError("portfolio.max_positions: must be > 0")
    _num_in_range(p.get("max_weight_per_asset"), 0, 1, "portfolio.max_weight_per_asset")
    _num_in_range(p.get("deploy_ratio"), 0, 1, "portfolio.deploy_ratio")
