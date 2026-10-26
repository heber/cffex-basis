"""Seven broad-based ETF categories tracked by the flow page.

Membership is not a handwritten code list. Eastmoney's ETF/index report
(`RPT_FUND_ETF_INDEX`) tags each listed ETF with the index it tracks.
A fund is in a category when that index code matches. Index-enhanced
ETFs that still track the same code are included: the 2026-10-23
screenshot totals only add up when they are.

Order is large-cap to the growth boards, which is the column order on
the creation/redemption table (中证1000, 创业板, 科创50 sit at the right,
just before 合计).
"""

from __future__ import annotations

CATEGORIES: list[dict] = [
    {
        "id": "sse50",
        "name": "上证50ETF",
        "index_code": "000016",
        "index_name": "上证50",
    },
    {
        "id": "csi300",
        "name": "沪深300ETF",
        "index_code": "000300",
        "index_name": "沪深300",
    },
    {
        "id": "a500",
        "name": "中证A500ETF",
        "index_code": "000510",
        "index_name": "中证A500",
    },
    {
        "id": "csi500",
        "name": "中证500ETF",
        "index_code": "000905",
        "index_name": "中证500",
    },
    {
        "id": "csi1000",
        "name": "中证1000ETF",
        "index_code": "000852",
        "index_name": "中证1000",
    },
    {
        "id": "chinext",
        "name": "创业板ETF",
        "index_code": "399006",
        "index_name": "创业板指",
    },
    {
        "id": "star50",
        "name": "科创50ETF",
        "index_code": "000688",
        "index_name": "科创50",
    },
]

INDEX_TO_ID = {row["index_code"]: row["id"] for row in CATEGORIES}
CATEGORY_IDS = [row["id"] for row in CATEGORIES]


def category_by_index(index_code: str | None) -> dict | None:
    if not index_code:
        return None
    target = INDEX_TO_ID.get(str(index_code).strip())
    if not target:
        return None
    for row in CATEGORIES:
        if row["id"] == target:
            return row
    return None
