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
import re
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


# === 关注选手白名单(PR 监控用)===

_NAME_PAREN_RE = re.compile(r"\(([^)]+)\)")


def _match_key(name: str) -> str:
    """cubing.com user.name → 选手 key(优先取括号内中文名,否则原名)"""
    m = _NAME_PAREN_RE.search(name or "")
    return (m.group(1) if m else (name or "")).strip()


def load_watched_keys(person_dir: str) -> set:
    """读取 watched 选手目录,返回 key 集合(目录名首字母为 ASCII 字母时去掉前缀)。
    规则与 D:/cube/video-by-face/fetch_competition.py 的 load_person_map 一致。"""
    if not person_dir:
        return set()
    p = Path(person_dir)
    if not p.is_dir():
        log.warning("watched_persons_dir 不存在: %s", person_dir)
        return set()
    keys = set()
    for d in p.iterdir():
        if not d.is_dir():
            continue
        name = d.name
        # Python 3.6 兼容:str.isascii 是 3.7+,用 ord 判断
        if name and ord(name[0]) < 128 and name[0].isalpha():
            key = name[1:]
        else:
            key = name
        if key:
            keys.add(key.strip())
    return keys


# === HTTP helpers ===

def http_get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def list_competitions() -> list:
    """拉粗饼比赛列表"""
    return http_get_json(CUBING_API).get("data", [])


def is_china_in_window(comp: dict, now: int, window_seconds: int) -> bool:
    """是否中国(含港澳台)+ 启用了 cubing.com live 直播 + date.from 在过去 window_seconds 内"""
    # live=0 表示这场没启用 cubing.com 直播(选手用 WCA Live 等其他系统),
    # /live/<slug> 页面没有 data-c 字段,扫描无意义且会触发 warning。
    if comp.get("live") != 1:
        return False
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


