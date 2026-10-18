"""Parser and basis tests. No network."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "commodity" / "scripts"))

import ppi
import universe


FIXTURE = """
<title>2026年10月16日商品现货与期货价格对比表</title>
<div class="title2">https://www.100ppi.com 2026年10月16日 16:30 来源：生意社</div>
<table id="fdata">
<tr><td>商品</td><td>现货</td><td colspan="3">最近合约</td><td colspan="3">主力合约</td></tr>
<tr><td>价格</td><td>代码</td><td>价格</td><td>现期差1</td><td>代码</td><td>价格</td><td>现期差2</td></tr>
<tr><td colspan="8">上海期货交易所</td></tr>
<tr>
  <td><a href="/sf/927.html">螺纹钢</a></td>
  <td>3164.84&nbsp;</td>
  <td>2611</td>
  <td>3098</td>
  <td><table><tr><td>66</td><td>2.09%</td></tr></table></td>
  <td>2701</td>
  <td>3074</td>
  <td><table><tr><td>90</td><td>2.84%</td></tr></table></td>
</tr>
<tr>
  <td><a>玻璃</a></td>
  <td>11.95</td>
  <td>2610</td>
  <td>888</td>
  <td></td>
  <td>2701</td>
  <td>859</td>
  <td></td>
</tr>
<tr>
  <td><a>黄金</a></td>
  <td>893.97</td>
  <td>2612</td>
  <td>901.82</td>
  <td></td>
  <td>2612</td>
  <td>901.82</td>
  <td></td>
</tr>
<tr><td colspan="8">大连商品交易所</td></tr>
<tr>
  <td>铁矿石</td>
  <td>662.44</td>
  <td>2610</td>
  <td>690</td>
  <td></td>
  <td>2701</td>
  <td>672.5</td>
  <td></td>
</tr>
<tr>
  <td>聚乙烯</td>
  <td>9825</td>
  <td>2610</td>
  <td>9760</td>
  <td></td>
  <td>2701</td>
  <td>8663</td>
  <td></td>
