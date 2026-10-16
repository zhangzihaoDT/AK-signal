"""
Research CLI — 当前独立篮子研究入口。

研究代码不参与每日 Market Facts 主链。
"""

from __future__ import annotations

import sys


def main() -> None:
    argv = sys.argv[1:]
    sub = argv[0] if argv else ""

    if sub in ("basket", "baskets"):
        from src.research.baskets.cli import main as basket_main
        sys.argv = [sys.argv[0], *argv[1:]]
        basket_main()
        return

    print("research subcommands: basket compare")
    sys.exit(2)


if __name__ == "__main__":
    main()
