# A 股宽基指数技术分析

用同一张离线 Plotly 页面查看中证500和中证1000的历史收盘走势、人工选定的价格轨道和关键时点。两张图默认展示最新三年，可分别切换近1/3/5年或全历史；悬停查看读数，完整工具栏保留缩放、平移、重置和下载等操作。为避免误触，默认关闭滚轮缩放、拖拽操作及双击复位；需要时可通过工具栏启用缩放或平移模式。

## 打开图表

解压专题包后，在浏览器打开包内的 `csi500_price_channel.html`。在 AKsignal 中，图表及专题压缩包存放于 `data/audits/a_share_index_technical_analysis/`。

| 指数 | 收盘数据范围 | 上轨锚点 | 下轨锚点 |
| --- | --- | --- | --- |
| 中证500（000905） | 2007-01-15 起，截止日见 `sources.json` | 2010-11-10、2025-09-28 | 2010-07-05、2025-06-23 |
| 中证1000（000852） | 2014-10-17 起，截止日见 `sources.json` | 2019-04-04、2025-09-28 | 2018-10-18、2025-04-07 |

每条轨道按日期轴连接两个收盘点位，并用同一斜率延长至最后一个交易日。两张图的灰色中轨取当日上下轨的中点，延长线末端按整数点位标注。2025-09-28 是非交易日，两张图都使用本指数 2025-09-26 的收盘值作为该日期的上轨锚点。图中事件文字保留研究时的标注。

## 复现与继续研究

### 手动增量更新

在 AKsignal 根目录执行一次 updater，即可补齐至 T-1 并重建 HTML 和专题 ZIP。

```bash
.venv/bin/python scripts/exploration/a_share_index_technical_analysis/updater.py
```

T-1 按上海时间今天之前最近的交易日确定，使用项目 `XSHG` 有界交易日历。周末和节假日回溯至前一交易日，超出日历覆盖范围时报错。`--as-of YYYY-MM-DD` 可指定参考日期，目标为该日期之前最近的交易日。`--data-only` 只更新数据。

首次从 2026-11-20 行情及 `official-20261123-pe-ttm` 补充版本启动，此后从 `sources.json` 的最新截止日增量获取。每只指数请求新增区间并重叠一个已验收交易日，长期未更新时按最多365自然日拆分请求。最新范围已齐全时跳过取数。CSV 和 Parquet 同时包含行情和 `pe_ttm`，官方 `peg` 空值保留为空。

每次建立独立 `research-<目标日>-<批次标识>` 数据版本，原始响应、请求回执和质量审计仍位于 `data/sources/csindex/{raw,processed,audits}/history/<代码>/`。全部指数完成日期、OHLC、重叠记录和文件验收后，原子更新 `sources.json` 中的路径、摘要、截止日和行数。目标日尚未发布、交易日缺失或重叠历史变动均报错，失败候选保留用于排查。并发启动会报错提示已有 updater 运行。

数据发布后调用 `generate.py --package`。如果绘图阶段失败，数据已提交，可再次执行 updater 重建图表。更新范围由本目录 `sources.json` 确定，包含中证500、中证1000和沪深300。沪深300以 `official-20261124-pe-ttm` 为历史基座，自 2005-04-08 起纳入专题数据，图表展示中证500和中证1000。

### 历史基座与绘图

专题的取数入口是 AKsignal 的共享工具 `scripts/audits/backfill_csi500.py`。它按指数分批保存中证官方原始响应、请求回执、CSV、Parquet 和质量审计。已有匹配快照时，直接运行下方绘图命令即可。

需要重放本专题的取数批次时，从 AKsignal 根目录执行：

```bash
for code in 000905 000852; do
  .venv/bin/python -m scripts.audits.backfill_csi500 --index-code "$code" --end 2026-11-20 --run-id official-20261123 || break
done
```

回填工具复用已保存的请求回执，缺少回执时访问官方接口。上述命令重放初始基座批次；日后增量使用 updater。核对当前研究输入时以 `sources.json` 锁定的 SHA-256 为准。

在 AKsignal 根目录运行，使用已有的 Python 环境和中证官方长历史回填。

```bash
.venv/bin/python scripts/exploration/a_share_index_technical_analysis/generate.py --package
```

`generate.py` 保存锚点、连线规则和标注。若只需更新 HTML，可省略 `--package`。`sources.json` 记录各指数输入 CSV 的路径、范围、SHA-256 和来源审计路径。再生成图表时需要 AKsignal 内的两份绘图 CSV，离线查看图表只需 HTML。

从解压目录运行脚本时，可用 `--repo-root` 指定 AKsignal 根目录，用 `--output` 指定生成文件。该专题作为独立探索维护，使用独立回填快照。
