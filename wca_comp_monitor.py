"""
WCA 官方比赛监控

轮询 WCA REST API，发现新公布的比赛时通过 Bark 推送通知。
数据源为 WCA 官网（全球覆盖），与粗饼网监控（中国大陆）互为补充。

用法：
  python wca_comp_monitor.py
"""

import json
import logging
import time
from pathlib import Path

import requests

from email_notifier import send_email
from monitor_utils import (
    load_config, load_known_ids, save_known_ids, send_bark,
    country_flag, GracefulKiller, poll_wait, setup_logging, SCRIPT_DIR,
)

# === 路径 ===

KNOWN_WCA_COMPS_PATH = SCRIPT_DIR / "known_wca_comp_ids.json"

# === 日志 ===

log = setup_logging("wca_comp")

# === API ===

WCA_API = "https://www.worldcubeassociation.org/api/v0/competitions"
HEADERS = {"User-Agent": "WCA-Monitor/1.0", "Accept": "application/json"}

# 每次拉取的比赛数量上限
# NOTE: 15 分钟内不可能有超过 50 个新比赛公布
PER_PAGE = 50


def query_competitions() -> list:
    """查询 WCA 最新公布的比赛（按 announced_at 倒序）"""
    params = {
        "sort": "-announced_at",
        "per_page": PER_PAGE,
    }
    for attempt in range(3):
        try:
            r = requests.get(WCA_API, params=params, headers=HEADERS, timeout=15)
            if r.status_code == 200 and r.text.strip():
                return r.json()
            log.warning("API returned %d, retry %d/3", r.status_code, attempt + 1)
        except requests.RequestException as e:
            log.warning("Request failed: %s, retry %d/3", e, attempt + 1)
        time.sleep(3)
    return []


def format_comp_message(comp: dict) -> "tuple[str, str, str]":
    """
    格式化 WCA 比赛通知。
    返回 (title, body, url)。
    """
    name = comp["name"]
    date_range = comp.get("date_range", comp.get("start_date", ""))
    city = comp.get("city", "")
    iso2 = comp.get("country_iso2", "")
    flag = country_flag(iso2)

    # 项目数量
    events = comp.get("event_ids", [])
    event_count = len(events)

    # 人数上限（可能为 None）
    limit = comp.get("competitor_limit")
    limit_str = f" | 👥 上限{limit}" if limit else ""

    # 标题：用 🌍 与粗饼监控的 "比赛公示快讯!" 区分
    title = f"🌍WCA新赛! {name}"

    # 正文
    body = f"📅 {date_range} | 📍 {city} {flag} | 🏷️ {event_count}个项目{limit_str}"

    # 链接到 WCA 比赛页面
    url = comp.get("url", f"https://www.worldcubeassociation.org/competitions/{comp['id']}")

    return title, body, url


def main():
    cfg = load_config()
    known_ids = load_known_ids(KNOWN_WCA_COMPS_PATH)
    poll_interval = cfg.get("wca_comp_poll_interval", 900)
    is_first_run = len(known_ids) == 0

    killer = GracefulKiller()

    log.info("=" * 50)
    log.info("WCA 比赛监控已启动")
    log.info("  轮询间隔: %ds", poll_interval)
    log.info("  已知比赛数: %d", len(known_ids))
    log.info("=" * 50)

    while not killer.kill_now:
        comps = query_competitions()
        if not comps:
            log.warning("Failed to fetch WCA competitions, retrying next cycle")
            poll_wait(poll_interval, killer)
            continue

        current_ids = {c["id"] for c in comps}
        new_ids = current_ids - known_ids

        if new_ids:
            if is_first_run:
                log.info("首次运行，静默记录 %d 条比赛", len(current_ids))
                known_ids = current_ids
                save_known_ids(KNOWN_WCA_COMPS_PATH, known_ids)
                is_first_run = False
            else:
                # 按 announced_at 排序，最早的先推送
                new_comps = sorted(
                    [c for c in comps if c["id"] in new_ids],
                    key=lambda c: c.get("announced_at", ""),
                )
                log.info("发现 %d 条新 WCA 比赛!", len(new_comps))
                for comp in new_comps:
                    title, body, url = format_comp_message(comp)
                    log.info("  %s - %s", title, body)
                    send_bark(cfg, title, body, url, "wca-comp")
                    send_email(cfg, title, f"{body}\n\n{url}", recipients_key="email_recipients_competition")
                    known_ids.add(comp["id"])
                save_known_ids(KNOWN_WCA_COMPS_PATH, known_ids)
        else:
            log.info("No new WCA competitions (checked %d)", len(current_ids))

        poll_wait(poll_interval, killer)

    log.info("WCA comp monitor stopped")


if __name__ == "__main__":
    main()
