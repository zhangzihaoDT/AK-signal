"""Pinned-date daily batch. Existing subsystems retain all calculation policies."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import warnings
import signal

from src.common import batch_release as release

import pandas as pd

from src.common.paths import project_root, stock_metrics_dir, etf_signal_daily_dir, etf_signal_signals_dir, sw_industry_processed_dir, outputs_dir


def resolve_date(value: str = "") -> str:
    if not value:
        from src.etf_signal.cli import _default_target_date
        value = _default_target_date()
    return pd.Timestamp(value).strftime("%Y%m%d")


def steps(trade_date: str, offline: bool = False) -> list[list[str]]:
    target = ["--target-date", trade_date]
    day = ["--date", trade_date]
    acquisition = [] if offline else [["etf", "update", *target], ["industry", "update", *target]]
    return acquisition + [
        ["etf", "calculate", *day], ["industry", "calculate", *target],
        ["etf", "pipeline", *day],
        ["select", "stock-metrics", *day, *([] if offline else ["--allow-online-fetch"])],
        ["industry", "confirm", *target], ["industry", "report", *target],
        ["research", "etf-bottom", "--scan", *day],
        ["research", "etf-bottom", "--refresh-v1", *day],
        ["research", "trend-transition", "state", *day],
        ["etf", "three-lane", *day], ["select", "run", *day],
        ["opportunity-radar", "run", *day], ["run-summary", "run", *day],
        ["final-check", *day],
    ]


def check_stock_date(trade_date: str) -> None:
    path = stock_metrics_dir() / f"stock_metrics_{trade_date}.parquet"
    df = pd.read_parquet(path)
    required = {"trade_date", "source_trade_date", "data_status", "symbol", "market", "name"}
    if df.empty or not required.issubset(df.columns):
        raise ValueError("个股指标缺失或缺少日期证据，停止主题确认")
    target = pd.Timestamp(trade_date)
    bad = df[(pd.to_datetime(df["trade_date"]) != target)
             | (pd.to_datetime(df["source_trade_date"]) != target)
             | (df["data_status"] != "current")]
    for _, row in bad.drop_duplicates(["market", "symbol"]).iterrows():
        print(f"股票未对齐：{row['market']} {row['symbol']} {row['name']} "
              f"行情日={row['source_trade_date']} 状态={row['data_status']}", flush=True)
    if not bad.empty:
        raise ValueError("个股数据未完整对齐目标日，停止主题确认，避免新旧数据混用")
    audit_path = path.with_name(f"stock_update_audit_{trade_date}.json")
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    benchmark_date = audit.get("benchmark_trade_date")
    if not benchmark_date or pd.Timestamp(benchmark_date) != target:
        raise ValueError(f"沪深300基准未对齐 {trade_date}（行情日={benchmark_date}），停止主题确认")


def check_partition(path: Path, trade_date: str, column: str = "trade_date") -> None:
    df = pd.read_parquet(path)
    if df.empty or column not in df or not (pd.to_datetime(df[column]) == pd.Timestamp(trade_date)).all():
        raise ValueError(f"产物日期不一致或为空：{path.name}，目标={trade_date}")


def check_step(command: list[str], trade_date: str) -> None:
    kind = tuple(command[:2])
    if kind == ("etf", "calculate"):
        check_partition(etf_signal_daily_dir() / f"rotation_{trade_date}.parquet", trade_date)
        check_partition(etf_signal_daily_dir() / f"daily_indicators_{trade_date}.parquet", trade_date, "date")
    elif kind == ("etf", "pipeline"):
        for stem in ("watchlist", "account_candidates"):
            check_partition(etf_signal_signals_dir() / f"{stem}_{trade_date}.parquet", trade_date)
    elif kind == ("select", "stock-metrics"):
        check_stock_date(trade_date)
    elif kind == ("industry", "calculate"):
        from src.sw_industry_rps import storage
        df = storage.load_metrics(sw_industry_processed_dir())
        if df.empty or not (pd.to_datetime(df["trade_date"]) == pd.Timestamp(trade_date)).any():
            raise ValueError(f"行业指标缺少目标日 {trade_date}")
    elif kind == ("industry", "confirm"):
        for stem in ("confirmation", "tier_confirmation"):
            check_partition(sw_industry_processed_dir() / f"{stem}_{trade_date}.parquet", trade_date)
    elif kind == ("etf", "three-lane"):
        check_partition(outputs_dir() / "etf_signal" / f"three_lane_{trade_date}.parquet", trade_date)
    elif kind == ("research", "trend-transition") and command[2] == "state":
        check_partition(outputs_dir() / "research" / "trend_transition"
                        / f"trend_transition_state_{trade_date}.parquet", trade_date)
    elif kind in (("select", "run"), ("opportunity-radar", "run"), ("run-summary", "run")):
        folder, stem, date_key = {
            ("select", "run"): ("selection", "tradable_candidates", "date"),
            ("opportunity-radar", "run"): ("opportunity_radar", "opportunity_radar", "trade_date"),
            ("run-summary", "run"): ("run_summary", "run_summary", "trade_date"),
        }[kind]
        payload = json.loads((outputs_dir() / folder / f"{stem}_{trade_date}.json").read_text(encoding="utf-8"))
        actual = (payload.get("meta") or payload).get(date_key)
        if actual != trade_date:
            raise ValueError(f"{stem} 日期不一致：{actual}，目标={trade_date}")


def phase(command: list[str]) -> str:
    if command[:2] == ["research", "etf-bottom"]:
        return "观察 ETF 底部" if "--scan" in command else "更新 ETF 历史状态"
    return {
        ("etf", "update"): "更新 ETF 行情",
        ("industry", "update"): "更新行业行情",
        ("etf", "calculate"): "计算 ETF 强度",
        ("industry", "calculate"): "计算行业强度",
        ("etf", "pipeline"): "整理 ETF 趋势池与报告",
        ("select", "stock-metrics"): "更新个股行情与趋势",
        ("industry", "confirm"): "确认主题支撑",
        ("industry", "report"): "生成行业报告",
        ("research", "trend-transition"): "识别 ETF 生命周期",
        ("etf", "three-lane"): "合成 ETF 状态与报告",
        ("select", "run"): "筛选交易候选",
        ("opportunity-radar", "run"): "发现主题外机会",
        ("run-summary", "run"): "生成日报摘要",
        ("final-check",): "检查全部结果",
    }.get(tuple(command[:2]), "检查全部结果")


def step_artifacts(command: list[str], trade_date: str) -> list[Path]:
    """Stable facts used for safe resume; reports may be re-rendered by later steps."""
    kind = tuple(command[:2])
    if kind == ("etf", "calculate"):
        return [etf_signal_daily_dir() / f"rotation_{trade_date}.parquet",
                etf_signal_daily_dir() / f"daily_indicators_{trade_date}.parquet"]
    if kind == ("industry", "calculate"):
        return [sw_industry_processed_dir() / "industry_daily_metrics.csv"]
    if kind == ("etf", "pipeline"):
        return [etf_signal_signals_dir() / f"{name}_{trade_date}.parquet"
                for name in ("watchlist", "account_candidates")]
    if kind == ("select", "stock-metrics"):
        return [stock_metrics_dir() / f"stock_metrics_{trade_date}.parquet",
                stock_metrics_dir() / f"stock_update_audit_{trade_date}.json"]
    if kind == ("industry", "confirm"):
        return [sw_industry_processed_dir() / f"{name}_{trade_date}.parquet"
                for name in ("confirmation", "tier_confirmation")]
    if kind == ("research", "etf-bottom") and "--refresh-v1" in command:
        return [outputs_dir() / "research" / "etf_bottom" / "backtest_v1" / "v1_signal_daily.parquet"]
    if kind == ("research", "trend-transition"):
        return [outputs_dir() / "research" / "trend_transition" / f"trend_transition_state_{trade_date}.parquet"]
    if kind == ("etf", "three-lane"):
        return [outputs_dir() / "etf_signal" / f"three_lane_{trade_date}.parquet"]
    if kind == ("select", "run"):
        return [outputs_dir() / "selection" / f"tradable_candidates_{trade_date}.json"]
    if kind == ("opportunity-radar", "run"):
        return [outputs_dir() / "opportunity_radar" / f"opportunity_radar_{trade_date}.json"]
    if kind == ("run-summary", "run"):
        return [outputs_dir() / "run_summary" / f"run_summary_{trade_date}.json"]
    if command[0] == "final-check":
        return [outputs_dir() / "run_day" / f"final_check_{trade_date}.json"]
    return []


def run_step(command: list[str], env: dict[str, str], log: Path, title: str) -> int:
    log.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with log.open("w", encoding="utf-8") as stream:
        process = subprocess.Popen([sys.executable, str(project_root() / "src/main.py"), *command],
                                   cwd=project_root(), env=env, stdout=stream, stderr=subprocess.STDOUT)
        try:
            while True:
                try:
                    return process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    print(f"  {title} · 已运行 {time.monotonic() - started:.0f} 秒", flush=True)
        except BaseException:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise


def check_generated_reports(command: list[str], trade_date: str, started_ns: int) -> None:
    """Seeded old reports cannot stand in for a report that this attempt failed to build."""
    base = outputs_dir()
    groups = {
        ("etf", "pipeline"): [[base / "etf_signal" / f"etf_rotation_{trade_date}.html"]],
        ("etf", "three-lane"): [[base / "etf_signal" / f"etf_rotation_{trade_date}.html"]],
        ("industry", "report"): [[base / "sw_industry_rps" / f"sw_industry_rps_{trade_date}{suffix}.html"
                                   for suffix in ("", "_provisional")]],
        ("select", "run"): [[base / "selection" / f"tradable_candidates_{trade_date}.html"]],
        ("opportunity-radar", "run"): [[base / "opportunity_radar" / f"opportunity_radar_{trade_date}.html"]],
        ("run-summary", "run"): [[base / "run_summary" / f"run_summary_{trade_date}.md"]],
    }.get(tuple(command[:2]), [])
    if command[:2] == ["research", "etf-bottom"] and "--scan" in command:
        groups = [[base / "research" / "etf_bottom" / f"scan_{trade_date}.html"]]
    if command[0] == "final-check":
        groups = [[base / "run_day" / f"final_check_{trade_date}.json"]]
    for variants in groups:
        if not any(p.is_file() and p.stat().st_size and p.stat().st_mtime_ns >= started_ns for p in variants):
            raise ValueError(f"该步骤未生成本批报告：{variants[0].name}；不能沿用旧文件冒充成功")


def resume_index(state: dict, trade_date: str) -> int:
    for index, step in enumerate(state["steps"]):
        if step["status"] != "completed":
            return index
        try:
            verify_step(step["command"], trade_date, Path(step["log"]) if step.get("log") else None)
            for path, digest in step.get("artifacts", {}).items():
                if release.fingerprint(Path(path)) != digest:
                    raise ValueError("已完成步骤的事实产物发生变化")
        except (ValueError, OSError, KeyError):
            print(f"第 {index + 1} 步产物缺失或变化，从该步重新生成", flush=True)
            return index
    return len(state["steps"])


def verify_step(command: list[str], trade_date: str, log: Path | None = None) -> None:
    captured = []
    try:
        with warnings.catch_warnings(record=True) as captured:
            check_step(command, trade_date)
    finally:
        if captured and log:
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as stream:
                for warning in captured:
                    stream.write(f"validation warning: {warning.message}\n")


def save_published_checkpoint(root: Path, state: dict) -> None:
    # The publication pointer is the commit point; bookkeeping cannot undo that commit.
    try:
        release.save_checkpoint(root, state)
    except OSError as exc:
        print(f"日报已发布，运行记录保存失败：{exc}；下次恢复会核对发布回执", flush=True)


def committed_release(root: Path, state: dict) -> dict | None:
    stage = release.run_dir(root, state) / "outputs"
    public = root / "outputs"
    if not public.is_symlink() or public.resolve() != stage.resolve():
        return None
    try:
        publication = json.loads((stage / "run_day" / "publication.json").read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return publication if publication.get("run_id") == state["run_id"] else None


def validate_release(trade_date: str) -> dict:
    """A complete report set and a successful final check are mandatory for publication."""
    base = outputs_dir()
    paths = [
        base / "etf_signal" / f"etf_rotation_{trade_date}.html",
        base / "selection" / f"tradable_candidates_{trade_date}.html",
        base / "opportunity_radar" / f"opportunity_radar_{trade_date}.html",
        base / "run_summary" / f"run_summary_{trade_date}.md",
        base / "research" / "etf_bottom" / f"scan_{trade_date}.html",
    ]
    industry = base / "sw_industry_rps" / f"sw_industry_rps_{trade_date}.html"
    provisional = industry.with_name(f"sw_industry_rps_{trade_date}_provisional.html")
    paths.append(industry if industry.exists() else provisional)
    for path in paths:
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"报告未完整生成：{path.name}")
    receipt = json.loads((base / "run_day" / f"final_check_{trade_date}.json").read_text(encoding="utf-8"))
    if not receipt.get("ok") or receipt.get("trade_date") != trade_date:
        raise ValueError("最终验收未通过，日报保持上一完整版本")
    summary = json.loads((base / "run_summary" / f"run_summary_{trade_date}.json").read_text(encoding="utf-8"))
    if summary.get("missing"):
        raise ValueError(f"日报摘要缺少输入：{summary['missing']}")
    return receipt


def show_status(root: Path, trade_date: str = "", offline: bool = False) -> None:
    state = release.read_checkpoint(root, trade_date, offline) if trade_date else None
    if not trade_date:
        path = root / ".run_day" / "latest.json"
        state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    if not state:
        print("尚无 run-day 批次记录")
        return
    index = state["current_step"]
    title = phase(state["steps"][index]["command"]) if index < len(state["steps"]) else "统一发布日报"
    if state["status"] == "preparing":
        title = "准备批次与暂存目录"
    labels = {"preparing": "准备中", "running": "运行中", "failed": "失败，可恢复",
              "interrupted": "已中断，可恢复", "publishing": "发布中", "published": "已发布"}
    print(f"信号日 {state['trade_date']} · {labels.get(state['status'], state['status'])}")
    print(f"进度 {index + 1}/{len(state['steps']) + 1} · {title}")
    if state["status"] == "running" and index < len(state["steps"]):
        started = state["steps"][index].get("started_at")
        if started:
            elapsed = (pd.Timestamp.now(tz="UTC") - pd.Timestamp(started)).total_seconds()
            print(f"本阶段已运行 {elapsed:.0f} 秒")
    if state.get("error"):
        print("原因：" + state["error"])
    print("批次记录：" + str(release.run_dir(root, state) / "state.json"))


def execute_batch(root: Path, trade_date: str, offline: bool, *, resume: bool = False, restart: bool = False) -> int:
    commands = steps(trade_date, offline)
    existing = None if restart else release.read_checkpoint(root, trade_date, offline)
    if resume and not existing:
        raise ValueError("没有可恢复的批次，直接运行 make run-day 即可")
    continuing = existing is not None and not restart and (resume or existing["status"] != "published")
    state = existing if continuing else release.new_checkpoint(root, trade_date, offline, commands)
    publication = committed_release(root, state)
    if state["status"] != "published" and publication:
        state.update(status="published", finished_at=publication["published_at"], result=publication["result"])
        save_published_checkpoint(root, state)
    if state["status"] == "published":
        print(f"信号日 {trade_date} 的日报已发布，无需重复恢复")
        return 0
    if state["signature"] != release.implementation_signature(root) or [s["command"] for s in state["steps"]] != commands:
        raise ValueError("代码或配置已变化，不能复用旧检查点；请添加 --restart 重新运行")
    try:
        if not state.get("prepared"):
            print("准备批次：隔离本轮报告，已发布日报继续可读", flush=True)
        stage = release.prepare_workspace(root, state)
        env = {**os.environ, "AKSIGNAL_TRADE_DATE": trade_date,
               "AKSIGNAL_OFFLINE": "1" if offline else "0", "AKSIGNAL_OUTPUTS_DIR": str(stage)}
        with release.output_workspace(stage):
            start = resume_index(state, trade_date) if continuing else 0
            if continuing:
                print(f"从第 {start + 1} 步恢复，复用前 {start} 步结果", flush=True)
            for item in state["steps"][start:]:
                item["status"] = "pending"
            total = len(commands) + 1
            for index in range(start, len(commands)):
                command = commands[index]
                item = state["steps"][index]
                title = f"[{index + 1}/{total}] {phase(command)}"
                print(title, flush=True)
                state.update(status="running", current_step=index, pid=os.getpid(), error="")
                item.update(status="running", attempts=item["attempts"] + 1, started_at=release.now())
                log = release.run_dir(root, state) / "logs" / f"{index + 1:02d}-attempt-{item['attempts']}.log"
                item["log"] = str(log)
                release.save_checkpoint(root, state)
                started = time.monotonic()
                started_ns = time.time_ns()
                code = run_step(command, env, log, title)
                if code:
                    raise ValueError(f"{phase(command)}失败（退出码 {code}），详细日志：{log}")
                verify_step(command, trade_date, log)
                check_generated_reports(command, trade_date, started_ns)
                item["artifacts"] = {str(p): release.fingerprint(p) for p in step_artifacts(command, trade_date)}
                item.update(status="completed", elapsed_s=round(time.monotonic() - started, 1))
                state["current_step"] = index + 1
                release.save_checkpoint(root, state)
                print(f"  已完成 · {item['elapsed_s']} 秒", flush=True)
            print(f"[{total}/{total}] 检查通过，统一发布日报", flush=True)
            state.update(status="publishing", current_step=len(commands), error="")
            release.save_checkpoint(root, state)
            receipt = validate_release(trade_date)
            if release.implementation_signature(root) != state["signature"]:
                raise ValueError("运行过程中代码或配置发生变化，暂不发布；请 --restart")
            release.atomic_json(stage / "run_day" / "publication.json", {
                "run_id": state["run_id"], "trade_date": trade_date,
                "published_at": release.now(), "result": receipt,
            })
            release.publish_workspace(root, stage)
            state.update(status="published", finished_at=release.now(), result=receipt)
            save_published_checkpoint(root, state)
            print(f"日报已统一发布 · {trade_date} · {receipt['status']} / {receipt['action']}", flush=True)
            for warning in receipt.get("warnings", []):
                print("提示：" + warning, flush=True)
            return 0
    except (OSError, ValueError, KeyError, KeyboardInterrupt) as exc:
        publication = committed_release(root, state)
        if publication:
            state.update(status="published", finished_at=publication["published_at"], result=publication["result"])
            save_published_checkpoint(root, state)
            print(f"已核对发布回执：日报已发布，后续处理提示：{exc}", flush=True)
            return 0
        state.update(status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed", error=str(exc))
        index = state["current_step"]
        if index < len(state["steps"]):
            state["steps"][index]["status"] = state["status"]
        release.save_checkpoint(root, state)
        print(f"批次未发布：{exc or '运行已中断'}", flush=True)
        print(f"再次运行同日同模式命令，将从第 {index + 1} 步恢复；已发布日报保持不变", flush=True)
        return 130 if isinstance(exc, KeyboardInterrupt) else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="统一交易日、支持恢复与统一发布的每日业务批次")
    parser.add_argument("--date", default="", help="目标交易日 YYYYMMDD；缺省最近完整交易日")
    parser.add_argument("--offline", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--resume", action="store_true", help="恢复该日该模式的批次")
    mode.add_argument("--restart", action="store_true", help="创建新批次，从第一步重新运行")
    parser.add_argument("--status", action="store_true", help="查看最新或指定日期批次进度")
    args = parser.parse_args()
    root = project_root()
    if args.status:
        show_status(root, resolve_date(args.date) if args.date else "", args.offline)
        return
    trade_date = resolve_date(args.date)
    print(f"RUN-DAY · 信号日 {trade_date} · {'离线' if args.offline else '在线'}", flush=True)
    def terminate_batch(signum, frame):
        raise KeyboardInterrupt("收到终止信号")
    previous_handler = signal.signal(signal.SIGTERM, terminate_batch)
    try:
        with release.batch_lock(root):
            sys.exit(execute_batch(root, trade_date, args.offline, resume=args.resume, restart=args.restart))
    except (ValueError, OSError) as exc:
        print(str(exc), flush=True)
        sys.exit(1)
    except KeyboardInterrupt:
        print("运行已中断，重新执行同日命令可恢复", flush=True)
        sys.exit(130)
    finally:
        signal.signal(signal.SIGTERM, previous_handler)


if __name__ == "__main__":
    main()
