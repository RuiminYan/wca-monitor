"""
WCA 比赛视频标题生成工具

优先从 WCA Live recentRecords 匹配纪录并生成纪录快讯标题。
如果不是纪录视频，则回退到 WCA REST API 查询选手和比赛信息，组装通用标题。

用法：
  python gen_title.py                           # 交互模式
  python gen_title.py "5.55 3x3 NR Avg Nahm"    # 命令行模式
  python gen_title.py --list                     # 列出所有近期纪录
"""

import io
import re
import sys

# NOTE: Windows 终端默认 GBK 编码，无法输出国旗 emoji，强制 UTF-8
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import requests

from wca_record_monitor import (
    query_recent_records,
    format_record_message,
    format_time,
    EVENT_CN_MAP,
    EVENT_EN_MAP,
)
from wca_rankings import RANKINGS
from monitor_utils import country_flag


# 将用户输入的项目缩写映射到 WCA event name
# NOTE: 覆盖常见的非规范写法（视频标题中常见的缩写）
_EVENT_ALIAS = {}
for _name, _abbr in EVENT_EN_MAP.items():
    _EVENT_ALIAS[_abbr.lower()] = _name
# 补充常见别名
_EVENT_ALIAS.update({
    "3x3": "3x3x3 Cube",
    "2x2": "2x2x2 Cube",
    "4x4": "4x4x4 Cube",
    "5x5": "5x5x5 Cube",
    "6x6": "6x6x6 Cube",
    "7x7": "7x7x7 Cube",
    "3bld": "3x3x3 Blindfolded",
    "4bld": "4x4x4 Blindfolded",
    "5bld": "5x5x5 Blindfolded",
    "mbld": "3x3x3 Multi-Blind",
    "multi": "3x3x3 Multi-Blind",
    "multibld": "3x3x3 Multi-Blind",
    "fmc": "3x3x3 Fewest Moves",
    "oh": "3x3x3 One-Handed",
    "mega": "Megaminx",
    "megaminx": "Megaminx",
    "pyra": "Pyraminx",
    "pyraminx": "Pyraminx",
    "sq1": "Square-1",
    "square-1": "Square-1",
    "cube": "3x3x3 Cube",
})


def _score_match(record: dict, keywords: list[str]) -> int:
    """
    计算纪录与用户输入关键词的匹配度。
    越高越匹配，0 = 完全不匹配。
    """
    result = record["result"]
    person = result["person"]
    event = result["round"]["competitionEvent"]["event"]
    event_id = event["id"]

    person_name = person["name"].lower()
    event_name = event["name"].lower()
    time_str = format_time(record["attemptResult"], event_id)

    score = 0
    for kw in keywords:
        kw_lower = kw.lower()

        # 选手名匹配（部分匹配即可，如姓氏）
        if kw_lower in person_name:
            score += 10

        # 成绩匹配（精确匹配格式化后的成绩字符串）
        if kw_lower == time_str.lower():
            score += 20

        # 项目匹配：先通过别名表找到标准名，再比较
        matched_event = _EVENT_ALIAS.get(kw_lower)
        if matched_event and matched_event.lower() == event_name:
            score += 5

        # 纪录类型匹配
        tag = record["tag"]
        if kw_lower in ("wr", "world record") and tag == "WR":
            score += 3
        elif kw_lower in ("nr", "national record") and tag == "NR":
            score += 3
        elif kw_lower in ("cr", "continental record") and tag == "CR":
            score += 3

        # 单次/平均匹配
        rec_type = record["type"]
        if kw_lower in ("single", "s") and rec_type == "single":
            score += 2
        elif kw_lower in ("average", "avg", "a") and rec_type == "average":
            score += 2

    return score


