"""Durable run checkpoints and a single publication pointer for all outputs."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


@contextmanager
def batch_lock(root: Path):
    store = root / ".run_day"
    store.mkdir(parents=True, exist_ok=True)
    with (store / "run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("已有 run-day 正在运行，请用 make run-day-status 查看进度") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


@contextmanager
def output_workspace(path: Path):
    previous = os.environ.get("AKSIGNAL_OUTPUTS_DIR")
    os.environ["AKSIGNAL_OUTPUTS_DIR"] = str(path)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("AKSIGNAL_OUTPUTS_DIR", None)
        else:
            os.environ["AKSIGNAL_OUTPUTS_DIR"] = previous


def fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def implementation_signature(root: Path) -> str:
    digest = hashlib.sha256()
    for directory in (root / "src", root / "config"):
        for path in sorted(directory.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                digest.update(str(path.relative_to(root)).encode())
                digest.update(fingerprint(path).encode())
    version = root / "VERSION"
    if version.exists():
        digest.update(b"VERSION")
        digest.update(fingerprint(version).encode())
    return digest.hexdigest()


def checkpoint_path(root: Path, trade_date: str, offline: bool) -> Path:
    mode = "offline" if offline else "online"
    return root / ".run_day" / "checkpoints" / f"{trade_date}-{mode}.json"


def read_checkpoint(root: Path, trade_date: str, offline: bool) -> dict | None:
    path = checkpoint_path(root, trade_date, offline)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def run_dir(root: Path, state: dict) -> Path:
    # IDs are generated locally, and must never route a checkpoint outside its store.
    run_id = state["run_id"]
    uuid.UUID(hex=run_id)
    trade_date = datetime.strptime(state["trade_date"], "%Y%m%d").strftime("%Y%m%d")
    return root / ".run_day" / "runs" / trade_date / run_id


def save_checkpoint(root: Path, state: dict) -> None:
    state["updated_at"] = now()
    atomic_json(run_dir(root, state) / "state.json", state)
    atomic_json(checkpoint_path(root, state["trade_date"], state["offline"]), state)
    atomic_json(root / ".run_day" / "latest.json", state)


def new_checkpoint(root: Path, trade_date: str, offline: bool, commands: list[list[str]]) -> dict:
    state = {
        "schema_version": 1, "run_id": uuid.uuid4().hex,
        "trade_date": trade_date, "offline": offline,
        "signature": implementation_signature(root), "started_at": now(),
        "status": "preparing", "current_step": 0, "prepared": False,
        "steps": [{"command": c, "status": "pending", "attempts": 0} for c in commands],
    }
    save_checkpoint(root, state)
    return state


def prepare_workspace(root: Path, state: dict) -> Path:
    stage = run_dir(root, state) / "outputs"
    if not state.get("prepared"):
        # A killed initial copy may leave a private partial tree; no public file is touched.
        if stage.exists():
            shutil.rmtree(stage)
        # Each batch publishes only current observation products. Prior releases
        # remain in their own run directory; do not duplicate research archives.
        stage.mkdir(parents=True)
        state["prepared"] = True
        save_checkpoint(root, state)
    return stage


def publish_workspace(root: Path, stage: Path) -> None:
    """Switch all reports together. Preserve the first ordinary outputs tree for recovery."""
    if not stage.is_dir():
        raise ValueError("待发布产物目录缺失")
    public = root / "outputs"
    temp = root / f".outputs-{uuid.uuid4().hex}"
    backup = None
    temp.symlink_to(os.path.relpath(stage, root), target_is_directory=True)
    try:
        if public.is_dir() and not public.is_symlink():
            backup = root / ".run_day" / "legacy" / uuid.uuid4().hex / "outputs"
            backup.parent.mkdir(parents=True, exist_ok=True)
            os.replace(public, backup)
        try:
            os.replace(temp, public)
        except BaseException:
            if backup is not None and not public.exists():
                os.replace(backup, public)
            raise
    finally:
        temp.unlink(missing_ok=True)


def published_batch(root: Path, trade_date: str) -> dict | None:
    """Verify a committed receipt, including older releases, across run modes.

    A receipt in a private stage alone is not evidence of publication. The
    public pointer or the archived published checkpoint is the commit witness.
    """
    public = root / "outputs"
    store = root / ".run_day" / "runs" / trade_date
    def candidates():
        if public.is_symlink():
            yield public.resolve(), True
        for state_path in sorted(store.glob("*/state.json"), reverse=True):
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("status") == "published" and state.get("trade_date") == trade_date:
                yield run_dir(root, state) / "outputs", False
    for stage, current in candidates():
        receipt_path = stage / "run_day" / "publication.json"
        if not receipt_path.exists():
            if current:
                raise ValueError("正式发布指针缺少回执，拒绝重复执行")
            continue
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("trade_date") != trade_date:
            continue
        expected = run_dir(root, receipt) / "outputs"
        if stage.resolve() != expected.resolve():
            raise ValueError("发布回执 run_id 与批次路径不一致")
        result = receipt.get("result", {})
        final = json.loads((stage / "run_day" / f"final_check_{trade_date}.json").read_text())
        report = json.loads((stage / "observation" / f"observation_{trade_date}.json").read_text())
        if (final != result or final.get("ok") is not True or final.get("errors")
                or final.get("trade_date") != trade_date
                or final.get("scope") != "observation_only"
                or report.get("trade_date") != trade_date):
            raise ValueError("正式发布日期或验收回执不一致，拒绝重复执行")
        for suffix in (".html", ".md"):
            path = stage / "observation" / f"observation_{trade_date}{suffix}"
            if not path.is_file() or not path.stat().st_size:
                raise ValueError("正式日报不完整，拒绝重复执行")
        return receipt
    return None


def lock_active(root: Path) -> bool:
    """Probe the native lock without creating a batch or parsing process names."""
    path = root / ".run_day" / "run.lock"
    if not path.exists():
        return False
    with path.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(stream, fcntl.LOCK_UN)
    return False
