# AKsignal 独立 Hub 业务入口：初版交付

范围：手动运行/恢复、只读状态、HTML/Markdown 正式日报附件。本文其余内容保留首次 Hub 接入的历史验收记录；当前调度所有权和自动/人工控制边界以 [AKsignal Daily Scheduler](DAILY_SCHEDULER.md) 为准。

2026-10-14 更新：Sealos Hub 已切换到 `0.3.2-def2a95`，本地 Worker 已重载并连回。后续 AKsignal 独立 LaunchAgent 与 Service/Run Control 分离已在本代码树实现，但尚未随该镜像上线，也未安装本机 Agent。源码改造、Hub/Worker 发布和 LaunchAgent 显式安装是三个独立步骤；未安装 Agent 时，08:30 自动调度未启用。

## 领域与职责

业务入口按领域划分，运行基础设施统一复用。AKsignal 是与 Service、Fetch、Knbase、Publish 同级的第五个业务区域。AKsignal Scheduler 独立于 Service Scheduler；底层 Daily Run 锁、checkpoint、发布门控仍由 AKsignal 统一拥有。

Service Scheduler 只管理 Service 域定时任务。AKsignal LaunchAgent 管自己的 08:30 计划、定时器健康和最近触发；Hub Worker 暴露 AKsignal Scheduler 启停/重启与 Daily Run 手动操作；Hub 只提供通用状态、审批、控制和附件展示。

## 接口和状态

`python -m src.daily_hub` 是只读 JSON 接口，返回 Scheduler 心跳、下次检查、可操作 trade_date、Daily 状态和正式附件描述，不运行 Pipeline，不写 checkpoint 或回执。

details 包含计划状态、目标交易日、触发来源、当前 run_id、业务阶段、失败原因、最新正式批次和 data_complete/warnings/errors；时间区采用 Asia/Shanghai。MISSED/FAILED 是等待人工处理的状态，不显示自动重试时间。

Daily 状态包括 RUNNING / MISSED / FAILED / WAITING / BLOCKED / PUBLISHED，未开始时 IDLE。Scheduler 状态另行显示 RUNNING / OFFLINE / ERROR。错过 08:30 或 scheduled batch 失败后不自动重跑；`--manual-run` 才会由面板按钮触发恢复。人工请求受理不等于正式发布。

Worker 注册 AKsignal Scheduler service 与 `aksignal-daily` manual operation，workspace=aksignal；可用 `MASHANG_AKSIGNAL_ROOT` 指定本地仓库路径，默认 Documents/github/AKsignal。Worker 只调用固定的 Agent 管理入口或 `python -m src.daily_schedule --manual-run`，不直接执行业务子步骤。没有为 AKsignal 注册 Agent 工作空间。

只读 Worker 快照通常每 30 秒更新，Hub 每 15 秒获取并订阅更新；“查询状态”读取当前 Worker 快照，页面显示快照时间。Worker 离线或快照过期禁用手动补跑。短控制请求独立于后台长批次，不会将控制会话结束/超时误当成后台失败；同日期互斥和防重仍由原生锁/回执负责。此入口不提供取消后台批次按钮。

## 正式交付

只解析正式 outputs 指针；核对批次路径/run_id、trade_date、final_check.ok 与回执 result、一致性和完整性标签。只提供两份 HTML/Markdown，不传行情缓存、parquet 或历史研究目录。

每份附件固定到 `.run_day/runs/{date}/{run_id}/outputs/observation/...`，而非可变化的 outputs 别名。Worker 核对文件范围、大小、SHA256，Hub 只接收不含本地路径的附件元数据。沿用现有 artifact.request/response 和预览/下载 API，下载时再次核对摘要；HTML 继续受现有 CSP 限制。重连后新快照恢复附件注册。失败批次和日历过期不覆盖上次正式结果。

CONFIRMED 表示来源确认，data_complete=false 仍明确展示“数据不完整”。warnings 不混入成功文案；过长消息如超出通用协议限制会明确提示截断，并引导查看完整正式附件。

## 修改文件

- AKsignal：src/daily_hub.py、tests/daily_run/test_hub_view.py、本文及 canonical map。
- mashang-hub：Worker runtime/registry、status、protocol、control、json-operation 与 worker；server/runtime-contract、operation-details 与 server.mjs；public/index、app、runtime-ui、runtime.css；registry/JSON provider/Hub bridge/browser 测试、package scripts 与 architecture-boundaries 文档。
- mashang-service 未修改，08:50 Daily 与生产 Scheduler 配置不变。

## 验证

