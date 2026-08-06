"""
Parity Validation — 重放结果与 daily pipeline 正式产物的逐字段一致性校验（v0.5.0 验收核心）。

对已有正式产物日期（如 2026-08-03），把 replay 输出与正式产物对比：
  - 数值字段（rps15/20/60）允许合理浮点误差；
  - 状态字段（trend_state / confirmation_status / selection_status / recommended_action）必须完全相同。

只有 parity 通过，Replay 才有资格作为历史研究链路。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from src.common.paths import (
    etf_signal_daily_dir, etf_signal_signals_dir,
    sw_industry_confirmation_dir, outputs_dir,
)

logger = logging.getLogger("research.validation.parity")

# 数值字段允许的绝对误差
FLOAT_ATOL = 0.02
# 状态字段（必须完全一致）
STATE_FIELDS = ("trend_state", "confirmation_status", "selection_status", "recommended_action")


def _load_formal_products(trade_date: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    rot = etf_signal_daily_dir() / f"rotation_{trade_date}.parquet"
    ac = etf_signal_signals_dir() / f"account_candidates_{trade_date}.parquet"
    conf = sw_industry_confirmation_dir() / f"confirmation_{trade_date}.parquet"
    sel = outputs_dir() / "selection" / f"tradable_candidates_{trade_date}.json"
    if rot.exists():
        out["rotation"] = pd.read_parquet(rot)
    if ac.exists():
        out["account_candidates"] = pd.read_parquet(ac)
    if conf.exists():
        out["confirmation"] = pd.read_parquet(conf)
    if sel.exists():
        try:
            out["selection"] = json.loads(sel.read_text(encoding="utf-8"))
        except Exception:
            out["selection"] = None
    return out


def _compare_float(actual: Any, expected: Any, atol: float = FLOAT_ATOL) -> bool:
    if actual is None or (isinstance(actual, float) and actual != actual):
        return expected is None or (isinstance(expected, float) and expected != expected)
    if expected is None or (isinstance(expected, float) and expected != expected):
        return False
    try:
        return abs(float(actual) - float(expected)) <= atol
    except (TypeError, ValueError):
        return str(actual) == str(expected)


def _compare_layer1(replayed: pd.DataFrame, formal: dict[str, Any]) -> dict[str, Any]:
    """ETF：rps15 数值一致 + trend_state 状态一致。"""
    result: dict[str, Any] = {"matched": 0, "mismatched": 0, "mismatch_examples": []}
    if replayed.empty:
        result["note"] = "replayed empty"
        return result
    rotation = formal.get("rotation")
    account = formal.get("account_candidates")
    if rotation is None or account is None:
        result["note"] = "formal rotation/account missing"
        return result

    rp = replayed.set_index("entity_code")
    rot_idx = rotation.set_index("fund_code")
    ac_idx = account.set_index("fund_code")

    for code, row in rp.iterrows():
        # trend_state 对比（正式 account_candidates）
        if code in ac_idx.index:
            exp_state = str(ac_idx.at[code, "trend_state"])
            act_state = str(row.get("trend_state", ""))
            ok_state = (act_state == exp_state)
        else:
            ok_state = False
        # rps15 对比（正式 rotation）
        if code in rot_idx.index:
            ok_rps = _compare_float(row.get("rps15"), rot_idx.at[code, "rps15"])
        else:
            ok_rps = False
        if ok_state and ok_rps:
            result["matched"] += 1
        else:
            result["mismatched"] += 1
            if len(result["mismatch_examples"]) < 10:
                result["mismatch_examples"].append({
                    "code": code,
                    "trend_state": (row.get("trend_state", ""), ac_idx.at[code, "trend_state"] if code in ac_idx.index else None),
                    "rps15": (row.get("rps15"), rot_idx.at[code, "rps15"] if code in rot_idx.index else None),
                })
    return result


def _compare_layer2(replayed: pd.DataFrame, formal: dict[str, Any]) -> dict[str, Any]:
    """行业：RPS15 数值一致 + confirmation_status 状态一致。"""
    result: dict[str, Any] = {"matched": 0, "mismatched": 0, "mismatch_examples": []}
    if replayed.empty:
        result["note"] = "replayed empty"
        return result
    conf = formal.get("confirmation")
    if conf is None:
        result["note"] = "formal confirmation missing"
        return result

    rp = replayed.set_index("entity_code")
    conf_idx = conf.set_index("industry_code")
    for code, row in rp.iterrows():
        if code not in conf_idx.index:
            result["mismatched"] += 1
            continue
        exp = conf_idx.at[code, "strength_level"]
        act = row.get("confirmation_status", "")
        ok_state = (str(act) == str(exp))
        ok_rps = _compare_float(row.get("rps15"), conf_idx.at[code, "RPS15"])
        if ok_state and ok_rps:
            result["matched"] += 1
        else:
            result["mismatched"] += 1
            if len(result["mismatch_examples"]) < 10:
                result["mismatch_examples"].append({
                    "code": code,
                    "confirmation_status": (act, exp),
                    "rps15": (row.get("rps15"), conf_idx.at[code, "RPS15"]),
                })
    return result


def _selection_entity_map(selection: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """从正式 selection JSON 提取 entity_code → (selection_status, recommended_action)。

    兼容两种结构：
      - 新（v0.5.0 recommendation）：buckets[].themes[] 含 recommendation.etf/stocks +
        watchlist.etf/stocks + monitoring（stock_watchlist 透传）
      - 旧（v0.4.3 selection）：buckets[].themes[] 含 stock_watchlist + core_etf/sub_industry_etf
    """
    out: dict[str, dict[str, Any]] = {}
    layer3 = (selection or {}).get("layer3") or {}
    action = (layer3.get("action") or {}).get("level", "WAIT")
    for b in layer3.get("buckets", []):
        for t in b.get("themes", []):
            if "recommendation" in t or "watchlist" in t:
                # 新结构：个股 = monitoring（全量 watchlist 透传）+ recommendation.stocks + watchlist.stocks
                wl = t.get("monitoring") or t.get("stock_watchlist") or {}
                for tier in ("leaders", "high_beta", "equipment"):
                    for a in wl.get(tier, []):
                        out[f"stock:{a.get('code')}"] = {
                            "selection_status": a.get("selection_status", ""),
                            "recommended_action": action,
                        }
                for a in t.get("watchlist", {}).get("stocks", []):
                    key = f"stock:{a.get('code')}"
                    if key not in out:
                        out[key] = {
                            "selection_status": a.get("selection_status", "") or a.get("state", ""),
                            "recommended_action": action,
                        }
                # ETF = recommendation.etf（推荐）+ watchlist.etf（观察，附原因）
                etfs = list(t.get("recommendation", {}).get("etf", []))
                etfs += t.get("watchlist", {}).get("etf", [])
                for a in etfs:
                    out[f"etf:{a.get('code')}"] = {
                        "selection_status": a.get("state", "") or a.get("selection_status", ""),
                        "recommended_action": action,
                    }
                # recommendation.stocks 中的推荐个股若不在 monitoring，补录
                for a in t.get("recommendation", {}).get("stocks", []):
                    key = f"stock:{a.get('code')}"
                    if key not in out:
                        out[key] = {
                            "selection_status": a.get("selection_status", ""),
                            "recommended_action": action,
                        }
            else:
                # 旧结构
                for tier in ("leaders", "high_beta", "equipment"):
                    for a in t.get("stock_watchlist", {}).get(tier, []):
                        out[f"stock:{a.get('code')}"] = {
                            "selection_status": a.get("selection_status", ""),
                            "recommended_action": action,
                        }
                for a in t.get("core_etf", []) + t.get("sub_industry_etf", []):
                    out[f"etf:{a.get('code')}"] = {
                        "selection_status": a.get("state", ""),
                        "recommended_action": action,
                    }
    return out


def _compare_layer3(replayed: pd.DataFrame, formal: dict[str, Any]) -> dict[str, Any]:
    """Selection：entity 级 selection_status + recommended_action 一致。"""
    result: dict[str, Any] = {"matched": 0, "mismatched": 0, "mismatch_examples": []}
    if replayed.empty:
        result["note"] = "replayed empty"
        return result
    selection = formal.get("selection")
    if not selection:
        result["note"] = "formal selection missing"
        return result
    expected = _selection_entity_map(selection)

    for _, row in replayed.iterrows():
        key = f"{row['entity_type']}:{row['entity_code']}"
        if key not in expected:
            continue  # 重放覆盖范围与正式 selection 不完全一致时，只对比双方都有 key
        exp = expected[key]
        ok_sel = (str(row.get("selection_status", "")) == str(exp["selection_status"]))
        ok_act = (str(row.get("recommended_action", "")) == str(exp["recommended_action"]))
        if ok_sel and ok_act:
            result["matched"] += 1
        else:
            result["mismatched"] += 1
            if len(result["mismatch_examples"]) < 10:
                result["mismatch_examples"].append({
                    "key": key,
                    "selection_status": (row.get("selection_status", ""), exp["selection_status"]),
                    "recommended_action": (row.get("recommended_action", ""), exp["recommended_action"]),
                })
    return result


def check_parity(
    trade_date: str,
    replayed: pd.DataFrame,
    *,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    """对单个 trade_date 校验重放与正式产物的一致性。

    Returns:
        {trade_date, ok, layers: {layer1, layer2, layer3}}
    """
    formal = _load_formal_products(trade_date)
    l1 = replayed[replayed["layer"] == "1"] if not replayed.empty else pd.DataFrame()
    l2 = replayed[replayed["layer"] == "2"] if not replayed.empty else pd.DataFrame()
    l3 = replayed[replayed["layer"] == "3"] if not replayed.empty else pd.DataFrame()

    layer1 = _compare_layer1(l1, formal)
    layer2 = _compare_layer2(l2, formal)
    layer3 = _compare_layer3(l3, formal)

    # 只有「重放有行 且 正式产物存在」的层才参与验收；缺正式产物的层标记 not_checked
    l1_checked = (not l1.empty) and formal.get("rotation") is not None and formal.get("account_candidates") is not None
    l2_checked = (not l2.empty) and formal.get("confirmation") is not None
    l3_checked = (not l3.empty) and formal.get("selection") is not None
    layer1["checked"] = l1_checked
    layer2["checked"] = l2_checked
    layer3["checked"] = l3_checked

    checks: list[bool] = []
    for layer, checked in ((layer1, l1_checked), (layer2, l2_checked), (layer3, l3_checked)):
        if checked:
            checks.append(layer.get("mismatched", 1) == 0 and layer.get("matched", 0) > 0)
    ok = all(checks) if checks else False
    report = {
        "trade_date": trade_date,
        "ok": ok,
        "rule_version": replayed["rule_version"].iloc[0] if not replayed.empty else "",
        "config_hash": replayed["config_hash"].iloc[0] if not replayed.empty else "",
        "layers": {"layer1": layer1, "layer2": layer2, "layer3": layer3},
    }

    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"replay_validation_{trade_date}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        logger.info("parity validation saved: %s", path)
    return report


def format_report(report: dict[str, Any]) -> str:
    lines = [
        f"trade_date : {report['trade_date']}",
        f"parity     : {'PASS' if report['ok'] else 'FAIL'}",
    ]
    for k, v in report.get("layers", {}).items():
        lines.append(f"  {k}: matched={v.get('matched', 0)} mismatched={v.get('mismatched', 0)}"
                     f"{'  ' + v.get('note', '') if v.get('note') else ''}")
    return "\n".join(lines)
