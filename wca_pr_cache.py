"""
关注选手 PR(职业生涯) 基线缓存

- 启动 / 手动 refresh 时从 WCA REST `/persons/<wcaId>/personal_records` 拉全量,
  写入 `wca_pr_cache.json`
- 监控运行时 in-process 维护:发现新 PR → 更新内存 + 持久化

数据形状:
  {
    "2021ZHAN01": {
        "333":   {"single": 339, "average": 438},
        "222":   {"single": null, "average": 132},
        ...
    },
    ...
  }
未参加过的事件 / 未出 average,字段值为 None。`fetch_prs` 返回的字典里所有 event
都补齐两类 key,简化下游对比逻辑。

用法(独立预热缓存):
  python wca_pr_cache.py                # 预热 watched_wca_ids 里所有选手
  python wca_pr_cache.py --refresh      # 忽略已有数据全部重新拉
  python wca_pr_cache.py --id 2021ZHAN01    # 只刷某个选手
"""
import argparse
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Optional

import requests

from monitor_utils import SCRIPT_DIR
from watched_wca_ids import all_watched_ids

log = logging.getLogger("wca_pr_cache")

CACHE_PATH = SCRIPT_DIR / "wca_pr_cache.json"
PR_URL = "https://www.worldcubeassociation.org/api/v0/persons/{wid}/personal_records"


# === 持久化 ===

def _load() -> Dict[str, Dict[str, Dict[str, Optional[int]]]]:
    if CACHE_PATH.exists():
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save(cache: Dict) -> None:
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2, sort_keys=True)


# === 取数 ===

def fetch_prs(wca_id: str, *, timeout: int = 15, retries: int = 3
              ) -> Optional[Dict[str, Dict[str, Optional[int]]]]:
    """从 WCA REST 拉单个选手的 PR 表。失败返 None。"""
    url = PR_URL.format(wid=wca_id)
    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=timeout,
                                headers={"User-Agent": "wca-monitor/1.0"})
            resp.raise_for_status()
            rows = resp.json()
            break
        except requests.RequestException as e:
            if attempt == retries - 1:
                log.warning("PR fetch %s 失败(已重试%d次): %s", wca_id, retries, e)
                return None
            time.sleep(2 ** attempt)
    else:
        return None

    out: Dict[str, Dict[str, Optional[int]]] = {}
    for r in rows:
        ev = r.get("eventId")
        tp = r.get("type")
        if not ev or tp not in ("single", "average"):
            continue
        out.setdefault(ev, {"single": None, "average": None})[tp] = r.get("best")
    return out


# === 缓存层 ===

class PRCache:
    """关注选手 PR 内存缓存,可持久化。
    本进程内的所有 PR 比对走这个类。"""

    def __init__(self, autosave: bool = True):
        self._data: Dict[str, Dict[str, Dict[str, Optional[int]]]] = _load()
        self._autosave = autosave
        self._lock = threading.Lock()

    def has(self, wca_id: str) -> bool:
        return wca_id in self._data

    def get_pr(self, wca_id: str, event_id: str, rec_type: str) -> Optional[int]:
        """返当前缓存的 PR(centiseconds),没有则 None。rec_type ∈ {single, average}"""
        return (self._data.get(wca_id) or {}).get(event_id, {}).get(rec_type)

    def set_pr(self, wca_id: str, event_id: str, rec_type: str, value: int) -> None:
        if not value or value <= 0:
            return
        person = self._data.setdefault(wca_id, {})
        event = person.setdefault(event_id, {"single": None, "average": None})
        event[rec_type] = int(value)
        if self._autosave:
            _save(self._data)

    def is_pr(self, wca_id: str, event_id: str, rec_type: str, value: int) -> bool:
        """value(cs)是否破了选手当前 PR(含 tied)。WCA Live 把 tied PR 也标橙色 PR
        角标,用户期望同样收到通知。注意 PR 不存在时同样判破(首记录也算 PR)。"""
        if not value or value <= 0:
            return False
        current = self.get_pr(wca_id, event_id, rec_type)
        return current is None or value <= current

    def is_tied_pr(self, wca_id: str, event_id: str, rec_type: str, value: int) -> bool:
        """value(cs)是否平了选手当前 PR(严格相等,首记录不算 tied)。"""
        if not value or value <= 0:
            return False
        current = self.get_pr(wca_id, event_id, rec_type)
        return current is not None and value == current

    def warm(self, wca_id: str, *, force: bool = False) -> bool:
        """从 WCA REST 拉单个选手到缓存。返回是否成功拉到。"""
        if not force and self.has(wca_id):
            return True
        prs = fetch_prs(wca_id)
        if prs is None:
            return False
        with self._lock:
            self._data[wca_id] = prs
        if self._autosave:
            with self._lock:
                _save(self._data)
        return True

    def warm_all(self, wca_ids, *, force: bool = False,
                 max_workers: int = 8) -> int:
        """批量预热(并发),返回新拉到的人数。每个 worker 独立打 HTTP,
        autosave 在批量场景下关掉,最后统一持久化。"""
        todo = [w for w in wca_ids if force or not self.has(w)]
        if not todo:
            return 0
        saved_autosave = self._autosave
        self._autosave = False
        added = 0
        try:
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                fut_map = {ex.submit(self.warm, w, force=force): w for w in todo}
                for fut in as_completed(fut_map):
                    wid = fut_map[fut]
                    try:
                        if fut.result():
                            added += 1
                        else:
                            log.warning("PR 预热失败: %s", wid)
                    except Exception as e:
                        log.warning("PR 预热异常 %s: %s", wid, e)
        finally:
            self._autosave = saved_autosave
            with self._lock:
                _save(self._data)
        return added

    def dump(self) -> None:
        """强制立即持久化(autosave 关闭时手工调用)"""
        _save(self._data)


# === CLI ===

def _setup_basic_logging():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%H:%M:%S")


def main():
    _setup_basic_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true",
                        help="忽略已有数据,全部重新拉")
    parser.add_argument("--id", help="只刷新某个 wcaId")
    args = parser.parse_args()

    cache = PRCache()
    if args.id:
        ok = cache.warm(args.id, force=True)
        log.info("%s %s", args.id, "OK" if ok else "FAIL")
        return

    wca_ids = sorted(set(all_watched_ids().values()))
    log.info("预热 PR 基线,选手数: %d", len(wca_ids))
    added = cache.warm_all(wca_ids, force=args.refresh)
    log.info("已拉取 %d 人 PR 数据(总缓存 %d 人)", added, len(cache._data))


if __name__ == "__main__":
    main()
