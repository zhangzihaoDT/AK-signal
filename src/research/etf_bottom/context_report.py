"""Study 2C Current Episode Context Matching HTML 报告（zihao raccoon 视觉体系）。

结构：
  一句话结论 + 成功/失败区分表（核心假设验证）
  每个当前 episode 的 Top3 历史匹配（等权 + sensitivity 权重）
  匹配一致性与关键差异
  口径与限定（no-look-ahead / scaler / 五维权重）
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from . import STUDY_DIR
from .context import CONTINUOUS_FEATURES, DIM_GROUP_ORDER

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
h3{color:var(--zh-blue)}
.meta{color:var(--zh-muted);font-size:.85em;margin-top:6px}
.card{background:var(--zh-card);border-radius:10px;padding:16px 20px;margin:14px 0;box-shadow:0 1px 3px rgba(6,33,61,.08)}
table{border-collapse:collapse;width:100%;font-size:.86em;margin:10px 0}
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
.hl{background:#fff6e6!important;font-weight:600}
"""

_DIM_LABEL = {
    "market": "市场环境", "industry_relative": "产业相对强弱",
    "bottom_depth": "底部深度", "synchronization": "同步度", "recovery": "修复状态",
}
_FEATURE_LABEL = {
    "market_ret_60d": "HS300 60D", "market_ret_120d": "HS300 120D", "market_breadth_60d": "全市场 breadth 60D",
    "industry_excess_60d": "产业超额 60D", "industry_excess_120d": "产业超额 120D",
    "pos60": "60D 位置", "pos120": "120D 位置", "pos360": "360D 位置",
    "distance_360": "距360D低", "dd60": "60D回撤",
    "initial_participation_ratio": "初始参与率", "entries_last_20d": "20日新入底",
    "deep_ratio": "深底占比", "recovering_ratio": "修复占比",
}


def _pct(v, nd=1, sign=True):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v*100:+.{nd}f}%" if sign else f"{v*100:.{nd}f}%"


def _num(v):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v:.2f}"


def _feature_table(feature_stats: dict) -> str:
    rows = ""
    for dim in DIM_GROUP_ORDER:
        feats = [f for f in CONTINUOUS_FEATURES[dim]]
        for f in feats:
            st = feature_stats.get(f, {})
            if st.get("success_mean") is None and st.get("fail_mean") is None:
                continue
            diff = (st.get("success_mean") or 0) - (st.get("fail_mean") or 0)
            hl = " class='hl'" if abs(diff) > 0.03 else ""
            rows += (
                f"<tr{hl}><td>{_FEATURE_LABEL.get(f, f)}</td><td>{_DIM_LABEL.get(dim, dim)}</td>"
                f"<td>{_num(st.get('success_mean'))}</td><td>{_num(st.get('fail_mean'))}</td>"
                f"<td>{st.get('success_n', 0)}/{st.get('fail_n', 0)}</td>"
                f"<td>{_num(st.get('strong_success_mean'))}</td></tr>"
            )
    return f"""<table>
<thead><tr><th>Context</th><th>维度</th><th>成功底部均值</th><th>失败底部均值</th><th>n成功/n失败</th><th>强成功均值(&gt;10%)</th></tr></thead>
<tbody>{rows}</tbody></table>"""


def _match_table(matches: dict) -> str:
    sections = ""
    for eid, m in matches.items():
        t3 = m["top3_equal"]
        ts = m["top3_sensitivity"]
        rows = ""
        for t in t3:
            up = "↑上涨" if t["episode_up"] else "↓下跌"
            sens = next((x for x in ts if x["episode_id"] == t["episode_id"]), {})
            rows += (
                f"<tr><td>{t['episode_id']}</td><td>{_pct(t['ret120_median'])}</td><td>{up}</td>"
                f"<td>{t['distance_equal']:.3f}</td><td>{t.get('distance_sensitivity', '—')}</td></tr>"
            )
        same = [x["episode_id"] for x in t3] == [x["episode_id"] for x in ts[:3]]
        note = "<span class='flag-ok'>两套权重 Top3 一致</span>" if same else "<span class='flag-bad'>两套权重 Top3 不一致</span>"
        sections += f"""<h3>{m['cluster']} · 起始 {m['start']}</h3>
<table><thead><tr><th>相似历史 episode</th><th>ret120</th><th>结果</th><th>距离(等权)</th><th>距离(sensitivity)</th></tr></thead>
<tbody>{rows}</tbody></table><p class="meta">{note}</p>"""
    return sections or "<p>无匹配</p>"


