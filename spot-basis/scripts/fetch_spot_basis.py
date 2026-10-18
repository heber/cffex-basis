"""Build spot-basis/data/latest.json from 生意社现货 and Sina futures.

    python3 spot-basis/scripts/fetch_spot_basis.py
    python3 spot-basis/scripts/fetch_spot_basis.py --only rb,m

A product missing from the spot table is written as unavailable. The run
fails only when the spot page itself cannot be read or parsed. Futures
quotes are best-effort: a Sina outage still leaves the spot column in place.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO / "commodity" / "scripts"))

import fetch_basis as commodity_fetch
import ppi
import roll
import universe

DATA = ROOT / "data"
LATEST = DATA / "latest.json"
DEBUG = DATA / "debug.json"
CST = timezone(timedelta(hours=8))
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
SINA_UA = UA
PPI_URL = "https://www.100ppi.com/sf/"
MIN_MAPPED = 8


def now_cst() -> datetime:
    return datetime.now(CST)


def products_for(only: str) -> list[dict]:
    wanted = {item.strip() for item in only.split(",") if item.strip()}
    rows = []
    for code, name, exchange, sector, rule in universe.PRODUCTS:
        if wanted and code not in wanted:
            continue
        rows.append({
            "code": code,
            "name": name,
            "exchange": exchange,
            "exchange_name": universe.EXCHANGE_NAME[exchange],
            "sector": sector,
            "rule": rule,
        })
    return rows


def download_ppi() -> tuple[str, str]:
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(PPI_URL, headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Referer": "https://www.100ppi.com/",
            })
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                final = resp.geturl()
            text = raw.decode("utf-8", "replace")
            if 'id="fdata"' not in text or "现货" not in text:
                raise RuntimeError("page has no spot table")
            return text, final
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, RuntimeError) as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"生意社现期表不可用: {last}")


def fetch_quotes(symbols: list[str]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    if not symbols:
        return found

    def pull(batch: list[str]) -> None:
        url = "https://hq.sinajs.cn/list=" + ",".join(f"nf_{sym}" for sym in batch)

        def once():
            req = urllib.request.Request(url, headers={
                "Referer": "https://finance.sina.com.cn",
                "User-Agent": SINA_UA,
                "Accept": "*/*",
            })
            with urllib.request.urlopen(req, timeout=25) as resp:
                return resp.read().decode("gbk", "replace")

        raw = commodity_fetch.retry(once, tries=4)
        for line in raw.splitlines():
            if '="' not in line:
                continue
            left, right = line.split('="', 1)
            sym = left.split("_")[-1]
            body = right.strip()
            if body.endswith(";"):
                body = body[:-1]
            body = body.strip().strip('"')
            parsed = commodity_fetch.parse_hq_body(body)
            if not parsed:
                continue
            parts = body.split(",")
            parsed["clock"] = parts[1].strip() if len(parts) > 1 else ""
            found[sym] = parsed

    size = 24
    for i in range(0, len(symbols), size):
        batch = symbols[i:i + size]
        try:
            pull(batch)
        except Exception as exc:
            print(f"quote batch failed ({exc.__class__.__name__})", flush=True)
            if len(batch) == 1:
                continue
            mid = len(batch) // 2
            for part in (batch[:mid], batch[mid:]):
                try:
                    pull(part)
                except Exception:
                    continue
        time.sleep(0.15)
    return found


def fetch_legs(products: list[dict], today: date) -> dict[str, dict]:
    symbols: list[str] = []
    owner: dict[str, str] = {}
    for product in products:
        for sym in commodity_fetch.candidate_symbols(product, today):
            symbols.append(sym)
            owner[sym] = product["code"]
    quotes = fetch_quotes(symbols)
    grouped: dict[str, dict] = {product["code"]: {} for product in products}
    for sym, quote in quotes.items():
        code = owner.get(sym)
        if code:
            grouped[code][sym] = quote
    legs = {}
    for product in products:
        qmap = grouped[product["code"]]
        if not qmap:
            print(f"  {product['code']} no futures quotes", flush=True)
            continue
        session = max(quote["trade_date"] for quote in qmap.values())
        contracts = commodity_fetch.product_contracts_live(product, qmap, session)
        for contract in contracts:
            contract["dte"] = (contract["expiry"] - session).days
        contracts = [contract for contract in contracts if contract["expiry"] >= session]
        if not contracts:
            print(f"  {product['code']} quotes were not for {session.isoformat()}", flush=True)
            continue
        main = roll.pick_virtual_main(contracts)
        far = roll.pick_far_leg(contracts, main) if main else None
        near = roll.pick_spot_proxy(contracts, session)
        curve = roll.select_liquid_terms(contracts, main, far, spot=near) if main else []
        clocks = []
        for contract in contracts:
            quote = qmap.get(contract.get("sina"))
            if quote and quote.get("clock"):
                clocks.append(quote["clock"])
        legs[product["code"]] = {
            "futures_date": session.isoformat(),
            "clocks": clocks,
            "near": near,
            "main": main,
            "far": far,
            "curve": curve,
        }
    return legs


def warn_against_ppi(code: str, row: dict | None, leg: dict | None) -> None:
    if code == "FG" or not row or not leg or not leg.get("main"):
        return
    theirs = row.get("main_price")
    ours = leg["main"].get("price")
    if not theirs or not ours or theirs <= 0 or ours <= 0:
        return
    ratio = float(theirs) / float(ours)
    if ratio > 3 or ratio < 1 / 3:
        print(
            f"warning {code} 生意社主力价 {theirs} 与新浪 {ours} 相差超过 3 倍",
            flush=True,
        )


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            old = None
        if isinstance(old, dict):
            old_body = {key: value for key, value in old.items() if key != "generated_at"}
            new_body = {key: value for key, value in payload.items() if key != "generated_at"}
            if old_body == new_body and old.get("generated_at"):
                payload["generated_at"] = old["generated_at"]
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default="", help="comma-separated codes; writes data/debug.json")
    parser.add_argument("--html", default="", help="parse this file instead of downloading")
    args = parser.parse_args()
    if args.html:
        html = Path(args.html).read_text(encoding="utf-8")
        source_url = PPI_URL
    else:
        html, source_url = download_ppi()
    try:
        parsed = ppi.parse_ppi_html(html)
    except ValueError as exc:
        raise SystemExit(f"生意社现期表不可用: {exc}") from exc
    mapped = ppi.map_spots(parsed["rows"])
    print(f"spot table {parsed['published']} rows {len(parsed['rows'])} mapped {len(mapped)}", flush=True)
    if len(mapped) < MIN_MAPPED:
        raise SystemExit(
            f"生意社现期表只认出 {len(mapped)} 个跟踪品种，低于 {MIN_MAPPED}，视为数据源不可用"
        )
    products = products_for(args.only)
    try:
        legs = fetch_legs(products, date.today())
    except Exception as exc:
        print(f"futures source failed, keeping spot without a curve: {exc}", flush=True)
        legs = {}
    for product in products:
        row = mapped.get(product["code"])
        if row is None:
            print(f"  {product['code']} {product['name']} spot unavailable", flush=True)
        else:
            warn_against_ppi(product["code"], row, legs.get(product["code"]))
    payload = ppi.build_payload(
        products,
        parsed,
        legs,
        source_url=source_url,
        generated_at=now_cst().isoformat(timespec="seconds"),
    )
    path = DEBUG if args.only else LATEST
    save_json(path, payload)
    covered = payload["coverage"]
    print(
        f"wrote {path} spot {covered['with_spot']}/{covered['tracked']} "
        f"futures_date {payload['futures_date']} aligned {payload['dates_aligned']}",
        flush=True,
    )


if __name__ == "__main__":
    main()
