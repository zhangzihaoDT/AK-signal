# A 股宽基指数技术分析

用同一张离线 Plotly 页面查看中证500和中证1000的历史收盘走势、人工选定的价格轨道和关键时点。两张图可分别缩放，双击主图复位。

## 打开图表

解压专题包后，在浏览器打开包内的 `csi500_price_channel.html`。在 AKsignal 中，图表及专题压缩包存放于 `data/audits/a_share_index_technical_analysis/`。

| 指数 | 收盘数据范围 | 上轨锚点 | 下轨锚点 |
| --- | --- | --- | --- |
| 中证500（000905） | 2007-01-15 至 2026-11-20 | 2010-11-10、2025-09-28 | 2010-07-05、2025-06-23 |
| 中证1000（000852） | 2014-10-17 至 2026-11-20 | 2019-04-04、2025-09-28 | 2018-10-18、2025-04-07 |

每条轨道按日期轴连接两个收盘点位，并用同一斜率延长至最后一个交易日。两张图的灰色中轨取当日上下轨的中点，延长线末端按整数点位标注。2025-09-28 是非交易日，两张图都使用本指数 2025-09-26 的收盘值作为该日期的上轨锚点。图中事件文字保留研究时的标注。

## 复现与继续研究

专题的取数入口是 AKsignal 的共享工具 `scripts/audits/backfill_csi500.py`。它按指数分批保存中证官方原始响应、请求回执、CSV、Parquet 和质量审计。已有匹配快照时，直接运行下方绘图命令即可。

需要重放本专题的取数批次时，从 AKsignal 根目录执行：

```bash
for code in 000905 000852; do
  .venv/bin/python -m scripts.audits.backfill_csi500 --index-code "$code" --end 2026-11-20 --run-id official-20261123 || break
done
```

回填工具复用已保存的请求回执，缺少回执时访问官方接口。核对生成结果时以 `sources.json` 锁定的 SHA-256 为准。取得其他截止日的行情时使用新的批次名。

在 AKsignal 根目录运行，使用已有的 Python 环境和中证官方长历史回填。

```bash
.venv/bin/python scripts/exploration/a_share_index_technical_analysis/generate.py --package
```

`generate.py` 保存锚点、连线规则和标注。若只需更新 HTML，可省略 `--package`。`sources.json` 记录两个输入 CSV 的路径、范围、SHA-256 和来源审计路径。再生成时需要 AKsignal 内的这两份 CSV，离线查看图表只需 HTML。

从解压目录运行脚本时，可用 `--repo-root` 指定 AKsignal 根目录，用 `--output` 指定生成文件。该专题作为独立探索维护，使用独立回填快照。
