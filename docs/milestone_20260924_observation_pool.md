# 项目里程碑 · A 股观察池扩展至 59 只（2026-09-24）

> 类型：观察池 / 研究候选扩展（Reference & Research，**不进入生产决策链**）
> 日期：2026-09-24
> 变更：观察池 51 → 59（+8）

---

## 1. 一句话

在原有 **51 只 A 股观察池**基础上，新增 **8 只存储 / 记忆主题（`memory_and_storage`）候选**，观察池扩至 **59 只**。
8 只最初为 `candidate_pending_quotes`（待行情核验）；**已于同日补齐 2026-09-23 日线行情与技术指标（`data_status=current`）**。原有 51 只的行情与技术指标**原样保留（逐字段校验 0 差异）**。
分两批注册到 `ai_infrastructure.memory_and_storage`（均为 `monitor_only`）：**首批 3 只**（兆易创新/联芸科技/江波龙，§8），**二批 2 只**（佰维存储/普冉股份，§8b）；其余 3 只（德明利/聚辰股份/香农芯创）暂留参考观察池。

## 2. 边界（明确不做什么）

- 不修改 `config/theme_registry.yaml`（主题与确认 Gate 不变）；`config/selection_universe.yaml` 仅新增 1 个 monitor_only tier（§8）。
- 8 只候选中 **5 只已注册**（兆易/联芸/江波龙/佰维/普冉，均为 `participation=monitor_only`），只进 stock-metrics 核心资产监控；**其余 3 只**（德明利/聚辰股份/香农芯创）**未注册**，不进入 Layer② 主题确认、Layer③ 交易候选、stock-metrics 主链路。
- 所有 8 只候选不产生任何买入信号：已注册 5 只仅监控不交易，未注册 3 只此时点仅存在于参考文件。
- 行情核验先于注册（Engine 直跑一次性补齐）；注册后 5 只进入日常 stock-metrics 流程（§8/§8b/§9）。

## 3. 新增 8 只候选

