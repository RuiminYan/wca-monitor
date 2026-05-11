"""
cubing.com 中国比赛纪录快讯监控

每隔指定时间:
  1. GET cubing.com/api/competition 取比赛列表
  2. 过滤"中国 ongoing(+ 缓冲)"的比赛
  3. 对每场比赛通过 WS 拉所有 round 的 result.all
  4. 任一 row 的 sr / ar 字段非空 → 视为破纪录,按 record_format 模板推 Bark

result row 字段(实测):
  i:result-id  c:cid  n:competitor#  e:event  r:round  f:format
  b:best(cs)   a:average(cs)  v:[5 attempts]
  sr:"WR"/"AsR"/"NR"/...   ar:同上(对应平均成绩)

用法:
  python cubing_record_monitor.py                         # 守护进程模式
  python cubing_record_monitor.py --once                  # 单次扫描后退出
  python cubing_record_monitor.py --once --comp <slug>    # 扫描指定比赛(测试用)
  python cubing_record_monitor.py --once --comp <slug> --dry-run   # 不推送
"""
import argparse
import json
import time
import urllib.request
from pathlib import Path

import websocket

from email_notifier import send_email
from monitor_utils import (
    load_config, load_known_ids, save_known_ids, send_bark,
    GracefulKiller, poll_wait, setup_logging, SCRIPT_DIR,
)
from record_format import (
    format_record_message, format_combined_records,
    EVENT_NAME_BY_ID, COUNTRY_EN_MAP, CR_ABBR_CN,
)
from wca_rankings import RANKINGS


# === 路径 / API ===

KNOWN_IDS_FILE = SCRIPT_DIR / "known_cubing_record_ids.json"
CUBING_API = "https://cubing.com/api/competition"
WS_URL = "wss://cubing.com/ws"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

# 中国(含港澳台)地区在 cubing.com locations.province 中的特殊关键字 → ISO2
_PROVINCE_TO_ISO2 = {
    "香港": "HK", "台湾": "TW", "澳门": "MO",
}

# 监控窗口(秒):扫 date.from 在过去 N 天内开始的中国比赛。
# 默认 30 天:进行中 + 最近一个月结束的比赛(覆盖赛中赛后补录的纪录),
# 而非仅 ongoing 24h。已推过的纪录由 known_cubing_record_ids.json dedup。
_DEFAULT_WINDOW_SEC = 30 * 24 * 3600

log = setup_logging("cubing_record")


# === HTTP helpers ===

def http_get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def list_competitions() -> list:
    """拉粗饼比赛列表"""
    return http_get_json(CUBING_API).get("data", [])


def is_china_in_window(comp: dict, now: int, window_seconds: int) -> bool:
    """是否中国(含港澳台)且 date.from 在过去 window_seconds 内(不太未来)"""
    locations = comp.get("locations") or []
    if not locations:
        return False
    province = (locations[0].get("province") or "").strip()
    if any(k in province for k in _PROVINCE_TO_ISO2):
        pass
    elif not province:
        return False
    date = comp.get("date") or {}
    start = date.get("from", 0)
    cutoff = now - window_seconds
    # 比赛开始时间在 [cutoff, now+24h](容忍少量未来比赛)
    return cutoff <= start <= now + 86400


def comp_iso2(comp: dict) -> str:
    """从 locations[0].province 推断比赛所在地区 ISO2,默认 CN"""
    locations = comp.get("locations") or []
    if locations:
        province = locations[0].get("province") or ""
        for keyword, iso in _PROVINCE_TO_ISO2.items():
            if keyword in province:
                return iso
    return "CN"


# === WebSocket fetch ===

def _open_ws():
    ws = websocket.create_connection(
        WS_URL, timeout=20, origin="https://cubing.com",
        header=["User-Agent: Mozilla/5.0"],
    )
    ws.settimeout(20)
    return ws


