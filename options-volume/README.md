# 美股期权成交量趋势

跟踪一份固定观察池的**当日期权总成交量、看涨/看跌占比、Cboe 30 日隐含波动率（IV30）及其变化**。栏目对应 Zen Research 日报的「期权成交量趋势」。

**在线页面：** https://heber.github.io/cffex-basis/options-volume/

静态站托管在 GitHub Pages，作为 [heber/cffex-basis](https://github.com/heber/cffex-basis) 的 `options-volume/` 子目录发布。页面只读本目录里的 `data/latest.json`。GitHub Action 在每个工作日 21:30 UTC（北京时间次日 05:30）运行 `options-volume/scripts/fetch_options_volume.py`。

股指分红矫正贴水：https://heber.github.io/cffex-basis/

商品期货贴水：https://heber.github.io/cffex-basis/commodity/

A/H 双重上市溢价：https://heber.github.io/cffex-basis/ah-premium/

现金现货对期货的期现基差：https://heber.github.io/cffex-basis/spot-basis/

主要宽基 ETF 净申赎：https://heber.github.io/cffex-basis/etf-flow/

## 数据从哪来

不需要 API key。脚本只请求 Cboe 延迟行情的两个公开 JSON：

| 字段 | 接口 |
|------|------|
| 期权总成交量、看涨量、看跌量 | `https://cdn.cboe.com/api/global/delayed_quotes/charts/intraday/{symbol}.json` |
| IV30、IV30 变化、标的价格 | `https://cdn.cboe.com/api/global/delayed_quotes/quotes/{symbol}.json` |

成交量把分时里的 `calls_volume`、`puts_volume`、`total_options_volume` **按分钟相加**。这些分钟值是增量，不是一根一直往上走的累计线。常规时段文件大约从美东 09:31 到 15:59，共 389 根。

指数代码在 URL 里用 Cboe 前缀：`SPX` → `_SPX`，`XSP` → `_XSP`。

IV30 是 Cboe 公布的 30 日隐含波动率。它是 IVolatility **IVX** 的免费近似，**不是 IVX**。`iv30_change` 也照 Cboe 的原值写入。收盘后部分指数和 ETF 这个变化会是 0，页面写 0，不改成别的算法。

2026-10-19 收盘后曾把同一时刻的期权链逐合约加总，和分时合计对照：CLF、INTC 相差约 0.3%，IWM 大约 4%（分时文件的缓存时间更早）。每日任务用分时，因为期权链单是 SPY 就要数 MB，而分时大约 100KB。

## 为什么不是全市场前 20

查过、而且 GitHub Actions 上能直接打到的免费接口里，没有一份「当天、全市场、按标的合计」的前 20：

- **Cboe most-active**（`www-api.cboe.com/.../most_active/data/`）按**单个合约**排名，而且是**单个交易所**。`mkt=opt` 在 2026-10-19 收盘后返回的是 BZX 上的合约，量级是一万张上下，不是标的全天总量。`mkt=cone`（Cboe Options）当时 `calls` / `puts` 是空的。
- **OCC volume-query**（`https://marketdata.theocc.com/volume-query`）无需密钥，能按标的给出各交易所、客户/公司/做市商、看涨/看跌的张数。2026-10-19 当晚它拒绝任何晚于 **2026-10-16** 的日期，而且必须带 `symbol`，不能一次拉出全市场排名。所以它赶不上「美股收盘后当天」这张表，也补不上 IV。
- **Yahoo Finance options v7** 在无 crumb / cookie 时返回 401，不适合作为定时任务的来源。

因此名单固定为 2026-10-19 日报里的 20 个标的，每天只在这份名单里按成交量重排。要官方全市场排名加 IVX，需要付费源，例如 IVolatility（IVX 与成交量）、OCC 的商业批量文件，或 Polygon / ORATS 一类期权 API。本页不使用这些。

## 观察池

`scripts/universe.py`：SPY、QQQ、SPX、TSLA、NVDA、AAPL、IWM、MU、AMZN、INTC、SPCX、CRWV、XSP、ETHA、CLF、AMD、TQQQ、IREN、META、SMH。

少于 15 个标的有成交量和 IV30 时，脚本拒绝覆盖 `data/latest.json`。

仓库里的第一份快照是交易日 **2026-10-19**（美东 09:31–15:59）。20 个标的都返回了成交量和 IV30。按成交量排序后 SPY 第一，合计 12,026,735 张，看涨 46.24%，IV30 13.649，Cboe 公布的 IV30 变化是 0。这是收盘后的全天分时合计，不是日报在 09:40 的盘中切片，也不能拿来和 IVolatility IVX 逐项对账。

本地手动刷新（仅标准库）：

```bash
python3 options-volume/scripts/fetch_options_volume.py
python3 options-volume/scripts/test_options_volume.py
```

## 免责声明

仅供研究参考，不构成投资建议。数字是 Cboe 延迟公开接口的快照，可能延迟或缺失，也不保证与 IVolatility、OCC 日终文件一致。
