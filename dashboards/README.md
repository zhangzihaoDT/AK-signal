# 本地研究看板

`index.html` 是本机看板入口页，不是 GitHub Pages 站点。它仅链接本地已生成的报告，报告及其行情数据仍留在 `data/audits/`。

## 打开

在浏览器中打开 `dashboards/index.html`，然后点击看板卡片。目录页使用相对文件链接，无需启动本地服务器或联网。

## 刷新目录

```bash
make dashboards-index
```

该命令从现有产物刷新卡片的报告链接、数据日期和报告更新时间。中证500报告的常规 updater 在重建 HTML/ZIP 后会自动刷新目录；持仓看板完成 HTML 导出后，运行一次上述命令即可更新到最新日期目录。

看板入口：

- `holdings_dashboard`：自动选择 `data/audits/holdings_dashboard/` 下最新日期目录中的 `holdings_daily.html`。
- `csi500_price_channel`：链接 `data/audits/a_share_index_technical_analysis/csi500_price_channel.html`，数据日期读取专题 `sources.json`。

缩略图为页面内嵌的示意 SVG，不包含真实持仓或成交信息。目录页不复制报告内容，也不包含任何远程部署配置。
