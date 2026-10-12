"""Historical corr_ann_365 store and same-tenor percentiles.

Window shown on the page: trailing 3 calendar years, excluding the quote date.
The file keeps every Sina daily close we can fetch (since about 2018-10 for
IH/IF/IC, and since IM listed in 2022-07). The weekday Action only appends.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.request
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import fetch_basis as fb

LABELS = ["当月", "次月", "当季", "下季"]
MIN_SAMPLE = 30
WINDOW_LABEL = "近3年"
HISTORY_START = {
    "IH": date(2018, 10, 1),
    "IF": date(2018, 10, 1),
    "IC": date(2018, 10, 1),
    "IM": date(2022, 7, 22),
}
CONTRACT_LIST_START = {
    "IH": date(2018, 10, 1),
    "IF": date(2018, 10, 1),
    "IC": date(2018, 10, 1),
    "IM": date(2022, 7, 1),
}
MIN_FRONT_ROWS = 400
CACHE_DIR = Path("/tmp/cffex-kline-cache")
SERIES_PATH = fb.ROOT / "data" / "history" / "series.json"

SOURCE = (
    "Sina InnerFuturesNewService.getDailyKLine futures close + "
    "CN_MarketData.getKLineData index close"
)
ROW_FIELDS = ["date_yyyymmdd", "spot", "fut_close", "days_to_expiry", "corr_ann_365"]
PERCENTILE_RULE = (
    "贴水分位：同一品种、同一期限（当月/次月/当季/下季）样本中，分红矫正年化贴水 "
    "corr_ann_365 小于等于当前值的交易日占比，范围 0–100%。"
    "样本为行情日往前 3 个日历年，不含行情日当天。"
)
HIGHER_MEANS = "贴水分位越高，当前贴水越深（相对该期限自身历史更便宜）"
SPOT_PERCENTILE_RULE = (
    "指数点位分位 = 同一品种、行情日往前 3 个日历年（不含行情日当天）的指数收盘里，"
    "收盘小于等于当日现货的交易日占比，范围 0–100%。"
    "样本取自库存中各期限按交易日去重后的指数收盘，与贴水分位同一日历窗口。"
    "某一期限期货缺收盘时，该日仍可计入指数样本，因此 N 可以和贴水样本数不同。"
)
SPOT_HIGHER_MEANS = "分位越高，指数越接近这段窗口的历史高位（不是贴水分位）"
ANNUALIZATION = "仅 corr_ann_365 = 矫正基差 / 现货 × 365 / 剩余自然日；不用 252，不扣无风险利率"
DIVIDEND_NOTE = (
    "历史序列使用与快照相同的默认股息率和季节性模型，不是逐笔真实分红。"
    "页面改股息率后，浏览器用同一库存重算贴水分位。指数点位分位不随股息率变化。"
)

_THROTTLE = threading.Lock()
_NEXT_AT = 0.0


def add_years(d: date, years: int) -> date:
    """Calendar shift. Feb 29 lands on Feb 28 when the target year is not a leap year."""
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, day=28)


def ymd_int(d: date) -> int:
    return d.year * 10000 + d.month * 100 + d.day


def date_from_ymd(n: int) -> date:
    y, md = divmod(int(n), 10000)
    m, day = divmod(md, 100)
    return date(y, m, day)


def corrected_ann_365(
    spot: float,
    fut_last: float,
    annual_yield: float,
    today: date,
    expiry: date,
) -> Optional[float]:
    """corr_ann_365 = (spot - fut - dividend points) / spot * 365 / calendar days."""
    days = (expiry - today).days
    if days <= 0 or spot <= 0 or fut_last <= 0:
        return None
    rem = fb.remaining_div_points(spot, annual_yield, today, expiry)
    corrected = (spot - fut_last) - rem
    return round((corrected / spot) * (365.0 / days), 6)


def percentile_leq(sample: List[float], current: float) -> Optional[float]:
    """Percent of observations <= current. None when the sample is empty."""
    if current is None or not sample:
        return None
    le = 0
    for value in sample:
        if value <= current:
            le += 1
    return 100.0 * le / len(sample)


def round1(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return float(Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def select_spot_sample(
    labels: Dict[str, List[List[Any]]], quote: date
) -> Tuple[List[float], Optional[date], Optional[date]]:
    """Unique index closes over the same trailing 3 calendar years, excluding quote.

    Each tenor row already stores that session's index close. The same date can
    appear on several tenors; it counts once. A day missing from one tenor still
    counts when another tenor stored the close.
    """
    start_i = ymd_int(add_years(quote, -3))
    quote_i = ymd_int(quote)
    by_day: Dict[int, float] = {}
    for rows in labels.values():
        for row in rows:
            ymd = int(row[0])
            if ymd < start_i or ymd >= quote_i:
                continue
            try:
                spot = float(row[1])
            except (TypeError, ValueError):
                continue
            if spot <= 0:
                continue
            by_day.setdefault(ymd, spot)
    if not by_day:
        return [], None, None
    days = sorted(by_day)
    return [by_day[day] for day in days], date_from_ymd(days[0]), date_from_ymd(days[-1])


def select_sample(
    rows: List[List[Any]], quote: date
) -> Tuple[List[float], Optional[date], Optional[date]]:
    """Trailing 3 calendar years of corr_ann_365, excluding quote itself."""
    start = add_years(quote, -3)
    start_i = ymd_int(start)
    quote_i = ymd_int(quote)
    values: List[float] = []
    first: Optional[date] = None
    last: Optional[date] = None
    for ymd, _spot, _fut, _days, corr in rows:
        if corr is None or ymd < start_i or ymd >= quote_i:
            continue
        values.append(float(corr))
        d = date_from_ymd(ymd)
        if first is None:
            first = d
        last = d
    return values, first, last


def default_yields() -> Dict[str, float]:
    return {code: cfg["default_yield"] for code, cfg in fb.PRODUCTS.items()}


def yields_match(stored: Dict[str, Any]) -> bool:
    current = default_yields()
    if set(stored.keys()) != set(current.keys()):
        return False
    for code, yld in current.items():
        try:
            if abs(float(stored[code]) - yld) > 1e-12:
                return False
        except (TypeError, ValueError):
            return False
    return True


def _row_with_yield(row: List[Any], annual_yield: float) -> Optional[List[Any]]:
    ymd, spot, fut, days, _corr = row
    today = date_from_ymd(int(ymd))
    expiry = today + timedelta(days=int(days))
    corr = corrected_ann_365(float(spot), float(fut), annual_yield, today, expiry)
    if corr is None:
        return None
    return [int(ymd), float(spot), float(fut), (expiry - today).days, corr]


def ensure_yields(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Recompute stored corr if the default dividend yields changed."""
    current = default_yields()
    if yields_match(doc.get("yields") or {}):
        doc["yields"] = current
        return doc
    for code, labels in doc["series"].items():
        yld = current[code]
        for label, rows in labels.items():
            rebuilt = []
            for row in rows:
                new_row = _row_with_yield(row, yld)
                if new_row is not None:
                    rebuilt.append(new_row)
            labels[label] = rebuilt
    doc["yields"] = current
    return doc


