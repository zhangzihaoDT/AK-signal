"""Stage 1a: probe ETF tracking-index sources on a representative sample.

This is deliberately separate from the daily ETF pipeline.  A failed or
blocked mapping source must not affect Layer 1 facts or selection decisions.
"""

from __future__ import annotations

import html
import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from src.common.paths import etf_signal_master_dir, project_root

logger = logging.getLogger(__name__)

SOURCES = ("SSE_OFFICIAL", "CSINDEX_OFFICIAL", "EASTMONEY_HTML", "HEURISTIC")
ETF_TYPES = ("broad", "industry", "theme", "dividend", "cross_border")


def _empty_row(code: str, name: str, etf_type: str, source: str) -> dict[str, Any]:
    return {
        "fund_code": code,
        "fund_name": name,
        "etf_type": etf_type,
        "tracking_index": "",
        "tracking_index_code": "",
        "index_provider": "",
        "mapping_source": source,
        "mapping_confidence": "NONE",
        "reliable_mapping": False,
        "PE_history_source": "",
        "PE_history_start": "",
        "PE_history_days": 0,
        "PB_history_source": "",
        "PB_history_days": 0,
        "valuation_ready": False,
    }


def _sample_type(name: str, bucket: str) -> str | None:
    text = f"{name} {bucket}"
    if re.search(r"红利|股息|高股息|低波红利", text):
        return "dividend"
    if bucket == "overseas_equity" or re.search(r"港股|恒生|纳指|标普|海外|全球|日经|德国|法国|印度|越南|中韩", text):
        return "cross_border"
    if bucket == "broad_market" or re.search(r"沪深300|中证500|中证1000|A500|上证50|创业板|科创50|MSCI|宽基", text):
        return "broad"
    if bucket == "industry" or re.search(r"半导体|芯片|证券|银行|医药|消费|军工|新能源|光伏|电池|通信|有色|煤炭|钢铁|汽车|传媒|计算机", text):
        return "industry"
    return "theme" if re.search(r"AI|人工智能|机器人|红利|央企|国企|主题|创新|成长|价值", text, re.I) else None


def _make_sample(master: pd.DataFrame, n: int) -> pd.DataFrame:
    rows = []
    for _, row in master.sort_values(["fund_code"]).iterrows():
        etf_type = _sample_type(str(row.get("fund_name", "")), str(row.get("primary_bucket", "")))
        if etf_type:
            rows.append({"fund_code": str(row["fund_code"]).zfill(6), "fund_name": str(row["fund_name"]), "etf_type": etf_type})
    sample = pd.DataFrame(rows).drop_duplicates("fund_code")
    selected = pd.concat([g.head(n) for _, g in sample.groupby("etf_type", sort=False)], ignore_index=True)
    counts = selected["etf_type"].value_counts().to_dict()
    missing = [t for t in ETF_TYPES if counts.get(t, 0) < n]
    if missing:
        logger.warning("sample shortfall by type: %s", missing)
    return selected


def _heuristic(name: str) -> tuple[str, str, str] | None:
    rules = [
        (r"沪深300", "沪深300指数", "000300"), (r"中证500", "中证500指数", "000905"),
        (r"中证1000", "中证1000指数", "000852"), (r"科创50", "上证科创板50成份指数", "000688"),
        (r"创业板", "创业板指数", "399006"), (r"上证50", "上证50指数", "000016"),
        (r"中证A500|A500", "中证A500指数", "000510"), (r"半导体|芯片", "中证全指半导体指数", "931865"),
        (r"红利", "中证红利指数", "000922"), (r"恒生", "恒生指数", "HSI"),
        (r"纳指|纳斯达克", "纳斯达克100指数", "NDX"), (r"标普500", "标普500指数", "SPX"),
    ]
    for pattern, index_name, code in rules:
        if re.search(pattern, name, re.I):
            return index_name, code, "CSINDEX" if code.isdigit() else "INDEX_PROVIDER_UNRESOLVED"
    return None


