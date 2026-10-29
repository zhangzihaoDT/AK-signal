# 核心宽基观察

2026-10-28。固定注册真源 `config/core_indices.yaml`，来源真源 `config/observation_sources.yaml` 的 `indices`。日报「01 市场环境」依次展示核心宽基、衍生品观察、风格参照、ETF 分类概览。ETF 分类概览按产品汇总，不作为独立大类资产基准。全部注册指数固定展示，不受 ETF 产品数量、成交额、活跃筛选、排名或 Top N 影响。

## 范围与身份

8 个宽基：沪深300、中证500、中证1000、创业板指、科创50、中证2000、中证全指、中证A500；风格参照为中证红利低波动指数（简称红利低波，H30269）。每项保留编制方、指数代码、供应商代码及日历路由；代码不与 ETF 或股票代码混为一个身份。

指数按注册逐项指定来源：7个已覆盖宽基使用长桥 OpenAPI / NoAdjust，中证2000（932000）与红利低波 H30269 使用中证指数官方网站价格指数行情，统一 `index_level`。不强制同一供应商，不自动跨来源回退或拼接，也不使用ETF价格替代。长桥在线逐批核验名称、代码和CNIX身份；中证官方逐条核验 indexCode/indexNameCn。静态接口缺失的交易所/币种元数据不追认为已验证，`identity_verified=false`、`price_basis_verified=false`；配置的 exchange 是源接口路由与日历映射，不表示指数编制方。

20261027 验证：7个长桥宽基及中证官方932000、H30269各243条近一年日线可用，指数9/9覆盖。长桥SDK对H30269.SH返回invalid symbol，静态信息为空；红利低波明确注册provider=csindex、provider_symbol=H30269。932000已核验官方代码、中文全称及简称，并固定注册为中证官方来源。详见 [来源测试](validation/CSINDEX_H30269_20261028.md)。

指数身份参考：

- [中证全指编制方案（000985）](https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/20231208175438-000985_Index_Methodology_cn.pdf)
- [中证A500证券编码（000510）](https://www.csisc.cn/zbscbzw/c100267/202409/f1a5ced63209459692b5541c6c278cbc.shtml)
- [中证2000官方单张（932000）](https://csi-web-dev.oss-cn-shanghai-finance-1-pub.aliyuncs.com/static/html/csindex/public/uploads/indices/detail/files/zh_CN/932000factsheet.pdf)
- [中证红利低波动编制方案（H30269）](https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/H30269_Index_Methodology_cn.pdf)

## 事实口径

规则 `core-index-facts-v1`，独立计算当日/5/20/60交易日价格指数收益和 MA20/MA60。收益为 `close[t]/close[t-n]-1`，机器值为小数；需要完整的 n+1 个日历交易日报价。价格指数收益不含分红再投资，不视为 ETF 收益或全收益。指数点位仅用于各自时间序列观察，不直接比较点位高低；不加入 ETF 或行业 RPS 横截面。

均线状态：收盘在 MA20/MA60 之上且两条均线较5交易日前均上升，为“均线上行”；同时在两条均线之下且均下降，为“均线下行”；其余有效状态为“均线交错”。需要65个连续交易日；数据不足显示“待数据完整”，不映射为弱势。

获取窗口统一为目标日前365个自然日至目标日。A500 的800日探索窗口有无效开盘价，未修复或认证该窗口；近一年窗口通过严格 OHLC 校验。已保存的早期失败批次保留在原始数据目录。这一观察模块不计算360日位置，不宣称近一年快照覆盖更长历史。

各收益窗口独立验收；历史不足标记 `INSUFFICIENT_HISTORY`，报价缺口标记 `DATA_GAP`，不前填、不按行数跨缺口计算。目标日缺失、未收盘或源日期未验证时所有数值为空。真实末次源交易日可用于解释缺失，但不显示成目标日报价。交易日历复用有界独立规则模型，非交易所全历史认证。

## 构建、验收与发布

唯一日常入口仍为 `make run-day`；`observation build` 在线构建独立指数快照，离线模式只读精确目标日引用。代码/配置变化须 `RUN_FLAGS=--restart`。股票与 ETF 既有入口保持独立。

原始数据按供应商分别保存在 `data/sources/{longbridge,csindex}/raw/core_indices/runs/{trade_date}/{run_id}/`，包含逐指数不可变快照与manifest；`partitions/{trade_date}.json` 只指向该日取数批次。每份快照保留内容摘要，并以原始文件 SHA256 将抓取时间一起绑定至 manifest；manifest 绑定注册/来源/窗口签名。消费不依赖后续改变的分区指针。中证官方额外保存原始HTTP JSON及摘要；验收按字段名重新解析原始响应，确认与标准快照一致。成交量/金额单位未认证，保留原响应但不转换为已验证单位。AKShare是相同官方接口的适配器，不是第二个供应商；生产直连官方并按字段名解析，避免依赖JSON键顺序。

正式产物：`outputs/observation/core_indices_{date}.json/.parquet/.csv`。JSON 保存输入回执、全部注册行、来源、价格依据、源日期、日历证据、各窗口状态和模块覆盖。报告摘要绑定 JSON 摘要。验收从不可变输入重算事实，核对 parquet、HTML 和 Markdown；行情日、身份、配置、摘要或展示不一致均拒绝发布。

缺失映射或单指数取数失败只影响对应行，固定清单不会缩短。任一行未达到 AVAILABLE，模块标为 partial/unavailable，日报按现有规则发布 PARTIAL；显式 `--require-complete` 拒绝该批发布。普通调度可交付其他已验收事实，不自动反复补数。
