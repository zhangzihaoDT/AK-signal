> 历史归档：已由正式来源架构替代，仅供追溯，不作为当前运行规范。

# AKsignal 市场数据架构可行性验证

> 验证日期：2026-10-16。只读验证 Longbridge OpenAPI 与通达信研究行业候选；未修改生产链路、配置、依赖或行情缓存，未执行 Daily Pipeline、未调用交易接口。Longbridge 凭证从 `.env` 仅在进程内读取，未输出或持久化。

## 结论摘要

1. **长桥适合作为固定股票池主源的有条件候选**：58 只固定池股票（56 CN、2 HK）及 7 只代表 ETF 均返回 2026-10-15 日线，65/65 目标日覆盖；57/58 股票和 7/7 ETF 达到 360 个报价日。禾赛 HK 02525 从 2025-09-16 有历史、截至 10/15 共 256 行，尚不能覆盖 360 日位置窗。
2. **长桥复权值得继续验证，但不能据此切全池**：官方接口提供 `NoAdjust` 与 `ForwardAdjust`；8 个样本在这两种口径之间有 5 个出现大量历史 close 变化。对 5 只 CN 股票的 ForwardAdjust 与本地 EM qfq 历史比对，3 只逐日 close 完全一致，另 2 只存在价格级差异，但 5/20/60/120/360 日收益差异为 0。另有 56 只股票样本与 10/12 EM qfq 参考的绝对 close 逐点匹配为 21/56，与 Sina qfq 参考为 0/56。结果支持长桥 ForwardAdjust 与 EM qfq 在部分样本/区间兼容，但**不证明 58 只全部同口径**；Sina 与 EM/长桥的历史调整不能直接拼接。
3. **ETF 代表样本通过目标日与短/中期历史检查，但有重要全历史边界**：7 只 ETF 全部有 10/15 日线、均超过 360 行；本次查询显示样本历史最早均为 2020-10-16。对 510300、518880 单独查询 2020-10-15 之前的多个区间，两种 adjustment mode 均返回 0 行。Longbridge 文档的 A 股一般历史范围不能外推为 ETF 全覆盖；完整 1,554 ETF universe 尚未验证。
4. **通达信分类清单可提取，但当前验证到的行情路径不能供 RPS 使用**：一次完整 `zhb.zip`（1,343,791 bytes）含 467 个 881xxx 节点及 X... 分类键；层级键长度分别为 3/5/7 字符的 30/128/309 个。配置 marker 的 122 个 `0` 节点全部有子节点、345 个 `1` 节点没有子节点；345 可作为避免父子重复的“终端节点”候选横截面，但是否作为正式 RPS 层级仍需业务定义。881062、881123 的标准 TDX quote 返回空；分类 K 线接口仍报 `TdxFunctionCallError`。**本次未获得任何可核验 TDX 行业日线，所以没有计算 TDX RPS，也没有拿申万数据填充。**
5. **当前迁移结论**：Longbridge 可进入有条件主源候选，尚不具备替换全市场 ETF 行情链的证据；通达信 881xxx 分类快照完整，但在获得稳定、带日期的自身价格源前，不能充当 Daily 行业价格基准。EM/Sina 继续作为当前运行源/回退，不与长桥复权历史无规则混接。

Longbridge 是与 EM/Sina 不同的 API 分发端；它的原始行情生产/分发链是否与现有源独立，未由 API 响应证明。历史 K 线响应只含 symbol/OHLC/volume/turnover/timestamp，不含公司身份说明或 ETF 跟踪指数；本次没有调用静态主数据接口。因此固定代码身份仍由当前证券配置/发行人资料维护，ETF tracking-index gap 仍在。

## 验证范围与调用限制

