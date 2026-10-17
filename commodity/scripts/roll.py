"""Continuous main (主力), near-month spot proxy (现货代理), and basis math.

There is one continuous price series per product. Each session it is the
single contract with the highest liquidity score. When that leader changes,
the series splices onto the new contract. This is not a second contract
sitting beside an exchange 「主力」, and it is not renamed to 现货.

Score
    score = open interest + volume (lots). Ties: higher open interest, then
    higher volume, then the nearer expiry.

Spot proxy (现货代理)
    Among eligible contracts that have not expired, the earliest expiry.
    Ties: higher score, then higher open interest, then higher volume.
    This is a near-month futures proxy, not a cash or port price.

Far leg (次活跃远月)
    Among contracts with a later expiry month than the main, the one with
    the highest score. Often not the adjacent month (soybean meal 1 vs 5).
    The basis far leg is this contract even when the spot proxy expires
    after the main. If the far expiry is not later than the spot proxy,
    basis is left empty.

Basis
    raw = P_spot − P_far
    ann = raw / P_spot × 365 / (far_expiry − spot_expiry)
    Positive means the further contract is cheaper (backwardation, 贴水).
    Gap under 7 calendar days is not annualized. Natural days only; no
    risk-free rate.

Percentiles
    Trailing 3 calendar years, inclusive of the start date, excluding the
    as-of session. Bare integer percent. Higher basis percentile = deeper
    discount of the spot-proxy series. Higher price percentile = closer to
    the window high of the ratio-spliced main (the latest point equals the
    live main price). Fewer than 30 samples → no percentile.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

MIN_SAMPLES = 30
WINDOW_YEARS = 3
MIN_GAP_DAYS = 7
LIQUID_COUNT = 6
LIQUID_CAP = 8
LIQUID_FLOOR = 0.20


def nth_weekday(year: int, month: int, n: int) -> date:
    """Nth Mon–Fri of the month. Holidays are not removed."""
    if n < 1:
        raise ValueError(n)
    d = date(year, month, 1)
    seen = 0
    while True:
        if d.weekday() < 5:
            seen += 1
            if seen == n:
                return d
        d += timedelta(days=1)
        if d.month != month:
            raise ValueError(f"month {year}-{month} has no weekday #{n}")


def day15_forward(year: int, month: int) -> date:
    """Contract-month 15th; Saturday/Sunday roll forward to Monday."""
    d = date(year, month, 15)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def prev_month_last_weekday(year: int, month: int) -> date:
    d = date(year, month, 1) - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def expiry_for(year: int, month: int, rule: str) -> date:
    if rule == "d15":
        return day15_forward(year, month)
    if rule == "tenth":
        return nth_weekday(year, month, 10)
    if rule == "prev_eom":
        return prev_month_last_weekday(year, month)
    raise ValueError(rule)


def window_start(asof: date, years: int = WINDOW_YEARS) -> date:
    try:
        return asof.replace(year=asof.year - years)
    except ValueError:
        # Feb 29 → Feb 28 of the earlier year.
        return asof.replace(year=asof.year - years, day=28)


def session_trade_date(d: date, hhmmss: str) -> date:
    """Map a Sina quote timestamp onto an exchange session date.

    A Friday night session can be stamped Saturday 00:00–02:30; that still
    belongs to Friday. Sunday evening (21:00 onward) is Monday's session.
    """
    digits = "".join(ch for ch in str(hhmmss) if ch.isdigit())
    t = int(digits) if digits else 0
    if d.weekday() == 5 and t < 80000:
        return d - timedelta(days=1)
    if d.weekday() == 6 and t >= 200000:
        return d + timedelta(days=1)
    if d.weekday() == 6:
        return d - timedelta(days=2)
    if d.weekday() == 5:
        return d - timedelta(days=1)
    return d


def parse_yymm(symbol: str) -> tuple[str, int, int] | None:
    """Split RB2701 / OI2701 / m2701 into (root, year, month)."""
    raw = symbol.strip()
    i = 0
    while i < len(raw) and raw[i].isalpha():
        i += 1
    root, digits = raw[:i], raw[i:]
    if not root or len(digits) != 4 or not digits.isdigit():
        return None
    month = int(digits[2:])
    if not 1 <= month <= 12:
        return None
    year = 2000 + int(digits[:2])
    return root.upper(), year, month


def sina_symbol(code: str, year: int, month: int) -> str:
    return f"{code.upper()}{year % 100:02d}{month:02d}"


def display_symbol(code: str, year: int, month: int, exchange: str) -> str:
    raw = sina_symbol(code, year, month)
    if exchange == "CZCE":
        return raw
    return raw.lower()


def month_iter(start: tuple[int, int], end: tuple[int, int]):
    y, m = start
    ey, em = end
    while (y, m) <= (ey, em):
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    idx = year * 12 + (month - 1) + delta
    return idx // 12, idx % 12 + 1


def eligible(contract: dict) -> bool:
    price = contract.get("price") or 0
    oi = contract.get("oi") or 0
    vol = contract.get("volume") or 0
    return price > 0 and (oi + vol) > 0


def rank_key(contract: dict) -> tuple:
    oi = float(contract.get("oi") or 0)
    vol = float(contract.get("volume") or 0)
    ordinal = int(contract["year"]) * 12 + int(contract["month"])
    # max() wins. Nearer expiry wins a perfect tie via -ordinal.
    return (oi + vol, oi, vol, -ordinal)


def pick_virtual_main(contracts: list[dict]) -> dict | None:
    pool = [c for c in contracts if eligible(c)]
    if not pool:
        return None
    return max(pool, key=rank_key)


def pick_far_leg(contracts: list[dict], main: dict) -> dict | None:
    main_ym = (int(main["year"]), int(main["month"]))
    pool = [
        c
        for c in contracts
        if eligible(c) and (int(c["year"]), int(c["month"])) > main_ym
    ]
    if not pool:
        return None
    return max(pool, key=rank_key)


def _spot_key(contract: dict) -> tuple:
    oi = float(contract.get("oi") or 0)
    vol = float(contract.get("volume") or 0)
    # min() wins. Earlier expiry first; a tie goes to the higher score.
    return (contract["expiry"], -(oi + vol), -oi, -vol)


def pick_spot_proxy(contracts: list[dict], trade_date: date) -> dict | None:
    """Nearest unexpired eligible contract (近月代理现货).

    Unexpired means expiry on or after the session date. Cash and port
    quotes are not consulted. Ties break by score, then open interest,
    then volume.
    """
    pool = [
        c
        for c in contracts
        if eligible(c) and c.get("expiry") is not None and c["expiry"] >= trade_date
    ]
    if not pool:
        return None
    return min(pool, key=_spot_key)


def annualized_basis(p_near: float | None, p_far: float | None, gap_days: int | None,
                     min_gap: int = MIN_GAP_DAYS) -> float | None:
    if p_near is None or p_far is None or gap_days is None:
        return None
    if p_near <= 0 or gap_days < min_gap:
        return None
    return (p_near - p_far) / p_near * 365.0 / gap_days


def spread_vs(earlier: dict, later: dict) -> tuple[float | None, float | None, int | None]:
    """Return (raw, ann, gap_days) in expiry order. Positive raw = backwardation."""
    gap = (later["expiry"] - earlier["expiry"]).days
    if gap <= 0:
        return None, None, gap
    raw = float(earlier["price"]) - float(later["price"])
    ann = annualized_basis(float(earlier["price"]), float(later["price"]), gap)
    return raw, ann, gap


def select_liquid_terms(contracts: list[dict], main: dict, far: dict | None,
                        k: int = LIQUID_COUNT, cap: int = LIQUID_CAP,
                        floor: float = LIQUID_FLOOR, spot: dict | None = None) -> list[dict]:
    """Liquid curve, expiry order. Always includes main, far leg, and spot proxy."""
    pool = [c for c in contracts if eligible(c)]
    ranked = sorted(pool, key=rank_key, reverse=True)
    chosen: list[dict] = []
    seen: set[tuple[int, int]] = set()

    def add(c: dict) -> None:
        key = (int(c["year"]), int(c["month"]))
        if key in seen:
            return
        seen.add(key)
        chosen.append(c)

    add(main)
    if far is not None:
        add(far)
    if spot is not None:
        add(spot)
    leader_score = rank_key(main)[0]
    for c in ranked:
        if len(chosen) >= cap:
            break
        score = rank_key(c)[0]
        if len(chosen) >= k and score < floor * leader_score:
            break
        add(c)
    chosen.sort(key=lambda c: (int(c["year"]), int(c["month"])))
    return chosen


def build_day(contracts: list[dict], trade_date: date) -> dict | None:
    main = pick_virtual_main(contracts)
    if main is None:
        return None
    far = pick_far_leg(contracts, main)
    spot = pick_spot_proxy(contracts, trade_date)
    raw = ann = gap = None
    far_px = None
    far_name = None
    if far is not None:
        far_px = float(far["price"])
        far_name = far["display"]
        # Far leg stays the contract later than the main. Basis is empty
        # when that leg does not expire after the spot proxy.
        if spot is not None:
            raw, ann, gap = spread_vs(spot, far)
    return {
        "date": trade_date,
        "contract": main["display"],
        "px": float(main["price"]),
        "far": far_name,
        "far_px": far_px,
        "spot": None if spot is None else spot["display"],
        "spot_px": None if spot is None else float(spot["price"]),
        "ann": ann,
        "raw": raw,
        "gap": gap,
        "dte": (main["expiry"] - trade_date).days,
        "oi": float(main.get("oi") or 0),
        "volume": float(main.get("volume") or 0),
        "out": None,
    }


def attach_out(rows: list[dict], price_on) -> list[dict]:
    """On a leadership change, store the outgoing contract's same-day price."""
    out_rows = []
    prev = None
    for row in rows:
        item = dict(row)
        item["out"] = None
        if prev is not None and prev["contract"] != row["contract"]:
            item["out"] = price_on(row["date"], prev["contract"])
        out_rows.append(item)
        prev = row
    return out_rows


