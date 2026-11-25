# 项目结构与职责

## 三种架构分治

- `config/`：数据来源、资产范围、分类、观察契约与研究定义；`decisions/`记录业务取舍的“为什么”。不再承载交易策略、执行或组合规则。
- `docs/`：项目实现架构、模块职责、运行流程、部署与验收说明；`archive/`只作历史追溯。
- `data/`：实际数据及存储架构说明，按来源、raw/processed/audits/state组织，见`data/README.md`。不将大数据放进config或docs。

## 代码与工具

| 目录 | 职责 |
|---|---|
| `src/market_facts/` | 正式事实契约、来源适配、日历/质量门控、长桥股票、ETF观察事实、核心指数、CFFEX与TDX行业数据 |
| `src/observation/` | 汇总已生成事实，输出日报并验收 |
| `src/etf_signal/` | ETF来源路由、主表、历史采集/补齐、分类输入及日指标计算；不含信号、账户映射或独立 Rotation 报告 |
| `src/hub/` | 只读Hub通信：daily状态/报告、baskets研究报告引用与摘要验证；不抓行情、不计算策略 |
| `src/daily_run.py`、`daily_schedule.py` | 批次执行、恢复、调度，与Hub通信层分离 |
| `src/daily_hub.py` | 既有Worker命令兼容入口，仅委派src.hub.daily |
| `src/research/baskets/` | 当前长桥篮子获取/计算comparison、纯净值nav、report、业务配置展开与stage_log |
| `src/research/etf_bottom/` | ETF历史价格位置观察；由日报消费，不生成策略动作 |
| `src/trend_engine/`、`src/selection/universe.py` | 现有股票指标计算、资产身份与观察池来源；按 Market Facts 消费边界保留 |
| `scripts/exploration/` | 手动调查与探测，生产不导入 |
| `scripts/validation/` | 可复现离线批量验收 |
| `scripts/audits/` | 价格依据等专项审计，默认只读 |
| `dashboards/` | 本地研究看板目录页及目录刷新工具；报告本体仍保存在`data/audits/`，不在此复制或托管 |
| `tests/` | 对应生产/研究/通信边界的回归与失败路径 |

`src/research/baskets/` 只保留正式 Basket Compare V2：`comparison.py` 计算、`nav.py` 提供净值、`report.py` 渲染；旧 `src/research/baskets/legacy/` 及 legacy CLI 已删除。

`longbridge_pilot.py`中的生产适配能力已提取为`longbridge_source.py`，旧试运行命令与无调用审计脚本删除；不能为历史名称再复制一套生产实现。

## 路径与迁移纪律

正式数据路径统一由`src/common/paths.py`及各来源模块定义；新增跨项目消费不得依赖未版本化的可变目录，来源与价格口径由`config/observation_sources.yaml`等数据契约约束。数据路径整理不得改变正式指标口径或历史快照摘要。

`dashboards/index.html` 是本地文件入口，不是 GitHub Pages 或报告托管目录。运行 `make dashboards-index` 按 `data/audits/` 中最新可用报告刷新入口、数据日期与更新时间；宽基指数 updater 在重建 HTML/ZIP 后自动刷新，持仓看板导出后需手动刷新目录。

原始快照字节及摘要保留，迁移只重定位分区引用。当前日报与篮子报告从迁移后的事实重建并复核，研究Hub单独重钉摘要，正式outputs指针不因整理目录而切换。历史发布回执不批量篡改。

不要新增“第2版/最终版”重复模块；先明确所属职责。探索逻辑转生产时应删除临时入口，并把可复用能力放入正式模块。历史材料不是当前运行说明。