</tr>
</table>
<div class="cle txtbz">备注：现期差=现货价格-期货价格<br />黄金为元/克，玻璃现货为元/平方米</div>
"""


def _contract(symbol, price, dte, expiry="2027-01-15"):
    return {
        "display": symbol,
        "price": price,
        "expiry": expiry,
        "dte": dte,
        "oi": 10,
        "volume": 3,
    }


class ParseTests(unittest.TestCase):
    def test_table_ignores_nested_basis_cells(self):
        parsed = ppi.parse_ppi_html(FIXTURE)
        self.assertEqual(parsed["date"], "2026-10-16")
        self.assertEqual(parsed["published"], "2026-10-16 16:30")
        self.assertIn("黄金为元/克", parsed["footnote"])
        by_name = {row["name"]: row for row in parsed["rows"]}
        self.assertEqual(by_name["螺纹钢"]["price"], 3164.84)
        self.assertEqual(by_name["螺纹钢"]["near_code"], "2611")
        self.assertEqual(by_name["螺纹钢"]["near_price"], 3098)
        self.assertEqual(by_name["螺纹钢"]["main_code"], "2701")
        self.assertEqual(by_name["螺纹钢"]["main_price"], 3074)
        self.assertNotIn(66, (by_name["螺纹钢"]["price"], by_name["螺纹钢"]["main_price"]))
        self.assertEqual(by_name["玻璃"]["exchange"], "上海期货交易所")
        self.assertEqual(by_name["铁矿石"]["exchange"], "大连商品交易所")

    def test_missing_table_raises(self):
        with self.assertRaises(ValueError):
            ppi.parse_ppi_html("<html><title>没有表</title></html>")

    def test_aliases_cover_the_commodity_universe(self):
        codes = {row[0] for row in universe.PRODUCTS}
        self.assertEqual(set(ppi.NAME_TO_CODE.values()), codes)

    def test_names_from_the_live_table_map(self):
        names = [
            "铜", "螺纹钢", "锌", "铝", "黄金", "燃料油", "白银", "热轧卷板", "镍",
            "PTA", "白糖", "棉花", "菜籽油OI", "玻璃", "菜籽粕", "甲醇MA", "尿素",
            "纯碱", "棕榈油", "聚氯乙烯", "聚乙烯", "豆粕", "豆油", "玉米", "焦炭",
            "焦煤", "铁矿石", "聚丙烯", "乙二醇", "工业硅", "碳酸锂",
        ]
        codes = {ppi.NAME_TO_CODE[name] for name in names}
        self.assertEqual(len(codes), 31)
        self.assertNotIn("sc", codes)
        self.assertNotIn("lu", codes)
        self.assertNotIn("cs", codes)
        self.assertNotIn("AP", codes)


class BasisTests(unittest.TestCase):
    def test_sign_and_annualization(self):
        raw, ann = ppi.basis_vs(365, 0.01, 365)
        self.assertAlmostEqual(raw, 364.99, places=2)
        raw, ann = ppi.basis_vs(100, 90, 365)
        self.assertEqual(raw, 10)
        self.assertAlmostEqual(ann, 0.1)
        raw, ann = ppi.basis_vs(100, 110, 365)
        self.assertEqual(raw, -10)
        self.assertAlmostEqual(ann, -0.1)
        raw, ann = ppi.basis_vs(100, 110, 6)
        self.assertEqual(raw, -10)
        self.assertIsNone(ann)
        self.assertEqual(ppi.basis_vs(None, 110, 30), (None, None))

    def test_glass_conversion_and_iron_flag(self):
        glass = ppi.spot_terms("FG", 11.95)
        self.assertEqual(glass["basis_price"], 956.0)
        self.assertEqual(glass["quality"], "converted")
        self.assertEqual(glass["spot_unit"], "元/平方米")
        iron = ppi.spot_terms("i", 662.44)
        self.assertEqual(iron["basis_price"], 662.44)
        self.assertEqual(iron["quality"], "spec_mismatch")
        gold = ppi.spot_terms("au", 893.97)
        self.assertEqual(gold["spot_unit"], "元/克")
        self.assertEqual(gold["quality"], "aligned")

    def test_missing_product_is_unavailable_and_does_not_raise(self):
        parsed = ppi.parse_ppi_html(FIXTURE)
        products = [
            {"code": "rb", "name": "螺纹钢", "exchange": "SHFE", "exchange_name": "上期所", "sector": "黑色"},
            {"code": "AP", "name": "苹果", "exchange": "CZCE", "exchange_name": "郑商所", "sector": "农产品"},
            {"code": "FG", "name": "玻璃", "exchange": "CZCE", "exchange_name": "郑商所", "sector": "化工"},
        ]
        leg = {
            "futures_date": "2026-10-16",
            "clocks": ["150000"],
            "near": _contract("rb2611", 3098, 28, "2026-11-13"),
            "main": _contract("rb2701", 3074, 90, "2027-01-15"),
            "far": _contract("rb2705", 3040, 200, "2027-05-14"),
            "curve": [
                _contract("rb2611", 3098, 28, "2026-11-13"),
                _contract("rb2701", 3074, 90, "2027-01-15"),
                _contract("rb2705", 3040, 200, "2027-05-14"),
            ],
        }
        payload = ppi.build_payload(
            products, parsed, {"rb": leg},
            source_url="https://www.100ppi.com/sf/",
            generated_at="2026-10-18T10:00:00+08:00",
        )
        self.assertEqual(payload["coverage"], {"tracked": 3, "with_spot": 2})
        self.assertTrue(payload["dates_aligned"])
        self.assertFalse(payload["futures_night_session"])
        by_code = {item["code"]: item for item in payload["products"]}
        self.assertFalse(by_code["AP"]["spot_available"])
        self.assertIsNone(by_code["AP"]["near"])
        self.assertIn("苹果", by_code["AP"]["note"])
        rb = by_code["rb"]
        self.assertEqual(rb["spot_price"], 3164.84)
        self.assertEqual(rb["main"]["symbol"], "rb2701")
        self.assertAlmostEqual(rb["main"]["basis"], 3164.84 - 3074)
        self.assertGreater(rb["main"]["ann"], 0)
        self.assertEqual(rb["curve"][1]["roles"], ["main"])
        self.assertEqual(rb["curve"][0]["roles"], ["near"])
        glass = by_code["FG"]
        self.assertEqual(glass["basis_price"], 956.0)
        self.assertIsNone(glass["main"])

    def test_night_clock(self):
        self.assertTrue(ppi.night_clock("230000"))
        self.assertTrue(ppi.night_clock("023000"))
        self.assertFalse(ppi.night_clock("150000"))
        self.assertFalse(ppi.night_clock(""))


if __name__ == "__main__":
    unittest.main()
