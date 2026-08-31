from __future__ import annotations

import argparse
from pathlib import Path

from .stage1a import run_stage1a


def main() -> None:
    parser = argparse.ArgumentParser(description="ETF mapping feasibility research")
    parser.add_argument("--sample-n", type=int, default=10)
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    path = run_stage1a(args.sample_n, Path(args.output) if args.output else None)
    print(path)


if __name__ == "__main__":
    main()
