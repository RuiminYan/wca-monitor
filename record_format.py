"""
纪录快讯消息格式化 — WCA Live 与 cubing.com 共用。

模板规则记在 breaking_news_prompt.md(不存在源文件时按本文件逻辑为准):
  WR 中文: 纪录快讯! [成绩][项目][单次/平均]世界纪录WR [人名][国旗]| [比赛]
  WR 英文: BREAKING NEWS! [成绩] [项目] WR [Single/Avg] [人名][国旗]| [比赛]
  CR 中文: 纪录快讯! [成绩][项目][单次/平均][纪录类型][纪录缩写] [人名][国旗]| [比赛]
  CR 英文: Breaking News! [成绩] [项目] [纪录缩写] [Single/Avg] [人名][国旗]| [比赛]
  NR 中文: 纪录快讯! [成绩][项目][单次/平均][国家中文名]纪录[国旗]NR [人名] | [比赛]
  NR 英文: Breaking News! [成绩] [项目][国旗]NR [Single/Avg] [人名] | [比赛]
"""
import re

from monitor_utils import country_flag
from wca_rankings import RANKINGS


# === 项目名映射 ===

EVENT_CN_MAP = {
    "3x3x3 Cube": "三阶魔方",
    "2x2x2 Cube": "二阶魔方",
    "4x4x4 Cube": "四阶魔方",
    "5x5x5 Cube": "五阶魔方",
    "6x6x6 Cube": "六阶魔方",
    "7x7x7 Cube": "七阶魔方",
    "3x3x3 Blindfolded": "三盲",
    "3x3x3 Fewest Moves": "最少步",
    "3x3x3 One-Handed": "三阶魔方单手",
    "Clock": "魔表",
    "Megaminx": "五魔",
    "Pyraminx": "金字塔魔方",
    "Skewb": "斜转魔方",
    "Square-1": " SQ1魔方",  # NOTE: prompt 明确要求中文 SQ1 前必须有空格
    "4x4x4 Blindfolded": "四盲",
    "5x5x5 Blindfolded": "五盲",
    "3x3x3 Multi-Blind": "多盲",
}

EVENT_EN_MAP = {
    "3x3x3 Cube": "3x3",
    "2x2x2 Cube": "2x2",
    "4x4x4 Cube": "4x4",
    "5x5x5 Cube": "5x5",
    "6x6x6 Cube": "6x6",
    "7x7x7 Cube": "7x7",
    "3x3x3 Blindfolded": "3BLD",
    "3x3x3 Fewest Moves": "FMC",
    "3x3x3 One-Handed": "OH",
    "Clock": "Clock",
    "Megaminx": "Megaminx",
    "Pyraminx": "Pyraminx",
    "Skewb": "Skewb",
    "Square-1": "SQ1",
    "4x4x4 Blindfolded": "4BLD",
    "5x5x5 Blindfolded": "5BLD",
    "3x3x3 Multi-Blind": "3BLD",
}

# WCA event id → 标准英文 event name(cubing.com 只给 id 不给名字)
EVENT_NAME_BY_ID = {
    "333": "3x3x3 Cube",
    "222": "2x2x2 Cube",
    "444": "4x4x4 Cube",
    "555": "5x5x5 Cube",
    "666": "6x6x6 Cube",
    "777": "7x7x7 Cube",
    "333bf": "3x3x3 Blindfolded",
    "333fm": "3x3x3 Fewest Moves",
    "333oh": "3x3x3 One-Handed",
    "clock": "Clock",
    "minx": "Megaminx",
    "pyram": "Pyraminx",
    "skewb": "Skewb",
    "sq1": "Square-1",
    "444bf": "4x4x4 Blindfolded",
    "555bf": "5x5x5 Blindfolded",
    "333mbf": "3x3x3 Multi-Blind",
}


# === 洲际/国家映射 ===

CR_ABBR_CN = {
    "AsR": "亚洲纪录",
    "ER": "欧洲纪录",
    "AfR": "非洲纪录",
    "OcR": "大洋洲纪录",
    "SAR": "南美洲纪录",
    "NAR": "北美洲纪录",
}

