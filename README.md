# a-share-shortline-picks · A股短线选股

输入某交易日 T，在全市场（剔 ST、换手 ≥1%、成交额 ≥1 亿）内按已训练的 26 维排序权重打分，输出 Top30 候选名单（CSV + 渲染好的 HTML 报告）。

- 持有口径：**T+1 开盘买入 → T+2 收盘卖出**（B2close）
- 权重已通过排序层（IC > 0.01、月同向 ≥ 90%）与组合层（IS / OOS-A / OOS-B 三段 TopN 绝对收益为正）双向验证

## 目录结构

```
a-share-shortline-picks/
├── SKILL.md                  作业手册（任务、口径、验证纪律，先读这个）
├── README.md                 本文件
├── weights/
│   └── 统一三开.json          排序权重（26 维岭回归）
└── scripts/
    ├── 出名单.py              出某日候选名单（纯本地计算）
    ├── 训练权重.py            （可选）重训权重，需完整历史数据
    ├── 补数_历史K线.py        （可选）东财公开接口回补历史 K 线
    └── 补数_资金流.py         （可选）westock CLI 回补腾讯口径资金流
```

## 环境要求

- Python 3.x + numpy
- 数据目录（默认 `F:/aigp/alt_screening`，可用环境变量 `PICKS_DATA_DIR` 指定），需包含：
  - `daily/<交易日>/kline.jsonl` + `mf.jsonl`：当日全市场 K 线与资金流快照（收盘前时点）
  - 历史日 K jsonl（算量比/涨幅因子用，约需 21 个交易日）：`候选_kline_*.jsonl` / `latest_kline.jsonl` / `ws_kline_full.jsonl` 等
  - `close_snap_*.json`：流通股本 / 名称 / ST 标记快照
- 快照数据由外部定时任务生成；本包只消费，不负责生产快照。

## 快速开始

```bash
python scripts/出名单.py 2026-09-18
# 输出：<数据目录>/候选_新方案_<日期>.csv 与 .html
```

## 固定参数（不得随意改）

- 池子：全市场剔 ST → 换手 ≥ 1% → 成交额 ≥ 1 亿
- 排序：`weights/统一三开.json`（26 维岭回归）
- 取前 30，**剔封板**（保证名单里的票必然可买）
- 买卖口径：T+1 开盘买 → T+2 收盘卖
- 成本：双边 0.1~0.2%

## 免责声明

本项目仅用于个人研究，不构成任何投资建议。历史回测表现不代表未来收益。
