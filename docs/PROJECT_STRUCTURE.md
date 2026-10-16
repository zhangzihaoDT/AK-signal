# 项目结构与职责

## 三种架构分治

- `config/`：业务架构与配置，包含对象范围、分类、来源口径、策略定义与`decisions/`决策记录。业务取舍的“为什么”放这里。
- `docs/`：项目实现架构、模块职责、运行流程、部署与验收说明；`archive/`只作历史追溯。
- `data/`：实际数据及存储架构说明，按来源、raw/processed/audits/state组织，见`data/README.md`。不将大数据放进config或docs。

## 代码与工具

| 目录 | 职责 |
|---|---|
| `src/market_facts/` | 正式事实契约、来源适配、日历门控、长桥个股与TDX行业生产 |
| `src/observation/` | 汇总已生成事实，输出日报并验收 |
| `src/etf_signal/` | ETF专用行情路由与指标实现 |
| `src/hub/` | 只读Hub通信：daily状态/报告、baskets研究报告引用与摘要验证；不抓行情、不计算策略 |
| `src/daily_run.py`、`daily_schedule.py` | 批次执行、恢复、调度，与Hub通信层分离 |
| `src/daily_hub.py` | 既有Worker命令兼容入口，仅委派src.hub.daily |
| `src/research/baskets/` | 当前长桥篮子获取/计算comparison、纯净值nav、report、业务配置展开与stage_log |
| `src/research/baskets/legacy/` | 仍有研究用途的旧多源计算/季度再平衡/报告，显式legacy-run/legacy-fetch，不作正式备用 |
| `scripts/exploration/` | 手动调查与探测，生产不导入 |
| `scripts/validation/` | 可复现离线批量验收 |
| `scripts/audits/` | 价格依据等专项审计，默认只读 |
| `tests/` | 对应生产/研究/通信边界的回归与失败路径 |

`comparison.py`是唯一正式篮子计算入口，`report.py`只渲染，`nav.py`提供固定初始权重净值函数。已删除旧重复build_comparison，合并longbridge_comparison/comparison_report的平行命名。旧run/fetch更名legacy-run/legacy-fetch；make basket/basket-refresh只走长桥正式路径。

`longbridge_pilot.py`中的生产适配能力已提取为`longbridge_source.py`，旧试运行命令与无调用审计脚本删除；不能为历史名称再复制一套生产实现。

## 路径与迁移纪律

正式运行路径统一由`src/common/paths.py`及各来源模块定义；新代码禁止硬编码旧data/market_facts或data/etf_signal路径。业务源策略由config定义，数据目录迁移不改变策略权重或来源口径。

原始快照字节及摘要保留，迁移只重定位分区引用。当前日报与篮子报告从迁移后的事实重建并复核，研究Hub单独重钉摘要，正式outputs指针不因整理目录而切换。历史发布回执不批量篡改。

不要新增“第2版/最终版”重复模块；先明确所属职责。探索逻辑转生产时应删除临时入口，并把可复用能力放入正式模块。历史材料不是当前运行说明。