def fetch_comp_results(cid: int, rounds: list) -> "Tuple[dict, list]":
    """
    拉单场比赛所有 round 的 result.all。
    rounds: [(event_id, round_id), ...]
    返回 (users_map, all_rows),all_rows 已附 _event/_round 字段。
    """
    ws = _open_ws()
    try:
        ws.send(json.dumps({"type": "competition", "competitionId": cid}))
        for eid, rid in rounds:
            ws.send(json.dumps({
                "type": "result", "action": "fetch",
                "params": {"event": eid, "round": rid, "filter": "all"},
            }))
        users = {}
        rows = []
        got_users = False
        received_rounds = 0
        deadline = time.time() + 30
        while time.time() < deadline:
            if got_users and received_rounds >= len(rounds):
                break
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                break
            except Exception as e:
                log.warning("WS recv error cid=%s: %s", cid, e)
                break
            if not raw:
                continue
            msg = json.loads(raw)
            t = msg.get("type")
            if t == "users":
                for k, v in msg.get("data", {}).items():
                    users[int(k)] = v
                got_users = True
            elif t == "result.all":
                received_rounds += 1
                for row in msg.get("data", []):
                    rows.append(row)
        return users, rows
    finally:
        try:
            ws.close()
        except Exception:
            pass


def fetch_live_rounds(slug: str) -> "Tuple[int, list, str]":
    """从 live 页 HTML 拿 (cid, events_list, title)。
    默认拉中文版,中国比赛 title 即为中文(例如 '2026WCA德清短时赛')。
    页面缺少必需字段(被下线 / 取消 / 改版)时抛 RuntimeError,由调用方捕获跳过。"""
    import html, re
    url = f"https://cubing.com/live/{slug}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8")
    m_c = re.search(r'data-c="(\d+)"', body)
    if not m_c:
        raise RuntimeError(f"data-c not found on /live/{slug} (页面无效或已下线)")
    m_ev = re.search(r'data-events="([^"]+)"', body)
    if not m_ev:
        raise RuntimeError(f"data-events not found on /live/{slug}")
    cid = int(m_c.group(1))
    events = json.loads(html.unescape(m_ev.group(1)))
    rounds = [(ev["i"], rd["i"]) for ev in events for rd in ev["rs"]]
    title_m = re.search(r"<title>([^<]+)</title>", body)
    title = html.unescape(title_m.group(1).split(" - ")[0]).strip() if title_m else slug
    return cid, rounds, title


# === 纪录检测与推送 ===

def iter_record_events(rows: list, users: dict, comp: dict):
    """
    遍历一场比赛的所有 result row,产出可推送的 event 字典。
    每条 sr / ar 标记产生一个 event;同一 row 的两条共享 group_key,后续可合并推送。
    uid 用作去重 key: 'cubing-<result_id>-<sr|ar>'。
    """
    c_iso2 = comp_iso2(comp)
    comp_name = comp.get("name") or comp.get("alias", "")
    slug = comp.get("alias", "")
    for row in rows:
        for tag_field, rec_type, value in (("sr", "single", row.get("b")),
                                           ("ar", "average", row.get("a"))):
            tag = row.get(tag_field) or ""
            if not tag:
                continue
            if value is None or value <= 0:
                # 纪录但成绩 DNF/缺失 — 不应该出现,跳过
                continue
            user = users.get(row.get("n"))
            if not user:
                continue
            uid = f"cubing-{row['i']}-{tag_field}"
            yield {
                "uid": uid,
                "group_key": f"cubing-row-{row['i']}",
                "tag": tag,
                "rec_type": rec_type,
                "attempt_result": value,
                "event_id": row.get("e"),
                "round_id": row.get("r"),
                "person_name": user.get("name", ""),
                "person_region": user.get("region", ""),
                "comp_iso2": c_iso2,
                "comp_name": comp_name,
                "slug": slug,
            }


