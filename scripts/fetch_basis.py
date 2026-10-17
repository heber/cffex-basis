#!/usr/bin/env python3
"""Fetch CFFEX equity-index futures basis and write data/latest.json.

Methodology (分红矫正年化贴水), same as the reference server:

    原始基差 = 现货 − 期货          （正 = 贴水）
    矫正基差 = 原始基差 − 期内分红点数
    年化     = 矫正基差 / 现货 × 365 / 剩余自然日

期内分红点数 = 现货 × 年化股息率 × 剩余期限内的 A 股季节性权重
（5–7 月高峰，按日比例摊入）。

Spot:  sh000016/IH, sh000300/IF, sh000905/IC, sh000852/IM
Futures: CFF_RE_{CODE} on hq.sinajs.cn (Referer: finance.sina.com.cn)

Historical percentiles (近3年, same calendar lookback):

    贴水分位     = 样本中 corr_ann_365 ≤ 当前值的占比（0–100%）
    样本         = 该品种该期限、行情日往前 3 个日历年的交易日，不含行情日当天
    分位越高     = 当前越贴水（相对该期限自身历史更便宜）

    指数点位分位 = 样本中指数收盘 ≤ 当日现货的占比（0–100%）
    样本         = 同一日历窗口内、各期限按交易日去重后的指数收盘
    分位越高     = 指数越接近这段窗口的历史高位（不是贴水）

Daily runs append the session close into data/history/series.json.
`--rebuild-history` refetches Sina daily bars (futures + index) and reseeds
the store from the first available contract (about 2018-10; IM from 2022-07).

An empty far-quarter quote (Sina `var hq_str_CFF_RE_IH2706="";` before that
contract is listed) is kept on the 下季 leg with `quote_missing` and does not
fail the run. History is appended only for contracts that actually quoted.
Missing spot or 当月/次月/当季 still exits non-zero and does not write.

Stdlib only. Exits non-zero on failure and does not overwrite a good snapshot.
"""

from __future__ import annotations

import calendar
import json
import math
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = ROOT / "data" / "latest.json"

PRODUCTS = {
    "IH": {
        "name": "上证50",
        "spot_code": "sh000016",
        "multiplier": 300,
        "default_yield": 0.023,
    },
    "IF": {
        "name": "沪深300",
        "spot_code": "sh000300",
        "multiplier": 300,
        "default_yield": 0.021,
    },
    "IC": {
        "name": "中证500",
        "spot_code": "sh000905",
        "multiplier": 200,
        "default_yield": 0.0105,
    },
    "IM": {
        "name": "中证1000",
        "spot_code": "sh000852",
        "multiplier": 200,
        "default_yield": 0.0092,
    },
}

_RAW_SEASONALITY = {
    1: 0.02,
    2: 0.02,
    3: 0.05,
    4: 0.08,
    5: 0.22,
    6: 0.25,
    7: 0.18,
    8: 0.08,
    9: 0.04,
    10: 0.02,
    11: 0.02,
    12: 0.02,
}
_SEASON_SUM = sum(_RAW_SEASONALITY.values())
SEASONALITY = {m: w / _SEASON_SUM for m, w in _RAW_SEASONALITY.items()}

QUARTER_MONTHS = {3, 6, 9, 12}
# Spot plus these three legs are required. 下季 may be unlisted on Sina.
REQUIRED_LABELS = ("当月", "次月", "当季")
OPTIONAL_LABEL = "下季"

SINA_HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


def third_friday(year: int, month: int) -> date:
    """CFFEX equity index futures expiry = 3rd Friday of the contract month."""
    cal = calendar.Calendar(firstweekday=calendar.MONDAY)
    fridays = [
        d
        for d in cal.itermonthdates(year, month)
        if d.month == month and d.weekday() == calendar.FRIDAY
    ]
    return fridays[2]


def contract_code(prefix: str, year: int, month: int) -> str:
    return f"{prefix}{year % 100:02d}{month:02d}"


