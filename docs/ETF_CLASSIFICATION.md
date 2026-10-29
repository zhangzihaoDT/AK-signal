# ETF 分类发布与 RunDay 消费

2026-10-29 · etf-classification Skill v1.2.0

分类维护与每日行情是两个独立流程。Skill 按需生产并发布分类事实；RunDay 读取已发布配置，不运行 Skill、不按名称重新分类、不补证或升级核验状态。

## 数据、脚本、配置

| 职责 | 路径 | 内容 |
| --- | --- | --- |
| 数据 | `data/reference/etf_classification/candidates/<run_id>/` | 候选事实、来源快照、输入摘要、审计与缺口 |
| 发布审计 | `data/reference/etf_classification/publications/<version>.json` | 发布状态、候选路径与摘要、配置摘要 |
| 维护脚本 | `scripts/maintenance/publish_etf_classification.py` | 调用 Skill 校验器、合并子集、原子发布；不计算行情 |
| 当前配置 | `config/etf_classification_facts.json` | RunDay 正式消费的逐基金双维度分类映射 |
| 不可变配置 | `config/etf_classification_versions/<version>.json` | 保留每次完整版本，用于审计与复现 |
| 分类维护词表 | `config/etf_classification.yaml` | 离线名称线索规则；不再决定每日分类 |
| 消费实现 | `src/etf_signal/classification_facts.py` | 结构检查、固定输入、按代码关联、时点适用性和覆盖统计 |

证据正文留在 data。config 每只 ETF 保留两个维度各自的值、状态、来源 ID、理由、适用时点，以及指向候选数据的路径和 SHA-256。源码在 scripts/src，不把执行逻辑写入数据文件。证据数据遵循项目现有 Git 忽略策略；迁移环境须同时保存 config 和其引用的 data，不得把只有配置的副本称为完整证据归档。

## 两个分类维度

| 维度 | 值 |
| --- | --- |
| 投资市场 | 境内 / 港股 / 美国 / 其他海外 / 跨市场 / 待核实 |
| 投资方向 | 宽基 / 科技 / 消费 / 医疗 / 金融 / 能源 / 其他行业 / 跨行业策略 / 固收 / 商品 / 货币 / 待核实 |

每个维度独立保留 `verified`（已核验）、`inferred`（推断）、`pending`（待核实）。已发布不等于已核验。“待核实”与 pending 对应；来源、状态、时点都是审计字段，不增加产品分类维度。投资市场描述底层投资范围，不能按 ETF 上市交易所推断。

## 独立发布

Skill 源码位于 `~/Documents/github/etf-classification/`，通过 `~/.agents/skills/etf-classification` 软链接挂载，类型为自研。候选格式与证据充分性要求以 Skill 的 `references/fact-contract.md` 为准。

在 AKsignal 根目录执行：

```bash
make etf-classification-publish \
  CLASSIFICATION_CANDIDATE=data/reference/etf_classification/candidates/<run_id>/classification_facts.json \
  CLASSIFICATION_VERSION=<new-version> \
  CLASSIFICATION_BASE=<current-version>
```

首次发布省略 CLASSIFICATION_BASE。发布器先调用安装的 Skill 校验器核对来源快照、范围和状态，再合并当前版本。子集候选保留未涉及 ETF；每只候选 ETF 须携带两个维度，未修订维度沿用适用事实，不省略。基准版本变化时拒绝覆盖，需要明确重新合并。完整任务可包含推断或待核实；未处理完的候选拒绝发布。

新版本先写不可变存档并读回验证，再原子替换当前配置，最后记录回执。切换前失败保留旧配置；切换后回执中断可通过同版本、同候选幂等重试恢复。版本内容不可覆写。纠错通过新版本发布，保留旧版本与所有被引用证据。分类发布不触发行情或日报。

首次配置 `20261029-v1` 仅导入既有 20261028 名称审计，未增加分类能力或重新取证：

| 维度 | 已核验 | 推断 | 待核实 |
| --- | ---: | ---: | ---: |
| 投资市场 | 0 | 790 | 758 |
| 投资方向 | 0 | 1497 | 51 |

共 1548 只、两个维度均有候选值 773 只；31 只现金流 ETF 的方向沿用“跨行业策略”推断，其市场不因本次发布升级。数量仅描述该次输入，不是固定产品池大小。

## RunDay 消费与质量门控

批次启动只读一次当前配置，将内容复制到该批次 `inputs/etf_classification.json`，把版本、SHA-256、路径和发布时间写入检查点；子进程通过固定路径与摘要读取。后台发布新分类不影响本批或断点恢复。分类配置不计入实现签名，而作为独立、有摘要的输入；消费代码和其他规则仍参与实现签名。固定副本损坏直接拒绝恢复/发布。

按基金代码左关联全部行情行，不覆盖 Master。缺失、损坏、未覆盖或日期不适用的分类保留“待核实”及具体原因。价格和收益照常计算；方向待核实、货币、固收不参与 RPS，空资格集合不会回退为全市场。市场待核实而方向已知可参与方向允许的 RPS，但不进入完整市场×方向分类概览。分类修订可能改变排名横截面，原始价格和收益算法不变。

`asset_bucket` 仅为从正式两维度派生的内部兼容字段，不读旧 Master 桶兜底。趋势事实保留两个维度的独立状态、来源引用、版本与摘要；check 从批次固定输入重新关联并逐字段核对。整体缺失或存在 pending 会进入 PARTIAL，`--require-complete` 拒绝不完整发布；inferred 明确展示但不冒充 verified。最终日报发布回执保存本批分类引用。

`valid_from/valid_to` 使用半开区间；未知起点以维度 as_of 为消费下界，不能追认更早历史。holdings_snapshot 仅用于 as_of 当日。后发布资料用于较早交易日时标记 `retrospective`，属于事后修订口径，不是当时可得信息回测。原批次复现读取原固定输入；当前实现不提供自动的历史知识时点版本搜索。

每日入口仍为 `make run-day` / `make run-day-offline`。本次消费代码变动后需 `RUN_FLAGS=--restart`；只有独立分类配置更新时，旧批次仍锁定旧版本，采用新配置须显式新建/重启批次。维护命令不替代日报验收与发布。
