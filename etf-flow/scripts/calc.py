"""Share-change flows, scale, and close-to-NAV premium. No network.

净申赎金额 = (当日份额 − 前一披露日份额) × 当日单位净值
规模       = 份额 × 单位净值
折溢价率   = (收盘价 − 单位净值) / 单位净值

Share counts are 份. Money inside this module is 元; the snapshot
writer divides by 1e8 when it stores 亿元.
"""

from __future__ import annotations

import calendar
from datetime import datetime

# A split or merge moves NAV and shares in opposite directions while
# shares × NAV stays put. A real creation moves shares × NAV.
# Modest pure splits are the 8% case (AUM unchanged). A NAV jump of 15%
# is treated as a split even when creations land the same day, because a
# broad index almost never moves that far in one session. The adjusted
# previous share count is prev_shares × prev_nav / nav.
SPLIT_NAV_MOVE = 0.08
SPLIT_RESIDUAL = 0.04
SPLIT_BIG_NAV = 0.15
# Official NAV sometimes lands a session late. Borrowing an older NAV
# keeps that day's flow; borrowing across a week would invent one.
NAV_CARRY_DAYS = 5
# Previous share print further back than a long holiday is a hole,
# not a redemption. Chinese New Year is inside this window.
MAX_SHARE_GAP_DAYS = 15
# A session where only one exchange has posted is not a full print.
INCOMPLETE_DAY_RATIO = 0.72
# Below this many priced funds the snapshot is too thin to publish.
MIN_PUBLISH_FUNDS = 120


def parse_day(day: str) -> datetime:
    return datetime.strptime(day, "%Y-%m-%d")


def median(values: list[float]) -> float:
    ordered = sorted(values)
    count = len(ordered)
    if count == 0:
        raise ValueError("median of empty")
    mid = count // 2
    if count % 2:
        return float(ordered[mid])
    return (float(ordered[mid - 1]) + float(ordered[mid])) / 2


def carry_value(series: dict | None, day: str, max_days: int = NAV_CARRY_DAYS):
    """Value on `day`, or the newest earlier value inside `max_days`."""
    if not series:
        return None
    if series.get(day) not in (None, ""):
        return series[day]
    end = parse_day(day)
    best = None
    best_day = None
    for key, value in series.items():
        if value in (None, ""):
            continue
        gap = (end - parse_day(key)).days
        if 0 < gap <= max_days and (best_day is None or key > best_day):
            best = value
            best_day = key
    return best


def flow_yuan(prev_shares, shares, prev_nav, nav) -> float | None:
    """Net creation value in 元. None when the print cannot be used.

    The first listing day has no previous share count, so the caller
    does not call this. A later zero share count is treated as a bad
    print rather than a redemption of the whole fund.
    """
    if prev_shares is None or shares is None or nav is None:
        return None
    try:
        prev_shares = float(prev_shares)
        shares = float(shares)
        nav = float(nav)
        prev_nav_f = None if prev_nav is None else float(prev_nav)
    except (TypeError, ValueError):
        return None
    if prev_shares <= 0 or shares <= 0 or nav <= 0:
        return None
    if prev_nav_f and prev_nav_f > 0:
        nav_ratio = nav / prev_nav_f
        share_ratio = shares / prev_shares
        aum_unchanged = abs(share_ratio * nav_ratio - 1) <= SPLIT_RESIDUAL
        big_nav = abs(nav_ratio - 1) >= SPLIT_BIG_NAV
        modest_split = abs(nav_ratio - 1) >= SPLIT_NAV_MOVE and aum_unchanged
        if big_nav or modest_split:
            adjusted_prev = prev_shares * (prev_nav_f / nav)
            return (shares - adjusted_prev) * nav
    return (shares - prev_shares) * nav