COUNTRY_CN_MAP = {
    # 亚洲 (AsR)
    "AF": "阿富汗", "AM": "亚美尼亚", "AZ": "阿塞拜疆", "BD": "孟加拉国",
    "BH": "巴林", "BN": "文莱", "BT": "不丹", "CN": "中国",
    "CY": "塞浦路斯", "GE": "格鲁吉亚", "HK": "中国香港", "ID": "印度尼西亚",
    "IL": "以色列", "IN": "印度", "IQ": "伊拉克", "IR": "伊朗",
    "JO": "约旦", "JP": "日本", "KG": "吉尔吉斯斯坦", "KH": "柬埔寨",
    "KR": "韩国", "KW": "科威特", "KZ": "哈萨克斯坦", "LA": "老挝",
    "LB": "黎巴嫩", "LK": "斯里兰卡", "MM": "缅甸", "MN": "蒙古",
    "MO": "中国澳门", "MY": "马来西亚", "NP": "尼泊尔", "OM": "阿曼",
    "PH": "菲律宾", "PK": "巴基斯坦", "QA": "卡塔尔", "SA": "沙特阿拉伯",
    "SG": "新加坡", "SY": "叙利亚", "TH": "泰国", "TJ": "塔吉克斯坦",
    "TM": "土库曼斯坦", "TW": "中国台湾", "UZ": "乌兹别克斯坦", "VN": "越南",
    "AE": "阿联酋", "YE": "也门",
    # 欧洲 (ER)
    "AL": "阿尔巴尼亚", "AT": "奥地利", "BA": "波黑", "BE": "比利时",
    "BG": "保加利亚", "BY": "白俄罗斯", "CH": "瑞士", "CZ": "捷克",
    "DE": "德国", "DK": "丹麦", "EE": "爱沙尼亚", "ES": "西班牙",
    "FI": "芬兰", "FR": "法国", "GB": "英国", "GR": "希腊",
    "HR": "克罗地亚", "HU": "匈牙利", "IE": "爱尔兰", "IS": "冰岛",
    "IT": "意大利", "LT": "立陶宛", "LU": "卢森堡", "LV": "拉脱维亚",
    "MD": "摩尔多瓦", "ME": "黑山", "MK": "北马其顿", "MT": "马耳他",
    "NL": "荷兰", "NO": "挪威", "PL": "波兰", "PT": "葡萄牙",
    "RO": "罗马尼亚", "RS": "塞尔维亚", "RU": "俄罗斯", "SE": "瑞典",
    "SI": "斯洛文尼亚", "SK": "斯洛伐克", "TR": "土耳其", "UA": "乌克兰",
    "XK": "科索沃",
    # 非洲 (AfR)
    "AO": "安哥拉", "BF": "布基纳法索", "BJ": "贝宁", "BW": "博茨瓦纳",
    "CD": "刚果(金)", "CI": "科特迪瓦", "CM": "喀麦隆", "DZ": "阿尔及利亚",
    "EG": "埃及", "ET": "埃塞俄比亚", "GA": "加蓬", "GH": "加纳",
    "GM": "冈比亚", "GN": "几内亚", "KE": "肯尼亚", "LR": "利比里亚",
    "LS": "莱索托", "LY": "利比亚", "MA": "摩洛哥", "MG": "马达加斯加",
    "ML": "马里", "MU": "毛里求斯", "MW": "马拉维", "MZ": "莫桑比克",
    "NA": "纳米比亚", "NE": "尼日尔", "NG": "尼日利亚", "RW": "卢旺达",
    "SD": "苏丹", "SL": "塞拉利昂", "SN": "塞内加尔", "SS": "南苏丹",
    "TD": "乍得", "TG": "多哥", "TN": "突尼斯", "TZ": "坦桑尼亚",
    "UG": "乌干达", "ZA": "南非", "ZW": "津巴布韦",
    # 大洋洲 (OcR)
    "AU": "澳大利亚", "FJ": "斐济", "GU": "关岛", "NC": "新喀里多尼亚",
    "NZ": "新西兰", "PF": "法属波利尼西亚", "PG": "巴布亚新几内亚",
    "SB": "所罗门群岛", "TO": "汤加", "VU": "瓦努阿图", "WS": "萨摩亚",
    # 南美洲 (SAR)
    "AR": "阿根廷", "BO": "玻利维亚", "BR": "巴西", "CL": "智利",
    "CO": "哥伦比亚", "EC": "厄瓜多尔", "GY": "圭亚那", "PE": "秘鲁",
    "PY": "巴拉圭", "SR": "苏里南", "UY": "乌拉圭", "VE": "委内瑞拉",
    # 北美洲 (NAR)
    "AG": "安提瓜和巴布达", "AW": "阿鲁巴", "BB": "巴巴多斯", "BS": "巴哈马",
    "BZ": "伯利兹", "CA": "加拿大", "CR": "哥斯达黎加", "CU": "古巴",
    "CW": "库拉索", "DM": "多米尼克", "DO": "多米尼加", "GD": "格林纳达",
    "GT": "危地马拉", "HN": "洪都拉斯", "HT": "海地", "JM": "牙买加",
    "KN": "圣基茨和尼维斯", "KY": "开曼群岛", "LC": "圣卢西亚", "MX": "墨西哥",
    "NI": "尼加拉瓜", "PA": "巴拿马", "PR": "波多黎各", "SV": "萨尔瓦多",
    "TC": "特克斯和凯科斯群岛", "TT": "特立尼达和多巴哥", "US": "美国",
    "VC": "圣文森特和格林纳丁斯", "VI": "美属维尔京群岛",
}

