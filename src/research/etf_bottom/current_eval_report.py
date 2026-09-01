"""Current Odds Table HTML 报告（zihao raccoon 视觉体系）。

定位：**纯 renderer**。只消费 current_watch_eval.json / current_odds_table.csv
已有的 stage / evidence_label / odds_assessment / history 字段，绝不重新计算
evidence、stage 或 assessment（这些是 current_eval.py 的研究事实）。

底层：current_odds_table.csv（18 列，机器可读、完整、可审计）。
上层：只展示 8 列，回答「当前哪些 ETF 最值得研究关注」。

8 列 = code / name / RR 阶段 / median_120d / win_rate / payoff / 时间证据 / 最终判断。

排序（固定）：
  strong_observe → watch_structure → position_only → cautious
  → out_of_domain_good → out_of_domain_bad → unreliable
  组内 median_120d DESC（缺失最后），再 fund_code ASC（deterministic）。

集合定义（锁定，展示层不得改口径）：
  in_domain  = stage in {TARGET, IN_DOMAIN_NON_TARGET}
  cross_year = evidence_label == CROSS_YEAR_SUPPORTED
  focus      = odds_assessment in {strong_observe, watch_structure}
  unreliable = stage == UNRELIABLE

YEAR_DEPENDENT 等 evidence 提供轻量可折叠详情（年份/n/median/win），
不占主表列数，仅供审计「哪一年出了问题」。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import STUDY_DIR

# ── 集合定义（锁定） ─────────────────────────────────────────────
IN_DOMAIN_STAGES = ("TARGET", "IN_DOMAIN_NON_TARGET")
CROSS_YEAR_LABEL = "CROSS_YEAR_SUPPORTED"
FOCUS_ODDS = ("strong_observe", "watch_structure")
UNRELIABLE_STAGE = "UNRELIABLE"

_CSS = """
:root {
  --zh-blue:#174A7C; --zh-deep-blue:#06213D; --zh-cyan:#7ECDEB;
  --zh-light-blue:#DDEFF8; --zh-cream:#FFF9EF; --zh-raccoon-gold:#D79A36;
  --zh-brown:#7A4A24; --zh-text:#1F2D3D; --zh-muted:#6B7C8F; --zh-card:#FFFFFF;
}
*{box-sizing:border-box}
body{font-family:-apple-system,'PingFang SC','Hiragino Sans GB',sans-serif;margin:0;background:var(--zh-cream);color:var(--zh-text);line-height:1.6}
.wrap{max-width:1100px;margin:0 auto;padding:24px}
header{border-bottom:3px solid var(--zh-blue);padding:18px 0;margin-bottom:20px}
h1{color:var(--zh-deep-blue);margin:0;font-size:1.7em}
h2{color:var(--zh-blue);margin-top:32px;border-left:4px solid var(--zh-raccoon-gold);padding-left:10px}
.meta{color:var(--zh-muted);font-size:.85em;margin-top:6px}
.card{background:var(--zh-card);border-radius:10px;padding:16px 20px;margin:14px 0;box-shadow:0 1px 3px rgba(6,33,61,.08)}
table{border-collapse:collapse;width:100%;font-size:.88em;margin:10px 0}
th{background:var(--zh-light-blue);color:var(--zh-deep-blue);text-align:left;padding:7px 9px;border:1px solid #cfe0ec}
td{padding:6px 9px;border:1px solid #e3edf5}
tr:nth-child(even) td{background:#f7fbfd}
.pos{color:#b32424;font-weight:600}
.neg{color:#1a7a5a}
.kpi{display:inline-block;background:var(--zh-card);border:1px solid #d5e6f2;border-radius:8px;padding:10px 16px;margin:6px 8px 6px 0;min-width:150px}
.kpi b{display:block;font-size:1.35em;color:var(--zh-blue)}
.kpi span{color:var(--zh-muted);font-size:.8em}
footer{margin-top:40px;padding-top:14px;border-top:1px solid #d5e6f2;color:var(--zh-muted);font-size:.8em}
.summary{background:var(--zh-deep-blue);color:#fff;border-radius:10px;padding:18px 22px;margin:16px 0}
.summary h3{color:var(--zh-cyan);margin-top:0}
.tag{display:inline-block;padding:1px 8px;border-radius:8px;font-size:.75em;margin-right:4px}
.tag-strong{background:#d8f0e0;color:#1a7a5a}.tag-watch{background:#dff1f8;color:#174A7C}
.tag-pos{background:#fff3d6;color:#7a5a00}.tag-caut{background:#f7dddd;color:#b32424}
.tag-unrel{background:#eee;color:#666}
details{background:#f7fbfd;border:1px solid #e3edf5;border-radius:6px;padding:6px 12px;margin-top:4px}
details summary{cursor:pointer;color:var(--zh-blue);font-size:.82em}
details table{font-size:.8em;margin:6px 0}
"""

_STAGE_LABEL = {
    "UNRELIABLE": "数据不可靠", "OUT_OF_DOMAIN": "已离开研究域",
    "IN_DOMAIN_NON_TARGET": "域内·非target", "TARGET": "命中 target",
}
_EVIDENCE_LABEL = {
    "CROSS_YEAR_SUPPORTED": "跨年正赔率", "YEAR_DEPENDENT": "依赖年份",
    "NEGATIVE_HISTORY": "历史负赔率", "INSUFFICIENT_HISTORY": "样本不足",
}
_ODDS_ORDER = ["strong_observe", "watch_structure", "position_only", "cautious",
               "out_of_domain_good", "out_of_domain_bad", "unreliable"]
_ODDS_LABEL = {
    "strong_observe": "★ 最强研究观察", "watch_structure": "🟢 重点等待结构",
    "position_only": "🟡 位置有意义·赔率未知", "cautious": "🔴 谨慎",
    "out_of_domain_good": "⚪ 历史不错·当前非机会", "out_of_domain_bad": "🔴 无吸引力",
    "unreliable": "⚠️ 先解决数据",
}


def _pct(v, nd=1, sign=True):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v*100:+.{nd}f}%" if sign else f"{v*100:.{nd}f}%"


def _num(v, nd=2):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v:.{nd}f}"


def _sorted_etfs(etfs: list[dict]) -> list[dict]:
    """按 odds_assessment 固定序 + median_120d DESC（缺失最后）+ fund_code ASC。"""
    def key(e):
        odds = e.get("odds_assessment", "unreliable")
        odds_rank = _ODDS_ORDER.index(odds) if odds in _ODDS_ORDER else len(_ODDS_ORDER)
        med = e.get("history", {}).get("median_120d")
        med_sort = -med if (med is not None and not (isinstance(med, float) and med != med)) else 1.0
        return (odds_rank, med_sort, e["fund_code"])
    return sorted(etfs, key=key)


def _by_year_detail(e: dict) -> str:
    h = e.get("history", {})
    by = h.get("by_year", {})
    if not by:
        return ""
    rows = "".join(
        f"<tr><td>{y}</td><td>{v['n']}</td><td>{_pct(v['median'], 1)}</td><td>{_pct(v['win'], 0, False)}</td></tr>"
        for y, v in sorted(by.items()))
    return f"""<details><summary>按年份审计（n / median_120d / win_rate）</summary>
<table><thead><tr><th>年份</th><th>n</th><th>120D中位</th><th>胜率</th></tr></thead><tbody>{rows}</tbody></table></details>"""


def _table_rows(etfs: list[dict]) -> str:
    rows = ""
    for e in etfs:
        h = e.get("history", {})
        med = h.get("median_120d")
        med_cls = "pos" if (med is not None and med > 0) else ("neg" if (med is not None and med < 0) else "")
        stage = e.get("stage", "")
        odds = e.get("odds_assessment", "unreliable")
        tag = {"strong_observe": "tag-strong", "watch_structure": "tag-watch",
               "position_only": "tag-pos", "cautious": "tag-caut", "out_of_domain_bad": "tag-caut",
               "out_of_domain_good": "tag-pos", "unreliable": "tag-unrel"}.get(odds, "tag-unrel")
        detail = _by_year_detail(e) if e.get("evidence_label") == "YEAR_DEPENDENT" or e.get("stage") == "TARGET" else ""
        rows += (
            f"<tr><td>{e['fund_code']}</td><td>{e.get('fund_name', '')}</td>"
            f"<td>{_STAGE_LABEL.get(stage, stage)}</td>"
            f"<td class='{med_cls}'>{_pct(med, 1)}</td>"
            f"<td>{_pct(h.get('win_rate'), 0, False)}</td>"
            f"<td>{_num(h.get('payoff_ratio'))}</td>"
            f"<td>{_EVIDENCE_LABEL.get(e.get('evidence_label', ''), e.get('evidence_label', ''))}</td>"
            f"<td><span class='tag {tag}'>{_ODDS_LABEL.get(odds, odds)}</span></td></tr>"
            + (f"<tr><td colspan='8'>{detail}</td></tr>" if detail else "")
        )
    return rows


def render_current_odds(payload: dict, out_path: Path | None = None) -> Path:
    out_path = out_path or (STUDY_DIR / "current_odds_report.html")
    etfs = _sorted_etfs(payload["etfs"])
    total = len(etfs)
    in_domain = sum(1 for e in etfs if e["stage"] in IN_DOMAIN_STAGES)
    cross = sum(1 for e in etfs if e.get("evidence_label") == CROSS_YEAR_LABEL)
    watch = sum(1 for e in etfs if e.get("odds_assessment") in FOCUS_ODDS)
    unrel = sum(1 for e in etfs if e["stage"] == UNRELIABLE_STAGE)

    watch_names = [e["fund_name"] for e in etfs if e.get("odds_assessment") in FOCUS_ODDS]
    headline = (
        f"当前 {total} 只关注 ETF 中，{in_domain} 只处于 Repair-Retest 研究域内，"
        f"{cross} 只历史赔率跨年为正；{len(watch_names)} 只值得重点观察"
        f"（{'、'.join(watch_names) if watch_names else '暂无'}）。"
        f"{unrel} 只因折算污染无法判断当前结构（数据层在 CSV 审计）。"
    )

    legend = "".join(f"<tr><td>{_ODDS_LABEL.get(o, o)}</td></tr>" for o in _ODDS_ORDER)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Current ETF Odds Table</title>
<style>{_CSS}</style></head><body>
<div class="wrap">
<header>
  <h1>Current ETF Odds Table</h1>
  <div class="meta">as_of={payload.get('as_of', '')} · 规则={payload.get('rule_spec_source', '')} · 底层 CSV 18 列机器可读 · {payload.get('generated_at', '')}</div>
</header>

<div class="summary">
  <h3>一句话结论</h3>
  <p>{headline}</p>
</div>

<div class="kpis">
  <div class="kpi"><span>关注 ETF</span><b>{total}</b></div>
  <div class="kpi"><span>研究域内</span><b>{in_domain}</b></div>
  <div class="kpi"><span>跨年正赔率</span><b>{cross}</b></div>
  <div class="kpi"><span>重点观察</span><b>{watch}</b></div>
</div>

<h2>当前赔率表（8 列）</h2>
<div class="card">
<table>
<thead><tr><th>代码</th><th>名称</th><th>RR 阶段</th><th>收益(120D中位)</th><th>胜率</th><th>Payoff</th><th>时间证据</th><th>最终判断</th></tr></thead>
<tbody>{_table_rows(etfs)}</tbody></table>
<p class="meta">收益 = 该 ETF 历史长期底部 entry 后 120D 中位收益；payoff = positive_median / abs(negative_median)，无负样本显示 —。按最终判断排序。</p>
</div>

<h2>决策矩阵图例</h2>
<div class="card"><table><thead><tr><th>最终判断</th></tr></thead><tbody>{legend}</tbody></table></div>

<footer>AKsignal · Lane 2 Research · Current ETF Odds Table · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</footer>
</div></body></html>"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path
