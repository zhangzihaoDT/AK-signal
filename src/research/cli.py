"""
Research CLI — 历史信号研究链路命令入口。

子命令：
  replay        历史信号重放（single / parity / range），见 src/research/replay/cli.py
  event-study   状态转换事件的前向收益研究，见 src/research/event_study/cli.py
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

    if sub in ("event-study", "event_study"):
        from src.research.event_study.cli import main as es_main
        sys.argv = [sys.argv[0], *argv[1:]]
        es_main()
        return

    print("research subcommands: replay (single|parity|range), event-study")
    sys.exit(2)


if __name__ == "__main__":
    main()
