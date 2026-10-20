#!/usr/bin/env python3
"""Parser and page-wiring tests for the options volume snapshot. No network."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import fetch_options_volume as fetch
import universe

ROOT = Path(__file__).resolve().parents[2]


def bars(*volumes):
    out = []
    for index, (calls, puts) in enumerate(volumes, start=1):
        out.append({
            "datetime": f"2026-10-19T09:{30 + index:02d}:00" if index < 30 else f"2026-10-19T10:{index - 30:02d}:00",
            "volume": {
                "calls_volume": calls,
                "puts_volume": puts,
                "total_options_volume": calls + puts,
            },
        })
    return out


class VolumeMathTests(unittest.TestCase):
    def test_minute_bars_are_summed_not_treated_as_cumulative(self):
        summed = fetch.sum_option_volume(bars((10, 5), (3, 7)))
        self.assertEqual(summed["call_volume"], 13)
        self.assertEqual(summed["put_volume"], 12)
        self.assertEqual(summed["total_volume"], 25)
        self.assertEqual(summed["session_start"], "2026-10-19T09:31:00")
        self.assertEqual(summed["bar_count"], 2)

    def test_missing_total_falls_back_to_call_plus_put(self):
        payload = [{
            "datetime": "2026-10-19T09:31:00",
            "volume": {"calls_volume": 4, "puts_volume": 6},
        }]
        summed = fetch.sum_option_volume(payload)
        self.assertEqual(summed["total_volume"], 10)

    def test_percent_split_matches_article_rounding(self):
        call_pct, put_pct = fetch.percent_split(5147, 4853)
        self.assertEqual(call_pct, 51.47)
        self.assertEqual(put_pct, 48.53)
        self.assertEqual(round(call_pct + put_pct, 2), 100.0)

    def test_zero_volume_has_no_percent(self):
        self.assertEqual(fetch.percent_split(0, 0), (None, None))


class PayloadTests(unittest.TestCase):
    def test_index_symbols_use_cboe_prefix(self):
        mapping = universe.by_symbol()
        self.assertEqual(mapping["SPX"]["cboe"], "_SPX")
        self.assertEqual(mapping["XSP"]["cboe"], "_XSP")
        self.assertEqual(len(universe.WATCHLIST), 20)
        symbols = [item["symbol"] for item in universe.WATCHLIST]
        for required in ("SPY", "QQQ", "SPX", "IWM", "TSLA", "NVDA", "AAPL", "AMZN"):
            self.assertIn(required, symbols)

    def test_quote_and_intraday_parsers(self):
        quote = fetch.parse_quote({
            "data": {
                "iv30": 13.649,
                "iv30_change": -1.01,
                "iv30_change_percent": -3.6,
                "current_price": 767.35,
                "last_trade_time": "2026-10-19T16:00:00",
            }
        })
        self.assertEqual(quote["iv30"], 13.649)
        self.assertEqual(quote["iv30_change"], -1.01)
        volume = fetch.parse_intraday({"data": bars((100, 50))})
        self.assertEqual(volume["total_volume"], 150)
        spec = universe.by_symbol()["SPY"]
        row = fetch.build_row(spec, quote, volume)
        self.assertEqual(row["symbol"], "SPY")
        self.assertEqual(row["call_pct"], 66.67)
        self.assertEqual(row["put_pct"], 33.33)
        self.assertEqual(row["name"], "标普500ETF")

    def test_rank_and_stale_session(self):
        spy = fetch.build_row(
            universe.by_symbol()["SPY"],
            {"iv30": 13.6, "iv30_change": 0.0, "iv30_change_percent": 0.0,
             "underlying_price": 100, "quote_time": "2026-10-19T16:00:00"},
            {"call_volume": 40, "put_volume": 60, "total_volume": 100,
             "bar_count": 2, "session_start": "2026-10-19T09:31:00",
             "session_end": "2026-10-19T15:59:00"},
        )
        qqq = fetch.build_row(
            universe.by_symbol()["QQQ"],
            {"iv30": 20.0, "iv30_change": -1.0, "iv30_change_percent": -4.0,
             "underlying_price": 90, "quote_time": "2026-10-19T16:00:00"},
            {"call_volume": 80, "put_volume": 70, "total_volume": 150,
             "bar_count": 2, "session_start": "2026-10-19T09:31:00",
             "session_end": "2026-10-19T15:59:00"},
        )
        old = fetch.build_row(
            universe.by_symbol()["IWM"],
            {"iv30": 19.0, "iv30_change": 0.0, "iv30_change_percent": 0.0,
             "underlying_price": 80, "quote_time": "2026-10-16T16:00:00"},
            {"call_volume": 10, "put_volume": 10, "total_volume": 9999,
             "bar_count": 1, "session_start": "2026-10-16T09:31:00",
             "session_end": "2026-10-16T15:59:00"},
        )
        payload = fetch.build_payload([spy, qqq, old], [{"symbol": "ZZZ", "reason": "404"}])
        self.assertEqual(payload["session_date"], "2026-10-19")
        self.assertEqual([row["symbol"] for row in payload["rows"]], ["QQQ", "SPY"])
        self.assertEqual([row["rank"] for row in payload["rows"]], [1, 2])
        self.assertEqual(payload["stale"][0]["symbol"], "IWM")
        self.assertEqual(payload["coverage"], "watchlist")
        self.assertIn("不是全市场前20", payload["coverage_note"])

    def test_publish_guard(self):
        thin = {"session_date": "2026-10-19", "rows": [{"symbol": "SPY"}], "missing": []}
        with self.assertRaises(RuntimeError):
            fetch.assert_publishable(thin, minimum=15)
        fetch.assert_publishable(
            {"session_date": "2026-10-19", "rows": [{}] * 15, "missing": []},
            minimum=15,
        )
        with self.assertRaises(RuntimeError):
            fetch.assert_publishable({"session_date": None, "rows": [{}] * 20}, minimum=15)


class PageWiringTests(unittest.TestCase):
    def test_page_columns_and_back_links(self):
        html = (ROOT / "options-volume" / "index.html").read_text(encoding="utf-8")
        for label in ("排名", "代码", "名称", "期权总成交量", "看涨", "看跌", "IV30", "IV30变化"):
            self.assertIn(label, html)
        self.assertIn("data/latest.json", html)
        self.assertIn("update-options-volume.yml", html)
        self.assertIn('href="../"', html)
        self.assertIn('href="../commodity/"', html)
        self.assertIn('href="../ah-premium/"', html)
        self.assertIn('href="../spot-basis/"', html)
        self.assertIn("IVolatility", html)
        self.assertIn("不是全市场", html)

    def test_sister_headers_link_here(self):
        root = (ROOT / "index.html").read_text(encoding="utf-8")
        commodity = (ROOT / "commodity" / "index.html").read_text(encoding="utf-8")
        premium = (ROOT / "ah-premium" / "index.html").read_text(encoding="utf-8")
        spot = (ROOT / "spot-basis" / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="options-volume/"', root)
        self.assertIn('href="../options-volume/"', commodity)
        self.assertIn('href="../options-volume/"', premium)
        self.assertIn('href="../options-volume/"', spot)

    def test_workflow_is_isolated_and_after_the_us_close(self):
        text = (ROOT / ".github" / "workflows" / "update-options-volume.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "30 21 * * 1-5"', text)
        self.assertIn("workflow_dispatch", text)
        self.assertIn("contents: write", text)
        self.assertIn("group: update-basis", text)
        self.assertIn("cancel-in-progress: false", text)
        self.assertIn("python3 options-volume/scripts/test_options_volume.py", text)
        self.assertIn("python3 options-volume/scripts/fetch_options_volume.py", text)
        self.assertIn("git add options-volume/data/latest.json", text)
        self.assertIn("git pull --rebase origin main", text)
        self.assertNotIn("git add data/latest.json", text)
        self.assertNotIn("git add commodity/data", text)
        self.assertNotIn("git add ah-premium/data", text)
        self.assertNotIn("git add spot-basis/data", text)

    def test_committed_snapshot_shape(self):
        path = ROOT / "options-volume" / "data" / "latest.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertGreaterEqual(payload["quoted"], 15)
        self.assertEqual(payload["coverage"], "watchlist")
        self.assertTrue(payload["session_date"])
        rows = payload["rows"]
        self.assertEqual(rows[0]["rank"], 1)
        volumes = [row["total_volume"] for row in rows]
        self.assertEqual(volumes, sorted(volumes, reverse=True))
        symbols = {row["symbol"] for row in rows}
        for required in ("SPY", "QQQ", "SPX", "IWM"):
            self.assertIn(required, symbols)
        for row in rows:
            self.assertGreater(row["total_volume"], 0)
            self.assertEqual(round(row["call_pct"] + row["put_pct"], 2), 100.0)
            self.assertIsNotNone(row["iv30"])
            self.assertIn("iv30_change", row)


if __name__ == "__main__":
    unittest.main()
