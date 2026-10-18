# 中国商品期现基差

跟踪 `commodity/` 里 35 个品种的**现金现货对期货**基差。现货来自生意社现期表，期货近月、主力、远月来自新浪最新价。

**在线页面：** https://heber.github.io/cffex-basis/spot-basis/

商品页上的「现货」是最近一张未到期合约，不是这里的现金价：https://heber.github.io/cffex-basis/commodity/

股指分红矫正贴水：https://heber.github.io/cffex-basis/

A/H 双重上市溢价：https://heber.github.io/cffex-basis/ah-premium/

## 基差

```
基差 = 现货 − 期货
年化 = 基差 / 现货 × 365 / 期货剩余自然日
```

- 正数 = 贴水（现货更高）。负数 = 升水。
- 剩余自然日按期货到期日减期货行情日。不足 7 日不年化。不扣利率，不用 252。
- 到期日规则与商品页相同，是工作日近似，没有剔除法定节假日。
- 近月 = 未到期合约里到期最早的一张。主力 = 持仓 + 成交最高的一张。远月 = 到期晚于主力、得分最高的一张。

## 覆盖

生意社现期表（2026-10-16）能对上 **31/35**。表上没有的四个品种页面写成暂无，不让整次抓取失败：

| 代码 | 品种 | 现货 |
|------|------|------|
| sc | 原油 | 无。表不列能源中心原油，报价还是元/桶 |
| lu | 低硫燃料油 | 无 |
| cs | 玉米淀粉 | 无。目录里有链接，当日对比表没有现货行 |
| AP | 苹果 | 无 |

单位和规格：

- 黄金元/克、白银元/千克，与期货一致。
- 玻璃现货是元/平方米。基差按 5mm 浮法、12.5 kg/㎡（×80）折成元/吨后再减期货，页面标「已折算」。
- 铁矿石现货是湿吨、期货是干吨，没有做水分折算。
- 燃料油现货是 180CST，期货是 RMG 380。焦炭现货是一级冶金焦，期货介于一级和二级之间。
- 这三列都标「规格不一」，数字仍按原单位相减。
- 生意社的「聚乙烯」对应大商所塑料（LLDPE）。

## 来源和限制

- 现货：<https://www.100ppi.com/sf/> ，历史日页 `https://www.100ppi.com/sf/day-YYYY-MM-DD.html`。页头时间常见为北京时间 16:30。`robots.txt` 只禁止 `/monitor/` 和 `/english/`，没有禁止 `/sf/`。服务条款把页面内容列为受版权保护的材料；本页只保留现货数字、在本地用新浪期货重算基差，并链回原表。
- 从中国大陆以外的环境可以打开这张表（本仓库的抓取在 GitHub Actions 上跑）。生意社对短时间重复访问会拒绝，工作流每天只拉一次。
- 期货：`https://hq.sinajs.cn/list=nf_...`，请求带 `Referer: https://finance.sina.com.cn`。用的是最新价。工作日 16:00 的运行通常还在日盘；若行情时间落在夜盘，页面会写明最新价已离开日盘收盘，也不是结算价。
- 工作流定在 `0 8 * * 1-5`（北京时间 16:00）。现货表多在 16:30 才换日，所以现货日可能比期货日早一个交易日。页头分两列写日期。

整张表打不开、或认不出至少 8 个跟踪品种时，脚本退出且不改快照。某一个品种缺行只记为暂无。

## 本地运行

Python 3.11+ 标准库。在仓库根目录：

```bash
python3 spot-basis/scripts/test_ppi.py
python3 spot-basis/scripts/fetch_spot_basis.py
python3 -m http.server 8741
```

打开 http://127.0.0.1:8741/spot-basis/ 。`--only rb,m` 写到 `spot-basis/data/debug.json`，不覆盖站点快照。

## 更新

`.github/workflows/update-spot-basis.yml`

- 定时：`0 8 * * 1-5`（UTC），另有 `workflow_dispatch`
- 与股指、商品工作流共用并发组 `update-basis`
- 提交前 `git pull --rebase`，只提交 `spot-basis/data/latest.json`
- 页面「手动刷新」打开 [Update spot basis](https://github.com/heber/cffex-basis/actions/workflows/update-spot-basis.yml)

## 免责声明

仅供研究参考，不构成投资建议。现货不是交割升贴水，规格和干湿吨对不上的品种不能当成可交易基差。