def _parse_keywords(text: str) -> list[str]:
    """
    将用户输入拆分为关键词列表。
    保留数字（包括小数）、英文单词，去掉常见废词。
    """
    # 用空格和常见分隔符拆分
    tokens = re.split(r"[\s,|]+", text.strip())
    # NOTE: 去除方括号，如 [3x3] → 3x3（YouTube 标题常见格式）
    tokens = [re.sub(r"[\[\]]", "", t) for t in tokens]
    # 过滤掉纯粹的废词和空串
    stopwords = {"in", "at", "the", "a", "an", "of", "by", "new", "record",
                 "breaking", "news", "official"}
    return [t for t in tokens if t and t.lower() not in stopwords]


# NOTE: 最低匹配分数门槛。
# 真正的纪录视频标题通常含成绩(+20) + 选手名(+10) + 项目(+5) = 35+。
# 阈值 15 可以过滤掉只靠 "single"(+2) 或 "3x3"(+5) 碰巧命中的误匹配。
_MIN_MATCH_SCORE = 15


def find_matching_records(keywords: list[str], records: list[dict]) -> list[tuple[dict, int]]:
    """按匹配度排序返回 [(record, score)]，只返回 score >= _MIN_MATCH_SCORE 的"""
    scored = []
    for r in records:
        s = _score_match(r, keywords)
        if s >= _MIN_MATCH_SCORE:
            scored.append((r, s))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def print_record_summary(record: dict, idx: int = 0):
    """打印纪录的简要信息"""
    tag = record["tag"]
    rec_type = record["type"]
    result = record["result"]
    person = result["person"]
    event = result["round"]["competitionEvent"]["event"]
    comp = result["round"]["competitionEvent"]["competition"]
    time_str = format_time(record["attemptResult"], event["id"])
    type_str = "Single" if rec_type == "single" else "Avg"

    prefix = f"  [{idx}]" if idx else "  "
    print(f"{prefix} {tag} | {time_str} {event['name']} {type_str} | {person['name']} | {comp['name']}")


def strip_prefix(text: str) -> str:
    """
    去掉纪录快讯前缀，只保留纪录内容部分。
    "纪录快讯! 5.55..." → "5.55..."
    "BREAKING NEWS! 5.55..." → "5.55..."
    "Breaking News! 5.55..." → "5.55..."
    """
    for prefix in ["纪录快讯! ", "BREAKING NEWS! ", "Breaking News! "]:
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def print_formatted(record: dict):
    """打印格式化后的标题，可直接复制使用"""
    cn, en, url = format_record_message(record)
    print()
    print(f"  标题: {cn}")
    print(f"  正文: {en}")
    print(f"  链接: {url}")
    print()


def write_info_files(record: dict, out_dir: str):
    """
    将中英文标题写入 info_chs.md 和 info_eng.md 的第一行。
    去掉 "纪录快讯!/Breaking News!" 前缀。
    如果文件已有非空内容则跳过，避免覆盖用户手动编辑。
    """
    from pathlib import Path
    cn, en, url = format_record_message(record)
    cn_title = strip_prefix(cn)
    en_title = strip_prefix(en)

    out = Path(out_dir)
    for fname, content in [("info_chs.md", cn_title), ("info_eng.md", en_title)]:
        fpath = out / fname
        # 如果文件已有非空内容，跳过
        if fpath.exists():
            existing = fpath.read_text(encoding="utf-8").strip()
            if existing:
                print(f"  跳过 {fname}（已有内容）")
                continue
        fpath.write_text(content + "\n", encoding="utf-8")
        print(f"  已写入 {fname}: {content}")


def list_all_records(records: list[dict]):
    """列出所有近期纪录"""
    print(f"\nWCA Live 近期纪录 (共 {len(records)} 条):\n")
    for i, r in enumerate(records, 1):
        print_record_summary(r, i)
    print()


# === WCA REST API 回退逻辑 ===
# NOTE: 当 WCA Live recentRecords 匹配失败时（非纪录视频），
# 用 WCA REST API 查询选手信息和比赛历史来组装通用标题。

WCA_API = "https://www.worldcubeassociation.org/api/v0"