def active_contracts(prefix: str, today: Optional[date] = None) -> List[Dict[str, Any]]:
    """Return 4 active contracts: 当月、次月、当季、下季."""
    today = today or date.today()
    candidates: List[Tuple[int, int, date]] = []
    y, m = today.year, today.month
    for _ in range(18):
        exp = third_friday(y, m)
        if exp >= today:
            candidates.append((y, m, exp))
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1

    if len(candidates) < 2:
        raise RuntimeError("Not enough active contract months")

    result: List[Dict[str, Any]] = []
    for i, label in enumerate(["当月", "次月"]):
        y, m, exp = candidates[i]
        result.append(
            {
                "label": label,
                "code": contract_code(prefix, y, m),
                "year": y,
                "month": m,
                "expiry": exp.isoformat(),
                "days_to_expiry": (exp - today).days,
            }
        )

    remaining = candidates[2:]
    quarters = [(y, m, exp) for y, m, exp in remaining if m in QUARTER_MONTHS]
    for i, label in enumerate(["当季", "下季"]):
        if i >= len(quarters):
            break
        y, m, exp = quarters[i]
        result.append(
            {
                "label": label,
                "code": contract_code(prefix, y, m),
                "year": y,
                "month": m,
                "expiry": exp.isoformat(),
                "days_to_expiry": (exp - today).days,
            }
        )
    return result


def remaining_div_points(
    spot: float,
    annual_yield: float,
    today: date,
    expiry: date,
) -> float:
    """Day-fraction prorate of seasonal dividend points over (today, expiry]."""
    if expiry <= today or spot <= 0 or annual_yield <= 0:
        return 0.0
    annual_pts = spot * annual_yield
    total_weight = 0.0
    y, m = today.year, today.month
    while True:
        days_in_month = calendar.monthrange(y, m)[1]
        month_start = date(y, m, 1)
        month_end = date(y, m, days_in_month)
        range_start = max(today + timedelta(days=1), month_start)
        range_end = min(expiry, month_end)
        if range_start <= range_end:
            n_days = (range_end - range_start).days + 1
            day_frac = n_days / days_in_month
            total_weight += SEASONALITY[m] * day_frac
        if month_end >= expiry:
            break
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
    return annual_pts * total_weight


def fmt_market_value(yuan: Optional[float]) -> str:
    if yuan is None or (isinstance(yuan, float) and math.isnan(yuan)):
        return "—"
    abs_y = abs(yuan)
    if abs_y >= 1e8:
        return f"{yuan / 1e8:.2f}亿元"
    if abs_y >= 1e4:
        return f"{yuan / 1e4:.2f}万元"
    return f"{yuan:.0f}元"


def parse_sina_quotes(raw: str) -> Dict[str, List[str]]:
    """Split an hq.sinajs.cn body into symbol -> fields.

    A symbol Sina has not listed yet still comes back as a line, with an empty
    quoted payload: `var hq_str_CFF_RE_IH2706="";`. That is [] here, not a
    transport failure. A body with no `="` at all is rejected by fetch_sina.
    """
    out: Dict[str, List[str]] = {}
    for line in raw.strip().split("\n"):
        line = line.strip()
        if not line or '="' not in line:
            continue
        left, right = line.split("=", 1)
        key = left.replace("var hq_str_", "").strip()
        payload = right.strip()
        if payload.startswith('"') and payload.endswith('";'):
            payload = payload[1:-2]
        elif payload.startswith('"') and payload.endswith('"'):
            payload = payload[1:-1]
        if not payload:
            out[key] = []
            continue
        out[key] = payload.split(",")
    return out


def fetch_sina(symbols: List[str]) -> Dict[str, List[str]]:
    if not symbols:
        return {}
    url = "https://hq.sinajs.cn/list=" + ",".join(symbols)
    last_err: Optional[BaseException] = None
    raw = ""
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=SINA_HEADERS)
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("gbk", errors="replace")
            if '="' in raw:
                break
            last_err = RuntimeError("empty Sina payload")
        except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
            last_err = exc
        time.sleep(1.5 * (attempt + 1))
    else:
        raise RuntimeError(f"Sina fetch failed: {last_err}")

    return parse_sina_quotes(raw)


def parse_spot(fields: List[str]) -> Optional[float]:
    if not fields or len(fields) < 4:
        return None
    try:
        value = float(fields[3])
        return value if value > 0 else None
    except ValueError:
        return None


def parse_futures(fields: List[str]) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "last": None,
        "open": None,
        "high": None,
        "low": None,
        "volume": None,
        "oi": None,
        "prev_oi": None,
        "quote_date": None,
        "quote_time": None,
        "name": None,
    }
    if not fields or len(fields) < 4:
        return result
    try:
        result["open"] = float(fields[0]) if fields[0] else None
        result["high"] = float(fields[1]) if fields[1] else None
        result["low"] = float(fields[2]) if fields[2] else None
        result["last"] = float(fields[3]) if fields[3] else None
        if len(fields) > 4 and fields[4]:
            result["volume"] = int(float(fields[4]))
        if len(fields) > 6 and fields[6]:
            result["oi"] = int(float(fields[6]))
        if len(fields) > 15 and fields[15]:
            result["prev_oi"] = int(float(fields[15]))
        if len(fields) > 36:
            result["quote_date"] = fields[36] or None
        if len(fields) > 37:
            result["quote_time"] = fields[37] or None
        if fields:
            result["name"] = fields[-1] or None
    except (ValueError, IndexError):
        pass
    return result


