"""Flow formula, share parsers, and the seven-index universe. No network."""

from __future__ import annotations

import io
import sys
import unittest
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parent))

import calc
import parse_reports
import universe


def _xlsx(rows: list[list[str]]) -> bytes:
    body = ['<?xml version="1.0" encoding="UTF-8"?>']
    body.append(
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
    )
    for index, row in enumerate(rows, start=1):
        body.append(f'<row r="{index}">')
        for column, value in enumerate(row):
            letter = chr(ord("A") + column)
            body.append(
                f'<c r="{letter}{index}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
            )
        body.append("</row>")
    body.append("</sheetData></worksheet>")
    sheet = "".join(body).encode("utf-8")
    content_types = b"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""
    rels = b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
    workbook = b"""<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>"""
    workbook_rels = b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w") as package:
        package.writestr("[Content_Types].xml", content_types)
        package.writestr("_rels/.rels", rels)
        package.writestr("xl/workbook.xml", workbook)
        package.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        package.writestr("xl/worksheets/sheet1.xml", sheet)
    return blob.getvalue()


class UniverseTests(unittest.TestCase):
    def test_seven_indexes_are_unique_and_ordered(self):
        codes = [row["index_code"] for row in universe.CATEGORIES]
        ids = [row["id"] for row in universe.CATEGORIES]
        self.assertEqual(len(codes), 7)
        self.assertEqual(len(set(codes)), 7)
        self.assertEqual(len(set(ids)), 7)
        self.assertEqual(ids[-3:], ["csi1000", "chinext", "star50"])
        self.assertEqual(universe.category_by_index("000300")["id"], "csi300")
        self.assertIsNone(universe.category_by_index("399673"))
        self.assertIsNone(universe.category_by_index("000698"))
        self.assertIsNone(universe.category_by_index(None))


class FlowTests(unittest.TestCase):
    def test_creation_is_share_change_times_nav(self):
        self.assertEqual(calc.flow_yuan(100, 150, 2, 2), 100)

    def test_first_zero_print_is_not_a_redemption(self):
        self.assertIsNone(calc.flow_yuan(100, 0, 2, 2))
        self.assertIsNone(calc.flow_yuan(None, 100, None, 2))

    def test_split_does_not_look_like_a_creation(self):
        # 2-for-1: shares double, NAV halves, value unchanged.
        self.assertAlmostEqual(calc.flow_yuan(100, 200, 2, 1), 0, places=6)
        # Same split plus 50 new shares at the new NAV.
        self.assertAlmostEqual(calc.flow_yuan(100, 250, 2, 1), 50, places=6)

    def test_ordinary_nav_move_is_not_a_split(self):
        self.assertEqual(calc.flow_yuan(100, 110, 2, 2.1), 21)

    def test_nav_carry_stops_after_five_days(self):
        series = {"2026-10-01": 1.5}
        self.assertEqual(calc.carry_value(series, "2026-10-01"), 1.5)
        self.assertEqual(calc.carry_value(series, "2026-10-06"), 1.5)
        self.assertIsNone(calc.carry_value(series, "2026-10-07"))

    def test_long_hole_is_not_a_redemption(self):
        fund = {
            "category": "sse50",
            "shares": {"2026-01-05": 100.0, "2026-02-01": 50.0},
            "nav": {"2026-01-05": 1.0, "2026-02-01": 1.0},
            "close": {},
        }
        rows, as_of = calc.build_series({"510050": fund}, ["sse50"], "2026-01-01")
        self.assertEqual(as_of, "2026-02-01")
        self.assertEqual(rows[-1]["flow"]["sse50"], 0)

    def test_window_matches_the_screenshot_month(self):
        self.assertEqual(calc.window_start("2026-10-23", "1m"), "2026-09-23")
        self.assertEqual(calc.window_start("2026-10-23", "3m"), "2026-07-23")
        self.assertEqual(calc.window_start("2026-10-23", "ytd"), "2026-01-01")
        self.assertIsNone(calc.window_start("2026-10-23", "5d"))
        self.assertEqual(calc.window_start("2026-03-31", "1m"), "2026-02-28")

    def test_thin_session_is_dropped_and_publish_gate_holds(self):
        funds = {}
        for index in range(10):
            shares = {"2026-10-22": 100.0, "2026-10-23": 100.0}
            if index < 3:
                shares["2026-10-24"] = 100.0
            funds[f"{index:06d}"] = {
                "category": "csi300",
                "shares": shares,
                "nav": {"2026-10-22": 1.0, "2026-10-23": 1.0, "2026-10-24": 1.0},
                "close": {},
            }
        rows, as_of = calc.build_series(funds, ["csi300"], "2026-01-01")
        self.assertEqual(as_of, "2026-10-23")
        self.assertEqual([row["date"] for row in rows], ["2026-10-22", "2026-10-23"])
        self.assertFalse(calc.publishable(119))
        self.assertTrue(calc.publishable(120))

    def test_category_sum_is_the_screenshot_total(self):
        parts = {
            "sse50": 0.44,
            "csi300": 248.70,
            "a500": 18.69,
            "csi500": 68.17,
            "csi1000": 52.24,
            "chinext": 74.88,
            "star50": 47.70,
        }
        self.assertAlmostEqual(sum(parts.values()), 510.82, places=2)


class ParserTests(unittest.TestCase):
    def test_sse_scale_is_in_shares_not_wan_fen(self):
        rows = parse_reports.parse_sse_scale(
            {
                "result": [
                    {
                        "STAT_DATE": "2026-10-23",
                        "SEC_CODE": "510300",
                        "SEC_NAME": "300ETF",
                        "TOT_VOL": "3524268.77",
                    }
                ]
            }
        )
        self.assertEqual(rows[0]["shares"], 3524268.77 * 10000)
        self.assertEqual(rows[0]["code"], "510300")

    def test_szse_xlsx_is_already_in_shares(self):
        blob = _xlsx(
            [
                ["日期", "基金代码", "基金简称", "基金规模(份)"],
                ["2026-10-23", "159915", "创业板ETF易方达", "33757454936.00"],
            ]
        )
        rows = parse_reports.parse_szse_scale_xlsx(blob)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["code"], "159915")
        self.assertEqual(rows[0]["shares"], 33757454936.0)
        self.assertEqual(rows[0]["date"], "2026-10-23")


if __name__ == "__main__":
    unittest.main()
