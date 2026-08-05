"""Strategy Specification — hash 边界。

- config_hash：覆盖影响 Strategy Specification 的所有配置（主题/资产池/策略/指标/执行/组合/行业），
  序列化为规范结构（key 排序）后哈希 → YAML 字段顺序变化不改变 hash；数值变化改变 hash。
- universe_hash：实际参与运行的资产集合（排序后哈希，顺序无关，增删改变）。
- rule_version：规则实现代码的语义版本（算法变化才改；配置数值变化不改）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from .loaders import spec_config_files
from .model import RULE_VERSION


def _canonical(obj: Any) -> Any:
    """递归排序 dict 键，使哈希与字段顺序无关。"""
    if isinstance(obj, dict):
        return {k: _canonical(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return [_canonical(v) for v in obj]
    return obj


def _file_canonical_json(path: Path) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        return json.dumps(_canonical(raw), ensure_ascii=False, sort_keys=True)
    except Exception:
        return None


def config_hash() -> str:
    """Strategy Specification 配置指纹（order-independent）。"""
    h = hashlib.sha256()
    h.update(f"rule_version:{RULE_VERSION}".encode("utf-8"))
    for path in spec_config_files():
        canon = _file_canonical_json(path)
        h.update(f"{path.name}:".encode("utf-8"))
        h.update((canon or "MISSING").encode("utf-8"))
    return h.hexdigest()[:16]


def universe_hash(codes: list[str], *, mode: str = "configured", theme: str = "") -> str:
    """实际参与运行的资产集合指纹（排序后哈希，顺序无关）。"""
    h = hashlib.sha256()
    h.update(f"universe_mode:{mode}|theme:{theme}".encode("utf-8"))
    for code in sorted(set(str(c) for c in codes if c)):
        h.update(f"{code},".encode("utf-8"))
    return h.hexdigest()[:16]
