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
from src.common.sleep_guard import batch_sleep_guard

import pandas as pd

from src.common.paths import project_root, sw_industry_raw_dir, stock_metrics_dir, etf_signal_daily_dir, etf_signal_signals_dir, sw_industry_processed_dir, outputs_dir


def resolve_date(value: str = "") -> str:
    if not value:
        from src.common.trading_calendar import default_calendar, shanghai_now
        value = default_calendar(project_root()).complete_day(shanghai_now())
    return pd.Timestamp(value).strftime("%Y%m%d")


def steps(trade_date: str, offline: bool = False) -> list[list[str]]:
    target = ["--target-date", trade_date]
    day = ["--date", trade_date]
    acquisition = [] if offline else [["etf", "update", *target], ["industry", "update", *target]]
    plan = acquisition + [
        ["etf", "calculate", *day], ["industry", "calculate", *target],
        ["observation", "stock-metrics", *day, *([] if offline else ["--allow-online-fetch"])],
        ["observation", "build", *day], ["observation", "check", *day],
    ]
    from src.market_facts.policy import source_policy
    if source_policy().get('industry',{}).get('provider') == 'tdx':
        # TDX acquisition/calculation is owned by observation build; SW is research-only.
        plan = [c for c in plan if c[0] != 'industry']
    return plan




def check_stock_date(trade_date: str) -> None:
    path = stock_metrics_dir() / f"stock_metrics_{trade_date}.parquet"
    df = pd.read_parquet(path)
    required = {"trade_date", "source_trade_date", "data_status", "symbol", "market", "name"}
    if df.empty or not required.issubset(df.columns):
        raise ValueError("个股指标缺失或缺少日期证据，停止观察发布")
    target = pd.Timestamp(trade_date)
    from src.market_facts.policy import aligned_stock_rows
    bad = df[~aligned_stock_rows(df,target)]
    for _, row in bad.drop_duplicates(["market", "symbol"]).iterrows():
        print(f"股票未对齐：{row['market']} {row['symbol']} {row['name']} "
              f"行情日={row['source_trade_date']} 状态={row['data_status']}", flush=True)
    if not bad.empty:
        raise ValueError("个股数据未完整对齐目标日，停止观察发布，避免新旧数据混用")
    audit_path = path.with_name(f"stock_update_audit_{trade_date}.json")
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    benchmark_date = audit.get("benchmark_trade_date")
    if not benchmark_date or pd.Timestamp(benchmark_date) != target:
        raise ValueError(f"沪深300基准未对齐 {trade_date}（行情日={benchmark_date}），停止观察发布")


def check_partition(path: Path, trade_date: str, column: str = "trade_date") -> None:
    df = pd.read_parquet(path)
    if df.empty or column not in df or not (pd.to_datetime(df[column]) == pd.Timestamp(trade_date)).all():
        raise ValueError(f"产物日期不一致或为空：{path.name}，目标={trade_date}")


def industry_target_coverage(trade_date: str) -> tuple[int, int, list[str]]:
    from src.sw_industry_rps import storage
    from src.sw_industry_rps.cli import validate_raw_completeness
    raw = sw_industry_raw_dir()
    try:
        active, _, _ = storage.compute_active_codes(raw)
        covered, expected, missing = validate_raw_completeness(raw, trade_date, active)
        return covered, expected, missing
    except (OSError, ValueError, KeyError, TypeError):
        return 0, 0, []


def check_step(command: list[str], trade_date: str) -> None:
    kind = tuple(command[:2])
    if kind == ("industry", "update"):
        covered, expected, _ = industry_target_coverage(trade_date)
        if not expected or covered != expected:
            raise ValueError(
                f"行业目标日 {trade_date} 带日期价格尚未就绪（已核验 {covered}/{expected}）；"
                "主源或历史源需补齐目标日行情，无日期实时价格不能替代")
    elif kind == ("etf", "calculate"):
        check_partition(etf_signal_daily_dir() / f"rotation_{trade_date}.parquet", trade_date)
        check_partition(etf_signal_daily_dir() / f"daily_indicators_{trade_date}.parquet", trade_date, "date")
    elif kind == ("observation", "stock-metrics"):
        check_stock_date(trade_date)
    elif kind == ("industry", "calculate"):
        from src.sw_industry_rps import storage
        df = storage.load_metrics(sw_industry_processed_dir())
        if df.empty or not (pd.to_datetime(df["trade_date"]) == pd.Timestamp(trade_date)).any():
            raise ValueError(f"行业指标缺少目标日 {trade_date}")
    elif kind == ("observation", "build"):
        from src.observation.cli import validate
        validate(trade_date)



