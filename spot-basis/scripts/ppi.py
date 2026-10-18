"""Parse the 生意社现期表 and turn spot plus futures into 期现基差.

The commodity page's 「现货」 is the nearest futures month. This module uses
the cash price on https://www.100ppi.com/sf/ and does not read that proxy.

Basis
    raw = spot_for_basis − futures
    ann = raw / spot_for_basis × 365 / days_to_expiry
    Positive means the cash price is above the future (贴水).
    Gap under 7 calendar days is not annualized. Natural days only.

Glass is quoted in yuan per square metre. Basis uses a 5 mm float-glass
conversion (12.5 kg/m² → ×80) so the subtraction is in yuan per tonne.
Iron ore, coke, and fuel oil stay in the publisher's units and are flagged
when the grade or the wet/dry tonne does not match the contract.
"""

from __future__ import annotations

import re
from datetime import date

MIN_GAP_DAYS = 7
GLASS_M2_PER_TON = 80.0

# 生意社品名 → 本仓库品种代码。一个代码可以有多个别名。
NAME_TO_CODE = {
    "铁矿石": "i",
    "焦炭": "j",
    "焦煤": "jm",
    "螺纹钢": "rb",
    "热轧卷板": "hc",
    "热卷": "hc",
    "豆粕": "m",
    "豆油": "y",
    "棕榈油": "p",
    "菜籽油OI": "OI",
    "菜籽油": "OI",
    "菜油": "OI",
    "菜籽粕": "RM",
    "菜粕": "RM",
    "铜": "cu",
    "沪铜": "cu",
    "铝": "al",
    "沪铝": "al",
    "锌": "zn",
    "沪锌": "zn",
    "镍": "ni",
    "沪镍": "ni",
    "黄金": "au",
    "沪金": "au",
    "白银": "ag",
    "沪银": "ag",
    "原油": "sc",
    "燃料油": "fu",
    "低硫燃料油": "lu",
    "PTA": "TA",
    "甲醇": "MA",
    "甲醇MA": "MA",
    "纯碱": "SA",
    "玻璃": "FG",
    "尿素": "UR",
    "乙二醇": "eg",
    "聚乙烯": "l",
    "塑料": "l",
    "聚氯乙烯": "v",
    "PVC": "v",
    "聚丙烯": "pp",
    "玉米": "c",
    "玉米淀粉": "cs",
    "棉花": "CF",
    "白糖": "SR",
    "苹果": "AP",
    "碳酸锂": "lc",
    "工业硅": "si",
}

# Exceptions to 「现货和期货都是元/吨，可以直接减」.
PRODUCT_META = {
    "au": {"spot_unit": "元/克", "basis_unit": "元/克"},
    "ag": {"spot_unit": "元/千克", "basis_unit": "元/千克"},
    "FG": {
        "spot_unit": "元/平方米",
        "basis_unit": "元/吨",
        "factor": GLASS_M2_PER_TON,
        "quality": "converted",
        "conversion": "5mm 浮法玻璃 12.5 kg/㎡，80 ㎡/吨，现货×80 得到元/吨",
        "note": "现货报价是元/平方米。基差先按 5mm、12.5 kg/㎡ 折成元/吨，再减期货。",
    },
    "i": {
        "spot_unit": "元/湿吨",
        "basis_unit": "元/吨",
        "quality": "spec_mismatch",
        "note": "现货是湿吨，期货是干吨。数字直接相减，没有按水分折成干吨。",
    },
    "fu": {
        "quality": "spec_mismatch",
        "note": "现货规格为 180CST，期货为 RMG 380 船用燃料油。单位都是元/吨，规格不同。",
    },
    "j": {
        "quality": "spec_mismatch",
        "note": "现货为一级冶金焦，期货交割品介于一级和二级之间。",
    },
    "l": {
        "note": "生意社品名是聚乙烯，对应大商所塑料（LLDPE）。",
    },
}

ABSENT_HINT = {
    "sc": "生意社现期表没有原油行。能源中心原油是元/桶，表上也没有对应的人民币现货列。",
    "lu": "生意社现期表没有低硫燃料油行。",
    "cs": "生意社当日现期对比表没有玉米淀粉现货行。",
    "AP": "生意社当日现期对比表没有苹果现货行。",
}


def night_clock(clock: str) -> bool:
    """True for a Sina quote time that sits in the night session."""
    digits = "".join(ch for ch in str(clock or "") if ch.isdigit())
    if not digits:
        return False
    t = int(digits)
    return t >= 210000 or 0 < t < 80000


def basis_vs(spot: float | None, fut: float | None, dte: int | None) -> tuple[float | None, float | None]:
    """Return (raw, annualized). Annualized is empty when life is under 7 days."""
    if spot is None or fut is None or dte is None:
        return None, None
    if spot <= 0 or fut <= 0 or dte < 0:
        return None, None
    raw = float(spot) - float(fut)
    if dte < MIN_GAP_DAYS:
        return raw, None
    return raw, raw / float(spot) * 365.0 / float(dte)


