"""Aug2026 研究：全市场 A 股日线抓取（tx 源，串行 + checkpoint）。

并发纪律：单进程串行拉取，每 N 只 checkpoint 落盘一次；断点续跑。
不用多并发打 tx 接口（避免限流）。真正并行的是「网络抓取」与「固定池本地分析」两个进程。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import akshare as ak
import pandas as pd

from . import MARKET_DAILY_DIR, CHUNK_DIR

logger = logging.getLogger("research.aug2026.fetch_market")

CHUNK_SIZE = 100
START_DATE = "20260701"
END_DATE = "20260828"


def _checkpoint_path() -> Path:
    return CHUNK_DIR / "fetch_progress.json"


def _load_checkpoint() -> dict:
    import json
    p = _checkpoint_path()
    if not p.exists():
        return {"done": [], "failed": [], "chunk": 0}
    return json.loads(p.read_text(encoding="utf-8"))


def _save_checkpoint(state: dict) -> None:
    import json
    _checkpoint_path().write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _symbol_tx(code: str) -> str:
    code = str(code).strip()
    if code[:2] in {"sh", "sz", "bj"}:
        return code
    digits = "".join(ch for ch in code if ch.isdigit())
    if len(digits) != 6:
        return code
    code = digits
    if code.startswith(("60", "68", "51", "52", "56", "58")) or code.startswith(("5", "6")):
        return f"sh{code}"
    if code.startswith(("00", "30", "20")) or code.startswith(("0", "1", "2", "3")):
        return f"sz{code}"
    return f"bj{code}"


def _fetch_one(code: str, max_retry: int = 2) -> pd.DataFrame | None:
    sym = _symbol_tx(code)
    for attempt in range(max_retry):
        try:
            df = ak.stock_zh_a_hist_tx(
                symbol=sym, start_date=START_DATE, end_date=END_DATE, adjust="qfq"
            )
            if df is not None and not df.empty:
                df = df.copy()
                df["code"] = code
                df["date"] = pd.to_datetime(df["date"])
                return df
        except Exception as e:  # noqa: BLE001
            logger.debug("fetch %s attempt %d failed: %s", code, attempt, str(e)[:80])
            time.sleep(1.5)
    return None


def fetch_all_codes(codes: list[str], *, force: bool = False) -> tuple[list[str], list[str]]:
    """串行抓取全市场清单。返回 (成功码列表, 失败码列表)。"""
    MARKET_DAILY_DIR.mkdir(parents=True, exist_ok=True)
    CHUNK_DIR.mkdir(parents=True, exist_ok=True)

    state = _load_checkpoint()
    done = set(state.get("done", []))
    failed = set(state.get("failed", []))
    chunk_idx = int(state.get("chunk", 0))

    todo = [c for c in codes if c not in done and c not in failed]
    logger.info("fetch_market: total=%d todo=%d chunk_size=%d", len(codes), len(todo), CHUNK_SIZE)

    chunk_rows: list[pd.DataFrame] = []
    for i, code in enumerate(todo):
        df = _fetch_one(code)
        if df is None:
            failed.add(code)
            logger.warning("fetch failed: %s", code)
        else:
            done.add(code)
            chunk_rows.append(df)

        if (i + 1) % CHUNK_SIZE == 0 or (i + 1) == len(todo):
            if chunk_rows:
                part = pd.concat(chunk_rows, ignore_index=True)
                part_path = CHUNK_DIR / f"chunk_{chunk_idx:04d}.parquet"
                part.to_parquet(part_path, index=False)
                logger.info("checkpoint chunk %d: %d rows -> %s", chunk_idx, len(part), part_path)
                chunk_rows = []
            chunk_idx += 1
            _save_checkpoint({"done": sorted(done), "failed": sorted(failed), "chunk": chunk_idx})

    # 合并所有 chunk 成单一 parquet
    chunks = sorted(CHUNK_DIR.glob("chunk_*.parquet"))
    if chunks:
        merged = pd.concat([pd.read_parquet(c) for c in chunks], ignore_index=True)
        merged["source"] = "tx"
        merged["adjust"] = "qfq"
        out = MARKET_DAILY_DIR / "market_daily_20260701_20260828_qfq.parquet"
        merged.to_parquet(out, index=False)
        logger.info("merged %d chunks -> %s (%d rows)", len(chunks), out, len(merged))

    return sorted(done), sorted(failed)
