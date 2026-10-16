# 数据源切换后的项目整理

日期：2026-10-16。范围：整个项目的文件/引用扫描；不读凭证内容，不清理.git和.venv，不改正式发布指针。

## 目录归属

| 所属 | 内容 | 入口 |
|---|---|---|
| config | 业务架构、对象范围、来源口径、每次架构调整思考 | [业务决策](../config/decisions/README.md) |
| docs | 工程架构、模块职责、运行与验收 | [项目结构](PROJECT_STRUCTURE.md) |
| data | 来源数据、原始/加工/审计/状态分层 | [数据架构](../data/README.md) |
| scripts | 探索、验收、专项审计，分别归子目录 | [手动工具](../scripts/README.md) |
| src/hub | Hub通信与摘要验证，不计算行情 | daily.py、baskets.py |

## 实际清理

- 清除757个已被正式链路替代的试运行文件，442,990,425字节（约422.5 MiB）；包括data/pilots及四个无生产调用的试运行审计模块。删除前压缩备份，并逐文件比对SHA256。
- 长桥试运行模块中的正式能力提取到longbridge_source.py，删除旧pilot命令和重复依赖表；可选日历工具依赖单列requirements-calendar-tools.txt。
- 正式篮子只保留comparison.py、nav.py、report.py；删除旧build_comparison，合并平行的longbridge_comparison/comparison_report文件名。仍有研究用途的旧计算/季度再平衡/多源工具集中legacy/，命令明确legacy-run/legacy-fetch。make basket和basket-refresh仅走长桥正式入口。
- Hub通信集中src/hub；src/daily_hub.py仅保留既有Worker命令兼容入口，无重复业务实现。
- 探索/验证/审计脚本分入scripts/exploration、validation、audits，更新引用与执行入口。
- 14个数据目录按来源与raw/processed层重定位；TDX获取审计独立audits，Hub指针独立data/hub。没有复制原始价格制造第二事实源，也没有留下旧路径回退软链接。
- 旧业务可行性思考归config/decisions/archive；旧工程调查与试运行记录归docs/archive。AGENTS及当前事实契约改为单一现行说明，完整旧手册仅作历史归档。
- 根目录参考工作簿移入data/reference；生成缓存及.DS_Store另行清理，不清理正式报告或引用中的源快照。

## 保留与验证

TDX的1,318个.day历史文件完整保留；ETF行情缓存和全部历史加工数据按新目录保留；长桥原始快照及内容摘要保留。旧申万/股票/研究数据进入data/legacy，不作正式来源回退。已发布批次、恢复检查点、旧来源审计证据保留，避免“清理”破坏追溯。

333份长桥快照重新通过契约/内容摘要校验。迁移后重建2026-10-15日报为CONFIRMED，行业128/128；篮子9类/68只、544日期的研究报告重建，Hub的报告/数据/manifest摘要重新钉定并验真。正式outputs符号链接保持原值，未以整理目录为由重发旧日期日报。

**680项定向回归通过**，覆盖正式行情、ETF、行业、个股、日报、发布、Hub、篮子、来源审计。全量测试曾在474项通过后，遇到3个旧ETF研究测试缺少outputs/research/etf_bottom/universe.parquet及state_odds_events.parquet；扫描清理前目录清单确认这些产物原已不存在，未为通过测试重造研究结论或修改测试跳过规则。

## 清单与恢复

[清理回执](validation/SOURCE_MIGRATION_CLEANUP_20261016.json)包含删除前逐文件摘要、迁移映射、两份压缩备份位置及摘要。[扫描清单](validation/SOURCE_MIGRATION_FILE_INVENTORY_20261016.csv.gz)记录40,019个项目文件的路径/大小，排除.git、.venv、缓存、凭证和符号链接。

备份存放于系统临时目录，适合此次回退，不保证长期留存；关键正式事实与验收证据仍保留在项目中。需要恢复单项时先核对压缩包SHA256，再从对应相对路径提取到隔离目录并比较，避免整包覆盖当前项目。

## 根目录追加整理

2026-10-16经用户指定核查后，删除notebooks/（审计结果重放，与scripts/audits重复）、doc/（旧日流程记录）、tools/（无调用的旧Legulegu来源对照脚本）、STATUS.md（过期状态）；tmp/此前已移除。无运行或构建调用，STATUS的两处文字引用已修正。四份文件删除前有独立SHA256校验备份，位置见机器回执；现行审计结果与里程碑正文继续保留。
