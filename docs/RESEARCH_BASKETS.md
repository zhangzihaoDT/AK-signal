# Research Basket

Research Basket 是独立于 Layer ①/②/③ 和 Portfolio Execution 的主题篮子研究模块。

## 定位

它回答：按照一组明确的资产、子组和权重规则持有一个主题篮子，历史表现如何，超额来自哪里。

它不直接生成买入建议，也不阻塞 `make run-day`。研究结论经过历史稳健性验证后，才可以被其他决策层显式消费。

## 配置

入口为 `config/research_baskets.yaml`。标的清单复用 `config/stock_universe.yaml`，研究配置只声明：

- 主题或观察组来源
- include/exclude 子组
- 权重方法
- 调仓方法
- 基准

当前默认篮子：

- `scania_china_watch`
- `china_auto_global_ex_oem`
- `ai_capex`

当前 `group_equal + buy_and_hold` 的含义是：组间等权、组内等权，使用前复权收盘价，不做期间调仓。

## 命令

```bash
python src/main.py research basket run --baskets all \
  --start 2025-08-20 --end 2026-08-19
```

只运行一个篮子：

```bash
python src/main.py research basket run --baskets ai_capex
```

## 产物

默认写入 `outputs/research/baskets/`：

- `{basket}_nav.csv`：篮子和基准归一化净值
- `{basket}_metrics.json`：收益、回撤、波动、Sharpe、胜率和超额
- `{basket}_groups.csv`：子组收益与标的数量
- `{basket}_constituents.csv`：展开后的研究成分
- `{basket}_manifest.json`：来源、权重、配置 hash 和覆盖元数据
- `basket_compare.html`：多篮子交互式对比图
- `basket_quarterly_compare.html`：季度调仓后的多篮子对比图
- `{basket}_rolling.csv`：20/60/120 日滚动超额、Sharpe、回撤
- `{basket}_contributions.csv`：个股收益贡献及 Top1/Top3 标记
- `{basket}_quarterly_nav.csv`：季度首个交易日重置权重后的净值

## 研究边界

- 当前数据刷新仍使用观察组价格获取工具，篮子计算本身纯离线。
- 港股和 A 股交易日不完全一致，统一到 HS300 交易日并对已开始交易的标的前值填充。
- 首个有效交易日前不填充。
- 当前结果存在静态资产池和幸存者偏差，不能直接视为可交易策略回测。
- 当前季度调仓采用季度首个交易日重置组间/组内权重，边界日不计入新季度收益。
- 当前滚动指标窗口为 20D / 60D / 120D：超额收益以百分点计，Sharpe 按 252 交易日年化，回撤为窗口内最大回撤。
- 个股贡献按篮子初始权重乘区间收益计算，`top1` / `top3` 标记贡献排名，不把负贡献从分母中静默剔除。
- 当前尚未加入市值权重和正式 `basket fetch` CLI。
