# AKsignal Daily → mashang-hub：现状审计与最小方案

> Historical audit / initial proposal. Its Service Scheduler external-hook design has been superseded by the AKsignal-owned LaunchAgent in [DAILY_SCHEDULER.md](DAILY_SCHEDULER.md).

审计日期：2026-10-14（Asia/Shanghai）。本轮只读审计、既有离线单元测试；未修改业务代码、生产配置、Scheduler、Worker 或部署，未请求在线行情。

## 实际基线

- AKsignal VERSION=0.5.0；`src/daily_run.py` 在线七个业务步骤加发布，离线五个业务步骤加发布。仅运行 Market Facts，不运行旧 Selection、Tier、Radar 等。
- Makefile 的 run-day、run-day-offline、run-day-status 和 RUN_FLAGS=--resume 均有实际入口。
- `src/common/batch_release.py` 提供非阻塞 flock 全局锁、按日期/在线模式检查点、步骤摘要、代码/config 签名、隔离暂存和 outputs 符号链接原子切换。失败不改变上一发布。
- 默认日期 `src/etf_signal/cli.py::_default_target_date` 使用宿主 datetime、16:30 截止，仅排除周末；不能直接用于 A 股节假日调度。
- published 检查点可避免同日同模式重跑；在线/离线检查点不同。跨模式、跨历史发布的去重需要核对正式发布索引和回执。
- 当前 outputs 指向 `.run_day/runs/20261012/e6df8bfc64b844db8ff2f01c61d37ab7/outputs`。publication 为该日期/批次，result.ok=true、status=CONFIRMED、data_complete=false；含 ETF 缺口、复权未统一、身份错配和位置变化未知等 warnings。CONFIRMED 不等于无警告。
- observation JSON 和 final_check 有 trade_date，但没有 run_id；publication 有 run_id。交付需通过固定批次路径和文件哈希绑定，不要求重写观察报告 UI。

## mashang 实际实现

- `mashang-service/mashang_workspace/utility_scripts/sales_scheduler.py`：标准库常驻分钟循环；08:50 Daily，key day 傍晚监控；datetime.now 无显式时区，fired 仅内存；精确分钟触发、同步 subprocess.run，无遗漏补跑机制，无通用任务注册配置。
- 实际 Scheduler 进程存在，参数 `--series CM3`；2026-10-14 的 Scheduler 日志存在。Mac Worker 进程存在。
- Worker LaunchAgent 未声明 MASHANG_RUNTIME_CONFIG 或 MASHANG_WORKSPACES_CONFIG；读取到 MASHANG_SERVICE_ROOT。不能把源码存在等同于已部署的云端行为。
- `mashang-hub/worker/runtime/registry.mjs` 本地声明服务、任务和操作，允许可选 JSON 覆盖；Daily 已有确定性 make data-pipeline 操作，声明 30 分钟超时。
- status.mjs 的业务运行状态目前读取 scheduler-log 或手动 operation 记录，没有 checkpoint JSON probe。
- Runtime 控制协议只有 up/down/restart/run。Resume 需独立声明操作或增加通用动作契约，优先前者。
- control.mjs 的 operation 取消只 kill 直接子进程；服务停止支持进程组。AKsignal 自身会清理直接计算子进程，但 Worker 直接调用 make 时仍需处理整条进程树。
- server/runtime-control.mjs 默认 5 分钟，可按 operation 建议延长；上限 1 小时，环境 CONTROL_TIMEOUT_MS 可覆盖建议。长任务不能只改 operation.timeoutMs 就宣称问题已解决。
- Worker 已有 artifact.created、摘要哈希校验和按需 artifact.response；artifact-resolver.mjs 支持 md/html/csv/png，不支持 json；同时检查 lexical 与 realpath 白名单，默认 outputs 路径不会授权 `.run_day` 下的实际文件。
- Hub AGENTS.md 明确要求业务执行在 Worker/业务仓库，Hub 仅做通用控制；此次遵循此边界。
- AKsignal 与 mashang-hub 工作区干净；mashang-service 有既存用户改动，后续按本次文件限定验证/提交，不纳入其他改动。