def assemble_history(panel: dict[date, list[dict]], min_date: date) -> list[dict]:
    """panel maps a session date to every contract that printed that day."""
    price_index: dict[tuple[date, str], float] = {}
    for d, contracts in panel.items():
        for c in contracts:
            px = c.get("price") or 0
            if px > 0:
                price_index[(d, c["display"])] = float(px)
    rows = []
    for d in sorted(panel):
        if d < min_date:
            continue
        rec = build_day(panel[d], d)
        if rec is not None:
            rows.append(rec)

    def price_on(d: date, display: str):
        return price_index.get((d, display))

    return attach_out(rows, price_on)


def ratio_back_adjust(rows: list[dict]) -> list[float]:
    """Multiplicative splice. The last price is unadjusted (the tradable price).

    Same contract: scale by that contract's own close ratio.
    Roll day: scale the previous day by old_same_day / new, using ``out``
    (outgoing contract's price on the roll day). If ``out`` is missing, the
    level is held flat across that step so the calendar spread is not treated
    as a return.
    """
    n = len(rows)
    if n == 0:
        return []
    adj = [0.0] * n
    adj[-1] = float(rows[-1]["px"])
    for i in range(n - 2, -1, -1):
        nxt = rows[i + 1]
        if rows[i]["contract"] == nxt["contract"]:
            denom = float(nxt["px"])
        else:
            denom = float(nxt["out"]) if nxt.get("out") else 0.0
        px = float(rows[i]["px"])
        if denom > 0 and px > 0:
            adj[i] = adj[i + 1] * px / denom
        else:
            adj[i] = adj[i + 1]
    return adj