def _to_format_kwargs(ev: dict) -> dict:
    """把内部 event dict 转成 format_record_message / format_combined_records 的 kwargs"""
    person_iso2 = COUNTRY_EN_MAP.get(ev["person_region"], "")
    event_name = EVENT_NAME_BY_ID.get(ev["event_id"], ev["event_id"])
    url = f"https://cubing.com/live/{ev['slug']}?event={ev['event_id']}&round={ev['round_id']}"
    return {
        "tag": ev["tag"],
        "rec_type": ev["rec_type"],
        "attempt_result": ev["attempt_result"],
        "event_id": ev["event_id"],
        "event_name": event_name,
        "person_name": ev["person_name"],
        "person_iso2": person_iso2,
        "person_country_en": ev["person_region"],
        "comp_name": ev["comp_name"],
        "comp_iso2": ev["comp_iso2"],
        "url": url,
    }


def build_message(events: list):
    """events 长度 1 或 2,返回 (cn, en, url);多条同 row 合并推送"""
    return format_combined_records([_to_format_kwargs(e) for e in events])


def send_bark_notification(cfg: dict, cn: str, en: str, url: str) -> bool:
    return send_bark(cfg, cn, en, url, "WCA Records", sound="multiwayinvitation")


def scan_comp(comp: dict) -> list:
    """扫描单场比赛,返回所有纪录事件 dict 列表"""
    slug = comp.get("alias")
    if not slug:
        log.warning("comp without alias: id=%s name=%s", comp.get("id"), comp.get("name"))
        return []
    try:
        cid, rounds, title = fetch_live_rounds(slug)
    except Exception as e:
        log.warning("fetch live page failed slug=%s: %s", slug, e)
        return []
    if not rounds:
        return []
    # 缺少 name(测试模式)时回填 live 页 title
    if not comp.get("name") or comp["name"] == slug:
        comp = dict(comp, name=title)
    try:
        users, rows = fetch_comp_results(cid, rounds)
    except Exception as e:
        log.warning("fetch ws results failed cid=%s: %s", cid, e)
        return []
    return list(iter_record_events(rows, users, comp))


def process_events(cfg: dict, events: list, known_ids: set,
                   dry_run: bool,
                   target_tags: set, nr_countries: set) -> int:
    """对一批纪录事件做过滤 + 按 group_key 聚合 + 推送,返回新计数。
    cubing 监控不做首次启动静默 —— 用户要"过去 N 天补推、已 push 不重推",
    所以未 known 的全推。"""

    def _wanted(ev: dict) -> bool:
        tag = ev["tag"]
        # tag 过滤:CR 通配所有具体洲缩写,其余精确匹配
        if not (tag in target_tags or (tag in CR_ABBR_CN and "CR" in target_tags)):
            return False
        if tag == "NR" and nr_countries:
            person_iso2 = COUNTRY_EN_MAP.get(ev["person_region"], "")
            if person_iso2 not in nr_countries:
                return False
        return True

    fresh = [e for e in events if _wanted(e) and e["uid"] not in known_ids]

    # 按 group_key 聚合(同一 row 的 sr+ar 落到同一组)
    groups = {}
    for ev in fresh:
        groups.setdefault(ev["group_key"], []).append(ev)

    new_count = 0
    for _gk, group in groups.items():
        group.sort(key=lambda e: 0 if e["rec_type"] == "single" else 1)
        uids = [e["uid"] for e in group]

        # WR 邮件:组内有 WR 则发邮件
        has_wr = any(e["tag"] == "WR" for e in group)

        cn, en, url = build_message(group)
        log.info("🆕 新纪录%s: %s", "(合并)" if len(group) > 1 else "", cn)
        if dry_run:
            print(f"DRY {cn}\n    {en}\n    {url}")
            for uid in uids:
                known_ids.add(uid)
            new_count += len(uids)
            continue
        if send_bark_notification(cfg, cn, en, url):
            for uid in uids:
                known_ids.add(uid)
            new_count += len(uids)
            if has_wr:
                send_email(cfg, cn, f"{en}\n\n{url}",
                           recipients_key="email_recipients_record")
        else:
            log.warning("  推送失败,下次轮询将重试: %s", uids)
    return new_count