- Longbridge：OpenAPI SDK `longbridge 5.2.1` 临时安装在 `/var/folders/.../T/opencode/longbridge_sdk`，未加到项目依赖；使用 `.env` 中已有 legacy App Key/Secret/Access Token，调用只读历史 K 线接口。
- 固定股票 universe 从 `config/selection_universe.yaml` 经 `stock_items()` 去重，确认 58 个代码：56 CN + 2 HK。ETF 代表样本 7 只：510300、512480、159819、518880、513100、511990、159985。
- 完整样本执行 219 个历史 K 线请求，含 ForwardAdjust 全样本及 8 个调整对照标的；另有少数只读 smoke/ETF 早期历史请求。完整样本 65 个唯一代码，含 smoke 的额外代码在本次会话总计 66 个唯一代码。请求按不超过官方 60 次/30 秒限制串行节流；无 API 请求错误、无交易接口调用、无本地行情/缓存写入。
- TDX：只读连接公开节点 `180.153.18.170:7709` 获取完整 `zhb.zip` 配置；读取 `tdxzs3.cfg` 的 881xxx 清单。K 线/现价探测关闭自动重试，范围仅为用于身份验证的 881062、881123 样本，不扫描全网节点。

## 1. Longbridge：股票与 ETF 行情覆盖

### 1.1 API 权限、额度与数据更新

| 项目 | 文档 / 本次证据 | 可行性判断 |
|---|---|---|
| 接口 | `history_candlesticks_by_date(symbol, Period.Day, AdjustType, start, end)`；代码格式 `ticker.SH` / `ticker.SZ` / `ticker.HK`；返回 OHLC、volume、turnover、timestamp | 支持按证券取日线；不是一次批量返回整个 1,600 只 universe |
| 历史范围 | 官方文档称日/周/月/年历史一般覆盖：A 股 1999-11 起、港股 2004-06 起；单次最多 1,000 根 | 每次查询按小于 1,000 个交易日切段；文档总范围不等于具体 ETF/证券必有完整历史 |
| 复权模式 | 官方 `AdjustType.NoAdjust` / `AdjustType.ForwardAdjust` | ForwardAdjust 是供应商调整序列；本次只做样本比对，不直接等同于所有历史供应商的同一复权 anchor |
| 权限 | SDK 会话报告 CN LV1 Real-time、HK Real-time/LV2、US LV1；官方文档称历史 quote API 有 OpenAPI quote 权限要求，Basic 对应 CN/US LV1、HK BMP 延迟行情 | 本次读取权限成功；没有查询账户资产、交易量、剩余额度或付费账单 |
| 月额度 | 官方历史 quote 文档按自然月限制 100–3,000 个唯一证券代码，随账户资产/交易量档位变化；同一代码同月重复取历史 K 线只计一次 | 本次 65 个正式样本代码低于最低 100 档；全市场 1,554 ETF + 58 股票约 1,612 个代码，至少需要能覆盖约 2,000 月唯一代码的额度档，并留出其他用途余量。当前账户档位/剩余额度 UNKNOWN |
| 速率 | 历史行情文档标示最高 60 次/30 秒 | 若按单证券历史 K 线更新全 1,612 个代码，理论限速下界约 13.4 分钟/每轮（未含网络延迟/失败）；初始历史回填需要多段调用，时间更长。批量报价接口可另行优化，但本次未将它视为已验证替代 |
| 更新时效 | 本次 2026-10-16 11:44（Asia/Shanghai）查询，58/58 股票样本及 7/7 ETF 的 latest daily bar 均为 2026-10-15 | 证明数据不晚于本次查询可用；历史 K 线响应只给 bar 日期，不给首次发布时间。**每日源端具体发布时间 / SLA 尚未验证** |
| 费用 | 历史 K 线文档标为免费权限，但有账户档位和按月代码额度；当前环境只证明该凭证可读 | 不推断长期商业授权、超额度价格或全池使用的实际费用；需确认账户额度条件 |

### 1.2 实际样本覆盖

完整样本请求窗口按 `2018-01-01..2022-01-31`、`2021-12-01..2025-01-31`、`2024-12-01..2026-10-15` 切段，保留重叠窗口以检查拼接日期。

| 范围 | 样本 | 10/15 bar | 历史行数/覆盖 |
|---|---:|---:|---|
| 固定股票池 | 58 | 58/58 | 58/58 ≥250 行；57/58 ≥360 行；唯一不足 360 行的是 HK 02525（256 行，首行 2025-09-16） |
| ETF 代表样本 | 7 | 7/7 | 7/7 ≥360 行；样本首个日期均为 2020-10-16，约 1,453–1,454 行 |
| Combined | 65 个唯一代码 | 65/65 | 所有本次样本均无重复日；API 调用无错误 |