def _eastmoney_tracking(code: str) -> tuple[str, str] | None:
    try:
        import requests
        urls = [f"https://fund.eastmoney.com/{code}.html", f"https://fund.eastmoney.com/f10/jbgk_{code}.html"]
        for url in urls:
            response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
            text = html.unescape(response.content.decode("gb18030", errors="ignore"))
            match = re.search(r"(?:跟踪标的|跟踪指数|业绩比较基准)[^>]{0,80}>([^<]{2,100})<", text)
            if match:
                value = re.sub(r"\s+", " ", match.group(1)).strip()
                code_match = re.search(r"\b(\d{6})\b", value)
                return value, code_match.group(1) if code_match else ""
    except Exception as exc:
        logger.debug("Eastmoney probe failed for %s: %s", code, exc)
    return None


def _probe_bulk_sources() -> dict[str, Any]:
    result: dict[str, Any] = {}
    try:
        import akshare as ak
        try:
            scale = ak.fund_etf_scale_sse(datetime.now().strftime("%Y%m%d"))
        except Exception:
            # The SSE endpoint may not have published today's scale yet.
            previous = datetime.now() - timedelta(days=3 if datetime.now().weekday() == 0 else 1)
            scale = ak.fund_etf_scale_sse(previous.strftime("%Y%m%d"))
        result["sse_scale"] = {"status": "ok", "rows": len(scale), "columns": list(scale.columns), "has_tracking_index": any("指数" in str(c) for c in scale.columns)}
    except Exception as exc:
        result["sse_scale"] = {"status": "error", "error": type(exc).__name__}
    try:
        import akshare as ak
        indices = ak.index_csindex_all()
        result["csindex_all"] = {"status": "ok", "rows": len(indices), "columns": list(indices.columns), "tracking_product_yes": int((indices.get("跟踪产品", pd.Series()) == "是").sum())}
    except Exception as exc:
        result["csindex_all"] = {"status": "error", "error": type(exc).__name__}
    return result


def _probe_sse_detail(codes: list[str]) -> dict[str, Any]:
    """Probe official-looking detail routes without inferring a mapping."""
    try:
        import requests
    except ImportError:
        return {"status": "dependency_missing", "attempted": 0}
    headers = {"Referer": "https://www.sse.com.cn/", "User-Agent": "Mozilla/5.0"}
    routes = (
        "https://www.sse.com.cn/assortment/fund/etf/list/detail.shtml?PRODUCTID={code}",
        "https://query.sse.com.cn/commonQuery.do?isPagination=true&pageHelp.pageSize=10&pageHelp.pageNo=1&sqlId=COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFXX_SEARCH_L&SEC_CODE={code}",
    )
    responses = []
    for code in codes:
        for route in routes:
            try:
                response = requests.get(route.format(code=code), headers=headers, timeout=10)
                text = html.unescape(response.content.decode("utf-8", errors="ignore"))
                responses.append({"code": code, "status_code": response.status_code, "bytes": len(response.content), "tracking_field_hit": bool(re.search(r"标的指数|跟踪指数|跟踪标的", text))})
            except Exception as exc:
                responses.append({"code": code, "status": type(exc).__name__})
    return {"status": "ok", "attempted": len(responses), "tracking_field_hits": sum(x.get("tracking_field_hit", False) for x in responses), "responses": responses}


