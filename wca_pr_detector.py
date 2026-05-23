"""
WCA Live PR(职业生涯) 监控

策略:
  1. 列出今天还在举办的比赛(`competitions(from=今天-3天)` + 本地过滤 startDate ≤ today ≤ endDate)
  2. 对每场比赛一次性拉所有事件 + 所有轮次 + 所有 results(fat GraphQL query)
  3. 关注选手(`watched_wca_ids` 缓存里的 wcaId 集合)的每条 best / average:
     - 若 singleRecordTag / averageRecordTag 非空 → 由 WR/CR/NR 路径推送,
       此处只更新 PR 缓存到新基线(避免后续重复推送 PR)
     - 否则与 `wca_pr_cache.PRCache` 比对,小于当前 PR 即"破 PR" → 推送

去重:
  - known_pr_ids 持久化记录已推过的 (result_id, rec_type) uid
  - PR 缓存的更新即"已记账",同一值不会再触发

首次运行:
  - 沉默吸收当前 ongoing 比赛已有的所有破 PR 结果,只更新 cache + known,不推送
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
import logging
import requests

from monitor_utils import send_bark
from record_format import format_combined_records
from wca_local_names import enrich_name

log = logging.getLogger("wca_pr")

WCA_LIVE_API = "https://live.worldcubeassociation.org/api"

ONGOING_COMPS_QUERY = """
query($from: Date) {
  competitions(from: $from, limit: 100) {
    id name startDate endDate
    venues { country { iso2 } }
  }
}
"""

COMP_ROUNDS_QUERY = """
query($id: ID!) {
  competition(id: $id) {
    id name
    venues { country { iso2 } }
    competitionEvents {
      event { id name }
      rounds { id finished numEnteredResults }
    }
  }
}
"""

ROUND_RESULTS_QUERY = """
query($id: ID!) {
  round(id: $id) {
    id finished
    results {
      id best average singleRecordTag averageRecordTag
      person { wcaId name country { iso2 name } }
    }
  }
}
"""


def _gql(query: str, variables: dict = None, timeout: int = 20) -> dict:
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    resp = requests.post(WCA_LIVE_API, json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errors"):
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data.get("data") or {}


def list_ongoing_comps(lookback_days: int = 3) -> list:
    """列 ongoing 比赛(今天处于 startDate~endDate 区间内)"""
    today = date.today()
    from_date = (today - timedelta(days=lookback_days)).isoformat()
    today_iso = today.isoformat()
    data = _gql(ONGOING_COMPS_QUERY, {"from": from_date})
    comps = data.get("competitions") or []
    return [c for c in comps
            if c.get("startDate") and c.get("endDate")
            and c["startDate"] <= today_iso <= c["endDate"]]


def fetch_comp_rounds(comp_id: str) -> dict:
    """轻量 query:只拉比赛元数据 + 各 round 的 id / finished / numEnteredResults"""
    data = _gql(COMP_ROUNDS_QUERY, {"id": comp_id})
    return data.get("competition") or {}


def fetch_round_results(round_id: str) -> list:
    """拉单个 round 的 results"""
    data = _gql(ROUND_RESULTS_QUERY, {"id": round_id})
    rnd = data.get("round") or {}
    return rnd.get("results") or []


def _active_rounds(comp_data: dict):
    """yield (event_id, event_name, round_id) 对未 finished 且有 result 的 round"""
    for ce in comp_data.get("competitionEvents") or []:
        event = ce.get("event") or {}
        event_id = event.get("id")
        event_name = event.get("name") or event_id
        for r in ce.get("rounds") or []:
            if r.get("finished"):
                continue
            if (r.get("numEnteredResults") or 0) <= 0:
                continue
            yield event_id, event_name, r.get("id")


def _candidates_from_round_results(results, *, comp_id, comp_name, comp_iso2,
                                   event_id, event_name, round_id, watched_ids):
    """从一个 round 的 results 列表里筛关注选手的 single+average,产出候选项"""
    for res in results:
        person = res.get("person") or {}
        wid = person.get("wcaId")
        if not wid or wid not in watched_ids:
            continue
        country = person.get("country") or {}
        for vkey, rkey, ttype in (("best", "singleRecordTag", "single"),
                                  ("average", "averageRecordTag", "average")):
            v = res.get(vkey) or 0
            if v <= 0:
                continue
            yield {
                "result_id": res.get("id"),
                "wcaid": wid,
                "name": person.get("name", ""),
                "person_iso2": country.get("iso2", ""),
                "person_country_en": country.get("name", ""),
                "event_id": event_id,
                "event_name": event_name,
                "rec_type": ttype,
                "value": v,
                "record_tag": res.get(rkey) or "",
                "round_id": round_id,
                "comp_id": comp_id,
                "comp_name": comp_name,
                "comp_iso2": comp_iso2,
            }


def iter_pr_candidates(comp_data: dict, watched_ids: set):
    """单场比赛串行 fetch 所有 active round 结果(scan_and_push 走并发版)"""
    venues = comp_data.get("venues") or []
    comp_iso2 = venues[0]["country"]["iso2"] if venues else ""
    comp_name = comp_data.get("name") or ""
    comp_id = comp_data.get("id")
    for event_id, event_name, round_id in _active_rounds(comp_data):
        try:
            results = fetch_round_results(round_id)
        except Exception as e:
            log.warning("拉 round 结果失败 rid=%s: %s", round_id, e)
            continue
        yield from _candidates_from_round_results(
            results, comp_id=comp_id, comp_name=comp_name, comp_iso2=comp_iso2,
            event_id=event_id, event_name=event_name, round_id=round_id,
            watched_ids=watched_ids)


def _pr_uid(cand: dict) -> str:
    """已推/已记账标识:同 result.id 的 single + average 各占一个 uid"""
    return f"wcalive-pr-{cand['result_id']}-{cand['rec_type']}"


def _group_key(cand: dict) -> str:
    """合并模板分组:同选手同事件的 single + average 合并为一条推送"""
    return f"wcalive-pr-{cand['wcaid']}-{cand['event_id']}"


def _to_format_kwargs(cand: dict) -> dict:
    return {
        "tag": "PR",
        "rec_type": cand["rec_type"],
        "attempt_result": cand["value"],
        "event_id": cand["event_id"],
        "event_name": cand["event_name"],
        "person_name": enrich_name(cand["name"], cand["wcaid"]),
        "person_iso2": cand["person_iso2"],
        "person_country_en": cand["person_country_en"],
        "comp_name": cand["comp_name"],
        "comp_iso2": cand["comp_iso2"],
        "tied": cand.get("tied", False),
        "url": (f"https://live.worldcubeassociation.org/competitions/"
                f"{cand['comp_id']}/rounds/{cand['round_id']}"),
    }


def scan_and_push(config: dict, pr_cache, watched_ids: set, known_pr_ids: set,
                  *, is_first_run: bool = False, dry_run: bool = False) -> int:
    """主入口。返回新增的 uid 个数(包括沉默吸收的)。
    失败时抛 RuntimeError 让调用方区分"扫成功但无内容"与"扫失败"。"""
    if not watched_ids:
        return 0
    comps = list_ongoing_comps()  # 失败抛 RequestException / RuntimeError

    if not comps:
        log.debug("无 ongoing 比赛")
        return 0
    log.debug("ongoing 比赛 %d 场,扫 PR", len(comps))

    # 并发拉 ongoing 比赛 meta(每场 ~1s 串行太慢)
    comp_metas = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(fetch_comp_rounds, c["id"]): c for c in comps}
        for fut in as_completed(futs):
            c = futs[fut]
            try:
                comp_metas.append(fut.result())
            except Exception as e:
                log.warning("拉比赛元数据失败 cid=%s: %s", c.get("id"), e)

    # 平铺所有 active round + 比赛上下文,并发拉 results
    active_tasks = []
    for comp_data in comp_metas:
        venues = comp_data.get("venues") or []
        comp_iso2 = venues[0]["country"]["iso2"] if venues else ""
        ctx = {
            "comp_id": comp_data.get("id"),
            "comp_name": comp_data.get("name") or "",
            "comp_iso2": comp_iso2,
        }
        for event_id, event_name, round_id in _active_rounds(comp_data):
            active_tasks.append((round_id, event_id, event_name, ctx))

    if not active_tasks:
        log.debug("ongoing %d 场,但无 active round", len(comps))
        return 0

    fresh_by_group = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(fetch_round_results, rid): (rid, eid, ename, ctx)
                for rid, eid, ename, ctx in active_tasks}
        for fut in as_completed(futs):
            rid, eid, ename, ctx = futs[fut]
            try:
                results = fut.result()
            except Exception as e:
                log.warning("拉 round 结果失败 rid=%s: %s", rid, e)
                continue
            for cand in _candidates_from_round_results(
                    results, event_id=eid, event_name=ename, round_id=rid,
                    watched_ids=watched_ids, **ctx):
                uid = _pr_uid(cand)
                if uid in known_pr_ids:
                    continue
                # 该项是 WR / CR / NR(regional record),交由 record 路径推送。
                # 仍要把 cache 同步到这个新基线,否则后续会按"小于旧 PR"误触发。
                # 注意 WCA Live 也用 singleRecordTag="PR" 标橙色 PR 角标,但 PR
                # 走的就是这个文件,不能当成 regional record 跳过。
                if cand["record_tag"] and cand["record_tag"] != "PR":
                    pr_cache.set_pr(cand["wcaid"], cand["event_id"],
                                    cand["rec_type"], cand["value"])
                    known_pr_ids.add(uid)
                    continue
                if not pr_cache.is_pr(cand["wcaid"], cand["event_id"],
                                      cand["rec_type"], cand["value"]):
                    continue
                cand["tied"] = pr_cache.is_tied_pr(
                    cand["wcaid"], cand["event_id"],
                    cand["rec_type"], cand["value"])
                fresh_by_group.setdefault(_group_key(cand), []).append(cand)

    new_count = 0
    for _gk, group in fresh_by_group.items():
        # single 在前,average 在后,合并模板期望的顺序
        group.sort(key=lambda c: 0 if c["rec_type"] == "single" else 1)
        uids = [_pr_uid(c) for c in group]

        if is_first_run:
            for c in group:
                pr_cache.set_pr(c["wcaid"], c["event_id"],
                                c["rec_type"], c["value"])
                known_pr_ids.add(_pr_uid(c))
            new_count += len(uids)
            continue

        kwargs_list = [_to_format_kwargs(c) for c in group]
        cn, en, url = format_combined_records(kwargs_list)
        log.info("🆕 新 PR%s: %s", "(合并)" if len(group) > 1 else "", cn)
        if dry_run:
            for c in group:
                pr_cache.set_pr(c["wcaid"], c["event_id"],
                                c["rec_type"], c["value"])
                known_pr_ids.add(_pr_uid(c))
            new_count += len(uids)
            continue
        if send_bark(config, cn, en, url, "WCA Records",
                     sound="multiwayinvitation"):
            for c in group:
                pr_cache.set_pr(c["wcaid"], c["event_id"],
                                c["rec_type"], c["value"])
                known_pr_ids.add(_pr_uid(c))
            new_count += len(uids)
        else:
            log.warning("  PR 推送失败,下次轮询将重试: %s", uids)
    return new_count
