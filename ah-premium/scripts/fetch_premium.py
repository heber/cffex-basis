#!/usr/bin/env python3
"""Fetch A/H dual-listed prices and write ah-premium/data/latest.json.

Industry AH premium, A versus H, same convention as the mainland AH boards:

    H股折合人民币 = H股价格（港元） × 港元兑人民币
    AH溢价率     = (A股人民币价格 − H股折合人民币) / H股折合人民币

Positive means the A-share is richer than the H-share after FX.
Negative means the A-share is cheaper. Comparison is per share
(1 A share = 1 H share), not lot size and not a market-cap index.

Quotes: Sina hq.sinajs.cn (Referer: finance.sina.com.cn), same family as
the CFFEX and commodity jobs. FX default is the Sina HKDCNY spot print.
The PBOC/CFETS central parity from China Money is stored beside it so the
page can restate the premium; it is not required to publish.

A name that does not match the curated issuer is skipped rather than
priced. The run refuses to overwrite the snapshot when too few names quote.
Stdlib only.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import universe

ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = ROOT / "data" / "latest.json"
CST = timezone(timedelta(hours=8))
MIN_QUOTED = 90

SINA_HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

FORMULA = (
    "AH溢价率 = (A股人民币价格 − H股港元价格 × 港元兑人民币) "
    "/ (H股港元价格 × 港元兑人民币)"
)


def now_cst() -> datetime:
    return datetime.now(CST)


def http_get(url: str, headers: dict, timeout: int = 25) -> bytes:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def parse_sina_quotes(raw: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in raw.splitlines():
        line = line.strip()
        if '="' not in line:
            continue
        left, right = line.split("=", 1)
        key = left.replace("var hq_str_", "").strip()
        payload = right.strip()
        if payload.endswith(";"):
            payload = payload[:-1]
        if payload.startswith('"') and payload.endswith('"'):
            payload = payload[1:-1]
        out[key] = payload.split(",") if payload else []
    return out


def fetch_symbols(symbols: list[str]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}

    def pull(batch: list[str]) -> None:
        if not batch:
            return
        url = "https://hq.sinajs.cn/list=" + ",".join(batch)
        last = None
        for attempt in range(4):
            try:
                raw = http_get(url, SINA_HEADERS).decode("gb18030", "replace")
                if '="' not in raw:
                    raise RuntimeError("empty Sina payload")
                found.update(parse_sina_quotes(raw))
                return
            except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
                last = exc
                time.sleep(0.8 * (attempt + 1))
        if len(batch) == 1:
            print(f"miss {batch[0]}: {last}", file=sys.stderr)
            return
        mid = max(1, len(batch) // 2)
        time.sleep(0.4)
        pull(batch[:mid])
        pull(batch[mid:])

    for i in range(0, len(symbols), 20):
        pull(symbols[i:i + 20])
        time.sleep(0.3)
    return found


def _f(fields: list[str], idx: int) -> float | None:
    if idx >= len(fields) or fields[idx] == "":
        return None
    try:
        return float(fields[idx])
    except ValueError:
        return None


def parse_a(fields: list[str]) -> dict | None:
    if not fields or len(fields) < 4 or not fields[0]:
        return None
    last = _f(fields, 3)
    prev = _f(fields, 2)
    if last is None or last <= 0:
        return None
    change = (last - prev) / prev if prev and prev > 0 else None
    return {
        "name": fields[0],
        "price": last,
        "prev": prev,
        "change_pct": change,
        "date": fields[30] if len(fields) > 30 and fields[30] else None,
        "time": fields[31] if len(fields) > 31 and fields[31] else None,
    }


def parse_h(fields: list[str]) -> dict | None:
    if not fields or len(fields) < 7:
        return None
    last = _f(fields, 6)
    prev = _f(fields, 3)
    if last is None or last <= 0:
        return None
    change = (last - prev) / prev if prev and prev > 0 else None
    date = fields[17] if len(fields) > 17 else ""
    if date and "/" in date:
        date = date.replace("/", "-")
    return {
        "name": fields[1] if len(fields) > 1 else "",
        "en": fields[0] if fields else "",
        "price": last,
        "prev": prev,
        "change_pct": change,
        "date": date or None,
        "time": fields[18] if len(fields) > 18 and fields[18] else None,
    }


def _fx_record(symbol: str, fields: list[str], last: float, name_idx: int, date_idx: int, time_idx: int = 0) -> dict | None:
    if not (0.5 < last < 1.2):
        return None
    name = fields[name_idx] if name_idx < len(fields) else ""
    if "港元" not in name and "港币" not in name:
        return None
    date = fields[date_idx] if date_idx < len(fields) else None
    return {
        "symbol": symbol,
        "rate": last,
        "bid": _f(fields, 1),
        "ask": _f(fields, 2),
        "prev_close": _f(fields, 8) if symbol == "HKDCNY" else None,
        "quote_time": fields[time_idx] if time_idx < len(fields) else None,
        "quote_date": date or None,
        "name": name,
    }


def parse_fx(quotes: dict[str, list[str]]) -> dict | None:
    """Sina HKDCNY last price: how many CNY for 1 HKD. Spot, not the fixing."""
    primary = quotes.get("HKDCNY") or []
    if len(primary) >= 11:
        last = _f(primary, 3)
        if last is not None:
            parsed = _fx_record("HKDCNY", primary, last, 9, 10)
            if parsed:
                return parsed
    backup = quotes.get("fx_shkdcny") or []
    if len(backup) >= 18:
        last = _f(backup, 3)
        if last is not None:
            return _fx_record("fx_shkdcny", backup, last, 9, 17)
    return None


def fetch_pboc() -> dict | None:
    """Best-effort CNY central parity for HKD. Failure leaves the spot rate in place."""
    url = "https://www.chinamoney.com.cn/r/cms/www/chinamoney/data/fx/ccpr.json"
    headers = {
        "User-Agent": SINA_HEADERS["User-Agent"],
        "Referer": "https://www.chinamoney.com.cn/",
    }
    try:
        doc = json.loads(http_get(url, headers, timeout=20).decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeError) as exc:
        print(f"pboc mid unavailable: {exc}", file=sys.stderr)
        return None
    hit = None
    for rec in doc.get("records") or []:
        ename = rec.get("vrtEName") or ""
        if rec.get("foreignCName") == "HKD" and ename.startswith("HKD/") and "港元" in (rec.get("vrtName") or ""):
            hit = rec
            break
    if not hit:
        print("pboc mid missing HKD/CNY", file=sys.stderr)
        return None
    try:
        rate = float(hit["price"])
    except (TypeError, ValueError, KeyError):
        return None
    if not 0.5 < rate < 1.2:
        return None
    return {
        "rate": rate,
        "date": (doc.get("data") or {}).get("lastDate"),
        "name": hit.get("vrtName") or "港元/人民币",
        "source": "中国货币网人民币汇率中间价",
    }


def ah_premium(a_cny: float, h_hkd: float, hkd_cny: float) -> tuple[float | None, float | None]:
    """Return (H in CNY, premium). Premium is (A - H_cny) / H_cny."""
    if a_cny is None or h_hkd is None or hkd_cny is None:
        return None, None
    if a_cny <= 0 or h_hkd <= 0 or hkd_cny <= 0:
        return None, None
    h_cny = h_hkd * hkd_cny
    if h_cny <= 0:
        return None, None
    return h_cny, (a_cny - h_cny) / h_cny


def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _round(value, digits):
    if value is None:
        return None
    return round(float(value), digits)


def mode_date(dates: list[str | None]) -> str | None:
    counts = Counter(d for d in dates if d)
    if not counts:
        return None
    return max(counts.items(), key=lambda item: (item[1], item[0]))[0]


def quote_symbols(pairs: list[dict] | None = None) -> list[str]:
    rows = universe.PAIRS if pairs is None else pairs
    symbols: list[str] = []
    seen = set()
    for item in rows:
        for symbol in (item["a_symbol"], universe.h_symbol(item["h_code"])):
            if symbol not in seen:
                seen.add(symbol)
                symbols.append(symbol)
    for symbol in ("HKDCNY", "fx_shkdcny"):
        if symbol not in seen:
            symbols.append(symbol)
    return symbols


def build_rows(pairs: list[dict], quotes: dict[str, list[str]], fx_rate: float) -> tuple[list[dict], list[dict]]:
    rows: list[dict] = []
    skipped: list[dict] = []
    for item in pairs:
        a = parse_a(quotes.get(item["a_symbol"]) or [])
        h = parse_h(quotes.get(universe.h_symbol(item["h_code"])) or [])
        if a is None or h is None:
            skipped.append({
                "name": item["name"],
                "a_symbol": item["a_symbol"],
                "h_code": item["h_code"],
                "reason": "无A股报价" if a is None else "无H股报价",
            })
            continue
        if not universe.names_match(a["name"], h["name"], item, h.get("en") or ""):
            skipped.append({
                "name": item["name"],
                "a_symbol": item["a_symbol"],
                "h_code": item["h_code"],
                "reason": f"名称不符 A={a['name']} H={h['name']}",
            })
            continue
        h_cny, premium = ah_premium(a["price"], h["price"], fx_rate)
        rows.append({
            "name": item["name"],
            "sector": item["sector"],
            "a_code": universe.a_code(item["a_symbol"]),
            "a_symbol": item["a_symbol"],
            "a_name": a["name"],
            "a_price": _round(a["price"], 4),
            "a_prev_close": _round(a["prev"], 4),
            "a_change_pct": _round(a["change_pct"], 6),
            "a_date": a["date"],
            "a_time": a["time"],
            "h_code": item["h_code"],
            "h_symbol": universe.h_symbol(item["h_code"]),
            "h_name": h["name"],
            "h_price_hkd": _round(h["price"], 4),
            "h_prev_close_hkd": _round(h["prev"], 4),
            "h_change_pct": _round(h["change_pct"], 6),
            "h_date": h["date"],
            "h_time": h["time"],
            "h_in_cny": _round(h_cny, 4),
            "premium": _round(premium, 6),
        })
    return rows, skipped


def summarize(rows: list[dict]) -> dict:
    premiums = [row["premium"] for row in rows if row.get("premium") is not None]
    return {
        "universe": len(universe.PAIRS),
        "quoted": len(premiums),
        "premium_count": sum(1 for value in premiums if value > 0),
        "discount_count": sum(1 for value in premiums if value < 0),
        "median_premium": _round(median(premiums), 6),
        "mean_premium": _round(sum(premiums) / len(premiums), 6) if premiums else None,
    }


def build_payload(pairs: list[dict], quotes: dict[str, list[str]], fx: dict, pboc: dict | None, generated_at: str) -> dict:
    rows, skipped = build_rows(pairs, quotes, fx["rate"])
    summary = summarize(rows)
    summary["universe"] = len(pairs)
    summary["skipped"] = len(skipped)
    return {
        "generated_at": generated_at,
        "as_of": mode_date([row["a_date"] for row in rows]),
        "h_as_of": mode_date([row["h_date"] for row in rows]),
        "fx": {
            "pair": "HKDCNY",
            "meaning": "1 港元兑换的人民币",
            "rate": _round(fx["rate"], 6),
            "bid": _round(fx.get("bid"), 6),
            "ask": _round(fx.get("ask"), 6),
            "prev_close": _round(fx.get("prev_close"), 6),
            "symbol": fx.get("symbol"),
            "name": fx.get("name"),
            "quote_date": fx.get("quote_date"),
            "quote_time": fx.get("quote_time"),
            "source": "新浪财经 hq.sinajs.cn 港元人民币即期",
            "pboc_mid": None if not pboc else _round(pboc["rate"], 6),
            "pboc_date": None if not pboc else pboc.get("date"),
            "pboc_source": None if not pboc else pboc.get("source"),
        },
        "sectors": [name for name in universe.SECTORS if any(row["sector"] == name for row in rows)],
        "methodology": {
            "formula": FORMULA,
            "h_in_cny": "H股折合人民币 = H股价格（港元） × 港元兑人民币",
            "sign": "正数 = A股相对H股溢价（A更贵）；负数 = A股折价（A更便宜）",
            "share": "按每股价格比较，1股A对1股H。不按买卖单位，不按市值加权。",
            "fx": "计算默认用新浪港元人民币即期最新价。央行中间价另存，页面可切换后重算。",
            "clock": "A股现金收盘约15:00，H股现金收盘约16:00，两边不是同一秒。汇率打印时间单独标出。",
            "universe": "固定名单见 scripts/universe.py，只收常见AH双重上市。不是恒生AH股溢价指数。",
            "not_paired": "同名不同发行人不成对，例如中国建筑与中国建筑国际、中国电建与中广核电力。只有GDR没有H股的不收。H股用港币柜台。",
        },
        "summary": summary,
        "skipped": skipped,
        "pairs": rows,
    }


def assert_publishable(payload: dict, minimum: int = MIN_QUOTED) -> None:
    rate = (payload.get("fx") or {}).get("rate")
    if not rate or not (0.5 < rate < 1.2):
        raise RuntimeError("missing HKDCNY spot")
    quoted = (payload.get("summary") or {}).get("quoted") or 0
    if quoted < minimum:
        sample = ", ".join(item["name"] + ":" + item["reason"] for item in (payload.get("skipped") or [])[:12])
        raise RuntimeError(f"only {quoted} quoted pairs, need {minimum}. skipped: {sample}")


def write_snapshot(payload: dict) -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(OUT_PATH)


def main(argv: list[str] | None = None) -> int:
    del argv
    try:
        quotes = fetch_symbols(quote_symbols())
        fx = parse_fx(quotes)
        if fx is None:
            raise RuntimeError("Sina HKDCNY quote missing")
        pboc = fetch_pboc()
        payload = build_payload(universe.PAIRS, quotes, fx, pboc, now_cst().isoformat(timespec="seconds"))
        assert_publishable(payload)
    except Exception as exc:  # noqa: BLE001 — keep a bad fetch from replacing the snapshot
        print(f"fetch failed: {exc}", file=sys.stderr)
        return 1

    write_snapshot(payload)
    summary = payload["summary"]
    fx_out = payload["fx"]
    print(
        f"wrote {OUT_PATH} quoted={summary['quoted']}/{summary['universe']} "
        f"median={summary['median_premium']} fx={fx_out['rate']} "
        f"pboc={fx_out.get('pboc_mid')} as_of={payload['as_of']}"
    )
    widest = sorted(payload["pairs"], key=lambda row: row["premium"])
    if widest:
        low, high = widest[0], widest[-1]
        print(f"  cheapest A {low['name']} {low['premium']:.2%}")
        print(f"  richest A {high['name']} {high['premium']:.2%}")
    if payload["skipped"]:
        print(f"skipped {len(payload['skipped'])}", file=sys.stderr)
        for item in payload["skipped"]:
            print(f"  {item['name']} {item['reason']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
