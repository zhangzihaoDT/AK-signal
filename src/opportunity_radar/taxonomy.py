"""
Opportunity Radar — Candidate Direction 分类器（V1.1，读研究级 YAML）

职责：把 passing 的 NEW_THEME_CANDIDATE ETF 归到「候选方向」。
  market_scope：真实市场（a_share / hk / overseas），由 scope_inference 独立推断。
  market_beta ：命中 broad_beta.keywords 的宽基/区域 ETF = True（Market Beta，非 Theme）；
                与 market_scope 正交（上证指数 = a_share + market_beta）。
  direction_key：directions[] 有序首命中 → 语义方向 key。
  candidate_theme_key = direction_key + "." + market_scope（跨市场自动拆开）。
  全部未命中 → UNCLASSIFIED（不猜，不进候选方向）。

本模块纯函数、无副作用；不联网；只消费 fund_name + YAML taxonomy。
"""

from __future__ import annotations

from typing import Any

# 未命中方向的残余标记（展示层显示为 audit，不占候选方向名额）
UNCLASSIFIED = "UNCLASSIFIED"

# candidate_theme_key 复合分隔符
_THEME_KEY_SEP = "."


def _hit(name: str, keywords: list[str]) -> bool:
    n = str(name).lower()
    return any(k and k.lower() in n for k in keywords)


def infer_market_scope(name: str, spec: dict[str, Any]) -> str:
    """真实市场 scope：hk / overseas，默认 a_share。（不含 beta——beta 是独立布尔）"""
    si = spec.get("scope_inference") or {}
    if _hit(name, si.get("hk") or []):
        return "hk"
    if _hit(name, si.get("overseas") or []):
        return "overseas"
    return "a_share"


def is_market_beta(name: str, spec: dict[str, Any]) -> bool:
    """宽基/区域 Market Beta 判定（broad_beta.keywords）。"""
    broad = (spec.get("broad_beta") or {}).get("keywords") or []
    return _hit(name, broad)


def match_direction(name: str, spec: dict[str, Any]) -> dict[str, Any] | None:
    """directions[] 有序首命中 → 返回 direction 条目 dict；未命中 None。"""
    for d in spec.get("directions") or []:
        if _hit(name, d.get("keywords") or []):
            return d
    return None


def classify_candidate(
    name: str,
    spec: dict[str, Any],
) -> dict[str, Any]:
    """逐 ETF 归类。

    Returns:
        {
          market_scope,           # a_share / hk / overseas（真实市场，恒有值）
          market_beta,            # bool：宽基/区域 Beta（非 Theme）
          direction_key,          # 语义方向 key（未命中 None）
          candidate_theme_key,    # direction_key.market_scope（未命中/beta → None）
          candidate_label,        # 方向 label（未命中/beta → None）
          bucket, note,           # 方向建议 bucket / note
          classified,             # 是否归入候选方向（!market_beta 且命中方向）
        }
    """
    market_scope = infer_market_scope(name, spec)
    beta = is_market_beta(name, spec)
    if beta:
        return {"market_scope": market_scope, "market_beta": True,
                "direction_key": None, "candidate_theme_key": None,
                "candidate_label": None, "bucket": None, "note": None,
                "classified": False}
    d = match_direction(name, spec)
    if d is None:
        return {"market_scope": market_scope, "market_beta": False,
                "direction_key": None, "candidate_theme_key": None,
                "candidate_label": None, "bucket": None, "note": None,
                "classified": False}
    return {
        "market_scope": market_scope,
        "market_beta": False,
        "direction_key": str(d.get("key", "")),
        "candidate_theme_key": f"{d.get('key')}{_THEME_KEY_SEP}{market_scope}",
        "candidate_label": d.get("label"),
        "bucket": d.get("bucket"),
        "note": d.get("note"),
        "classified": True,
    }


def aggregate_directions(classified: list[dict[str, Any]]) -> dict[str, Any]:
    """candidate_themes[] 聚合（在 classify_candidate 之后）。

    每个 candidate_theme_key 一个条目：
      candidate_theme_key / direction_key / market_scope / label / bucket /
      n_etfs / median_rps15 / max_rps15 / representative(fund_code/name/amount) /
      members / note。
    排序：n_etfs desc → candidate_theme_key asc（展示用，不代表推荐）。

    Args:
        classified: [ {classify_candidate(...), **etf 事实} ... ] 仅 classified=True 的条目。

    Returns:
        {"candidate_themes": [ ... ]}
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in classified:
        tk = row.get("candidate_theme_key")
        if not tk:
            continue
        groups.setdefault(tk, []).append(row)

    out: list[dict[str, Any]] = []
    for tk, rows in groups.items():
        first = rows[0]
        rps = [float(r["rps15"]) for r in rows if r.get("rps15") is not None]
        rep = max(rows, key=lambda r: r.get("amount") or 0)
        n = len(rps)
        rps_sorted = sorted(rps)
        median = rps_sorted[n // 2] if n else None
        out.append({
            "candidate_theme_key": tk,
            "direction_key": first.get("direction_key"),
            "market_scope": first.get("market_scope"),
            "label": first.get("candidate_label"),
            "bucket": first.get("bucket"),
            "note": first.get("note"),
            "n_etfs": len(rows),
            "median_rps15": median,
            "max_rps15": rps_sorted[-1] if rps_sorted else None,
            "representative": {
                "fund_code": rep.get("fund_code"),
                "fund_name": rep.get("fund_name"),
                "amount": rep.get("amount"),
            },
            "members": [r.get("fund_code") for r in sorted(rows, key=lambda r: r.get("fund_code") or "")],
        })
    out.sort(key=lambda g: (-g["n_etfs"], g["candidate_theme_key"] or ""))
    return {"candidate_themes": out}