def _cell_text(inner: str) -> str:
    text = re.sub(r"<[^>]+>", "", inner)
    text = text.replace("\xa0", " ").replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", text).strip()


def _extract_fdata(html: str) -> str:
    marker = html.find('id="fdata"')
    if marker < 0:
        marker = html.find("id='fdata'")
    if marker < 0:
        raise ValueError("生意社页面没有现期表")
    start = html.rfind("<table", 0, marker)
    if start < 0:
        raise ValueError("现期表缺少 table")
    depth = 0
    for match in re.finditer(r"</?table\b", html[start:], flags=re.I):
        token = match.group(0)
        if token.startswith("</"):
            depth -= 1
            if depth == 0:
                end = start + match.end()
                close = html.find(">", end - 1)
                return html[start:close + 1]
        else:
            depth += 1
    raise ValueError("现期表没有结束")


def _strip_nested_tables(table_html: str) -> str:
    pattern = re.compile(r"<table\b[^>]*>(?:(?!</?table\b).)*</table>", re.I | re.S)
    while True:
        match = pattern.search(table_html)
        if match is None or match.start() == 0:
            return table_html
        table_html = table_html[:match.start()] + table_html[match.end():]


def _parse_float(cell: str) -> float | None:
    text = cell.replace(",", "").strip()
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return None
    return float(text)


def _looks_like_code(cell: str) -> bool:
    """Contract month YYMM. A 4-digit price such as 3098 is not a month."""
    text = cell.strip()
    if not re.fullmatch(r"\d{4}", text):
        return False
    month = int(text[2:])
    return 1 <= month <= 12


def _row_from_cells(cells: list[str], exchange: str) -> dict | None:
    """Read one body row.

    After nested basis tables are removed the columns are:
    name, spot, near code, near price, basis, main code, main price, basis.
    Prices can be 4-digit integers, so they are taken by position, not by
    "the next number that is not four digits".
    """
    if not cells:
        return None
    name = cells[0].strip()
    if not name or name in {"商品", "价格"} or "交易所" in name or name.startswith("©"):
        return None
    if len(cells) < 2:
        return None
    spot = _parse_float(cells[1])
    if spot is None:
        return None
    near_code = cells[2].strip() if len(cells) > 2 and _looks_like_code(cells[2]) else None
    near_price = _parse_float(cells[3]) if len(cells) > 3 else None
    main_code = cells[5].strip() if len(cells) > 5 and _looks_like_code(cells[5]) else None
    main_price = _parse_float(cells[6]) if len(cells) > 6 else None
    return {
        "name": name,
        "exchange": exchange,
        "price": spot,
        "near_code": near_code,
        "near_price": near_price,
        "main_code": main_code,
        "main_price": main_price,
    }


def parse_ppi_html(html: str) -> dict:
    """Parse the daily 现期对比 table. Raises ValueError when the table is absent."""
    if "现货" not in html or "主力合约" not in html:
        raise ValueError("页面不像生意社现期表")
    table = _strip_nested_tables(_extract_fdata(html))
    exchange = ""
    rows: list[dict] = []
    for row_html in re.findall(r"<tr\b[^>]*>(.*?)</tr>", table, flags=re.S | re.I):
        cells = [_cell_text(cell) for cell in re.findall(r"<td\b[^>]*>(.*?)</td>", row_html, flags=re.S | re.I)]
        nonempty = [cell for cell in cells if cell]
        if len(nonempty) == 1 and "交易所" in nonempty[0]:
            exchange = nonempty[0]
            continue
        parsed = _row_from_cells(cells, exchange)
        if parsed is not None:
            rows.append(parsed)
    dated = re.findall(r"(\d{4})年(\d{1,2})月(\d{1,2})日(?:\s*(\d{1,2}:\d{2}))?", html)
    spot_date = None
    published = None
    for year, month, day, clock in dated:
        spot_date = date(int(year), int(month), int(day)).isoformat()
        if clock:
            published = f"{spot_date} {clock}"
            break
    if spot_date is None:
        raise ValueError("现期表没有日期")
    footnote = ""
    note = re.search(r'class="cle txtbz">(.*?)</div>', html, flags=re.S)
    if note:
        footnote = _cell_text(note.group(1).replace("<br />", " ").replace("<br>", " "))
    return {
        "date": spot_date,
        "published": published or spot_date,
        "footnote": footnote,
        "rows": rows,
    }