def share_counts(funds: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for fund in funds.values():
        for day, shares in (fund.get("shares") or {}).items():
            if shares and float(shares) > 0:
                counts[day] = counts.get(day, 0) + 1
    return counts


def usable_days(counts: dict[str, int], ratio: float = INCOMPLETE_DAY_RATIO) -> list[str]:
    """Drop sessions whose share print is much thinner than the rest."""
    if not counts:
        return []
    floor = ratio * median(list(counts.values()))
    return [day for day in sorted(counts) if counts[day] >= floor]


def window_start(as_of: str, key: str) -> str | None:
    """Inclusive start date for a display window.

    `5d` returns None: the caller keeps the last five trading rows.
    `1m` / `3m` go back that many calendar months from `as_of`,
    clamping the day (31 March minus one month is 28 February).
    `ytd` is 1 January of the snapshot year.
    """
    year, month, day = (int(part) for part in as_of.split("-"))
    if key == "5d":
        return None
    if key == "ytd":
        return f"{year:04d}-01-01"
    if key not in ("1m", "3m"):
        raise ValueError(f"unknown window {key}")
    month -= 1 if key == "1m" else 3
    while month <= 0:
        month += 12
        year -= 1
    last = calendar.monthrange(year, month)[1]
    day = min(day, last)
    return f"{year:04d}-{month:02d}-{day:02d}"


def _previous_share_day(days: list[str], index: int) -> str | None:
    if index <= 0:
        return None
    return days[index - 1]


def _fund_share_days(fund: dict) -> list[str]:
    return sorted(
        day for day, shares in (fund.get("shares") or {}).items() if shares and float(shares) > 0
    )


def build_series(funds: dict, category_ids: list[str], publish_from: str) -> tuple[list[dict], str | None]:
    """Daily category totals from `publish_from` onward.

    Each row's flow is 元. AUM is 元. Premium is a ratio,
    (close − NAV) / NAV, weighted by that fund's AUM.
    `as_of` is the last usable session on or after `publish_from`.
    """
    allowed = set(usable_days(share_counts(funds)))
    dates = [day for day in sorted(allowed) if day >= publish_from]
    if not dates:
        return [], None

    prepared = []
    for code, fund in funds.items():
        category = fund.get("category")
        if category not in category_ids:
            continue
        days = _fund_share_days(fund)
        if not days:
            continue
        prepared.append((code, fund, days))

    rows = []
    for day in dates:
        flow = {category: 0.0 for category in category_ids}
        aum = {category: 0.0 for category in category_ids}
        premium_weight = {category: 0.0 for category in category_ids}
        premium_sum = {category: 0.0 for category in category_ids}
        total_premium_weight = 0.0
        total_premium_sum = 0.0
        for _code, fund, days in prepared:
            if day not in fund["shares"] or not fund["shares"][day] or float(fund["shares"][day]) <= 0:
                continue
            category = fund["category"]
            shares = float(fund["shares"][day])
            nav = carry_value(fund.get("nav") or {}, day)
            nav_f = float(nav) if nav not in (None, "") else None
            if nav_f and nav_f > 0:
                aum[category] += shares * nav_f
            index = days.index(day) if day in days else -1
            prev = _previous_share_day(days, index) if index >= 0 else None
            if prev is not None and nav_f and nav_f > 0:
                gap = (parse_day(day) - parse_day(prev)).days
                if gap <= MAX_SHARE_GAP_DAYS:
                    prev_nav = carry_value(fund.get("nav") or {}, prev)
                    value = flow_yuan(fund["shares"][prev], shares, prev_nav, nav_f)
                    if value is not None:
                        flow[category] += value
            # Close is not carried forward: a stale print against today's
            # NAV would look like a premium that nobody could have traded.
            close = (fund.get("close") or {}).get(day)
            if nav_f and nav_f > 0 and close not in (None, "") and float(close) > 0:
                weight = shares * nav_f
                premium = (float(close) - nav_f) / nav_f
                premium_weight[category] += weight
                premium_sum[category] += premium * weight
                total_premium_weight += weight
                total_premium_sum += premium * weight
        premium = {}
        for category in category_ids:
            if premium_weight[category] > 0:
                premium[category] = premium_sum[category] / premium_weight[category]
            else:
                premium[category] = None
        rows.append(
            {
                "date": day,
                "flow": flow,
                "total": sum(flow.values()),
                "aum": aum,
                "aum_total": sum(aum.values()),
                "premium": premium,
                "premium_total": (
                    total_premium_sum / total_premium_weight if total_premium_weight > 0 else None
                ),
            }
        )
    return rows, (rows[-1]["date"] if rows else None)


def latest_funds(funds: dict, category_ids: list[str], as_of: str) -> list[dict]:
    """One row per fund that has a share print on `as_of`."""
    out = []
    allowed = set(category_ids)
    for code, fund in funds.items():
        if fund.get("category") not in allowed:
            continue
        shares = (fund.get("shares") or {}).get(as_of)
        if not shares or float(shares) <= 0:
            continue
        days = _fund_share_days(fund)
        nav = carry_value(fund.get("nav") or {}, as_of)
        close = (fund.get("close") or {}).get(as_of)
        nav_f = float(nav) if nav not in (None, "") else None
        close_f = float(close) if close not in (None, "") else None
        flow = None
        if as_of in days:
            index = days.index(as_of)
            prev = _previous_share_day(days, index)
            if prev is not None and nav_f and nav_f > 0:
                gap = (parse_day(as_of) - parse_day(prev)).days
                if gap <= MAX_SHARE_GAP_DAYS:
                    flow = flow_yuan(
                        fund["shares"][prev],
                        shares,
                        carry_value(fund.get("nav") or {}, prev),
                        nav_f,
                    )
        aum = shares * nav_f if nav_f and nav_f > 0 else None
        premium = None
        if nav_f and nav_f > 0 and close_f and close_f > 0:
            premium = (close_f - nav_f) / nav_f
        out.append(
            {
                "code": code,
                "name": fund.get("name") or code,
                "category": fund["category"],
                "shares": float(shares),
                "nav": nav_f,
                "close": close_f,
                "flow": flow,
                "aum": aum,
                "premium": premium,
            }
        )
    return out


def publishable(fund_count: int) -> bool:
    return fund_count >= MIN_PUBLISH_FUNDS
