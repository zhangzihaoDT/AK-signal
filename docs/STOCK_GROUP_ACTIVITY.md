# 日报04趋势启动观察 · V2

2026-10-24 · `stock_group_trend_start_v2` · 日报布局 `daily_observation_modules_v9`。

本轮替换启动规则与事件去重，保留双路径、结构指数、快照审计、完整性和状态历史重建机制。全部参数为样本内探索候选，**未验证**。本地比较和预览不覆盖正式发布，不构成交易、仓位或收益预测结论。

## V1独立对照

- 原始完整规则归档：[V1规则](validation/STOCK_GROUP_TREND_START_V1_RULES.md)。
- 冻结函数/参数：`scripts/validation/fixtures/stock_group_trend_start_v1.py`，仅供验收，生产不导入。包含原始版本、RULES、describe_returns、trend_features、reconstruct_states，注明冻结前完整源文件SHA256。
- 对照运行必须验证此前保存的10月23日V1事实：input_hash、清单SHA、规则、每组NAV及逐日完整状态记录全部一致。不能用V2模拟出一份“看起来像V1”的基线。
- 比较产物另存 `data/audits/group_activity/20261023/trend_start_v2/`。原V1验收、正式outputs和发布指针保留。

## 数据与指标契约

9组由Compare配置与成员展开生成；组间等权、组内等权、季度再平衡及首个完整自然季度末锚点不变。只读本批审计清单绑定的长桥股票/沪深300不可变快照，检查内容摘要、身份、日期、当前注册签名、配置和日历。原生市场交易日报价缺口严格拒绝；不读latest、旧Compare或网络。

结构指数N与同源沪深300B均按HS300交易日历。休市可按Compare约定持有估值，但不变成零收益观察；状态只在完整有效观察日更新。5/10/20日参与度与10日参与度5日前对照复用Compare的有效估值端点，结构指标不完整则UNAVAILABLE并冻结组级状态。单日异常使用各市场原生一交易日收益，基线为此前60个共同开市日，不含当日。

- MA5/10/20：N对应窗口简单均值。
- 价格止跌/初步转强：N>MA5、5日收益>0、MA5较3日前上升。渐进WATCH始终需要此条件。
- 价格转强结构：N>MA20、MA5>MA20、MA20较3日前不下降、5日收益>0。
- RS与Compare `relative_strength()`一致；RS20Δ=`100×(RS_t/RS_(t−20)−1)`。另记录RS20Δ较5日前变化（pp）。不另造同名RS20口径。
- 参与度为Compare有效估值端点且区间收益>0的个股数/有效个股数；日报要求完整组分母。Compare的has_price为持有估值可用性，允许已验证休市时沿用估值，非端点当日新成交证明。这与原生单日收益交集是两种时钟。上一轮文档将它描述为实际报价端点，现按源码纠正；V1冻结计算、V2计算及正式Compare结果不因本轮说明修正而改变。
- 已有价格上行背景：此前连续5次有效观察N>MA20、MA20较3日前上升、20日收益≥3%。左边界第一条有效记录若价格上行成立，先按已有背景处理，避免凭空发首次提示。此5次是背景判别，**不是NO_SETUP冷却**。
- 长桥 `price_basis_verified=false`，保留历史限制、输入与配置哈希、指标、条件、线索及完整历史。

## WATCH双路径

### 突发式

弱势/横盘或已重置的回落背景下，存在GROUP_UP、BREADTH_UP或正向MEMBER_MOVE之一，且N当日上涨、成分日收益中位数>0、上涨比例≥50%。此处仍要求集体方向证据。

单日异常参数保持不变：GROUP_UP要求≥3只、中位数≥2%、偏离前60日均值≥max(2.5σ,2pp)、上涨比例≥60%；BREADTH_UP要求上涨数增≥2只、比例增≥30pp、变化偏离基线均值≥max(2σ,30pp)；MEMBER_MOVE要求绝对涨跌≥5%、偏离自身均值≥max(3σ,5pp)。只有正向成员异动作为领先线索。

### 渐进式