# === 主循环 ===

def run_once(cfg: dict, known_ids: set, slug_override: str = None,
             dry_run: bool = False) -> int:
    """单次扫描全部目标比赛,返回新纪录条数。
    cubing 监控不走 wca-record-monitor 的首次启动静默策略:每场扫到的未 known
    纪录都会推送,正合用户"过去 N 天补推 + 已 push 不重推"的需求。
    """
    target_tags = set(cfg.get("tags", ["WR", "CR", "NR"]))
    nr_countries = set(cfg.get("nr_countries", []))
    window_days = cfg.get("cubing_record_window_days", 30)
    now = int(time.time())
    new_total = 0
    if slug_override:
        # 测试用:直接指定 slug,跳过列表过滤
        comp = {"alias": slug_override, "name": slug_override,
                "locations": [{"province": "测试"}], "date": {"from": 0, "to": now}}
        events = scan_comp(comp)
        log.info("scan slug=%s → %d record events", slug_override, len(events))
        new_total += process_events(cfg, events, known_ids,
                                    dry_run, target_tags, nr_countries)
        return new_total

    try:
        comps = list_competitions()
    except Exception as e:
        log.warning("list competitions failed: %s", e)
        return 0
    window_sec = window_days * 86400
    targets = [c for c in comps if is_china_in_window(c, now, window_sec)]
    log.info("CN comps in last %d days: %d", window_days, len(targets))
    for comp in targets:
        events = scan_comp(comp)
        if events:
            log.info("  %s: %d record events", comp.get("alias"), len(events))
        new_total += process_events(cfg, events, known_ids,
                                    dry_run, target_tags, nr_countries)
    return new_total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="只扫描一次后退出")
    ap.add_argument("--comp", default=None,
                    help="测试模式:指定比赛 slug(如 Please-Be-Quiet-Hefei-2026),跳过列表过滤")
    ap.add_argument("--dry-run", action="store_true",
                    help="检测纪录但不推送 Bark/邮件,也写入 known_ids 防止重复打印")
    args = ap.parse_args()

    cfg = load_config()
    known_ids = load_known_ids(KNOWN_IDS_FILE)
    interval = cfg.get("cubing_record_poll_interval", cfg.get("poll_interval", 60))
    window_days = cfg.get("cubing_record_window_days", 30)

    log.info("=" * 50)
    log.info("cubing.com 纪录监控启动 once=%s dry_run=%s", args.once, args.dry_run)
    log.info("  监控类型: %s", ",".join(sorted(cfg.get("tags", ["WR", "CR", "NR"]))))
    log.info("  扫描窗口: 过去 %d 天", window_days)
    log.info("  已知纪录数: %d", len(known_ids))
    if args.comp:
        log.info("  测试比赛: %s", args.comp)
    log.info("=" * 50)

    RANKINGS.update_all()

    if args.once or args.comp:
        n = run_once(cfg, known_ids, slug_override=args.comp,
                     dry_run=args.dry_run)
        if n > 0 and not args.dry_run:
            save_known_ids(KNOWN_IDS_FILE, known_ids)
        log.info("done. new=%d", n)
        return

    killer = GracefulKiller()
    while not killer.kill_now:
        try:
            n = run_once(cfg, known_ids)
            if n > 0:
                save_known_ids(KNOWN_IDS_FILE, known_ids)
        except Exception as e:
            log.error("未预期错误: %s", e, exc_info=True)
        poll_wait(interval, killer)

    save_known_ids(KNOWN_IDS_FILE, known_ids)
    log.info("监控已停止,状态已保存")


if __name__ == "__main__":
    main()
