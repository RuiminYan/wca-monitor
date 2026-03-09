"""
WCA Live 纪录监控工具

每隔指定时间轮询 WCA Live GraphQL API，检测新的 WR/CR 纪录，
通过 Bark 推送通知到 iPhone。

用法：
  python wca_record_monitor.py

首次运行前，需要在同目录下创建配置文件 config.json：
  {
    "bark_device_key": "你的Bark设备密钥",
    "bark_server": "https://api.day.app",
    "poll_interval": 30,
    "tags": ["WR", "CR"]
  }

Bark 设备密钥获取方式：打开 iPhone 上的 Bark App，首页会显示你的推送 URL，
例如 https://api.day.app/XXXXXX/测试内容，其中 XXXXXX 就是 device_key。
"""

import json
import time
import signal
import sys
import logging
from pathlib import Path
from datetime import datetime

import requests

from email_notifier import send_email
from monitor_utils import (
    load_config, load_known_ids, save_known_ids, send_bark,
    country_flag, GracefulKiller, poll_wait, setup_logging,
    SCRIPT_DIR,
)
from wca_rankings import RANKINGS

# === 常量 ===

# 持久化文件：记录已通知过的纪录 ID，避免重复推送
KNOWN_IDS_FILE = SCRIPT_DIR / "known_ids.json"

WCA_LIVE_API = "https://live.worldcubeassociation.org/api"

# GraphQL 查询：获取近期纪录的完整信息
RECORDS_QUERY = """
{
  recentRecords {
    id
    tag
    type
    attemptResult
    result {
      person {
        name
        country {
          name
          iso2
        }
      }
      round {
        id
        name
        competitionEvent {
          event {
            id
            name
          }
          competition {
            id
            name
            venues {
              country {
                iso2
              }
            }
          }
        }
      }
    }
  }
}
"""

# 项目名称映射（严格对照 breaking_news_prompt.md 对照表）
# NOTE: 中文 SQ1 前必须有空格（prompt 明确要求）
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
    "Square-1": " SQ1魔方",
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

# 洲际纪录缩写及中文名（CR → 具体洲缩写）
CR_ABBR_CN = {
    "AsR": "亚洲纪录",
    "ER": "欧洲纪录",
    "AfR": "非洲纪录",
    "OcR": "大洋洲纪录",
    "SAR": "南美洲纪录",
    "NAR": "北美洲纪录",
}

# NR 国家/地区中文名映射（覆盖 WCA 所有参赛国家，避免中文通知出现英文国名）
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

# ISO2 国家代码 → 洲际纪录缩写
# NOTE: 用反向定义减少代码量，启动时展开为 {iso2: abbr} 映射
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

# 启动时展开为 {"CN": "AsR", "JP": "AsR", ...}
ISO2_TO_CR = {}
for _abbr, _countries in _CONTINENT_COUNTRIES.items():
    for _iso2 in _countries.split(","):
        ISO2_TO_CR[_iso2.strip()] = _abbr

# NOTE: country_flag 已移至 monitor_utils.py


import re
# 匹配名字末尾的括号部分，如 "Ziyu Wu (吴子钰)"
_NAME_PAREN_RE = re.compile(r"\s*\(([^)]+)\)\s*$")

# 使用中文本名的国家/地区代码
_CJK_NAME_COUNTRIES = {"CN", "HK", "TW", "MO"}

def split_name(full_name: str, iso2: str) -> "Tuple[str, str]":
    """
    拆分 WCA 名字为 (cn_name, en_name)。
    中国/港澳台选手：cn_name 用括号内的中文名，en_name 用英文名。
    其他选手：两者相同，均为英文名。
    """
    m = _NAME_PAREN_RE.search(full_name)
    en_name = _NAME_PAREN_RE.sub("", full_name)
    if m and iso2 in _CJK_NAME_COUNTRIES:
        return m.group(1), en_name  # (中文名, 英文名)
    return en_name, en_name


# === 日志 ===

log = setup_logging("wca_monitor")


# === 核心函数 ===

# NOTE: load_config, load_known_ids, save_known_ids 已移至 monitor_utils.py


