# AH股溢价跟踪

跟踪 A 股与 H 股双重上市的常见公司，把 H 股按港元兑人民币折成人民币后，和 A 股比每股价格。

**在线页面：** https://heber.github.io/cffex-basis/ah-premium/

静态站托管在 GitHub Pages，作为 [heber/cffex-basis](https://github.com/heber/cffex-basis) 的 `ah-premium/` 子目录发布。页面只读本目录里的 `data/latest.json`。GitHub Action 在每个工作日北京时间 16:00 运行 `ah-premium/scripts/fetch_premium.py`。

股指分红矫正贴水：https://heber.github.io/cffex-basis/

商品期货贴水：https://heber.github.io/cffex-basis/commodity/

现金现货对期货的期现基差：https://heber.github.io/cffex-basis/spot-basis/

## 溢价怎么定义

```
H股折合人民币 = H股价格（港元） × 港元兑人民币
AH溢价率     = (A股人民币价格 − H股折合人民币) / H股折合人民币
```

- 正数 = A 股溢价，A 比折算后的 H 更贵。
- 负数 = A 股折价，A 更便宜。
- 按每股比较，1 股 A 对 1 股 H。不按每手股数，也不按市值加权，所以不是恒生 AH 股溢价指数。

汇率默认用新浪 `HKDCNY` 即期最新价（1 港元兑换的人民币）。中国货币网的人民币汇率中间价写在同一份快照里，页面可以改用中间价，在浏览器里重算。A 股约 15:00 收盘，H 股约 16:00 收盘，两边不是同一秒的成交。

## 名单

`scripts/universe.py` 里是手写的常见双重上市名单，覆盖银行、保险、券商、能源、材料、工业、汽车、消费、医药、通信、交运、公用事业、建材和地产。抓取时会核对报价简称，对不上发行人的代码不进表。

不成对的例子：中国建筑与中国建筑国际，中国电建与中广核电力（中广核电力配的是中国广核）。只有 GDR、没有 H 股的公司（例如华友钴业）不收。H 股用港币柜台。

## 本地运行

只需 Python 3.11+ 标准库。在 `ah-premium/` 目录下执行：

```bash
python3 scripts/test_premium.py
python3 scripts/fetch_premium.py
python3 -m http.server 8741
```

打开 http://127.0.0.1:8741/ 。从仓库根目录开静态服务时，页面在 http://127.0.0.1:8741/ah-premium/ 。

## 更新频率

仓库根目录的 `.github/workflows/update-ah-premium.yml`

- 定时：`0 8 * * 1-5`（UTC）= 北京时间周一至周五 16:00
- 也可在 Actions 页手动 `workflow_dispatch`
- 与股指、商品工作流共用并发组 `update-basis`，提交前 `git pull --rebase`，避免三边同时推
- 只提交 `ah-premium/data`

行情与汇率（均带 `Referer: https://finance.sina.com.cn`，汇率中间价除外）：

- A 股、H 股、`HKDCNY`：`https://hq.sinajs.cn/list=`
- 人民币汇率中间价：中国货币网 `ccpr.json`（失败时仍用即期发布）

## 目录

```
cffex-basis/
├── ah-premium/
│   ├── index.html
│   ├── README.md
│   ├── data/latest.json
│   └── scripts/
│       ├── universe.py
│       ├── fetch_premium.py
│       └── test_premium.py
└── .github/workflows/update-ah-premium.yml
```

## 免责声明

仅供研究参考，不构成投资建议。行情来自新浪财经公开接口，中间价来自中国货币网，可能延迟或缺失。