def refresh_archive(doc: Dict[str, Any]) -> None:
    products: Dict[str, Any] = {}
    for code, labels in doc["series"].items():
        counts = {label: len(rows) for label, rows in labels.items()}
        dates = [rows[0][0] for rows in labels.values() if rows]
        dates += [rows[-1][0] for rows in labels.values() if rows]
        if dates:
            products[code] = {
                "start": date_from_ymd(min(dates)).isoformat(),
                "end": date_from_ymd(max(dates)).isoformat(),
                "counts": counts,
            }
        else:
            products[code] = {"start": None, "end": None, "counts": counts}
    doc["archive"] = {"products": products}


def new_document(series: Dict[str, Dict[str, List[List[Any]]]]) -> Dict[str, Any]:
    doc: Dict[str, Any] = {
        "version": 1,
        "built_at": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z"),
        "source": SOURCE,
        "row": ROW_FIELDS,
        "window": "trailing_calendar_3y",
        "window_label": WINDOW_LABEL,
        "percentile_rule": PERCENTILE_RULE,
        "higher_means": HIGHER_MEANS,
        "spot_percentile_rule": SPOT_PERCENTILE_RULE,
        "spot_higher_means": SPOT_HIGHER_MEANS,
        "annualization": ANNUALIZATION,
        "dividend_note": DIVIDEND_NOTE,
        "min_sample": MIN_SAMPLE,
        "yields": default_yields(),
        "archive": {},
        "series": series,
    }
    refresh_archive(doc)
    return doc


