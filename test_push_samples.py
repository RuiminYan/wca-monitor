"""
正式回归测试:Bark 推送 record_format 样本。

**任何改动 record_format / format_combined_records / wca_local_names / 两个 record
monitor 的合并和聚合逻辑后,都应跑一遍这个脚本人肉验证 Bark 通知格式。**

用法:
  python test_push_samples.py              # 真推全部 case 到 Bark(默认 5s 间隔)
  python test_push_samples.py --dry-run    # 只打印 CN/EN,不推送
  python test_push_samples.py --interval 3 # 改间隔(秒)

覆盖规则:
  - 中文本地名补全:Lim Hung (林弘) 等非 CN 国籍的 CJK 名字
  - mean-of-3 项目(6x6 / 7x7 / 333fm / 4BLD / 5BLD)EN 用 "Mean" 而非 "Avg"
  - WR 用 "BREAKING NEWS!" 全大写,其他用 "Breaking News!"
  - 合并模式分隔符:flag 结尾 "| " / 字母结尾 " | "
  - NR 必带国旗,合并消息里国旗只出现一次
  - 不同 tag 合并按 WR > CR > NR 优先级排序
"""
import argparse, io, sys, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from monitor_utils import load_config
from wca_record_monitor import (
    query_recent_records, _record_to_kwargs,
    format_record_message, send_bark_notification,
)
from record_format import format_combined_records
from wca_rankings import RANKINGS


# (round_id, person_substr|None, 描述) — person_substr 用于在同 round 多 person
# 都破纪录时(如 FMC 一轮多人 NR)精确锁定一组
SAMPLES = [
    ("139619", "Lim Hung",  "Lim Hung 6x6 WR Mean(林弘 + Mean)"),
    ("139622", "Lim Hung",  "Lim Hung 7x7 WR+AsR(林弘 合并 + r2 Mean)"),
    ("138886", "Tymon",     "Tymon 5x5 双 WR 同 tag(BREAKING NEWS!)"),
    ("139475", "Tarasenko", "Tarasenko 5x5 双 NR 同 tag(Breaking News!)"),
    ("138735", "Goluboff",  "Goluboff 5x5 SAR+NR 异 tag(去 r1 flag + NR 前 flag)"),
    ("139245", "Zemdegs",   "Zemdegs 7x7 双 OcR(Mean + WR rank suffix)"),
]


def _select(records, rid, person_substr):
    rs = [r for r in records if r["result"]["round"]["id"] == rid]
    if person_substr:
        rs = [r for r in rs if person_substr in r["result"]["person"]["name"]]
    rs.sort(key=lambda r: 0 if r["type"] == "single" else 1)
    return rs


def _format(records_subset):
    if len(records_subset) == 1:
        return format_record_message(records_subset[0])
    return format_combined_records([_record_to_kwargs(r) for r in records_subset])


def _pick_first_fmc_pair(records):
    """从 recentRecords 自动找一个 FMC (333fm) 合并组(单次+平均同 person)"""
    by_key = {}
    for r in records:
        if r["result"]["round"]["competitionEvent"]["event"]["id"] != "333fm":
            continue
        rid = r["result"]["round"]["id"]
        wid = r["result"]["person"].get("wcaId") or r["result"]["person"]["name"]
        by_key.setdefault((rid, wid), []).append(r)
    for k, g in by_key.items():
        if len(g) == 2:
            g.sort(key=lambda r: 0 if r["type"] == "single" else 1)
            return g
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--interval", type=int, default=5, help="推送间隔秒数")
    args = ap.parse_args()

    RANKINGS.update_all()
    cfg = load_config()
    records = query_recent_records()

    items = []
    for rid, person, label in SAMPLES:
        rs = _select(records, rid, person)
        if not rs:
            print(f"[skip] {label}: round {rid} {person or ''} 不在 recentRecords 窗口")
            continue
        cn, en, url = _format(rs)
        items.append((label, cn, en, url))

    # 自动追加一个 FMC 合并样本(round id 会变,不能写死)
    fmc = _pick_first_fmc_pair(records)
    if fmc:
        cn, en, url = _format(fmc)
        items.append(("FMC 合并(Mean+Single 异 tag)", cn, en, url))

    mode = "DRY-RUN" if args.dry_run else "PUSH"
    print(f"\n{mode} {len(items)} samples (interval={args.interval}s)\n")
    for i, (label, cn, en, url) in enumerate(items, 1):
        print(f"[{i}/{len(items)}] {label}")
        print(f"  CN: {cn}")
        print(f"  EN: {en}")
        if args.dry_run:
            print()
            continue
        ok = send_bark_notification(cfg, cn, en, url)
        print(f"  → {'ok' if ok else 'FAIL'}\n")
        if i < len(items):
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