价格止跌/初步转强为必要条件，其后允许两种具有不同经济含义的早期证据，**不按指标数量计分**：

- RS改善路径：RS最近5日上升，RS20Δ最近5日中≥3日增加，较5日前累计改善≥0.5pp。先观察相对领跑，不强制同时出现参与度扩散。
- 参与度扩散路径：5日参与度≥50%，10日参与度较5日前扩大≥10pp。先观察更多成分止跌转涨，不强制同时获得RS支持。

两者之一即可支持渐进WATCH。两者同时满足记录RS_AND_PARTICIPATION，不加分。EMERGING才要求更完整的价格、广度与RS支持。

## 新趋势识别与事件去重

取消统一五次NO_SETUP等待期。候选识别与事件去重分别记录条件，可复核为何触发或被抑制。

1. 没有任何已观察事件时，候选可当日首次进入WATCH；不额外等待5日。
2. 活跃事件中，WATCH/EMERGING/CONFIRMED延续或转换均保留同一episode_start，first_trigger=false。
3. 事件失效/到期后，从终止日收盘开始记录reset_low。价格失效本身即为有效回落重置；到期只有当日价格转向已丢失或价格恶化时算重置，之后也可通过真实价格转弱形成重置。
4. 重置后仍须重新满足A/B候选，且满足以下之一：候选由不成立变为成立；突发线索形成新脉冲；N从重置后最低收盘恢复≥1%且当日上涨。允许次日二次启动，无固定等待日数。
5. 单纯WATCH到期、价格始终向上而同一条件一直成立，没有真实重置，不每日重发。
6. 已有价格上行通常不作为新机会；但上个事件已结束且存在真实回落重置时，允许新脉冲/恢复被识别为二次启动。不能让“已有趋势”门控继续硬挡回落后的新机会。

1%恢复是候选去重参数，未据前瞻收益优化；高波动组会更容易满足，可能增加噪声。所有规则只用截至当日的价格和状态重建，不读取状态文件。

## 单股领先线索

正向MEMBER_MOVE独立保存到leader_clues，包含股票身份、涨幅、基线、阈值、scope=MEMBER和first_clue。只要原生单日基线完整，即使组级结构指标因端点休市不可判定，也可展示这条明确限定范围的成员事实。

同一股票连续有效观察日保持异常只首次标新线索；结束异常后再次出现可重新标新。组级UNAVAILABLE时不伪造组级状态。NO_SETUP也展示个股线索，不因组级扩散不足丢弃。first_leader_clue不等于first_trigger；成员线索不计入首次WATCH数量或组级趋势捕获率，不直接升级EMERGING。

## 状态、来源、提示分开

- `status`：WATCH / EMERGING / CONFIRMED / NO_SETUP / UNAVAILABLE，只有趋势证据强弱。
- `event_origin`：EARLY_CANDIDATE（从WATCH观察）或EXISTING_TREND（左边界/后续已有趋势被观察到）。TRACKING不再混入path或状态。
- `path`：SUDDEN / GRADUAL，已有趋势观察为null。
- `trigger_channel`：事件首次观察时的证据来源；`evidence_channel`为当日的证据条件，二者不混淆。
- `first_trigger`：仅新组级WATCH为true。`alert_kind`区分FIRST_WATCH、STATE_CHANGE、MEMBER_LEAD、NONE。
- `event_active`与`episode_status`：明确ACTIVE、INVALIDATED、EXPIRED、NONE。NO_SETUP保留已结束episode供审计，日报标为“上次已结束事件”，不能当成当前机会。

### 升级、降级和终止

“初步支持”=10日参与度≥60%，且（RS20Δ>0或较5日前改善≥0.5pp）；“持续确认”=价格转强+初步支持+20日收益≥3%+20日参与度≥60%+RS20Δ≥0。

