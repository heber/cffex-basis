"""China commodity universe for the virtual-main basis tracker.

Contract multiplier and exchange minimum margin live in specs.py.

Expiry rules are weekday approximations of exchange last-trading-day
conventions. Statutory holidays are not removed; see README.
"""

from __future__ import annotations

# code, Chinese name, exchange, sector, expiry rule
# code is the display root (CZCE stays uppercase).
PRODUCTS = [
    ("i", "铁矿石", "DCE", "黑色", "tenth"),
    ("j", "焦炭", "DCE", "黑色", "tenth"),
    ("jm", "焦煤", "DCE", "黑色", "tenth"),
    ("rb", "螺纹钢", "SHFE", "黑色", "d15"),
    ("hc", "热轧卷板", "SHFE", "黑色", "d15"),
    ("m", "豆粕", "DCE", "油脂油料", "tenth"),
    ("y", "豆油", "DCE", "油脂油料", "tenth"),
    ("p", "棕榈油", "DCE", "油脂油料", "tenth"),
    ("OI", "菜油", "CZCE", "油脂油料", "tenth"),
    ("RM", "菜粕", "CZCE", "油脂油料", "tenth"),
    ("cu", "沪铜", "SHFE", "有色贵金属", "d15"),
    ("al", "沪铝", "SHFE", "有色贵金属", "d15"),
    ("zn", "沪锌", "SHFE", "有色贵金属", "d15"),
    ("ni", "沪镍", "SHFE", "有色贵金属", "d15"),
    ("au", "沪金", "SHFE", "有色贵金属", "d15"),
    ("ag", "沪银", "SHFE", "有色贵金属", "d15"),
    ("sc", "原油", "INE", "能源", "prev_eom"),
    ("fu", "燃料油", "SHFE", "能源", "prev_eom"),
    ("lu", "低硫燃料油", "INE", "能源", "prev_eom"),
    ("TA", "PTA", "CZCE", "化工", "tenth"),
    ("MA", "甲醇", "CZCE", "化工", "tenth"),
    ("SA", "纯碱", "CZCE", "化工", "tenth"),
    ("FG", "玻璃", "CZCE", "化工", "tenth"),
    ("UR", "尿素", "CZCE", "化工", "tenth"),
    ("eg", "乙二醇", "DCE", "化工", "tenth"),
    ("l", "塑料", "DCE", "化工", "tenth"),
    ("v", "PVC", "DCE", "化工", "tenth"),
    ("pp", "聚丙烯", "DCE", "化工", "tenth"),
    ("c", "玉米", "DCE", "农产品", "tenth"),
    ("cs", "玉米淀粉", "DCE", "农产品", "tenth"),
    ("CF", "棉花", "CZCE", "农产品", "tenth"),
    ("SR", "白糖", "CZCE", "农产品", "tenth"),
    ("AP", "苹果", "CZCE", "农产品", "tenth"),
    ("lc", "碳酸锂", "GFEX", "新能源", "tenth"),
    ("si", "工业硅", "GFEX", "新能源", "tenth"),
]

EXCHANGE_NAME = {
    "DCE": "大商所",
    "SHFE": "上期所",
    "CZCE": "郑商所",
    "INE": "能源中心",
    "GFEX": "广期所",
}

SECTORS = ["黑色", "油脂油料", "有色贵金属", "能源", "化工", "农产品", "新能源"]

# History fetch span. The percentile window is the trailing 3 calendar years;
# contracts listed before the window still have to be loaded so the early
# window has a leader.
HISTORY_START = (2022, 9)
HISTORY_END_MONTHS_AHEAD = 15
SERIES_MIN_DATE = (2023, 1, 1)
