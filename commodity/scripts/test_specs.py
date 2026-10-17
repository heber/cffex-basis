"""Locks the reviewed multiplier and ordinary-month exchange margin table."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import specs
import universe

# code, multiplier, unit, ordinary-month exchange speculative rate
# Reviewed 2026-10-11 from exchange contract texts and the 2026-10-12 margin schedule.
EXPECTED = {
    "i": (100, "吨", 0.08),
    "j": (100, "吨", 0.12),
    "jm": (60, "吨", 0.12),
    "rb": (10, "吨", 0.07),
    "hc": (10, "吨", 0.07),
    "m": (10, "吨", 0.07),
    "y": (10, "吨", 0.07),
    "p": (10, "吨", 0.08),
    "OI": (10, "吨", 0.07),
    "RM": (10, "吨", 0.07),
    "cu": (5, "吨", 0.09),
    "al": (5, "吨", 0.09),
    "zn": (5, "吨", 0.09),
    "ni": (1, "吨", 0.10),
    "au": (1000, "克", 0.16),
    "ag": (15, "千克", 0.16),
    "sc": (1000, "桶", 0.16),
    "fu": (10, "吨", 0.16),
    "lu": (10, "吨", 0.16),
    "TA": (5, "吨", 0.07),
    "MA": (10, "吨", 0.07),
    "SA": (20, "吨", 0.08),
    "FG": (20, "吨", 0.09),
    "UR": (20, "吨", 0.08),
    "eg": (10, "吨", 0.07),
    "l": (5, "吨", 0.07),
    "v": (5, "吨", 0.07),
    "pp": (5, "吨", 0.07),
    "c": (10, "吨", 0.07),
    "cs": (10, "吨", 0.06),
    "CF": (5, "吨", 0.07),
    "SR": (10, "吨", 0.06),
    "AP": (10, "吨", 0.09),
    "lc": (1, "吨", 0.15),
    "si": (5, "吨", 0.10),
}


class SpecTableTests(unittest.TestCase):
    def test_covers_every_product(self):
        codes = [row[0] for row in universe.PRODUCTS]
        self.assertEqual(len(codes), 35)
        self.assertEqual(set(specs.BY_CODE), set(codes))
        self.assertEqual(set(EXPECTED), set(codes))

    def test_reviewed_multiplier_and_rate(self):
        for code, multiplier, unit, rate in (
            (code, *EXPECTED[code]) for code in EXPECTED
        ):
            spec = specs.get(code)
            self.assertEqual(spec["multiplier"], multiplier, code)
            self.assertEqual(spec["unit"], unit, code)
            self.assertEqual(spec["margin_rate"], rate, code)
            self.assertGreater(spec["margin_rate"], 0)
            self.assertLessEqual(spec["margin_rate"], 0.25)
            self.assertTrue(spec["quote_unit"].startswith("元/"))

    def test_iron_ore_notional_and_margin(self):
        spec = specs.get("i")
        self.assertEqual(specs.label(spec), "100吨/手")
        self.assertEqual(specs.notional(684, spec), 68400)
        self.assertEqual(specs.margin_amount(684, spec), 5472)

    def test_gold_silver_and_crude_units(self):
        self.assertEqual(specs.label(specs.get("au")), "1000克/手")
        self.assertEqual(specs.notional(900, specs.get("au")), 900000)
        self.assertEqual(specs.label(specs.get("ag")), "15千克/手")
        self.assertEqual(specs.notional(8000, specs.get("ag")), 120000)
        self.assertEqual(specs.label(specs.get("sc")), "1000桶/手")
        self.assertIn("元/桶", specs.QUOTE_NOTE["sc"])

    def test_apply_uses_main_price_and_stamps_legs(self):
        snap = specs.apply({
            "code": "i",
            "virtual": {"symbol": "i2701", "price": 684},
            "spot_proxy": {"symbol": "i2610", "price": 690},
            "far": {"symbol": "i2705", "price": 678},
            "terms": [{"symbol": "i2701", "price": 684, "role": "virtual"}],
        })
        self.assertEqual(snap["multiplier"], 100)
        self.assertEqual(snap["multiplier_text"], "100吨/手")
        self.assertEqual(snap["margin_rate"], 0.08)
        self.assertEqual(snap["margin_as_of"], "2026-10-12")
        self.assertEqual(snap["currency"], "CNY")
        self.assertEqual(snap["notional"], 68400)
        self.assertEqual(snap["margin_min"], 5472)
        self.assertEqual(snap["spot_proxy"]["notional"], 69000)
        self.assertEqual(snap["spot_proxy"]["margin_min"], 5520)
        self.assertEqual(snap["far"]["margin_min"], round(678 * 100 * 0.08, 2))
        self.assertEqual(snap["terms"][0]["notional"], 68400)

    def test_missing_price_leaves_money_empty(self):
        snap = specs.apply({"code": "rb", "virtual": None, "spot_proxy": None, "far": None, "terms": []})
        self.assertEqual(snap["multiplier_text"], "10吨/手")
        self.assertIsNone(snap["notional"])
        self.assertIsNone(snap["margin_min"])


if __name__ == "__main__":
    unittest.main()