# ISO2 → 洲际纪录缩写
_CONTINENT_COUNTRIES = {
    "AsR": (
        "AF,AM,AZ,BD,BH,BN,BT,CN,CY,GE,HK,ID,IL,IN,IQ,IR,JO,JP,KG,KH,"
        "KR,KW,KZ,LA,LB,LK,MM,MN,MO,MY,NP,OM,PH,PK,QA,SA,SG,SY,TH,TJ,"
        "TM,TW,UZ,VN,AE,YE"
    ),
    "ER": (
        "AL,AT,BA,BE,BG,BY,CH,CZ,DE,DK,EE,ES,FI,FR,GB,GR,HR,HU,IE,IS,"
        "IT,LT,LU,LV,MD,ME,MK,MT,NL,NO,PL,PT,RO,RS,RU,SE,SI,SK,TR,UA,XK"
    ),
    "AfR": (
        "AO,BF,BJ,BW,CD,CI,CM,DZ,EG,ET,GA,GH,GM,GN,KE,LR,LS,LY,MA,MG,"
        "ML,MU,MW,MZ,NA,NE,NG,RW,SD,SL,SN,SS,TD,TG,TN,TZ,UG,ZA,ZW"
    ),
    "OcR": "AU,FJ,GU,NC,NZ,PF,PG,SB,TO,VU,WS",
    "SAR": "AR,BO,BR,CL,CO,EC,GY,PE,PY,SR,UY,VE",
    "NAR": (
        "AG,AW,BB,BS,BZ,CA,CR,CU,CW,DM,DO,GD,GT,HN,HT,JM,KN,KY,LC,MX,"
        "NI,PA,PR,SV,TC,TT,US,VC,VI"
    ),
}

ISO2_TO_CR = {}
for _abbr, _countries in _CONTINENT_COUNTRIES.items():
    for _iso2 in _countries.split(","):
        ISO2_TO_CR[_iso2.strip()] = _abbr

# 反向:用于 cubing.com region 英文国名 → iso2
COUNTRY_EN_MAP = {
    "China": "CN", "Hong Kong": "HK", "Taiwan": "TW", "Macau": "MO", "Macao": "MO",
    "Japan": "JP", "Korea": "KR", "South Korea": "KR", "North Korea": "KP",
    "Singapore": "SG", "Malaysia": "MY", "Thailand": "TH", "Vietnam": "VN",
    "Indonesia": "ID", "Philippines": "PH", "India": "IN", "Pakistan": "PK",
    "Mongolia": "MN", "Kazakhstan": "KZ", "Uzbekistan": "UZ",
    "United States": "US", "USA": "US", "Canada": "CA", "Mexico": "MX",
    "United Kingdom": "GB", "France": "FR", "Germany": "DE", "Italy": "IT",
    "Spain": "ES", "Russia": "RU", "Poland": "PL", "Netherlands": "NL",
    "Australia": "AU", "New Zealand": "NZ", "Brazil": "BR",
}


# === 名字拆分 ===

_NAME_PAREN_RE = re.compile(r"\s*\(([^)]+)\)\s*$")
_CJK_NAME_COUNTRIES = {"CN", "HK", "TW", "MO"}


def split_name(full_name: str, iso2: str):
    """
    拆分 WCA 名字为 (cn_name, en_name)。
    中国/港澳台选手 cn_name 取括号内的中文名,en_name 取英文名。
    其他选手两者均为英文名。
    """
    m = _NAME_PAREN_RE.search(full_name)
    en_name = _NAME_PAREN_RE.sub("", full_name)
    if m and iso2 in _CJK_NAME_COUNTRIES:
        return m.group(1), en_name
    return en_name, en_name