def iter_months(start: date, end: date) -> List[Tuple[int, int]]:
    y, m = start.year, start.month
    out: List[Tuple[int, int]] = []
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
    return out


def contract_symbols(product: str, today: date) -> List[str]:
    end = today + timedelta(days=240)
    return [
        fb.contract_code(product, y, m)
        for y, m in iter_months(CONTRACT_LIST_START[product], end)
    ]


def build_rows_for_product(
    product: str,
    spot_by_date: Dict[date, float],
    bars_by_code: Dict[str, Dict[date, float]],
    start: date,
    end: date,
) -> Dict[str, List[List[Any]]]:
    """One observation per trading day and tenor label."""
    yld = fb.PRODUCTS[product]["default_yield"]
    buckets: Dict[str, Dict[int, List[Any]]] = {label: {} for label in LABELS}
    for day in sorted(spot_by_date):
        if day < start or day > end:
            continue
        spot = spot_by_date[day]
        if spot is None or spot <= 0:
            continue
        try:
            contracts = fb.active_contracts(product, day)
        except RuntimeError:
            continue
        for contract in contracts:
            bars = bars_by_code.get(contract["code"])
            if not bars:
                continue
            fut = bars.get(day)
            if fut is None or fut <= 0:
                continue
            expiry = date.fromisoformat(contract["expiry"])
            spot_r = round(float(spot), 4)
            fut_r = round(float(fut), 2)
            corr = corrected_ann_365(spot_r, fut_r, yld, day, expiry)
            if corr is None:
                continue
            days = (expiry - day).days
            buckets[contract["label"]][ymd_int(day)] = [ymd_int(day), spot_r, fut_r, days, corr]
    return {
        label: [buckets[label][key] for key in sorted(buckets[label])]
        for label in LABELS
    }


def upsert_row(rows: List[List[Any]], row: List[Any]) -> List[List[Any]]:
    ymd = row[0]
    lo, hi = 0, len(rows)
    while lo < hi:
        mid = (lo + hi) // 2
        if rows[mid][0] < ymd:
            lo = mid + 1
        else:
            hi = mid
    if lo < len(rows) and rows[lo][0] == ymd:
        rows[lo] = row
    else:
        rows.insert(lo, row)
    return rows


def _throttle() -> None:
    global _NEXT_AT
    with _THROTTLE:
        now = time.monotonic()
        wait = _NEXT_AT - now
        if wait > 0:
            time.sleep(wait)
        _NEXT_AT = time.monotonic() + 0.25


def _http_text(url: str) -> str:
    last: Optional[BaseException] = None
    for attempt in range(5):
        _throttle()
        try:
            req = urllib.request.Request(url, headers=fb.SINA_HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 — retry transport and throttle errors
            last = exc
            time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"{url} failed: {last}")


