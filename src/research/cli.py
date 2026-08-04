"""
Research CLI — 历史信号研究链路命令入口。

子命令：
  replay   历史信号重放（single / parity），见 src/research/replay/cli.py
"""

from __future__ import annotations

import sys


def main() -> None:
    argv = sys.argv[1:]
    sub = argv[0] if argv else ""

    if sub == "replay":
        from src.research.replay.cli import main as replay_main
        sys.argv = [sys.argv[0], *argv[1:]]
        replay_main()
        return

    print("research subcommands: replay (single|parity --date YYYYMMDD)")
    sys.exit(2)


if __name__ == "__main__":
    main()
