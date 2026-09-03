# config/ — 策略知识与可实验参数的事实源

> v0.6.1 Strategy Specification：**可研究、可扫描、可比较、可能按主题变化的内容进 config；
> 算法定义、执行语义、系统不变量留在代码**。业务代码经 `src/common/spec` 统一 Loader 读取（不直接读 YAML）。

## 分层一览（2026-08 命名收敛后）

| 文件 | 职责 | 消费方 |
|---|---|---|
| `theme_registry.yaml` | 主题注册表：bucket / theme / 行业焦点组 / ETF 关键词（含 v0.11.1 `etf_exclude_keywords` 反向排除）/ Tier 映射 | Layer①②③ |
| `selection_universe.yaml` | Selection 固定资产池（theme → tier → assets，含 ETF 与个股） | Selection / Tier 确认 / 回测 universe / 核心资产监控 |
| `research_observations.yaml` | 研究观察组（evidence_stage 等三正交字段；不参与 Layer③ 确认与候选） | Research Basket / 观察组行情抓取 |
| `strategy_spec.yaml` | Layer③ 选筹 Policy（etf_selection / stock_selection）+ 主题级回测策略（entry/exit + `strategy_id` + 权重） | Selection / 回测 / Portfolio |
| `indicator_spec.yaml` | RPS/MA 窗口、信号门限（ETF 趋势态 / 行业确认 / Tier Gate 阈值） | Layer①②③ / Replay |
| `execution.yaml` | 执行模型 / fee / slippage / leverage / pyramiding | Portfolio / Trade |
| `portfolio.yaml` | 组合构建（资金 / 持仓 / 单资产上限 / deploy / 权重） | Portfolio |
| `industry_data.yaml` | 申万行业数据模块自身配置（数据源 / provisional / storage / bootstrap） | SW 模块 |
| `market_data.yaml` | ETF 行情获取 Data Acquisition 参数（增量窗口起点 / EM 熔断 window-min_requests-failure_rate / Sina 并发 workers-retry） | etf update（fetch_policy） |
| `etf_classification.yaml` | ETF 资产桶分类（展示标签） | classifier / rotation 报告 |
| `research_baskets.yaml` | Research Basket 定义（来源 / 权重 / 基准） | Research Basket |
| `research_stage_log.yaml` | 观察组商业化阶段变更审计日志（genesis + entries 链） | stage_log 校验 / 未来 stage event study |
| `guojin_tradable_blacklist.csv` | 实战可交易黑名单 | Selection 账户映射 |

## 职责边界要点

- `theme_registry.yaml`（主题是什么、如何确认、ETF 如何归属）与
  `selection_universe.yaml`（主题具体跟踪哪些资产）分工明确：前者不维护股票清单，
  Tier 通过 `universe_tiers` 映射后者的 tier key。
- `selection_universe.yaml` 与 `research_observations.yaml` 是两个互不重叠的宇宙：
  前者进入交易候选与「核心资产监控」，后者只供研究观察（如中鼎股份在后者）。
- **研究迁移 Tier（participation=monitor_only）** 是两者的受控接口：
  资产在 selection_universe 获得监控资格，但商业化阶段事实仍从 research_observations
  只读联接（`evidence_source`），不产生交易候选资格（如 ai_infrastructure.auto_thermal_ai_cooling）。

## 命名迁移记录（2026-08）

| 旧名 | 新名 |
|---|---|
| `themes_two_directions.yaml` | `theme_registry.yaml` |
| `stock_universe.yaml` | 拆分为 `selection_universe.yaml`（themes）+ `research_observations.yaml`（observation_groups） |
| `strategies.yaml` | `strategy_spec.yaml` |
| `indicators.yaml` | `indicator_spec.yaml` |
| `sw_industry_rps.yaml` | `industry_data.yaml` |
| `etf_buckets.yaml` | `etf_classification.yaml` |
| `research_baskets_stage_log.yaml` | `research_stage_log.yaml` |

`execution.yaml` / `portfolio.yaml` / `research_baskets.yaml` / 黑名单保持原名。
仅重命名不改内容也会改变 `config_hash`（文件名参与哈希），属预期中的配置迁移；
历史里程碑文档中的旧文件名保持原样，作为当时状态的记录。

## Hash 边界

- `config_hash` = `theme_registry` + `selection_universe` + `research_observations` + `strategy_spec` + `indicator_spec` + `execution` + `portfolio` + `industry_data` + 黑名单，order-independent。
- `universe_hash` = 实际参与运行的资产集合（排序后哈希）。
- `rule_version` = v0.7.0（算法变化才改；配置数值变化只改 config_hash）。
- **`market_data.yaml` 不进入以上 hash**：它只控制「怎么抓行情」，相同最终行情下 Layer①②③
  结果不变，不应因抓取参数变化触发 replay parity / config_hash 失效。

## _legacy/（归档，不参与运行）

历史遗留配置，仅作参考，**不参与任何信号/回测/哈希**：
- `etf_signal_rules.yaml`（旧 ETF 规则草案，无代码引用）
- `etf_universe.yaml`（旧 ETF 门控草案，仅被已删除的 `load_gate_config` 读取）
- `guojin_tradable_verified_backup.csv`（原人工验证白名单备份，黑名单机制已取代）