def phase(command: list[str]) -> str:
    return {
        ("etf", "update"): "更新 ETF 行情", ("industry", "update"): "更新行业行情",
        ("etf", "calculate"): "计算 ETF 强度", ("industry", "calculate"): "计算行业轮动",
        ("observation", "stock-metrics"): "更新观察池个股趋势",
        ("observation", "build"): "构建趋势与底部位置观察",
        ("observation", "check"): "验收观察事实",
    }.get(tuple(command[:2]), "检查全部结果")


def step_artifacts(command: list[str], trade_date: str) -> list[Path]:
    kind = tuple(command[:2])
    if kind == ("etf", "calculate"):
        return [etf_signal_daily_dir() / f"rotation_{trade_date}.parquet",
                etf_signal_daily_dir() / f"daily_indicators_{trade_date}.parquet"]
    if kind == ("industry", "calculate"):
        return [sw_industry_processed_dir() / "industry_daily_metrics.csv"]
    if kind == ("observation", "stock-metrics"):
        paths = [stock_metrics_dir() / f"stock_metrics_{trade_date}.parquet",
                 stock_metrics_dir() / f"stock_update_audit_{trade_date}.json"]
        from src.market_facts.policy import source_policy
        if source_policy().get("stocks",{}).get("provider") == "longbridge":
            paths.append(stock_metrics_dir() / f"stock_position_inputs_{trade_date}.parquet")
        return paths
    if kind == ("observation", "build"):
        folder = outputs_dir() / "observation"
        return [folder / f"{stem}_{trade_date}.parquet" for stem in
                ("etf_trends", "asset_directions", "industry_rotation", "industry_directions", "stock_positions", "etf_positions")]
    if kind == ("observation", "check"):
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
    kind = tuple(command[:2])
    paths = []
    if kind == ("observation", "build"):
        paths = [outputs_dir()/"observation"/f"observation_{trade_date}{suffix}" for suffix in (".html", ".md", ".json")]
    elif kind == ("observation", "check"):
        paths = [outputs_dir()/"run_day"/f"final_check_{trade_date}.json"]
    for path in paths:
        if not path.is_file() or not path.stat().st_size or path.stat().st_mtime_ns < started_ns:
            raise ValueError(f"该步骤未生成本批报告：{path.name}；不能沿用旧文件冒充成功")


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
    for suffix in (".html", ".md", ".json"):
        path = outputs_dir()/"observation"/f"observation_{trade_date}{suffix}"
        if not path.is_file() or not path.stat().st_size:
            raise ValueError(f"报告未完整生成：{path.name}")
    from src.observation.cli import validate
    return validate(trade_date)


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


