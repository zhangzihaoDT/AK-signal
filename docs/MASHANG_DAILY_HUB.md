# AKsignal 独立 Hub 业务入口：初版交付

范围：手动补跑、只读状态、HTML/Markdown 正式日报附件。不是原 P1/P2 全部能力验收；Resume、维护 Restart、浏览器日志、组合与策略功能未纳入。生产 Hub/Worker 尚未更新，本轮只在本地隔离环境验证。

## 领域与职责

业务入口按领域划分，运行基础设施统一复用。AKsignal 是与 Service、Fetch、Knbase、Publish 同级的第五个业务区域。复用 Service Scheduler 只构成技术依赖，不构成业务归属。

Scheduler 继续提供共享触发；AKsignal 拥有 Market Facts、Runner、业务锁、checkpoint、等待/防重/发布门控和独立结果。Worker 拥有固定调用与安全文件读取；Hub 拥有通用状态、审批、控制和附件展示。没有新增 Scheduler、数据库、依赖或 Agent。

## 接口和状态

`python -m src.daily_hub` 是新增只读 JSON 接口，返回 lastRun、details 和正式附件描述，不运行 Pipeline，不写 checkpoint 或回执。

details 包含目标交易日、当前 run_id、业务阶段、完成/总阶段、运行秒数、失败原因、重试时间、最新正式批次和 data_complete/warnings/errors；时间区采用 Asia/Shanghai。行业主源覆盖只读已发布的行业事实。失败耗时停止在 checkpoint 更新时间，不将等待时长冒充计算时长。

状态包括 RUNNING / WAITING / BLOCKED / PUBLISHED，未开始时 IDLE。业务状态与控制请求结果分离：`--tick` 请求结束只表示检查请求已处理，不等于发布成功。正式回执与 checkpoint 不同批次时，继续展示当前运行/失败，同时保留上一正式日报。

Worker 注册 `aksignal-daily`，workspace=aksignal；可用 `MASHANG_AKSIGNAL_ROOT` 指定本地仓库路径，默认 Documents/github/AKsignal。Run 只调用原生 `python -m src.daily_schedule --tick`，不传 --restart、不直接执行业务子步骤。没有为 AKsignal 注册 Agent 工作空间。

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

## 正式启用待确认

开发分支提交后还需更新部署的 Hub 静态资源/协议代码，并让 Mac Worker 重新加载注册与 provider。生产操作会短暂影响 Hub/Worker 连接，应确认当前任务和维护时点后执行。本轮未推送、部署、重启 Worker/Scheduler、修改 LaunchAgent 或开启生产自动调度；不重新请求在线行情。

## Market Snapshot

只读 `src/market_snapshot.py` 消费正式批次的 asset_directions、industry_directions、industry_rotation、etf_trends 与 etf_positions。输出市场状态/关注方向/重要变化；不修改任何事实或指标，不运行 Pipeline。广泛行业支撑优先于单点强势，不做股票榜。变化只比较官方日历上一交易日的已核验正式批次、同源同状态共同样本；缺失或不可比明确提示。有效底部变化仍受窗口完整性门控，不构成买入建议。Snapshot 的日期/run_id 与当前回执绑定，由通用 Runtime details 交付。测试覆盖行顺序无关、广度优先、无上一期、同源共同样本与无效位置排除。

## Snapshot v2 可视化数据

只读摘要增加124行业格子（RPS15、所属一级行业活跃比例、上一交易日同源可比RPS15差值）、20个官方交易日广度序列以及行业状态分布。序列只含正式发布，缺失记null；不拉取历史、不插值。每点附样本总数、来源口径和完整性，防止跨缺失/来源变更连线。当前实际只有两期正式发布，明确显示2/20。63项每日链测试通过；Hub图形单测与浏览器三口径切换、未知、手机布局、原补跑/附件/只读讨论回归通过。
