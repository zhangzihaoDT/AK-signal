"""Manual on-demand refresh for the Hub's pinned basket comparison."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4

from src.common.paths import project_root
from src.common.trading_calendar import default_calendar, shanghai_now
from src.hub.baskets import comparison_view, pin_comparison
from . import comparison
from .report import render_comparison


# The validated Longbridge comparison baseline. Subsequent refreshes preserve
# the start date of the currently pinned report rather than shifting the view.
DEFAULT_START_DATE = "2024-07-16"


def update(root: Path | None = None, at: datetime | None = None) -> dict:
    root = (root or project_root()).resolve()
    target_date = default_calendar(root).complete_day(at or shanghai_now())

    # A corrupt existing pointer must fail closed; do not overwrite the last
    # valid research result with a refresh built on an unknown baseline.
    current, _ = comparison_view(root)
    start_date = current["startDate"] if current else DEFAULT_START_DATE
    end_date = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:]}"
    if end_date <= start_date:
        raise ValueError("目标交易日必须晚于 Compare 起始日期")

    snapshot = comparison.fetch_prices(start_date, end_date)
    output = (root / ".run_day" / "runs" / target_date /
              f"research-compare-{uuid4().hex}" / "outputs" / "research" / "baskets" / "comparable")
    results, receipt = comparison.build_longbridge_comparison(snapshot, output, start_date, end_date)
    report = render_comparison(results, receipt, output / "basket_compare.html")

    # Publish the new pointer only after data validation and report generation
    # have both completed. A failure leaves the previous comparison visible.
    pin_comparison(root, output)
    print(f"Compare updated: {start_date} — {end_date}")
    print(f"report: {report}")
    return {"startDate": start_date, "endDate": end_date, "report": str(report)}


if __name__ == "__main__":
    update()
