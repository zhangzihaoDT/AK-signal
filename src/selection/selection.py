"""
Layer ③ — 交易标的筛选与表达方式选择（Tradable Selection）

定位：执行对象压缩层，不是又一层强弱排名。
职责：把 Layer ①（ETF 轮动）与 Layer ②（行业确认）的结论，压缩成
      「这个已确认方向，应当由哪只 ETF、哪类股票来交易」。

核心输出：候选资产对象（结构化 dict/JSON），HTML 报告只是它的可视化。

流程：
  1. 方向门控      Layer① 焦点组 verdict + Layer② 群共振 status 决定是否处理
  2. 子主题确认     按 ai_core / digital_infrastructure / intelligent_manufacturing
                    聚合 Layer② confirmation，判断每个子主题是否被确认
  3. ETF 候选      动态从 Layer① rotation 全市场选（趋势门控 + 流动性 + 排序 + 去重）
  4. 个股候选      从 universe.yaml（leader/high_beta/equipment）+ 趋势报告选择
  5. 表达决策       基于上涨结构（参与率 / HHI / Top3）选 ETF vs 个股
  6. 构建候选对象   每子主题输出 core_etf / sub_industry_etf / leaders / high_beta / equipment

Layer 4 边界：本层只回答「买什么」，不回答「买多少 / 何时买 / 何时卖」。
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger("selection")

# ── 子主题定义（复用 Layer② 的分组语义） ───────────────────────────
SUBTHEMES: dict[str, dict[str, Any]] = {
    "ai_core": {
        "label": "AI 核心产业链",
        "industries": ["801081.SI", "801083.SI", "801085.SI", "801101.SI", "801104.SI"],
        # ETF 名称关键词（按优先级匹配）
        "etf_keywords": ["人工智能", "ai", "芯片", "半导体", "集成电路", "科创芯片", "算力", "电子", "科技"],
    },
    "digital_infrastructure": {
        "label": "数字基础设施（TMT）",
        "industries": ["801102.SI", "801223.SI", "801103.SI"],
        "etf_keywords": ["通信", "云计算", "软件", "计算机", "5g", "信息技术", "大数据"],
    },
    "intelligent_manufacturing": {
        "label": "智能制造",
        "industries": ["801078.SI", "801084.SI"],
        "etf_keywords": ["机器人", "自动化", "智能制造", "工业母机", "机器视觉"],
    },
}

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

# 趋势门控：允许进入候选的 ETF 状态
ETF_TREND_GATES = {"BUY_CANDIDATE", "STRONG_WATCH"}
# 观察池（弱势市场兜底）：额外纳入 WATCH，仅作观察候选，recommended=False
ETF_WATCH_GATES = ETF_TREND_GATES | {"WATCH"}
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
# 个股趋势合格门槛（与 trend_engine watch_level=S/A 的 score>=70 对齐）
STOCK_QUALIFIED_SCORE = 70
# 子主题确认门槛（与 Layer② OBSERVE_THRESHOLD 对齐，ETF RPS15 距此门槛的远近衡量接近转强程度）
SUBTHEME_CONFIRM_RPS15 = 80


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
    subtheme: str = ""
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


# ── 1. 方向门控 ────────────────────────────────────────────────────

def evaluate_direction(
    rotation_df: pd.DataFrame,
    confirmation_df: pd.DataFrame,
) -> dict[str, Any]:
    """Layer① 焦点组 verdict + Layer② 群共振 status → 主题级信号。"""
    direction: dict[str, Any] = {"gate": "SKIP", "reason": ""}

    # Layer② 群共振：confirmation 的 strength_level 分布
    if not confirmation_df.empty and "strength_level" in confirmation_df.columns:
        levels = confirmation_df["strength_level"].astype(str)
        n_strong = int(levels.isin(["强势", "观察"]).sum())
        confirmed = n_strong > 0
    else:
        confirmed = False

    # Layer① 焦点组：tech ETF 中位 RPS15
    tech_median = None
    if not rotation_df.empty and "is_tech" in rotation_df.columns:
        tech = rotation_df[rotation_df["is_tech"] == True]
        if not tech.empty and "rps15" in tech.columns:
            tech_median = _round(tech["rps15"].median())

    if confirmed:
        direction = {"gate": "PROCEED", "tech_median_rps15": tech_median,
                     "reason": f"Layer② 有 {n_strong} 个重点行业进入强势/观察区"}
    else:
        direction = {"gate": "WATCHLIST_ONLY", "tech_median_rps15": tech_median,
                     "reason": "Layer② 行业群未确认（无行业进入观察区），仅输出观察候选"}

    logger.info("方向门控: %s | %s", direction["gate"], direction["reason"])
    return direction


# ── 2. 子主题确认 ──────────────────────────────────────────────────

def evaluate_subthemes(confirmation_df: pd.DataFrame) -> dict[str, dict[str, Any]]:
    """按子主题聚合 Layer② confirmation，判定每个子主题是否确认。"""
    out: dict[str, dict[str, Any]] = {}
    for key, tdef in SUBTHEMES.items():
        sub = confirmation_df[confirmation_df["industry_code"].isin(tdef["industries"])] if not confirmation_df.empty else pd.DataFrame()
        entry: dict[str, Any] = {"label": tdef["label"], "industries": tdef["industries"]}
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
            entry.update({
                "confirmed": n_observe > 0,
                "n_strong": n_strong,
                "n_observe": n_observe,
                "median_rps15": _round(rps.median()) if not rps.empty else None,
                "strongest_industry_rps15": round(float(rps.max()), 1) if not rps.empty else None,
                "median_participation": float(part.median()) if not part.empty else None,
                "median_hhi": float(hhi.median()) if not hhi.empty else None,
                "median_top3_share": float(top3.median()) if not top3.empty else None,
                "reason": f"{n_observe}/{len(sub)} 个行业进入观察区" if n_observe else "无行业进入观察区",
            })
        out[key] = entry
        logger.info("子主题[%s]: confirmed=%s (%s)", key, entry.get("confirmed"), entry.get("reason"))
    return out


# ── 3. ETF 候选（动态从 Layer① rotation 选） ───────────────────────

def _match_subtheme(name: str) -> str | None:
    n = (name or "").lower()
    for key, tdef in SUBTHEMES.items():
        for kw in tdef["etf_keywords"]:
            if kw.lower() in n:
                return key
    return None


def select_etf_candidates(
    rotation_df: pd.DataFrame,
    account_df: pd.DataFrame,
    master_df: pd.DataFrame,
    subtheme: str,
    trend_gates: set[str] = ETF_TREND_GATES,
    min_amount: float = ETF_MIN_AMOUNT,
) -> pd.DataFrame:
    """从全市场 rotation 中筛选某子主题的 ETF，附加趋势门控与流动性。

    Returns:
        DataFrame（含 selection_score，未排序前）
    """
    if rotation_df.empty:
        return pd.DataFrame()

    etf = rotation_df[rotation_df["is_tech"] == True].copy()
    if etf.empty:
        return pd.DataFrame()

    # 按名称关键词匹配子主题
    etf["_subtheme"] = etf["fund_name"].apply(_match_subtheme)
    etf = etf[etf["_subtheme"] == subtheme]

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

    # 趋势门控（None = 不过滤，仅用于观察兜底展示子主题代表）
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


# ── 4. 个股固定观察池 ─────────────────────────────────────────────

def _stock_state(
    score_trend: float | None,
    watch_level: str,
    action_txt: str,
    subtheme_confirmed: bool,
) -> str:
    """个股三层状态判定。

    QUALIFIED = 趋势合格（score≥70 且 S/A 且非剔除观察/风险警戒）
    RECOMMENDED = QUALIFIED 且 所在子主题行业确认（+主题归属由调用方保证）
    """
    qualified = (
        score_trend is not None
        and score_trend >= STOCK_QUALIFIED_SCORE
        and watch_level in ("S", "A")
        and action_txt not in ("剔除观察", "风险警戒")
    )
    if not qualified:
        return STOCK_STATE_WATCH
    if subtheme_confirmed:
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
    subtheme: str,
    trend_df: pd.DataFrame,
    subtheme_confirmed: bool = False,
) -> tuple[list[AssetCandidate], list[AssetCandidate], list[AssetCandidate]]:
    """输出该子主题的固定观察池全量（universe 分层池），不按强弱筛选。

    每个标的状态由 _stock_state 判定（WATCH / QUALIFIED / RECOMMENDED），
    降级与风险警戒同样保留，用于监控状态变化。

    Returns:
        (leaders, high_beta, equipment)
    """
    leaders: list[AssetCandidate] = []
    high_beta: list[AssetCandidate] = []
    equipment: list[AssetCandidate] = []

    # 行业代码 → 子主题
    ind_to_sub: dict[str, str] = {}
    for key, tdef in SUBTHEMES.items():
        for code in tdef["industries"]:
            ind_to_sub[code] = key

    for item in universe_items:
        if item.theme != "ai_tech":
            continue
        if ind_to_sub.get(item.sw_industry, "") != subtheme:
            # 无行业关联的高弹性标的（智能驾驶等）也归入 ai_core 高弹性
            if not (item.tier == "high_beta" and subtheme == "ai_core"):
                continue

        # 从 Trend Engine 结果读取趋势
        trend_row = None
        if not trend_df.empty:
            m = trend_df[trend_df["symbol"].astype(str) == item.asset.symbol]
            if not m.empty:
                trend_row = m.iloc[0]

        role = {"leader": "LEADER", "high_beta": "HIGH_BETA",
                "equipment_upstream": "UPSTREAM"}.get(item.tier)
        if role is None:
            continue

        def _tv(col: str, default: Any = None) -> Any:
            if trend_row is None:
                return default
            v = trend_row.get(col)
            return default if v is None or (isinstance(v, float) and pd.isna(v)) else v

        watch_level = str(_tv("watch_level", ""))
        action_txt = str(_tv("action", ""))
        score_trend = _round(_tv("score_trend"))
        state = _stock_state(score_trend, watch_level, action_txt, subtheme_confirmed)
        change = _trend_change_fields(item.asset.symbol, item.asset.market, score_trend, watch_level)
        risk_flags_raw = str(_tv("risk_flags", "") or "")
        risk_flags = [f.strip() for f in risk_flags_raw.split("，") if f.strip()] if risk_flags_raw else []
        risk_gate_passed = action_txt not in ("风险警戒", "剔除观察") and not risk_flags
        cand = AssetCandidate(
            code=item.asset.symbol,
            name=item.asset.name,
            role=role,
            asset_type="stock",
            subtheme=subtheme,
            rps15=None,  # 个股无全市场 RPS 横截面；relative_strength_20d 是相对收益，不映射进 rps15
            score_trend=score_trend,
            trend_status=_clean_trend_status(watch_level),
            tradable=True,  # 黑名单机制：未确认不可交易即默认可交易
            recommended=state == STOCK_STATE_RECOMMENDED,
            state=state,
            score_change_1d=change["score_change_1d"],
            state_change=change["state_change"],
            days_in_state=change["days_in_state"],
            last_trend_qualified_date=change["last_trend_qualified_date"],
            risk_gate_passed=risk_gate_passed,
            risk_flags=risk_flags,
            reason=action_txt,
        )
        if role == "LEADER":
            leaders.append(cand)
        elif role == "HIGH_BETA":
            high_beta.append(cand)
        else:
            equipment.append(cand)

    return leaders, high_beta, equipment


# ── 5. 表达方式决策 ────────────────────────────────────────────────

def decide_expression(subtheme_meta: dict[str, Any]) -> dict[str, Any]:
    """基于上涨结构（参与率 / HHI / Top3）判断 ETF vs 个股。"""
    confirmed = subtheme_meta.get("confirmed", False)
    if not confirmed:
        return {"expression": "WATCHLIST_ONLY",
                "expression_label": EXPRESSION_LABELS["WATCHLIST_ONLY"],
                "expression_reason": "子主题行业未确认，仅输出观察候选"}

    part = subtheme_meta.get("median_participation")
    hhi = subtheme_meta.get("median_hhi")
    top3 = subtheme_meta.get("median_top3_share")

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


def build_top_action(subtheme_objs: list[dict[str, Any]]) -> dict[str, Any]:
    """顶层唯一行动建议：基于确认子主题与可用推荐标的生成一句话结论。"""
    confirmed = [s for s in subtheme_objs if s.get("confirmed")]
    if not confirmed:
        return {"level": "WAIT",
                "summary": "全主题观察，不建仓：无行业进入观察区（RPS15≥80 确认证据），等待行业转强"}
    rank = {"ETF_PRIORITY": 3, "LEADER_PRIORITY": 3, "ETF_CORE_PLUS_LEADER": 2, "WATCHLIST_ONLY": 1}
    best = max(confirmed, key=lambda s: rank.get(s.get("expression", ""), 1))
    expr = best.get("expression_label", best.get("expression", ""))
    sub_label = best.get("subtheme_label", best.get("subtheme", ""))
    all_assets = (best.get("core_etf", []) + best.get("sub_industry_etf", [])
                  + best.get("stock_candidates", []))
    rec = [a for a in all_assets if a.get("recommended")]
    if expr != "WATCHLIST_ONLY" and rec:
        primary = rec[0]
        return {"level": "BUY",
                "summary": f"{sub_label} 已确认（{expr}），首选 {primary.get('name', primary.get('code', ''))} 建仓，共 {len(rec)} 个推荐标的"}
    if expr != "WATCHLIST_ONLY":
        return {"level": "OBSERVE",
                "summary": f"{sub_label} 已确认（{expr}），但暂无满足建仓条件的推荐标的，继续观察"}
    return {"level": "OBSERVE",
            "summary": f"{sub_label} 已确认但表达为仅观察，等待标的趋势转强"}


# ── 6. 构建候选对象 ────────────────────────────────────────────────

def build_candidates(
    *,
    rotation_df: pd.DataFrame,
    account_df: pd.DataFrame,
    master_df: pd.DataFrame,
    confirmation_df: pd.DataFrame,
    universe_items: list[Any],
    trend_df: pd.DataFrame,
) -> dict[str, Any]:
    """构建 Layer ③ 候选资产对象（结构化 dict，可直接落 JSON）。"""
    direction = evaluate_direction(rotation_df, confirmation_df)
    subthemes = evaluate_subthemes(confirmation_df)

    theme_obj: dict[str, Any] = {
        "theme": "ai_technology",
        "theme_label": "AI/科技/半导体",
        "direction_gate": direction["gate"],
        "direction_reason": direction["reason"],
        "tech_median_rps15": direction.get("tech_median_rps15"),
        "subthemes": [],
    }

    for key, meta in subthemes.items():
        # ETF 候选（动态从 Layer① 选）：严格池 → WATCH 观察池 → 子主题代表兜底
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
            # 核心 ETF：子主题内评分最高 1 只
            c = top.iloc[0]
            core_etf.append(_to_etf_candidate(c, "CORE_ETF", key, "子主题评分最高"))
            # 细分 ETF：其余不同方向各取 1 只（最多 2）
            for _, r in top.iloc[1:].iterrows():
                if len(sub_industry_etf) >= 2:
                    break
                sub_industry_etf.append(_to_etf_candidate(r, "SUB_INDUSTRY_ETF", key, "细分方向代表"))

        # 个股固定观察池（全量）+ 动态候选（按状态门控后的子集）
        leaders, high_beta, equipment = select_stock_watchlist(
            universe_items, key, trend_df, subtheme_confirmed=meta["confirmed"])
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

        sub_obj = {
            "subtheme": key,
            "subtheme_label": meta["label"],
            "confirmed": meta["confirmed"],
            "confirmation_reason": meta["reason"],
            "metrics": {k: _fmt_metric(k, v) for k, v in meta.items()
                        if k not in ("label", "industries", "confirmed", "reason")},
            "expression": expr["expression"],
            "expression_label": expr["expression_label"],
            "expression_reason": expr["expression_reason"],
            "core_etf": [c.to_dict() for c in core_etf],
            "sub_industry_etf": [c.to_dict() for c in sub_industry_etf],
            "stock_watchlist": stock_watchlist,
            "stock_candidates": stock_candidates,
        }
        sub_obj.update(_subtheme_stage_meta(sub_obj))
        theme_obj["subthemes"].append(sub_obj)

    theme_obj["subthemes"].sort(key=_subtheme_sort_key)
    theme_obj["recommended_actions"] = _collect_recommended_actions(theme_obj["subthemes"])
    theme_obj["closest_industry_subtheme"] = _closest_subtheme(theme_obj["subthemes"])
    theme_obj["summary"] = _theme_summary(theme_obj["subthemes"], theme_obj["recommended_actions"])
    theme_obj["action"] = build_top_action(theme_obj["subthemes"])
    logger.info("candidates built: %d subthemes (action=%s, recommended=%d)",
                len(theme_obj["subthemes"]), theme_obj["action"]["level"],
                len(theme_obj["recommended_actions"]))
    return theme_obj


def _subtheme_stage_meta(sub: dict[str, Any]) -> dict[str, Any]:
    """子主题阶段判断：确认门槛在行业 RPS15≥80，距离以行业口径为准。

    distance_to_industry_confirm = 80 - 子主题最强行业 RPS15（真实确认门）
    distance_to_etf_strength     = 80 - 最强 ETF RPS15（ETF 自身强势门槛，仅供参考）
    两者口径不同，分开暴露，不混用。
    """
    strongest = sub.get("core_etf")[0] if sub.get("core_etf") else None
    strongest_rps = strongest.get("rps15") if strongest else None
    m = sub.get("metrics", {})
    ind_rps = m.get("strongest_industry_rps15")
    confirmed = sub.get("confirmed", False)

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


def _subtheme_sort_key(s: dict[str, Any]) -> tuple[int, int, float, float, str]:
    """三个方向相对状态排序：确认在前，再按阶段（行业距离离散化）、
    行业确认距离升序、最强 ETF RPS15 降序。保证第一行 = 最接近确认的展开项。"""
    stage_rank = {"已确认": 0, "修复观察": 1, "弱势": 2}
    se = s.get("strongest_etf") or {}
    rps = se.get("rps15")
    d_ind = s.get("distance_to_industry_confirm")
    return (
        0 if s.get("confirmed") else 1,
        stage_rank.get(s.get("stage", ""), 9),
        float(d_ind) if d_ind is not None else 1e9,
        -(float(rps) if rps is not None else -1e9),
        s.get("subtheme", ""),
    )


def _closest_subtheme(subtheme_objs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """最接近转强：排序后第一个未确认子主题的轻量摘要（完整内容仍在 subthemes）。"""
    target = next((s for s in subtheme_objs if not s.get("confirmed")), None)
    if target is None:
        return None
    return {
        "subtheme": target.get("subtheme"),
        "subtheme_label": target.get("subtheme_label"),
        "stage": target.get("stage"),
        "strongest_etf": target.get("strongest_etf"),
        "distance_to_industry_confirm": target.get("distance_to_industry_confirm"),
        "distance_to_etf_strength": target.get("distance_to_etf_strength"),
    }


def _theme_summary(subtheme_objs: list[dict[str, Any]], recommended_actions: list[dict[str, Any]]) -> dict[str, Any]:
    """首屏四个数字：推荐行动 / 合格候选 / 确认子主题 / ETF 表现最强。"""
    qualified = sum(
        1 for s in subtheme_objs
        for c in s.get("stock_candidates", []) if c.get("state") == STOCK_STATE_QUALIFIED
    )

    def _etf_rps(s: dict[str, Any]) -> float:
        se = s.get("strongest_etf") or {}
        return float(se["rps15"]) if se.get("rps15") is not None else -1e9

    etf_best = max(subtheme_objs, key=_etf_rps) if subtheme_objs else None
    return {
        "recommended_actions": len(recommended_actions),
        "qualified_candidates": qualified,
        "confirmed_subthemes": f"{sum(1 for s in subtheme_objs if s.get('confirmed'))}/{len(subtheme_objs)}",
        "strongest_etf_subtheme": etf_best.get("subtheme_label") if etf_best else "",
    }


def _collect_recommended_actions(subtheme_objs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """跨子主题汇总「今日行动候选」：推荐 ETF + RECOMMENDED 个股。"""
    out: list[dict[str, Any]] = []
    for sub in subtheme_objs:
        for a in (sub.get("core_etf", []) + sub.get("sub_industry_etf", [])
                  + sub.get("stock_candidates", [])):
            if a.get("recommended"):
                out.append(a)
    return out


def _to_etf_candidate(row: pd.Series, role: str, subtheme: str, reason: str) -> AssetCandidate:
    return AssetCandidate(
        code=str(row["fund_code"]),
        name=str(row.get("fund_name", "")),
        role=role,
        asset_type="etf",
        subtheme=subtheme,
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
