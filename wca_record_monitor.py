"""
WCA Live 纪录监控工具

每隔指定时间轮询 WCA Live GraphQL API,检测新的 WR/CR/NR 纪录,
通过 Bark 推送通知到 iPhone。

用法:
  python wca_record_monitor.py

首次运行前,需要在同目录下创建 config.json(见 config.example.json)。
"""

import requests

from email_notifier import send_email
from monitor_utils import (
    load_config, load_known_ids, save_known_ids, send_bark,
    GracefulKiller, poll_wait, setup_logging,
    SCRIPT_DIR,
)
from record_format import (
    format_record_message as _format_record_message,
    format_combined_records,
)
from wca_rankings import RANKINGS

# === 常量 ===

# 持久化文件:记录已通知过的纪录 ID,避免重复推送
KNOWN_IDS_FILE = SCRIPT_DIR / "known_ids.json"

WCA_LIVE_API = "https://live.worldcubeassociation.org/api"

# GraphQL 查询:获取近期纪录的完整信息
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
        wcaId
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

# === 日志 ===

log = setup_logging("wca_monitor")


# === 核心函数 ===

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


def _record_to_kwargs(record: dict) -> dict:
    """WCA Live GraphQL record → format_record_message / format_combined_records kwargs"""
    result = record["result"]
    person = result["person"]
    round_obj = result["round"]
    event = round_obj["competitionEvent"]["event"]
    competition = round_obj["competitionEvent"]["competition"]
    venues = competition.get("venues", [])
    comp_iso2 = venues[0]["country"]["iso2"] if venues else ""
    round_id = round_obj["id"]
    comp_id = competition["id"]
    return {
        "tag": record["tag"],
        "rec_type": record["type"],
        "attempt_result": record["attemptResult"],
        "event_id": event["id"],
        "event_name": event["name"],
        "person_name": person["name"],
        "person_iso2": person["country"]["iso2"],
        "person_country_en": person["country"]["name"],
        "comp_name": competition["name"],
        "comp_iso2": comp_iso2,
        "url": f"https://live.worldcubeassociation.org/competitions/{comp_id}/rounds/{round_id}",
    }


def format_record_message(record: dict):
    """单条 WCA Live record → (cn, en, url),保留给 test_push / gen_title 使用"""
    return _format_record_message(**_record_to_kwargs(record))


def _group_key(record: dict) -> str:
    """合并 key:同 round + 同选手的多条 record 合并为一条推送"""
    rid = record["result"]["round"]["id"]
    wid = record["result"]["person"].get("wcaId") or record["result"]["person"]["name"]
    return f"wcalive-round-{rid}-{wid}"


def send_bark_notification(config: dict, cn_text: str, en_text: str, url: str) -> bool:
    """兼容包装:纪录监控的 Bark 推送(标题=中文,正文=英文)"""
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

    # 首次运行:静默加载当前所有纪录,避免历史纪录触发通知
    is_first_run = len(known_ids) == 0

    # 启动时更新世界排名(Top 100),优先用本地缓存
    RANKINGS.update_all()

    while not killer.kill_now:
        try:
            records = query_recent_records()
            important = []
            for r in records:
                tag = r["tag"]
                if tag not in target_tags:
                    continue
                # NR 国家过滤:只推送白名单内的国家
                if tag == "NR" and nr_countries:
                    iso2 = r["result"]["person"]["country"]["iso2"]
                    if iso2 not in nr_countries:
                        continue
                important.append(r)

            # 过滤已知 → 按 (round, person) 聚合 → 合并推送
            fresh = [r for r in important if r["id"] not in known_ids]
            groups = {}
            for r in fresh:
                groups.setdefault(_group_key(r), []).append(r)

            new_count = 0
            for _gk, group in groups.items():
                # 同组按 (single, average) 稳定排序
                group.sort(key=lambda r: 0 if r["type"] == "single" else 1)
                rids = [r["id"] for r in group]
                if is_first_run:
                    for rid in rids:
                        known_ids.add(rid)
                    new_count += len(rids)
                    continue

                has_wr = any(r["tag"] == "WR" for r in group)
                kwargs_list = [_record_to_kwargs(r) for r in group]
                cn_text, en_text, url = format_combined_records(kwargs_list)
                log.info("🆕 新纪录%s: %s", "(合并)" if len(group) > 1 else "", cn_text)

                # NOTE: 只有 Bark 推送成功才标记为已知,失败时下次轮询会重试
                if send_bark_notification(config, cn_text, en_text, url):
                    for rid in rids:
                        known_ids.add(rid)
                    new_count += len(rids)
                    # NOTE: 邮件只发 WR,CR/NR 不发邮件
                    if has_wr:
                        send_email(config, cn_text, f"{en_text}\n\n{url}", recipients_key="email_recipients_record")
                else:
                    log.warning(f"  推送失败,下次轮询将重试: {rids}")

            if is_first_run and new_count > 0:
                log.info(f"首次运行,静默记录了 {new_count} 条现有纪录(不推送)")
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
    log.info("监控已停止,状态已保存")


if __name__ == "__main__":
    main()
