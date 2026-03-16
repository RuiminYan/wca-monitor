"""
纪录标题生成工具

从 WCA Live 的 recentRecords 中匹配目标纪录，生成符合纪录快讯模板的中英文标题。
用于转发 WCA 纪录视频时快速生成规范标题。

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

from wca_record_monitor import (
    query_recent_records,
    format_record_message,
    format_time,
    EVENT_EN_MAP,
)
from wca_rankings import RANKINGS


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
    # 过滤掉纯粹的废词
    stopwords = {"in", "at", "the", "a", "an", "of", "by", "new", "record", "breaking", "news"}
    return [t for t in tokens if t and t.lower() not in stopwords]


def find_matching_records(keywords: list[str], records: list[dict]) -> list[tuple[dict, int]]:
    """按匹配度排序返回 [(record, score)]，只返回 score > 0 的"""
    scored = []
    for r in records:
        s = _score_match(r, keywords)
        if s > 0:
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
