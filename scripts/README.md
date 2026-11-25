# 手动工具

- `exploration/`：短期探索、接口探测；当前保留通达信分日官方包探测。
- `validation/`：可复现批量验收；当前包含128个TDX行业覆盖/解析/网站收益核验。
- `audits/`：只读来源与价格依据审计，支持重放证据。

[A 股宽基指数技术分析](exploration/a_share_index_technical_analysis/README.md) 的复现脚本和来源清单位于 `exploration/a_share_index_technical_analysis/`。

固定日期专题的本地摘要页：`scripts/exploration/ic500_trend_full.py --inline` 生成 IC500 离线报告，`scripts/exploration/a_shape_v2_report.py` 从已验收 A-Shape V2 扫描结果生成摘要 HTML。两者由 `dashboards/index.html` 本地索引，不参与日报发布。

生产代码不导入探索脚本。工具从项目根执行，通用能力转入src正式模块后删除重复临时实现。输出进入对应来源audits或具名研究批次，不在scripts旁写临时结果。
