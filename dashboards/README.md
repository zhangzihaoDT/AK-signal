# 本地研究看板

`index.html` 是本机看板入口页，不是 GitHub Pages 站点。它只链接本地已生成的报告，报告及其行情数据仍留在 `data/audits/` 或历史批次 `.run_day/runs/`，不复制到看板目录。卡片按报告的数据截止日降序排列；数据日相同再按页面生成时间排列。

## 打开

在浏览器中打开 `dashboards/index.html`，然后点击看板卡片。目录页使用相对文件链接，无需启动本地服务器或联网。

## 当前入口

| 卡片 | 链接来源 | 手动更新命令 |
|---|---|---|
| Market Facts 日报 | `outputs/observation/observation_<date>.html`，日期读取 `outputs/run_day/publication.json` | `make run-day`（日报当前暂停发布；显示最后一期） |
| Holdings Dashboard | `data/audits/holdings_dashboard/<最新日期>/holdings_daily.html` | 重建 Data App 并导出 HTML 后执行 `make dashboards-index`（构建步骤见 `data/audits/holdings_dashboard/<日期>/app/AGENTS.md`） |
| CSI500 Price Channel | `data/audits/a_share_index_technical_analysis/csi500_price_channel.html` | `.venv/bin/python scripts/exploration/a_share_index_technical_analysis/updater.py` |
| Basket Compare V2 | 读取 `data/hub/basket_comparison.json` 的已登记指针，并校验报告/Manifest SHA-256 | `make basket-refresh START=YYYY-MM-DD END=YYYY-MM-DD`（或 `make basket` + 快照） |
| IC500 Full Trend | `data/audits/ic500_trend/ic500_trend_full.html`（离线内嵌 Plotly） | `.venv/bin/python scripts/exploration/ic500_trend_full.py --inline --output data/audits/ic500_trend/ic500_trend_full.html` |
| A-Shape V2 | `data/audits/a_shape_v2/official-*/a_shape_v2_report.html` | 重跑扫描后执行 `.venv/bin/python scripts/exploration/a_shape_v2_report.py` |

静态页面本身不能执行 shell 命令；你直接说“更新某个看板”即可，我会运行对应命令并执行 `make dashboards-index` 刷新目录。日报暂停发布期间，手动运行 `make run-day` 仍会发布新批次。

## 刷新目录

```bash
make dashboards-index
```

该命令从现有产物刷新卡片的报告链接、数据日期和报告更新时间。日报发布暂停期间，目录仍可打开最后一期日报，但不会自动生成新批次。

缩略图为页面内嵌的示意 SVG，不包含真实持仓或成交信息。目录页不复制报告内容，也不包含任何远程部署配置。
