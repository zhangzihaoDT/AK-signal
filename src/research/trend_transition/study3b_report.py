"""Study 3B · HTML 报告（zihao raccoon 视觉体系）。

纯 renderer：只消费 study3b_summary.json 等结果文件，不重算/不训练模型。
首页必须回答 6 个问题（§26）并以 verdict 收尾。

产物：outputs/research/trend_transition/study3b_report.html
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import STUDY_DIR

_CSS = """
:root {
  --zh-blue:#174A7C; --zh-deep-blue:#06213D; --zh-cyan:#7ECDEB;
  --zh-light-blue:#DDEFF8; --zh-cream:#FFF9EF; --zh-raccoon-gold:#D79A36;
  --zh-brown:#7A4A24; --zh-text:#1F2D3D; --zh-muted:#6B7C8F; --zh-card:#FFFFFF;
}
*{box-sizing:border-box}
body{font-family:-apple-system,'PingFang SC','Hiragino Sans GB',sans-serif;margin:0;background:var(--zh-cream);color:var(--zh-text);line-height:1.6}
.wrap{max-width:1120px;margin:0 auto;padding:24px}
header{border-bottom:3px solid var(--zh-blue);padding:18px 0;margin-bottom:20px}
h1{color:var(--zh-deep-blue);margin:0;font-size:1.6em}
h2{color:var(--zh-blue);margin-top:30px;border-left:4px solid var(--zh-raccoon-gold);padding-left:10px}
h3{color:var(--zh-blue)}
.meta{color:var(--zh-muted);font-size:.85em;margin-top:6px}
.card{background:var(--zh-card);border-radius:10px;padding:16px 20px;margin:14px 0;box-shadow:0 1px 3px rgba(6,33,61,.08)}
table{border-collapse:collapse;width:100%;font-size:.9em;margin:10px 0}
th{background:var(--zh-light-blue);color:var(--zh-deep-blue);text-align:left;padding:7px 9px;border:1px solid #cfe0ec}
td{padding:6px 9px;border:1px solid #e3edf5}
tr:nth-child(even) td{background:#f7fbfd}
.pos{color:#b32424;font-weight:600}
.neg{color:#1a7a5a}
.hl{background:#fff6e6!important;font-weight:600}
.kpi{display:inline-block;background:var(--zh-card);border:1px solid #d5e6f2;border-radius:8px;padding:10px 16px;margin:6px 8px 6px 0;min-width:150px}
.kpi b{display:block;font-size:1.3em;color:var(--zh-blue)}
.kpi span{color:var(--zh-muted);font-size:.8em}
footer{margin-top:40px;padding-top:14px;border-top:1px solid #d5e6f2;color:var(--zh-muted);font-size:.8em}
.summary{background:var(--zh-deep-blue);color:#fff;border-radius:10px;padding:18px 22px;margin:16px 0}
.summary h3{color:var(--zh-cyan);margin-top:0}
.summary .s{color:#cfe6f5;font-size:.95em;margin-top:8px}
.verdict{font-size:1.15em;font-weight:700;margin-top:10px;padding:10px 14px;border-radius:8px;display:inline-block}
.verdict.pass{background:#1a7a5a;color:#fff}
.verdict.fail{background:#b32424;color:#fff}
.pill{display:inline-block;padding:2px 10px;border-radius:20px;font-size:.78em;margin-right:6px}
.pill.pass{background:#e2f4e9;color:#1a7a5a;border:1px solid #bfe6cd}
.pill.fail{background:#fde4e4;color:#b32424;border:1px solid #f2c3c3}
details{margin:8px 0;background:#fafcfe;border:1px solid #d5e6f2;border-radius:8px;padding:8px 12px}
summary{cursor:pointer;font-weight:600;color:var(--zh-blue)}
"""

_ETYPE_LABEL = {"broad": "宽基", "industry": "行业", "theme": "主题",
                "dividend": "红利", "cross_border": "跨境"}
_FAM_LABEL = {"F1_position": "F1 位置切换", "F2_rps": "F2 相对强弱",
              "F3_drawdown": "F3 回撤变浅", "F4_bottom_history": "F4 底部历史",
              "F5_market": "F5 市场/广度"}


def _fmt(v: Any, nd: int = 3) -> str:
    if v is None or (isinstance(v, float) and not (v == v)):
        return "—"
    try:
        f = float(v)
        if not (f == f):
            return "—"
        return f"{f:.{nd}f}"
    except (TypeError, ValueError):
        return str(v)


def _pct(v: Any) -> str:
    if v is None or (isinstance(v, float) and not (v == v)):
        return "—"
    return f"{float(v) * 100:.1f}%"


def _pill(ok: bool) -> str:
    return f'<span class="pill {"pass" if ok else "fail"}">{"PASS" if ok else "FAIL"}</span>'


def _load_summary() -> dict[str, Any]:
    p = STUDY_DIR / "study3b_summary.json"
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _q1_base_rate(s: dict[str, Any]) -> str:
    br = s.get("base_rate")
    n = s.get("n_trainable")
    return (f'<div class="kpi"><b>{_pct(br)}</b><span>ESCAPE_120D base rate</span></div>'
            f'<div class="kpi"><b>{n}</b><span>可训练事件（120d 非右截断）</span></div>'
            f'<p>其中 2022=33.5% · 2023=3.3% · 2024=52.9% · 2025=80.5% · 2026=0% —— '
            f'escape 基础率高度 regime-dependent（3A 已证 924 结构断点）。</p>')


def _q2_market_only(s: dict[str, Any]) -> str:
    mc = s.get("model_comparison", {})
    m = mc.get("market_only", {})
    full = mc.get("full_logistic", {})
    indiv = mc.get("individual_only", {})
    return (
        f'<div class="kpi"><b>{_fmt(m.get("pooled_auc"))}</b><span>Market Only OOS AUC（event）</span></div>'
        f'<div class="kpi"><b>{_fmt(m.get("pooled_dw_auc"))}</b><span>Market Only OOS AUC（date）</span></div>'
        f'<p>仅用市场/广度/类型特征，walk-forward OOS 判别力 <b>低于随机</b>：说明'
        f'“知道市场处于强 regime”本身不足以预测单只 ETF 的 transition（§12 Baseline 1）。</p>'
        f'<p>对照：Individual Only={_fmt(indiv.get("pooled_auc"))} · Full={_fmt(full.get("pooled_auc"))}。</p>'
    )


def _q3_increment(s: dict[str, Any]) -> str:
    mc = s.get("model_comparison", {})
    full = mc.get("full_logistic", {})
    market = mc.get("market_only", {})
    indiv = mc.get("individual_only", {})
    fu = full.get("pooled_auc")
    mu = market.get("pooled_auc")
    iu = indiv.get("pooled_auc")
    delta = (fu - mu) if (fu is not None and mu is not None) else None
    verdict = "个体 transition 特征（position velocity / RPS）携带弱但真实的信息（Individual 0.56 > Market 0.40），但加入市场特征后 Full 被 regime-proxy 拖回随机（0.41）——个体的 OOS 增量不稳定。"
    return (
        f'<div class="kpi"><b>{_fmt(delta)}</b><span>Full − Market Only（OOS AUC Δ）</span></div>'
        f'<div class="kpi"><b>{_fmt(fu)}</b><span>Full</span></div>'
        f'<div class="kpi"><b>{_fmt(mu)}</b><span>Market Only</span></div>'
        f'<p>{verdict}</p>'
    )


def _q4_top20(s: dict[str, Any]) -> str:
    mc = s.get("model_comparison", {})
    full = mc.get("full_logistic", {})
    # 从 walkforward.json 取 full logistic 的 Top20% lift（与 gate 同源）
    lift = _lift_from_gate(s)
    return (
        f'<div class="kpi"><b>{_fmt(lift.get("lift"))}</b><span>Top20% lift</span></div>'
        f'<div class="kpi"><b>{_pct(lift.get("selected_rate"))}</b><span>Top20% 实际 ESCAPE rate</span></div>'
        f'<div class="kpi"><b>{_pct(lift.get("base_rate"))}</b><span>base rate</span></div>'
        f'<p>Full logistic 的 Top20% 分位样本实际 escape 率未显著高于 base（lift={_fmt(lift.get("lift"))}），'
        f'decision lift 不成立（§14/§19 B3）。</p>'
    )


def _lift_from_gate(s: dict[str, Any]) -> dict[str, Any]:
    checks = s.get("pass_gate", {}).get("checks", {})
    detail = checks.get("B3", {}).get("detail", "")
    # 从 detail 解析不够稳健；改从 walkforward.json pooled 直接取
    wf = STUDY_DIR / "study3b_walkforward.json"
    try:
        with open(wf, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("pooled", {}).get("event_weighted", {}).get("lift_20", {})
    except Exception:  # noqa: BLE001
        return {}


def _q5_oos(s: dict[str, Any]) -> str:
    wf = STUDY_DIR / "study3b_walkforward.json"
    try:
        with open(wf, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:  # noqa: BLE001
        return "<p>walkforward.json 缺失。</p>"
    rows = ""
    for f in data.get("folds", []):
        e = f["metrics"]["event_weighted"]
        dw = f["metrics"].get("date_weighted") or {}
        rows += (f"<tr><td>{f['val_year']}</td><td>{f['n_train']}</td><td>{f['n_val']}</td>"
                 f"<td>{_pct(e['base_rate'])}</td><td>{_fmt(e['auc'])}</td>"
                 f"<td>{_fmt(dw.get('auc'))}</td><td>{_fmt(e['brier'])}</td></tr>")
    return (
        f'<table><tr><th>OOS 年</th><th>train n</th><th>val n</th><th>base rate</th>'
        f'<th>event AUC</th><th>date AUC</th><th>Brier</th></tr>{rows}</table>'
        f'<p>2025 (n=41) / 2026 (n=3) OOS 极稀疏；2024 独占 pooled（跨 924 断点），'
        f'walk-forward OOS 判别力不稳定 → <b>不能单独用 2025/2026 断言成立</b>（§11）。</p>'
    )


def _q6_mechanisms(s: dict[str, Any]) -> str:
    top = s.get("discovery_top", [])
    rows = ""
    for r in top:
        rows += (f"<tr><td>{r['feature']}</td><td>{_fmt(r['spearman'])}</td>"
                 f"<td>{_fmt(r['median_diff'])}</td><td>{r['n']}</td></tr>")
    return (
        f'<table><tr><th>特征</th><th>Spearman</th><th>escape−retest 中位差</th><th>n</th></tr>{rows}</table>'
        f'<p>最有信息的是 <b>Position velocity</b>（delta_pos60/120 的 5-20 日变化，spearman≈0.32-0.34）'
        f'与 <b>RPS</b>（rps60 进入 score）；Drawdown shallowing / Bottom history 增量弱。'
        f'但方向跨 regime 不稳（2023 年几乎全 retest），ablation 仅 F2_rps 有正增量 → 单一 mechanism，'
        f'不满足 §19 B5。</p>'
    )


def _pass_gate_table(s: dict[str, Any]) -> str:
    checks = s.get("pass_gate", {}).get("checks", {})
    rows = "".join(
        f"<tr><td>{k}</td><td>{v['name']}</td><td>{_pill(v['ok'])}</td><td>{v.get('detail','')}</td></tr>"
        for k, v in checks.items()
    )
    return f'<table><tr><th>ID</th><th>检查项</th><th>状态</th><th>依据</th></tr>{rows}</table>'


def _discovery_table(s: dict[str, Any]) -> str:
    feats = s.get("discovery", {}).get("features", {})
    ranked = sorted(
        [(f, v.get("spearman") or 0) for f, v in feats.items() if v.get("spearman") is not None],
        key=lambda kv: abs(kv[1]), reverse=True,
    )[:15]
    rows = ""
    for f, _ in ranked:
        v = feats[f]
        quint = ""
        for q in v.get("quintiles", []):
            quint += f" {q['label']}:{_pct(q['escape_rate'])}"
        rows += (f"<tr><td>{f}</td><td>{_fmt(v.get('spearman'))}</td><td>{_fmt(v.get('median_diff'))}</td>"
                 f"<td>{_fmt(v.get('cohens_d'))}</td><td>{v.get('n')}</td><td style='font-size:.82em'>{quint}</td></tr>")
    return (
        f'<table><tr><th>特征</th><th>Spearman</th><th>中位差</th><th>Cohen d</th><th>n</th>'
        f'<th>Q1→Q5 escape rate</th></tr>{rows}</table>'
    )


def _regime_table(s: dict[str, Any]) -> str:
    seg = s.get("regime", {}).get("segments", {})
    rows = ""
    for k, v in seg.items():
        rows += f"<tr><td>{k}</td><td>{v.get('n')}</td><td>{_pct(v.get('base_rate'))}</td></tr>"
    return f'<table><tr><th>regime 段</th><th>n</th><th>base rate</th></tr>{rows}</table>'


def _ablation_table(s: dict[str, Any]) -> str:
    drops = s.get("ablation", {}).get("drops", {})
    full = s.get("ablation", {}).get("full_auc")
    rows = ""
    for fam, v in drops.items():
        drop = v.get("drop")
        cell = (f'<span class="{"pos" if (drop or 0) > 0 else "neg"}">{_fmt(drop)}</span>'
                if drop is not None else "—")
        rows += (f"<tr><td>{_FAM_LABEL.get(fam, fam)}</td><td>{len(v.get('features', []))}</td>"
                 f"<td>{_fmt(v.get('auc'))}</td><td>{cell}</td></tr>")
    return (f'<p>Full pooled OOS AUC = <b>{_fmt(full)}</b>。drop>0 = 删除该 family 使 AUC 下降（有增量）。</p>'
            f'<table><tr><th>删除 family</th><th>#特征</th><th>删除后 AUC</th><th>Δ（Full−删除）</th></tr>{rows}</table>')


def _robustness_table(s: dict[str, Any]) -> str:
    r = s.get("robustness", {})
    html = ""
    for sec, label in (("persistence", "R1 · persistence"), ("horizon", "R2 · horizon"),
                       ("etf_type", "R4 · ETF 类型")):
        rows = ""
        for k, v in r.get(sec, {}).items():
            if isinstance(v, dict) and v.get("status") == "failed":
                rows += f"<tr><td>{k}</td><td colspan=3>failed: {v.get('error','')[:60]}</td></tr>"
                continue
            if not isinstance(v, dict):
                continue
            rows += (f"<tr><td>{k}</td><td>{v.get('n')}</td>"
                     f"<td>{_pct(v.get('base_rate'))}</td><td>{_fmt(v.get('pooled_auc'))}</td></tr>")
        html += (f"<h3>{label}</h3>"
                 f"<table><tr><th>口径</th><th>n</th><th>base rate</th><th>pooled OOS AUC</th></tr>{rows}</table>")
    return html


def _verdict_html(s: dict[str, Any]) -> str:
    v = s.get("pass_gate", {}).get("verdict", "")
    ok = str(v).startswith("PASS")
    cls = "pass" if ok else "fail"
    return f'<div class="verdict {cls}">{v}</div>'


def render(summary: dict[str, Any] | None = None, out_path: Path | None = None) -> Path:
    s = summary if summary is not None else _load_summary()
    generated = s.get("generated_at", "unknown")
    gate = s.get("pass_gate", {})
    checks = gate.get("checks", {})

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><style>{_CSS}</style>
<title>Study 3B · 预测 Trend Transition</title></head><body><div class="wrap">
<header><h1>Lane 3 · Study 3B — 预测 Trend Transition（first_exit 时点）</h1>
<div class="meta">生成于 {generated} · persistence={s.get('persistence')} · target=ESCAPE_{s.get('horizon')}D · n_trainable={s.get('n_trainable')} · score_features={s.get('score_features')}</div></header>

<div class="summary">
<h3>结论</h3>
{_verdict_html(s)}
<div class="s">B1-B5 逐项见下。核心：Market Only ≈ Full（{_fmt(s.get('model_comparison',{}).get('market_only',{}).get('pooled_auc'))} vs {_fmt(s.get('model_comparison',{}).get('full_logistic',{}).get('pooled_auc'))}），
Individual Only 略强（{_fmt(s.get('model_comparison',{}).get('individual_only',{}).get('pooled_auc'))}）但叠加市场特征后 OOS 判别力回落——Lane 3 尚无独立 ETF-level selection value，不进入 3C。</div>
</div>

<h2>0. 首页必须回答的 6 个问题</h2>
<div class="card">
<h3>Q1 · ESCAPE_120D base rate 是多少？</h3>{_q1_base_rate(s)}
<h3>Q2 · Market Only 能预测到什么程度？</h3>{_q2_market_only(s)}
<h3>Q3 · 加入 Individual Transition features 后提升多少？</h3>{_q3_increment(s)}
<h3>Q4 · Top 20% score 的实际 Escape Rate 是多少？</h3>{_q4_top20(s)}
<h3>Q5 · 2025 / 2026 OOS 是否仍成立？</h3>{_q5_oos(s)}
<h3>Q6 · 最重要的 2–3 个 Trend Transition mechanism 是什么？</h3>{_q6_mechanisms(s)}
</div>

<h2>1. PASS Gate（B1-B5）</h2>
<div class="card">{_pass_gate_table(s)}</div>

<h2>2. 单变量 Discovery（§9）</h2>
<div class="card">{_discovery_table(s)}</div>

<h2>3. Regime Robustness（§10，诊断不进模型）</h2>
<div class="card">{_regime_table(s)}</div>

<h2>4. Model Comparison（§15，walk-forward pooled OOS）</h2>
<div class="card">
<table><tr><th>模型</th><th>特征</th><th>event AUC</th><th>date AUC</th><th>OOS n</th></tr>
{_model_compare_rows(s)}
</table>
</div>

<h2>5. Ablation（§16）</h2>
<div class="card">{_ablation_table(s)}</div>

<h2>6. Robustness（§18）</h2>
<div class="card">{_robustness_table(s)}</div>

<details><summary>口径与限定</summary><p>
Study 3B 只消费 3A 事实层（trajectories + v1_signal_daily + raw 价格）。特征全部 as-of（≤ first_exit_date），
无 look-ahead。Expanding walk-forward（train=早于 val 年），preprocess 只在 train fit。禁止 year/month/date/post_924 进模型。
2025/2026 OOS 样本极稀疏（n=41/3），结论以 pooled（2023/2024 主导）+ 逐 fold 为准。
</p></details>

<footer>Lane 3 · Study 3B · 手写 logistic/tree/score（无 sklearn）· event-weighted + date-weighted 双口径 · 3C 仅当 B1-B5 全过才开启。</footer>
</div></body></html>"""
    path = out_path or (STUDY_DIR / "study3b_report.html")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path


def _model_compare_rows(s: dict[str, Any]) -> str:
    mc = s.get("model_comparison", {})
    labels = {
        "market_only": "Market Only（F5）",
        "position_only": "Position Only（F1）",
        "individual_only": "Individual Only（F1-F4）",
        "full": "Full（F1-F5）",
        "full_logistic": "Full logistic（主模型）",
        "simple_score": "Simple Score（M2）",
    }
    rows = ""
    for k, label in labels.items():
        v = mc.get(k, {})
        rows += (f"<tr><td>{label}</td><td>{k}</td><td>{_fmt(v.get('pooled_auc'))}</td>"
                 f"<td>{_fmt(v.get('pooled_dw_auc'))}</td><td>{v.get('n_oos') or '—'}</td></tr>")
    return rows
