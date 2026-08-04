"""
AKSignal 顶层命令路由

职责：
  识别用户要执行的子系统，转发到对应的 CLI 入口。
  不包含个股、行业或 ETF 的业务逻辑。

用法：
  python src/main.py                        → Layer ③ 交易候选（默认）
  python src/main.py select [run]           → Layer ③ 交易候选
  python src/main.py final-check            → run-day 末端 Final Validation
  python src/main.py industry <command>     → 申万行业 RPS（Layer ② 确认）
  python src/main.py etf <command>          → ETF 趋势信号（Layer ①）
  python src/main.py run-day                → 申万行业 RPS run-day（向后兼容）
  python src/main.py bootstrap|update|calculate|report|validate → 申万行业 RPS（向后兼容）
"""

from __future__ import annotations

import sys
from pathlib import Path

# 确保 src/ 和项目根目录均在 sys.path 上，使模块入口和脚本入口一致
_this_dir = str(Path(__file__).resolve().parent)
_project_root = str(Path(__file__).resolve().parent.parent)
for p in [_project_root, _this_dir]:
    if p not in sys.path:
        sys.path.insert(0, p)

SW_INDUSTRY_COMMANDS = {
    "bootstrap", "update", "calculate", "report", "validate", "run-day", "drilldown",
}

ETF_COMMANDS = {
    "bootstrap", "bootstrap-core", "update", "classify", "layer1", "screen",
    "watchlist", "account", "card", "pipeline",
    "calculate", "signal", "report", "backtest", "run-day",
}


def main() -> None:
    argv = sys.argv[1:]

    if argv:
        cmd = argv[0]

        if cmd == "etf":
            from src.etf_signal.cli import main as etf_main
            sys.argv = [sys.argv[0], *argv[1:]]
            etf_main()
            return

        if cmd in SW_INDUSTRY_COMMANDS:
            from src.sw_industry_rps.cli import main as sw_main
            sys.argv = [sys.argv[0], *argv]
            sw_main()
            return

        if cmd == "industry":
            from src.sw_industry_rps.cli import main as sw_main
            sys.argv = [sys.argv[0], *argv[1:]]
            sw_main()
            return

        if cmd in ("select", "layer3"):
            from src.selection.cli import main as selection_main
            sys.argv = [sys.argv[0], *argv[1:]]
            selection_main()
            return

        if cmd in ("final-check", "run-day-check"):
            from src.final_validation.cli import main as fv_main
            sys.argv = [sys.argv[0], *argv[1:]]
            fv_main()
            return

        if cmd == "research":
            from src.research.cli import main as research_main
            sys.argv = [sys.argv[0], *argv[1:]]
            research_main()
            return

        if cmd == "backtest":
            from src.backtest.cli import main as backtest_main
            sys.argv = [sys.argv[0], *argv[1:]]
            backtest_main()
            return

        if cmd == "data":
            from src.data_refresh.cli import main as data_main
            sys.argv = [sys.argv[0], *argv[1:]]
            data_main()
            return

        if cmd == "replay":  # 兼容别名：research replay
            from src.research.cli import main as research_main
            sys.argv = [sys.argv[0], *["replay", *argv[1:]]]
            research_main()
            return

    # 无命令时默认走 Layer ③（原 stock_trend 已并入 trend_engine，不再有独立入口）
    from src.selection.cli import main as selection_main
    selection_main()


if __name__ == "__main__":
    main()
