# 日报 04：研究注意力分配

2026-10-25 · 展示解释 `group_attention_v2` · 日报布局 `daily_observation_modules_v10`。

这是读取已验收 `stock_group_trend_start_v2` 的展示层，不计算行情、趋势信号或状态机。`status`、`first_trigger`、事件、RS及参与度保留原值。只解释事实和安排阅读顺序，不表示投资吸引力、买入概率或预期收益。

## 三层阅读

1. 今日观察结论：最多突出3组，按“新增机会 / 已有机会”分开说明，每组附自然语言理由。避免在结论中堆叠MA、RS和条件字段；中期尚待修复及短线分化仍保留。同档并列，不按涨幅或首次WATCH排名。
2. 完整 9 组速览：短名称、阶段、原规则状态、首次/持续/状态变化、上一有效观察日的关键变化、机会类型与阅读优先级。完整名称保留在详情及链接提示中。移动端按组转为单栏。
3. 折叠详细证据：保留 `stock-group-{key}` 锚点、QR/MA、收益、两种时间尺度的RS变化、参与度、事件首次证据与当日证据、领先个股、数据限制和个股表。点击速览或带锚点的外部链接自动展开；不依赖外部脚本。没有脚本时仍可手动展开。

共性限制集中在速览后的折叠说明中。每组独有的 limitations、issues 和由 snapshot_ids 关联的 history_limitations 单独显示；不把“数据完整”解释为复权认证。仍保留 `UNVALIDATED` 和 `price_basis_verified=false`。

## 阶段定义（按表从上到下首次命中）

输入须为可判定组，`data_complete=true`，所需数值有限，所需 V2 布尔条件齐全；否则展示“无法判定 / 待数据完整”，不修改原规则状态。N为QR结构指数；所有条件名直接来自V2的conditions，不在展示层重算。

| 阶段 | 定义 |
| --- | --- |
| 上行延续候选 | N>MA20，price_turn、support成立，RS20Δ≥0，20日收益>0 |
| 修复转强 | structure、support同时成立 |
| 转强观察 | N>MA20，price_turn成立，rs_early或participation_early成立 |
| 上方修复 / 反弹修复 | price_turn成立，分别为N>MA20 / N≤MA20 |
| 初步企稳 | N>MA5，5日收益>0，rs_early或participation_early成立；此前分支未命中 |
| 短期走弱 | 5日收益≤0且N>MA20 |
| 弱势异动 | 5日收益≤0、N≤MA20且sudden成立 |
| 弱势整理 / 弱势 | 5日收益≤0、N≤MA20，分别为RS20Δ≥0 / RS20Δ<0 |
| 整理待观察 | 其余完整事实 |

这些标签描述不同观察维度，不是单调升级序列。“初步企稳”可以出现在完整 price_turn 尚未成立时；“上行延续候选”不要求完整 structure 成立，完整结构是否成立仍以详情中的`conditions.structure`及原V2状态为准。结构转强仍以原V2条件为准，不由展示标签认证。

## 阅读优先级（按表从上到下首次命中）

活跃状态指 WATCH / EMERGING / CONFIRMED。阶段与状态独立；NO_SETUP可以对应上行价格背景，绝不推断弱势已确认。

| 阅读档 | 定义 |
| --- | --- |
| 待数据完整 | 展示依据不齐或UNAVAILABLE；不参与优先安排 |
| 常规观察 | 完整事实但非活跃状态 |
| 优先研究 | 活跃且(structure与support成立)，或(price_turn、rs_early、participation_early同时成立) |
| 重点观察 | 活跃且N>MA5、5日收益>0、rs_early和participation_early同时成立 |
| 观察持续性 | 活跃且price_turn成立，并有rs_early或participation_early |
| 持续跟踪 | 活跃且structure或price_turn成立 |
| 暂不优先 | 其他活跃状态 |

展示排序为优先研究 → 重点观察 → 观察持续性 → 持续跟踪 → 暂不优先 → 常规观察 → 待数据完整。同档并列，无综合评分，不增加统计阈值。`first_trigger`用于“首次 WATCH”变化提示，不压过更完整的持续证据；状态降级、失效、到期仍在变化栏保留前后状态。

摘要仅从前两档按整档纳入，最多3组。不能在不打破并列的情况下纳入下一档时停止；第一档超过3组时明确说明并列，不任意截取。没有前两档时明确无优先组；全不可判定时明确数据不足，不称没有机会。

## 新增与已有、变化取舍

- `opportunity`：完整事实且首次WATCH为“新增机会”；其余活跃状态为“已有机会”；NO_SETUP为“常规观察”；展示依据不齐为“待判定”。这里的机会仅指研究观察线索。
- `priority`仍使用上表的原证据分档，排序不变。`priority_label`将已有机会的“优先研究 / 重点观察”分别显示为“优先跟踪 / 重点跟踪”。新增不会自动压过已有的完整证据。摘要先列选中组中的新增机会，再列已有机会；总共仍最多3组。
- `summary_sections`保存两类结论及逐组中文理由。理由使用阶段、条件和上一有效观察日的变化作固定模板选择。例如10日参与度增加且RS20Δ下降，描述为上涨成分增多、相对大盘表现回落；若当前RS20Δ仍为正，则描述为领先幅度收窄。10日参与度增加而5日参与度下降，描述为上涨成分增多但短线仍有分化。价格仍不高于MA20且止跌条件成立时保留“中期仍待修复”。
- `today`先展示首次WATCH或状态转换，再展示最多两项非零指标变化。回落项优先，同方向依次按10日参与度、5日参与度、RS20Δ取舍；完整候选变化参与确定性选择，选中的两项记入`developments`。不重复把“持续WATCH”当成每日新变化，它仍显示在规则状态下。
- 只省略在现有两位小数精度下显示为零的变化（包括负零），不设置新的统计显著性阈值。没有事件、可展示指标变化或新成员线索时写“暂无新增变化（按显示精度）”。缺变化字段时写数据不齐，无前次有效日期时写无对照，绝不冒充零变化。
- 只有`first_clue=true`的成员线索进入“新增个股线索”提示；延续线索保留在详情。零变化、未进入速览的变化和全部成员线索仍保留在事实JSON及详细证据中。

