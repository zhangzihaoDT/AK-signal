"""Research Basket CLI。"""

from __future__ import annotations

import argparse
from pathlib import Path


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Formal Basket Compare research")
    sub = p.add_subparsers(dest="command")

    compare = sub.add_parser('compare', aliases=['compare-longbridge'], help='长桥独立收盘价快照：八个研究篮子+高现金流参照')
    compare.add_argument('--start', required=True)
    compare.add_argument('--end', required=True)
    compare.add_argument('--snapshot', help='离线快照目录')
    compare.add_argument('--allow-online-fetch', action='store_true')
    compare.add_argument('--output', required=True)

    return p


def main() -> None:
    args = build_arg_parser().parse_args()
    if args.command in ('compare','compare-longbridge'):
        from .comparison import fetch_prices, build_longbridge_comparison
        from .report import render_comparison
        snapshot=fetch_prices(args.start,args.end) if args.allow_online_fetch else args.snapshot
        if not snapshot:
            raise ValueError('离线比较必须提供 --snapshot；联网需显式 --allow-online-fetch')
        results,receipt=build_longbridge_comparison(Path(snapshot),Path(args.output),args.start,args.end)
        path=render_comparison(results,receipt,Path(args.output)/'basket_compare.html')
        from src.common.paths import project_root
        from src.hub.baskets import pin_comparison
        try:
            Path(args.output).resolve().relative_to(project_root().resolve()/'.run_day/runs')
        except ValueError:
            pass
        else:
            pin_comparison(project_root(),Path(args.output))
        print(f'report: {path}')
        return
    build_arg_parser().print_help()
    sys.exit(2)