def calc_basis_row(
    spot: float,
    fut_last: float,
    multiplier: int,
    oi: Optional[int],
    days: int,
    rem_div: float,
) -> Dict[str, Any]:
    contract_mv = fut_last * multiplier
    oi_notional = (oi * fut_last * multiplier) if oi is not None else None
    raw_basis = spot - fut_last
    corrected_basis = raw_basis - rem_div

    def ann365(basis: float) -> Optional[float]:
        if days <= 0 or spot <= 0:
            return None
        return (basis / spot) * (365 / days)

    raw_ann_365 = ann365(raw_basis)
    corr_ann_365 = ann365(corrected_basis)

    return {
        "last": round(fut_last, 2),
        "contract_mv": contract_mv,
        "contract_mv_fmt": fmt_market_value(contract_mv),
        "oi": oi,
        "oi_notional": oi_notional,
        "oi_notional_fmt": fmt_market_value(oi_notional) if oi_notional is not None else None,
        "spot": round(spot, 4),
        "raw_basis": round(raw_basis, 4),
        "remaining_div_points": round(rem_div, 4),
        "corrected_basis": round(corrected_basis, 4),
        "raw_ann_365": None if raw_ann_365 is None else round(raw_ann_365, 6),
        "corr_ann_365": None if corr_ann_365 is None else round(corr_ann_365, 6),
        "is_contango": raw_basis < 0,
        "is_backwardation": raw_basis > 0,
    }


