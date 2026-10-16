"""
AKSignal 顶层命令路由

职责：
  识别用户要执行的子系统，转发到对应的 CLI 入口。
  不包含个股、行业或 ETF 的业务逻辑。

用法：
  python src/main.py                        → 观察系统每日批次（默认）
  python src/main.py final-check            → 观察产物验收
  python src/main.py run-summary run        → 观察日报摘要
  python src/main.py etf <command>          → ETF 行情与指标
  python src/main.py run-day                → 观察系统每日批次
  python src/main.py research basket compare → 独立篮子研究
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

def main() -> None:
    argv = sys.argv[1:]

    if argv:
        cmd = argv[0]

        if cmd == "run-day":
            from src.daily_run import main as daily_main
            sys.argv = [sys.argv[0], *argv[1:]]
            daily_main()
            return

        if cmd == "observation":
            from src.observation.cli import main as observation_main
            sys.argv = [sys.argv[0], *argv[1:]]
            observation_main()
            return

        if cmd == "etf":
            from src.etf_signal.cli import main as etf_main
            sys.argv = [sys.argv[0], *argv[1:]]
            etf_main()
            return

        if cmd in ("final-check", "run-day-check"):
            from src.observation.cli import main as observation_main
            sys.argv = [sys.argv[0], "check", *argv[1:]]
            observation_main()
            return

        if cmd == "research":
            from src.research.cli import main as research_main
            sys.argv = [sys.argv[0], *argv[1:]]
            research_main()
            return

        if cmd == "data":
            from src.data_refresh.cli import main as data_main
            sys.argv = [sys.argv[0], *argv[1:]]
            data_main()
            return

        if cmd in ("run-summary", "run-day-summary"):
            from src.observation.cli import main as observation_main
            tail = argv[2:] if len(argv) > 1 and argv[1] == "run" else argv[1:]
            sys.argv = [sys.argv[0], "summary", *tail]
            observation_main()
            return

    # Default entry is the observation-only daily batch.
    from src.daily_run import main as daily_main
    daily_main()



if __name__ == "__main__":
    main()
