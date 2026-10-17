"""Unit tests for virtual-main selection, basis sign, and percentiles."""

from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import roll


def C(year, month, price, oi, volume, expiry=None, exchange="DCE", code="m"):
    if expiry is None:
        expiry = date(year, month, 15)
    return {
        "display": roll.display_symbol(code, year, month, exchange),
        "year": year,
        "month": month,
        "price": price,
        "oi": oi,
        "volume": volume,
        "expiry": expiry,
    }


class ExpiryTests(unittest.TestCase):
    def test_day15_saturday_rolls_forward(self):
        # 2024-06-15 was Saturday; SHFE base metals printed through Monday 17th.
        self.assertEqual(roll.day15_forward(2024, 6), date(2024, 6, 17))

    def test_day15_weekday_unchanged(self):
        self.assertEqual(roll.day15_forward(2024, 8), date(2024, 8, 15))

    def test_tenth_weekday_sep_2024(self):
        # Matches TA2409 / M2409 last active week when no long holiday.
        self.assertEqual(roll.nth_weekday(2024, 9, 10), date(2024, 9, 13))

    def test_prev_month_end_june_contract(self):
        self.assertEqual(roll.prev_month_last_weekday(2024, 6), date(2024, 5, 31))

    def test_prev_month_end_january(self):
        self.assertEqual(roll.prev_month_last_weekday(2025, 1), date(2024, 12, 31))


class SessionDateTests(unittest.TestCase):
    def test_saturday_after_midnight_is_friday(self):
        self.assertEqual(roll.session_trade_date(date(2026, 10, 10), "010000"), date(2026, 10, 9))

    def test_friday_night_stays_friday(self):
        self.assertEqual(roll.session_trade_date(date(2026, 10, 9), "230000"), date(2026, 10, 9))

    def test_sunday_evening_is_monday(self):
        self.assertEqual(roll.session_trade_date(date(2026, 10, 11), "210000"), date(2026, 10, 12))

    def test_sunday_afternoon_falls_back_to_friday(self):
        self.assertEqual(roll.session_trade_date(date(2026, 10, 11), "150000"), date(2026, 10, 9))

    def test_monday_after_midnight_stays_monday(self):
        self.assertEqual(roll.session_trade_date(date(2026, 10, 12), "010000"), date(2026, 10, 12))


class SymbolTests(unittest.TestCase):
    def test_display_case(self):
        self.assertEqual(roll.display_symbol("m", 2027, 1, "DCE"), "m2701")
        self.assertEqual(roll.display_symbol("OI", 2027, 5, "CZCE"), "OI2705")
        self.assertEqual(roll.sina_symbol("rb", 2026, 10), "RB2610")

    def test_parse(self):
        self.assertEqual(roll.parse_yymm("OI2705"), ("OI", 2027, 5))
        self.assertEqual(roll.parse_yymm("m2701"), ("M", 2027, 1))
        self.assertIsNone(roll.parse_yymm("TA701"))


class LeadershipTests(unittest.TestCase):
    def test_not_the_nearest_month(self):
        # Nearby month is listed but the 1-month contract leads on oi+volume.
        near = C(2026, 11, 3390, 476316, 26697)
        main = C(2027, 1, 3416, 2504626, 392507)
        mid = C(2027, 3, 3353, 381715, 24866)
        far = C(2027, 5, 3023, 845989, 74474)
        dust = C(2027, 9, 3112, 34923, 2038)
        dead = C(2026, 9, 3319, 0, 0)
        contracts = [near, main, mid, far, dust, dead]
        picked = roll.pick_virtual_main(contracts)
        self.assertEqual(picked["display"], "m2701")
        leg = roll.pick_far_leg(contracts, picked)
        # 5-month outranks the nearer 3-month. Not "next calendar month".
        self.assertEqual(leg["display"], "m2705")

    def test_tie_prefers_nearer_expiry(self):
        a = C(2027, 1, 100, 10, 10)
        b = C(2027, 5, 100, 10, 10)
        self.assertEqual(roll.pick_virtual_main([b, a])["display"], "m2701")

    def test_zero_activity_excluded(self):
        dead = C(2027, 1, 100, 0, 0)
        live = C(2027, 5, 90, 1, 0)
        self.assertEqual(roll.pick_virtual_main([dead, live])["display"], "m2705")

    def test_far_leg_missing_when_main_is_last(self):
        only = C(2027, 9, 100, 50, 50)
        main = roll.pick_virtual_main([only])
        self.assertIsNone(roll.pick_far_leg([only], main))

    def test_liquid_terms_keep_far_leg_and_drop_dust(self):
        contracts = [
            C(2027, 1, 3416, 2_000_000, 300_000),
            C(2027, 3, 3350, 20_000, 1_000),
            C(2027, 5, 3023, 800_000, 70_000),
            C(2027, 7, 3000, 5_000, 100),
            C(2027, 8, 2990, 4_000, 100),
            C(2027, 9, 3112, 30_000, 2_000),
            C(2027, 11, 3100, 3_000, 50),
            C(2027, 12, 3090, 2_000, 40),
        ]
        main = roll.pick_virtual_main(contracts)
        far = roll.pick_far_leg(contracts, main)
        terms = roll.select_liquid_terms(contracts, main, far, k=4, cap=6, floor=0.20)
        names = [c["display"] for c in terms]
        self.assertEqual(names[0], "m2701")
        self.assertIn("m2705", names)
        self.assertNotIn("m2712", names)
        self.assertEqual(names, sorted(names))


