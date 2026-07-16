"""
AKSignal 顶层命令路由

职责：
  识别用户要执行的子系统，转发到对应的 CLI 入口。
  不包含个股或行业的业务逻辑。

用法：
  python src/main.py                      → 个股趋势监控（默认，向后兼容）
  python src/main.py --start-date ...     → 个股趋势监控（向后兼容）
  python src/main.py stock [options]      → 个股趋势监控
  python src/main.py industry <command>   → 申万行业 RPS
  python src/main.py run-day              → 申万行业 RPS run-day（向后兼容）
  python src/main.py bootstrap|update|calculate|report|validate → 申万行业 RPS（向后兼容）
"""

from __future__ import annotations

import sys

SW_INDUSTRY_COMMANDS = {
    "bootstrap", "update", "calculate", "report", "validate", "run-day",
}


def main() -> None:
    argv = sys.argv[1:]

    if argv:
        cmd = argv[0]

        if cmd in SW_INDUSTRY_COMMANDS:
            from sw_industry_rps.cli import main as sw_main
            sys.argv = [sys.argv[0], *argv]
            sw_main()
            return

        if cmd == "industry":
            from sw_industry_rps.cli import main as sw_main
            sys.argv = [sys.argv[0], *argv[1:]]
            sw_main()
            return

        if cmd == "stock":
            from stock_trend.cli import main as stock_main
            sys.argv = [sys.argv[0], *argv[1:]]
            stock_main()
            return

    from stock_trend.cli import main as stock_main
    stock_main()


if __name__ == "__main__":
    main()
