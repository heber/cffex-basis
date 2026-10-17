"""Fetch Sina quotes and build the virtual-main basis snapshot.

Stdlib only. Live curve comes from hq.sinajs.cn. History comes from
InnerFuturesNewService.getDailyKLine (settlement, volume, open interest).

    python3 scripts/fetch_basis.py --rebuild-history
    python3 scripts/fetch_basis.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import roll
import specs
import universe

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
HIST_PATH = DATA / "history" / "series.json"
LATEST_PATH = DATA / "latest.json"
CACHE = DATA / "cache"

CST = timezone(timedelta(hours=8))
SCHEMA = ["d", "main", "px", "far", "far_px", "ann", "raw", "gap", "dte", "oi", "vol", "out", "spot", "spot_px"]
UA = "Mozilla/5.0 (compatible; commodity-basis/1.0; +https://github.com/heber/cffex-basis)"


def now_cst() -> datetime:
    return datetime.now(CST)


def http_get(url: str, encoding: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={
        "Referer": "https://finance.sina.com.cn",
        "User-Agent": UA,
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return raw.decode(encoding, "replace")


def retry(fn, tries: int = 5):
    last = None
    for attempt in range(tries):
        try:
            return fn()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
            last = exc
            time.sleep(1.2 * (attempt + 1))
    raise last


def parse_kline(raw: str) -> list[dict]:
    if "__ERROR" in raw and "(" not in raw:
        raise RuntimeError("sina error")
    i = raw.find("(")
    j = raw.rfind(")")
    if i < 0 or j <= i:
        raise RuntimeError("kline wrapper missing")
    payload = raw[i + 1:j].strip()
    if payload in ("null", "None", ""):
        return []
    data = json.loads(payload)
    if not isinstance(data, list):
        raise RuntimeError("kline not a list")
    bars = []
    for row in data:
        try:
            settle = float(row.get("s") or 0)
            close = float(row.get("c") or 0)
        except (TypeError, ValueError):
            continue
        px = settle if settle > 0 else close
        if px <= 0 or not row.get("d"):
            continue
        bars.append({
            "d": row["d"],
            "px": px,
            "v": float(row.get("v") or 0),
            "oi": float(row.get("p") or 0),
        })
    return bars


def fetch_kline(symbol: str) -> list[dict]:
    url = (
        "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
        f"var%20_{symbol}=/InnerFuturesNewService.getDailyKLine?symbol={symbol}"
    )

    def once():
        raw = http_get(url, "utf-8")
        if "__ERROR" in raw and "null" not in raw.split("=", 1)[-1]:
            raise RuntimeError(raw[:180])
        return parse_kline(raw)

    return retry(once)


def load_cached_kline(symbol: str) -> list[dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{symbol}.json"
    fresh_cutoff = (date.today() - timedelta(days=25)).isoformat()
    if path.exists():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cached = None
        if isinstance(cached, list):
            if not cached or cached[-1].get("d", "9999") < fresh_cutoff:
                return cached
    bars = fetch_kline(symbol)
    path.write_text(json.dumps(bars, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return bars


def parse_hq_body(body: str) -> dict | None:
    parts = body.split(",")
    if len(parts) < 18 or not parts[0]:
        return None
    try:
        last = float(parts[8] or 0)
        prev = float(parts[10] or 0)
        oi = float(parts[13] or 0)
        vol = float(parts[14] or 0)
    except ValueError:
        return None
    price = last if last > 0 else prev
    if price <= 0:
        return None
    qdate = parts[17].strip()
    try:
        d = date.fromisoformat(qdate)
    except ValueError:
        return None
    trade_date = roll.session_trade_date(d, parts[1])
    return {"price": price, "oi": oi, "volume": vol, "quote_date": d, "trade_date": trade_date, "name": parts[0]}


def fetch_quotes(symbols: list[str]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    if not symbols:
        return found

    def pull(batch: list[str]) -> None:
        url = "https://hq.sinajs.cn/list=" + ",".join(f"nf_{s}" for s in batch)

        def once():
            return http_get(url, "gbk", timeout=25)

        raw = retry(once, tries=4)
        for line in raw.splitlines():
            if '="' not in line:
                continue
            left, right = line.split('="', 1)
            sym = left.split("_")[-1]
            body = right.strip()
            if body.endswith(";"):
                body = body[:-1]
            body = body.strip().strip('"')
            parsed = parse_hq_body(body)
            if parsed:
                found[sym] = parsed

    # Sina rejects very long lists; keep batches modest.
    size = 24
    for i in range(0, len(symbols), size):
        batch = symbols[i:i + size]
        try:
            pull(batch)
        except Exception:
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


def product_contracts_live(product: dict, quotes: dict[str, dict], session: date) -> list[dict]:
    contracts = []
    for sym, q in quotes.items():
        if q["trade_date"] != session:
            continue
        parsed = roll.parse_yymm(sym)
        if not parsed:
            continue
        _root, year, month = parsed
        exp = roll.expiry_for(year, month, product["rule"])
        contracts.append({
            "display": roll.display_symbol(product["code"], year, month, product["exchange"]),
            "sina": sym,
            "year": year,
            "month": month,
            "expiry": exp,
            "price": q["price"],
            "oi": q["oi"],
            "volume": q["volume"],
        })
    return contracts


def candidate_symbols(product: dict, today: date) -> list[str]:
    start = roll.add_months(today.year, today.month, -3)
    end = roll.add_months(today.year, today.month, universe.HISTORY_END_MONTHS_AHEAD)
    return [roll.sina_symbol(product["code"], y, m) for y, m in roll.month_iter(start, end)]


def history_symbols(product: dict, today: date) -> list[str]:
    end = roll.add_months(today.year, today.month, universe.HISTORY_END_MONTHS_AHEAD)
    return [roll.sina_symbol(product["code"], y, m) for y, m in roll.month_iter(universe.HISTORY_START, end)]


def round_or_none(value, ndigits):
    if value is None:
        return None
    return round(float(value), ndigits)


def row_to_list(row: dict) -> list:
    return [
        row["date"].isoformat() if isinstance(row["date"], date) else row["date"],
        row["contract"],
        round(float(row["px"]), 4),
        row.get("far"),
        round_or_none(row.get("far_px"), 4),
        round_or_none(row.get("ann"), 6),
        round_or_none(row.get("raw"), 4),
        row.get("gap"),
        row.get("dte"),
        int(round(row.get("oi") or 0)),
        int(round(row.get("volume") or 0)),
        round_or_none(row.get("out"), 4),
        row.get("spot"),
        round_or_none(row.get("spot_px"), 4),
    ]


def list_to_row(item: list) -> dict:
    data = dict(zip(SCHEMA, item))
    data["date"] = date.fromisoformat(data["d"])
    data["contract"] = data["main"]
    data["px"] = data["px"]
    data["ann"] = data["ann"]
    data["out"] = data["out"]
    return data


def build_panel(product: dict, today: date) -> dict[date, list[dict]]:
    symbols = history_symbols(product, today)
    bars_by_sym: dict[str, list[dict]] = {}

    def job(sym: str):
        return sym, load_cached_kline(sym)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(job, sym) for sym in symbols]
        done = 0
        for fut in as_completed(futures):
            done += 1
            try:
                sym, bars = fut.result()
            except Exception as exc:
                print(f"  kline fail {exc.__class__.__name__}: {exc}", flush=True)
                continue
            if bars:
                bars_by_sym[sym] = bars
            if done % 20 == 0 or done == len(symbols):
                print(f"  {product['code']} klines {done}/{len(symbols)} with data {len(bars_by_sym)}", flush=True)

    panel: dict[date, list[dict]] = {}
    for sym, bars in bars_by_sym.items():
        parsed = roll.parse_yymm(sym)
        if not parsed:
            continue
        _root, year, month = parsed
        exp = roll.expiry_for(year, month, product["rule"])
        display = roll.display_symbol(product["code"], year, month, product["exchange"])
        for bar in bars:
            d = date.fromisoformat(bar["d"])
            panel.setdefault(d, []).append({
                "display": display,
                "year": year,
                "month": month,
                "expiry": exp,
                "price": bar["px"],
                "oi": bar["oi"],
                "volume": bar["v"],
            })
    return panel


def spark_from_rows(rows: list[dict], asof: date) -> list[float | None]:
    start = roll.window_start(asof)
    series = []
    for row in rows:
        d = row["date"]
        if start <= d < asof:
            ann = row.get("ann")
            series.append(None if ann is None else round(float(ann), 4))
    return roll.downsample(series, 90)


def term_payload(contract: dict, ref: dict, role: str, is_spot: bool) -> dict:
    """Curve spread versus the spot proxy (or the main, if no proxy exists)."""
    raw = ann = gap = None
    if contract["display"] != ref["display"]:
        if contract["expiry"] <= ref["expiry"]:
            raw, ann, gap = roll.spread_vs(contract, ref)
        else:
            raw, ann, gap = roll.spread_vs(ref, contract)
    return {
        "symbol": contract["display"],
        "expiry": contract["expiry"].isoformat(),
        "price": round(float(contract["price"]), 4),
        "oi": int(round(contract["oi"])),
        "volume": int(round(contract["volume"])),
        "score": int(round((contract["oi"] or 0) + (contract["volume"] or 0))),
        "dte": None,
        "role": role,
        "is_spot": is_spot,
        "raw": round_or_none(raw, 4),
        "ann": round_or_none(ann, 6),
        "gap": gap,
    }


def live_product(product: dict, today: date, history_rows: list[dict]) -> dict:
    symbols = candidate_symbols(product, today)
    quotes = fetch_quotes(symbols)
    session_dates = [q["trade_date"] for q in quotes.values()]
    session = max(session_dates) if session_dates else today
    contracts = product_contracts_live(product, quotes, session)
    # Stamp dte against the session date, not the wall clock.
    for c in contracts:
        c["dte"] = (c["expiry"] - session).days
    live_row = roll.build_day(contracts, session) if contracts else None
    if live_row is not None:
        # Outgoing price if leadership changed versus the previous stored day.
        prev = None
        for row in history_rows:
            if row["date"] < session:
                prev = row
        if prev and prev["contract"] != live_row["contract"]:
            for c in contracts:
                if c["display"] == prev["contract"]:
                    live_row["out"] = float(c["price"])
                    break
    scored = roll.score_snapshot(history_rows, live_row, session) if live_row else {
        "basis_pct": None, "basis_n": 0, "price_pct": None, "price_n": 0,
    }
    main = roll.pick_virtual_main(contracts) if contracts else None
    far = roll.pick_far_leg(contracts, main) if main else None
    spot = roll.pick_spot_proxy(contracts, session) if contracts else None
    terms = []
    if main is not None:
        ref = spot if spot is not None else main
        for c in roll.select_liquid_terms(contracts, main, far, spot=spot):
            is_spot = spot is not None and c["display"] == spot["display"]
            role = "other"
            if c["display"] == main["display"]:
                role = "virtual"
            elif far is not None and c["display"] == far["display"]:
                role = "far"
            elif is_spot:
                role = "spot"
            item = term_payload(c, ref, role, is_spot)
            item["dte"] = (c["expiry"] - session).days
            terms.append(item)
    meta_start = history_rows[0]["date"].isoformat() if history_rows else None
    meta_end = history_rows[-1]["date"].isoformat() if history_rows else None
    snap = {
        "code": product["code"],
        "name": product["name"],
        "exchange": product["exchange"],
        "exchange_name": universe.EXCHANGE_NAME[product["exchange"]],
        "sector": product["sector"],
        "expiry_rule": product["rule"],
        "as_of": session.isoformat() if contracts else None,
        "virtual": None if main is None else {
            "symbol": main["display"],
            "price": round(float(main["price"]), 4),
            "oi": int(round(main["oi"])),
            "volume": int(round(main["volume"])),
            "score": int(round(main["oi"] + main["volume"])),
            "dte": (main["expiry"] - session).days,
            "expiry": main["expiry"].isoformat(),
        },
        "spot_proxy": None if spot is None else {
            "symbol": spot["display"],
            "price": round(float(spot["price"]), 4),
            "oi": int(round(spot["oi"])),
            "volume": int(round(spot["volume"])),
            "score": int(round((spot["oi"] or 0) + (spot["volume"] or 0))),
            "dte": (spot["expiry"] - session).days,
            "expiry": spot["expiry"].isoformat(),
        },
        "far": None if far is None or live_row is None else {
            "symbol": far["display"],
            "price": round(float(far["price"]), 4),
            "oi": int(round(far["oi"])),
            "volume": int(round(far["volume"])),
            "dte": (far["expiry"] - session).days,
            "expiry": far["expiry"].isoformat(),
            "raw": round_or_none(live_row.get("raw"), 4),
            "ann": round_or_none(live_row.get("ann"), 6),
            "gap": live_row.get("gap"),
        },
        "basis_pct": scored["basis_pct"],
        "basis_n": scored["basis_n"],
        "price_pct": scored["price_pct"],
        "price_n": scored["price_n"],
        "history_start": meta_start,
        "history_end": meta_end,
        "spark": spark_from_rows(history_rows, session) if history_rows else [],
        "terms": terms,
        "error": None if contracts else "无活跃合约报价",
    }
    return specs.apply(snap)


def selected_products(only: str) -> list[dict]:
    wanted = {p.strip() for p in only.split(",") if p.strip()}
    products = []
    for code, name, exchange, sector, rule in universe.PRODUCTS:
        if wanted and code not in wanted:
            continue
        products.append({
            "code": code,
            "name": name,
            "exchange": exchange,
            "sector": sector,
            "rule": rule,
        })
    return products


def load_history() -> dict:
    if not HIST_PATH.exists():
        return {"schema": SCHEMA, "products": {}}
    doc = json.loads(HIST_PATH.read_text(encoding="utf-8"))
    doc.setdefault("products", {})
    return doc


def save_json(path: Path, payload: dict, pretty: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if pretty:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text + "\n", encoding="utf-8")
    tmp.replace(path)


def rebuild_product(product: dict, today: date) -> list[dict]:
    print(f"rebuild {product['code']} {product['name']}", flush=True)
    panel = build_panel(product, today)
    min_date = date(*universe.SERIES_MIN_DATE)
    rows = roll.assemble_history(panel, min_date)
    print(f"  {product['code']} sessions {len(rows)}", flush=True)
    return rows


def upsert_row(existing: list[list], row: dict) -> list[list]:
    item = row_to_list(row)
    if not existing:
        return [item]
    if existing[-1][0] == item[0]:
        existing[-1] = item
        return existing
    if existing[-1][0] < item[0]:
        existing.append(item)
        return existing
    # Replace an interior duplicate or insert in order. Rare (backfill).
    dates = [r[0] for r in existing]
    if item[0] in dates:
        existing[dates.index(item[0])] = item
        return existing
    existing.append(item)
    existing.sort(key=lambda r: r[0])
    return existing


def main() -> int:
    parser = argparse.ArgumentParser(description="Build commodity virtual-main basis snapshot")
    parser.add_argument("--rebuild-history", action="store_true", help="reload Sina daily bars and splice history")
    parser.add_argument("--only", default="", help="comma-separated product codes, e.g. m,rb")
    args = parser.parse_args()

    today = now_cst().date()
    products = selected_products(args.only)
    if not products:
        print("no products selected", file=sys.stderr)
        return 1

    history = load_history()
    if args.rebuild_history and not args.only:
        history = {"schema": SCHEMA, "products": {}}
    elif args.rebuild_history and args.only:
        for product in products:
            history["products"].pop(product["code"], None)

    if args.rebuild_history or not history["products"]:
        for product in products:
            try:
                rows = rebuild_product(product, today)
            except Exception:
                traceback.print_exc()
                rows = []
            history["products"][product["code"]] = [row_to_list(r) for r in rows]
        history["schema"] = SCHEMA
        history["built_at"] = now_cst().isoformat(timespec="seconds")
        history["price"] = "settlement if >0 else close"
        if not args.only:
            save_json(HIST_PATH, history, pretty=False)
            print(f"wrote {HIST_PATH}", flush=True)

    snapshots = []
    for product in products:
        print(f"snapshot {product['code']}", flush=True)
        stored = [list_to_row(item) for item in history["products"].get(product["code"], [])]
        try:
            snap = live_product(product, today, stored)
        except Exception as exc:
            traceback.print_exc()
            snap = {
                "code": product["code"],
                "name": product["name"],
                "exchange": product["exchange"],
                "exchange_name": universe.EXCHANGE_NAME[product["exchange"]],
                "sector": product["sector"],
                "expiry_rule": product["rule"],
                "as_of": None,
                "virtual": None,
                "spot_proxy": None,
                "far": None,
                "basis_pct": None,
                "basis_n": 0,
                "price_pct": None,
                "price_n": 0,
                "history_start": None,
                "history_end": None,
                "spark": [],
                "terms": [],
                "error": str(exc),
            }
            snap = specs.apply(snap)
        snapshots.append(snap)
        # Append the live session into history so the next run keeps the splice.
        if snap.get("virtual") and snap.get("as_of"):
            session = date.fromisoformat(snap["as_of"])
            # Reconstruct a history row from the snapshot so incremental runs
            # record the same virtual main the page shows.
            far = snap.get("far") or {}
            virtual = snap["virtual"]
            spot = snap.get("spot_proxy") or {}
            prev = None
            for row in stored:
                if row["date"] < session:
                    prev = row
            out = None
            if prev and prev["contract"] != virtual["symbol"]:
                for term in snap["terms"]:
                    if term["symbol"] == prev["contract"]:
                        out = term["price"]
                        break
            live_row = {
                "date": session,
                "contract": virtual["symbol"],
                "px": virtual["price"],
                "far": far.get("symbol"),
                "far_px": far.get("price"),
                "spot": spot.get("symbol"),
                "spot_px": spot.get("price"),
                "ann": far.get("ann"),
                "raw": far.get("raw"),
                "gap": far.get("gap"),
                "dte": virtual.get("dte"),
                "oi": virtual.get("oi") or 0,
                "volume": virtual.get("volume") or 0,
                "out": out,
            }
            history["products"][product["code"]] = upsert_row(
                history["products"].get(product["code"], []), live_row
            )
        ann = (snap.get("far") or {}).get("ann")
        ann_txt = "—" if ann is None else f"{ann * 100:.1f}%"
        virt = (snap.get("virtual") or {}).get("symbol", "—")
        spot_name = (snap.get("spot_proxy") or {}).get("symbol", "—")
        money = snap.get("notional")
        margin = snap.get("margin_min")
        money_txt = "—" if money is None else f"{money:.0f}"
        margin_txt = "—" if margin is None else f"{margin:.0f}"
        print(
            f"  {product['code']} spot {spot_name} virtual {virt} ann {ann_txt} "
            f"basis {snap.get('basis_pct')} price {snap.get('price_pct')} "
            f"notional {money_txt} margin {margin_txt}",
            flush=True,
        )

    if not args.only:
        history["updated_at"] = now_cst().isoformat(timespec="seconds")
        save_json(HIST_PATH, history, pretty=False)
        as_ofs = [s["as_of"] for s in snapshots if s.get("as_of")]
        latest = {
            "generated_at": now_cst().isoformat(timespec="seconds"),
            "as_of": max(as_ofs) if as_ofs else None,
            "methodology": {
                "virtual_main": "单一连续序列：每个交易日得分=持仓量+成交量最高的合约。领先换手则换月拼接，不另设交易所主力。价格分位用这条序列，不把主力改叫现货。",
                "tie_break": "得分相同则持仓量更高者优先，再比较成交量，再取更近到期。",
                "spot_proxy": "现货 = 当天未到期合约里到期最早的一张（近月代理），不是港口价或现金现货。并列时先比持仓+成交，再比持仓，再比成交。",
                "far_leg": "次活跃远月 = 到期晚于主力的合约里得分最高者，不一定是相邻月。年化贴水的远腿固定用这一张。",
                "basis": "(现货代理价-次活跃远月价)/现货代理价×365/(次活跃远月到期日-现货代理到期日)。正数=远月更便宜=贴水。远月不晚于现货代理则为空。",
                "min_gap_days": roll.MIN_GAP_DAYS,
                "basis_percentile": "近3年年化贴水≤当前值的交易日占比。越高=贴水越深。不含行情日当天。样本<30为null。历史按现货代理对次活跃远月重算。",
                "price_percentile": "比率拼接后的主力价格≤当前可交易价的占比。越高=越接近近3年高位。末日价格不复权。",
                "window_years": roll.WINDOW_YEARS,
                "min_samples": roll.MIN_SAMPLES,
                "spot": "页面现货是近月未到期合约代理。不使用港口价、品牌价或厂库现金价。",
                "history_price": "日K结算价（结算为0时用收盘）。快照曲线用最新价。",
                "expiry": "未到期合约：上期所金属与黑色为合约月15日遇周末顺延；燃料油/原油/低硫燃料油为交割月前最后工作日；其余为大商所/郑商所/广期所风格的合约月第10个工作日。未剔除法定节假日。",
                **specs.methodology_fields(),
            },
            "products": snapshots,
        }
        save_json(LATEST_PATH, latest, pretty=True)
        print(f"wrote {LATEST_PATH}", flush=True)
    else:
        debug = DATA / "debug.json"
        save_json(debug, {"generated_at": now_cst().isoformat(timespec="seconds"), "products": snapshots}, pretty=True)
        print(f"wrote {debug} (partial, not the site snapshot)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
