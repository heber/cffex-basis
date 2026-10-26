#!/usr/bin/env python3
"""Fetch broad-based ETF creations and write etf-flow/data/latest.json.

Seven categories, the same set the mini-program table adds up to
(checked against the 2026-10-23 screenshot):

    上证50 000016, 沪深300 000300, 中证A500 000510, 中证500 000905,
    中证1000 000852, 创业板指 399006, 科创50 000688

    净申赎金额 = (当日份额 − 前一披露日份额) × 当日单位净值

Sources, all public and keyless:

- Who tracks which index: Eastmoney datacenter `RPT_FUND_ETF_INDEX`
- Shanghai shares: query.sse.com.cn ETF scale, TOT_VOL in 万份
- Shenzhen shares: szse.cn ShowReport catalog `scsj_fund_jjgm`, 份
- NAV: Sina `CaihuiFundInfoService.getNav` (单位净值)
- Close, for the premium tab: Sina unadjusted daily kline

The run keeps per-fund history in data/history/funds.json and only
re-requests the recent overlap unless `--rebuild` is set. A session
where far fewer funds printed than usual is left out, so a one-exchange
outage does not look like a wave of redemptions.

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import calc
import parse_reports
import universe

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
LATEST = DATA / "latest.json"
HISTORY = DATA / "history" / "funds.json"
CST = timezone(timedelta(hours=8))

# Share history starts before the published window so the first 2026
# session has a previous print to subtract.
HISTORY_START = "2025-12-15"
PUBLISH_FROM = "2026-01-01"
OVERLAP_DAYS = 8

# 2026-10-23 / 2026-10-22 screenshot rows. A later exchange revision can
# move these; the run prints a drift line and still publishes.
ANCHORS = {
    "2026-10-23": {"csi1000": 52.24, "chinext": 74.88, "star50": 47.70, "total": 510.82},
    "2026-10-22": {"csi1000": 32.05, "chinext": 68.80, "star50": 74.66, "total": 484.28},
}

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)
SSE_HEADERS = {"User-Agent": UA, "Referer": "https://www.sse.com.cn/"}
SZSE_HEADERS = {
    "User-Agent": UA,
    "Referer": "https://www.szse.cn/market/fund/volume/etf/index.html",
}
EM_HEADERS = {"User-Agent": UA, "Referer": "https://data.eastmoney.com/"}
SINA_HEADERS = {"User-Agent": UA, "Referer": "https://finance.sina.com.cn"}

FORMULA = "净申赎金额 = (当日份额 − 前一披露日份额) × 当日单位净值"
AUM_FORMULA = "规模 = 份额 × 单位净值"
PREMIUM_FORMULA = "折溢价率 = (收盘价 − 单位净值) / 单位净值"


def now_cst() -> datetime:
    return datetime.now(CST)


def parse_day(day: str) -> datetime:
    return datetime.strptime(day, "%Y-%m-%d")


def iso(day: datetime) -> str:
    return day.strftime("%Y-%m-%d")


def http_get(url: str, headers: dict, timeout: int = 45, retries: int = 5) -> bytes:
    last = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code not in (429, 500, 502, 503, 504):
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last = exc
        time.sleep(0.7 * (attempt + 1))
    raise RuntimeError(f"GET failed {url[:140]}: {last}")


def load_json(raw: bytes):
    if not raw or raw.strip() in (b"null", b""):
        return None
    text = None
    for encoding in ("utf-8", "gb18030"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return None
    text = text.strip().lstrip("\ufeff")
    if text in ("", "null"):
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def dump_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def weekdays(start: str, end: str) -> list[str]:
    cursor = parse_day(start)
    stop = parse_day(end)
    out = []
    while cursor <= stop:
        if cursor.weekday() < 5:
            out.append(iso(cursor))
        cursor += timedelta(days=1)
    return out


def month_spans(start: str, end: str) -> list[tuple[str, str]]:
    cursor = parse_day(start).replace(day=1)
    stop = parse_day(end)
    spans = []
    while cursor <= stop:
        if cursor.month == 12:
            next_month = cursor.replace(year=cursor.year + 1, month=1, day=1)
        else:
            next_month = cursor.replace(month=cursor.month + 1, day=1)
        span_end = min(stop, next_month - timedelta(days=1))
        span_start = max(parse_day(start), cursor)
        spans.append((iso(span_start), iso(span_end)))
        cursor = next_month
    return spans


def norm_shares(value: float):
    rounded = round(float(value), 2)
    if float(rounded).is_integer():
        return int(rounded)
    return rounded


def sina_symbol(code: str) -> str:
    return ("sh" if code.startswith(("5", "6")) else "sz") + code


def fetch_universe() -> list[dict]:
    rows = []
    page = 1
    pages = 1
    while page <= pages:
        query = urllib.parse.urlencode(
            {
                "reportName": "RPT_FUND_ETF_INDEX",
                "columns": "SECURITY_CODE,SECURITY_NAME_ABBR,INDEX_CODE,INDEX_NAME_ABBR,TOTAL_SHARE",
                "pageSize": "500",
                "pageNumber": str(page),
                "sortColumns": "SECURITY_CODE",
                "sortTypes": "1",
            }
        )
        payload = load_json(
            http_get(
                "https://datacenter-web.eastmoney.com/api/data/v1/get?" + query,
                EM_HEADERS,
            )
        )
        result = (payload or {}).get("result") or {}
        chunk = result.get("data") or []
        rows.extend(chunk)
        pages = int(result.get("pages") or page)
        page += 1
    picked = []
    seen = set()
    for row in rows:
        category = universe.category_by_index(row.get("INDEX_CODE"))
        code = str(row.get("SECURITY_CODE") or "").strip()
        if not category or not code or code in seen:
            continue
        seen.add(code)
        try:
            reported = float(row["TOTAL_SHARE"]) * parse_reports.WAN_FEN if row.get("TOTAL_SHARE") not in (None, "") else None
        except (TypeError, ValueError):
            reported = None
        picked.append(
            {
                "code": code,
                "name": str(row.get("SECURITY_NAME_ABBR") or code).strip(),
                "category": category["id"],
                "index_code": category["index_code"],
                "reported_shares": reported,
            }
        )
    if len(picked) < calc.MIN_PUBLISH_FUNDS:
        raise RuntimeError(f"universe too small: {len(picked)}")
    print(f"universe {len(picked)} funds", file=sys.stderr)
    return picked


def fetch_sse_day(day: str) -> list[dict]:
    query = urllib.parse.urlencode(
        {
            "isPagination": "true",
            "pageHelp.pageSize": "10000",
            "pageHelp.pageNo": "1",
            "pageHelp.beginPage": "1",
            "pageHelp.cacheSize": "1",
            "pageHelp.endPage": "1",
            "sqlId": "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L",
            "STAT_DATE": day,
        }
    )
    payload = load_json(http_get("https://query.sse.com.cn/commonQuery.do?" + query, SSE_HEADERS))
    if not isinstance(payload, dict):
        raise RuntimeError(f"SSE payload not json for {day}")
    return parse_reports.parse_sse_scale(payload)


def fetch_szse_span(start: str, end: str, depth: int = 0) -> list[dict]:
    query = urllib.parse.urlencode(
        {
            "SHOWTYPE": "xlsx",
            "CATALOGID": "scsj_fund_jjgm",
            "TABKEY": "tab1",
            "txtStart": start,
            "txtEnd": end,
            "jjlb": "ETF",
            "random": "0.27",
        }
    )
    url = "https://www.szse.cn/api/report/ShowReport?" + query
    try:
        blob = http_get(url, SZSE_HEADERS, timeout=120, retries=6)
        if blob[:2] != b"PK":
            raise RuntimeError(f"SZSE did not return xlsx ({len(blob)} bytes)")
        rows = parse_reports.parse_szse_scale_xlsx(blob)
        if not rows and (parse_day(end) - parse_day(start)).days > 4:
            raise RuntimeError("empty SZSE workbook")
        print(f"szse {start}..{end} rows {len(rows)} bytes {len(blob)}", file=sys.stderr)
        return rows
    except Exception as exc:
        span = (parse_day(end) - parse_day(start)).days
        if depth >= 3 or span < 3:
            raise
        print(f"szse split {start}..{end}: {exc}", file=sys.stderr)
        mid = parse_day(start) + timedelta(days=span // 2)
        left = fetch_szse_span(start, iso(mid), depth + 1)
        right = fetch_szse_span(iso(mid + timedelta(days=1)), end, depth + 1)
        return left + right


def fetch_nav(code: str, start: str, end: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for page in range(1, 8):
        query = urllib.parse.urlencode(
            {
                "symbol": code,
                "datefrom": start,
                "dateto": end,
                "num": "100",
                "page": str(page),
            }
        )
        url = (
            "https://stock.finance.sina.com.cn/fundInfo/api/openapi.php/"
            "CaihuiFundInfoService.getNav?" + query
        )
        payload = load_json(http_get(url, SINA_HEADERS, timeout=30, retries=4))
        rows = (((payload or {}).get("result") or {}).get("data") or {}).get("data") or []
        if not isinstance(rows, list) or not rows:
            break
        for row in rows:
            day = str(row.get("fbrq") or "")[:10]
            try:
                nav = float(row["jjjz"])
            except (TypeError, ValueError, KeyError):
                continue
            if day and nav > 0:
                out[day] = nav
        if len(rows) < 100:
            break
    return out


def fetch_closes(code: str, length: int) -> dict[str, float]:
    query = urllib.parse.urlencode(
        {
            "symbol": sina_symbol(code),
            "scale": "240",
            "ma": "no",
            "datalen": str(length),
        }
    )
    url = (
        "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        "CN_MarketData.getKLineData?" + query
    )
    payload = load_json(http_get(url, SINA_HEADERS, timeout=30, retries=4))
    out: dict[str, float] = {}
    if not isinstance(payload, list):
        return out
    for row in payload:
        day = str(row.get("day") or "")[:10]
        try:
            close = float(row["close"])
        except (TypeError, ValueError, KeyError):
            continue
        if day and close > 0:
            out[day] = close
    return out


def empty_fund(meta: dict) -> dict:
    return {
        "name": meta["name"],
        "category": meta["category"],
        "index_code": meta["index_code"],
        "shares": {},
        "nav": {},
        "close": {},
    }


def load_history() -> dict:
    if not HISTORY.exists():
        return {}
    try:
        payload = json.loads(HISTORY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    funds = payload.get("funds") if isinstance(payload, dict) else None
    return funds if isinstance(funds, dict) else {}


def merge_share_rows(funds: dict, rows: list[dict], allowed: dict[str, dict]) -> int:
    kept = 0
    for row in rows:
        meta = allowed.get(row["code"])
        if not meta or not row.get("date"):
            continue
        fund = funds.setdefault(row["code"], empty_fund(meta))
        fund["name"] = meta["name"]
        fund["category"] = meta["category"]
        fund["index_code"] = meta["index_code"]
        fund["shares"][row["date"]] = norm_shares(row["shares"])
        kept += 1
    return kept


def merge_map(funds: dict, code: str, field: str, values: dict) -> None:
    fund = funds.get(code)
    if not fund or not values:
        return
    bucket = fund.setdefault(field, {})
    for day, value in values.items():
        bucket[day] = round(float(value), 4 if field == "nav" else 4)


def check_share_units(catalog: list[dict], funds: dict, as_of: str) -> None:
    """Eastmoney TOTAL_SHARE is 万份 and should match the exchange print."""
    ratios = []
    for meta in catalog:
        reported = meta.get("reported_shares")
        got = ((funds.get(meta["code"]) or {}).get("shares") or {}).get(as_of)
        if not reported or not got or reported <= 0 or got <= 0:
            continue
        ratios.append(float(got) / float(reported))
    if len(ratios) < 40:
        print(f"unit check skipped, paired funds {len(ratios)}", file=sys.stderr)
        return
    mid = calc.median(ratios)
    print(f"share-unit median exchange/eastmoney {mid:.4f} on {len(ratios)} funds", file=sys.stderr)
    if not 0.5 <= mid <= 1.5:
        raise RuntimeError(f"share units look wrong, median ratio {mid:.4f}")


def check_anchors(series: list[dict]) -> None:
    by_day = {row["date"]: row for row in series}
    for day, expect in ANCHORS.items():
        row = by_day.get(day)
        if row is None:
            print(f"anchor {day} not in this snapshot", file=sys.stderr)
            continue
        for key, target in expect.items():
            got = row["total"] if key == "total" else row["flow"].get(key)
            if got is None or abs(got - target) > 0.08:
                print(f"ANCHOR DRIFT {day} {key}: got {got} expected {target}", file=sys.stderr)
            else:
                print(f"anchor {day} {key} {got}", file=sys.stderr)


def round_money_yi(yuan) -> float | None:
    if yuan is None:
        return None
    return round(float(yuan) / 1e8, 4)


def series_for_page(rows: list[dict]) -> list[dict]:
    page = []
    for row in rows:
        page.append(
            {
                "date": row["date"],
                "flow": {key: round(value / 1e8, 4) for key, value in row["flow"].items()},
                "total": round(row["total"] / 1e8, 4),
                "aum": {key: round(value / 1e8, 4) for key, value in row["aum"].items()},
                "aum_total": round(row["aum_total"] / 1e8, 4),
                "premium": {
                    key: (None if value is None else round(value, 6))
                    for key, value in row["premium"].items()
                },
                "premium_total": None if row["premium_total"] is None else round(row["premium_total"], 6),
            }
        )
    return page


def funds_for_page(rows: list[dict]) -> list[dict]:
    order = {category: index for index, category in enumerate(universe.CATEGORY_IDS)}
    shaped = []
    for row in rows:
        shaped.append(
            {
                "code": row["code"],
                "name": row["name"],
                "category": row["category"],
                "shares": norm_shares(row["shares"]),
                "nav": None if row["nav"] is None else round(row["nav"], 4),
                "close": None if row["close"] is None else round(row["close"], 4),
                "flow": round_money_yi(row["flow"]),
                "aum": round_money_yi(row["aum"]),
                "premium": None if row["premium"] is None else round(row["premium"], 6),
            }
        )
    shaped.sort(key=lambda item: (order.get(item["category"], 99), -(item["aum"] or 0), item["code"]))
    return shaped


def parallel_map(keys: list[str], worker, label: str, workers: int) -> dict:
    found = {}
    failures = 0
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(worker, key): key for key in keys}
        for future in as_completed(futures):
            key = futures[future]
            done += 1
            try:
                found[key] = future.result()
            except Exception as exc:
                failures += 1
                print(f"{label} miss {key}: {exc}", file=sys.stderr)
            if done % 40 == 0 or done == len(keys):
                print(f"{label} {done}/{len(keys)} fail {failures}", file=sys.stderr)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch ETF creation/redemption snapshot")
    parser.add_argument("--rebuild", action="store_true", help="ignore saved history and reload from 2025-12-15")
    args = parser.parse_args()

    today = iso(now_cst())
    catalog = fetch_universe()
    allowed = {row["code"]: row for row in catalog}
    funds = {} if args.rebuild else load_history()
    # Drop anything Eastmoney no longer tags with one of the seven indexes,
    # so a past mis-tag does not keep flowing into the total.
    funds = {code: fund for code, fund in funds.items() if code in allowed}
    for code, meta in allowed.items():
        fund = funds.setdefault(code, empty_fund(meta))
        fund["name"] = meta["name"]
        fund["category"] = meta["category"]
        fund["index_code"] = meta["index_code"]
        fund.setdefault("shares", {})
        fund.setdefault("nav", {})
        fund.setdefault("close", {})

    known_days = sorted({day for fund in funds.values() for day in fund["shares"]})
    full = args.rebuild or len(known_days) < 40
    if full:
        start = HISTORY_START
    else:
        start = iso(parse_day(known_days[-1]) - timedelta(days=OVERLAP_DAYS))
        if start < HISTORY_START:
            start = HISTORY_START
    print(f"share window {start} .. {today} rebuild={full}", file=sys.stderr)

    sse_days = weekdays(start, today)
    sse_rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch_sse_day, day): day for day in sse_days}
        finished = 0
        for future in as_completed(futures):
            day = futures[future]
            finished += 1
            try:
                sse_rows.extend(future.result())
            except Exception as exc:
                print(f"sse miss {day}: {exc}", file=sys.stderr)
            if finished % 25 == 0 or finished == len(sse_days):
                print(f"sse {finished}/{len(sse_days)}", file=sys.stderr)
    merge_share_rows(funds, sse_rows, allowed)

    for span_start, span_end in month_spans(start, today):
        try:
            szse_rows = fetch_szse_span(span_start, span_end)
        except Exception as exc:
            print(f"szse miss {span_start}..{span_end}: {exc}", file=sys.stderr)
            continue
        merge_share_rows(funds, szse_rows, allowed)

    nav_start = HISTORY_START if full else iso(parse_day(start) - timedelta(days=12))
    close_len = 320 if full else 40

    def nav_worker(code: str):
        return fetch_nav(code, nav_start, today)

    def close_worker(code: str):
        # A fund with no stored closes still needs the longer kline.
        length = close_len
        if not (funds.get(code) or {}).get("close"):
            length = 320
        return fetch_closes(code, length)

    codes = sorted(allowed)
    nav_maps = parallel_map(codes, nav_worker, "nav", 10)
    close_maps = parallel_map(codes, close_worker, "close", 10)
    for code, values in nav_maps.items():
        merge_map(funds, code, "nav", values)
    for code, values in close_maps.items():
        merge_map(funds, code, "close", values)

    sample_nav = len((funds.get("510300") or {}).get("nav") or {})
    sample_close = len((funds.get("510300") or {}).get("close") or {})
    print(f"510300 nav days {sample_nav} close days {sample_close}", file=sys.stderr)
    if full and sample_nav < 150:
        raise RuntimeError(f"510300 NAV history is only {sample_nav} days")

    rows, as_of = calc.build_series(funds, universe.CATEGORY_IDS, PUBLISH_FROM)
    if not as_of:
        raise RuntimeError("no usable share sessions")
    latest = calc.latest_funds(funds, universe.CATEGORY_IDS, as_of)
    priced = len(latest)
    if not calc.publishable(priced):
        raise RuntimeError(f"only {priced} funds priced on {as_of}, refuse to publish")
    staleness = (parse_day(today) - parse_day(as_of)).days
    if staleness > 6:
        raise RuntimeError(f"latest share session {as_of} is {staleness} days behind {today}")

    check_share_units(catalog, funds, as_of)
    page_series = series_for_page(rows)
    check_anchors(page_series)
    page_funds = funds_for_page(latest)
    missing = []
    have_shares = {row["code"] for row in page_funds}
    for meta in catalog:
        if meta["code"] not in have_shares:
            missing.append({"code": meta["code"], "name": meta["name"], "category": meta["category"]})

    ytd = [row for row in page_series if row["date"] >= f"{as_of[:4]}-01-01"]
    ytd_mean = round(sum(row["total"] for row in ytd) / len(ytd), 4) if ytd else None
    snapshot = {
        "generated_at": now_cst().isoformat(timespec="seconds"),
        "as_of": as_of,
        "publish_from": PUBLISH_FROM,
        "unit": "亿元",
        "formula": FORMULA,
        "aum_formula": AUM_FORMULA,
        "premium_formula": PREMIUM_FORMULA,
        "windows": {
            "5d": "最近 5 个交易日",
            "1m": "快照日往前 1 个日历月，含两端",
            "3m": "快照日往前 3 个日历月，含两端",
            "ytd": "当年 1 月 1 日至快照日",
        },
        "categories": universe.CATEGORIES,
        "coverage": {
            "universe": len(catalog),
            "priced_on_as_of": priced,
            "with_flow_on_as_of": sum(1 for row in page_funds if row["flow"] is not None),
            "with_premium_on_as_of": sum(1 for row in page_funds if row["premium"] is not None),
            "trading_days": len(page_series),
            "ytd_days": len(ytd),
            "ytd_mean_total": ytd_mean,
            "missing_share_print": missing[:40],
            "missing_share_print_count": len(missing),
            "includes_enhanced": True,
            "split_rule": (
                "净值单日变动达到 15%，或变动达到 8% 且份额×净值基本不变时，"
                "按折算把前日份额换到当日净值上再算申赎；否则直接用份额差。"
            ),
        },
        "sources": {
            "universe": "东方财富 datacenter RPT_FUND_ETF_INDEX（跟踪指数代码）",
            "shares_sse": "上交所 query.sse.com.cn ETF 基金规模，TOT_VOL 为万份，脚本乘以 10000",
            "shares_szse": "深交所 www.szse.cn ShowReport CATALOGID=scsj_fund_jjgm，基金规模(份)",
            "nav": "新浪财经 CaihuiFundInfoService.getNav 单位净值",
            "close": "新浪财经 CN_MarketData.getKLineData 不复权日收盘，只用于折溢价",
        },
        "series": page_series,
        "funds": page_funds,
    }
    history = {
        "version": 1,
        "updated": snapshot["generated_at"],
        "funds": funds,
    }
    dump_json(HISTORY, history)
    dump_json(LATEST, snapshot)
    last = page_series[-1]
    print(
        f"as_of {as_of} funds {priced} total {last['total']:+.2f}亿 "
        f"ytd_days {len(ytd)} ytd_mean {ytd_mean}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
