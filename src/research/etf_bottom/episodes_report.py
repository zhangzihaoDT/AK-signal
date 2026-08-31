"""Study 2B Bottom Episode Clustering HTML 报告（zihao raccoon 视觉体系）。

结构：
  一句话结论 + 各产业独立底部周期数
  每产业 episode 时间线表（起始~结束 / 参与ETF数 / 占比 / ret120中位 / 上涨 / 当前标注）
  汇总表（产业 × 独立周期数 × 上涨比例 × A/B 分类）
  口径与限定
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from . import STUDY_DIR

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
.tag{display:inline-block;padding:1px 8px;border-radius:8px;font-size:.75em}
.tag-cur{background:#fff3d6;color:#7a5a00}
.tag-up{color:#1a7a5a}.tag-down{color:#b32424}
"""


def _pct(v, nd=1, sign=True):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v*100:+.{nd}f}%" if sign else f"{v*100:.{nd}f}%"


def _ret(v):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    cls = "pos" if v > 0 else ("neg" if v < 0 else "")
    return f"<span class='{cls}'>{_pct(v)}</span>"


def _episode_table(eps: list[dict]) -> str:
    if not eps:
        return "<p>无 episode</p>"
    rows = ""
    for e in sorted(eps, key=lambda x: x["start"]):
        r = e["returns"]
        cur = " <span class='tag tag-cur'>当前</span>" if e["is_current"] else ""
        up = ("<span class='tag tag-up'>上涨</span>" if r.get("episode_up") is True
              else ("<span class='tag tag-down'>下跌</span>" if r.get("episode_up") is False else "—"))
        rows += (
            f"<tr><td>{str(e['start'])[:10]}</td><td>{str(e['end'])[:10]}</td>"
            f"<td>{e['n_etfs_participating']}/{e['cluster_size']}</td><td>{e['participation_ratio']*100:.0f}%</td>"
            f"<td>{_ret(r.get('ret120_median'))}</td><td>{_pct(r.get('ret120_win_rate'), 0, False)}</td>"
            f"<td>{r.get('n_etfs_with_ret120', 0)}</td><td>{up}{cur}</td></tr>"
        )
    return f"""<table>
<thead><tr><th>起始</th><th>结束</th><th>参与ETF/簇</th><th>参与占比</th><th>120D中位</th><th>胜率</th><th>有120D的ETF</th><th>结果</th></tr></thead>
<tbody>{rows}</tbody></table>"""


def _cluster_summary_table(summary: dict) -> str:
    rows = ""
    for cluster, s in summary.items():
        n_hist = s["n_episodes_historical"]
        up = s["n_episodes_up"]
        ratio = s["up_ratio_historical"]
        if ratio >= 0.6:
            cls = "A 类（独立周期多且多数上涨）"
        elif ratio >= 0.4:
            cls = "B 类（证据中等）"
        else:
            cls = "C 类（低位后少反弹）"
        rows += (
            f"<tr><td><b>{cluster}</b></td><td>{s['n_episodes_total']}</td><td>{n_hist}</td>"
            f"<td>{up}/{n_hist}</td><td>{_pct(ratio, 0, False)}</td><td>{cls}</td></tr>"
        )
    return f"""<table>
<thead><tr><th>产业簇</th><th>episode 总数</th><th>历史独立周期</th><th>上涨周期</th><th>上涨比例</th><th>分类</th></tr></thead>
<tbody>{rows}</tbody></table>"""


def render_episodes(payload: dict, out_path: Path | None = None) -> Path:
    out_path = out_path or (STUDY_DIR / "bottom_episodes.html")
    summary = payload["summary"]
    clusters = payload["clusters"]

    best = max(summary.items(), key=lambda kv: kv[1]["up_ratio_historical"])
    worst = min(summary.items(), key=lambda kv: kv[1]["up_ratio_historical"])
    headline = (
        f"按独立产业周期压缩后：{best[0]} 上涨比例最高（{best[1]['n_episodes_up']}/{best[1]['n_episodes_historical']}）"
        f"，{worst[0]} 最低（{worst[1]['n_episodes_up']}/{worst[1]['n_episodes_historical']}）——"
        f"去除同产业+同周期重复暴露后，低位的产业周期支持并不均匀。"
    )

    sections = ""
    for cluster, eps in clusters.items():
        sections += f"<h3>{cluster}（{len(eps)} episodes）</h3>{_episode_table(eps)}"

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Study 2B · Bottom Episode Clustering</title>
<style>{_CSS}</style></head><body>
<div class="wrap">
<header>
  <h1>Study 2B · Bottom Episode Clustering</h1>
  <div class="meta">同产业高度同步的 ETF 底部事件 → 独立产业周期 episode · ETF_MERGE_DAYS={payload['params']['etf_merge_days']} 交易日 · OVERLAP={payload['params']['episode_overlap_days']} 交易日 · 上涨=ret_120 中位>0 · {payload.get('generated_at', '')}</div>
</header>

<div class="summary">
  <h3>一句话结论</h3>
  <p>{headline}</p>
</div>

<div class="kpis">
  <div class="kpi"><span>ETF 低位期</span><b>{payload.get('n_etf_low_periods', 0)}</b></div>
  <div class="kpi"><span>产业簇</span><b>{len(clusters)}</b></div>
</div>

<h2>一、各产业独立底部周期汇总</h2>
<div class="card">{_cluster_summary_table(summary)}</div>

<h2>二、每产业 episode 时间线</h2>
<div class="card">{sections}</div>

<h2>三、口径与限定</h2>
<div class="card">
<ul>
<li>Layer 1 ETF 低位期合并：同一 ETF 相邻 entry 间隔 &lt; {payload['params']['etf_merge_days']} 交易日 → 合并（解决 off→on 反复触发把同一底部周期拆碎）</li>
<li>Layer 2 产业 episode：同簇内 ETF 低位期起始间隔 &lt; {payload['params']['episode_overlap_days']} 交易日 → 合并为一个独立产业周期</li>
<li>episode 收益 = 参与 ETF 各自低位期起始日起的 ret_120（等权，中位判定上涨）；当前 2026 episode 无 ret_120（未到期），不参与历史上涨比例</li>
<li>产业簇为研究专用硬编码（游戏传媒/汽车产业链/软件大数据/消费文旅/周期/军工航空）；货币 ETF 华安日日鑫剔除</li>
<li>分类：A 类=独立周期≥2 且多数上涨；B 类=证据中等；C 类=低位后少反弹（对照 Study 2A 的 ETF 级「历史支持/不支持」）</li>
</ul>
</div>

<footer>AKsignal · Lane 2 Research · Study 2B Bottom Episode Clustering · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</footer>
</div></body></html>"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path
