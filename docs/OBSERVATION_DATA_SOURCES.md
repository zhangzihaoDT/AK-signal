# AKsignal 正式模块来源

更新：2026-10-16。配置真源 `config/observation_sources.yaml`。

| 模块 | 正式来源 | 口径与范围 |
|---|---|---|
| 个股趋势/位置 | 长桥OpenAPI | Longbridge ForwardAdjust；69只配置，禾赛02525排除，68只有效；沪深300同源000300.SH |
| ETF/大类资产 | 东方财富/新浪 | 保留模块内部路由，记录实际来源与现有ETF价格口径 |
| 行业轮动 | 通达信官方.day | TDX Research Industry 20261016，固定128个二级研究行业，自算收益率/RPS |

AKShare是适配器，不是独立供应商。三模块独立维护历史，不跨模块备用，不强制分类映射。SW2021只供历史研究，不进入正式日报。

## 长桥个股

`src/market_facts/longbridge_source.py`负责只读行情与时区适配；`stock_daily.py`构建同源趋势/位置，`stock_universe.py`组合固定池与8个研究篮子注册。代码补足后按市场/代码去重，研究新增注册仅monitor_only，不修改策略资格。Daily不读取旧EM/Sina个股缓存。

每批原始快照：`data/sources/longbridge/raw/daily/runs/{trade_date}/{run_id}/`；分区引用：`partitions/{trade_date}.json`。manifest及快照摘要、配置指纹、目标日、来源与基准必须通过校验，离线只读精确目标日批次。失败不读旧源或旧日期。SDK依赖longbridge==5.2.1，凭证仅在进程内读取，不写报告。

price_basis_verified=false表示供应商复权算法未独立认证，不以跨供应商价格相等作为准入条件。日历为有界独立规则模型，不冒充官方全历史认证。关键窗口缺口、目标日缺失、基准失败均显式报错/降级。

东方海外国际00316的完整OHLC窗口存在2025-10-02源端异常，同源近一年有界重取后趋势可用，360日位置保留历史不足和history_limitation。独立篮子报告使用显式close_only完整窗口，不认证或修复其OHLC。

## ETF

新取行情逐行保存provider、endpoint与series_price_basis，产物记录实际序列/目标日来源。已知不同价格口径不能增量拼接；旧缓存未知元数据保持unknown_legacy/unknown，不追认为已验证。来源记录不改变ETF RPS/趋势/流动性算法。ETF日收益机器值为百分点，日报显示时与TDX小数收益分别转换。

## 通达信行业

`data/sources/tdx/raw/day`为用户提供的官方.day历史基座，完整保留。128个固定研究行业清单在`config/tdx_research_industry_20261016.csv`。命中目标日零请求；缺日从官方ZIP按成员Range读取、本地只追加。已有历史修订明确报错，不静默重写。细则见 [TDX正式集成](TDX_INDUSTRY_INTEGRATION.md)。

收益率close[t]/close[t-N]-1，RPS平均并列百分位；各窗口记录有效样本数，历史不足为INSUFFICIENT_HISTORY、缺失不填0。更新失败/目标日不完整则模块UNAVAILABLE，不能用旧数据或申万回退。

## 日报、研究报告与发布

日报按大类资产、行业轮动、ETF、个股观察排序，各部分标明来源；趋势和位置归入资产板块，质量审计集中末尾。个股按8个研究篮子加高现金流分类，跨篮子可重复，69/68计数均按个股去重。题材分类不改变确认权或策略准入。

`basket_compare`独立展示8个研究篮子 + 高现金流参照、68只股票，长桥收盘价与同源沪深300；组间/组内等权 + 季度再平衡为默认结构指数，买入持有为对照，报告同时给出RS、参与度、集中度和重叠诊断；不含汇率/费用，不视为真实组合或经验证总回报。见 [篮子报告](RESEARCH_BASKETS.md)。

正常入口`make run-day`、离线`make run-day-offline`。代码/配置变化后旧检查点需要`--restart`。日报原子发布与研究Hub指针独立；失败保持上一发布，模块可按PARTIAL规则交付，`--require-complete`拒绝不完整发布。当前日报开发预览仍在`.run_day/source_migration_preview/20261015/outputs`，清理不会改变正式outputs指针。

## 验收与历史

- [TDX官方包验收](validation/TDX_OFFICIAL_PACKAGE_ACCEPTANCE_20261016.md)：128覆盖与2560项收益核对。
- [长桥篮子切换记录](validation/BASKET_COMPARE_LONGBRIDGE_20261016.md)。
- [切换历史归档](archive/source_migration_20261016/README.md)：过期探索结论不再作为当前规范。