def fetch_comp_results(cid: int, rounds: list,
                       watched_keys: set = None) -> "Tuple[dict, list, list]":
    """
    拉单场比赛所有 round 的 result.all,以及(可选)关注选手的 PR rows。
    rounds: [(event_id, round_id), ...]
    watched_keys: 关注选手 key 集合(中文名 / 英文名),空则不查 PR。
    返回 (users_map, all_rows, pr_rows)。
      pr_rows 是 cubing.com `result.user` 返回的 nb/na 标记 row,每条多了
      _event(事件 ID)用以补全(因为 r 项原 schema 不带 event)。
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

        # PR 扫描:对参赛 + 关注的选手发 result.user 请求,收 nb/na 标记
        pr_rows = []
        if watched_keys and users:
            watched_pairs = []
            for number, u in users.items():
                if not u.get("wcaid"):
                    continue
                if _match_key(u.get("name", "")) in watched_keys:
                    watched_pairs.append((number, u["wcaid"], u.get("name", "")))
            for number, wcaid, name in watched_pairs:
                pr_rows.extend(_fetch_user_pr_rows(ws, cid, number, wcaid, name))

        return users, rows, pr_rows
    finally:
        try:
            ws.close()
        except Exception:
            pass


def _fetch_user_pr_rows(ws, cid: int, number: int, wcaid: str, name: str) -> list:
    """发送 result.user 请求,返回该选手 nb/na 为 true 的 row 列表(附 _event/_wcaid/_name)。
    cubing.com 服务端把 nb/na 设为 true 即"破职业生涯 PR"的标记,
    对应前端的橙色字。"""
    try:
        ws.send(json.dumps({
            "type": "result", "action": "user",
            "user": {"number": number, "wcaid": wcaid},
        }))
    except Exception as e:
        log.warning("send user req failed wcaid=%s: %s", wcaid, e)
        return []
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            raw = ws.recv()
        except websocket.WebSocketTimeoutException:
            return []
        except Exception as e:
            log.warning("recv user response failed wcaid=%s: %s", wcaid, e)
            return []
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except Exception:
            continue
        if msg.get("type") != "result.user":
            # 边角消息(比如别人推送过来的 broadcast),继续等
            continue
        data = msg.get("data", [])
        pr_rows = []
        current_event = None
        for entry in data:
            t = entry.get("t")
            if t == "e":
                current_event = entry.get("e")
            elif t == "r":
                # 跳过 sr/ar 已经触发 record 推送的 row(record 路径已覆盖)
                if entry.get("sr") or entry.get("ar"):
                    continue
                if entry.get("nb") or entry.get("na"):
                    pr_rows.append({
                        **entry,
                        "_event": current_event,
                        "_wcaid": wcaid,
                        "_name": name,
                    })
        return pr_rows
    return []


def _extract_title(body: str, slug: str) -> str:
    import html, re
    m = re.search(r"<title>([^<]+)</title>", body)
    return html.unescape(m.group(1).split(" - ")[0]).strip() if m else slug


def fetch_live_rounds(slug: str) -> "Tuple[int, list, str, str]":
    """从 live 页 HTML 拿 (cid, events_list, cn_title, en_title)。
    跑两次 HTTP:默认中文版 + ?lang=en 英文版。
    页面缺少必需字段(被下线 / 取消 / 改版)时抛 RuntimeError,由调用方捕获跳过。"""
    import html, re
    url_cn = f"https://cubing.com/live/{slug}"
    req = urllib.request.Request(url_cn, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        body_cn = resp.read().decode("utf-8")
    m_c = re.search(r'data-c="(\d+)"', body_cn)
    if not m_c:
        raise RuntimeError(f"data-c not found on /live/{slug} (页面无效或已下线)")
    m_ev = re.search(r'data-events="([^"]+)"', body_cn)
    if not m_ev:
        raise RuntimeError(f"data-events not found on /live/{slug}")
    cid = int(m_c.group(1))
    events = json.loads(html.unescape(m_ev.group(1)))
    rounds = [(ev["i"], rd["i"]) for ev in events for rd in ev["rs"]]
    cn_title = _extract_title(body_cn, slug)

    # 再拿一次英文 title(EN 推送用)
    url_en = f"https://cubing.com/live/{slug}?lang=en"
    try:
        req_en = urllib.request.Request(url_en, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req_en, timeout=30) as resp:
            body_en = resp.read().decode("utf-8")
        en_title = _extract_title(body_en, slug)
    except Exception as e:
        log.warning("fetch en title failed slug=%s: %s; fallback to slug", slug, e)
        en_title = slug.replace("-", " ")
    return cid, rounds, cn_title, en_title


# === 纪录检测与推送 ===

def iter_record_events(rows: list, users: dict, comp: dict):
    """
    遍历一场比赛的所有 result row,产出可推送的 event 字典。
    每条 sr / ar 标记产生一个 event;同一 row 的两条共享 group_key,后续可合并推送。
    uid 用作去重 key: 'cubing-<result_id>-<sr|ar>'。
    """
    c_iso2 = comp_iso2(comp)
    comp_name = comp.get("name") or comp.get("alias", "")
    comp_name_en = comp.get("name_en") or comp_name
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
                "comp_name_en": comp_name_en,
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
        "comp_name_en": ev.get("comp_name_en") or ev["comp_name"],
        "comp_iso2": ev["comp_iso2"],
        "url": url,
    }


def build_message(events: list):
    """events 长度 1 或 2,返回 (cn, en, url);多条同 row 合并推送"""
    return format_combined_records([_to_format_kwargs(e) for e in events])


def send_bark_notification(cfg: dict, cn: str, en: str, url: str) -> bool:
    return send_bark(cfg, cn, en, url, "WCA Records", sound="multiwayinvitation")


def iter_pr_events(pr_rows: list, comp: dict):
    """
    把 result.user 返回的 nb/na rows 转成可推送的 PR event 字典。
    cubing.com 服务端 nb/na 是累积标记(同选手同事件初赛/复赛/决赛都可能标),
    这里按 (wcaid, event_id, rec_type) 去重,只取最快的那条。
    同选手同事件的 single + average PR 共享 group_key,会合并推送("双 PR" 模板)。
    """
    c_iso2 = comp_iso2(comp)
    comp_name = comp.get("name") or comp.get("alias", "")
    comp_name_en = comp.get("name_en") or comp_name
    slug = comp.get("alias", "")

    # (wcaid, event_id, rec_type) → (row, value)
    best = {}
    for row in pr_rows:
        wcaid = row.get("_wcaid", "")
        # r.e 是字符串 "333";t:"e" 顶部 e 是 int — 用 r.e 优先,fallback 用 str(_event)
        event_id = row.get("e") or str(row.get("_event") or "")
        for kind, rec_type, vf in (("nb", "single", "b"), ("na", "average", "a")):
            if not row.get(kind):
                continue
            v = row.get(vf)
            if v is None or v <= 0:
                continue
            k = (wcaid, event_id, rec_type)
            if k not in best or v < best[k][1]:
                best[k] = (row, v)

    for (wcaid, event_id, rec_type), (row, v) in best.items():
        i = row.get("i")
        if not i:
            continue
        field = "nb" if rec_type == "single" else "na"
        yield {
            "uid": f"cubing-{i}-{field}",
            # 同选手同事件 single+avg 合并(无论它们来自哪个 round)
            "group_key": f"cubing-pr-{wcaid}-{event_id}",
            "tag": "PR",
            "rec_type": rec_type,
            "attempt_result": v,
            "event_id": event_id,
            "round_id": row.get("r"),
            "person_name": row.get("_name", ""),
            "person_region": row.get("_region") or "China",
            "comp_iso2": c_iso2,
            "comp_name": comp_name,
            "comp_name_en": comp_name_en,
            "slug": slug,
        }


def scan_comp(comp: dict, watched_keys: set = None) -> list:
    """扫描单场比赛,返回所有 record + PR 事件 dict 列表"""
    slug = comp.get("alias")
    if not slug:
        log.warning("comp without alias: id=%s name=%s", comp.get("id"), comp.get("name"))
        return []
    try:
        cid, rounds, cn_title, en_title = fetch_live_rounds(slug)
    except Exception as e:
        log.warning("fetch live page failed slug=%s: %s", slug, e)
        return []
    if not rounds:
        return []
    if not comp.get("name") or comp["name"] == slug:
        comp = dict(comp, name=cn_title)
    comp = dict(comp, name_en=en_title)
    try:
        users, rows, pr_rows = fetch_comp_results(cid, rounds, watched_keys)
    except Exception as e:
        log.warning("fetch ws results failed cid=%s: %s", cid, e)
        return []
    # 为 PR row 补 region 字段(从 users map 反查)
    for pr in pr_rows:
        u = users.get(pr.get("n"))
        if u:
            pr["_region"] = u.get("region")
    events = list(iter_record_events(rows, users, comp))
    events.extend(iter_pr_events(pr_rows, comp))
    return events


def process_events(cfg: dict, events: list, known_ids: set,
                   dry_run: bool,
                   target_tags: set, nr_countries: set) -> int:
    """对一批纪录事件做过滤 + 按 group_key 聚合 + 推送,返回新计数。
    cubing 监控不做首次启动静默 —— 用户要"过去 N 天补推、已 push 不重推",
    所以未 known 的全推。"""

    def _wanted(ev: dict) -> bool:
        tag = ev["tag"]
        # PR: 只要进了 events 列表(已被 watched_keys 过滤),无条件放行
        if tag == "PR":
            return True
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
             dry_run: bool = False, watched_keys: set = None) -> int:
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
        comp = {"alias": slug_override, "name": slug_override,
                "locations": [{"province": "测试"}], "date": {"from": 0, "to": now}}
        events = scan_comp(comp, watched_keys)
        log.info("scan slug=%s → %d events (record+PR)", slug_override, len(events))
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
        events = scan_comp(comp, watched_keys)
        if events:
            log.info("  %s: %d events (record+PR)", comp.get("alias"), len(events))
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
    watched_keys = load_watched_keys(cfg.get("watched_persons_dir", ""))

    log.info("=" * 50)
    log.info("cubing.com 纪录监控启动 once=%s dry_run=%s", args.once, args.dry_run)
    log.info("  监控类型: %s", ",".join(sorted(cfg.get("tags", ["WR", "CR", "NR"]))))
    log.info("  扫描窗口: 过去 %d 天", window_days)
    log.info("  PR 关注选手数: %d", len(watched_keys))
    log.info("  已知纪录数: %d", len(known_ids))
    if args.comp:
        log.info("  测试比赛: %s", args.comp)
    log.info("=" * 50)

    RANKINGS.update_all()

    if args.once or args.comp:
        n = run_once(cfg, known_ids, slug_override=args.comp,
                     dry_run=args.dry_run, watched_keys=watched_keys)
        if n > 0 and not args.dry_run:
            save_known_ids(KNOWN_IDS_FILE, known_ids)
        log.info("done. new=%d", n)
        return

    killer = GracefulKiller()
    while not killer.kill_now:
        try:
            n = run_once(cfg, known_ids, watched_keys=watched_keys)
            if n > 0:
                save_known_ids(KNOWN_IDS_FILE, known_ids)
        except Exception as e:
            log.error("未预期错误: %s", e, exc_info=True)
        poll_wait(interval, killer)

    save_known_ids(KNOWN_IDS_FILE, known_ids)
    log.info("监控已停止,状态已保存")


if __name__ == "__main__":
    main()
