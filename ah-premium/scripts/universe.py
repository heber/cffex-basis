"""Curated A/H dual-listed universe.

Each row is one issuer with a mainland A-share and a Hong Kong H-share.
Prices are compared per share (1 A share = 1 H share), which is the usual
AH premium convention. This is not an automatic scrape of every connected
name, and it is not the market-cap-weighted Hang Seng Stock Connect China
AH Premium Index.

Left out on purpose:
- Same-looking but different issuers, e.g. 中国建筑 (601668) vs 中国建筑国际
  (03311), and 中国电建 (601669) vs 中广核电力 (01816, paired here with 中国广核).
- Names with a GDR but no H-share, e.g. 华友钴业.
- The Hong Kong RMB counter. Quotes use the HKD counter (hk + 5 digits).
"""

from __future__ import annotations

import re

SECTORS = (
    "银行",
    "保险",
    "证券",
    "能源",
    "材料",
    "工业",
    "汽车",
    "新能源",
    "消费",
    "医药",
    "通信",
    "交运",
    "公用事业",
    "建材",
    "地产",
)

_A_RE = re.compile(r"^(sh|sz)\d{6}$")
_H_RE = re.compile(r"^\d{5}$")


def pair(name, sector, a_symbol, h_code, a_key, h_key=None, aliases=(), reject=()):
    return {
        "name": name,
        "sector": sector,
        "a_symbol": a_symbol,
        "h_code": h_code,
        "a_key": a_key,
        "h_key": a_key if h_key is None else h_key,
        "h_aliases": tuple(aliases),
        "reject": tuple(reject),
    }