# WCA API event_id → event full name 的映射
_EVENT_ID_TO_NAME = {
    "222": "2x2x2 Cube", "333": "3x3x3 Cube", "444": "4x4x4 Cube",
    "555": "5x5x5 Cube", "666": "6x6x6 Cube", "777": "7x7x7 Cube",
    "333bf": "3x3x3 Blindfolded", "333fm": "3x3x3 Fewest Moves",
    "333oh": "3x3x3 One-Handed", "clock": "Clock", "minx": "Megaminx",
    "pyram": "Pyraminx", "skewb": "Skewb", "sq1": "Square-1",
    "444bf": "4x4x4 Blindfolded", "555bf": "5x5x5 Blindfolded",
    "333mbf": "3x3x3 Multi-Blind",
}


def search_wca_person(name: str) -> dict | None:
    """
    用 WCA REST API 搜索选手，返回 {wca_id, name, country_iso2} 或 None。
    只取 persons_table 中的第一个结果。
    """
    try:
        r = requests.get(
            f"{WCA_API}/search/users",
            params={"q": name, "persons_table": "true"},
            timeout=10,
        )
        r.raise_for_status()
        results = r.json().get("result", [])
        if not results:
            return None
        p = results[0]
        return {
            "wca_id": p["wca_id"],
            "name": p["name"],
            "country_iso2": p["country_iso2"],
        }
    except Exception as e:
        print(f"  WCA API 搜人失败: {e}")
        return None


def find_competition_by_result(
    wca_id: str, time_cs: int, event_id: str, is_average: bool
) -> dict | None:
    """
    用 WCA REST API 查选手成绩，反查出对应的比赛。
    time_cs: 成绩（厘秒），event_id: WCA 项目 ID（如 '333'），
    is_average: True=查 average，False=查 best。
    返回 {comp_id, comp_name, comp_country_iso2} 或 None。
    """
    try:
        # 查选手全部成绩记录
        r = requests.get(f"{WCA_API}/persons/{wca_id}/results", timeout=10)
        r.raise_for_status()
        all_results = r.json()

        # 按成绩 + 项目过滤
        field = "average" if is_average else "best"
        matching_comps = []
        for res in all_results:
            if res["event_id"] == event_id and res.get(field) == time_cs:
                matching_comps.append(res["competition_id"])

        if not matching_comps:
            return None

        # 取最后一个（最近的比赛），API 结果按时间顺序排列
        target_comp_id = matching_comps[-1]

        # 查比赛详情
        r2 = requests.get(f"{WCA_API}/persons/{wca_id}/competitions", timeout=10)
        r2.raise_for_status()
        comps = r2.json()

        for comp in comps:
            if comp["id"] == target_comp_id:
                return {
                    "comp_id": comp["id"],
                    "comp_name": comp["name"],
                    "comp_country_iso2": comp["country_iso2"],
                }

        # 比赛没在列表里（成绩还没上传到 WCA 官网），用 ID 作为名字
        return {
            "comp_id": target_comp_id,
            "comp_name": target_comp_id,
            "comp_country_iso2": "",
        }
    except Exception as e:
        print(f"  WCA API 查成绩失败: {e}")
        return None


def _extract_title_parts(keywords: list[str]) -> dict:
    """
    从关键词列表中分离出 成绩/项目/类型/选手名。
    返回 {time_str, event_name, event_id, rec_type, person_name}。
    """
    time_str = None
    event_name = None
    event_id = None
    rec_type = "single"  # 默认
    name_parts = []

    for kw in keywords:
        kw_lower = kw.lower()

        # 成绩：匹配数字格式（如 4.89, 1:23.45）
        if re.match(r"^\d+[.:][\d.]+$", kw) or re.match(r"^\d+\.\d+$", kw):
            if not time_str:
                time_str = kw
            continue

        # 项目：通过别名表匹配
        matched = _EVENT_ALIAS.get(kw_lower)
        if matched:
            event_name = matched
            # 反查 event_id
            for eid, ename in _EVENT_ID_TO_NAME.items():
                if ename == matched:
                    event_id = eid
                    break
            continue

        # 类型
        if kw_lower in ("single", "s"):
            rec_type = "single"
            continue
        if kw_lower in ("average", "avg", "a", "ao5", "mean", "mo3"):
            rec_type = "average"
            continue

        # 其余当作选手名的一部分
        name_parts.append(kw)

    return {
        "time_str": time_str,
        "event_name": event_name or "3x3x3 Cube",
        "event_id": event_id or "333",
        "rec_type": rec_type,
        "person_name": " ".join(name_parts) if name_parts else None,
    }