对两个较早上市 ETF（510300.SH、518880.SH）再查 2012-01 至 2020-10 的 NoAdjust / ForwardAdjust 分段，六个请求全为空。故这次实测表明**当前该 API 路由的 ETF 样本没有提供 2020-10-16 前序列**；准确原因/全 ETF 范围未知。此点足以阻止将“股票日线覆盖广”直接外推为“ETF 历史覆盖全面”。

### 1.3 复权基准比较

#### NoAdjust 与 ForwardAdjust 对照

以 `|close_forward - close_no_adjust| > 0.01` 计数，比较同一 Longbridge 代码、同一日期：

| 代码 | 共同日期 | 价格模式差异日数 | 最新共同日绝对差 |
|---|---:|---:|---:|
| 000429.SZ | 2,129 | 2,086 | 0 |
| 600941.SH | 1,155 | 1,148 | 0 |
| 000887.SZ | 2,129 | 2,039 | 0 |
| 02525.HK | 256 | 197 | 0 |
| 02498.HK | 683 | 0 | 0 |
| 510300.SH | 1,454 | 1,277 | 0 |
| 518880.SH | 1,454 | 0 | 0 |
| 159985.SZ | 1,454 | 0 | 0 |

这证明调整开关会对部分证券历史序列产生显著变化；不能将不同 mode 的价格直接拼接。0 差异也只代表样本与观测窗口中没有超过一分的变化，不代表长期公司行为已独立审计。

#### ForwardAdjust 与本地历史参考

以 10/12 留存的 `em_qfq` / `sina_qfq` 参考序列为共同日期样本，比较 close 差异阈值 0.01：

- 56 只 CN 股票中，Longbridge ForwardAdjust 与 EM qfq 参考逐日完全匹配 **21/56**；与 Sina qfq 参考完全匹配 **0/56**。两路参考不是同一序列。
- 五只额外检查了收益率窗口的股票中，ForwardAdjust 与 EM qfq 在 5/20/60/120/360 日收益上相同到 0.000001 个百分点：000429、600941、000887、002050、600900。前三只 close 全历史逐点匹配 EM qfq；002050 / 600900 存在部分 close 绝对差异，但样本窗口收益保持相同。
- 结论只支持“ForwardAdjust 在部分样本与 EM qfq 数值相容”，不代表全部 58 只历史 basis 已通过，也不证明其公司行动算法等于 EM/Sina 的公开定义。Sina qfq 显著不同，不能作为无条件的复权序列 fallback。

### 1.4 与当前 Daily 的可比性

当前最新正式 Daily 报告是 2026-10-14，Longbridge 样本已包含 10/15 bar。由于日期不同，不据此声称“Longbridge 与最新日报的 10/15 信号相同”。只在共同历史日期上做过样本 basis 对照。Longbridge 行情事实样本可覆盖固定池趋势及 60/120 日观察；对 02525 的 360 日位置历史不足；全池固定股票的 Longbridge 趋势结果/信号没有重算。本次未把 Longbridge 数据注入 Observation。

## 2. 通达信行业体系：身份、清单、行情和 RPS

### 2.1 行业身份与全量清单

完整 `zhb.zip` 从 TDX 标准节点成功读取，含 `tdxzs3.cfg` 与 `incon.dat`；本次解压解析 `tdxzs3.cfg`，得到 **467 个唯一 881xxx 研究行业节点**，清单保存在 [`docs/validation/tdx_research_industry_universe_20261016.csv`](../../../docs/validation/tdx_research_industry_universe_20261016.csv)。档案大小 1,343,791 bytes、传输完整。配置记录没有明确发布日期/版次标签，故这里只能称“本次取得的 TDX Research Industry 配置快照”。

| 观察到的 taxonomy key 深度 | 节点数 |
|---|---:|
| 3 字符 | 30 |
| 5 字符 | 128 |
| 7 字符 | 309 |
| 合计 881xxx | 467 |