class SpotProxyTests(unittest.TestCase):
    def test_earliest_unexpired_beats_the_liquid_main(self):
        trade = date(2026, 10, 9)
        near = C(2026, 11, 689.5, 80_000, 4_000, expiry=date(2026, 11, 13), code="i")
        main = C(2027, 1, 684.0, 600_000, 70_000, expiry=date(2027, 1, 14), code="i")
        far = C(2027, 5, 678.0, 120_000, 6_000, expiry=date(2027, 5, 14), code="i")
        contracts = [far, main, near]
        self.assertEqual(roll.pick_virtual_main(contracts)["display"], "i2701")
        self.assertEqual(roll.pick_spot_proxy(contracts, trade)["display"], "i2611")
        rec = roll.build_day(contracts, trade)
        gap = (date(2027, 5, 14) - date(2026, 11, 13)).days
        self.assertEqual(rec["contract"], "i2701")
        self.assertEqual(rec["px"], 684.0)
        self.assertEqual(rec["spot"], "i2611")
        self.assertEqual(rec["spot_px"], 689.5)
        self.assertEqual(rec["far"], "i2705")
        self.assertEqual(rec["raw"], 689.5 - 678.0)
        self.assertEqual(rec["gap"], gap)
        self.assertAlmostEqual(rec["ann"], (689.5 - 678.0) / 689.5 * 365 / gap)

    def test_tie_prefers_higher_score_then_oi(self):
        trade = date(2026, 10, 9)
        quiet = C(2026, 12, 100, 10, 1, expiry=date(2026, 11, 13))
        busy = C(2026, 11, 100, 40, 5, expiry=date(2026, 11, 13))
        self.assertEqual(roll.pick_spot_proxy([quiet, busy], trade)["display"], "m2611")
        # Same score 15. Higher open interest wins over higher volume.
        oi_heavy = C(2026, 11, 100, 12, 3, expiry=date(2026, 11, 13))
        vol_heavy = C(2026, 12, 100, 10, 5, expiry=date(2026, 11, 13))
        self.assertEqual(roll.pick_spot_proxy([vol_heavy, oi_heavy], trade)["display"], "m2611")

    def test_expired_contract_is_not_the_proxy(self):
        trade = date(2026, 10, 9)
        dead = C(2026, 9, 200, 99_000, 99_000, expiry=date(2026, 9, 15), code="i")
        near = C(2026, 11, 100, 10, 1, expiry=date(2026, 11, 13), code="i")
        self.assertEqual(roll.pick_spot_proxy([dead, near], trade)["display"], "i2611")
        # Liquidity main can still be the expired contract. Far stays the
        # later month, and basis is empty because that far leg is the proxy.
        rec = roll.build_day([dead, near], trade)
        self.assertEqual(rec["contract"], "i2609")
        self.assertEqual(rec["spot"], "i2611")
        self.assertEqual(rec["far"], "i2611")
        self.assertIsNone(rec["ann"])
        self.assertIsNone(rec["raw"])

    def test_far_stays_later_than_main_when_proxy_is_after_main(self):
        trade = date(2026, 10, 9)
        main = C(2026, 9, 100, 9_000, 1_000, expiry=date(2026, 9, 14), code="i")
        spot = C(2026, 11, 110, 50, 5, expiry=date(2026, 11, 13), code="i")
        far = C(2027, 1, 90, 500, 50, expiry=date(2027, 1, 14), code="i")
        rec = roll.build_day([main, spot, far], trade)
        self.assertEqual(rec["contract"], "i2609")
        self.assertEqual(rec["spot"], "i2611")
        self.assertEqual(rec["far"], "i2701")
        gap = (date(2027, 1, 14) - date(2026, 11, 13)).days
        self.assertEqual(rec["gap"], gap)
        self.assertAlmostEqual(rec["ann"], (110 - 90) / 110 * 365 / gap)

    def test_far_expiry_not_after_proxy_leaves_basis_empty(self):
        trade = date(2026, 10, 9)
        main = C(2026, 8, 100, 9_000, 100, expiry=date(2026, 8, 14), code="i")
        far = C(2026, 9, 95, 800, 80, expiry=date(2026, 9, 15), code="i")
        spot = C(2026, 11, 110, 10, 1, expiry=date(2026, 11, 13), code="i")
        rec = roll.build_day([main, far, spot], trade)
        self.assertEqual(rec["far"], "i2609")
        self.assertEqual(rec["spot"], "i2611")
        self.assertLessEqual(rec["gap"], 0)
        self.assertIsNone(rec["ann"])
        self.assertIsNone(rec["raw"])

    def test_short_gap_between_proxy_and_far_is_not_annualized(self):
        trade = date(2026, 10, 9)
        spot = C(2026, 10, 100, 10, 1, expiry=date(2026, 11, 13))
        main = C(2026, 11, 200, 5_000, 500, expiry=date(2026, 11, 14))
        far = C(2026, 12, 90, 400, 40, expiry=date(2026, 11, 16))
        rec = roll.build_day([spot, main, far], trade)
        self.assertEqual(rec["spot"], "m2610")
        self.assertEqual(rec["contract"], "m2611")
        self.assertEqual(rec["far"], "m2612")
        self.assertEqual(rec["gap"], 3)
        self.assertEqual(rec["raw"], 100 - 90)
        self.assertIsNone(rec["ann"])

    def test_expiry_day_still_counts_as_unexpired(self):
        trade = date(2026, 11, 13)
        spot = C(2026, 11, 100, 10, 1, expiry=date(2026, 11, 13))
        later = C(2027, 1, 90, 9_000, 100, expiry=date(2027, 1, 14))
        self.assertEqual(roll.pick_spot_proxy([later, spot], trade)["display"], "m2611")

    def test_liquid_curve_keeps_an_illiquid_proxy(self):
        contracts = [
            C(2026, 11, 689.5, 1_000, 10, expiry=date(2026, 11, 13), code="i"),
            C(2027, 1, 684.0, 2_000_000, 300_000, expiry=date(2027, 1, 14), code="i"),
            C(2027, 5, 678.0, 800_000, 70_000, expiry=date(2027, 5, 14), code="i"),
        ]
        main = roll.pick_virtual_main(contracts)
        far = roll.pick_far_leg(contracts, main)
        spot = roll.pick_spot_proxy(contracts, date(2026, 10, 9))
        terms = roll.select_liquid_terms(contracts, main, far, k=2, cap=4, floor=0.20, spot=spot)
        names = [c["display"] for c in terms]
        self.assertIn("i2611", names)
        self.assertEqual(names, sorted(names))