def _time_str_to_centiseconds(time_str: str) -> int | None:
    """将时间字符串转换为厘秒。如 '4.89' → 489, '1:23.45' → 8345"""
    try:
        if ":" in time_str:
            parts = time_str.split(":")
            minutes = int(parts[0])
            seconds = float(parts[1])
            # NOTE: 用 round 而非 int 截断，避免浮点精度问题
            # 例如 float('4.89') * 100 = 488.999...  int → 488  round → 489
            return round((minutes * 60 + seconds) * 100)
        else:
            return round(float(time_str) * 100)
    except (ValueError, IndexError):
        return None


def format_general_title(
    time_str: str, event_name: str, rec_type: str,
    person_name: str, person_iso2: str,
    comp_name: str | None, comp_iso2: str | None,
) -> tuple[str, str]:
    """
    组装通用中英文标题（不含纪录前缀）。
    返回 (cn_title, en_title)。
    """
    event_cn = EVENT_CN_MAP.get(event_name, event_name)
    event_en = EVENT_EN_MAP.get(event_name, event_name)
    type_cn = "单次" if rec_type == "single" else "平均"
    type_en = "Single" if rec_type == "single" else "Avg"
    person_flag = country_flag(person_iso2) if person_iso2 else ""

    cn = f"{time_str}{event_cn}{type_cn} {person_name}{person_flag}"
    en = f"{time_str} {event_en} {type_en} {person_name}{person_flag}"

    if comp_name:
        comp_flag = country_flag(comp_iso2) if comp_iso2 else ""
        cn += f" | {comp_name}{comp_flag}"
        en += f" | {comp_name}{comp_flag}"

    return cn, en


def fallback_wca_api(keywords: list[str], write_dir: str | None) -> bool:
    """
    纪录匹配失败后的回退：用 WCA REST API 查询选手和比赛信息。
    成功则输出/写入标题并返回 True，失败返回 False。
    """
    parts = _extract_title_parts(keywords)
    if not parts["person_name"] or not parts["time_str"]:
        return False

    print(f"\n  回退: WCA API 查询 '{parts['person_name']}'...")
    person = search_wca_person(parts["person_name"])
    if not person:
        print("  未找到 WCA 选手")
        return False

    print(f"  找到: {person['name']} ({person['country_iso2']})")

    # 用成绩反查比赛
    time_cs = _time_str_to_centiseconds(parts["time_str"])
    comp = None
    if time_cs:
        is_avg = parts["rec_type"] == "average"
        comp = find_competition_by_result(
            person["wca_id"], time_cs, parts["event_id"], is_avg
        )
        if comp:
            print(f"  比赛: {comp['comp_name']}")
        else:
            print("  未找到对应比赛成绩")

    cn, en = format_general_title(
        parts["time_str"], parts["event_name"], parts["rec_type"],
        person["name"], person["country_iso2"],
        comp["comp_name"] if comp else None,
        comp["comp_country_iso2"] if comp else None,
    )

    print()
    print(f"  标题: {cn}")
    print(f"  正文: {en}")
    print()

    # 写入 info 文件
    if write_dir:
        from pathlib import Path
        out = Path(write_dir)
        for fname, content in [("info_chs.md", cn), ("info_eng.md", en)]:
            fpath = out / fname
            if fpath.exists() and fpath.read_text(encoding="utf-8").strip():
                print(f"  跳过 {fname}（已有内容）")
                continue
            fpath.write_text(content + "\n", encoding="utf-8")
            print(f"  已写入 {fname}: {content}")

    return True