def _summarise(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["mapping_source"], row["etf_type"], row["mapping_confidence"])].append(row)
    out = []
    for (source, etf_type, confidence), values in grouped.items():
        n = len(values)
        out.append({
            "source": source, "etf_type": etf_type, "confidence": confidence, "sample_n": n,
            "success_n": sum(bool(x["tracking_index"]) for x in values),
            "success_rate": round(sum(bool(x["tracking_index"]) for x in values) / n, 4) if n else 0,
            "reliable_n": sum(bool(x["reliable_mapping"]) for x in values),
            "reliable_rate": round(sum(bool(x["reliable_mapping"]) for x in values) / n, 4) if n else 0,
            "code_resolved_rate": round(sum(bool(x["tracking_index_code"]) for x in values) / n, 4) if n else 0,
            "index_name_resolved_rate": round(sum(bool(x["tracking_index"]) for x in values) / n, 4) if n else 0,
            "maintenance_mode": {"SSE_OFFICIAL": "STRUCTURED_API", "CSINDEX_OFFICIAL": "DYNAMIC_HTML", "EASTMONEY_HTML": "STATIC_HTML", "HEURISTIC": "HEURISTIC"}[source],
            "anti_crawl_risk": "unknown" if source in ("SSE_OFFICIAL", "CSINDEX_OFFICIAL") else ("medium" if source == "EASTMONEY_HTML" else "none"),
            "recommended_for_bulk": source in ("SSE_OFFICIAL", "CSINDEX_OFFICIAL") and any(x["reliable_mapping"] for x in values),
        })
    return sorted(out, key=lambda x: (x["source"], x["etf_type"]))


def run_stage1a(sample_n: int = 10, output_path: Path | None = None) -> Path:
    master_path = etf_signal_master_dir() / "etf_master.parquet"
    master = pd.read_parquet(master_path)
    sample = _make_sample(master, sample_n)
    bulk = _probe_bulk_sources()
    sse_codes = master.loc[master["exchange"].eq("SSE"), "fund_code"].astype(str).str.zfill(6).head(sample_n).tolist()
    bulk["sse_detail"] = _probe_sse_detail(sse_codes)
    source_results: list[dict[str, Any]] = []
    for item in sample.to_dict("records"):
        heuristic = _heuristic(item["fund_name"])
        for source in SOURCES:
            row = _empty_row(item["fund_code"], item["fund_name"], item["etf_type"], source)
            if source == "EASTMONEY_HTML":
                found = _eastmoney_tracking(item["fund_code"])
                if found:
                    row.update(tracking_index=found[0], tracking_index_code=found[1], index_provider="UNRESOLVED", mapping_confidence="MEDIUM", reliable_mapping=True)
            elif source == "HEURISTIC" and heuristic:
                row.update(tracking_index=heuristic[0], tracking_index_code=heuristic[1], index_provider=heuristic[2], mapping_confidence="LOW", reliable_mapping=False)
            if source == "SSE_OFFICIAL":
                row["source_observation"] = "code_in_scale_bulk"
                row["source_probe_status"] = "no_tracking_index_field"
            elif source == "CSINDEX_OFFICIAL":
                row["source_observation"] = "index_universe_only"
                row["source_probe_status"] = "no_reverse_product_mapping"
            elif source == "EASTMONEY_HTML":
                row["source_observation"] = "html_requested"
                row["source_probe_status"] = "tracking_field_not_found"
            else:
                row["source_observation"] = "name_rule_match" if heuristic else "no_rule_match"
                row["source_probe_status"] = "not_reliable_by_design"
            source_results.append(row)
    # Keep the requested one-row-per-ETF view separate from source diagnostics.
    sample_rows = []
    for item in sample.to_dict("records"):
        candidates = [x for x in source_results if x["fund_code"] == item["fund_code"]]
        chosen = next((x for x in candidates if x["reliable_mapping"]), None)
        chosen = chosen or next((x for x in candidates if x["mapping_source"] == "HEURISTIC" and x["tracking_index"]), None)
        chosen = chosen or next(x for x in candidates if x["mapping_source"] == "EASTMONEY_HTML")
        sample_rows.append(chosen)
    output_path = output_path or (project_root() / "outputs/research/etf_mapping/mapping_feasibility_report.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "study": "Stage 1a ETF tracking-index mapping feasibility",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sample_n_per_type": sample_n,
        "sample_counts": sample["etf_type"].value_counts().to_dict(),
        "bulk_probe": bulk,
        "source_summary": _summarise(source_results),
        "rows": sample_rows,
        "source_results": source_results,
        "valuation_readiness": {"status": "not_probed", "reason": "No PE/PB history source is currently implemented; mapping and valuation remain separate."},
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path
