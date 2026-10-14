# AKsignal Daily / mashang Scheduler — P0 离线交付

生产开关保持关闭。未修改 LaunchAgent、`.env` 或生产 Scheduler 配置，未重启 Scheduler，未更新真实在线行情，未部署 Sealos。P1 Runtime Manager 和 P2 附件交付尚未实施。

## 修改与复用

AKsignal：新增 `src/daily_schedule.py`、`src/common/trading_calendar.py`、`config/a_share_calendar.json`、调度测试与本文；修改 `src/daily_run.py`、`src/common/batch_release.py` 和原恢复测试，更新 canonical map。初次审计文档一并纳入版本管理。

mashang-service：修改 `mashang_workspace/utility_scripts/sales_scheduler.py`，新增同目录 `external_daily_jobs.py`、`external_daily_jobs.json.example`、`tests/test_external_daily_jobs.py` 和 `docs/aksignal_daily_scheduler.md`。mashang-hub 未修改。

Service Scheduler 每分钟检查可选外部任务注册，非阻塞启动短控制入口。AKsignal 再异步调用原有 `make run-day[-offline] RUN_DATE=... RUN_FLAGS=--require-complete`。控制入口不等待业务批次，不占用原 08:50 Daily 执行时隙。原 due_actions、Daily 顺序和 key-day 策略未改。

复用原生步骤、checkpoint、代码/config 签名、flock、隔离输出与原子发布。未改 Market Facts 计算、指标、日报 UI 或 Strategy。

## 新接口与规则

- `python -m src.daily_schedule --plan --offline`：只读规则与目标日，不写记录。
- `python -m src.daily_schedule --tick --offline`：一次资格检查，必要时异步启动缓存批次。
- Runner `--require-complete`：调度发布门控；partial/unknown/失败不替换上一日报。在线新发布另要求带日期主源。
- `.run_day/scheduler/{trade_date}.json`：跨在线/离线模式持久化尝试、触发时间、进程、日志、等待/阻断/发布状态与回执；最新检查在 scheduler/latest.json。
- Service `SCHEDULER_EXTERNAL_JOBS_CONFIG`：可选本地注册路径，缺省为空。JSON example 为 enabled=false、argv 含 --offline；本轮未设置生产变量。

固定 Asia/Shanghai，每天 08:30 检查。只补最近已发生的 08:30 对应的完整 A 股交易日，不循环回填历史。10 月 14 日收盘后唤醒仍补 10 月 13 日批次；08:30 前可补前一次遗漏批次，不提前启动当日批次。

日历依据 [上交所 2025 年休市通知](https://www.sse.com.cn/disclosure/announcement/general/c/c_20241223_10767108.shtml) 与 [2026 年休市通知](https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml)，覆盖 2025-12-01 至 2026-12-31，265 个交易日。验证 SHA256、年份来源与覆盖范围。缺失、损坏或过期即 blocked，不凭星期或行情最大日期推断；进入 2027 年前需维护官方快照。

每目标最多三次尝试；观察到未发布退出后等待 30/90 分钟。预算不因午夜、周末或模式变化重置。手动业务锁占用不消耗尝试。记录/回执损坏、签名变化时阻断，不自动清空记录或 --restart。

跨模式防重核对正式指针或已发布历史 checkpoint，验证 run_id 路径、trade_date、final_check 与回执 result、三份日报。未提交暂存目录中的回执不算发布。失败恢复保留 run_id；离线复用完成步骤，未来在线调度重试重新检查增量 acquisition，避免第一次 update 无目标数据后一直跳过更新。

## 源日期门控：对原审计方案的保守调整

未进行 08:30 源端在线探测。代码发现 `sw_industry_rps/cli.py::_build_realtime_provisional` 给无独立日期证据的 prev_close 赋目标日；休市日补跑不能据此证明价格日。

因此未来在线新批次只得到 COMPLETE 实时兜底时，调度保留 checkpoint 和上一发布，等待带日期主源并有限重试。未改兜底计算或既有手动政策。离线缓存与既有正式发布保留原始状态及 warnings；CONFIRMED/ok=true 不等于 data_complete=true。开放自动在线兜底发布前，需补齐并验证独立源交易日证据。

## 离线验收证据

隔离目录：`/private/tmp/aksignal-p0-offline-e2e`。复制现有缓存、拦截 Python socket 连接，未将原仓库 data/ 与正式发布作为可写测试空间。

1. Service ExternalDailyJobs → AKsignal 控制入口 → 原生 make run-day-offline。受控时钟 2026-10-13 08:30，目标 20261012，hook 返回约 0.0013 秒。
2. 五个真实缓存业务阶段与发布完成；run_id=574257a3ee9d4ceda464f723845aa8be，final_check.ok=true，status=CONFIRMED，data_complete=false。ETF 缺口、复权未统一、身份错配、位置未知警告完整保留。
3. 同目标改在线模式检查，命中同一回执，不发起在线更新或重新计算。
4. 缓存不足的 20261013 实际离线执行在 ETF 计算失败；下一检查 waiting，预算=1，next_attempt_at=2026-10-14T09:01:00+08:00。上一发布回执 SHA256 未变。
5. 网络尝试=0。证据在 acceptance.json、hook-evidence.json、.run_day/scheduler 和批次日志。
6. 原仓库离线命令命中既有发布 e6df8bfc64b844db8ff2f01c61d37ab7，没有重算/重发。

测试覆盖节假日、调休周六、时区/收盘边界、日历损坏/过期、重启/休眠补跑、双锁、跨模式预算/去重、未提交回执、partial 阻断、fallback 警告、恢复与源日期门控。Service 使用 verify-scope/verify 限定本次文件，显式纳入原 Scheduler 回归。首轮通用脚本因 sandbox 禁止写测试报告失败；在网络拦截和获准测试写入权限下重新验证，没有修改业务脚本规避权限错误。JSON example 未被通用 scope resolver 分类，人工核验语法、disabled 和 offline 参数。

## 剩余风险

- 生产未启用；在线端到端和 Mac 真实休眠验收待单独批准。当前休眠/重启由受控时钟和持久化恢复测试覆盖。
- 在线调度保守等待带日期主源；用尽预算或签名不兼容需维护，不无界重试。
- Scheduler 原有同步业务任务可能延后后续检查；AKsignal 本身非阻塞，不改原 Daily 行为。
- 取消与 Hub 状态映射属 P1，成果交付属 P2；本轮未实现。
- 日历需维护；没有新增依赖、数据库或常驻服务。