| 转换 | 条件 |
| --- | --- |
| NO_SETUP→WATCH | 任一路径成立且事件新颖性通过，first_trigger=true |
| WATCH→EMERGING | 连续2次有效观察价格转强且有初步支持 |
| EMERGING→CONFIRMED | 连续10次有效观察满足持续确认 |
| EMERGING/CONFIRMED→WATCH | 连续2次丢失价格转强结构，或连续2次丢失初步RS/参与度支持 |
| CONFIRMED→EMERGING | 价格结构和初步支持仍在，但连续2次不满足持续确认，例如20日参与度下降、RS20Δ转负但仍在改善 |
| 活跃→NO_SETUP/INVALIDATED | N较首次WATCH收盘跌≥3%，或连续2次N<MA10且5日收益<0；已有趋势来源没有伪造WATCH入场价，只使用后者失效 |
| WATCH→NO_SETUP/EXPIRED | 在WATCH连续等待10次仍无价格转强+初步支持；降级回WATCH后重新计时 |
| 无活跃事件→已有趋势状态 | 已有价格上行背景且连续2次初步确认，记录EXISTING_TREND/EMERGING；持续确认10次后为CONFIRMED，不标首次WATCH |

处理优先级：失效/过期 → 结构或初步支持丢失降WATCH → 持续确认丢失降EMERGING → 升级。闭市/历史不足/结构指标不齐冻结计数，previous_observation_date始终是上一有效观察日。

## 评估与覆盖

主事件保留：20日上涨≥8%，末5日收盘均高于20日前至少4%，确认间隔≥20日。温和敏感性事件只将8%/4%改为4%/2%，其余完全相同；不从结果中挑最好标签。事件不使用MA、RS、参与度或候选状态。

捕获窗口、提前量及5/10/20日前瞻收益/收盘MAE定义保持V1，分母排除预热起点前20日的不可评估事件。独立价格扩张窗口可能重复标记长趋势，故须逐例区分实际遗漏与已有趋势延续。

每条提示同时记录两条轴：

- price_outcome：已有独立价格确认CONFIRMED；满20日仍未确认UNCONFIRMED；尚无确认且未满20日IMMATURE。
- lifecycle_outcome：本规则事件在20日窗口内INVALIDATED、EXPIRED或未终止。失效后价格仍可能恢复并出现独立确认，不能删除此类反例。
- `matured_20d`独立记录是否满20日。末尾未满20日但已经确认者可为CONFIRMED；因此IMMATURE价格结果数可能少于未满20日样本数。假启动比例仅对满20日提示统计“没有独立价格确认”。

无提示对照为两版共同NO_SETUP且V2无新个股线索的有效观察日，报告同组5/10/20日收益及MAE；它非随机、未按市场状态或波动匹配，窗口相互重叠，只作描述性背景，不作因果或超额收益结论。

逐组报告结构锚点、首次有效日、结构日数、有效/UNAVAILABLE日数、原因分布（主原因互斥、多原因另列）、主/温和可评估事件数。首段预热、原生历史不足、跨市场闭市和其他结构指标不全分别量化；首笔价格缺少前一收盘归为原生收益预热不足。实际原生缺口导致整组拒绝，不能解释为删掉几天后仍完整。

完整对照、每条新增捕获、未确认新增提示、漏报、最新9组证据见 [V2验收](validation/STOCK_GROUP_TREND_START_V2_20261023.md)。全部结果为样本内探索，未证实预测优势。

```sh
.venv/bin/python scripts/validation/compare_group_trend_rules.py --date 20261023 \
  --metrics-dir data/audits/group_activity/20261023/trend_start_v1/stock_metrics \
  --reference-v1 data/audits/group_activity/20261023/trend_start_v1/observation/stock_group_activity_20261023.json \
  --output-dir data/audits/group_activity/20261023/trend_start_v2
```

## 日报与发布门控

原地使用日报04，优先新WATCH、升级、其他状态变化，再到趋势跟踪和独立成员线索。组级提示与单股线索在文字上明确区分。保留AK favicon与原分组跳转。

日报JSON副本纳入来源、活动状态、事件状态、触发证据和成员线索。check仍从本批审计快照重建事实，并核验HTML/JSON证据与跳转；旧版本不作为V2事实验收。日常入口保持make run-day/offline，代码/配置变更须--restart。