def query_recent_records() -> list:
    """查询 WCA Live 最近的纪录列表"""
    resp = requests.post(
        WCA_LIVE_API,
        json={"query": RECORDS_QUERY},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", {}).get("recentRecords", [])


def format_time(centiseconds: int, event_id: str) -> str:
    """
    将 WCA 成绩（厘秒）格式化为可读字符串。
    特殊项目：FMC 成绩单位是步数，多盲有独立编码。
    """
    if centiseconds <= 0:
        return "DNF"

    # FMC：成绩直接就是步数（单次）或步数*100（平均）
    if event_id == "333fm":
        if centiseconds < 1000:
            return f"{centiseconds}"
        return f"{centiseconds / 100:.2f}"

    # 多盲：编码格式 0DDTTTTTMM（D=差值，T=时间秒，M=错误数）
    if event_id == "333mbf":
        dd = 99 - (centiseconds // 10000000)  # 解题数 - 错误数
        ttttt = (centiseconds // 100) % 100000  # 用时（秒）
        mm = centiseconds % 100  # 错误数
        solved = dd + mm
        minutes = ttttt // 60
        seconds = ttttt % 60
        return f"{solved}/{solved + mm} {minutes}:{seconds:02d}"

    # 常规项目：厘秒 → 分:秒.毫秒
    total_seconds = centiseconds / 100
    if total_seconds >= 60:
        minutes = int(total_seconds // 60)
        secs = total_seconds - minutes * 60
        return f"{minutes}:{secs:05.2f}"
    return f"{total_seconds:.2f}"


def format_record_message(record: dict) -> "Tuple[str, str]":
    """
    严格按照 breaking_news_prompt.md 模板格式化纪录。
    返回 (cn, en, url)：cn=中文格式，en=英文格式，url=比赛轮次页面链接。

    WR 中文模板: 纪录快讯! [成绩][项目][单次/平均]WR [人名][国旗]| [比赛]
    WR 英文模板: BREAKING NEWS! [成绩] [项目] WR [Single/Avg] [人名][国旗]| [比赛]
    CR 中文模板: 纪录快讯! [成绩][项目][单次/平均][纪录类型][纪录缩写] [人名][国旗]| [比赛]
    CR 英文模板: Breaking News! [成绩] [项目] [纪录缩写] [Single/Avg] [人名][国旗]| [比赛]
    """
    tag = record["tag"]  # WR / CR
    rec_type = record["type"]  # single / average
    result = record["result"]
    person = result["person"]
    round_obj = result["round"]
    event = round_obj["competitionEvent"]["event"]
    competition = round_obj["competitionEvent"]["competition"]

    # 比赛所在国家（从第一个 venue 获取）
    venues = competition.get("venues", [])
    comp_iso2 = venues[0]["country"]["iso2"] if venues else ""
    comp_flag = country_flag(comp_iso2)

    iso2 = person["country"]["iso2"]
    cn_name, en_name = split_name(person["name"], iso2)
    flag = country_flag(iso2)
    event_name = event["name"]
    event_id = event["id"]
    comp_name = f"{competition['name']}{comp_flag}"
    time_str = format_time(record["attemptResult"], event_id)

    cn_event = EVENT_CN_MAP.get(event_name, event_name)
    en_event = EVENT_EN_MAP.get(event_name, event_name)
    type_cn = "单次" if rec_type == "single" else "平均"
    type_en = "Single" if rec_type == "single" else "Avg"

    if tag == "WR":
        # WR 模板：成绩和项目之间无空格
        cn = f"纪录快讯! {time_str}{cn_event}{type_cn}世界纪录WR {cn_name}{flag}| {comp_name}"
        en = f"BREAKING NEWS! {time_str} {en_event} WR {type_en} {en_name}{flag}| {comp_name}"
    elif tag == "NR":
        # NR 模板：使用中文国名，找不到则回退到 API 返回的英文名
        country_cn = COUNTRY_CN_MAP.get(iso2, person["country"]["name"])
        cn = f"纪录快讯! {time_str}{cn_event}{type_cn}{country_cn}纪录{flag}NR {cn_name} | {comp_name}"
        en = f"Breaking News! {time_str} {en_event} {flag} NR {type_en} {en_name} | {comp_name}"
    else:
        # CR → 推导具体洲际缩写（AsR/ER/AfR 等）
        cr_abbr = ISO2_TO_CR.get(iso2, "CR")
        cr_cn = CR_ABBR_CN.get(cr_abbr, "洲际纪录")
        cn = f"纪录快讯! {time_str}{cn_event}{type_cn}{cr_cn}{cr_abbr} {cn_name}{flag}| {comp_name}"
        en = f"Breaking News! {time_str} {en_event} {cr_abbr} {type_en} {en_name}{flag}| {comp_name}"

    # 尝试获取世界排名 (Top 100)
    # WR 已经包含 "世界纪录" 字样，不需要额外显示 /WR1
    if tag in ("CR", "NR"):
        rank = RANKINGS.get_world_rank(event_id, rec_type, record["attemptResult"])
        if rank:
            # 在 AsR 或 NR 后追加 /WRxx
            # 为了简单起见，直接替换最后出现的 tag (NR/CR/AsR/...)
            # 但模板里位置不固定，不如简单地加在 tag 后面
            # 重新构建字符串太麻烦，不如替换掉特定的部分
            
            # 使用简单的正则替换，或者直接在生成时就决定
            # 为了代码清晰，我们重写一下上面的逻辑
            # 但是为了最小化改动，我们可以这样做：
            suffix = f"/WR{rank}"
            
            # 针对 CR: ...AsR... -> ...AsR/WRxx...
            if tag == "NR":
                cn = cn.replace("NR", f"NR{suffix}", 1)
                en = en.replace("NR", f"NR{suffix}", 1)
            else:
                # CR 的缩写是动态的 (AsR, ER 等)
                cn = cn.replace(cr_abbr, f"{cr_abbr}{suffix}", 1)
                en = en.replace(cr_abbr, f"{cr_abbr}{suffix}", 1)

    # 拼接比赛轮次页面链接
    round_id = round_obj["id"]
    comp_id = competition["id"]
    url = f"https://live.worldcubeassociation.org/competitions/{comp_id}/rounds/{round_id}"

    return cn, en, url


# NOTE: send_bark_notification 已移至 monitor_utils.send_bark

def send_bark_notification(config: dict, cn_text: str, en_text: str, url: str) -> bool:
    """兼容包装：纪录监控的 Bark 推送（标题=中文，正文=英文）"""
    return send_bark(config, cn_text, en_text, url, "WCA Records", sound="multiwayinvitation")


# === 主循环 ===

def main():
    config = load_config()
    known_ids = load_known_ids(KNOWN_IDS_FILE)
    target_tags = set(config["tags"])
    nr_countries = set(config["nr_countries"])  # NR 国家过滤白名单
    interval = config["poll_interval"]

    log.info("=" * 50)
    log.info("WCA Live 纪录监控已启动")
    log.info(f"  监控纪录类型: {', '.join(target_tags)}")
    if "NR" in target_tags and nr_countries:
        log.info(f"  NR 国家过滤: {', '.join(sorted(nr_countries))}")
    log.info(f"  轮询间隔: {interval}s")
    log.info(f"  已知纪录数: {len(known_ids)}")
    log.info("=" * 50)

    killer = GracefulKiller()

    # 首次运行：静默加载当前所有纪录，避免历史纪录触发通知
    is_first_run = len(known_ids) == 0

    # 启动时更新世界排名（Top 100）
    # 优先使用本地缓存（秒级），缓存失效时才请求 WCA 网站（约 30s）
    RANKINGS.update_all()

    while not killer.kill_now:
        try:
            records = query_recent_records()
            important = []
            for r in records:
                tag = r["tag"]
                if tag not in target_tags:
                    continue
                # NR 国家过滤：只推送白名单内的国家
                if tag == "NR" and nr_countries:
                    iso2 = r["result"]["person"]["country"]["iso2"]
                    if iso2 not in nr_countries:
                        continue
                important.append(r)

            new_count = 0
            for record in important:
                rid = record["id"]
                if rid not in known_ids:
                    if is_first_run:
                        # 首次运行不推送，直接记录
                        known_ids.add(rid)
                        new_count += 1
                        continue

                    cn_text, en_text, url = format_record_message(record)
                    log.info(f"🆕 新纪录: {cn_text}")

                    # NOTE: 只有 Bark 推送成功才标记为已知，失败时下次轮询会重试
                    if send_bark_notification(config, cn_text, en_text, url):
                        known_ids.add(rid)
                        new_count += 1
                        # NOTE: 邮件只发 WR，CR/NR 不发邮件
                        if record["tag"] == "WR":
                            send_email(config, cn_text, f"{en_text}\n\n{url}", recipients_key="email_recipients_record")
                    else:
                        log.warning(f"  推送失败，下次轮询将重试: {rid}")

            if is_first_run and new_count > 0:
                log.info(f"首次运行，静默记录了 {new_count} 条现有纪录（不推送）")
                is_first_run = False
                save_known_ids(KNOWN_IDS_FILE, known_ids)
            elif new_count > 0:
                save_known_ids(KNOWN_IDS_FILE, known_ids)
            else:
                log.debug(f"无新纪录 ({len(important)} 条 {'/'.join(target_tags)} 在列)")

        except requests.exceptions.RequestException as e:
            log.warning(f"API 请求失败: {e}")
        except Exception as e:
            log.error(f"未预期的错误: {e}", exc_info=True)

        poll_wait(interval, killer)

    # 退出前保存
    save_known_ids(KNOWN_IDS_FILE, known_ids)
    log.info("监控已停止，状态已保存")


if __name__ == "__main__":
    main()
