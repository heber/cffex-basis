#!/usr/bin/env python3
"""Unit tests for tenor history and the <= percentile. No network."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import basis_history as bh
import fetch_basis as fb


class HistoryTests(unittest.TestCase):
    def test_parse_futures_jsonp(self) -> None:
        sample = (
            "/*<script>location.href='//sina.com';</script>*/\n"
            'var _IH2410=([{"d":"2024-08-19","c":"2352.200","o":"1","h":"1","l":"1"}]);'
        )
        rows = bh.parse_futures_kline(sample)
        self.assertEqual(rows[0]["d"], "2024-08-19")
        self.assertEqual(rows[0]["c"], "2352.200")
        self.assertEqual(bh.parse_futures_kline("var _IH1810=(null);"), [])
    def test_add_years_leap_day(self) -> None:
        self.assertEqual(bh.add_years(date(2024, 2, 29), -3), date(2021, 2, 28))
        self.assertEqual(bh.add_years(date(2024, 3, 1), -3), date(2021, 3, 1))

    def test_active_contracts_january(self) -> None:
        contracts = fb.active_contracts("IF", date(2024, 1, 15))
        self.assertEqual([c["code"] for c in contracts], ["IF2401", "IF2402", "IF2403", "IF2406"])
        self.assertEqual([c["label"] for c in contracts], ["当月", "次月", "当季", "下季"])
        self.assertEqual(contracts[0]["expiry"], "2024-01-19")
        self.assertEqual(contracts[0]["days_to_expiry"], 4)

    def test_corrected_ann_matches_hand_formula(self) -> None:
        spot = 4000.0
        fut = 3980.0
        yld = 0.021
        today = date(2024, 10, 8)
        expiry = date(2024, 10, 18)
        rem = 4000.0 * 0.021 * (0.02 * 10 / 31)
        self.assertAlmostEqual(
            fb.remaining_div_points(spot, yld, today, expiry),
            rem,
            places=9,
        )
        manual = ((spot - fut - rem) / spot) * (365 / 10)
        self.assertEqual(bh.corrected_ann_365(spot, fut, yld, today, expiry), round(manual, 6))
        self.assertIsNone(bh.corrected_ann_365(spot, fut, yld, expiry, expiry))

    def test_percentile_is_fraction_leq(self) -> None:
        self.assertIsNone(bh.percentile_leq([], 1))
        self.assertEqual(bh.percentile_leq([1, 2, 3], 0), 0)
        self.assertAlmostEqual(bh.percentile_leq([1, 2, 3], 1), 100 / 3)
        self.assertEqual(bh.percentile_leq([1, 2, 3], 3), 100)
        self.assertEqual(bh.percentile_leq([1, 2, 3], 4), 100)
        self.assertEqual(bh.percentile_leq([0.2, 0.2, 0.2], 0.2), 100)

    def test_spot_sample_counts_each_index_day_once(self) -> None:
        labels = {label: [] for label in bh.LABELS}
        labels["当月"] = [
            [20231008, 100.0, 1, 10, 0.1],
            [20231009, 110.0, 1, 10, 0.1],
            [20261008, 130.0, 1, 10, 0.1],
            [20261009, 999.0, 1, 10, 0.1],
        ]
        labels["次月"] = [
            [20231009, 110.0, 1, 20, 0.1],
            [20240102, 120.0, 1, 20, 0.1],
        ]
        sample, first, last = bh.select_spot_sample(labels, date(2026, 10, 9))
        self.assertEqual(sample, [110.0, 120.0, 130.0])
        self.assertEqual(first, date(2023, 10, 9))
        self.assertEqual(last, date(2026, 10, 8))
        self.assertEqual(bh.round1(bh.percentile_leq(sample, 120.0)), 66.7)
        self.assertEqual(bh.percentile_leq(sample, 130.0), 100)
        self.assertEqual(bh.percentile_leq(sample, 100.0), 0)

    def test_sample_excludes_quote_date_and_uses_three_calendar_years(self) -> None:
        rows = [
            [20231008, 1, 1, 10, 0.01],
            [20231009, 1, 1, 10, 0.02],
            [20261008, 1, 1, 10, 0.05],
            [20261009, 1, 1, 10, 9.99],
        ]
        sample, first, last = bh.select_sample(rows, date(2026, 10, 9))
        self.assertEqual(sample, [0.02, 0.05])
        self.assertEqual(first, date(2023, 10, 9))
        self.assertEqual(last, date(2026, 10, 8))

    def test_build_rows_assigns_each_tenor(self) -> None:
        day = date(2024, 1, 15)
        contracts = fb.active_contracts("IF", day)
        spot = {day: 4000.0}
        bars = {}
        for i, contract in enumerate(contracts):
            bars[contract["code"]] = {day: 4000.0 - (i + 1) * 10}
        rows = bh.build_rows_for_product("IF", spot, bars, date(2024, 1, 1), day)
        self.assertEqual(len(rows["当月"]), 1)
        self.assertEqual(rows["当月"][0][0], 20240115)
        self.assertEqual(rows["当月"][0][3], 4)
        self.assertEqual(len(rows["下季"]), 1)
        self.assertEqual(
            rows["下季"][0][4],
            bh.corrected_ann_365(4000.0, 3960.0, 0.021, day, date(2024, 6, 21)),
        )
        self.assertEqual(
            rows["当月"][0][4],
            bh.corrected_ann_365(4000.0, 3990.0, 0.021, day, date(2024, 1, 19)),
        )

    def test_upsert_replaces_same_date_and_keeps_order(self) -> None:
        rows = [
            [20240115, 1, 1, 4, 0.1],
            [20240117, 1, 1, 2, 0.3],
        ]
        bh.upsert_row(rows, [20240116, 1, 1, 3, 0.2])
        bh.upsert_row(rows, [20240115, 1, 1, 4, 0.15])
        self.assertEqual([row[0] for row in rows], [20240115, 20240116, 20240117])
        self.assertEqual(rows[0][4], 0.15)

    def test_attach_percentile_and_roundtrip(self) -> None:
        series = {
            code: {label: [] for label in bh.LABELS}
            for code in fb.PRODUCTS
        }
        # 40 trading-ish days inside the window, plus the quote date itself.
        front = []
        for i in range(40):
            day = date(2026, 8, 1) + __import__("datetime").timedelta(days=i)
            front.append([bh.ymd_int(day), 3000.0, 2970.0, 20, round(0.01 * (i + 1), 6)])
        front.append([20261009, 3000.0, 2900.0, 5, 0.5])
        series["IH"]["当月"] = front
        doc = bh.new_document(series)
        payload = {
            "today": "2026-10-11",
            "products": [
                {
                    "code": "IH",
                    "spot": 3000.0,
                    "contracts": [
                        {
                            "label": "当月",
                            "code": "IH2610",
                            "expiry": "2026-10-16",
                            "quote_date": "2026-10-09",
                            "spot": 3000.0,
                            "last": 2900.0,
                            "oi": 10,
                            "corr_ann_365": 0.2,
                        },
                        {
                            "label": "次月",
                            "code": "IH2611",
                            "expiry": "2026-11-20",
                            "quote_date": "2026-10-09",
                            "spot": 3000.0,
                            "last": 2950.0,
                            "oi": 99,
                            "corr_ann_365": 0.05,
                        },
                    ],
                }
            ],
            "methodology": {},
        }
        # Only August dates through 2026-09-09 are within 40 days of Aug 1,
        # and all of them are inside [2023-10-09, 2026-10-09).
        bh.attach_percentiles(payload, doc)
        front_row = payload["products"][0]["contracts"][0]
        self.assertEqual(front_row["hist_n"], 40)
        self.assertEqual(front_row["hist_pct"], 50.0)  # 0.01..0.40, 20 values <= 0.20
        product = payload["products"][0]
        self.assertEqual(product["spot_n"], 40)
        self.assertEqual(product["spot_pct"], 100.0)  # every stored close equals today's spot
        self.assertEqual(product["spot_start"], "2026-08-01")
        self.assertEqual(product["spot_end"], "2026-09-09")
        self.assertIn("高位", payload["history_meta"]["spot_higher_means"])
        self.assertIn("贴水分位", payload["history_meta"]["higher_means"])
        self.assertTrue(product["contracts"][1]["is_main"])
        self.assertNotIn("is_main", front_row)
        self.assertIn("越高", payload["history_meta"]["higher_means"])

        bh.upsert_payload(doc, payload)
        stored = doc["series"]["IH"]["当月"]
        self.assertEqual(stored[-1][0], 20261009)
        # Upsert stores the quote-date annualization, not the weekend snapshot value.
        self.assertNotEqual(stored[-1][4], 0.2)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "series.json"
            bh.save_series(doc, path)
            loaded = bh.load_series(path)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["series"]["IH"]["当月"][-1], stored[-1])
            again = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(again["series"]["IF"]["下季"], [])

    def test_dumps_doc_when_last_metadata_field_is_a_string(self) -> None:
        """A trailing string must not trip the outer-brace splice.

        series.json written before the spot-rule fields existed loads with
        `series` last. apply() then assigns those strings, so they become the
        last metadata fields. The writer used to require the text left after
        stripping one `}` to still end in `}`.
        """
        series = {code: {label: [] for label in bh.LABELS} for code in fb.PRODUCTS}
        series["IF"]["当月"] = [[20261009, 4000.0, 3980.0, 7, 0.12]]
        fresh = bh.new_document(series)
        stored: dict = {}
        for key in (
            "version",
            "built_at",
            "source",
            "row",
            "window",
            "window_label",
            "percentile_rule",
            "higher_means",
            "annualization",
            "dividend_note",
            "min_sample",
            "yields",
            "archive",
            "series",
        ):
            stored[key] = fresh[key]
        stored["spot_percentile_rule"] = bh.SPOT_PERCENTILE_RULE
        stored["spot_higher_means"] = bh.SPOT_HIGHER_MEANS
        stored["custom_note"] = "plain string"
        payload = {
            "today": "2026-10-12",
            "products": [
                {
                    "code": "IF",
                    "spot": 4010.0,
                    "contracts": [
                        {
                            "label": "当月",
                            "code": "IF2610",
                            "expiry": "2026-10-16",
                            "quote_date": "2026-10-12",
                            "spot": 4010.0,
                            "last": 3990.0,
                            "oi": 10,
                            "corr_ann_365": 0.2,
                        }
                    ],
                }
            ],
            "methodology": {},
        }
        bh.upsert_payload(stored, payload)
        text = bh.dumps_doc(stored)
        loaded = json.loads(text)
        rows = loaded["series"]["IF"]["当月"]
        self.assertEqual([row[0] for row in rows], [20261009, 20261012])
        self.assertEqual(loaded["custom_note"], "plain string")
        self.assertEqual(loaded["spot_higher_means"], bh.SPOT_HIGHER_MEANS)
        self.assertLess(text.index('"spot_percentile_rule"'), text.index('"archive"'))
        self.assertLess(text.index('"archive"'), text.index('"series"'))
        self.assertEqual(loaded["archive"]["products"]["IF"]["end"], "2026-10-12")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "series.json"
            bh.save_series(stored, path)
            again = bh.load_series(path)
            self.assertIsNotNone(again)
            self.assertEqual(again["series"]["IF"]["当月"][-1][0], 20261012)
            self.assertEqual(again["series"]["IH"]["下季"], [])


if __name__ == "__main__":
    unittest.main()
