# 中国股指期货贴水跟踪（分红矫正）

跟踪中金所股指期货 **IH（上证50）、IF（沪深300）、IC（中证500）、IM（中证1000）** 四个活跃合约（当月、次月、当季、下季）的贴水/升水，并对期内分红做矫正后再年化。

**在线页面：** https://zaviichen.github.io/cffex-basis/

静态站托管在 GitHub Pages。行情不由 Pages 上的常驻服务拉取，而是由 GitHub Action 运行 `scripts/fetch_basis.py`，把结果写入 `data/latest.json`。

## 为什么要矫正分红？

价格指数在成分股除息时自然回落，期货定价已隐含期内分红。直接用 `(现货 − 期货) / 现货` 年化会把「分红」误当成「贴水」。

```
原始基差   = 现货 − 期货                 （正 = 贴水，负 = 升水）
矫正基差   = 原始基差 − 期内分红点数
年化贴水率 = 矫正基差 / 现货 × 天数因子 / 剩余自然日
```

年化只用自然日 **365**：矫正基差 / 现货 × 365 / 剩余自然日。不扣无风险利率。页面用红色标出「未扣分红的原始年化」，避免把分红季的近月贴水看成纯基差。

## 期内分红

```
期内分红点数 = 现货 × 年化股息率 × 剩余期限内季节性权重之和
```

季节性按 A 股分红节奏：5–7 月为高峰，其余月份权重大幅更低，权重归一化后按日比例摊进 `(今天, 到期日]`。

默认股息率（估算，可在页面上改，浏览器内重算，不改快照里的价格）：

| 品种 | 指数 | 默认股息率 | 乘数 |
|------|------|------------|------|
| IH | 上证50 `sh000016` | 2.3% | 300 |
| IF | 沪深300 `sh000300` | 2.1% | 300 |
| IC | 中证500 `sh000905` | 1.05% | 200 |
| IM | 中证1000 `sh000852` | 0.92% | 200 |

这不是券商成分股逐笔预测。

## 合约与行情

- **期货代码：** 新浪 `CFF_RE_{CODE}`（例如 `CFF_RE_IF2610`），请求带 `Referer: https://finance.sina.com.cn`
- **到期日：** 合约月份第三个周五
- **活跃合约：** 到期日 ≥ 今天的月份里，取当月、次月，再从其后取最近两个季月（3/6/9/12）作为当季、下季
- **合约市值** = 最新价 × 乘数
- **持仓名义市值** = 持仓量 × 最新价 × 乘数
- 周末和节假日沿用最近交易日收盘价

## 更新频率

`.github/workflows/update-basis.yml`：

- 定时：`1,31 1-7 * * 1-5`（UTC）≈ 北京时间周一至周五 09:01–15:31，每 30 分钟
- 也可在 Actions 页手动 `workflow_dispatch`
- 脚本每次写入新的时间戳；`data/latest.json` 有变化才提交

本地手动刷新快照（仅标准库）：

```bash
python3 scripts/fetch_basis.py
```

## 目录

```
cffex-basis/
├── index.html                         # 中文 Dashboard，读取 data/latest.json
├── data/latest.json                   # 最近一次快照
├── scripts/fetch_basis.py             # 拉新浪行情并计算分红矫正贴水
├── .github/workflows/update-basis.yml # 定时提交快照
├── .nojekyll
└── README.md
```

## GitHub Pages

仓库公开，Pages 从 `main` 分支根目录 `/` 发布。站点地址为 https://zaviichen.github.io/cffex-basis/ 。

若 Pages 尚未打开：仓库 **Settings → Pages → Build and deployment → Deploy from a branch**，Branch 选 `main`、目录选 `/ (root)`，保存。

## 免责声明

仅供研究参考，不构成投资建议。股息为估算；行情来自新浪财经公开接口，可能延迟或缺失。