def interactive_mode(records: list[dict]):
    """交互模式：循环输入关键词搜索纪录"""
    print("\n=== 纪录标题生成工具 ===")
    print(f"已加载 {len(records)} 条近期纪录")
    print("输入关键词搜索（如: 5.55 3x3 Nahm），输入 list 列出全部，输入 q 退出\n")

    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input:
            continue
        if user_input.lower() in ("q", "quit", "exit"):
            break
        if user_input.lower() == "list":
            list_all_records(records)
            continue

        keywords = _parse_keywords(user_input)
        if not keywords:
            print("  请输入有效的关键词\n")
            continue

        matches = find_matching_records(keywords, records)

        if not matches:
            print("  未找到匹配纪录，试试其他关键词\n")
            continue

        if len(matches) == 1 or matches[0][1] > matches[1][1] * 1.5:
            # 唯一匹配或最高分明显领先 → 直接输出
            print_formatted(matches[0][0])
        else:
            # 多条匹配 → 列出让用户选择
            print(f"\n  找到 {len(matches)} 条匹配，请选择：\n")
            top = matches[:8]
            for i, (r, s) in enumerate(top, 1):
                print_record_summary(r, i)

            try:
                choice = input("\n  输入编号 (1-{}): ".format(len(top))).strip()
                idx = int(choice) - 1
                if 0 <= idx < len(top):
                    print_formatted(top[idx][0])
                else:
                    print("  无效编号\n")
            except (ValueError, EOFError, KeyboardInterrupt):
                print()
                continue


def main():
    raw_args = sys.argv[1:]

    # 解析 --write <目录> 参数
    write_dir = None
    if "--write" in raw_args:
        wi = raw_args.index("--write")
        if wi + 1 < len(raw_args):
            write_dir = raw_args[wi + 1]
            raw_args = raw_args[:wi] + raw_args[wi + 2:]
        else:
            raw_args = raw_args[:wi]

    # --auto: 非交互模式，匹配失败时静默退出
    auto_mode = "--auto" in raw_args

    args = [a for a in raw_args if not a.startswith("--")]
    flags = [a for a in raw_args if a.startswith("--")]

    # 初始化排名缓存（用于 /WRxx 后缀）
    print("加载世界排名数据...")
    RANKINGS.update_all()

    print("查询 WCA Live 近期纪录...")
    records = query_recent_records()
    print(f"获取到 {len(records)} 条纪录")

    # --list: 列出全部
    if "--list" in flags:
        list_all_records(records)
        return

    # 命令行模式：参数作为关键词
    if args:
        all_text = " ".join(args)
        keywords = _parse_keywords(all_text)

        matches = find_matching_records(keywords, records)
        if not matches:
            # NOTE: 纪录匹配失败 → 回退到 WCA API 查询
            if fallback_wca_api(keywords, write_dir):
                return
            if auto_mode:
                print("未匹配到纪录，跳过")
                return
            print("\n未找到匹配纪录")
            sys.exit(1)

        best = matches[0][0]

        # 多条高分匹配时，auto 模式取第一条，手动模式提示
        if not auto_mode and len(matches) > 1 and matches[1][1] >= matches[0][1] * 0.7:
            print(f"  (还有 {len(matches) - 1} 条可能匹配，用交互模式查看)")

        # 输出最佳匹配
        print_formatted(best)

        # 写入 info 文件
        if write_dir:
            write_info_files(best, write_dir)
        return

    # 无参数 → 交互模式（auto 模式下直接退出）
    if auto_mode:
        print("无关键词，跳过")
        return
    interactive_mode(records)


if __name__ == "__main__":
    main()