PAIRS = [
    # 银行
    pair("工商银行", "银行", "sh601398", "01398", "工商银行"),
    pair("建设银行", "银行", "sh601939", "00939", "建设银行"),
    pair("农业银行", "银行", "sh601288", "01288", "农业银行"),
    pair("中国银行", "银行", "sh601988", "03988", "中国银行"),
    pair("招商银行", "银行", "sh600036", "03968", "招商银行"),
    pair("交通银行", "银行", "sh601328", "03328", "交通银行"),
    pair("邮储银行", "银行", "sh601658", "01658", "邮储银行"),
    pair("中信银行", "银行", "sh601998", "00998", "中信银行"),
    pair("光大银行", "银行", "sh601818", "06818", "光大银行"),
    pair("民生银行", "银行", "sh600016", "01988", "民生银行"),
    pair("浙商银行", "银行", "sh601916", "02016", "浙商银行"),
    pair("重庆银行", "银行", "sh601963", "01963", "重庆银行"),
    pair("郑州银行", "银行", "sz002936", "06196", "郑州银行"),
    pair("青岛银行", "银行", "sz002948", "03866", "青岛银行"),
    # 保险
    pair("中国平安", "保险", "sh601318", "02318", "中国平安"),
    pair("中国人寿", "保险", "sh601628", "02628", "中国人寿"),
    pair("中国太保", "保险", "sh601601", "02601", "中国太保"),
    pair("新华保险", "保险", "sh601336", "01336", "新华保险"),
    pair("中国人保", "保险", "sh601319", "01339", "人保", "人民保险"),
    # 证券
    pair("中信证券", "证券", "sh600030", "06030", "中信证券"),
    pair("华泰证券", "证券", "sh601688", "06886", "华泰", aliases=("HTSC",)),
    pair("中金公司", "证券", "sh601995", "03908", "中金公司"),
    pair("国泰海通", "证券", "sh601211", "02611", "国泰海"),
    pair("广发证券", "证券", "sz000776", "01776", "广发证券"),
    pair("申万宏源", "证券", "sz000166", "06806", "申万宏源"),
    pair("东方证券", "证券", "sh600958", "03958", "东方证券"),
    pair("光大证券", "证券", "sh601788", "06178", "光大证券"),
    pair("招商证券", "证券", "sh600999", "06099", "招商证券"),
    pair("中国银河", "证券", "sh601881", "06881", "中国银河"),
    pair("中信建投", "证券", "sh601066", "06066", "中信建投"),
    # 能源
    pair("中国石油", "能源", "sh601857", "00857", "中国石油", reject=("化工", "海洋", "油服", "油田")),
    pair("中国石化", "能源", "sh600028", "00386", "中国石化", "石油化工", reject=("油服", "上海", "海洋")),
    pair("中国海油", "能源", "sh600938", "00883", "中国海", reject=("油服", "油田", "化工")),
    pair("中国神华", "能源", "sh601088", "01088", "中国神华"),
    pair("中煤能源", "能源", "sh601898", "01898", "中煤能源"),
    pair("兖矿能源", "能源", "sh600188", "01171", "兖矿能源"),
    pair("中海油服", "能源", "sh601808", "02883", "中海油"),
    pair("石化油服", "能源", "sh600871", "01033", "油服", reject=("上海", "海洋")),
    pair("上海石化", "能源", "sh600688", "00338", "上海石", reject=("油服",)),
    # 材料
    pair("中国铝业", "材料", "sh601600", "02600", "中国铝业"),
    pair("江西铜业", "材料", "sh600362", "00358", "江西铜业"),
    pair("紫金矿业", "材料", "sh601899", "02899", "紫金矿业"),
    pair("洛阳钼业", "材料", "sh603993", "03993", "洛阳钼业"),
    pair("山东黄金", "材料", "sh600547", "01787", "山东黄金"),
    pair("鞍钢股份", "材料", "sz000898", "00347", "鞍钢"),
    pair("马钢股份", "材料", "sh600808", "00323", "马钢", "马鞍山钢铁"),
    pair("重庆钢铁", "材料", "sh601005", "01053", "重庆钢铁"),
    pair("天齐锂业", "材料", "sz002466", "09696", "天齐锂业"),
    pair("赣锋锂业", "材料", "sz002460", "01772", "赣锋锂业"),
    pair("赤峰黄金", "材料", "sh600988", "06693", "赤峰黄金"),
    pair("晨鸣纸业", "材料", "sz000488", "01812", "晨鸣"),
    # 工业
    pair("中国中车", "工业", "sh601766", "01766", "中国中车"),
    pair("中国中铁", "工业", "sh601390", "00390", "中国中铁"),
    pair("中国铁建", "工业", "sh601186", "01186", "中国铁建"),
    pair("中国交建", "工业", "sh601800", "01800", "中国交建", "交通建设"),
    pair("中国中冶", "工业", "sh601618", "01618", "中国中冶"),
    pair("中国能建", "工业", "sh601868", "03996", "中国能建", "能源建设"),
    pair("中国通号", "工业", "sh688009", "03969", "中国通号"),
    pair("中联重科", "工业", "sz000157", "01157", "中联重科"),
    pair("潍柴动力", "工业", "sz000338", "02338", "潍柴"),
    pair("上海电气", "工业", "sh601727", "02727", "上海电气"),
    pair("东方电气", "工业", "sh600875", "01072", "东方电气"),
    pair("金风科技", "工业", "sz002202", "02208", "金风"),
    pair("中船防务", "工业", "sh600685", "00317", "中船防务"),
    pair("时代电气", "工业", "sh688187", "03898", "时代电"),
    pair("三一重工", "工业", "sh600031", "06031", "三一重工"),
    # 汽车 / 新能源
    pair("比亚迪", "汽车", "sz002594", "01211", "比亚迪"),
    pair("长城汽车", "汽车", "sh601633", "02333", "长城汽车"),
    pair("广汽集团", "汽车", "sh601238", "02238", "广汽"),
    pair("福耀玻璃", "汽车", "sh600660", "03606", "福耀"),
    pair("宁德时代", "新能源", "sz300750", "03750", "宁德时代"),
    # 消费
    pair("青岛啤酒", "消费", "sh600600", "00168", "青岛啤酒"),
    pair("美的集团", "消费", "sz000333", "00300", "美的"),
    pair("海尔智家", "消费", "sh600690", "06690", "海尔智家"),
    pair("海信家电", "消费", "sz000921", "00921", "海信家电"),
    pair("中国中免", "消费", "sh601888", "01880", "中国中免"),
    # 医药
    pair("恒瑞医药", "医药", "sh600276", "01276", "恒瑞"),
    pair("药明康德", "医药", "sh603259", "02359", "药明康德"),
    pair("复星医药", "医药", "sh600196", "02196", "复星"),
    pair("百济神州", "医药", "sh688235", "06160", "百济"),
    pair("君实生物", "医药", "sh688180", "01877", "君实"),
    pair("康龙化成", "医药", "sz300759", "03759", "康龙"),
    pair("泰格医药", "医药", "sz300347", "03347", "泰格"),
    pair("白云山", "医药", "sh600332", "00874", "白云山"),
    pair("上海医药", "医药", "sh601607", "02607", "上海医药"),
    pair("丽珠集团", "医药", "sz000513", "01513", "丽珠"),
    pair("荣昌生物", "医药", "sh688331", "09995", "荣昌"),
    pair("康希诺", "医药", "sh688185", "06185", "康希诺"),
    pair("诺诚健华", "医药", "sh688428", "09969", "诺诚健华"),
    # 通信
    pair("中国联通", "通信", "sh600050", "00762", "中国联通"),
    pair("中国电信", "通信", "sh601728", "00728", "中国电信"),
    pair("中国移动", "通信", "sh600941", "00941", "中国移动"),
    pair("中兴通讯", "通信", "sz000063", "00763", "中兴通讯"),
    pair("中芯国际", "通信", "sh688981", "00981", "中芯国际"),
    pair("华虹公司", "通信", "sh688347", "01347", "华虹"),
    # 交运
    pair("中国国航", "交运", "sh601111", "00753", "中国国航"),
    pair("南方航空", "交运", "sh600029", "01055", "南方航空"),
    pair("中国东航", "交运", "sh600115", "00670", "东航", "东方航空"),
    pair("中远海控", "交运", "sh601919", "01919", "中远海控"),
    pair("中远海能", "交运", "sh600026", "01138", "中远海能"),
    pair("中远海发", "交运", "sh601866", "02866", "中远海发"),
    pair("中国外运", "交运", "sh601598", "00598", "中国外运"),
    pair("中集集团", "交运", "sz000039", "02039", "中集"),
    pair("青岛港", "交运", "sh601298", "06198", "青岛港"),
    pair("广深铁路", "交运", "sh601333", "00525", "广深铁路"),
    pair("宁沪高速", "交运", "sh600377", "00177", "宁沪"),
    pair("深高速", "交运", "sh600548", "00548", "深高速", "深圳高速"),
    pair("皖通高速", "交运", "sh600012", "00995", "皖通"),
    pair("四川成渝", "交运", "sh601107", "00107", "四川成渝"),
    # 公用事业 / 建材 / 地产
    pair("华能国际", "公用事业", "sh600011", "00902", "华能"),
    pair("华电国际", "公用事业", "sh600027", "01071", "华电"),
    pair("大唐发电", "公用事业", "sh601991", "00991", "大唐发电"),
    pair("中国广核", "公用事业", "sz003816", "01816", "广核"),
    pair("龙源电力", "公用事业", "sz001289", "00916", "龙源"),
    pair("海螺水泥", "建材", "sh600585", "00914", "海螺水泥"),
    pair("华新建材", "建材", "sh600801", "06655", "华新建材"),
    pair("万科", "地产", "sz000002", "02202", "万科"),
    pair("金隅集团", "地产", "sh601992", "02009", "金隅"),
    pair("北辰实业", "地产", "sh601588", "00588", "北辰"),
]


