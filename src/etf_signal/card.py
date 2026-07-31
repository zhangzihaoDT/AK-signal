"""
ETF 信息卡片生成

职责：
  - 汇聚趋势、账户、行业增强、外部验证信息，生成 ETF Candidate Card
  - 回答五个问题：
    1. 这是什么资产？
    2. 为什么进入关注池？
    3. 当前账户能否交易？
    4. 趋势背后的驱动力是什么？
    5. 下一步应该去验证什么？
  - 包含验收门控：指标完整性、极端值检测、漏斗状态

P0-E 交付物
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger("etf_signal.card")


# ── 指标验收门控 ───────────────────────────────────────────────

# 指标完整性门控：rps60 至少应有 >10 个唯一值
RPS60_NUNIQUE_MIN = 10
# 全零 return_5d 比例上限
RETURN5D_ZERO_MAX_RATIO = 0.20
# 极端收益阈值：abs(return_20d) > 40% 时标记
EXTREME_RETURN_THRESHOLD = 40.0
# 价格拆分检测：单日价格变化 > 30% 且成交额变化 < 50%
SPLIT_PRICE_CHANGE_MIN = 30.0
SPLIT_AMOUNT_CHANGE_MAX = 0.50


def validate_indicators(metrics: pd.DataFrame) -> list[str]:
    """指标验收门控。返回所有未通过的检查项列表。"""
    gates: list[str] = []

    rps60_nunique = metrics["rps60"].nunique(dropna=True) if "rps60" in metrics.columns else 0
    if rps60_nunique <= RPS60_NUNIQUE_MIN:
        gates.append(f"rps60_nunique={rps60_nunique} ≤ {RPS60_NUNIQUE_MIN}")

    if "return_5d" in metrics.columns:
        zero_ratio = (metrics["return_5d"] == 0).mean()
        if zero_ratio > RETURN5D_ZERO_MAX_RATIO:
            gates.append(f"return_5d_zero_ratio={zero_ratio:.1%} > {RETURN5D_ZERO_MAX_RATIO:.0%}")

    return gates


def detect_risks(
    fund_code: str,
    raw_dir: Path,
) -> list[str]:
    """从原始日行情检测极端值和异常。"""
    flags: list[str] = []
    path = raw_dir / f"{fund_code}.parquet"
    if not path.exists():
        return flags

    try:
        df = pd.read_parquet(path)
        if df.empty or "close" not in df.columns or "amount" not in df.columns:
            return flags

        closes = df["close"].values
        amounts = df["amount"].values

        # 极端收益
        if len(closes) >= 20:
            ret_20d = (closes[-1] / closes[-20] - 1) * 100
            if abs(ret_20d) > EXTREME_RETURN_THRESHOLD:
                flags.append(f"extreme_return_20d={ret_20d:+.1f}%")

        # 价格拆分检测
        if len(closes) >= 2:
            daily_changes = abs(closes[1:] / closes[:-1] - 1) * 100
            max_day_change = daily_changes[-20:].max() if len(daily_changes) >= 20 else daily_changes.max()
            if max_day_change > SPLIT_PRICE_CHANGE_MIN:
                # 检查同日成交额变化
                idx = len(daily_changes) - daily_changes[-20:].argmax() - 1 if len(daily_changes) >= 20 else daily_changes.argmax()
                if idx > 0 and idx < len(amounts):
                    amount_ratio = amounts[idx] / amounts[idx - 1] if amounts[idx - 1] > 0 else 1
                    if amount_ratio < SPLIT_AMOUNT_CHANGE_MAX or amount_ratio > (1 / SPLIT_AMOUNT_CHANGE_MAX):
                        pass  # 成交额也剧变，可能是真实波动
                    else:
                        flags.append(f"possible_split: 单日{max_day_change:.1f}% 成交额变化{amount_ratio:.2f}x")

        # 缺失近期数据
        if len(df) >= 5:
            latest_date = df["date"].max()
            if hasattr(latest_date, "date") and (date.today() - latest_date.date()).days > 7:
                flags.append(f"stale_data: 最新日期{latest_date.date()}")

    except Exception as e:
        logger.debug("risk detection failed for %s: %s", fund_code, e)

    return flags


# ── 卡片数据类 ─────────────────────────────────────────────────

@dataclass
class ETFBaseInfo:
    name: str
    code: str
    exchange: str
    asset_class: str
    etf_type: str
    exposure: str
    tracking_index: str
    broker: str = "国金证券"
    account_tradable: bool = False


@dataclass
class TrendInfo:
    trend_state: str
    rps15: float
    rps60: float
    rps20: float = 50.0
    return_5d: float = 0.0
    return_20d: float = 0.0
    trend_change: str = "平稳"
    amount_change: str = "—"


@dataclass
class AccountScreenInfo:
    account_tradable: bool
    account_status: str = "UNVERIFIED"
    trading_status: str = "正常"
    liquidity_gate: str = "—"
    size_gate: str = "—"
    history_gate: str = "—"
    premium_risk: str = "—"


@dataclass
class IndustryEnrichment:
    sw_industry: str
    sw_rps15: float
    participation_rate: float
    drive_pattern: str
    top1_contrib: float
    etf_consistency: str


@dataclass
class RiskFlags:
    flags: list[str] = field(default_factory=list)


@dataclass
class ValidationAction:
    next_step: str = "前往趋势动物搜索该 ETF 或对应资产，检查其趋势状态是否同样为右侧、加速或强势"


@dataclass
class ETFCandidateCard:
    base_info: ETFBaseInfo
    trend: TrendInfo
    account_screen: AccountScreenInfo
    enrichment: IndustryEnrichment | None = None
    risks: RiskFlags = field(default_factory=RiskFlags)
    validation: ValidationAction = field(default_factory=ValidationAction)
    card_status: str = "complete"  # complete | incomplete | flagged
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_text(self) -> str:
        status_tag = {"complete": "", "incomplete": " [字段不完整]", "flagged": " [需人工复核]"}.get(self.card_status, "")
        lines = [
            f"═ {self.base_info.name}（{self.base_info.code}）{status_tag}",
            "",
            f"  资产类别：{self.base_info.asset_class or '—'}  |  ETF 类型：{self.base_info.etf_type or '—'}",
            f"  暴露：{self.base_info.exposure or '—'}",
            f"  跟踪指数：{self.base_info.tracking_index or '—'}",
            f"  交易账户：{self.base_info.broker}  |  账户状态：{self.account_screen.account_status}",
            "",
            "── 趋势信息 ──────────────────────────────",
            f"  趋势状态：{self.trend.trend_state}",
            f"  RPS15：{self.trend.rps15:.1f}  |  RPS20：{self.trend.rps20:.1f}  |  RPS60：{self.trend.rps60:.1f}",
            f"  5 日收益：{self.trend.return_5d:+.1f}%  |  20 日收益：{self.trend.return_20d:+.1f}%",
            f"  趋势变化：{self.trend.trend_change}  |  成交额变化：{self.trend.amount_change}",
            "",
            "── 账户筛选 ──────────────────────────────",
            f"  国金可交易：{'是' if self.account_screen.account_tradable else '否'}",
            f"  账户状态：{self.account_screen.account_status}",
        ]

        if self.risks.flags:
            lines += ["", "── 风险提示 ──────────────────────────────"]
            for flag in self.risks.flags:
                lines.append(f"  ⚠ {flag}")

        lines += [
            "",
            "── 验证动作 ──────────────────────────────",
            f"  {self.validation.next_step}",
            "",
            f"  卡片状态：{self.card_status}  生成时间：{self.generated_at}",
            "═" * 50,
        ]
        return "\n".join(lines)


def build_card(
    master_row: pd.Series,
    trend_info: TrendInfo,
    account_info: AccountScreenInfo,
    enrichment: IndustryEnrichment | None = None,
    risk_flags: list[str] | None = None,
    card_status: str = "complete",
) -> ETFCandidateCard:
    return ETFCandidateCard(
        base_info=ETFBaseInfo(
            name=str(master_row.get("fund_name", "")),
            code=str(master_row.get("fund_code", "")),
            exchange=str(master_row.get("exchange", "")),
            asset_class=str(master_row.get("primary_asset_class", str(master_row.get("asset_bucket", "")))),
            etf_type=str(master_row.get("exposure_type", "")),
            exposure=str(master_row.get("exposure_name", "")),
            tracking_index=str(master_row.get("tracking_index", "")),
            account_tradable=account_info.account_tradable,
        ),
        trend=trend_info,
        account_screen=account_info,
        enrichment=enrichment,
        risks=RiskFlags(flags=risk_flags or []),
        card_status=card_status,
        generated_at=str(date.today()),
    )


def save_cards(cards: list[ETFCandidateCard], output_dir: Path, date_str: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "date": date_str,
        "count": len(cards),
        "cards": [c.to_dict() for c in cards],
    }
    path = output_dir / f"candidate_cards_{date_str}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("saved %d cards -> %s", len(cards), path)
    return path


def print_card_summary(cards: list[ETFCandidateCard]) -> None:
    if not cards:
        print("  无候选")
        return
    print(f"\n{'ETF':<20} {'状态':<16} {'RPS15':>6} {'账户':>6} {'卡片':>8}")
    print("-" * 60)
    for c in cards:
        print(
            f"{c.base_info.name:<20} {c.trend.trend_state:<16} "
            f"{c.trend.rps15:>6.1f} "
            f"{'可' if c.account_screen.account_tradable else '—':>4} "
            f"{c.card_status:>8}"
        )
    print()
