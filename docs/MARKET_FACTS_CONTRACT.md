# Market Facts Contract v1

当前正式来源和边界见 [模块来源](OBSERVATION_DATA_SOURCES.md)。契约代码 `src/market_facts/contract.py`；长桥生产适配器 `longbridge_source.py`，每日构建 `stock_daily.py`。longbridge==5.2.1 属生产依赖；有界日历再生成工具的可选依赖在 `requirements-calendar-tools.txt`。

| 事实维度 | 必须保留 |
|---|---|
| 证券身份 | security_id、market、exchange、currency、asset_type、provider_symbol |
| 来源 | provider、adapter、endpoint；AKShare不是供应商 |
| 价格依据 | price_basis、adjustment_version、price_basis_verified，未知明确unknown |
| 分类 | classification_version；TDX固定128个二级行业，不混申万历史 |
| 日期 | target_trade_date、源端source_trade_date、源时间戳、fetched_at |
| 摘要 | contract_version、内容哈希snapshot_id、批次manifest摘要 |
| 日线 | open/high/low/close/volume/turnover；close_only投影必须显式声明 |

严格校验重复/未来日期、非有限/非正价格、负量额与OHLC关系。不改写异常价格，不补造缺失交易日。上市不足、历史不足、目标日不完整、身份未知、复权未认证分别记录，不互相替代。

生产快照不可变，抓取时间不参与价格内容哈希。消费时重新校验内容摘要、来源和精确目标日。长桥趋势/位置共享同一批快照；基准滞后不生成当日相对强度。行业历史基座按源端记录独立更新，原始价格日必须与目标日一致。

`calendar.py` 使用已提交的有界CN/HK独立规则模型，保存版本/摘要/逐日收盘时刻。不是交易所全历史认证；SZSE沿用A股日期代理，不能称独立深交所核验。`consume.py` 复用既有趋势与个股位置计算，缺日历、关键窗口缺口、未收盘或OHLCV不足时明确不可用。

长桥Python SDK可能返回主机本地naive时间戳，适配器先恢复时间点再转换为市场时区，不将naive时间直接声明为UTC。[SDK实现依据](https://github.com/longbridge/openapi/blob/main/python/src/time.rs)。测试覆盖aware跨日和naive本地反向恢复。

`adapters.py`保留旧缓存研究适配与回归测试用途；未知来源不追认为正式源。Daily不通过该模块回退旧个股源或申万。试运行流程、早期生产准入标签与供应商探索已移到 [历史归档](archive/source_migration_20261016/README.md)，不再是现行运行入口。

### 跨市场休市验收

批次 `trade_date` 仍为统一目标日，各资产以所属市场有界独立规则日历核验。
目标日休市时，仅允许最近实际交易日行情，记录 `data_status=market_closed`、
`market_session_status=closed`、`expected_source_trade_date`、真实 `source_trade_date`、
日历版本/摘要/authority 与原始快照 ID。趋势、位置、相对强度截至该实际交易日，
相对强度基准亦截至同日；不生成休市日价格或收益，不修改原始快照。
休市不阻止其他市场、ETF、行业与报告继续运行。最近应有交易日缺失、日历超出覆盖、
历史不足或 OHLC 异常仍执行原有质量门控，不能借休市豁免。