def h_symbol(h_code: str) -> str:
    return f"hk{h_code}"


def a_code(a_symbol: str) -> str:
    return a_symbol[-6:]


def norm_name(name: str) -> str:
    """Drop spaces, fullwidth A, and short-name prefixes (ST / XD)."""
    text = (name or "").replace(" ", "").replace("\u3000", "").replace("Ａ", "A").replace("ａ", "a")
    for prefix in ("*ST", "S*ST", "SST", "ST", "XD", "XR", "DR"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    return text


def side_ok(raw: str, key: str, aliases=()) -> bool:
    """True when the quote name is this issuer, including truncated XD names."""
    folded = norm_name(raw)
    if key and (key in folded or (len(folded) >= 3 and key.startswith(folded))):
        return True
    compact = (raw or "").replace(" ", "")
    for alias in aliases:
        if alias and alias.lower() == compact.lower():
            return True
    return False


def names_match(a_name: str, h_name: str, item: dict, h_en: str = "") -> bool:
    """Reject a quote whose name is a different issuer, even if the code matched."""
    for token in item.get("reject") or ():
        if token and (token in norm_name(a_name) or token in norm_name(h_name)):
            return False
    if not side_ok(a_name, item["a_key"]):
        return False
    aliases = item.get("h_aliases") or ()
    return side_ok(h_name, item["h_key"], aliases) or side_ok(h_en, item["h_key"], aliases)


def validate_universe(pairs=None) -> None:
    rows = PAIRS if pairs is None else pairs
    seen_a = set()
    seen_h = set()
    if not rows:
        raise ValueError("universe is empty")
    for item in rows:
        if item["sector"] not in SECTORS:
            raise ValueError(f"{item['name']} has unknown sector {item['sector']}")
        if not _A_RE.fullmatch(item["a_symbol"]):
            raise ValueError(f"{item['name']} bad A symbol {item['a_symbol']}")
        if not _H_RE.fullmatch(item["h_code"]):
            raise ValueError(f"{item['name']} bad H code {item['h_code']}")
        if item["a_symbol"] in seen_a:
            raise ValueError(f"duplicate A symbol {item['a_symbol']}")
        if item["h_code"] in seen_h:
            raise ValueError(f"duplicate H code {item['h_code']}")
        if not item["a_key"] or not item["h_key"]:
            raise ValueError(f"{item['name']} missing name key")
        seen_a.add(item["a_symbol"])
        seen_h.add(item["h_code"])


validate_universe()