class BasisTests(unittest.TestCase):
    def test_backwardation_is_positive(self):
        ann = roll.annualized_basis(3416, 3023, 120)
        self.assertAlmostEqual(ann, (3416 - 3023) / 3416 * 365 / 120)
        self.assertGreater(ann, 0)

    def test_contango_is_negative(self):
        self.assertLess(roll.annualized_basis(100, 110, 90), 0)

    def test_short_gap_not_annualized(self):
        self.assertIsNone(roll.annualized_basis(100, 90, 6))

    def test_build_day_uses_far_liquid_not_adjacent(self):
        contracts = [
            C(2027, 1, 3416, 2_000_000, 300_000, expiry=date(2027, 1, 14)),
            C(2027, 3, 3353, 300_000, 20_000, expiry=date(2027, 3, 12)),
            C(2027, 5, 3023, 800_000, 70_000, expiry=date(2027, 5, 14)),
        ]
        rec = roll.build_day(contracts, date(2026, 10, 9))
        self.assertEqual(rec["contract"], "m2701")
        self.assertEqual(rec["spot"], "m2701")
        self.assertEqual(rec["far"], "m2705")
        self.assertGreater(rec["ann"], 0)
        self.assertEqual(rec["raw"], 3416 - 3023)


class AdjustAndPercentileTests(unittest.TestCase):
    def test_ratio_splice_removes_roll_gap(self):
        rows = [
            {"contract": "a", "px": 100, "out": None},
            {"contract": "a", "px": 110, "out": None},
            {"contract": "b", "px": 80, "out": 105},
            {"contract": "b", "px": 88, "out": None},
        ]
        adj = roll.ratio_back_adjust(rows)
        self.assertAlmostEqual(adj[-1], 88)
        self.assertAlmostEqual(adj[2], 80)
        self.assertAlmostEqual(adj[1] / adj[0], 110 / 100)
        self.assertAlmostEqual(adj[2] / adj[1], 105 / 110)
        self.assertAlmostEqual(adj[3] / adj[2], 88 / 80)

    def test_flat_when_out_missing(self):
        rows = [
            {"contract": "a", "px": 100, "out": None},
            {"contract": "b", "px": 80, "out": None},
        ]
        adj = roll.ratio_back_adjust(rows)
        self.assertEqual(adj, [80, 80])

    def test_percentile_direction_and_window(self):
        asof = date(2026, 10, 9)
        start = roll.window_start(asof)
        self.assertEqual(start, date(2023, 10, 9))
        points = []
        d = start
        i = 0
        while d < asof:
            points.append((d, float(i)))
            d += timedelta(days=10)
            i += 1
        # Also a point before the window and the as-of day, both ignored.
        points.append((start - timedelta(days=1), 10_000))
        points.append((asof, -1))
        shallow, n_s = roll.historical_percentile(points, asof, 0)
        deep, n_d = roll.historical_percentile(points, asof, 10_000)
        self.assertEqual(n_s, n_d)
        self.assertGreaterEqual(n_s, 30)
        self.assertLess(shallow, deep)
        self.assertEqual(deep, 100)

    def test_min_samples(self):
        asof = date(2026, 10, 9)
        points = [(asof - timedelta(days=i + 1), 1.0) for i in range(10)]
        pct, n = roll.historical_percentile(points, asof, 1.0)
        self.assertIsNone(pct)
        self.assertEqual(n, 10)

    def test_feb29_window(self):
        self.assertEqual(roll.window_start(date(2024, 2, 29)), date(2021, 2, 28))

    def test_score_snapshot_anchors_on_live_price(self):
        asof = date(2026, 10, 9)
        history = []
        px = 100.0
        for i in range(40):
            d = asof - timedelta(days=40 - i)
            history.append({
                "date": d,
                "contract": "m2701",
                "px": px,
                "ann": 0.01 * i,
                "out": None,
            })
            px += 1
        live = {
            "date": asof,
            "contract": "m2705",
            "px": 80.0,
            "ann": 0.50,
            "out": 140.0,
        }
        scored = roll.score_snapshot(history, live, asof)
        self.assertIsNotNone(scored["basis_pct"])
        self.assertIsNotNone(scored["price_pct"])
        # Live ann 0.50 is above every historical ann (max 0.39), so the
        # basis percentile is the top of the window.
        self.assertEqual(scored["basis_pct"], 100)
        rows = history + [live]
        adj = roll.ratio_back_adjust(rows)
        self.assertAlmostEqual(adj[-1], 80.0)

    def test_assemble_history_roll(self):
        d1, d2 = date(2024, 1, 2), date(2024, 1, 3)
        a1 = C(2024, 5, 100, 500, 100, expiry=date(2024, 5, 15))
        b1 = C(2024, 9, 90, 100, 10, expiry=date(2024, 9, 13))
        a2 = C(2024, 5, 110, 80, 10, expiry=date(2024, 5, 15))
        b2 = C(2024, 9, 95, 400, 200, expiry=date(2024, 9, 13))
        rows = roll.assemble_history({d1: [a1, b1], d2: [a2, b2]}, date(2023, 1, 1))
        self.assertEqual([r["contract"] for r in rows], ["m2405", "m2409"])
        self.assertEqual(rows[1]["out"], 110)
        self.assertEqual(rows[0]["far"], "m2409")
        self.assertEqual(rows[0]["spot"], "m2405")
        self.assertGreater(rows[0]["ann"], 0)


if __name__ == "__main__":
    unittest.main()
