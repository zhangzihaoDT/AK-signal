# 业务架构决策

这里记录观察对象、主题归属、数据来源口径、边界与架构调整的原因。机器配置与决策记录放在同一业务层，修改时同步说明依据，避免只看到参数、看不到取舍。

- [2026-10-16 数据来源与观察架构](20261016_market_data_architecture.md)：当前已接受方案。
- [2026-10-28 日报信息架构重整](20261028-daily-information-architecture.md)：v0.7.3 日报展示契约。
- [2026-10-28 核心指数观察](20261028-core-index-observation.md)、[2026-10-28 IC 衍生品观察](20261028-ic-derivative-observation.md)：01 模块来源。
- [原候选架构与可行性思考](archive/20261016_market_data_feasibility.md)：已被正式决策替代，不是运行规则。

工程模块、命令、存储路径和测试说明放在 `docs/`；数据存储架构从 `data/README.md`进入。决策记录不会被机器配置loader当作参数读取。
