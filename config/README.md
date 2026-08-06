# config/ — 策略知识与可实验参数的事实源

> v0.6.1 Strategy Specification：**可研究、可扫描、可比较、可能按主题变化的内容进 config；
> 算法定义、执行语义、系统不变量留在代码**。业务代码经 `src/common/spec` 统一 Loader 读取（不直接读 YAML）。

## 分层一览

| 文件 | 职责 | 消费方 |
|---|---|---|
| `themes_two_directions.yaml` | 主题 / bucket / 行业焦点组 / ETF 关键词 / 启用状态 | Layer①②③ |
| `stock_universe.yaml` | 主题资产池（tier → 资产）、主题映射 | Selection / 回测 universe |
| `strategies.yaml` | 主题级策略（entry/exit 参数 + `strategy_id` + 权重） | 回测 / Portfolio |
| `indicators.yaml` | RPS/MA 窗口、信号门限（ETF 趋势门/个股合格线/确认阈值） | Layer①②③ / Replay |
| `execution.yaml` | 执行模型 / fee / slippage / leverage / pyramiding | Portfolio / Trade |
| `portfolio.yaml` | 组合构建（资金 / 持仓 / 单资产上限 / deploy / 权重） | Portfolio |
| `sw_industry_rps.yaml` | 申万模块自身配置（provisional / storage / bootstrap） | SW 模块 |
| `etf_buckets.yaml` | ETF 资产桶分类（展示标签） | classifier / rotation 报告 |
| `guojin_tradable_blacklist.csv` | 实战可交易黑名单 | Selection 账户映射 |

## Hash 边界

- `config_hash` = `themes` + `stock_universe` + `strategies` + `indicators` + `execution` + `portfolio` + `sw_industry_rps` + 黑名单，order-independent。
- `universe_hash` = 实际参与运行的资产集合（排序后哈希）。
- `rule_version` = v0.7.0（算法变化才改；配置数值变化只改 config_hash）。

## _legacy/（归档，不参与运行）

历史遗留配置，仅作参考，**不参与任何信号/回测/哈希**：
- `etf_signal_rules.yaml`（旧 ETF 规则草案，无代码引用）
- `etf_universe.yaml`（旧 ETF 门控草案，仅被已删除的 `load_gate_config` 读取）
- `guojin_tradable_verified_backup.csv`（原人工验证白名单备份，黑名单机制已取代）
