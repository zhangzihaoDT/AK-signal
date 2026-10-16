# 通达信行业正式集成

2026-10-16：按用户授权直接启用，不再等待连续五日观察作为切换前置条件；每日质量门控继续执行，更新稳定性记录仍需持续积累。

## 正式链路

`data/sources/tdx/raw/day/*.day → 本地行业价格库 → 独立收益率/RPS → Daily Observation`

历史基座为用户提供的 `data/sources/tdx/raw/day`，现有 1,318 份文件保留；每日只更新配置中 128 个研究行业。正式分类固定 `TDX Research Industry 20261016`，行业清单及一级归属位于 `config/tdx_research_industry_20261016.csv`。SW2021 不进入正式日报，不充当备用。ETF 和个股模块来源及计算未改变。

## 增量与失败语义

- 目标日已存在于 128 份本地文件：0 个网络请求，直接使用已验证完整日线。缓存命中不是更新失败。
- 有行业缺少目标日：从通达信官方板块 ZIP `https://www.tdx.com.cn/products/data/data/vipdoc/tdxzs_day.zip` 读取中央目录及缺失行业成员。HTTP Range 以 ETag/If-Match 固定同一版本，验证 206、Content-Range、大小和成员 CRC；服务器不支持或版本改变则失败，不退回整包下载。
- 官方服务不提供本次已验证的单日 `.day` 记录接口，所以传输层仍需读取缺失行业的压缩历史成员（256 KiB 块缓存）；本地只追加新增记录。并非声称网络只下载当日 32 字节。
- 对照所有已有重叠日期的 OHLC/量额，历史被修改或删减则拒绝自动更新，等待明确复核。新增记录逐文件原子写；全部待更新文件先通过验证，正式计算仍要求 128/128 目标日完整，不发布部分横截面。
- 网络更新失败、文件缺失/损坏、目标日缺失：行业 `status=unavailable`、`status_code=UNAVAILABLE`，日报行业事实为空，其他模块按既有规则交付。在线失败后绝不回退旧价格快照。`--require-complete` 仍可拒绝不完整发布。
- 离线构建直接读取历史基座，要求精确目标日；不把旧日期改写为新日期。

## 指标规范

价格为 `.day` 整数 OHLC / 100 的未复权行业指数。收益率 `close[t]/close[t-N]-1`，N 为已有源端日线记录数；窗口 1/5/10/15/20/60/120/250。日期唯一且递增，价格关系及量额合法。未完成全部历史交易日历缺口认证，现阶段窗口按源端交易记录计数。

每个窗口独立计算横截面 RPS：`100 × (average_rank(return)-1)/(valid_count-1)`，并列用平均排名，缺失不填零；有效样本少于 2 时 RPS 缺失。固定分类池 128 不代表所有窗口分母均为 128。每行保留 `status_Nd` 和 `valid_count_Nd`；N+1 根不足时收益/RPS 缺失，状态 `INSUFFICIENT_HISTORY`。

目前电子化学品 881479 只有 73 根，因此 120/250 日有效横截面均为 127；1/5/10/15 日均为 128。日报展示 RPS5、RPS15 和事实趋势阶段，质量区域说明口径及历史不足。

## 入口与产物

正常 `make run-day` 已移除 SW 更新/计算；行业在线增量和计算由 `observation build` 在批次环境 `AKSIGNAL_OFFLINE=0` 下执行。离线每日入口和手工 build 使用本地基座。

可独立执行（无需重复导入已有历史包）：

```bash
.venv/bin/python -m src.market_facts.tdx_industry --date 20261015
```

`--archive path.zip` 为显式历史导入/验收入口，仅生成价格快照，不改写本地 `.day` 基座。常规更新使用已有基座及增量链路。

派生价格、获取审计、指标位于 `data/market_facts/tdx/{prices,acquisition,metrics}_{date}.*`。日报行业事实为 `outputs/observation/industry_rotation_{date}.{parquet,csv}`，含来源、价格口径、分类版本、源日期、生成时间、各指标状态及有效样本数。

本次已用用户历史基座成功生成 2026-10-15 的 128 行行业事实并重建预览日报；正式代码与配置已启用，未为此重跑 ETF/个股在线获取，也未改动正式发布指针。代码/配置变化后已有 run-day 检查点须 `--restart`。