原始 marker 字段为 0 的 122 个节点各自都有更深层子节点；marker=1 的 345 个节点在当前 467 项中都没有子节点。若后续要独立排名，**345 个结构终端节点**是避免把父行业和子行业重复当作同等行业票的候选集合；这是由当前配置树结构得到的可行设计，不代表 TDX 官方已把该集合命名为标准 RPS universe。配置快照生效日、行业节点编制变化和成分归属仍需 versioned master 固化。

具体身份不是申万重编码：已核查例子中，TDX 881062 冶钢原料映射到 TDX 自身 `X1301`；SW2021 801043 冶钢原料对应 `230300`。名称相同而分类代码/体系不同，不能做 881↔801 的价格替换或拿 RPS 排名验同一性。本次没有要求两套 RPS 排名一致。

### 2.2 TDX 行情路径与新鲜度

| 探测 | 样本/接口 | 结果 | 解释 |
|---|---|---|---|
| TDX 标准行情证券清单 | 180.153.18.170:7709，标准市场 0/1 | 已有 2026-10-16 验证未在 0/1 清单找到 881xxx | 881 research-industry 清单来自配置包，不是标准股票/指数列表 |
| Daily K 线，category=4 | 881062、881123 × `get_security_bars` / `get_index_bars`，market=1 | 4 个样本请求均报 `TdxFunctionCallError: calling function error` | 不是成功的空行情；源/接口兼容性失败，未形成历史序列 |
| 当前报价 | 881062、881123 `get_security_quotes` | 返回空记录 | 无法核验最新价/交易日期 |
| 历史 K 线（先前探测） | 881062 category=9 | 原先 3 次请求均为 `SOURCE_ERROR` | 不计作 `SOURCE_EMPTY`；本次 category=4 变化参数仍失败 |

该结论限定于已验证的标准节点/协议路由，不泛化为“通达信所有商业客户端/API 均无 881 数据”。本机/官方授权 TDX 客户端之外的专用行业行情服务、其他授权端点没有验证。配置包没有逐行业的 last-updated/source trade date，不能以配置下载成功替代每日价格更新证明。

### 2.3 RPS 结果

**本次 TDX RPS：NOT_COMPUTED（没有一行可验证的 TDX 行业日线）。** 行业身份快照虽有 467 个 code，但 quote 样本当前报价为空，category=4 代表 K 线接口错误；既无统一交易日、也无历史 close 横截面。没有用 SW close、同名指数、TDX 成分股涨跌或 synthetic series 填充成“TDX RPS”。

若未来取得同一 TDX taxonomy 的有效日线，首轮方法建议以 345 个 terminal-node 候选为 denominator，沿用市场交易日、close return 和 RPS5/10/15 的已定义算法，但独立命名 `tdx_rps`、记录 TDX taxonomy version/data source；同一日期 RPS 与 SW2021 属于不同 universe，不比较绝对排名相等。此方案不改变当前日报。

## 3. 方案比较与架构结论

| 维度 | Longbridge OpenAPI | 通达信 TDX Research Industry | 当前 EM/Sina + SW |
|---|---|---|---|
| 目标日样本覆盖 | 58/58 固定池股票 + 7/7 ETF 到 10/15 | 467 个身份配置；测试行业最新价 0/2，目标日 K 线 0/2 有效 | 10/14 ETF/行业正式报告；10/15 申万 source-date verified 0/124 |
| 历史可用性 | 固定股票池样本 57/58 ≥360 bars；ETF 7/7 ≥360，但样本的首日为 2020-10-16 | 不能验证任何行业历史 bar | 申万历史缓存可见，但目标日发布受单点 source gate |
| 身份/体系 | `ticker.SH/SZ/HK`；证券代码和名称可由 Longbridge security metadata 进一步核对；本次只测历史 K bars | 配置快照有 467 个 code 和分类键，与 SW 是不同体系；快照没有版本日期 | SW active snapshot 124 个、主源代码属申万体系；行业版本在 Daily Master 中仍未显式记录 |
| 价格 basis | NoAdjust/ForwardAdjust 可选；样本 ForwardAdjust 与 EM qfq 部分相同、Sina qfq 不同 | 行业 close 未取到，basis 无从判断 | 行情有 EM/Sina fallback 路由；现有股票缓存已出现跨源/复权历史差异 |
| 更新时效 | 10/15 日线在 10/16 11:44 已可读；无首次发布时间证据 | 最新价空、K 线 source error，无每日更新证据 | SW 10/14 09:20 可用；10/15 到 10:06 未有有效 124/124 |
| 可扩展成本/运行时 | 当前样本 65 unique（全会话 66）；quota 100–3,000/month。全量 1,554 ETF +58 股票约 1,612 unique/月，至少需满足约 2,000 额度档并留余量；单证券 history endpoint 下理论最小取数时间约 13.4 分钟 | 静态配置包 1.34 MB 可取；价格协议失败，不能计入低成本可用行情源 | 当前已有缓存与增量/fallback；稳定性不足但不新增账户额度成本 |
| 分析价值 | 固定池价格可行；与 EM qfq 五样本的 horizon returns 相容，具备进一步试跑价值 | taxonomy 提供更细行业分组潜力，但没有日线，趋势/RPS 识别价值未实测 | 当前 124 SW 行业 RPS 是可计算的正式事实基线；不代表另一套 taxonomy 排名必须与之相同 |

