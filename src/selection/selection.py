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
            # 上涨结构（对已穿透行业取中位）
            part = sub["participation_rate"].dropna()
            hhi = sub["hhi"].dropna()
            top3 = sub["top3_share"].dropna()
            entry.update({
                "confirmed": n_observe > 0,
                "n_strong": n_strong,
                "n_observe": n_observe,
                "median_rps15": _round(rps.median()) if not rps.empty else None,
                "median_participation": _round(part.median()) if not part.empty else None,
                "median_hhi": _round(hhi.median()) if not hhi.empty else None,
                "median_top3_share": _round(top3.median()) if not top3.empty else None,
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

    # 趋势门控
    etf = etf[etf["trend_state"].isin(trend_gates)].copy()
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


# ── 4. 个股候选 ────────────────────────────────────────────────────

def select_stock_candidates(
    universe_items: list[Any],
    subtheme: str,
    trend_df: pd.DataFrame,
) -> tuple[list[AssetCandidate], list[AssetCandidate], list[AssetCandidate]]:
    """从 universe 分层池选择该子主题的 leader / high_beta / equipment。

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

        cand = AssetCandidate(
            code=item.asset.symbol,
            name=item.asset.name,
            role=role,
            asset_type="stock",
            subtheme=subtheme,
            rps15=_round(_tv("relative_strength_20d")),
            score_trend=_round(_tv("score_trend")),
            trend_status=str(_tv("watch_level", "")),
            tradable=True,  # 黑名单机制：未确认不可交易即默认可交易
            reason=str(_tv("action", "")),
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
        # ETF 候选（动态从 Layer① 选）
        etf_pool = select_etf_candidates(rotation_df, account_df, master_df, key)
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

        # 个股候选
        leaders, high_beta, equipment = select_stock_candidates(universe_items, key, trend_df)

        # 表达决策
        expr = decide_expression(meta)

        sub_obj = {
            "subtheme": key,
            "subtheme_label": meta["label"],
            "confirmed": meta["confirmed"],
            "confirmation_reason": meta["reason"],
            "metrics": {k: v for k, v in meta.items()
                        if k not in ("label", "industries", "confirmed", "reason")},
            "expression": expr["expression"],
            "expression_label": expr["expression_label"],
            "expression_reason": expr["expression_reason"],
            "core_etf": [c.to_dict() for c in core_etf],
            "sub_industry_etf": [c.to_dict() for c in sub_industry_etf],
            "leaders": [c.to_dict() for c in leaders],
            "high_beta": [c.to_dict() for c in high_beta],
            "equipment": [c.to_dict() for c in equipment],
        }
        theme_obj["subthemes"].append(sub_obj)

    logger.info("candidates built: %d subthemes", len(theme_obj["subthemes"]))
    return theme_obj


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
        trend_status=str(row.get("trend_state", "")),
        rank_change_5d=_round(row.get("rank_change_5d")),
        liquidity=_round(row.get("amount")),
        tradable=bool(row.get("account_tradable", False)),
        selection_score=_round(row.get("selection_score")),
        reason=reason,
    )


# ── 持久化 ─────────────────────────────────────────────────────────

def save_candidates_json(candidates: dict[str, Any], output_dir: Path, date_str: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"tradable_candidates_{date_str}.json"
    payload = {"date": date_str, "layer3": candidates}
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
