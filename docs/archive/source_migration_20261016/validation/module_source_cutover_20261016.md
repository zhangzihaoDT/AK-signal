> 历史归档：已由正式来源架构替代，仅供追溯，不作为当前运行规范。

# 模块独立来源切换验收

2026-10-16；目标交易日2026-10-15。根据用户明确指定落实：个股长桥、ETF东财/新浪、行业通达信；不跨模块备用，不强制统一分类。

## 代码与配置状态

- `config/observation_sources.yaml`是模块来源真源，缺配置或含未授权回退时失败，不隐式切回旧来源。
- `observation stock-metrics`现使用长桥生产入口。个股与长桥沪深300基准均完成在线获取；个股趋势、收益率、位置共用单批快照。旧个股CSV未读取/覆盖。
- 禾赛HK02525由配置显式排除，不豁免其他股票的日期门控。机器行保留excluded，正文不生成其趋势或位置判断。
- Daily移除申万update/calculate；Observation构建不读取申万价格或指标。行业声明TDX Research Industry，价格未就绪则unavailable，不拿申万补位。
- ETF仍使用现有EM/Sina路由；新取行情记录实际来源、端点和价格口径，已知口径不同时禁止合并；旧来源未知保持unknown_legacy。

## 实际数据验证

| 验收项 | 结果 |
|---|---|
| 观察池个股 | 57只current，1只excluded，核心无missing/stale |
| 个股相对强度基准 | Longbridge 000300.SH，行情日2026-10-15 |
| 数据来源 | 个股ForwardAdjust、基准NoAdjust指数，均为长桥；未请求其他个股供应商 |
| 趋势/位置一致性 | 通过同snapshot_id门控；故意混入另一快照的测试被拒绝 |
| 离线恢复 | 精确日期长桥快照复用成功；不联网，不读旧CSV、不回退上一日 |
| ETF数值Parity | 1552行rotation全部旧非来源/运行审计列逐字段一致；仅新增/修正来源元数据 |
| ETF历史来源 | 现存1552份计算序列无逐行来源证据，明确unknown_legacy；不追认为东财 |
| 行业 | provider=tdx，classification=TDX Research Industry，unavailable，未产生TDX RPS |
| 报告验收 | ok=true、status=PARTIAL、errors=[]；TDX未接通和原有ETF缺口明确展示 |

PARTIAL不是长桥核心日期失败。原有ETF诊断仍列512390、560650两只目标日缺口；个股位置不再把按配置排除的禾赛报作更新事故。

## 证据位置

- 长桥完整窗口与基准：`data/market_facts/longbridge/runs/20261015/ea1d87bf70c64782bb0a73e0e329818b/manifest.json`
- Manifest SHA256：`071a7fafe005f9efb14b4f5219ff2c6efd4f40eca70e69677ee8b9892e370799`
- 隔离预览：`.run_day/source_migration_preview/20261015/outputs/`
- HTML：该目录下`observation/observation_20261015.html`
- 最终验收：该目录下`run_day/final_check_20261015.json`

现有正式outputs发布指针未切换。新配置将在后续Daily运行中使用；原检查点代码/配置签名已变化，需要显式--restart，不能用旧检查点冒充新源产物。

## 测试与限制

相关完整回归436 passed：market_facts、observation、daily_run、etf_signal、trend_engine及SW价格日期验收；后续原始事实准入字段/输出列清理追加通过market_facts回归。compileall与git diff --check通过。

仍未实现TDX行业价格获取及TDX RPS，345是分类快照候选终端规模，不宣称正式分母已经验收。供应商复权算法未独立认证，price_basis_verified保持false；此标记不再被错误解释为必须与旧EM/Sina价格相同才能接入长桥。
