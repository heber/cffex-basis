"""Contract multiplier and the exchange's ordinary-month minimum margin.

``margin_rate`` is the speculative rate the exchange is actually charging on a
non-delivery month (the lowest such rate when near months are temporarily
higher). It is not the lower percentage often printed in the original contract
text, and it is not the futures-company rate. Sources and the as-of date are
in the README.

Notional and minimum margin are in CNY. Crude oil (sc) is quoted in yuan per
barrel on INE, not US dollars.
"""

from __future__ import annotations

# Schedule dated 20261012, published 2026-10-09, for the 2026-10-12 session.
MARGIN_AS_OF = "2026-10-12"
MARGIN_PUBLISHED = "2026-10-09"

# code, multiplier, unit, quote unit, ordinary-month exchange speculative rate
_ROWS = [
    ("i", 100, "吨", "元/吨", 0.08),
    ("j", 100, "吨", "元/吨", 0.12),
    ("jm", 60, "吨", "元/吨", 0.12),
    ("rb", 10, "吨", "元/吨", 0.07),
    ("hc", 10, "吨", "元/吨", 0.07),
    ("m", 10, "吨", "元/吨", 0.07),
    ("y", 10, "吨", "元/吨", 0.07),
    ("p", 10, "吨", "元/吨", 0.08),
    ("OI", 10, "吨", "元/吨", 0.07),
    ("RM", 10, "吨", "元/吨", 0.07),
    ("cu", 5, "吨", "元/吨", 0.09),
    ("al", 5, "吨", "元/吨", 0.09),
    ("zn", 5, "吨", "元/吨", 0.09),
    ("ni", 1, "吨", "元/吨", 0.10),
    ("au", 1000, "克", "元/克", 0.16),
    ("ag", 15, "千克", "元/千克", 0.16),
    ("sc", 1000, "桶", "元/桶", 0.16),
    ("fu", 10, "吨", "元/吨", 0.16),
    ("lu", 10, "吨", "元/吨", 0.16),
    ("TA", 5, "吨", "元/吨", 0.07),
    ("MA", 10, "吨", "元/吨", 0.07),
    ("SA", 20, "吨", "元/吨", 0.08),
    ("FG", 20, "吨", "元/吨", 0.09),
    ("UR", 20, "吨", "元/吨", 0.08),
    ("eg", 10, "吨", "元/吨", 0.07),
    ("l", 5, "吨", "元/吨", 0.07),
    ("v", 5, "吨", "元/吨", 0.07),
    ("pp", 5, "吨", "元/吨", 0.07),
    ("c", 10, "吨", "元/吨", 0.07),
    ("cs", 10, "吨", "元/吨", 0.06),
    ("CF", 5, "吨", "元/吨", 0.07),
    ("SR", 10, "吨", "元/吨", 0.06),
    ("AP", 10, "吨", "元/吨", 0.09),
    ("lc", 1, "吨", "元/吨", 0.15),
    ("si", 5, "吨", "元/吨", 0.10),
]

QUOTE_NOTE = {
    "sc": "原油在能源中心以人民币元/桶报价，不是美元。",
}

BY_CODE = {
    code: {
        "multiplier": multiplier,
        "unit": unit,
        "quote_unit": quote_unit,
        "margin_rate": margin_rate,
    }
    for code, multiplier, unit, quote_unit, margin_rate in _ROWS
}


def get(code: str) -> dict:
    try:
        return BY_CODE[code]
    except KeyError as exc:
        raise KeyError(f"no contract spec for {code}") from exc


def label(spec: dict) -> str:
    multiplier = spec["multiplier"]
    text = str(int(multiplier)) if float(multiplier).is_integer() else str(multiplier)
    return f"{text}{spec['unit']}/手"


def notional(price, spec: dict):
    if price is None:
        return None
    return round(float(price) * float(spec["multiplier"]), 2)


def margin_amount(price, spec: dict):
    value = notional(price, spec)
    if value is None:
        return None
    return round(value * float(spec["margin_rate"]), 2)


def _stamp_leg(leg, spec: dict) -> None:
    if not isinstance(leg, dict) or leg.get("price") is None:
        return
    leg["notional"] = notional(leg["price"], spec)
    leg["margin_min"] = margin_amount(leg["price"], spec)


def apply(snap: dict) -> dict:
    """Attach multiplier, rate, main-leg notional and minimum margin."""
    spec = get(snap["code"])
    snap["multiplier"] = spec["multiplier"]
    snap["multiplier_unit"] = spec["unit"]
    snap["multiplier_text"] = label(spec)
    snap["quote_unit"] = spec["quote_unit"]
    snap["currency"] = "CNY"
    note = QUOTE_NOTE.get(snap["code"])
    if note:
        snap["quote_note"] = note
    snap["margin_rate"] = spec["margin_rate"]
    snap["margin_as_of"] = MARGIN_AS_OF
    snap["margin_scope"] = "ordinary_month"
    virtual = snap.get("virtual")
    price = virtual.get("price") if isinstance(virtual, dict) else None
    snap["notional"] = notional(price, spec)
    snap["margin_min"] = margin_amount(price, spec)
    for key in ("virtual", "spot_proxy", "far"):
        _stamp_leg(snap.get(key), spec)
    for term in snap.get("terms") or []:
        _stamp_leg(term, spec)
    return snap


def methodology_fields() -> dict:
    return {
        "multiplier": "合约乘数是交易所交易单位，例如铁矿石 100 吨/手、沪金 1000 克/手、原油 1000 桶/手。",
        "notional": "面值 = 最新价 × 乘数，单位人民币。总览的面值用主力最新价。原油 sc 的报价是元/桶，不是美元。",
        "margin_min": (
            "最低保证金 = 面值 × 交易所普通月份最低投机保证金比例。"
            f"比例是静态表，截至 {MARGIN_AS_OF}（{MARGIN_PUBLISHED} 公布的下一交易日交易所保证金，取非交割月里的最低一档）。"
            "这是交易所下限，期货公司通常再加收。交割月和部分近月比例更高，本页不按合约月份切换。"
        ),
        "margin_as_of": MARGIN_AS_OF,
        "currency": "面值与保证金均为人民币。原油 sc 报价单位是元/桶；低硫燃料油 lu 仍是元/吨。",
    }
