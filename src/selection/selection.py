"""
Layer ③ — 多主题交易标的筛选与表达方式选择（Tradable Selection, v0.4.3）

定位：执行对象压缩层，不是又一层强弱排名。
职责：把 Layer ①（ETF 轮动）与 Layer ②（多主题行业确认）的结论，压缩成
      「这个已确认主题，应当由哪只 ETF、哪类股票来交易」。

多主题框架：bucket（Core/Quality/Tactical，为什么持有） → theme（市场方向）→ 候选资产。
  theme 与 industries / etf_keywords 来自 config/themes_two_directions.yaml（单一事实源）。

核心输出：候选资产对象（结构化 dict/JSON），HTML 报告只是它的可视化。

流程：
  1. 主题确认     按 theme 的 industries 聚合 Layer② confirmation，判定每个主题是否确认
  2. ETF 候选     动态从 Layer① rotation 全市场按 theme.etf_keywords 选（趋势门控 + 流动性 + 排序 + 去重）
  3. 个股候选     从 universe.yaml（bucket → theme → tier）读取固定观察池
  4. 表达决策     基于上涨结构（参与率 / HHI / Top3）选 ETF vs 个股
  5. 构建候选对象  每主题输出 core_etf / sub_industry_etf / leaders / high_beta / equipment
  6. bucket 聚合  汇总 Core / Quality / Tactical 三个组合意图

Layer 4 边界：本层只回答「买什么」，不回答「买多少 / 何时买 / 何时卖」。
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from src.common import themes as themes_cfg

logger = logging.getLogger("selection")

# 表达方式
EXPRESSION_LABELS = {
    "WATCHLIST_ONLY": "仅观察（行业未确认）",
    "ETF_PRIORITY": "优先 ETF（广泛上涨）",
    "LEADER_PRIORITY": "优先龙头个股（龙头主导）",
    "ETF_CORE_PLUS_LEADER": "ETF 核心 + 龙头卫星（扩散形成）",
}

# 个股 role 标记
ROLE_LABELS = {
    "CORE_ETF": "核心 ETF",
    "SUB_INDUSTRY_ETF": "细分行业 ETF",
    "LEADER": "行业龙头",
    "HIGH_BETA": "高弹性标的",
    "UPSTREAM": "设备与上游",
}

# 趋势门控：允许进入候选的 ETF 状态（来自统一 Strategy Specification config/indicators.yaml）
def _indicator_gates() -> tuple[set[str], set[str], float, float]:
    from src.common.spec.loaders import load_indicator_spec
    s = load_indicator_spec()
    return (set(s.etf_gate_states), set(s.etf_watch_gate_states),
            float(s.etf_min_amount), float(s.stock_qualified_score))


ETF_TREND_GATES, ETF_WATCH_GATES, ETF_MIN_AMOUNT, STOCK_QUALIFIED_SCORE = _indicator_gates()
# 观察池（弱势市场兜底）：额外纳入 WATCH，仅作观察候选，recommended=False
# 对外暴露的趋势状态标签：OUT_OF_SCOPE 语义易误读为「不属于主题」，实为「未达趋势门」
ETF_TREND_STATUS_LABELS = {
    "BUY_CANDIDATE": "BUY_CANDIDATE",
    "STRONG_WATCH": "STRONG_WATCH",
    "WATCH": "WATCH",
    "OUT_OF_SCOPE": "BELOW_TREND_GATE",
}

# 个股三层状态：WATCH（固定池内不达标）→ QUALIFIED（趋势合格，行业未确认）→ RECOMMENDED（行业确认+趋势确认）
STOCK_STATE_WATCH = "WATCH"
STOCK_STATE_QUALIFIED = "QUALIFIED"
STOCK_STATE_RECOMMENDED = "RECOMMENDED"
# 个股趋势合格门槛（来自 config/indicators.yaml signal_gates.stock.qualified_score）
# 主题确认门槛（与 Layer② OBSERVE_THRESHOLD 对齐，ETF RPS15 距此门槛的远近衡量接近转强程度）
SUBTHEME_CONFIRM_RPS15 = 80


def _confirmation_breadth_params() -> tuple[float, float]:
    from src.common.spec.loaders import load_indicator_spec
    s = load_indicator_spec()
    return s.confirmation_broad_fraction, s.confirmation_watch_proximity


CONF_BROAD_FRACTION, CONF_WATCH_PROXIMITY = _confirmation_breadth_params()

# 个股 tier → role
TIER_ROLE_MAP = {
    "leader": "LEADER",
    "high_beta": "HIGH_BETA",
    "equipment_upstream": "UPSTREAM",
}


def _clean_trend_status(v: Any) -> str:
    """趋势状态归一化：缺失/空 → UNKNOWN；OUT_OF_SCOPE → BELOW_TREND_GATE。"""
    if v is None:
        return "UNKNOWN"
    if isinstance(v, float) and pd.isna(v):
        return "UNKNOWN"
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none"):
        return "UNKNOWN"
    return ETF_TREND_STATUS_LABELS.get(s, s)
# ETF 流动性门槛（成交额，元）
ETF_MIN_AMOUNT = 50_000_000


@dataclass
class AssetCandidate:
    code: str
    name: str
    role: str                        # CORE_ETF / SUB_INDUSTRY_ETF / LEADER / HIGH_BETA / UPSTREAM
    asset_type: str                  # etf / stock
    bucket: str = ""
    theme: str = ""
    rps15: float | None = None
    rps20: float | None = None
    rps60: float | None = None
    return_5d: float | None = None
    return_20d: float | None = None
    trend_status: str = ""
    score_trend: float | None = None   # 0-100 趋势分（个股来自 trend_engine）
    rank_change_5d: float | None = None
    liquidity: float | None = None   # 成交额（元）
    tradable: bool = True
    recommended: bool = True
    state: str = STOCK_STATE_WATCH
    data_status: str = "current"          # current / stale / missing（来自个股趋势产物）
    selection_status: str = "available"   # available / unavailable（missing → unavailable）
    # 变化跟踪（固定观察池监控用）
    score_change_1d: int | None = None
    state_change: str = ""
    days_in_state: int = 1
    last_trend_qualified_date: str = ""
    # 风险门控：趋势达标 ≠ 可行动，风险警戒/剔除观察等门控单独记录
    risk_gate_passed: bool = True
    risk_flags: list[str] = field(default_factory=list)
    selection_score: float | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None}


def _round(v: Any) -> float | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    return round(float(v), 1)


# ── 1. 主题确认 ────────────────────────────────────────────────────

def classify_confirmation_breadth(
    confirmed: bool,
    n_observe: int,
    n_total: int,
    max_rps15: float | None,
    *,
    broad_fraction: float = 0.5,
    watch_proximity: float = 70.0,
) -> tuple[str, str]:
    """确认广度分类：区分「多数子行业共同走强」与「少数子行业拉动」。

    Returns:
        (state, label)
        BROAD_CONFIRMED  多数焦点行业进入观察区（≥ broad_fraction）
        NARROW_CONFIRMED 已确认但仅少数行业拉动（窄幅确认）
        WATCH            未确认但最强行业接近门槛
        UNCONFIRMED      无支撑
    """
    if confirmed:
        broad = n_total > 0 and n_observe >= max(1, int(round(n_total * broad_fraction)))
        return ("BROAD_CONFIRMED", "广泛确认") if broad else ("NARROW_CONFIRMED", "窄幅确认")
    if max_rps15 is not None and max_rps15 >= watch_proximity:
        return ("WATCH", "接近确认")
    return ("UNCONFIRMED", "无支撑")


def _confirm_evidence(sub: pd.DataFrame, confirmed: bool) -> dict[str, Any]:
    """确认依据：已确认 → 最强进入观察区行业；未确认 → 最强行业（距门槛）。"""
    obs = sub[sub["strength_level"].astype(str).isin(["观察", "强势"])] if confirmed else sub
    if obs.empty:
        obs = sub
    if obs.empty:
        return {}
    top = obs.sort_values("RPS15", ascending=False).iloc[0]
    rps = float(top["RPS15"]) if pd.notna(top.get("RPS15")) else None
    return {"industry": str(top.get("industry_name", "")),
            "industry_code": str(top.get("industry_code", "")),
            "rps15": _round(rps)}


def evaluate_themes(
    confirmation_df: pd.DataFrame,
    rotation_df: pd.DataFrame,
) -> dict[str, dict[str, Any]]:
    """按 theme 聚合 Layer② confirmation，判定每个主题是否确认。

    Returns:
        {theme_key: {label, bucket, bucket_label, confirmed, n_strong, n_observe,
                     median_rps15, strongest_industry_rps15, median_participation,
                     median_hhi, median_top3_share, etf_median_rps15, reason}}
    """
    buckets = themes_cfg.load_buckets()
    themes = {th.key: th for b in buckets for th in b.themes}
    out: dict[str, dict[str, Any]] = {}
    for key, th in themes.items():
        sub = confirmation_df[confirmation_df["industry_code"].isin(th.industry_codes())] \
            if not confirmation_df.empty else pd.DataFrame()
        entry: dict[str, Any] = {
            "label": th.label,
            "bucket": next(b.key for b in buckets if key in [t.key for t in b.themes]),
            "bucket_label": next(b.label for b in buckets if key in [t.key for t in b.themes]),
            "industries": th.industry_codes(),
        }
        if sub.empty:
            entry.update({"confirmed": False, "n_strong": 0, "n_observe": 0,
                          "median_rps15": None, "reason": "无确认数据"})
        else:
            levels = sub["strength_level"].astype(str)
            n_strong = int(levels.isin(["强势"]).sum())
            n_observe = int(levels.isin(["强势", "观察"]).sum())
            rps = sub["RPS15"].dropna()
            # 上涨结构（对已穿透行业取中位）：保留原始值供表达决策，展示时另行保留两位小数
            part = sub["participation_rate"].dropna()
            hhi = sub["hhi"].dropna()
            top3 = sub["top3_share"].dropna()
            confirmed = n_observe > 0
            max_rps = round(float(rps.max()), 1) if not rps.empty else None
            breadth_state, breadth_label = classify_confirmation_breadth(
                confirmed, n_observe, len(sub), max_rps,
                broad_fraction=CONF_BROAD_FRACTION, watch_proximity=CONF_WATCH_PROXIMITY)
            evidence = _confirm_evidence(sub, confirmed)
            reason = (
                f"{n_observe}/{len(sub)} 个行业进入观察区"
                f"（{breadth_label}，依据 {evidence.get('industry', '')} RPS15={evidence.get('rps15')}）"
                if n_observe else "无行业进入观察区")
            entry.update({
                "confirmed": confirmed,
                "n_strong": n_strong,
                "n_observe": n_observe,
                "median_rps15": _round(rps.median()) if not rps.empty else None,
                "strongest_industry_rps15": max_rps,
                "median_participation": float(part.median()) if not part.empty else None,
                "median_hhi": float(hhi.median()) if not hhi.empty else None,
                "median_top3_share": float(top3.median()) if not top3.empty else None,
                "confirmation_state": breadth_state,
                "confirmation_breadth": breadth_label,
                "confirm_evidence": evidence,
                "reason": reason,
            })
        # 主题 ETF 中位 RPS15（Layer① 按关键词匹配，供展示）
        if not rotation_df.empty and "fund_name" in rotation_df.columns:
            matched = rotation_df[rotation_df["fund_name"].apply(
                lambda n: themes_cfg.match_theme(n, buckets) == key)]
            r = matched["rps15"].dropna()
            entry["etf_median_rps15"] = _round(r.median()) if not r.empty else None
        else:
            entry["etf_median_rps15"] = None
        out[key] = entry
        logger.info("主题[%s]: confirmed=%s (%s)", key, entry.get("confirmed"), entry.get("reason"))
    return out


def evaluate_direction(theme_metas: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """跨主题方向门控：任一主题行业确认 → PROCEED，否则 WATCHLIST_ONLY。"""
    confirmed = [k for k, m in theme_metas.items() if m.get("confirmed")]
    if confirmed:
        return {"gate": "PROCEED", "n_confirmed_themes": len(confirmed),
                "confirmed_themes": confirmed,
                "reason": f"{len(confirmed)} 个主题行业进入观察区（RPS15≥80）"}
    return {"gate": "WATCHLIST_ONLY", "n_confirmed_themes": 0,
            "confirmed_themes": [],
            "reason": "无主题进入观察区（RPS15≥80 确认证据），仅输出观察候选"}


# ── 2. ETF 候选（动态从 Layer① rotation 选） ───────────────────────

def match_theme(fund_name: str) -> str | None:
    """按 config/themes_two_directions.yaml 的 etf_keywords 匹配首个 theme（bucket 顺序优先）。"""
    return themes_cfg.match_theme(fund_name)


def select_etf_candidates(
    rotation_df: pd.DataFrame,
    account_df: pd.DataFrame,
    master_df: pd.DataFrame,
    theme: str,
    trend_gates: set[str] = ETF_TREND_GATES,
    min_amount: float = ETF_MIN_AMOUNT,
) -> pd.DataFrame:
    """从全市场 rotation 中筛选某主题的 ETF，附加趋势门控与流动性。

    ETF 归属按主题关键词匹配（不再依赖 Layer① 单一 is_tech 焦点组）。

    Returns:
        DataFrame（含 selection_score，未排序前）
    """
    if rotation_df.empty or "fund_name" not in rotation_df.columns:
        return pd.DataFrame()

    etf = rotation_df.copy()
    etf["_theme"] = etf["fund_name"].apply(match_theme)
    etf = etf[etf["_theme"] == theme]

    # 合并 trend_state（来自 account_candidates / watchlist）
    if not account_df.empty and "trend_state" in account_df.columns:
        etf = etf.merge(account_df[["fund_code", "trend_state", "account_tradable"]].drop_duplicates(subset=["fund_code"]),
                        on="fund_code", how="left")
    else:
        etf["trend_state"] = ""
        etf["account_tradable"] = True

    # 合并流动性（来自 master）
    if not master_df.empty and "amount" in master_df.columns:
        etf = etf.merge(master_df[["fund_code", "amount"]].drop_duplicates(subset=["fund_code"]),
                        on="fund_code", how="left")
        etf["amount"] = pd.to_numeric(etf.get("amount"), errors="coerce")
    else:
        etf["amount"] = pd.NA

    # 趋势门控（None = 不过滤，仅用于观察兜底展示主题代表）
    if trend_gates is not None:
        etf = etf[etf["trend_state"].isin(trend_gates)].copy()
    # 数据完整性：横截面日期无有效 RPS15 的 ETF 不作为候选（剔除数据缺口/历史不足标的）
    if "rps15" in etf.columns:
        etf = etf[etf["rps15"].notna()].copy()
    # 流动性门槛
    etf = etf[etf["amount"].fillna(0) >= min_amount].copy()

    # 选择评分：RPS15 主导 + 流动性 + 多周期一致性
    rps15 = pd.to_numeric(etf.get("rps15"), errors="coerce").fillna(0)
    rps20 = pd.to_numeric(etf.get("rps20"), errors="coerce").fillna(0)
    rps60 = pd.to_numeric(etf.get("rps60"), errors="coerce").fillna(0)
    amount = pd.to_numeric(etf.get("amount"), errors="coerce").fillna(0)
    amount_log = amount.apply(lambda x: float(x) if x > 0 else 0.0).apply(lambda x: 0 if x == 0 else __import__("math").log10(x))
    amount_score = ((amount_log - amount_log.min()) / (amount_log.max() - amount_log.min() + 1e-9) * 100).fillna(0)
    etf["selection_score"] = (0.55 * rps15 + 0.25 * rps20 + 0.20 * amount_score).round(1)
    return etf


# 常见基金公司名（用于剥离 ETF 名称尾缀，识别方向词）
_FUND_COMPANIES = [
    "国泰", "华夏", "易方达", "鹏华", "富国", "南方", "嘉实", "博时", "华泰柏瑞",
    "天弘", "招商", "广发", "汇添富", "工银", "华安", "万家", "建信", "银华",
    "国联安", "华宝", "景顺", "申万菱信", "方正富邦", "民生加银", "兴业", "泰康",
    "国寿", "中金", "东财", "永赢", "平安", "海富通", "大成", "长信", "前海开源",
    "交银", "浦银安盛", "中欧", "兴全", "上投摩根", "贝莱德", "华富", "浙商",
    "诺安", "国联", "华泰", "信诚", "天弘", "银华", "创金合信", "国投瑞银",
]


def _direction_word(name: str) -> str:
    """从 ETF 名称提取方向词（剥离 ETF 与基金公司名）。"""
    n = str(name or "").replace("ETF", "").strip()
    for company in _FUND_COMPANIES:
        if n.endswith(company):
            n = n[: -len(company)]
            break
        if n.startswith(company):
            n = n[len(company):]
            break
    return n.strip() or name


def _dedup_etf(etf_df: pd.DataFrame) -> pd.DataFrame:
    """同类 ETF（同方向词）只保留代表。"""
    if etf_df.empty:
        return etf_df
    df = etf_df.copy()
    df["_direction"] = df["fund_name"].apply(_direction_word)
    df = df.sort_values(["selection_score"], ascending=False)
    df = df.drop_duplicates(subset=["_direction"], keep="first")
    return df.reset_index(drop=True)


# ── 3. 个股固定观察池 ─────────────────────────────────────────────

def _stock_state(
    score_trend: float | None,
    watch_level: str,
    action_txt: str,
    theme_confirmed: bool,
) -> str:
    """个股三层状态判定。

    QUALIFIED = 趋势合格（score≥70 且 S/A 且非剔除观察/风险警戒）
    RECOMMENDED = QUALIFIED 且 所在主题行业确认（+主题归属由调用方保证）
    """
    qualified = (
        score_trend is not None
        and score_trend >= STOCK_QUALIFIED_SCORE
        and watch_level in ("S", "A")
        and action_txt not in ("剔除观察", "风险警戒")
    )
    if not qualified:
        return STOCK_STATE_WATCH
    if theme_confirmed:
        return STOCK_STATE_RECOMMENDED
    return STOCK_STATE_QUALIFIED


def _trend_change_fields(
    symbol: str,
    market: str,
    score_trend: float | None,
    watch_level: str,
) -> dict[str, Any]:
    """从 trend processed CSV 重算最近历史分数/watch_level，给出变化跟踪字段。

    固定观察池的价值在于变化：score_change_1d（一日分数变动）、
    state_change（watch_level 变化，如 A→B）、days_in_state（当前等级持续天数）、
    last_trend_qualified_date（最近一次趋势分数达到资格线 S/A 的日期）。
    注意：这是「趋势达标」日期，不等同于 selection 最终状态 QUALIFIED。
    """
    out = {"score_change_1d": None, "state_change": "", "days_in_state": 1, "last_trend_qualified_date": ""}
    try:
        from src.common.paths import processed_dir
        from src.trend_engine import engine as te
        from src.trend_engine import scoring as tscoring
    except Exception:
        return out
    path = processed_dir() / f"{market}_{symbol}.csv"
    if not path.exists():
        return out
    try:
        df = pd.read_csv(path, parse_dates=["date"]).sort_values("date").tail(40)
    except Exception:
        return out
    if df.empty:
        return out

    hist: list[tuple[pd.Timestamp, int, str]] = []
    for _, row in df.iterrows():
        s, _ = tscoring.score_latest_row(pd.DataFrame([row]))
        rs = row.get("relative_strength_20d")
        wl = te.calc_watch_level(
            s,
            float(rs) if pd.notna(rs) else None,
            float(row["ma20"]) if pd.notna(row.get("ma20")) else None,
            float(row["ma60"]) if pd.notna(row.get("ma60")) else None,
            float(row["volume_ratio"]) if pd.notna(row.get("volume_ratio")) else None,
        )
        hist.append((pd.Timestamp(row["date"]), int(s), wl))
    if not hist:
        return out

    if len(hist) >= 2 and score_trend is not None:
        out["score_change_1d"] = int(score_trend) - hist[-2][1]

    today_wl = str(watch_level)
    days = 0
    for _d, _s, wl in reversed(hist):
        if wl == today_wl:
            days += 1
        else:
            break
    out["days_in_state"] = days
    if len(hist) >= 2:
        prev_wl = hist[-2][2]
        if prev_wl and prev_wl != today_wl:
            out["state_change"] = f"{prev_wl}→{today_wl}"
    for d, _s, wl in reversed(hist):
        if wl in ("S", "A"):
            out["last_trend_qualified_date"] = d.date().isoformat()
            break
    return out


def select_stock_watchlist(
    universe_items: list[Any],
    theme: str,
    trend_df: pd.DataFrame,
    theme_confirmed: bool = False,
) -> tuple[list[AssetCandidate], list[AssetCandidate], list[AssetCandidate]]:
    """输出该主题的固定观察池全量（universe 分层池），不按强弱筛选。

    每个标的状态由 _stock_state 判定（WATCH / QUALIFIED / RECOMMENDED），
    降级与风险警戒同样保留，用于监控状态变化。
    趋势数据缺失（data_status=missing）的标的标记 selection_status=unavailable，
    不进入任何候选（不阻塞整体 Selection）。

    Returns:
        (leaders, high_beta, equipment)
    """
    leaders: list[AssetCandidate] = []
    high_beta: list[AssetCandidate] = []
    equipment: list[AssetCandidate] = []

    for item in universe_items:
        if item.theme != theme:
            continue
        role = TIER_ROLE_MAP.get(item.tier)
        if role is None:
            continue

        # 从 Trend Engine 结果读取趋势
        trend_row = None
        if not trend_df.empty:
            m = trend_df[trend_df["symbol"].astype(str) == item.asset.symbol]
            if not m.empty:
                trend_row = m.iloc[0]

        def _tv(col: str, default: Any = None) -> Any:
            if trend_row is None:
                return default
            v = trend_row.get(col)
            return default if v is None or (isinstance(v, float) and pd.isna(v)) else v

        data_status = str(_tv("data_status", "current"))
        if trend_row is None or data_status == "missing":
            # 局部降级：该资产无可用趋势数据，明确标记 unavailable，不阻塞整体
            cand = AssetCandidate(
                code=item.asset.symbol,
                name=item.asset.name,
                role=role,
                asset_type="stock",
                bucket=item.bucket,
                theme=theme,
                score_trend=None,
                trend_status="UNKNOWN",
                tradable=True,
                recommended=False,
                state=STOCK_STATE_WATCH,
                data_status=data_status if trend_row is not None else "missing",
                selection_status="unavailable",
                risk_gate_passed=False,
                reason="stock_trend_input_missing",
            )
            _append_by_role(cand, role, leaders, high_beta, equipment)
            continue

        watch_level = str(_tv("watch_level", ""))
        action_txt = str(_tv("action", ""))
        score_trend = _round(_tv("score_trend"))
        state = _stock_state(score_trend, watch_level, action_txt, theme_confirmed)
        change = _trend_change_fields(item.asset.symbol, item.asset.market, score_trend, watch_level)
        risk_flags_raw = str(_tv("risk_flags", "") or "")
        risk_flags = [f.strip() for f in risk_flags_raw.split("，") if f.strip()] if risk_flags_raw else []
        risk_gate_passed = action_txt not in ("风险警戒", "剔除观察") and not risk_flags
        cand = AssetCandidate(
            code=item.asset.symbol,
            name=item.asset.name,
            role=role,
            asset_type="stock",
            bucket=item.bucket,
            theme=theme,
            rps15=None,  # 个股无全市场 RPS 横截面；relative_strength_20d 是相对收益，不映射进 rps15
            score_trend=score_trend,
            trend_status=_clean_trend_status(watch_level),
            tradable=True,  # 黑名单机制：未确认不可交易即默认可交易
            recommended=state == STOCK_STATE_RECOMMENDED,
            state=state,
            data_status=data_status,
            selection_status="available",
            score_change_1d=change["score_change_1d"],
            state_change=change["state_change"],
            days_in_state=change["days_in_state"],
            last_trend_qualified_date=change["last_trend_qualified_date"],
            risk_gate_passed=risk_gate_passed,
            risk_flags=risk_flags,
            reason=action_txt,
        )
        _append_by_role(cand, role, leaders, high_beta, equipment)

    return leaders, high_beta, equipment


def _append_by_role(
    cand: AssetCandidate,
    role: str,
    leaders: list[AssetCandidate],
    high_beta: list[AssetCandidate],
    equipment: list[AssetCandidate],
) -> None:
    if role == "LEADER":
        leaders.append(cand)
    elif role == "HIGH_BETA":
        high_beta.append(cand)
    else:
        equipment.append(cand)


# ── 4. 表达方式决策 ────────────────────────────────────────────────

def decide_expression(theme_meta: dict[str, Any]) -> dict[str, Any]:
    """基于上涨结构（参与率 / HHI / Top3）判断 ETF vs 个股。"""
    confirmed = theme_meta.get("confirmed", False)
    if not confirmed:
        return {"expression": "WATCHLIST_ONLY",
                "expression_label": EXPRESSION_LABELS["WATCHLIST_ONLY"],
                "expression_reason": "主题行业未确认，仅输出观察候选"}

    part = theme_meta.get("median_participation")
    hhi = theme_meta.get("median_hhi")
    top3 = theme_meta.get("median_top3_share")

    # 龙头主导：HHI 高 或 Top3 贡献集中（单核/集中领涨）
    leader_dominated = (hhi is not None and hhi >= 0.15) or (top3 is not None and top3 >= 0.60)
    broad = part is not None and part >= 0.60

    if broad and not leader_dominated:
        expression, reason = "ETF_PRIORITY", "参与率≥60% 且结构分散，ETF 完整承接行业 Beta"
    elif leader_dominated:
        expression, reason = "LEADER_PRIORITY", "龙头贡献集中（HHI/Top3 高），优先龙头个股，ETF 作低风险替代"
    else:
        expression, reason = "ETF_CORE_PLUS_LEADER", "扩散形成中，ETF 作核心、龙头作卫星"

    return {"expression": expression,
            "expression_label": EXPRESSION_LABELS.get(expression, expression),
            "expression_reason": reason}


def build_top_action(theme_objs: list[dict[str, Any]]) -> dict[str, Any]:
    """顶层行动建议：只回答「今天投哪一个方向」，不枚举具体标的。

    输出 BUY / OBSERVE / WAIT + 方向（bucket）与主题；ETF / 股票 / 观察池
    全部落在下层 buckets[].themes[]，不进入顶层 Action。
    """
    confirmed = [s for s in theme_objs if s.get("confirmed")]
    if not confirmed:
        return {"level": "WAIT", "direction": "", "direction_label": "",
                "theme": "", "theme_label": "", "expression": "", "expression_label": "",
                "summary": "今日方向：等待 —— 无主题进入观察区（RPS15≥80 确认证据），不建仓"}
    rank = {"ETF_PRIORITY": 3, "LEADER_PRIORITY": 3, "ETF_CORE_PLUS_LEADER": 2, "WATCHLIST_ONLY": 1}
    best = max(confirmed, key=lambda s: rank.get(s.get("expression", ""), 1))
    expr = best.get("expression", "")
    expr_label = best.get("expression_label", expr)
    theme_label = best.get("theme_label", best.get("theme", ""))
    direction_label = best.get("bucket_label", best.get("bucket", ""))
    base = {
        "direction": best.get("bucket", ""),
        "direction_label": direction_label,
        "theme": best.get("theme", ""),
        "theme_label": theme_label,
        "expression": expr,
        "expression_label": expr_label,
    }
    if expr != "WATCHLIST_ONLY":
        # 有可行动表达（即使暂无推荐标的，标的详情在下层）
        return {"level": "BUY", **base,
                "summary": f"今日方向：买入 {direction_label} · {theme_label}"}
    return {"level": "OBSERVE", **base,
            "summary": f"今日方向：观察 {direction_label} · {theme_label}（已确认但表达为仅观察）"}


# ── 5. 构建候选对象 ────────────────────────────────────────────────

def build_candidates(
    *,
    rotation_df: pd.DataFrame,
    account_df: pd.DataFrame,
    master_df: pd.DataFrame,
    confirmation_df: pd.DataFrame,
    universe_items: list[Any],
    trend_df: pd.DataFrame,
) -> dict[str, Any]:
    """构建 Layer ③ 候选资产对象（结构化 dict，可直接落 JSON）。

    v0.4.3 输出结构：buckets[].themes[]，每个 theme 含 ETF 候选 / 个股观察池 / 表达决策。
    """
    theme_metas = evaluate_themes(confirmation_df, rotation_df)
    direction = evaluate_direction(theme_metas)
    buckets_cfg = themes_cfg.load_buckets()

    buckets_out: list[dict[str, Any]] = []
    for bucket_cfg in buckets_cfg:
        theme_objs: list[dict[str, Any]] = []
        for th in bucket_cfg.themes:
            key = th.key
            meta = theme_metas.get(key, {"label": th.label, "confirmed": False,
                                         "industries": th.industry_codes(),
                                         "reason": "无确认数据"})

            # ETF 候选（动态从 Layer① 选）：严格池 → WATCH 观察池 → 主题代表兜底
            etf_pool = select_etf_candidates(rotation_df, account_df, master_df, key,
                                             trend_gates=ETF_TREND_GATES)
            dedup = _dedup_etf(etf_pool)
            if dedup.empty:
                etf_pool = select_etf_candidates(rotation_df, account_df, master_df, key,
                                                 trend_gates=ETF_WATCH_GATES)
                dedup = _dedup_etf(etf_pool)
            if dedup.empty:
                etf_pool = select_etf_candidates(rotation_df, account_df, master_df, key,
                                                 trend_gates=None)
                dedup = _dedup_etf(etf_pool)
            core_etf: list[AssetCandidate] = []
            sub_industry_etf: list[AssetCandidate] = []
            if not dedup.empty:
                top = dedup.sort_values("selection_score", ascending=False)
                # 核心 ETF：主题内评分最高 1 只
                c = top.iloc[0]
                core_etf.append(_to_etf_candidate(c, "CORE_ETF", bucket_cfg.key, key, "主题评分最高"))
                # 细分 ETF：其余不同方向各取 1 只（最多 2）
                for _, r in top.iloc[1:].iterrows():
                    if len(sub_industry_etf) >= 2:
                        break
                    sub_industry_etf.append(_to_etf_candidate(r, "SUB_INDUSTRY_ETF", bucket_cfg.key, key, "细分方向代表"))

            # 个股固定观察池（全量）+ 动态候选（按状态门控后的子集）
            leaders, high_beta, equipment = select_stock_watchlist(
                universe_items, key, trend_df, theme_confirmed=meta["confirmed"])
            stock_watchlist = {
                "leaders": [c.to_dict() for c in leaders],
                "high_beta": [c.to_dict() for c in high_beta],
                "equipment": [c.to_dict() for c in equipment],
            }
            stock_candidates = [
                c.to_dict() for c in (leaders + high_beta + equipment)
                if c.state in (STOCK_STATE_QUALIFIED, STOCK_STATE_RECOMMENDED)
            ]

            # 表达决策（基于原始结构中位数，不因展示舍入翻转阈值）
            expr = decide_expression(meta)

            # 展示指标：结构字段保留两位小数，避免 hhi=0.04 被舍为 0.0
            def _fmt_metric(k: str, v: Any) -> Any:
                if v is None:
                    return None
                if k in ("median_participation", "median_hhi", "median_top3_share"):
                    return round(float(v), 2)
                if k in ("median_rps15",):
                    return _round(v)
                return v

            theme_obj = {
                "theme": key,
                "theme_label": th.label,
                "bucket": bucket_cfg.key,
                "bucket_label": bucket_cfg.label,
                "objective": th.objective,
                "signal_model": th.signal_model,
                "maturity": th.maturity,
                "confirmed": meta["confirmed"],
                "confirmation_reason": meta["reason"],
                "confirmation_state": meta.get("confirmation_state", ""),
                "confirmation_breadth": meta.get("confirmation_breadth", ""),
                "confirm_evidence": meta.get("confirm_evidence", {}),
                "metrics": {k: _fmt_metric(k, v) for k, v in meta.items()
                            if k not in ("label", "bucket", "bucket_label", "industries", "confirmed", "reason",
                                         "confirmation_state", "confirmation_breadth", "confirm_evidence")},
                "expression": expr["expression"],
                "expression_label": expr["expression_label"],
                "expression_reason": expr["expression_reason"],
                "core_etf": [c.to_dict() for c in core_etf],
                "sub_industry_etf": [c.to_dict() for c in sub_industry_etf],
                "stock_watchlist": stock_watchlist,
                "stock_candidates": stock_candidates,
            }
            theme_obj.update(_theme_stage_meta(theme_obj))
            theme_objs.append(theme_obj)

        theme_objs.sort(key=_theme_sort_key)
        bucket_obj: dict[str, Any] = {
            "bucket": bucket_cfg.key,
            "bucket_label": bucket_cfg.label,
            "objective": bucket_cfg.objective,
            "n_themes": len(theme_objs),
            "n_confirmed": sum(1 for t in theme_objs if t.get("confirmed")),
            "themes": theme_objs,
        }
        buckets_out.append(bucket_obj)

    all_themes = [t for b in buckets_out for t in b["themes"]]
    recommended_actions = _collect_recommended_actions(all_themes)
    closest = _closest_theme(all_themes)
    summary = _selection_summary(buckets_out)
    action = build_top_action(all_themes)
    logger.info("candidates built: %d buckets / %d themes (action=%s, recommended=%d)",
                len(buckets_out), len(all_themes), action["level"],
                len(recommended_actions))
    return {
        "version": "0.4.3",
        "direction": direction,
        "buckets": buckets_out,
        "recommended_actions": recommended_actions,
        "closest_theme": closest,
        "summary": summary,
        "action": action,
    }


def _theme_stage_meta(theme_obj: dict[str, Any]) -> dict[str, Any]:
    """主题阶段判断：确认门槛在行业 RPS15≥80，距离以行业口径为准。

    distance_to_industry_confirm = 80 - 主题最强行业 RPS15（真实确认门）
    distance_to_etf_strength     = 80 - 最强 ETF RPS15（ETF 自身强势门槛，仅供参考）
    两者口径不同，分开暴露，不混用。
    """
    strongest = theme_obj.get("core_etf")[0] if theme_obj.get("core_etf") else None
    strongest_rps = strongest.get("rps15") if strongest else None
    m = theme_obj.get("metrics", {})
    ind_rps = m.get("strongest_industry_rps15")
    confirmed = theme_obj.get("confirmed", False)

    if confirmed:
        stage = "已确认"
        d_ind = 0
        d_etf = round(SUBTHEME_CONFIRM_RPS15 - float(strongest_rps), 1) if strongest_rps is not None else None
    else:
        d_ind = round(SUBTHEME_CONFIRM_RPS15 - float(ind_rps), 1) if ind_rps is not None else None
        d_etf = round(SUBTHEME_CONFIRM_RPS15 - float(strongest_rps), 1) if strongest_rps is not None else None
        stage = "修复观察" if (d_ind is not None and d_ind <= 20) else "弱势"
    return {
        "stage": stage,
        "strongest_etf": {
            "code": strongest.get("code", ""),
            "name": strongest.get("name", ""),
            "rps15": strongest_rps,
            "return_5d": strongest.get("return_5d"),
            "return_20d": strongest.get("return_20d"),
        } if strongest else None,
        "distance_to_industry_confirm": d_ind,
        "distance_to_etf_strength": d_etf,
    }


def _theme_sort_key(t: dict[str, Any]) -> tuple[int, int, float, float, str]:
    """主题相对状态排序：确认在前，再按阶段（行业距离离散化）、
    行业确认距离升序、最强 ETF RPS15 降序。保证第一行 = 最接近确认的展开项。"""
    stage_rank = {"已确认": 0, "修复观察": 1, "弱势": 2}
    se = t.get("strongest_etf") or {}
    rps = se.get("rps15")
    d_ind = t.get("distance_to_industry_confirm")
    return (
        0 if t.get("confirmed") else 1,
        stage_rank.get(t.get("stage", ""), 9),
        float(d_ind) if d_ind is not None else 1e9,
        -(float(rps) if rps is not None else -1e9),
        t.get("theme", ""),
    )


def _closest_theme(theme_objs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """最接近转强：排序后第一个未确认主题的轻量摘要（完整内容仍在 themes）。"""
    target = next((s for s in theme_objs if not s.get("confirmed")), None)
    if target is None:
        return None
    return {
        "theme": target.get("theme"),
        "theme_label": target.get("theme_label"),
        "bucket": target.get("bucket"),
        "bucket_label": target.get("bucket_label"),
        "stage": target.get("stage"),
        "strongest_etf": target.get("strongest_etf"),
        "distance_to_industry_confirm": target.get("distance_to_industry_confirm"),
        "distance_to_etf_strength": target.get("distance_to_etf_strength"),
    }


def _selection_summary(buckets: list[dict[str, Any]]) -> dict[str, Any]:
    """首屏四个数字：推荐行动 / 合格候选 / 确认主题 / ETF 表现最强。"""
    theme_objs = [t for b in buckets for t in b.get("themes", [])]
    qualified = sum(
        1 for t in theme_objs
        for c in t.get("stock_candidates", []) if c.get("state") == STOCK_STATE_QUALIFIED
    )

    def _etf_rps(t: dict[str, Any]) -> float:
        se = t.get("strongest_etf") or {}
        return float(se["rps15"]) if se.get("rps15") is not None else -1e9

    etf_best = max(theme_objs, key=_etf_rps) if theme_objs else None
    return {
        "recommended_actions": len(_collect_recommended_actions(theme_objs)),
        "qualified_candidates": qualified,
        "confirmed_themes": f"{sum(1 for t in theme_objs if t.get('confirmed'))}/{len(theme_objs)}",
        "strongest_etf_theme": etf_best.get("theme_label") if etf_best else "",
    }


def _collect_recommended_actions(theme_objs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """跨主题汇总「今日行动候选」：推荐 ETF + RECOMMENDED 个股。

    同一资产在多个 theme 注册时（跨主题归属），按 (asset_type, code) 去重，
    保留首个出现（bucket order 靠前者 = primary 归属）。Position 权重归属属于 Layer 4。
    """
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for t in theme_objs:
        for a in (t.get("core_etf", []) + t.get("sub_industry_etf", [])
                  + t.get("stock_candidates", [])):
            if not a.get("recommended"):
                continue
            key = (str(a.get("asset_type", "")), str(a.get("code", "")))
            if key in seen:
                continue
            seen.add(key)
            out.append(a)
    return out


def _to_etf_candidate(row: pd.Series, role: str, bucket: str, theme: str, reason: str) -> AssetCandidate:
    return AssetCandidate(
        code=str(row["fund_code"]),
        name=str(row.get("fund_name", "")),
        role=role,
        asset_type="etf",
        bucket=bucket,
        theme=theme,
        rps15=_round(row.get("rps15")),
        rps20=_round(row.get("rps20")),
        rps60=_round(row.get("rps60")),
        return_5d=_round(row.get("return_5d")),
        return_20d=_round(row.get("return_20d")),
        trend_status=_clean_trend_status(row.get("trend_state", "")),
        rank_change_5d=_round(row.get("rank_change_5d")),
        liquidity=_round(row.get("amount")),
        tradable=bool(row.get("account_tradable", False)),
        recommended=bool(row.get("trend_state", "")) in ETF_TREND_GATES,
        state=STOCK_STATE_RECOMMENDED if str(row.get("trend_state", "")) in ETF_TREND_GATES else STOCK_STATE_WATCH,
        selection_score=_round(row.get("selection_score")),
        reason=reason,
    )


# ── 持久化 ─────────────────────────────────────────────────────────

def save_candidates_json(
    candidates: dict[str, Any],
    output_dir: Path,
    date_str: str,
    meta: dict[str, Any] | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"tradable_candidates_{date_str}.json"
    payload: dict[str, Any] = {"date": date_str, "layer3": candidates}
    if meta:
        payload.update(meta)
    path.write_text(_json_dumps(payload), encoding="utf-8")
    logger.info("candidates json: %s", path)
    return path


def _json_dumps(obj: Any) -> str:
    import json

    def _default(o: Any):
        if isinstance(o, (pd.Timestamp, pd.Timedelta)):
            return str(o)
        if isinstance(o, float):
            return o
        return str(o)
    return json.dumps(obj, ensure_ascii=False, indent=2, default=_default)