def _cache_path(name: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{name}.json"


def _load_cache(name: str) -> Optional[Dict[str, float]]:
    path = _cache_path(name)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None
    return {key: float(value) for key, value in raw.items()}


def _save_cache(name: str, bars: Dict[date, float]) -> None:
    payload = {day.isoformat(): price for day, price in bars.items()}
    _cache_path(name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def parse_futures_kline(text: str) -> List[Dict[str, Any]]:
    """Sina JSONP is `var _X=([{...}]);` or `var _X=(null);`."""
    idx = text.find("=(")
    if idx < 0:
        if "null" in text:
            return []
        raise ValueError("unexpected futures kline")
    body = text[idx + 2 :].strip()
    if body.endswith(";"):
        body = body[:-1].strip()
    if body.endswith(")"):
        body = body[:-1].strip()
    if not body or body == "null":
        return []
    data = json.loads(body)
    if not data:
        return []
    if not isinstance(data, list):
        raise ValueError("futures kline is not a list")
    return data


def parse_index_kline(text: str) -> List[Dict[str, Any]]:
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end < start:
        raise ValueError("index kline has no array")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, list) or not data:
        raise ValueError("index kline is empty")
    return data


def _dated_closes(rows: List[Dict[str, Any]], day_key: str, close_key: str) -> Dict[date, float]:
    out: Dict[date, float] = {}
    for row in rows:
        try:
            day = date.fromisoformat(str(row[day_key])[:10])
            close = float(row[close_key])
        except (KeyError, TypeError, ValueError):
            continue
        if close > 0:
            out[day] = close
    return out


def fetch_index_closes(spot_code: str) -> Dict[date, float]:
    cached = _load_cache("idx_" + spot_code)
    if cached is not None:
        return {date.fromisoformat(key): value for key, value in cached.items()}
    url = (
        "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"CN_MarketData.getKLineData?symbol={spot_code}&scale=240&ma=no&datalen=6000"
    )
    last: Optional[BaseException] = None
    for _attempt in range(4):
        try:
            rows = parse_index_kline(_http_text(url))
            bars = _dated_closes(rows, "day", "close")
            if len(bars) < 500:
                raise RuntimeError(f"{spot_code} only {len(bars)} closes")
            _save_cache("idx_" + spot_code, bars)
            return bars
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.0)
    raise RuntimeError(f"index history {spot_code}: {last}")


def fetch_futures_closes(symbol: str) -> Dict[date, float]:
    cached = _load_cache("fut_" + symbol)
    if cached is not None:
        return {date.fromisoformat(key): value for key, value in cached.items()}
    url = (
        "https://stock.finance.sina.com.cn/futures/api/jsonp.php/"
        f"var%20_{symbol}=/InnerFuturesNewService.getDailyKLine?symbol={symbol}"
    )
    last: Optional[BaseException] = None
    for _attempt in range(4):
        try:
            rows = parse_futures_kline(_http_text(url))
            bars = _dated_closes(rows, "d", "c")
            _save_cache("fut_" + symbol, bars)
            return bars
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.8)
    print(f"  {symbol} skipped: {last}", file=sys.stderr, flush=True)
    return {}


def fetch_all_futures(symbols: List[str]) -> Dict[str, Dict[date, float]]:
    """Sequential. Sina drops the body when several contract histories run at once."""
    out: Dict[str, Dict[date, float]] = {}
    total = len(symbols)
    for done, symbol in enumerate(symbols, start=1):
        try:
            bars = fetch_futures_closes(symbol)
        except Exception as exc:  # noqa: BLE001
            print(f"  {symbol} FAIL {exc}", file=sys.stderr, flush=True)
            bars = {}
        out[symbol] = bars
        if bars:
            days = sorted(bars)
            print(
                f"  [{done}/{total}] {symbol} n={len(bars)} {days[0]}..{days[-1]}",
                flush=True,
            )
        elif done <= 8 or done % 25 == 0 or symbol[-2:] in {"03", "06", "09", "12"}:
            print(f"  [{done}/{total}] {symbol} empty", flush=True)
    return out