# === 时间格式化 ===

def format_time(centiseconds: int, event_id: str) -> str:
    """WCA 厘秒成绩 → 可读字符串。FMC / 多盲有独立编码。"""
    if centiseconds <= 0:
        return "DNF"
    if event_id == "333fm":
        # FMC: 单次=步数, 平均=步数*100
        if centiseconds < 1000:
            return f"{centiseconds}"
        return f"{centiseconds / 100:.2f}"
    if event_id == "333mbf":
        # 多盲编码 0DDTTTTTMM(D=差值, T=时间秒, M=错误数)
        dd = 99 - (centiseconds // 10000000)
        ttttt = (centiseconds // 100) % 100000
        mm = centiseconds % 100
        solved = dd + mm
        minutes = ttttt // 60
        seconds = ttttt % 60
        return f"{solved}/{solved + mm} {minutes}:{seconds:02d}"
    total_seconds = centiseconds / 100
    if total_seconds >= 60:
        minutes = int(total_seconds // 60)
        secs = total_seconds - minutes * 60
        return f"{minutes}:{secs:05.2f}"
    return f"{total_seconds:.2f}"


# === 主格式化函数 ===

def format_record_message(
    *,
    tag: str,
    rec_type: str,
    attempt_result: int,
    event_id: str,
    event_name: str,
    person_name: str,
    person_iso2: str,
    person_country_en: str,
    comp_name: str,
    comp_iso2: str,
    url: str,
):
    """
    参数化的纪录消息格式化。两个 monitor 共用。

    tag 取值:
      "WR"                            — 世界纪录
      "NR"                            — 国家纪录
      "CR" | "AsR" | "ER" | "AfR"
        | "OcR" | "SAR" | "NAR"       — 洲际纪录(CR 表示由 iso2 推导,其余直接使用)

    返回 (cn_text, en_text, url)。
    """
    comp_flag = country_flag(comp_iso2)
    person_flag = country_flag(person_iso2)
    cn_name, en_name = split_name(person_name, person_iso2)
    time_str = format_time(attempt_result, event_id)
    cn_event = EVENT_CN_MAP.get(event_name, event_name)
    en_event = EVENT_EN_MAP.get(event_name, event_name)
    type_cn = "单次" if rec_type == "single" else "平均"
    type_en = "Single" if rec_type == "single" else "Avg"
    comp_label = f"{comp_name}{comp_flag}"

    if tag == "WR":
        cn = f"纪录快讯! {time_str}{cn_event}{type_cn}世界纪录WR {cn_name}{person_flag}| {comp_label}"
        en = f"BREAKING NEWS! {time_str} {en_event} WR {type_en} {en_name}{person_flag}| {comp_label}"
        cr_abbr = None
    elif tag == "NR":
        country_cn = COUNTRY_CN_MAP.get(person_iso2, person_country_en or person_iso2)
        cn = f"纪录快讯! {time_str}{cn_event}{type_cn}{country_cn}纪录{person_flag}NR {cn_name} | {comp_label}"
        en = f"Breaking News! {time_str} {en_event}{person_flag}NR {type_en} {en_name} | {comp_label}"
        cr_abbr = None
    else:
        cr_abbr = tag if tag in CR_ABBR_CN else ISO2_TO_CR.get(person_iso2, "CR")
        cr_cn = CR_ABBR_CN.get(cr_abbr, "洲际纪录")
        cn = f"纪录快讯! {time_str}{cn_event}{type_cn}{cr_cn}{cr_abbr} {cn_name}{person_flag}| {comp_label}"
        en = f"Breaking News! {time_str} {en_event} {cr_abbr} {type_en} {en_name}{person_flag}| {comp_label}"

    # 在 NR / CR 后追加 /WRxx(WR 已含"世界纪录"字样,不再叠加)
    if tag != "WR":
        rank = RANKINGS.get_world_rank(event_id, rec_type, attempt_result)
        if rank:
            suffix = f"/WR{rank}"
            if tag == "NR":
                cn = cn.replace("NR", f"NR{suffix}", 1)
                en = en.replace("NR", f"NR{suffix}", 1)
            else:
                cn = cn.replace(cr_abbr, f"{cr_abbr}{suffix}", 1)
                en = en.replace(cr_abbr, f"{cr_abbr}{suffix}", 1)

    return cn, en, url