def render_context(payload: dict, out_path: Path | None = None) -> Path:
    out_path = out_path or (STUDY_DIR / "context_matching.html")
    feature_stats = payload["feature_stats"]
    matches = payload["matches"]
    labels = payload["label_summary"]

    # 核心结论：最强的区分维度
    ie60 = feature_stats.get("industry_excess_60d", {})
    pos120 = feature_stats.get("pos120", {})
    mkt = feature_stats.get("market_ret_60d", {})
    dd = feature_stats.get("dd60", {})
    headline = (
        f"成功与失败底部同样'低'（dd60 成功 {_pct(dd.get('success_mean'))} vs 失败 {_pct(dd.get('fail_mean'))}、几乎无差），"
        f"但成功组产业相对超额显著更负（{_pct(ie60.get('success_mean'))} vs 失败 {_pct(ie60.get('fail_mean'))}）"
        f"且 120D 位置更高（{_num(pos120.get('success_mean'))} vs {_num(pos120.get('fail_mean'))}）——"
        f"区分好坏底的更可能是<b>产业独跌超跌 + 已开始修复</b>，而不是跌得多深。"
    )
    own = labels.get("success_mode", {}).get("own_decline", {})
    foll = labels.get("success_mode", {}).get("following_market", {})
    mode_note = ""
    if own and foll:
        mode_note = (f"产业独跌(own_decline)成功率 {_pct(own.get('success_rate'), 0, False)} "
                     f"vs 随大盘跌(following_market) {_pct(foll.get('success_rate'), 0, False)}。")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Study 2C · Current Episode Context Matching</title>
<style>{_CSS}</style></head><body>
<div class="wrap">
<header>
  <h1>Study 2C · Current Episode Context Matching</h1>
  <div class="meta">{payload['n_historical']} 历史 episode + {payload['n_current']} 当前 episode · context 在 episode.start 当日可观察（no look-ahead）· scaler 只 fit 历史 · 五维等权为主 · {payload.get('generated_at', '')}</div>
</header>

<div class="summary">
  <h3>一句话结论</h3>
  <p>{headline}</p>
  <p>{mode_note}</p>
</div>

<div class="kpis">
  <div class="kpi"><span>历史 episode</span><b>{payload['n_historical']}</b></div>
  <div class="kpi"><span>当前 episode</span><b>{payload['n_current']}</b></div>
  <div class="kpi"><span>强成功阈值</span><b>&gt;10%</b></div>
</div>

<h2>一、成功 vs 失败底部 × context（核心假设验证）</h2>
<div class="card">{_feature_table(feature_stats)}
<p class="meta">高亮 = 成功/失败均值差 >3 个百分点（或位置差明显）。「强成功」= ret120 中位 &gt;10%（经济意义阈值，非统计显著）。</p></div>

<h2>二、分类标签分布</h2>
<div class="card">
<h3>市场 regime × 成功</h3>
<p>{_label_html(labels.get('success_regime', {}))}</p>
<h3>产业相对模式 × 成功</h3>
<p>{_label_html(labels.get('success_mode', {}))}</p>
<h3>已改善状态 × 成功</h3>
<p>{_label_html(labels.get('success_recovery', {}))}</p>
</div>

<h2>三、每个当前 episode 的 Top3 历史匹配</h2>
<div class="card">{_match_table(matches)}
<p class="meta">距离 = 五维组内 z² 加权平方和开方；等权（20%×5）为主，30/25/20/15/10 为 sensitivity。相似历史可能来自不同产业（context 可比），不构成「同产业 analog」。</p></div>

<h2>四、口径与限定</h2>
<div class="card">
<ul>
<li>No look-ahead：所有 context 只用 episode.start 当日及之前信息；2B 的 ex-post 字段（最终参与率/时长）不进入距离</li>
<li>Z-score scaler 只 fit 历史 episode（{payload['n_historical']} 个），再 transform historical + current</li>
<li>产业相对强弱 = 当天簇内全部有效 ETF 相对 HS300；底部深度/修复 = 当天实际处于 DEEP/RECOVERING 的 ETF</li>
<li>五维权重：等权为主（market/industry/depth/sync/recovery 各 20%），30/25/20/15/10 为 sensitivity</li>
<li>样本小（20 历史 episode），不做复杂统计模型，结论为描述性观察</li>
</ul>
</div>

<footer>AKsignal · Lane 2 Research · Study 2C Current Episode Context Matching · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</footer>
</div></body></html>"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path


def _label_html(d: dict) -> str:
    if not d:
        return "—"
    return "；".join(f"{k}：{_pct(v.get('success_rate'), 0, False)}（{v.get('n_success')}/{v.get('n_total')}）" for k, v in d.items())
