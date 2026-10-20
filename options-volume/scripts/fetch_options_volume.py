#!/usr/bin/env python3
"""Fetch a US options volume snapshot and write options-volume/data/latest.json.

Volume is the sum of Cboe delayed intraday minute bars
(calls_volume, puts_volume, total_options_volume) for the cash session.
IV30 and its change come from the matching delayed quote. Both endpoints
are unauthenticated JSON on cdn.cboe.com. No API key.

This is a free proxy for the Zen Research 「期权成交量趋势」table:

- Total / call / put volume is Cboe's published contract volume, not the
  OCC all-exchange daily file and not an IVolatility feed.
- IV30 is Cboe's 30-day implied volatility, not IVolatility IVX.
- The universe is a fixed watchlist (the names on the 2026-10-19 article),
  ranked by this session's volume. It is not a market-wide top 20.

Stdlib only.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import universe

ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = ROOT / "data" / "latest.json"

QUOTE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/quotes/{symbol}.json"
INTRADAY_URL = "https://cdn.cboe.com/api/global/delayed_quotes/charts/intraday/{symbol}.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; cffex-basis-options/1.0; "
        "+https://github.com/heber/cffex-basis)"
    ),
    "Accept": "application/json",
}

# Fifteen of the twenty names must quote. A holiday or a blocked CDN
# should fail the job instead of replacing the last good snapshot.
MIN_ROWS = 15

COVERAGE_NOTE = (
    "固定观察池内按当日期权成交量排序，不是全市场前20。"
    "Cboe most-active 只列出单个交易所的单个合约；"
    "OCC volume-query 无需密钥，但滞后一个以上交易日且必须指定代码。"
)


def round_or_none(value, digits: int):
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return round(number, digits)


def sum_option_volume(bars: list) -> dict:
    """Add per-minute option volume. Bars are increments, not a cumulative line."""
    calls = 0.0
    puts = 0.0
    total = 0.0
    first = None
    last = None
    for bar in bars:
        if not isinstance(bar, dict):
            continue
        vol = bar.get("volume") or {}
        call_v = float(vol.get("calls_volume") or 0)
        put_v = float(vol.get("puts_volume") or 0)
        if vol.get("total_options_volume") is None:
            total_v = call_v + put_v
        else:
            total_v = float(vol.get("total_options_volume") or 0)
        calls += call_v
        puts += put_v
        total += total_v
        stamp = bar.get("datetime")
        if stamp:
            if first is None:
                first = stamp
            last = stamp
    # The published minute total matches call+put on this feed. If a bar
    # disagrees, the call and put legs are what the percentages use.
    if int(round(total)) != int(round(calls + puts)):
        total = calls + puts
    return {
        "call_volume": int(round(calls)),
        "put_volume": int(round(puts)),
        "total_volume": int(round(total)),
        "bar_count": sum(1 for bar in bars if isinstance(bar, dict)),
        "session_start": first,
        "session_end": last,
    }


def percent_split(call_volume: int, put_volume: int):
    total = call_volume + put_volume
    if total <= 0:
        return None, None
    call_pct = round(100.0 * call_volume / total, 2)
    put_pct = round(100.0 - call_pct, 2)
    return call_pct, put_pct


def parse_quote(payload: dict) -> dict:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise ValueError("quote payload missing data")
    return {
        "iv30": round_or_none(data.get("iv30"), 4),
        "iv30_change": round_or_none(data.get("iv30_change"), 4),
        "iv30_change_percent": round_or_none(data.get("iv30_change_percent"), 4),
        "underlying_price": round_or_none(data.get("current_price"), 4),
        "quote_time": data.get("last_trade_time") or None,
    }


def parse_intraday(payload: dict) -> dict:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise ValueError("intraday payload missing bars")
    return sum_option_volume(data)


def build_row(spec: dict, quote: dict, volume: dict) -> dict:
    call_pct, put_pct = percent_split(volume["call_volume"], volume["put_volume"])
    row = {
        "symbol": spec["symbol"],
        "name": spec["name"],
        "name_en": spec["name_en"],
        "kind": spec["kind"],
        "cboe_symbol": spec["cboe"],
        "total_volume": volume["total_volume"],
        "call_volume": volume["call_volume"],
        "put_volume": volume["put_volume"],
        "call_pct": call_pct,
        "put_pct": put_pct,
        "iv30": quote.get("iv30"),
        "iv30_change": quote.get("iv30_change"),
        "iv30_change_percent": quote.get("iv30_change_percent"),
        "underlying_price": quote.get("underlying_price"),
        "quote_time": quote.get("quote_time"),
        "session_start": volume.get("session_start"),
        "session_end": volume.get("session_end"),
        "bar_count": volume.get("bar_count"),
    }
    return row


def session_date(row: dict) -> str | None:
    end = row.get("session_end") or ""
    if len(end) >= 10 and end[4] == "-" and end[7] == "-":
        return end[:10]
    return None


def rank_rows(rows: list[dict]) -> list[dict]:
    ordered = sorted(rows, key=lambda row: (-int(row.get("total_volume") or 0), row["symbol"]))
    for index, row in enumerate(ordered, start=1):
        row["rank"] = index
    return ordered


def select_session(rows: list[dict]) -> tuple[str | None, list[dict], list[dict]]:
    """Keep the modal session date. Other dates are returned as stale."""
    dates = [session_date(row) for row in rows]
    counted = Counter(date for date in dates if date)
    if not counted:
        return None, [], rows
    chosen = counted.most_common(1)[0][0]
    kept = []
    stale = []
    for row in rows:
        if session_date(row) == chosen and (row.get("total_volume") or 0) > 0:
            kept.append(row)
        else:
            stale.append({
                "symbol": row["symbol"],
                "session_date": session_date(row),
                "total_volume": row.get("total_volume"),
                "reason": "session date differs or volume is zero",
            })
    return chosen, rank_rows(kept), stale


def build_payload(rows: list[dict], missing: list[dict]) -> dict:
    session, kept, stale = select_session(rows)
    ends = [row["session_end"] for row in kept if row.get("session_end")]
    starts = [row["session_start"] for row in kept if row.get("session_start")]
    return {
        "timestamp": max(ends) if ends else None,
        "session_date": session,
        "session_start": min(starts) if starts else None,
        "session_end": max(ends) if ends else None,
        "timezone": "America/New_York",
        "source": {
            "volume": INTRADAY_URL,
            "iv30": QUOTE_URL,
            "vendor": "Cboe delayed quotes",
            "iv_series": "Cboe iv30 (30-day implied volatility), not IVolatility IVX",
        },
        "coverage": "watchlist",
        "coverage_note": COVERAGE_NOTE,
        "watchlist_size": len(universe.WATCHLIST),
        "quoted": len(kept),
        "missing": missing,
        "stale": stale,
        "rows": kept,
    }


def assert_publishable(payload: dict, minimum: int = MIN_ROWS) -> None:
    rows = payload.get("rows") or []
    if not payload.get("session_date"):
        raise RuntimeError("options snapshot has no session_date")
    if len(rows) < minimum:
        symbols = ", ".join(item.get("symbol", "?") for item in (payload.get("missing") or []))
        raise RuntimeError(
            f"only {len(rows)} option rows, need {minimum}. missing: {symbols}"
        )


def http_get_json(url: str, timeout: int = 40) -> dict:
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise RuntimeError("expected JSON object")
            return payload
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404):
                raise
            last = exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
            last = exc
        time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed: {last}")


def fetch_spec(spec: dict) -> dict:
    cboe = spec["cboe"]
    quote = parse_quote(http_get_json(QUOTE_URL.format(symbol=cboe)))
    time.sleep(0.5)
    volume = parse_intraday(http_get_json(INTRADAY_URL.format(symbol=cboe)))
    time.sleep(0.5)
    return build_row(spec, quote, volume)


def fetch_all(specs: list[dict] | None = None) -> dict:
    rows = []
    missing = []
    for spec in specs or universe.WATCHLIST:
        try:
            row = fetch_spec(spec)
        except (urllib.error.HTTPError, RuntimeError, ValueError, TimeoutError) as exc:
            print(f"miss {spec['symbol']}: {exc}", file=sys.stderr)
            missing.append({"symbol": spec["symbol"], "reason": str(exc)})
            time.sleep(0.5)
            continue
        if (row.get("total_volume") or 0) <= 0:
            missing.append({"symbol": spec["symbol"], "reason": "no option volume"})
            continue
        if row.get("iv30") is None:
            missing.append({"symbol": spec["symbol"], "reason": "no iv30"})
            continue
        rows.append(row)
        print(
            f"{spec['symbol']} vol={row['total_volume']} "
            f"call={row['call_pct']} iv30={row['iv30']} chg={row['iv30_change']}",
            file=sys.stderr,
        )
    return build_payload(rows, missing)


def write_payload(payload: dict, path: Path = OUT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    payload = fetch_all()
    assert_publishable(payload)
    write_payload(payload)
    print(
        f"wrote {OUT_PATH} session={payload['session_date']} rows={payload['quoted']}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"options volume fetch failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