| 代码 | 名称 | tier | 定位 | 注册 | 证据 |
|---|---|---|---|---|---|
| 603986 | 兆易创新 | `flash_memory_design` | NOR/NAND/利基型 DRAM 设计，存储产品线较完整 | ✅ monitor_only | [gigadevice](https://www.gigadevice.com.cn/product/flash) |
| 301308 | 江波龙 | `memory_modules_and_solutions` | 嵌入式存储 / SSD / 移动存储 / 内存条 | ✅ monitor_only | [longsys](https://cn.longsys.com/) |
| 688525 | 佰维存储 | `memory_modules_and_solutions` | 存储方案 + 主控 + 封测/先进封测 | ✅ monitor_only | [biwin](https://www.biwin.com.cn/about/company) |
| 001309 | 德明利 | `storage_controller_and_modules` | 自主存储主控与固件方案 + 模组 | ⏸ 暂留观察池 | [twsc](https://twsc.com.cn/about/index.html) |
| 688766 | 普冉股份 | `nonvolatile_memory_design` | NOR Flash / EEPROM 等非易失存储 | ✅ monitor_only | [puyasemi](https://www.puyasemi.com/about.html) |
| 688123 | 聚辰股份 | `memory_interface_adjacent` | EEPROM + 服务器内存条 SPD/SPD+TS | ⏸ 暂留观察池 | [giantec-semi](https://www.giantec-semi.com/spdxilie) |
| 688449 | 联芸科技 | `ssd_controller` | SATA / PCIe·NVMe SSD 控制芯片 | ✅ monitor_only | [maxio-tech](https://www.maxio-tech.com/product/11628/) |
| 300475 | 香农芯创 | `memory_distribution_and_products` | 存储芯片代理分销 + 自主品牌 | ⏸ 暂留观察池 | [shannonxsemi](https://www.shannonxsemi.com/) |

> 均为主题初筛候选，非纯 DRAM/HBM 原厂，属研究观察，不作为买入信号。

## 4. 主题定位

`memory_and_storage`（存储 / 记忆）——覆盖 **设计**（兆易 / 普冉 / 聚辰）、**模组与方案**（江波龙 / 佰维）、**主控**（德明利 / 联芸）、**分销**（香农芯创）四个环节。

## 5. 事实源与产物

| 用途 | 路径 |
|---|---|
| 原始变更文件（51 继承 + 8 新增） | `data/reference/ashare_stock_list_20260924_candidate_update.csv` |
| 合并观察池（59 行，6 位代码归一；8 只已回填 0923 行情/指标） | `data/reference/ashare_stock_observation_pool_20260924.csv` |
| 前序池（51 行） | `outputs/stock_metrics/ashare_stock_list_20260923.csv` |

> 本文保留2026-09-24里程碑事实；当时的STATUS.md已因过期删除。当前业务范围见config/decisions，当前项目能力见ARCHITECTURE_MAP.md。

## 6. 校验

- 合并池 **59 行、symbol 唯一**。
- **51 只继承行逐字段一致**（close / return_5d / return_20d / trend_score / watch_level / action / participation / data_status / risk_flags），仅补回被算术去零的 6 位代码 —— 本轮补齐**再次校验 0 差异**（含新增列前的继承列全字段）。
- 8 只新行：`data_status=current`、`participation=monitor_only`（tier 语义保留在行内；Engine 类别字段仅原子管道的过滤位）。

## 7. 行情核验结果（2026-09-23，已回填）

技术指标一次补齐（`score_trend` 0-100、`watch_level`、`action`、`data_status=current`）；**均为技术评分快照，不代表买入信号**，且部分已命中「风险警戒」（强康/长下跌缺口需先看 MA / log 判据）。

| 代码 | 名称 | close | return_5d | return_20d | score_trend | watch_level | action | 风险 |
|---|---|---|---|---|---|---|---|---|
| 603986 | 兆易创新 | 398.72 | 0.0686 | 0.0158 | 55 | B | 风险警戒 | 跌破MA60/MA120 |
| 301308 | 江波龙 | 346.00 | 0.0208 | -0.0500 | 20 | C | 风险警戒 | 破MA20/60/120，RS负 |
| 688525 | 佰维存储 | 222.69 | 0.0445 | -0.0011 | 35 | B | 风险警戒 | 破MA60/120，RS负 |
| 001309 | 德明利 | 410.32 | 0.0232 | 0.0502 | 55 | B | 风险警戒 | 破MA60/120 |
| 688766 | 普冉股份 | 431.00 | 0.0806 | 0.0852 | 65 | B | 风险警戒 | 破MA60 |
| 688123 | 聚辰股份 | 168.11 | 0.1918 | 0.2882 | 90 | A | 风险警戒 | — |
| 688449 | 联芸科技 | 64.36 | 0.0248 | 0.0159 | 80 | A | 风险警戒 | — |
| 300475 | 香农芯创 | 179.03 | 0.0780 | 0.1742 | 80 | A | 风险警戒 | 破MA120 |

> 全部 8 只 action=风险警戒：`drawdown_from_high ≥ 0.15` 处的判定（未写回生产链路）；技术评分对存储主题分化明显（聚辰/联芸/香农 score≥80 vs 江波龙/佰维 <50）。

## 8. 后续

1. ✅ **行情核验**（2026-09-24 完成）：8 只候选日线行情与技术指标已补齐（online fetch，as_of=20260923，`data_status=current`）。
2. ✅ **首批注册 3 只 monitor_only**（2026-09-24）：兆易创新（存储芯片）/ 联芸科技（SSD 主控）/ 江波龙（存储产品）→ `config/selection_universe.yaml` `ai_infrastructure.memory_and_storage`（`participation=monitor_only`，`sw_industry=801081.SI` 半导体）；`src/trend_engine/inputs.py` `STOCK_TIERS` 同步补 `memory_and_storage`。只进 stock-metrics「核心资产监控」，不参与主题排名/四段信号/候选资格；`theme_registry.yaml` 不改（不进主题确认 Gate）。

## 8b. 二批注册 2 只 monitor_only（2026-09-24）

将 **佰维存储（688525）**、**普冉股份（688766）** 补入同一 tier `ai_infrastructure.memory_and_storage`（`participation=monitor_only`，`sw_industry=801081.SI`）：

- 佰维存储：存储方案 + 主控 + 封测/先进封测（`memory_modules_and_solutions`）
- 普冉股份：非易失存储 NOR Flash / EEPROM（`nonvolatile_memory_design`）

保留已有 3 只（兆易/联芸/江波龙）；**德明利、聚辰股份、香农芯创暂留观察池**（未注册）。注册后台动作同 §8：只进核心资产监控，无候选资格。

## 8c. 后续

1. 其余 3 只（德明利/聚辰股份/香农芯创）留候选池：行情指标已补齐，待评估。
2. 注册后进入 stock-metrics「核心资产监控」随 run-day 每日刷新（`make stock-metrics` 已验证 **60/60 current**）。

## 9. 校验（注册后）

- `selection_universe.yaml` 加载 **78 assets / 3 themes**；`detect_unregistered_themes=[]`；`cross_theme_assets` 仅原有 拓普/三花（`601689`/`002050`）。
- `memory_and_storage` tier **5 只**：theme=ai_infrastructure、participation=monitor_only、STOCK_TIERS 放行（stock items **60/78**）。
- `make stock-metrics`（离线）重建 `stock_metrics_20260923.parquet` **60 行 / 60 current**，5 只与池值一致（兆易 398.72/55/B、联芸 64.36/80/A、江波龙 346.00/20/C、佰维 222.69/35/B、普冉 431.00/65/B）。
- **角色映射修复**：`src/selection/selection.py` `TIER_ROLE_MAP` 补 `memory_and_storage → LEADER`（注册时遗漏，导致 Layer③ 观察池按 `role=None` 静默跳过）。修复后 engine（`include_stocks=True`，research/parity 路径）监控组 monitor_only **10 只** = 5 先例（auto_thermal：中鼎/银轮/飞龙/拓普/三花）+ 5 只（兆易/联芸/江波龙/佰维/普冉），全部 state=WATCH / recommended=False / reason_codes=`['monitor_only']`；每日 ETF-only 发布（`include_stocks=False`）个股容器为空，行为不受影响。`tests/selection` 196 + `tests/trend_engine` 131 全绿。

## 10. 观察池口径（2026-09-24 固化，participation 是 stock×tier 层属性）

观察池 59 只中 universe 认识 56 只，按**三个正交语义层次**记（不互相推导、不做加减，与 AGENTS.md「participation 是 stock×tier 层属性」段一致）：

| 层次 | 数量 | 问题 | 定义 | 明细 |
|---|---|---|---|---|
| unique stocks | 56 | 有多少只股票 | 观察池 ∩ `selection_universe.yaml` | 51 原池 + 5 已注册存储 |
| confirmation-capable | 48 | 多少只有主题确认权 | 存在 ≥1 个 tier 注册 tradeable 且该 tier 进入 Theme→Tier 确认 | 20 ai + 11 auto + 15 hc + 2 overlap |
| monitor-involved | 10 | 多少只至少有一个监控身份 | 存在 ≥1 个 tier 注册 monitor_only | auto_thermal 5（中鼎/银轮/飞龙/拓普/三花）+ memory 5（兆易/联芸/江波龙/佰维/普冉） |

补充（不参与推导）：10 只 monitor-involved 中 **拓普/三花**同时有 `china_auto_global.global_ev_components(tradeable)` 确认身份（overlap 双身份，也计入 48），其余 8 只仅有监控身份。需要「全体 monitor 相关股票」时写 **10 monitor-involved**。`select run` 的 `config_issues.cross_theme_assets` 是跨主题事实清单（`601689`→[ai_infrastructure, china_auto_global]、`002050` 同），与上述语义一致。观察池 CSV 中 002050/601689 行 participation 列保留 `tradeable`（表示 china_auto 身份），screen_note 注明 stock×tier 双身份，不压成股票级单一标签。