- AKsignal tests/daily_run：60 passed，覆盖只读性、完整性警告、四种状态、未发布附件拒绝、日历过期和失败后上一结果保留，以及同日期维护运行不被旧回执伪装成成功。
- Hub test:runtime、test:operation-provider、test:artifact、test:dom 全部通过。短请求可与其他业务长操作共存，原长操作互斥策略保留；文件摘要变化/越界拒绝，非法时区与警告截断正确处理。
- 原 Service/Fetch/Knbase/Publish 的 workbench-browser 回归通过，导航扩展为五项，保留草稿、会话和结果隔离。
- 真正的本地 Hub + Worker + Python provider + Chrome 集成通过。隔离 fixture 在 /private/tmp/aksignal-hub-e2e-fixture，复制现有 20261013 正式发布和源码，不复制行情缓存；Python socket 禁止行情网络请求。
- 实际点击“手动补跑”及既有审批，调用原生 --tick 后命中现有正式回执，attempts=0，无计算/重发。HTML/Markdown 预览与下载逐个验证 SHA256，data_complete=false 和全部五条 warnings 可见；没有 Agent POST 请求。桌面 1440×1050、手机 390×844 无横向溢出，完成截图视觉复核。

## LaunchAgent 部署边界

Hub/Worker 的业务入口与独立 Scheduler 属于不同部署动作。Hub/Worker 新版本部署不自动安装或开启 `com.aksignal.daily-scheduler`；需要显式运行 `make daily-scheduler-install`，并在 AKsignal 面板确认 Scheduler 心跳。暂停/重启 Scheduler 不会终止或重跑已经启动的 Daily batch。当前 miss/failure 只允许面板人工 Run/Resume，详见 [AKsignal Daily Scheduler](DAILY_SCHEDULER.md)。

## Market Snapshot

只读 `src/market_snapshot.py` 消费正式批次的 asset_directions、industry_directions、industry_rotation、etf_trends 与 etf_positions。输出市场状态/关注方向/重要变化；不修改任何事实或指标，不运行 Pipeline。广泛行业支撑优先于单点强势，不做股票榜。变化只比较官方日历上一交易日的已核验正式批次、同源同状态共同样本；缺失或不可比明确提示。有效底部变化仍受窗口完整性门控，不构成买入建议。Snapshot 的日期/run_id 与当前回执绑定，由通用 Runtime details 交付。测试覆盖行顺序无关、广度优先、无上一期、同源共同样本与无效位置排除。

## Snapshot v2 可视化数据

只读摘要增加124行业格子（RPS15、所属一级行业活跃比例、上一交易日同源可比RPS15差值）、20个官方交易日广度序列以及行业状态分布。序列只含正式发布，缺失记null；不拉取历史、不插值。每点附样本总数、来源口径和完整性，防止跨缺失/来源变更连线。当前实际只有两期正式发布，明确显示2/20。63项每日链测试通过；Hub图形单测与浏览器三口径切换、未知、手机布局、原补跑/附件/只读讨论回归通过。

## 观察篮子走势（2026-10-15，本地源码已接入）

`research basket compare` 生成独立价格表现研究报告，并将文件哈希和已解析的批次内路径写入 `data/hub/basket_comparison.json`。`src/research/baskets/hub_view.py` 仅读取该指针、核验报告/manifest/曲线摘要，再通过通用 `details.trendComparison` 提供九条日频走势和独立研究日期；不运行抓取、重算或 Daily 发布，不改变正式日报状态。文件只能位于既有 Worker 的 `.run_day/runs` 附件范围，不能扩大为任意本地目录。自定义目录导出不自动注册 Hub。

Hub 将研究图放在主面板的每日摘要后方，显示研究窗口和价格口径，匹配附件摘要后提供预览与交互 HTML 下载。正式日报 HTML 仍单独识别，研究报告不能冒充正式发布。受限预览保留静态 SVG，下载后可使用完整筛选和悬停交互。当前研究快照截至 2026-10-14；此接入不代表研究数据已经配置每日自动刷新。线上 Hub/Worker 尚需单独部署；本地源码接入和线上可见不是同一状态。

2026-10-15 线上生效核验：镜像 `0.3.2-a156054` 的 `runtime-ui.js`、`trend-comparison.js` 已与线上文件 SHA256 比对一致。**本次上线需要重启本地 Worker**：虽然 `src.daily_hub` 每轮启动新 Python 进程，Worker 的 `runtime/protocol.mjs` 在启动时导入共享的 `server/operation-details.mjs`，旧 Node 进程会过滤新增字段，不能仅等待下一次快照。确认无未完成任务后仅重启 Worker，线上主摘要后已观察到9条走势和两个报告入口。后续仅改 Python 产物可随下一轮刷新；改 Worker 导入的 JS、共享协议过滤器、registry 或环境配置时必须重启 Worker。

2026-10-15 交互预览修正：旧附件响应的 CSP 禁止全部脚本，完整报告按钮因此仅在下载后有效。Hub 修复版对内嵌脚本使用精确 SHA256，并以不含 `allow-same-origin` 的 CSP 沙箱运行，仍禁止网络、表单、外部脚本与框架。只更新 Hub 镜像即可，本次不涉及 Worker 导入模块变更。研究报告八张小图已按用户要求默认展开、各自 Autoscale，切换区间后重新缩放；主图保留共同纵轴。报告已重新生成，附件哈希和 Hub 指针随之更新，需从 Hub 最新入口打开。
