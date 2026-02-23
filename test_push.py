"""
WCA 监控套件推送测试工具

用法：
  python test_push.py record              # 推送最新 10 条纪录到手机
  python test_push.py record 3            # 推送最新 3 条纪录
  python test_push.py record --dry-run    # 只查看纪录，不推送
  python test_push.py record --tags WR,CR # 只推送 WR 和 CR（过滤 NR）
  python test_push.py comp                # 推送最新 10 条比赛到手机
  python test_push.py comp 5 --dry-run    # 预览最新 5 条比赛
  python test_push.py record --email      # 推送到手机 + 发邮件
"""
import sys
import time

from wca_record_monitor import (
    load_config,
    query_recent_records,
    format_record_message,
    send_bark_notification as send_bark_record,
)
from cubing_com_monitor import (
    query_competitions as query_cubing_competitions,
    format_comp_message as format_cubing_message,
    send_bark_notification as send_bark_cubing,
)
from wca_comp_monitor import (
    query_competitions as query_wca_competitions,
    format_comp_message as format_wca_message,
)
from monitor_utils import send_bark
from email_notifier import send_email
from wca_rankings import RANKINGS


def test_records(config: dict, count: int, dry_run: bool, with_email: bool = False, tags: set = None):
    """测试纪录快讯推送"""
    print("Initializing world rankings for correct formatting (this takes time)...")
    RANKINGS.update_all()

    print("Querying WCA Live API...")
    records = query_recent_records()
    print(f"Got {len(records)} records from API")

    # 按 tag 过滤（先过滤再取 count 条）
    if tags:
        records = [r for r in records if r["tag"] in tags]
        print(f"Filtered by {'/'.join(sorted(tags))}: {len(records)} records")
    print()

    latest = records[:count]

    for i, record in enumerate(latest):
        cn_text, en_text, url = format_record_message(record)
        print(f"[{i+1}/{len(latest)}] {record['tag']} | {record['id']}")
        print(f"  CN: {cn_text}")
        print(f"  EN: {en_text}")
        print(f"  URL: {url}")

        if not dry_run:
            try:
                send_bark_record(config, cn_text, en_text, url)
                print("  -> Bark OK")
            except Exception as e:
                print(f"  -> Bark FAILED: {e}")
            if with_email:
                send_email(config, cn_text, f"{en_text}\n\n{url}", recipients_key="email_recipients_record")
                print("  -> Email sent")
            if i < len(latest) - 1:
                time.sleep(1)
        print()

    return len(latest)


def test_competitions(config: dict, count: int, dry_run: bool, with_email: bool = False):
    """测试粗饼新比赛推送"""
    print("Querying cubing.com API...")
    comps = query_cubing_competitions()
    print(f"Got {len(comps)} competitions from API\n")

    latest = comps[:count]

    for i, comp in enumerate(latest):
        title, body, url = format_cubing_message(comp)
        print(f"[{i+1}/{len(latest)}] {comp.get('name', '?')}")
        print(f"  Title: {title}")
        print(f"  Body:  {body}")
        print(f"  URL:   {url}")

        if not dry_run:
            try:
                send_bark_cubing(config, title, body, url)
                print("  -> Bark OK")
            except Exception as e:
                print(f"  -> Bark FAILED: {e}")
            if with_email:
                send_email(config, title, f"{body}\n\n{url}", recipients_key="email_recipients_competition")
                print("  -> Email sent")
            if i < len(latest) - 1:
                time.sleep(1)
        print()

    return len(latest)


def test_wca_competitions(config: dict, count: int, dry_run: bool, with_email: bool = False):
    """测试 WCA 官方比赛推送"""
    print("Querying WCA API...")
    comps = query_wca_competitions()
    print(f"Got {len(comps)} competitions from WCA API\n")

    latest = comps[:count]

    for i, comp in enumerate(latest):
        title, body, url = format_wca_message(comp)
        print(f"[{i+1}/{len(latest)}] {comp.get('name', '?')}")
        print(f"  Title: {title}")
        print(f"  Body:  {body}")
        print(f"  URL:   {url}")

        if not dry_run:
            try:
                send_bark(config, title, body, url, "wca-comp")
                print("  -> Bark OK")
            except Exception as e:
                print(f"  -> Bark FAILED: {e}")
            if with_email:
                send_email(config, title, f"{body}\n\n{url}", recipients_key="email_recipients_competition")
                print("  -> Email sent")
            if i < len(latest) - 1:
                time.sleep(1)
        print()

    return len(latest)


USAGE = """Usage: python test_push.py <command> [count] [--dry-run] [--email] [--tags WR,CR]

Commands:
  record     Test WCA record notifications
  comp       Test cubing.com competition notifications
  wca-comp   Test WCA competition notifications

Options:
  count        Number of items to push (default: 10)
  --dry-run    Preview only, do not push
  --email      Also send email notifications
  --tags X,Y   Filter records by tag (WR/CR/NR, comma-separated)

Examples:
  python test_push.py record --dry-run
  python test_push.py record --tags WR,CR
  python test_push.py comp 5
  python test_push.py wca-comp 3 --dry-run
  python test_push.py record 3 --email"""


def main():
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        print(USAGE)
        return

    # 解析标志参数
    dry_run = "--dry-run" in args
    with_email = "--email" in args

    # 解析 --tags 参数（如 --tags WR,CR）
    tags = None
    if "--tags" in args:
        idx = args.index("--tags")
        if idx + 1 < len(args):
            tags = set(args[idx + 1].upper().split(","))
            args = args[:idx] + args[idx + 2:]  # 移除 --tags 及其值
        else:
            args = args[:idx]

    args = [a for a in args if not a.startswith("--")]

    # 解析子命令，默认 record
    command = "record"
    if args and args[0] in ("record", "comp", "wca-comp"):
        command = args.pop(0)

    # 解析数量
    count = int(args[0]) if args else 10

    config = load_config()

    if command == "record":
        total = test_records(config, count, dry_run, with_email, tags)
    elif command == "wca-comp":
        total = test_wca_competitions(config, count, dry_run, with_email)
    else:
        total = test_competitions(config, count, dry_run, with_email)

    parts = []
    if dry_run:
        parts.append("DRY RUN")
    else:
        parts.append("Bark")
        if with_email:
            parts.append("Email")
    print(f"Done: {total} items [{' + '.join(parts)}]")


if __name__ == "__main__":
    main()
