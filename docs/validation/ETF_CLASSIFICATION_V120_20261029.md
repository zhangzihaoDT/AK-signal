# ETF 分类发布与消费 v1.2.0 验收

2026-10-29。此版本实现分类发布与消费边界，不扩展分类能力。

## 正式分类发布

- 配置：`config/etf_classification_facts.json`。
- 不可变存档：`config/etf_classification_versions/20261029-v1.json`。
- 版本：`20261029-v1`。
- SHA-256：`1c50c39988db3bc1b5aa9c60ab2bf45994012a2d8d9b467557af29de819811f9`。
- 候选：`data/reference/etf_classification/candidates/20261029-bootstrap-v1/classification_facts.json`。
- 发布回执：`data/reference/etf_classification/publications/20261029-v1.json`。

候选仅导入已有名称审计，经过安装的 Skill 证据结构校验。1548 只 ETF：市场 inferred 790、pending 758；方向 inferred 1497、pending 51；verified 均为 0。发布状态与证据核验状态独立。

## 实际缓存计算验收

使用这 1548 只 ETF 本地缓存中 2025-01-01 至 2026-10-28 的行情，独立运行正式 `compute_rotation_metrics` 消费函数；计算产物写入隔离验收目录，不改写正式日报。

- 1548 只全部存在本地行情，结果未丢基金行。
- 每行读取版本 `20261029-v1` 和一致的配置摘要。
- 将名称分类函数替换为抛错桩，实际调用为 0。
- 正常分类与配置缺失两个条件下，各收益列逐字段完全一致。
- 配置缺失时 rps1/rps5/rps15 全部缺失，没有空资格集合回退全市场。
- 31 只现金流 ETF 的方向为已有“跨行业策略”推断，没有升级核验状态。
- 配置在消费前后逐字节一致；质量模块为 partial，保留两个维度的 pending 数量。

机器回执与计算结果：`data/reference/etf_classification/validation/20261029-v1.2.0/`。此步骤仅为隔离缓存消费验收；其后完成的正式 RunDay 发布见下节。

## 自动化验收范围

发布拒绝不合格证据；子集合并保留其他基金；基准版本冲突拒绝；相同候选幂等重试；原子切换失败保留旧版本并可恢复；配置有效期与快照日期限制；整体缺失或损坏降级；固定副本摘要损坏拒绝；RunDay 失败恢复期间当前配置发布新版仍消费原版；行情保留和空 RPS 集合；现有 ETF、observation 与 RunDay 回归。

Skill 仍保留原来的 22 项证据校验测试并通过格式校验。生产消费测试属于 AKsignal，不能用 Skill 文档或候选校验通过替代。

## 正式 RunDay 发布

随后按用户要求，以已有缓存执行 `make run-day-offline RUN_DATE=20261028 RUN_FLAGS=--restart`，全链验收后原子发布：

- 目标交易日：2026-10-28。
- 发布时间：2026-10-29 16:37:36（Asia/Shanghai）。
- 批次 ID：`9fee52be2d3d4e2590dee973283f88d1`。
- 发布状态：PARTIAL，保留分类待核实、4 只 ETF 行情未对齐、位置历史不足等明确限制。
- 分类版本和 SHA-256 与上文配置一致；1548 只趋势事实，773 只两个维度均有分类值，31 只现金流方向为跨行业策略推断。
- 发布回执：该批次 `outputs/run_day/publication.json`，SHA-256 `1eea13990c03743ebe94699bcaf33b1ff78b19f3ab3a04679902cf057bd155fa`。
- 正式报告：`outputs/observation/observation_20261028.html`；原批次保存在 `.run_day/runs/20261028/9fee52be2d3d4e2590dee973283f88d1/`。

## 两仓库 Git 归档

- AKsignal：项目版本 `0.7.3`，里程碑标签 `etf-classification-v1.2.0`；按用户确认包含当前完整运行代码与配置，不仅截取 ETF 文件。既有日报、核心指数、衍生品与行业快照修复一并归档。
- Skill：私有仓库 `https://github.com/zhangzihaoDT/etf-classification.git`，标签 `v1.2.0`，提交 `107af6d3b9b762f82fd6919a46b4ad23766057ee`。源码仍在独立本地仓库，安装软链接不变。
- 归档包含正式分类配置与版本存档、代码、测试和验收说明。按既有忽略策略，data 中证据/行情、.run_day、outputs 和凭证不提交。Git 归档不是运行数据备份；跨机器复现需另行保留被配置和回执引用的数据。

归档前验证：AKsignal 全量 `pytest -q` 为 909 passed（52.78s）；Skill 为 22 项 unittest 通过，Skill 格式校验通过；`git diff --check` 通过。
