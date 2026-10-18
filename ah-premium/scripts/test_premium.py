"""Name gate, AH premium formula, and snapshot wiring. No network."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fetch_premium
import universe

ROOT = Path(__file__).resolve().parents[2]

A_LINE = (
    'var hq_str_sh601398="工商银行,8.470,8.500,8.480,8.540,8.380,8.480,8.490,'
    '321920585,2723358570.000,61900,8.480,323200,8.470,495100,8.460,650900,8.450,'
    '912000,8.440,2175300,8.490,467200,8.500,1179100,8.510,2864000,8.520,7458967,'
    '8.530,2026-10-16,15:34:59,00,D|43300|367184.00";'
)
H_LINE = (
    'var hq_str_hk01398="ICBC,工商银行,7.740,7.695,7.740,7.530,7.590,-0.105,-1.365,'
    '7.65000,7.65000,1466903643,192551187,0.000,0.000,7.740,5.355,2026/10/16,16:06";'
)
FX_LINE = 'var hq_str_HKDCNY="05:21:27,0.8516,0.8524,0.8522,14,0.8528,0.8529,0.8515,0.8516,港元人民币,2026-10-17";'

# Different issuer that shares a leading character with 中国建筑. Must never enter the list.
FALSE_FRIEND_H = {"03311", "06888", "03076", "06696", "00550", "02005", "06316", "06313"}
FALSE_FRIEND_A = {"sh601668", "sh601669", "sh603799"}


class UniverseTests(unittest.TestCase):
    def test_universe_is_unique_and_sized(self):
        universe.validate_universe()
        self.assertGreaterEqual(len(universe.PAIRS), 100)
        self.assertEqual(len({row["a_symbol"] for row in universe.PAIRS}), len(universe.PAIRS))
        self.assertEqual(len({row["h_code"] for row in universe.PAIRS}), len(universe.PAIRS))

    def test_false_friends_stay_out(self):
        h_codes = {row["h_code"] for row in universe.PAIRS}
        a_symbols = {row["a_symbol"] for row in universe.PAIRS}
        self.assertTrue(FALSE_FRIEND_H.isdisjoint(h_codes))
        self.assertTrue(FALSE_FRIEND_A.isdisjoint(a_symbols))

    def test_name_gate_accepts_truncated_and_st_names(self):
        cases = [
            ("XD中国海", "中国海洋石油", "中国海油"),
            ("XD国泰海", "国泰海通", "国泰海通"),
            ("XD时代电", "时代电气", "时代电气"),
            ("ST晨鸣", "晨鸣纸业", "晨鸣纸业"),
            ("万 科Ａ", "万科企业", "万科"),
            ("中国人保", "中国人民保险集团", "中国人保"),
            ("华泰证券", "HTSC", "华泰证券"),
            ("中国石化", "中国石油化工股份", "中国石化"),
            ("中国广核", "中广核电力", "中国广核"),
            ("中海油服", "中海油田服务", "中海油服"),
        ]
        by_name = {row["name"]: row for row in universe.PAIRS}
        for a_name, h_name, key in cases:
            self.assertTrue(universe.names_match(a_name, h_name, by_name[key]), key)

    def test_name_gate_rejects_sibling_issuers(self):
        by_name = {row["name"]: row for row in universe.PAIRS}
        self.assertFalse(universe.names_match("中国石油", "中国石油化工股份", by_name["中国石油"]))
        self.assertFalse(universe.names_match("中国石化", "上海石油化工股份", by_name["中国石化"]))
        self.assertFalse(universe.names_match("中国海油", "中海油田服务", by_name["中国海油"]))
        self.assertFalse(universe.names_match("中国建筑", "中国建筑国际", by_name["中国中铁"]))


class PremiumTests(unittest.TestCase):
    def test_formula_matches_industry_ratio(self):
        h_cny, premium = fetch_premium.ah_premium(8.48, 7.59, 0.8522)
        self.assertAlmostEqual(h_cny, 7.59 * 0.8522)
        self.assertAlmostEqual(premium, (8.48 - h_cny) / h_cny)
        self.assertGreater(premium, 0)

        h_cny, premium = fetch_premium.ah_premium(42.45, 53.15, 0.8522)
        self.assertLess(premium, 0)
        self.assertAlmostEqual(premium, (42.45 - 53.15 * 0.8522) / (53.15 * 0.8522))

    def test_formula_rejects_bad_prices(self):
        self.assertEqual(fetch_premium.ah_premium(0, 7.59, 0.85), (None, None))
        self.assertEqual(fetch_premium.ah_premium(8.48, 0, 0.85), (None, None))
        self.assertEqual(fetch_premium.ah_premium(8.48, 7.59, 0), (None, None))

    def test_sina_fixtures(self):
        quotes = fetch_premium.parse_sina_quotes("\n".join([A_LINE, H_LINE, FX_LINE]))
        a = fetch_premium.parse_a(quotes["sh601398"])
        h = fetch_premium.parse_h(quotes["hk01398"])
        fx = fetch_premium.parse_fx(quotes)
        self.assertEqual(a["name"], "工商银行")
        self.assertEqual(a["price"], 8.48)
        self.assertEqual(a["prev"], 8.50)
        self.assertEqual(a["date"], "2026-10-16")
        self.assertAlmostEqual(a["change_pct"], (8.48 - 8.50) / 8.50)
        self.assertEqual(h["name"], "工商银行")
        self.assertEqual(h["price"], 7.59)
        self.assertEqual(h["prev"], 7.695)
        self.assertEqual(h["date"], "2026-10-16")
        self.assertAlmostEqual(h["change_pct"], (7.59 - 7.695) / 7.695)
        self.assertEqual(fx["symbol"], "HKDCNY")
        self.assertEqual(fx["rate"], 0.8522)
        self.assertEqual(fx["quote_date"], "2026-10-17")

    def test_build_payload_skips_name_mismatch(self):
        rows = [
            universe.pair("工商银行", "银行", "sh601398", "01398", "工商银行"),
            universe.pair("中国石油", "能源", "sh601857", "00857", "中国石油", reject=("化工",)),
        ]
        quotes = fetch_premium.parse_sina_quotes("\n".join([A_LINE, H_LINE, FX_LINE]))
        quotes["sh601857"] = ["中国石油", "0", "10", "10"] + [""] * 28 + ["2026-10-16", "15:00:00"]
        quotes["hk00857"] = ["SINOPEC", "中国石油化工股份", "0", "4", "0", "0", "4", "", "", "", "", "", "", "", "", "", "", "2026/10/16", "16:00"]
        fx = fetch_premium.parse_fx(quotes)
        payload = fetch_premium.build_payload(rows, quotes, fx, None, "2026-10-18T08:00:00+08:00")
        self.assertEqual([row["name"] for row in payload["pairs"]], ["工商银行"])
        self.assertEqual(payload["summary"]["quoted"], 1)
        self.assertEqual(payload["skipped"][0]["name"], "中国石油")
        h_cny, premium = fetch_premium.ah_premium(8.48, 7.59, 0.8522)
        self.assertAlmostEqual(payload["pairs"][0]["premium"], round(premium, 6))
        self.assertAlmostEqual(payload["pairs"][0]["h_in_cny"], round(h_cny, 4))
        self.assertGreaterEqual(payload["summary"]["median_premium"], 0)

    def test_publish_guard(self):
        payload = {"fx": {"rate": 0.85}, "summary": {"quoted": 10}, "skipped": []}
        with self.assertRaises(RuntimeError):
            fetch_premium.assert_publishable(payload, minimum=90)
        payload["summary"]["quoted"] = 90
        fetch_premium.assert_publishable(payload, minimum=90)


class PageWiringTests(unittest.TestCase):
    def test_page_states_formula_and_links_back(self):
        html = (ROOT / "ah-premium" / "index.html").read_text(encoding="utf-8")
        self.assertIn("AH溢价率", html)
        self.assertIn("H股折合人民币", html)
        self.assertIn('href="../"', html)
        self.assertIn('href="../commodity/"', html)
        self.assertIn("data/latest.json", html)
        self.assertIn("update-ah-premium.yml", html)

    def test_sister_pages_link_here(self):
        root = (ROOT / "index.html").read_text(encoding="utf-8")
        commodity = (ROOT / "commodity" / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="ah-premium/"', root)
        self.assertIn('href="../ah-premium/"', commodity)

    def test_workflow_coordinates_with_the_other_updates(self):
        text = (ROOT / ".github" / "workflows" / "update-ah-premium.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "0 8 * * 1-5"', text)
        self.assertIn("workflow_dispatch", text)
        self.assertIn("contents: write", text)
        self.assertIn("group: update-basis", text)
        self.assertIn("cancel-in-progress: false", text)
        self.assertIn("python ah-premium/scripts/fetch_premium.py", text)
        self.assertIn("git add ah-premium/data", text)
        self.assertIn("git pull --rebase origin main", text)
        self.assertNotIn("git add data/latest.json", text)
        self.assertNotIn("git add commodity/data", text)


if __name__ == "__main__":
    unittest.main()
