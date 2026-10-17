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


def _sina_line(symbol: str, payload: str) -> str:
    return f'var hq_str_{symbol}="{payload}";'


def _spot_payload(px: str) -> str:
    return ",".join(["index", "1", "1", px, "1", "1"])


def _fut_payload(last: str, oi: str, quote_date: str) -> str:
    fields = [""] * 38
    fields[0] = fields[1] = fields[2] = fields[3] = last
    fields[4] = "12"
    fields[6] = oi
    fields[36] = quote_date
    fields[37] = "15:00:00"
    return ",".join(fields)


def _far_codes(day: date) -> set:
    codes = set()
    for product in fb.PRODUCTS:
        for contract in fb.active_contracts(product, day):
            if contract["label"] == "下季":
                codes.add(contract["code"])
    return codes


def _quote_book(day: date, blank: set) -> dict:
    """Build parsed Sina quotes. `blank` holds spot codes or contract codes.

    Blank futures are the empty payload Sina returns for an unlisted contract
    (`var hq_str_CFF_RE_IH2706="";`), not a missing line.
    """
    lines = []
    bases = {"IH": 3000, "IF": 4000, "IC": 5000, "IM": 6000}
    for product, cfg in fb.PRODUCTS.items():
        spot_sym = cfg["spot_code"]
        spot_body = "" if spot_sym in blank else _spot_payload(str(bases[product]))
        lines.append(_sina_line(spot_sym, spot_body))
        for i, contract in enumerate(fb.active_contracts(product, day)):
            sym = f"CFF_RE_{contract['code']}"
            if sym in blank or contract["code"] in blank:
                body = ""
            else:
                body = _fut_payload(
                    str(bases[product] - (i + 1)),
                    str(100 * (i + 1)),
                    day.isoformat(),
                )
            lines.append(_sina_line(sym, body))
    return fb.parse_sina_quotes("\n".join(lines))


