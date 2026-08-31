"""Aug2026 研究 CLI。"""

from __future__ import annotations

import argparse
import json
import sys

import akshare as ak
import pandas as pd

from .universe import build_universe_manifest
from . import fetch_market


def _fetch_full_market_codes() -> list[str]:
    """全市场 A 股代码清单（akshare spot，当前可用源）。

    返回值形如 'bj920000' / 'sz000001' / 'sh600000'，抓取前需转 6 位数字。
    """
    df = ak.stock_zh_a_spot()  # sina 全市场快照（5550 只）
    if df is None or df.empty:
        raise RuntimeError("stock_zh_a_spot returned empty")
    cols = {str(c).strip().lower(): c for c in df.columns}
    code_col = cols.get("代码") or cols.get("code") or cols.get("symbol")
    if code_col is None:
        raise RuntimeError(f"stock_zh_a_spot unexpected columns: {list(df.columns)}")
    codes = [str(x).strip() for x in df[code_col].dropna()]
    # 只保留含 6 位数字的代码（去掉 sh/sz/bj 前缀）
    out = []
    for c in codes:
        digits = "".join(ch for ch in c if ch.isdigit())
        if len(digits) == 6:
            out.append(digits)
    return sorted(set(out))


def cmd_manifest(args: argparse.Namespace) -> int:
    m = build_universe_manifest()
    print(json.dumps({k: v for k, v in m.items() if not isinstance(v, list)}, ensure_ascii=False, indent=2))
    print("missing_from_replay_need_compute:", m["compute_list"])
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    if args.list:
        codes = _fetch_full_market_codes()
        pd.Series(codes).to_csv(args.list, index=False, header=["code"])
        print(f"full market codes: {len(codes)} -> {args.list}")
        return 0
    codes = []
    if args.code_list:
        codes = pd.read_csv(args.code_list, dtype={"code": str})["code"].str.strip().tolist()
        codes = [c for c in codes if c]
    done, failed = fetch_market.fetch_all_codes(codes, force=args.force)
    print(f"fetch done: {len(done)} failed: {len(failed)}")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="August 2026 Cross-sectional Return Study")
    sub = p.add_subparsers(dest="command")
    p_manifest = sub.add_parser("manifest", help="构建 universe manifest + 差集校验")
    p_manifest.set_defaults(func=cmd_manifest)

    p_fetch = sub.add_parser("fetch", help="全市场日线抓取（串行 + checkpoint）")
    p_fetch.add_argument("--code-list", default="", help="代码清单 csv（第一列 code）")
    p_fetch.add_argument("--list", default="", help="仅生成全市场清单并写入该路径")
    p_fetch.add_argument("--force", action="store_true", help="强制重抓")
    p_fetch.set_defaults(func=cmd_fetch)
    return p


def main() -> None:
    args = build_arg_parser().parse_args()
    fn = getattr(args, "func", None)
    if fn is None:
        build_arg_parser().print_help()
        sys.exit(2)
    sys.exit(fn(args))


if __name__ == "__main__":
    main()
