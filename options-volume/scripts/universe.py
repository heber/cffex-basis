"""Stable US options watchlist for the daily volume table.

These are the twenty underlyings in the 2026-10-19 Zen Research
「期权成交量趋势」table. The job re-ranks this list by the session's
options volume. It is not a market-wide top-20 screen.

Index roots use Cboe's underscore prefix on the delayed-quote URLs.
"""

from __future__ import annotations

# Article order, kept as the canonical watchlist. Ranking is applied later.
WATCHLIST = [
    {
        "symbol": "SPY",
        "cboe": "SPY",
        "kind": "etf",
        "name": "标普500ETF",
        "name_en": "State Street SPDR S&P 500 ETF Trust",
    },
    {
        "symbol": "QQQ",
        "cboe": "QQQ",
        "kind": "etf",
        "name": "纳指100ETF",
        "name_en": "Invesco QQQ Trust",
    },
    {
        "symbol": "SPX",
        "cboe": "_SPX",
        "kind": "index",
        "name": "标普500指数",
        "name_en": "S&P 500 Index",
    },
    {
        "symbol": "TSLA",
        "cboe": "TSLA",
        "kind": "equity",
        "name": "特斯拉",
        "name_en": "Tesla",
    },
    {
        "symbol": "NVDA",
        "cboe": "NVDA",
        "kind": "equity",
        "name": "英伟达",
        "name_en": "NVIDIA",
    },
    {
        "symbol": "AAPL",
        "cboe": "AAPL",
        "kind": "equity",
        "name": "苹果",
        "name_en": "Apple",
    },
    {
        "symbol": "IWM",
        "cboe": "IWM",
        "kind": "etf",
        "name": "罗素2000ETF",
        "name_en": "iShares Russell 2000 ETF",
    },
    {
        "symbol": "MU",
        "cboe": "MU",
        "kind": "equity",
        "name": "美光科技",
        "name_en": "Micron Technology",
    },
    {
        "symbol": "AMZN",
        "cboe": "AMZN",
        "kind": "equity",
        "name": "亚马逊",
        "name_en": "Amazon.com",
    },
    {
        "symbol": "INTC",
        "cboe": "INTC",
        "kind": "equity",
        "name": "英特尔",
        "name_en": "Intel",
    },
    {
        "symbol": "SPCX",
        "cboe": "SPCX",
        "kind": "equity",
        "name": "SpaceX",
        "name_en": "Space Exploration Technologies",
    },
    {
        "symbol": "CRWV",
        "cboe": "CRWV",
        "kind": "equity",
        "name": "CoreWeave",
        "name_en": "CoreWeave",
    },
    {
        "symbol": "XSP",
        "cboe": "_XSP",
        "kind": "index",
        "name": "迷你标普500",
        "name_en": "Mini-SPX Index",
    },
    {
        "symbol": "ETHA",
        "cboe": "ETHA",
        "kind": "etf",
        "name": "以太坊ETF",
        "name_en": "iShares Ethereum Trust ETF",
    },
    {
        "symbol": "CLF",
        "cboe": "CLF",
        "kind": "equity",
        "name": "克利夫兰-克利夫斯",
        "name_en": "Cleveland-Cliffs",
    },
    {
        "symbol": "AMD",
        "cboe": "AMD",
        "kind": "equity",
        "name": "超威半导体",
        "name_en": "Advanced Micro Devices",
    },
    {
        "symbol": "TQQQ",
        "cboe": "TQQQ",
        "kind": "etf",
        "name": "三倍做多纳指",
        "name_en": "ProShares UltraPro QQQ",
    },
    {
        "symbol": "IREN",
        "cboe": "IREN",
        "kind": "equity",
        "name": "IREN",
        "name_en": "IREN",
    },
    {
        "symbol": "META",
        "cboe": "META",
        "kind": "equity",
        "name": "Meta",
        "name_en": "Meta Platforms",
    },
    {
        "symbol": "SMH",
        "cboe": "SMH",
        "kind": "etf",
        "name": "半导体ETF",
        "name_en": "VanEck Semiconductor ETF",
    },
]


def by_symbol() -> dict[str, dict]:
    return {item["symbol"]: item for item in WATCHLIST}