## 最小实施方案（待审阅基线）

### P0：调度和批次保护

1. 在现有 Scheduler 增加默认关闭的外部确定性任务注册；AKsignal 08:30、ZoneInfo(Asia/Shanghai)，保持原 Daily 配置与行为。
2. AKsignal 提供薄 Daily 接入入口：用可靠、可离线读取且带覆盖范围的 A 股日历固定最近完整交易日；日历缺失或过期显式拒绝，不能只凭星期推断。仍调用原 run-day，不复制 Pipeline。
3. Scheduler 非阻塞启动 AKsignal，并持久化触发记录；启动/唤醒后检查最近应完成的目标批次，不自动逐日回填历史。单目标防重、原生业务锁、已发布回执去重；失败保留 checkpoint，有限重试和退避，不自动 --restart。
4. 08:30 仅代表触发时间，不承诺此时所有来源就绪。利用目标日期证据和现有验收；主源未就绪时允许已有完整 fallback 并显式 COMPLETE/FALLBACK_SOURCE；陈旧或 partial 不作为本次正式交付。源未就绪保留状态并延后重试。
5. 首轮离线场景覆盖周末/节假日、重启/休眠恢复、同日重复、跨模式发布和失败恢复；生产开关保持关闭。

### P1：运行状态与控制

1. AKsignal 新增只读 JSON status：run_id、trade_date、status、phase、completed/total、error、恢复兼容性、pid/活性、最近发布。Scheduler 状态来自独立调度记录，不能伪装为 checkpoint 自带字段。
2. Worker 注册 AKsignal Run / Resume / Status / Logs；Restart 单列维护操作。运行、恢复均调用原生 Runner 并共享 flock。
3. 通用 checkpoint JSON probe 和运行详情在 Worker 读取，Hub 仅展示机器字段。禁止解析日志判断业务成功；退出码与发布回执双重核验。
4. 长任务通过持久化执行记录与周期 checkpoint 状态更新管理；核验 server 超时策略，允许超过短控制请求生命周期，断线不得等同业务失败。
5. 取消按受控进程组 SIGTERM、等待清理、必要时 SIGKILL，并核验无遗留子进程。

### P2：正式批次交付

1. AKsignal 提供只读 Artifact Manifest：一次解析 outputs 到固定批次，验证目录/run_id/trade_date/回执/final_check.ok 和报告日期一致，只列 HTML、MD、JSON、final_check、publication 五文件；记录 sha256、大小、warnings/errors/status/data_complete。
2. 不扫描或传输整个 data/outputs。Worker 只读取 Manifest 白名单，按批次固定路径注册并保留 digest，避免读取期间 outputs 切换导致混批。
3. JSON 加入通用附件类型；真实路径授权仅限已验证交付文件，不能开放整个 .run_day。复用 artifact transport 与 HTML 预览；必要的 Hub 扩展保持通用。
4. 成功发布后自动交付；失败、无有效回执、哈希异常不更新最新成功交付。保留上一报告和质量提示。

## 已执行验证与未完成项

- `python -m src.daily_run --status`：20261012 已发布，6/6（离线）。
- `.venv/bin/python -m pytest tests/daily_run -q`：28 passed。
- 读取五份正式产物的实际发布基线；未重新执行缓存计算、未完成 P0/P1/P2 集成测试。
- 未验证 08:30 当前源端实时可用性；历史手册称 SW 主源约 T+1 10:00，不据此冒充本次实时验证。在线端到端验收需用户确认后执行。
- 未读取 Sealos 线上部署状态，未部署或重启任何服务。
- 本会话写入范围包含 AKsignal，不包含 mashang-service / mashang-hub；后续跨仓库写入需要工具权限扩展，不等于需要重申业务开发授权。

## 分阶段交付

P0、P1、P2 分别提交，每阶段列修改文件、复用能力、新接口、测试/实际验证、剩余风险与 commit。生产 Scheduler 开启/重启、真实在线行情更新、Sealos 部署单独说明影响并取得确认；在此之前完成可审阅代码和离线测试。