def execute_batch(root: Path, trade_date: str, offline: bool, *, resume: bool = False, restart: bool = False,
                  require_complete: bool = False) -> int:
    if not restart:
        published = release.published_batch(root, trade_date)
        if published:
            published_status = published['result'].get('status')
            if published_status == 'PARTIAL':
                # A manual rerun may upgrade a partial observation once its
                # missing module has recovered; the previous release remains
                # active until a replacement batch commits atomically.
                pass
            elif require_complete and published_status not in ('CONFIRMED', 'COMPLETE'):
                raise ValueError('该日既有发布不满足完整性门控，需显式维护，不能自动重发')
            else:
                # Reconcile bookkeeping interrupted after the atomic pointer commit.
                path = release.run_dir(root, published) / 'state.json'
                if path.exists():
                    state = json.loads(path.read_text())
                    if state.get('status') != 'published':
                        state.update(status='published', finished_at=published['published_at'], result=published['result'])
                        save_published_checkpoint(root, state)
                print(f"信号日 {trade_date} 已正式发布（跨模式核对 run_id={published['run_id']}），不重复执行", flush=True)
                return 0
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
            # Waiting for fresh data must recheck acquisition: a successful
            # update with no target bars cannot be frozen across scheduled retries.
            # Keep the same run_id/checkpoint; this is not an implicit restart.
            if continuing and require_complete and not offline:
                start = 0
            if continuing:
                print(f"从第 {start + 1} 步恢复，复用前 {start} 步结果", flush=True)
            for item in state["steps"][start:]:
                item["status"] = "pending"
            state.setdefault("degraded_modules", {})
            from src.market_facts.policy import source_policy
            state['degraded_modules'].pop('industry_rotation', None)
            industry_update_index = next((i for i, command in enumerate(commands)
                                          if tuple(command[:2]) == ("industry", "update")), None)
            industry_degraded = bool(offline or (industry_update_index is not None and start > industry_update_index))
            if industry_degraded:
                covered, expected, _ = industry_target_coverage(trade_date)
                industry_degraded = not expected or covered != expected
                if not industry_degraded:
                    state["degraded_modules"].pop("industry_rotation", None)
            total = len(commands) + 1
            for index in range(start, len(commands)):
                command = commands[index]
                item = state["steps"][index]
                title = f"[{index + 1}/{total}] {phase(command)}"
                kind = tuple(command[:2])
                if kind == ("industry", "calculate") and industry_degraded:
                    reason = state["degraded_modules"].get("industry_rotation", {}).get(
                        "reason", "industry update 未获得完整、已核验目标日价格")
                    log = release.run_dir(root, state) / "logs" / f"{index + 1:02d}-skipped-degraded.log"
                    log.parent.mkdir(parents=True, exist_ok=True)
                    log.write_text(f"SKIPPED: industry_rotation unavailable: {reason}\n", encoding="utf-8")
                    item.update(status="skipped_degraded", log=str(log), error=reason)
                    item.pop("artifacts", None)
                    state["current_step"] = index + 1
                    release.save_checkpoint(root, state)
                    print(f"{title} · 跳过，行业模块降级交付", flush=True)
                    continue
                print(title, flush=True)
                state.update(status="running", current_step=index, pid=os.getpid(), error="")
                item.update(status="running", attempts=item["attempts"] + 1, started_at=release.now())
                log = release.run_dir(root, state) / "logs" / f"{index + 1:02d}-attempt-{item['attempts']}.log"
                item["log"] = str(log)
                release.save_checkpoint(root, state)
                started = time.monotonic()
                started_ns = time.time_ns()
                try:
                    code = run_step(command, env, log, title)
                    if code:
                        raise ValueError(f"{phase(command)}失败（退出码 {code}），详细日志：{log}")
                    verify_step(command, trade_date, log)
                except (OSError, ValueError, KeyError) as exc:
                    if kind not in {("industry", "update"), ("industry", "calculate")}:
                        raise
                    industry_degraded = True
                    reason = str(exc)
                    state["degraded_modules"]["industry_rotation"] = {
                        "status": "unavailable", "trade_date": trade_date, "reason": reason,
                    }
                    item.update(status="degraded", error=reason, elapsed_s=round(time.monotonic() - started, 1))
                    item.pop("artifacts", None)
                    state["current_step"] = index + 1
                    release.save_checkpoint(root, state)
                    print(f"  行业模块降级 · {reason}", flush=True)
                    continue
                check_generated_reports(command, trade_date, started_ns)
                item["artifacts"] = {str(p): release.fingerprint(p) for p in step_artifacts(command, trade_date)}
                item.update(status="completed", elapsed_s=round(time.monotonic() - started, 1))
                if kind == ("industry", "update"):
                    state["degraded_modules"].pop("industry_rotation", None)
                state["current_step"] = index + 1
                release.save_checkpoint(root, state)
                print(f"  已完成 · {item['elapsed_s']} 秒", flush=True)
            print(f"[{total}/{total}] 检查通过，统一发布日报", flush=True)
            state.update(status="publishing", current_step=len(commands), error="")
            release.save_checkpoint(root, state)
            receipt = validate_release(trade_date)
            if require_complete and (receipt.get("ok") is not True or receipt.get("errors")
                                     or receipt.get("status") not in ("CONFIRMED", "COMPLETE")):
                raise ValueError("目标日数据尚未完整就绪，保持上一发布；请在数据就绪后手动恢复（不会自动重试）")
            if receipt.get('status') == 'COMPLETE' and receipt.get('industry_price_dates_verified') is not True:
                raise ValueError('行业兜底缺少独立源交易日验收证据；请在数据就绪后手动恢复（不会自动重试）')
            if release.implementation_signature(root) != state["signature"]:
                raise ValueError("运行过程中代码或配置发生变化，暂不发布；请 --restart")
            release.atomic_json(stage / "run_day" / "publication.json", {
                "run_id": state["run_id"], "trade_date": trade_date,
                "published_at": release.now(), "result": receipt,
            })
            release.publish_workspace(root, stage)
            state.update(status="published", finished_at=release.now(), result=receipt)
            save_published_checkpoint(root, state)
            print(f"日报已统一发布 · {trade_date} · {receipt['status']} · 观察事实", flush=True)
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
        if require_complete and not offline:
            print("再次运行同日同模式命令，将从第 1 步重新获取数据并重新验收；已发布日报保持不变", flush=True)
        else:
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
    parser.add_argument("--require-complete", action="store_true", help="调度发布只接受 confirmed/complete 验收")
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
        with release.batch_lock(root), batch_sleep_guard():
            sys.exit(execute_batch(root, trade_date, args.offline, resume=args.resume, restart=args.restart,
                                   require_complete=args.require_complete))
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