## 可追溯与兼容

`observation_{date}.json`增补独立的`group_attention`，包含解释版本、摘要、highlighted_keys、每组阶段、理由、优先级及逐字段trace。原`group_activity`回执及`stock_group_activity_{date}.json`不变。展示校验比较解释对象与事实，HTML校验继续核对全部原始状态、证据及分组锚点。日报布局版本由v9变为v10；过期布局需要重建展示，不能复用旧检查点绕过签名。

`today`中的RS20Δ与5/10日参与度变化来自`changes`，其日期来自`previous_observation_date`，单位均为pp；详情“RS20Δ较5日前变化”来自`metrics.rs_20d_change_5d_pp`。不从日历猜上一有效日，不用5日变化冒充逐观察日变化。`phase_fields`、`priority_fields`、`observation_fields`和trace用于追溯标签及摘要。

Hub继续交付原文件名与HTML artifact类型；锚点、favicon、原4个日报区域和数据质量区保留。解释JSON是增补字段。此轮只生成本地预览，未调用发布入口。

## 本地复现

```sh
.venv/bin/python scripts/validation/preview_group_attention.py \
  --source data/audits/group_activity/20261023/trend_start_v2 \
  --output data/audits/group_activity/20261023/attention_preview_v3
```

输出目录必须不存在；已有预览不覆盖。脚本读取已验收目录及其回执，校验事实摘要，只重渲染既有事实，复制V2 JSON时保持字节一致，核对输入文件前后哈希，输出新版04、旧版04、完整日报、增补JSON及`render_verification.json`。不取行情、不重放状态机、不写正式outputs或latest。

2026-10-23：商用车、汽车热管理、Physical AI并列优先；汽车全球化（非OEM）重点观察；存储观察持续性；AZ暂不优先；其余常规观察。热管理仍低于MA20，故为反弹修复，未套用任务示例中的“修复转强”。Physical AI的RS20Δ较5日前+9.77pp，但较上一有效观察日-3.69pp，两个事实分别展示。

## 本轮展示验收

- `pytest tests/observation tests/market_facts/test_group_activity.py tests/daily_run/test_hub_view.py -q`：102项通过（2026-10-25）。
- Chrome无头浏览器：1280px桌面、390px移动及320px窄屏；9行、9个原锚点，默认折叠，点击自动展开，外部深链接自动展开；组内搜索返回实际结果；无JavaScript时原生折叠仍可用。无脚本错误，移动端和窄屏无页面横向溢出。
- `render_verification.json`确认来源文件哈希不变、V2事实副本字节一致、原JSON states回执不变、渲染依据和锚点校验通过。
- 只重渲染已有2026-10-23验收事实，未重新取数、调参、重放历史评估或发布。原验收中的ETF未更新及位置未知等警告仍在完整日报的数据质量区；展示验收不把原有数据限制提升为“全数据已验证”。
- 本地最终预览：`data/audits/group_activity/20261023/attention_preview_v3/section04.html`。同目录有`section04_before.html`、完整`observation_20261023.html/json`、事实JSON副本、桌面/移动端/展开详情截图及两个验收JSON。

| 原展示 | 新展示 |
| --- | --- |
| 首次WATCH一律前置，概要中展开指标长段落 | 按证据分档；持续WATCH可并列优先；顶部一句话附每组理由 |
| NO_SETUP无单股线索时不进入概要 | 所有9组都有阶段、规则状态、变化和阅读优先级 |
| 概要和分组面板重复完整指标及风险 | 概要只作阅读提示，完整依据按组折叠，共性限制集中一次 |
| 参与度变化使用百分比显示 | 与上一有效观察日的变化统一用pp，明确区别RS20Δ较5日前变化 |
| 新增和已有机会共用一个优先级标签 | 新增／已有独立标识；已有优先组显示“优先跟踪” |
| 机械展示零值及持续状态 | 事件与非零变化优先，保留改善和回落分化 |
| 状态与标签主要混合在段落 | 规则状态独立保留，解释字段及trace单独增补到JSON |

改动文件：

- `src/observation/group_attention.py`：确定性阶段、阅读分档、摘要和字段追溯。
- `src/observation/report.py`：三层HTML、响应式速览、折叠证据、锚点展开、共性及特有风险。
- `src/observation/cli.py`：仅增补展示解释JSON及其校验，布局版本使用v10；原事实构建及验收契约不变。
- `tests/observation/test_group_attention.py`：实际9组与缺失、并列、两种时钟、标签边界和风险回归。
- `tests/observation/fixtures/group_activity_20261023_v2.json`：已验收事实的精简提取，记录原文件SHA256。
- `tests/observation/test_report.py`、`tests/observation/test_module_partial.py`：新布局、成员线索及不可用分组的展示断言。
- `scripts/validation/preview_group_attention.py`：只读验收事实的本地预览及字节/哈希对照。
- `docs/DAILY_GROUP_ATTENTION.md`、`docs/STOCK_GROUP_ACTIVITY.md`：展示定义、边界、复现与验收记录。
