# AKsignal — Investment Decision System

当前基线：**v0.7.2**（2026-10-25）。版本源为 `VERSION`，变更记录见 [CHANGELOG.md](CHANGELOG.md)。

AKsignal 通过可追溯的市场事实、策略信号和真实组合约束，辅助管理投资组合。投资者保留最终决策权。

## 四层架构

```text
04 · Portfolio / Position
仓位管理 · 持仓监控 · 风险暴露 · 调仓决策
            ↑ 决策约束与执行反馈
03 · Strategy
机会识别 · 信号生成 · 策略验证 · 买卖规则
            ↑ 标准化市场事实
02 · Market Facts
市场日报 · 行业趋势 · 篮子 Compare
            ↑ 行情数据与质量验证
01 · Data Foundation
数据源 · 历史行情 · 增量更新 · 数据质量
```

| 层级 | 回答的问题 | 核心产出 |
|---|---|---|
| Data Foundation | 数据是否完整、可信？ | 标准化行情及质量证据 |
| Market Facts | 市场正在发生什么？ | 市场事实与比较结果 |
| Strategy | 根据事实应该采取什么行动？ | 买卖信号及其独立验证依据 |
| Portfolio / Position | 结合实际持仓应该如何执行？ | 仓位建议、风险与调仓计划 |

**层间约束：** Facts 不知道 Strategy 的存在，也不编码策略语义；Strategy 不直接维护或重算底层行情；Position 不自行生成市场信号。Strategy 信号不等于仓位决策：Position 可以基于实际持仓和风险暴露拒绝或调整策略建议，但不能篡改市场事实。

**v0.7.2 完成重点：日报 04 研究注意力简报，区分新增与已有机会、突出关键变化，提供九组速览与折叠证据；v0.7.1 接入分组趋势启动观察，v0.7.0 完成 Basket Compare v2。** 展示解释见 [日报 04 信息架构](docs/DAILY_GROUP_ATTENTION.md)，不改变底层 V2 规则。Strategy 与 Portfolio / Position 是后续建设阶段，将按新架构重新设计；不以旧 Selection、Timing、回测或仓位代码作为实现蓝本。

当前 Market Facts 保留九项能力：固定池个股数据、全市场 ETF 数据、全行业指数数据、行业轮动、大类资产趋势、ETF 趋势活跃池、个股趋势、ETF 与个股底部位置、分组趋势启动观察。覆盖范围随配置和行情质量变化，固定池个股不等于 A 股全量。

**必须坚持：数据质量与市场事实可追溯；策略信号不等于持仓决策；投资者保留最终决策权。**

## 当前 Market Facts 与日报

Data Foundation（数据基础）：固定池股票、全市场 ETF、全行业指数。
Trend（趋势观察）：行业轮动、大类资产、ETF 趋势、个股趋势。
Price Position（价格位置观察）：ETF 与固定池个股的历史价格位置。
Daily Observation Report：统一日期、统一数据质量、统一发布。

日报按大类资产、行业轮动、ETF、个股观察四个板块展示，每部分标明来源；趋势和位置归入各资产板块，审计放数据质量区。个股按8个研究篮子加高现金流分类，目前68只有效、禾赛1只排除。正式来源见 [模块来源契约](docs/OBSERVATION_DATA_SOURCES.md)。

## 每日运行

```bash
make run-day
make run-day RUN_DATE=20261012
make run-day-offline RUN_DATE=20261012
make run-day-status
make run-day RUN_DATE=20261012 RUN_FLAGS=--resume
make run-day RUN_DATE=20261012 RUN_FLAGS=--restart
make run-day-check
make run-day-summary
```

主链：ETF更新/计算 → 长桥观察池个股事实 → Observation构建（含通达信增量/收益/RPS）→ 验收 → 统一发布。启动时固定最近完整交易日，失败从检查点恢复；代码或配置变化需 --restart。离线模式不联网补数。

当前每日产品只发布市场事实，不输出买入推荐或投资 action。未来 Strategy 与 Portfolio / Position 将分别建设，不能把事实层、信号层和仓位层合并。

## 交付

- `outputs/observation/observation_{date}.{html,json,md}`：一个观察日报入口。
- `outputs/observation/{etf_trends,asset_directions,industry_rotation,industry_directions,etf_positions,stock_positions}_{date}.{parquet,csv}`：完整观察事实。
- `outputs/stock_metrics/stock_metrics_{date}.parquet`：个股趋势事实。
- `outputs/stock_metrics/stock_update_audit_{date}.json`：个股行情审计。
- `outputs/run_day/final_check_{date}.json`、`publication.json`：验收与发布回执，不含投资 action。

批次 outputs 从空目录建立，验收后一次切换发布指针。失败保持上一完整日报；历史批次仍保存在 `.run_day/runs`，首次旧目录保存在 `.run_day/legacy`，不随新批次重复复制。

底部只描述历史价格位置。个股统一使用长桥ForwardAdjust，price_basis_verified=false表示供应商复权未独立认证。缺历史与数据洞保持未知，不伪造离底。

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

能力与边界见 [canonical 状态图](docs/ARCHITECTURE_MAP.md)；位置口径见 [POSITION_OBSERVATION.md](docs/POSITION_OBSERVATION.md)。

独立 [篮子研究报告](docs/RESEARCH_BASKETS.md) 使用8个研究篮子 + 高现金流参照、68只个股与长桥同源基准；季度再平衡为默认结构指数，买入持有为对照。数据源探索旧材料见 [历史归档](docs/archive/source_migration_20261016/README.md)。

目录职责：[业务架构与决策](config/decisions/README.md) · [项目工程结构](docs/PROJECT_STRUCTURE.md) · [数据存储架构](data/README.md) · [手动工具](scripts/README.md)。