def backfill(today: date) -> Dict[str, Any]:
    print("backfill: index daily closes", flush=True)
    spots: Dict[str, Dict[date, float]] = {}
    for code, cfg in fb.PRODUCTS.items():
        bars = fetch_index_closes(cfg["spot_code"])
        days = sorted(bars)
        print(f"  {cfg['spot_code']} n={len(bars)} {days[0]}..{days[-1]}", flush=True)
        if code != "IM" and days[0] > date(2018, 10, 1):
            raise RuntimeError(f"{cfg['spot_code']} starts {days[0]}, expected through 2018-10")
        if code == "IM" and days[0] > date(2022, 7, 22):
            raise RuntimeError(f"{cfg['spot_code']} starts {days[0]}, expected through IM listing")
        spots[code] = bars

    symbols: List[str] = []
    for code in fb.PRODUCTS:
        symbols.extend(contract_symbols(code, today))
    print(f"backfill: {len(symbols)} futures contracts", flush=True)
    fut_bars = fetch_all_futures(symbols)

    series: Dict[str, Dict[str, List[List[Any]]]] = {}
    for code in fb.PRODUCTS:
        product_bars = {symbol: fut_bars.get(symbol, {}) for symbol in contract_symbols(code, today)}
        series[code] = build_rows_for_product(
            code,
            spots[code],
            product_bars,
            HISTORY_START[code],
            today,
        )
        for label, rows in series[code].items():
            span = ""
            if rows:
                span = f" {date_from_ymd(rows[0][0])}..{date_from_ymd(rows[-1][0])}"
            print(f"  {code} {label} n={len(rows)}{span}", flush=True)
        n_front = len(series[code]["当月"])
        if n_front < MIN_FRONT_ROWS:
            raise RuntimeError(f"{code} 当月 history has {n_front} rows (< {MIN_FRONT_ROWS})")
    return new_document(series)


def _sort_series(doc: Dict[str, Any]) -> None:
    for labels in doc["series"].values():
        for label in LABELS:
            labels.setdefault(label, [])
            labels[label].sort(key=lambda row: row[0])


def load_series(path: Path = SERIES_PATH) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("version") != 1 or "series" not in doc:
        raise RuntimeError(f"{path} is not a version-1 history store")
    for code in fb.PRODUCTS:
        doc["series"].setdefault(code, {})
        for label in LABELS:
            rows = doc["series"][code].setdefault(label, [])
            for row in rows:
                if len(row) != 5:
                    raise RuntimeError(f"{code} {label} row has {len(row)} fields")
    _sort_series(doc)
    return doc


# Header field order for the on-disk store. `series` is written last, by hand,
# so each close stays on one line. Unknown keys are kept after this list.
META_KEYS = (
    "version",
    "built_at",
    "source",
    "row",
    "window",
    "window_label",
    "percentile_rule",
    "higher_means",
    "spot_percentile_rule",
    "spot_higher_means",
    "annualization",
    "dividend_note",
    "min_sample",
    "yields",
    "archive",
)


