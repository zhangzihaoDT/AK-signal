# 数据存储架构

业务来源取舍由 `config/decisions/`与`config/observation_sources.yaml`定义；工程模块见 `docs/PROJECT_STRUCTURE.md`。这里说明数据在哪、是什么、由谁读写。

```text
data/
├── sources/                         正式来源数据
│   ├── longbridge/
│   │   └── raw/
│   │       ├── daily/               每日原始快照、manifest、精确日期分区引用
│   │       ├── core_indices/        固定指数独立快照、身份取证、manifest与日期引用
│   │       └── baskets/             篮子研究独立close_only快照及manifest
│   ├── cffex/                      中金所官方衍生品来源
│   │   └── raw/daily/               按日期原始日CSV、manifest与日期分区
│   ├── csindex/                     中证官方指数来源
│   │   └── raw/core_indices/        原始HTTP响应、标准快照、manifest与日期引用
│   ├── tdx/
│   │   ├── raw/day/                 官方.day历史基座（1,318个文件）
│   │   ├── processed/               128行业价格表与独立收益/RPS
│   │   └── audits/                  增量获取、CRC/摘要、HTTP版本记录
│   └── etf_em_sina/                  ETF专用路由数据域
│       ├── raw/                     已有ETF供应商日线缓存，实际来源逐序列记录
│       ├── processed/               master/daily/signals/positions/manifests
│       ├── audits/                  来源诊断与获取审计
│       └── state/                   增量补数检查点
├── hub/                             Hub研究报告引用及摘要，不是行情
├── reference/                       证券参考清单
├── audits/                          保留的历史来源审计证据
└── legacy/                          旧股票/申万/研究数据，不是正式备用
    ├── raw/
    ├── processed/
    └── state/
```

长桥原始JSON含源日期、来源、价格依据和摘要，不是CSV加工结果。其趋势与位置加工产物直接进入隔离批次的 `outputs/stock_metrics` 与 `outputs/observation`，由发布层管理，不在来源目录重复复制一套。篮子加工结果同样在独立批次outputs中；data/hub只保存引用。

ETF历史缓存含不同年代的东财/新浪路由，按单一ETF模块数据域保存，以真实元数据区分来源，不能为凑目录拆分或伪造旧来源。不同来源不得未经口径核对拼接同一序列。ETF raw是供应商日线缓存，并非原始HTTP响应。

TDX官方.day为原始数据基础，仅追加新交易记录；加工价格库与RPS可重建。不能将processed指标反写原始.day。重复历史修订明确报错，目标日不完整不生成正式行业横截面。

raw不可被计算或展示静默修改；processed保留输入版本/日期/来源。数据缺失应报错或标记不可用，不能从legacy目录偷偷补齐。旧审计与已发布研究可追溯，但其结论不代表当前业务规则。

新来源须增加独立来源目录及schema说明；不要新增含糊的temp/final/new2等目录。原始事实只留一份，派生物放processed或正式批次outputs；大体积试验结果按清理回执移出工作树。

## ETF 分类维护数据

`reference/etf_classification/candidates/<run_id>/` 保存 Skill 候选、证据快照、输入摘要与审计；`publications/<version>.json` 保存发布回执。RunDay 不从候选生成分类，只读 `config/etf_classification_facts.json` 并锁定批次副本。不可变正式版本位于 `config/etf_classification_versions/`。证据不随每日行情重建，清理前检查 config 与历史批次引用。完整接口见 [ETF_CLASSIFICATION.md](../docs/ETF_CLASSIFICATION.md)。