def map_spots(rows: list[dict]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for row in rows:
        code = NAME_TO_CODE.get(row["name"])
        if not code:
            continue
        if code in found and found[code].get("price"):
            continue
        found[code] = row
    return found


def _round(value: float | None, ndigits: int) -> float | None:
    if value is None:
        return None
    return round(float(value), ndigits)


def spot_terms(code: str, raw_price: float) -> dict:
    meta = PRODUCT_META.get(code, {})
    unit = meta.get("spot_unit", "元/吨")
    factor = meta.get("factor")
    if factor:
        basis_price = round(float(raw_price) * float(factor), 4)
    else:
        basis_price = round(float(raw_price), 4)
    return {
        "spot_unit": unit,
        "basis_price": basis_price,
        "basis_unit": meta.get("basis_unit", unit),
        "quality": meta.get("quality", "aligned"),
        "conversion": meta.get("conversion"),
        "note": meta.get("note"),
    }


def _iso(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def pack_leg(basis_price: float | None, contract: dict | None) -> dict | None:
    if not contract:
        return None
    raw, ann = basis_vs(basis_price, contract.get("price"), contract.get("dte"))
    return {
        "symbol": contract.get("display"),
        "price": _round(contract.get("price"), 4),
        "expiry": _iso(contract.get("expiry")),
        "dte": contract.get("dte"),
        "oi": int(round(contract.get("oi") or 0)),
        "volume": int(round(contract.get("volume") or 0)),
        "basis": _round(raw, 4),
        "ann": _round(ann, 6),
    }


def _roles(symbol: str, near: dict | None, main: dict | None, far: dict | None) -> list[str]:
    roles = []
    if near and near.get("display") == symbol:
        roles.append("near")
    if main and main.get("display") == symbol:
        roles.append("main")
    if far and far.get("display") == symbol:
        roles.append("far")
    return roles


def make_product(product: dict, row: dict | None, leg: dict | None) -> dict:
    code = product["code"]
    available = bool(row and row.get("price") and float(row["price"]) > 0)
    terms = spot_terms(code, float(row["price"])) if available else {
        "spot_unit": PRODUCT_META.get(code, {}).get("spot_unit", "元/吨"),
        "basis_price": None,
        "basis_unit": PRODUCT_META.get(code, {}).get("basis_unit", "元/吨"),
        "quality": "unavailable",
        "conversion": None,
        "note": ABSENT_HINT.get(code, "生意社当日现期表没有这一行。"),
    }
    basis_price = terms["basis_price"]
    near = main = far = None
    curve = []
    futures_date = None
    if leg:
        futures_date = leg.get("futures_date")
        near = pack_leg(basis_price, leg.get("near"))
        main = pack_leg(basis_price, leg.get("main"))
        far = pack_leg(basis_price, leg.get("far"))
        for contract in leg.get("curve") or []:
            packed = pack_leg(basis_price, contract)
            if packed is None:
                continue
            packed["roles"] = _roles(contract.get("display"), leg.get("near"), leg.get("main"), leg.get("far"))
            curve.append(packed)
    return {
        "code": code,
        "name": product["name"],
        "ppi_name": None if not row else row.get("name"),
        "exchange": product["exchange"],
        "exchange_name": product.get("exchange_name"),
        "sector": product["sector"],
        "spot_available": available,
        "spot_price": None if not available else round(float(row["price"]), 4),
        "spot_unit": terms["spot_unit"],
        "basis_price": basis_price,
        "basis_unit": terms["basis_unit"],
        "quality": terms["quality"],
        "conversion": terms["conversion"],
        "note": terms["note"],
        "futures_date": futures_date,
        "near": near,
        "main": main,
        "far": far,
        "curve": curve,
    }


def build_payload(products: list[dict], parsed: dict, legs: dict, *, source_url: str, generated_at: str) -> dict:
    mapped = map_spots(parsed["rows"])
    items = []
    fut_dates = []
    clocks: list[str] = []
    for product in products:
        leg = legs.get(product["code"])
        items.append(make_product(product, mapped.get(product["code"]), leg))
        if leg and leg.get("futures_date"):
            fut_dates.append(leg["futures_date"])
        if leg:
            clocks.extend(leg.get("clocks") or [])
    futures_date = max(fut_dates) if fut_dates else None
    spot_date = parsed["date"]
    night = False
    if clocks:
        night = sum(1 for clock in clocks if night_clock(clock)) * 2 >= len(clocks)
    canonical = f"https://www.100ppi.com/sf/day-{spot_date}.html" if spot_date else source_url
    return {
        "generated_at": generated_at,
        "spot_date": spot_date,
        "spot_published": parsed.get("published"),
        "futures_date": futures_date,
        "dates_aligned": bool(spot_date and futures_date and spot_date == futures_date),
        "futures_night_session": night,
        "source": {
            "spot": "生意社现期表",
            "spot_url": canonical,
            "spot_page": "https://www.100ppi.com/sf/",
            "futures": "新浪财经 hq.sinajs.cn",
            "futures_note": "期货是新浪最新价。工作日 16:00 的快照通常还在日盘；夜盘之后最新价会离开日盘收盘，也不是结算价。",
        },
        "coverage": {
            "tracked": len(items),
            "with_spot": sum(1 for item in items if item["spot_available"]),
        },
        "footnote": parsed.get("footnote") or "",
        "products": items,
    }