def metadata_for_dump(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Metadata in stable order. `series` is spliced in by dumps_doc."""
    meta: Dict[str, Any] = {}
    for key in META_KEYS:
        if key in doc:
            meta[key] = doc[key]
    for key, value in doc.items():
        if key == "series" or key in meta:
            continue
        meta[key] = value
    return meta


def dumps_doc(doc: Dict[str, Any]) -> str:
    # json.dumps has no trailing newline. The last character is the outer `}`.
    # Peel that brace off and insert "series" before it. The field just before
    # the brace may be an object (archive) or a string (a rule added after the
    # file was first written); only the dump as a whole must end in `}`.
    meta_txt = json.dumps(metadata_for_dump(doc), ensure_ascii=False, indent=2).rstrip()
    if len(meta_txt) < 2 or not meta_txt.startswith("{") or not meta_txt.endswith("}"):
        raise RuntimeError("history metadata dump did not end in an object")
    head = meta_txt[:-1].rstrip()
    lines = [head + ","]
    lines.append('  "series": {')
    products = list(doc["series"].items())
    for pi, (product, labels) in enumerate(products):
        lines.append(f"    {json.dumps(product)}: {{")
        label_items = [(label, labels[label]) for label in LABELS if label in labels]
        for li, (label, rows) in enumerate(label_items):
            comma = "," if li < len(label_items) - 1 else ""
            lines.append(f"      {json.dumps(label, ensure_ascii=False)}: [")
            for ri, row in enumerate(rows):
                row_comma = "," if ri < len(rows) - 1 else ""
                lines.append(f"        {json.dumps(row, ensure_ascii=False)}{row_comma}")
            lines.append(f"      ]{comma}")
        product_comma = "," if pi < len(products) - 1 else ""
        lines.append(f"    }}{product_comma}")
    lines.append("  }")
    lines.append("}")
    return "\n".join(lines) + "\n"


def save_series(doc: Dict[str, Any], path: Path = SERIES_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(dumps_doc(doc), encoding="utf-8")
    tmp.replace(path)


def upsert_payload(doc: Dict[str, Any], payload: Dict[str, Any]) -> None:
    """Insert or replace each tenor's quote-date close. Days use the quote date."""
    for product in payload["products"]:
        code = product["code"]
        yld = float(doc["yields"][code])
        labels = doc["series"].setdefault(code, {label: [] for label in LABELS})
        for contract in product["contracts"]:
            if contract.get("error"):
                continue
            if contract.get("last") is None or contract.get("spot") is None:
                continue
            quote = contract.get("quote_date")
            if not quote:
                continue
            label = contract.get("label")
            if label not in LABELS:
                continue
            qd = date.fromisoformat(quote)
            expiry = date.fromisoformat(contract["expiry"])
            spot = round(float(contract["spot"]), 4)
            fut = round(float(contract["last"]), 2)
            corr = corrected_ann_365(spot, fut, yld, qd, expiry)
            if corr is None:
                continue
            row = [ymd_int(qd), spot, fut, (expiry - qd).days, corr]
            labels[label] = upsert_row(labels.setdefault(label, []), row)
    refresh_archive(doc)
    doc["built_at"] = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


def mark_main(product: Dict[str, Any]) -> None:
    best_i: Optional[int] = None
    best_oi = -1
    for i, contract in enumerate(product.get("contracts") or []):
        oi = contract.get("oi")
        if oi is None:
            continue
        if oi > best_oi:
            best_oi = oi
            best_i = i
    if best_i is not None and best_oi > 0:
        product["contracts"][best_i]["is_main"] = True


def _window_label(quote: date, sample_start: Optional[date]) -> str:
    if sample_start is None:
        return "样本不足"
    window_start = add_years(quote, -3)
    if sample_start > window_start + timedelta(days=10):
        return f"自{sample_start.isoformat()}起"
    return WINDOW_LABEL


def _blank_spot(product: Dict[str, Any]) -> None:
    product["spot_pct"] = None
    product["spot_n"] = 0
    product["spot_start"] = None
    product["spot_end"] = None
    product["spot_window_label"] = "样本不足"


def attach_spot_percentile(
    product: Dict[str, Any], payload: Dict[str, Any], doc: Dict[str, Any]
) -> None:
    """Index-level percentile. Independent of tenor and of the dividend yield."""
    quote_s = None
    for contract in product.get("contracts") or []:
        if contract.get("quote_date"):
            quote_s = contract["quote_date"]
            break
    if not quote_s:
        quote_s = payload.get("today")
    current = product.get("spot")
    if current is None:
        for contract in product.get("contracts") or []:
            if contract.get("spot") is not None:
                current = contract["spot"]
                break
    labels = doc.get("series", {}).get(product.get("code"), {})
    if not quote_s or current is None:
        _blank_spot(product)
        return
    quote = date.fromisoformat(quote_s)
    sample, first, last = select_spot_sample(labels, quote)
    product["spot_n"] = len(sample)
    product["spot_start"] = first.isoformat() if first else None
    product["spot_end"] = last.isoformat() if last else None
    if len(sample) >= MIN_SAMPLE:
        product["spot_pct"] = round1(percentile_leq(sample, float(current)))
        product["spot_window_label"] = _window_label(quote, first)
    else:
        product["spot_pct"] = None
        product["spot_window_label"] = "样本不足"


def attach_percentiles(payload: Dict[str, Any], doc: Dict[str, Any]) -> None:
    for product in payload["products"]:
        mark_main(product)
        attach_spot_percentile(product, payload, doc)
        code = product["code"]
        for contract in product["contracts"]:
            quote_s = contract.get("quote_date") or payload.get("today")
            current = contract.get("corr_ann_365")
            rows = doc["series"].get(code, {}).get(contract.get("label"), [])
            if not quote_s or current is None:
                contract["hist_pct"] = None
                contract["hist_n"] = 0
                contract["hist_start"] = None
                contract["hist_end"] = None
                contract["hist_window_label"] = "样本不足"
                continue
            quote = date.fromisoformat(quote_s)
            sample, first, last = select_sample(rows, quote)
            contract["hist_n"] = len(sample)
            contract["hist_start"] = first.isoformat() if first else None
            contract["hist_end"] = last.isoformat() if last else None
            if len(sample) >= MIN_SAMPLE:
                contract["hist_pct"] = round1(percentile_leq(sample, float(current)))
                contract["hist_window_label"] = _window_label(quote, first)
            else:
                contract["hist_pct"] = None
                contract["hist_window_label"] = "样本不足"

    bits = []
    for code, info in doc["archive"]["products"].items():
        n_front = info["counts"].get("当月", 0)
        if info.get("start"):
            bits.append(f"{code} 自{info['start']}起（当月 {n_front} 个交易日）")
        else:
            bits.append(f"{code} 无库存")
    payload["history_meta"] = {
        "window": "trailing_calendar_3y",
        "window_label": WINDOW_LABEL,
        "min_sample": MIN_SAMPLE,
        "rule": PERCENTILE_RULE,
        "higher_means": HIGHER_MEANS,
        "spot_rule": SPOT_PERCENTILE_RULE,
        "spot_higher_means": SPOT_HIGHER_MEANS,
        "annualization": ANNUALIZATION,
        "dividend_note": DIVIDEND_NOTE,
        "source": SOURCE,
        "store": "data/history/series.json",
        "archive_note": "；".join(bits),
        "archive": doc["archive"],
    }
    payload["methodology"]["percentile"] = PERCENTILE_RULE + HIGHER_MEANS
    payload["methodology"]["spot_percentile"] = SPOT_PERCENTILE_RULE + SPOT_HIGHER_MEANS
    payload["methodology"]["percentile_window"] = (
        "页面展示近 3 年（行情日往前 3 个日历年，不含当天）。"
        "data/history/series.json 保存自新浪日 K 可得日起的更长库存，Action 每个交易日追加。"
    )


def apply(payload: Dict[str, Any], rebuild: bool = False, path: Path = SERIES_PATH) -> None:
    today = date.fromisoformat(payload["today"])
    if rebuild or not path.exists():
        print("rebuilding history from Sina daily bars", flush=True)
        doc = backfill(today)
    else:
        loaded = load_series(path)
        if loaded is None:
            doc = backfill(today)
        else:
            doc = ensure_yields(loaded)
    upsert_payload(doc, payload)
    doc["percentile_rule"] = PERCENTILE_RULE
    doc["higher_means"] = HIGHER_MEANS
    doc["spot_percentile_rule"] = SPOT_PERCENTILE_RULE
    doc["spot_higher_means"] = SPOT_HIGHER_MEANS
    attach_percentiles(payload, doc)
    save_series(doc, path)
    print(f"wrote {path}", flush=True)
    for product in payload["products"]:
        print(
            f"  {product['code']} spot={product.get('spot')} "
            f"spot_pct={product.get('spot_pct')} N={product.get('spot_n')} "
            f"{product.get('spot_start')}..{product.get('spot_end')}",
            flush=True,
        )
        for contract in product["contracts"]:
            print(
                f"  {product['code']} {contract.get('label')} {contract.get('code')} "
                f"ann={contract.get('corr_ann_365')} "
                f"pct={contract.get('hist_pct')} N={contract.get('hist_n')} "
                f"{contract.get('hist_start')}..{contract.get('hist_end')}",
                flush=True,
            )