class MissingQuoteTests(unittest.TestCase):
    """Sina empty-string quotes after the 2026-10-16 expiry.

    On 2026-10-17, 下季 rolls to *2706 while hq.sinajs.cn still answers
    `var hq_str_CFF_RE_IH2706="";`. Nearer contracts, including *2703, quote.
    """

    DAY = date(2026, 10, 17)

    def test_empty_sina_payload_parses_as_no_fields(self) -> None:
        raw = "\n".join(
            [
                'var hq_str_CFF_RE_IH2706="";',
                _sina_line("CFF_RE_IH2703", _fut_payload("2700", "9", "2026-10-16")),
            ]
        )
        parsed = fb.parse_sina_quotes(raw)
        self.assertEqual(parsed["CFF_RE_IH2706"], [])
        self.assertIsNone(fb.parse_futures(parsed["CFF_RE_IH2706"])["last"])
        self.assertEqual(fb.parse_futures(parsed["CFF_RE_IH2703"])["last"], 2700.0)

    def test_empty_far_quarter_keeps_near_legs_and_history(self) -> None:
        blank = _far_codes(self.DAY)
        self.assertEqual(blank, {"IH2706", "IF2706", "IC2706", "IM2706"})
        quotes = _quote_book(self.DAY, blank)
        for code in blank:
            self.assertEqual(quotes[f"CFF_RE_{code}"], [])
        self.assertEqual(fb.parse_futures(quotes["CFF_RE_IH2703"])["last"], 2997.0)

        payload = fb.build_basis_payload(self.DAY, quotes=quotes)
        self.assertEqual(
            payload["meta"]["missing_quotes"],
            ["IH2706", "IF2706", "IC2706", "IM2706"],
        )
        self.assertNotIn("error", payload)

        series = {code: {label: [] for label in bh.LABELS} for code in fb.PRODUCTS}
        doc = bh.new_document(series)
        bh.upsert_payload(doc, payload)
        bh.attach_percentiles(payload, doc)

        for product in payload["products"]:
            codes = [c["code"] for c in product["contracts"]]
            self.assertEqual(codes[1][-4:], "2612")
            self.assertEqual(codes[2][-4:], "2703")
            self.assertEqual(codes[3][-4:], "2706")
            near = product["contracts"][:3]
            self.assertEqual([c["label"] for c in near], ["当月", "次月", "当季"])
            for contract in near:
                self.assertNotIn("quote_missing", contract)
                self.assertNotIn("error", contract)
                self.assertIsNotNone(contract["last"])
                self.assertIsNotNone(contract["corr_ann_365"])
            far = product["contracts"][3]
            self.assertEqual(far["label"], "下季")
            self.assertTrue(far["quote_missing"])
            self.assertEqual(far["error"], "新浪暂无报价")
            self.assertIsNone(far["last"])
            self.assertNotIn("corr_ann_365", far)
            self.assertIsNone(far["hist_pct"])
            self.assertNotIn("is_main", far)
            stored = doc["series"][product["code"]]
            self.assertEqual(stored["下季"], [])
            for label in ("当月", "次月", "当季"):
                self.assertEqual(len(stored[label]), 1)
                self.assertEqual(stored[label][0][0], 20261017)

        front = payload["products"][0]["contracts"][0]
        self.assertEqual(front["code"], "IH2611")
        self.assertEqual(front["last"], 2999.0)
        self.assertAlmostEqual(front["raw_basis"], 1.0, places=4)

    def test_one_product_far_quarter_does_not_drop_the_others(self) -> None:
        payload = fb.build_basis_payload(self.DAY, quotes=_quote_book(self.DAY, {"IH2706"}))
        self.assertEqual(payload["meta"]["missing_quotes"], ["IH2706"])
        ih = payload["products"][0]
        self.assertTrue(ih["contracts"][3]["quote_missing"])
        for product in payload["products"][1:]:
            for contract in product["contracts"]:
                self.assertNotIn("quote_missing", contract)
                self.assertIsNotNone(contract["corr_ann_365"])

    def test_complete_book_omits_missing_quotes_key(self) -> None:
        payload = fb.build_basis_payload(self.DAY, quotes=_quote_book(self.DAY, set()))
        self.assertNotIn("missing_quotes", payload["meta"])
        for product in payload["products"]:
            self.assertEqual(len(product["contracts"]), 4)
            for contract in product["contracts"]:
                self.assertNotIn("error", contract)
                self.assertIsNotNone(contract["corr_ann_365"])

    def test_absent_required_label_aborts(self) -> None:
        orig = fb.active_contracts

        def without_current_quarter(prefix: str, today=None):
            return [c for c in orig(prefix, today) if c["label"] != "当季"]

        fb.active_contracts = without_current_quarter
        try:
            with self.assertRaises(RuntimeError) as ctx:
                fb.build_basis_payload(self.DAY, quotes=_quote_book(self.DAY, set()))
        finally:
            fb.active_contracts = orig
        self.assertEqual(
            str(ctx.exception),
            "missing quotes: IH:当季, IF:当季, IC:当季, IM:当季",
        )

    def test_required_legs_still_abort(self) -> None:
        cases = {
            "spot": ({"sh000016"}, "sh000016, IH2611, IH2612, IH2703, IH2706"),
            "当月": ({"IH2611"}, "IH2611"),
            "次月": ({"IH2612"}, "IH2612"),
            "当季 even if 下季 quotes": ({"IH2703"}, "IH2703"),
            "当季 and 下季": ({"IH2703", "IH2706"}, "IH2703, IH2706"),
            "only 当季 left": ({"IH2611", "IH2612", "IH2706"}, "IH2611, IH2612, IH2706"),
        }
        for name, (blank, message) in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(RuntimeError) as ctx:
                    fb.build_basis_payload(self.DAY, quotes=_quote_book(self.DAY, blank))
                self.assertEqual(str(ctx.exception), "missing quotes: " + message)

    def test_main_does_not_write_when_required_quotes_are_missing(self) -> None:
        latest = (fb.ROOT / "data" / "latest.json").read_bytes()
        series = (bh.SERIES_PATH).read_bytes()
        writes: list = []
        applies: list = []
        orig_fetch, orig_write, orig_apply = fb.fetch_sina, fb.write_snapshot, bh.apply
        fb.fetch_sina = lambda symbols: {sym: [] for sym in symbols}
        fb.write_snapshot = lambda payload: writes.append(payload)
        bh.apply = lambda *args, **kwargs: applies.append(True)
        try:
            rc = fb.main([])
        finally:
            fb.fetch_sina, fb.write_snapshot, bh.apply = orig_fetch, orig_write, orig_apply
        self.assertEqual(rc, 1)
        self.assertEqual(writes, [])
        self.assertEqual(applies, [])
        self.assertEqual((fb.ROOT / "data" / "latest.json").read_bytes(), latest)
        self.assertEqual(bh.SERIES_PATH.read_bytes(), series)

    def test_main_publishes_when_only_far_quarter_is_empty(self) -> None:
        day = date.today()
        book = _quote_book(day, _far_codes(day))
        latest = (fb.ROOT / "data" / "latest.json").read_bytes()
        series = bh.SERIES_PATH.read_bytes()
        writes: list = []
        orig_fetch, orig_write, orig_apply = fb.fetch_sina, fb.write_snapshot, bh.apply

        def fake_apply(payload, rebuild=False, path=None):
            doc = bh.new_document(
                {code: {label: [] for label in bh.LABELS} for code in fb.PRODUCTS}
            )
            bh.upsert_payload(doc, payload)
            bh.attach_percentiles(payload, doc)
            writes.append(doc)

        fb.fetch_sina = lambda symbols: book
        fb.write_snapshot = lambda payload: writes.append(payload)
        bh.apply = fake_apply
        try:
            rc = fb.main([])
        finally:
            fb.fetch_sina, fb.write_snapshot, bh.apply = orig_fetch, orig_write, orig_apply
        self.assertEqual(rc, 0)
        self.assertEqual(len(writes), 2)
        doc, payload = writes
        expected = []
        for product in fb.PRODUCTS:
            for contract in fb.active_contracts(product, day):
                if contract["label"] == "下季":
                    expected.append(contract["code"])
        self.assertEqual(payload["meta"]["missing_quotes"], expected)
        for product in payload["products"]:
            code = product["code"]
            for contract in product["contracts"]:
                rows = doc["series"][code][contract["label"]]
                quoted_days = None
                if contract.get("quote_date") and contract.get("expiry"):
                    quoted_days = (
                        date.fromisoformat(contract["expiry"])
                        - date.fromisoformat(contract["quote_date"])
                    ).days
                if contract.get("quote_missing") or not quoted_days or quoted_days <= 0:
                    self.assertEqual(rows, [])
                else:
                    self.assertEqual(len(rows), 1)
        self.assertEqual((fb.ROOT / "data" / "latest.json").read_bytes(), latest)
        self.assertEqual(bh.SERIES_PATH.read_bytes(), series)


if __name__ == "__main__":
    unittest.main()
