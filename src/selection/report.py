"""
Layer ③ — 投资推荐 HTML 入口（v2，Report Engine 薄入口）。

本文件不再拼 HTML：报告结构由 `report_spec.yaml` 声明，HTML 由 `report_engine.py`
解释 Spec + ReportViewModel 生成。本文件只保留：
  - render_selection_html（外部契约入口，签名不变，新增可选 now_str / prev）
  - 若干 formatter 兼容别名（供既有测试/调用方使用，实现在 report_formatters.py）

报告内容真源：src/selection/report_spec.yaml；改内容改 Spec，不在此处拼 HTML。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .report_engine import render_selection_html_v2
from .report_formatters import (
    RISK_FLAG_SHORT,
    _etf_conclusion_text,
    _monitor_conclusion_text,
    fmt_blocking,
    fmt_data_quality,
    fmt_etf_trend_status,
    fmt_technical,
    fmt_trade_state,
)

# ── 兼容别名（旧内部函数 → report_formatters 实现） ───────────────────
_technical_cell = fmt_technical
_blocking_cell = fmt_blocking
_data_quality_cell = fmt_data_quality
_etf_trend_status_cn = fmt_etf_trend_status
_trade_state_cn = fmt_trade_state
_monitor_conclusion = _monitor_conclusion_text  # 纯文本（表格层用 fmt_monitor_conclusion 的 tag 包装）
_risk_short = lambda a: fmt_technical(a)  # legacy 回退路径


def render_selection_html(
    recommendation: dict[str, Any],
    output_dir: Path,
    date_str: str,
    meta: dict[str, Any] | None = None,
    *,
    now_str: str | None = None,
    prev: dict[str, Any] | None = None,
) -> Path:
    """生成 Layer③ 报告 HTML（v2 Report Engine 入口）。

    Args:
        recommendation: build_recommendation() 输出（决策 contract，只读）
        output_dir: 输出目录
        date_str: 报告日期 YYYYMMDD
        meta: alignment/layers/coverage/config_issues 等运行信息（审计用）
        now_str: 生成时间（测试注入固定值，保证确定性）
        prev: 上一份 Layer③ 的 build_recommendation 结构（可选，05 跨日对比）
    """
    html = render_selection_html_v2(
        recommendation, output_dir, date_str, meta=meta, now_str=now_str, prev=prev)
    html_path = output_dir / f"tradable_candidates_{date_str}.html"
    output_dir.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html, encoding="utf-8")
    return html_path