def build_basis_payload(
    today: Optional[date] = None,
    *,
    quotes: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, Any]:
    today = today or date.today()
    all_contracts: Dict[str, List[Dict[str, Any]]] = {}
    symbols: List[str] = []
    for product, cfg in PRODUCTS.items():
        symbols.append(cfg["spot_code"])
        contracts = active_contracts(product, today)
        all_contracts[product] = contracts
        for contract in contracts:
            symbols.append(f"CFF_RE_{contract['code']}")

    if quotes is None:
        quotes = fetch_sina(symbols)
    products_out: List[Dict[str, Any]] = []
    # (symbol, fatal). Fatal gaps abort before any write; optional gaps are 下季 only.
    gaps: List[Tuple[str, bool]] = []

    for product, cfg in PRODUCTS.items():
        spot = parse_spot(quotes.get(cfg["spot_code"], []))
        annual_yield = cfg["default_yield"]
        annual_div_pts = (spot * annual_yield) if spot else None
        if spot is None:
            gaps.append((cfg["spot_code"], True))

        contracts_out: List[Dict[str, Any]] = []
        seen_labels = {c["label"] for c in all_contracts[product]}
        for contract in all_contracts[product]:
            fut = parse_futures(quotes.get(f"CFF_RE_{contract['code']}", []))
            expiry = date.fromisoformat(contract["expiry"])
            days = contract["days_to_expiry"]
            row: Dict[str, Any] = {
                "label": contract["label"],
                "code": contract["code"],
                "expiry": contract["expiry"],
                "days_to_expiry": days,
                "multiplier": cfg["multiplier"],
                "quote_date": fut.get("quote_date"),
                "quote_time": fut.get("quote_time"),
                "volume": fut.get("volume"),
            }
            if spot is None or fut.get("last") is None:
                # Keep the leg in the snapshot so the page can show which code
                # had no quote. Basis fields stay absent; last is null.
                row["error"] = "新浪暂无报价"
                row["quote_missing"] = True
                row["last"] = fut.get("last")
                row["spot"] = spot
                # 下季 alone is optional, and only when this product's spot quoted.
                optional = (
                    contract["label"] == OPTIONAL_LABEL
                    and spot is not None
                    and fut.get("last") is None
                )
                gaps.append((contract["code"], not optional))
                contracts_out.append(row)
                continue

            rem_div = remaining_div_points(spot, annual_yield, today, expiry)
            row.update(
                calc_basis_row(
                    spot=spot,
                    fut_last=fut["last"],
                    multiplier=cfg["multiplier"],
                    oi=fut.get("oi"),
                    days=days,
                    rem_div=rem_div,
                )
            )
            contracts_out.append(row)

        # active_contracts() normally always emits 当月/次月/当季. If a required
        # label was never selected, there is nothing useful to publish.
        for label in REQUIRED_LABELS:
            if label not in seen_labels:
                gaps.append((f"{product}:{label}", True))

        products_out.append(
            {
                "code": product,
                "name": cfg["name"],
                "spot_code": cfg["spot_code"],
                "spot": round(spot, 4) if spot else None,
                "multiplier": cfg["multiplier"],
                "dividend_yield": annual_yield,
                "dividend_yield_pct": round(annual_yield * 100, 4),
                "annual_div_points": round(annual_div_pts, 4) if annual_div_pts else None,
                "contracts": contracts_out,
            }
        )

    # Empty Sina payloads (`var hq_str_CFF_RE_IH2706="";`) show up for 下季
    # right after expiry, before that contract is listed. Spot and 当月/次月/当季
    # are required; if any of those are missing, or a required label was never
    # selected, abort so main() does not overwrite the snapshot. A lone missing
    # 下季 is recorded and the run continues with the other three legs.
    fatal = [code for code, is_fatal in gaps if is_fatal]
    optional_missing = [code for code, is_fatal in gaps if not is_fatal]
    if fatal:
        raise RuntimeError("missing quotes: " + ", ".join(code for code, _fatal in gaps))

    # JSON object keys must be strings.
    seasonality = {str(m): round(w, 8) for m, w in SEASONALITY.items()}

    return {
        "timestamp": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z"),
        "today": today.isoformat(),
        "products": products_out,
        "seasonality": seasonality,
        "methodology": {
            "summary": (
                "价格指数在成分股除息时自然回落，期货定价已隐含期内分红。"
                "直接用 (现货−期货)/现货 年化会把「分红」误当成「贴水」。"
                "必须用 矫正基差 = 原始基差 − 期内分红点数 再年化。"
            ),
            "raw_basis": "现货 − 期货（正=贴水，负=升水）",
            "corrected_basis": "原始基差 − 期内分红点数",
            "ann_365": "矫正基差 / 现货 × 365 / 剩余自然日（仅自然日，不含无风险利率）",
            "dividend_model": (
                "年化股息点数 = 现货 × 股息率；按 A 股季节性权重"
                "（5–7 月高峰）按日比例摊入剩余期限内。"
            ),
            "disclaimer": (
                "股息为估算（默认 IH 2.3% / IF 2.1% / IC 1.05% / IM 0.92%），"
                "非券商成分股逐笔预测。页面可改股息率后在本地重算。"
                "周末/节假日为最近交易日收盘价。"
            ),
            "default_yields": {k: v["default_yield"] for k, v in PRODUCTS.items()},
            "multipliers": {k: v["multiplier"] for k, v in PRODUCTS.items()},
        },
        "meta": {
            "source": "Sina Finance (hq.sinajs.cn)",
            "referer": "https://finance.sina.com.cn",
            "contracts_per_product": 4,
            "labels": ["当月", "次月", "当季", "下季"],
            "update": "GitHub Actions cron 0 8 * * 1-5 UTC（北京时间周一至周五 16:00），并追加 data/history/series.json",
            **({"missing_quotes": optional_missing} if optional_missing else {}),
        },
    }


def write_snapshot(payload: Dict[str, Any]) -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    OUT_PATH.write_text(text, encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Fetch CFFEX basis and historical percentiles")
    parser.add_argument(
        "--rebuild-history",
        action="store_true",
        help="Refetch Sina daily bars and rebuild data/history/series.json",
    )
    args = parser.parse_args(argv)

    try:
        payload = build_basis_payload()
    except Exception as exc:  # noqa: BLE001 — surface fetch failures to Actions logs
        print(f"fetch failed: {exc}", file=sys.stderr)
        return 1

    try:
        import basis_history

        basis_history.apply(payload, rebuild=args.rebuild_history)
    except Exception as exc:  # noqa: BLE001 — keep a bad history file from replacing the snapshot
        print(f"history failed: {exc}", file=sys.stderr)
        return 1

    write_snapshot(payload)
    skipped = payload.get("meta", {}).get("missing_quotes") or []
    if skipped:
        print("optional quotes missing: " + ", ".join(skipped), file=sys.stderr)
    print(f"wrote {OUT_PATH} at {payload['timestamp']}")
    for product in payload["products"]:
        front = product["contracts"][0]
        print(
            f"  {product['code']} spot={product['spot']} "
            f"{front['code']} last={front['last']} "
            f"raw={front['raw_basis']} corr={front['corrected_basis']} "
            f"ann365={front['corr_ann_365']} "
            f"pct={front.get('hist_pct')} N={front.get('hist_n')} "
            f"spot_pct={product.get('spot_pct')} spot_n={product.get('spot_n')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
