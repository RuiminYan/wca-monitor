"""
粗饼（cubing.com）新比赛监控
轮询 /api/competition 端点，发现新比赛时通过 Bark 推送通知。
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path

import requests

from email_notifier import send_email
from monitor_utils import (
    load_config, load_known_ids, save_known_ids, send_bark,
    GracefulKiller, poll_wait, setup_logging, SCRIPT_DIR,
)

# === 路径 ===

# NOTE: 和纪录监控共用 config.json，但已知 ID 分开存储
KNOWN_COMPS_PATH = SCRIPT_DIR / "known_comp_ids.json"
COMPS_JSON_PATH = SCRIPT_DIR / "cubing_competitions.json"

# === 日志 ===

log = setup_logging("cubing_comp")

# === API ===

CUBING_API = "https://cubing.com/api/competition"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


# NOTE: load_config, load_known_ids, save_known_ids 已移至 monitor_utils.py


def query_competitions() -> list:
    """查询粗饼比赛列表"""
    for attempt in range(3):
        try:
            r = requests.get(CUBING_API, headers=HEADERS, timeout=15)
            if r.status_code == 200 and r.text.strip():
                data = r.json()
                return data.get("data", [])
            log.warning("API returned %d, retry %d/3", r.status_code, attempt + 1)
        except requests.RequestException as e:
            log.warning("Request failed: %s, retry %d/3", e, attempt + 1)
        time.sleep(3)
    return []


def format_date(timestamp: int) -> str:
    """Unix 时间戳转日期字符串"""
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")


def format_comp_message(comp: dict) -> "Tuple[str, str, str]":
    """
    格式化比赛通知。
    返回 (title, body, url)。
    """
    name = comp["name"]
    date_from = format_date(comp["date"]["from"])
    date_to = format_date(comp["date"]["to"])
    comp_type = comp.get("type", "")  # WCA / other

    # 地点信息
    locations = comp.get("locations", [])
    if locations:
        loc = locations[0]
        city = f"{loc.get('province', '')}{loc.get('city', '')}"
    else:
        city = "未知"

    # 报名信息
    limit = comp.get("competitor_limit", 0)
    registered = comp.get("registered_competitors", 0)

    # 日期格式
    if date_from == date_to:
        date_str = date_from
    else:
        date_str = f"{date_from} ~ {date_to}"

    # 标题
    title = f"比赛公示快讯! {name}"

    # 正文
    body = f"📅 {date_str} | 📍 {city} | 👥 {registered}/{limit}"

    # 链接到粗饼比赛页面
    url = f"https://cubing.com{comp.get('url', '')}"

    return title, body, url


def send_bark_notification(cfg: dict, title: str, body: str, url: str):
    """兼容包装：粗饼比赛的 Bark 推送"""
    send_bark(cfg, title, body, url, "cubing-comp")


def main():
    cfg = load_config()
    known_ids = load_known_ids(KNOWN_COMPS_PATH)
    poll_interval = cfg.get("comp_poll_interval", 60)
    is_first_run = len(known_ids) == 0

    killer = GracefulKiller()

    log.info("=" * 50)
    log.info("粗饼新比赛监控已启动")
    log.info("  轮询间隔: %ds", poll_interval)
    log.info("  已知比赛数: %d", len(known_ids))
    log.info("=" * 50)

    while not killer.kill_now:
        comps = query_competitions()
        if not comps:
            log.warning("Failed to fetch competitions, retrying next cycle")
            poll_wait(poll_interval, killer)
            continue

        current_ids = {c["id"] for c in comps}
        new_ids = current_ids - known_ids

        if new_ids:
            if is_first_run:
                log.info("首次运行，静默记录 %d 条比赛", len(current_ids))
                known_ids = current_ids
                save_known_ids(KNOWN_COMPS_PATH, known_ids)
                is_first_run = False
            else:
                new_comps = [c for c in comps if c["id"] in new_ids]
                log.info("发现 %d 条新比赛!", len(new_comps))
                for comp in new_comps:
                    title, body, url = format_comp_message(comp)
                    log.info("  %s - %s", title, body)
                    send_bark_notification(cfg, title, body, url)
                    send_email(cfg, title, f"{body}\n\n{url}", recipients_key="email_recipients_competition")
                    known_ids.add(comp["id"])
                save_known_ids(KNOWN_COMPS_PATH, known_ids)
                # 更新完整比赛列表 JSON
                try:
                    with open(COMPS_JSON_PATH, "w", encoding="utf-8") as f:
                        json.dump(comps, f, ensure_ascii=False, indent=2)
                    log.info("Updated cubing_competitions.json")
                except Exception as e:
                    log.warning("Failed to update JSON: %s", e)
        else:
            log.info("No new competitions (total: %d)", len(current_ids))

        poll_wait(poll_interval, killer)

    log.info("Monitor stopped")


if __name__ == "__main__":
    main()
