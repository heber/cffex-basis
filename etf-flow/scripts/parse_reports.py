"""Parsers for the exchange share disclosures. No network.

Shanghai (query.sse.com.cn) reports TOT_VOL in 万份.
Shenzhen (ShowReport xlsx, 基金规模(份)) reports 份.
Both parsers return 份.
"""

from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree as ET

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
WAN_FEN = 10000.0


def parse_sse_scale(payload: dict) -> list[dict]:
    rows = payload.get("result") or []
    out = []
    for row in rows:
        code = str(row.get("SEC_CODE") or "").strip()
        if not code:
            continue
        try:
            wan = float(str(row.get("TOT_VOL")).replace(",", ""))
        except (TypeError, ValueError):
            continue
        out.append(
            {
                "date": str(row.get("STAT_DATE") or "")[:10],
                "code": code,
                "name": str(row.get("SEC_NAME") or "").strip(),
                "shares": wan * WAN_FEN,
            }
        )
    return out


def _column_letters(ref: str | None) -> str:
    if not ref:
        return ""
    return "".join(char for char in ref if char.isalpha())


def _shared_strings(blob: bytes) -> list[str]:
    try:
        xml = zipfile.ZipFile(io.BytesIO(blob)).read("xl/sharedStrings.xml")
    except (KeyError, zipfile.BadZipFile):
        return []
    root = ET.fromstring(xml)
    strings = []
    for item in root.findall("m:si", NS):
        strings.append("".join(node.text or "" for node in item.findall(".//m:t", NS)))
    return strings


def _cell_text(cell: ET.Element, shared: list[str]) -> str:
    kind = cell.get("t")
    if kind == "inlineStr":
        inline = cell.find("m:is", NS)
        if inline is None:
            return ""
        return "".join(node.text or "" for node in inline.findall(".//m:t", NS))
    value = cell.find("m:v", NS)
    if value is None or value.text is None:
        return ""
    if kind == "s":
        try:
            return shared[int(value.text)]
        except (IndexError, ValueError):
            return ""
    return value.text


def parse_szse_scale_xlsx(blob: bytes) -> list[dict]:
    """Read the Shenzhen ETF share workbook. Returns 份."""
    if not blob:
        return []
    shared = _shared_strings(blob)
    with zipfile.ZipFile(io.BytesIO(blob)) as package:
        sheet = ET.fromstring(package.read("xl/worksheets/sheet1.xml"))
    table: list[dict[str, str]] = []
    for row in sheet.findall("m:sheetData/m:row", NS):
        cells: dict[str, str] = {}
        for cell in row.findall("m:c", NS):
            letter = _column_letters(cell.get("r"))
            if letter:
                cells[letter] = _cell_text(cell, shared).strip()
        if cells:
            table.append(cells)
    if not table:
        return []
    header = table[0]
    columns = {}
    for letter, label in header.items():
        if "日期" in label:
            columns["date"] = letter
        elif "代码" in label:
            columns["code"] = letter
        elif "简称" in label:
            columns["name"] = letter
        elif "规模" in label or "份额" in label:
            columns["shares"] = letter
    if not {"date", "code", "shares"} <= set(columns):
        raise ValueError(f"unexpected SZSE share header: {header}")
    out = []
    for cells in table[1:]:
        code = cells.get(columns["code"], "").strip()
        day = cells.get(columns["date"], "").strip()[:10]
        raw = cells.get(columns["shares"], "").replace(",", "").strip()
        if not code or not day or not raw:
            continue
        try:
            shares = float(raw)
        except ValueError:
            continue
        name = cells.get(columns.get("name", ""), "").strip() if "name" in columns else ""
        out.append({"date": day, "code": code, "name": name, "shares": shares})
    return out