### 建议决策

1. **Longbridge：继续作为固定池股票价格主源/日线备选的可行候选，不立即切全市场主源。** 58 只股票的目标日期覆盖良好；需要把 02525 的 360 日位置窗口不足和不同 vendor ForwardAdjust/QFQ basis 的证据放进决策条件。全市场 ETF 扩展前，要先确认账户每月可用 unique target quota ≥1,612、ETF 历史是否能覆盖 360 日前目标窗口，以及是否能在预算内满足更新时限。
2. **EM/Sina：保留现有市场事实回退，但不要跨来源拼接调整后的历史序列。** 本次样本中 Longbridge ForwardAdjust 跟 EM qfq 更接近，和 Sina qfq 的历史差异显著；用来源码+复权 mode 进行整条时间序列切换，或者做可审计 rebase，不要只拿“同日 close 一致”作为替换证明。
3. **TDX：现在可作为独立分类研究目录，不可作为正式行业价格基准。** `881xxx` 目录按 TDX taxonomy 独立保留，与 SW2021 不做硬性排名对齐；在拿到有合同/日期/历史的 TDX Research Industry daily close 前，不发布 TDX RPS、不替换 SW 价格源。
4. **下一步 RPS 试验门槛**：先取得可靠 TDX 881 daily bars（至少 360 日、全 345 候选 terminal universe、同一交易日期、缺码有清单）并覆盖数个交易日；再单独计算 TDX RPS5/10/15。用数据覆盖、连续性、阶段分类变化和代表行业作分析价值比较，避免以 SW 排名一致作为通过条件。

## 证据与限制

- Longbridge SDK 官方文档：[Getting Started](https://open.longbridge.com/docs/getting-started)、[History Candlesticks](https://open.longbridge.com/docs/quote/pull/history-candlestick)、[Quote API Overview](https://open.longbridge.com/docs/quote/overview)、[Adjustment Types](https://open.longbridge.com/docs/quote/objects)。
- TDX 协议接口范围参考：[Pytdx 标准行情](https://pytdx-docs.readthedocs.io/zh-cn/latest/pytdx_hq/)（市场 0/1）与[扩展行情](https://pytdx-docs.readthedocs.io/zh-cn/latest/pytdx_exhq/)（由服务端 `get_markets()` 返回可用市场）；本报告的接口错误只覆盖所探测节点/代码/方法。
- Longbridge 样本：本地固定池配置、10/12 adjustment audit reference、10/16 只读 OpenAPI 结果；API 结果未缓存到项目目录，只有本报告聚合结果。Access token 未写入文档或命令输出。
- TDX 全量列表：本次完整 `zhb.zip` 配置快照，CSV：[tdx_research_industry_universe_20261016.csv](../../../docs/validation/tdx_research_industry_universe_20261016.csv)。此前身份调查及 sample history error 见 `docs/validation/tdx_sw_identity_20261015.md` 与 `tdx_801043_881062_identity.md`。
- 不测 TDX 的完整 467 行历史、没有 RPS 数值；也不将 API failure 解释为所有 TDX 渠道都没有该数据。
- 不触发交易、账户余额查询、Daily Pipeline、缓存回写、依赖变更或正式日报发布。