def historical_percentile(points: list[tuple[date, float | None]], asof: date,
                          current: float | None, min_n: int = MIN_SAMPLES,
                          years: int = WINDOW_YEARS) -> tuple[int | None, int]:
    """Share of window samples <= current, as an integer percent.

    Window is [asof - years, asof). ``points`` may contain None values; those
    are dropped. Returns (percent or None, sample size).
    """
    start = window_start(asof, years)
    sample = [v for d, v in points if v is not None and start <= d < asof]
    n = len(sample)
    if current is None or n < min_n:
        return None, n
    k = sum(1 for v in sample if v <= current)
    pct = int((100 * k + n // 2) // n)
    return pct, n


def score_snapshot(history: list[dict], live: dict | None, asof: date):
    """Basis percentile of daily ann, and price percentile of the spliced main.

    The live row is appended (replacing a same-day history row) before the
    ratio splice, so past prices are expressed in today's contract units and
    today's percentile uses today's tradable price. The as-of session itself
    is not part of the sample.
    """
    rows = [r for r in history if r["date"] != asof]
    if live is not None:
        rows.append(live)
    rows.sort(key=lambda r: r["date"])
    adj = ratio_back_adjust(rows) if rows else []
    price_pts = [(r["date"], a) for r, a in zip(rows, adj)]
    basis_pts = [(r["date"], r.get("ann")) for r in rows]
    cur_px = float(live["px"]) if live is not None else None
    cur_ann = live.get("ann") if live is not None else None
    b_pct, b_n = historical_percentile(basis_pts, asof, cur_ann)
    p_pct, p_n = historical_percentile(price_pts, asof, cur_px)
    return {
        "basis_pct": b_pct,
        "basis_n": b_n,
        "price_pct": p_pct,
        "price_n": p_n,
    }


def downsample(values: list[float | None], limit: int = 90) -> list[float | None]:
    if len(values) <= limit:
        return list(values)
    step = max(1, len(values) // limit)
    picked = values[::step]
    if values[-1] != picked[-1]:
        picked.append(values[-1])
    return picked


def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]
