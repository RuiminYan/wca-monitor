"""
关注选手目录 → WCA ID 解析

从 watched_persons_dir 下的每个子目录(目录名 = 选手 key,首字母为 ASCII 字母时
表示分组前缀)出发,自动查询 WCA REST `search/users` 拿到 wcaId,持久化到
`watched_wca_ids_cache.json`。

歧义处理:
  - 若子目录内存在 `wca_id.txt`,以其内容为准(用户强制指定)
  - 否则取搜索结果中首个 `wca_id != null` 的条目

用法(独立预热缓存):
  python watched_wca_ids.py                 # 预热默认目录
  python watched_wca_ids.py --refresh       # 忽略缓存重新解析全部
  python watched_wca_ids.py --print         # 打印当前映射表
"""
import argparse
import json
import logging
import time
import urllib.parse
from pathlib import Path
from typing import Dict, Optional

import requests

from monitor_utils import SCRIPT_DIR, load_config

log = logging.getLogger("watched_wca_ids")

CACHE_PATH = SCRIPT_DIR / "watched_wca_ids_cache.json"
SEARCH_URL = "https://www.worldcubeassociation.org/api/v0/search/users"


# === 目录扫描 ===

def _dir_search_key(dir_name: str) -> str:
    """目录名 → 搜索 key。仅当首字符是 ASCII 字母 + 第二字符是非 ASCII(分组前缀
    紧跟 CJK)时才剥掉。例如 `Z张博藩` → `张博藩`,但 `Max Park` / `Đỗ Quang Hưng`
    保持原样。"""
    if (len(dir_name) >= 2
            and ord(dir_name[0]) < 128 and dir_name[0].isalpha()
            and ord(dir_name[1]) >= 128):
        return dir_name[1:].strip()
    return dir_name.strip()


def list_watched_dirs(person_dir: str):
    """yield (dir_path, search_key) 对所有 watched 目录"""
    if not person_dir:
        return
    p = Path(person_dir)
    if not p.is_dir():
        log.warning("watched_persons_dir 不存在: %s", person_dir)
        return
    for d in sorted(p.iterdir()):
        if not d.is_dir():
            continue
        key = _dir_search_key(d.name)
        if key:
            yield d, key


# === 缓存 ===

def _load_cache() -> Dict[str, str]:
    if CACHE_PATH.exists():
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_cache(cache: Dict[str, str]) -> None:
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2, sort_keys=True)


# === 解析 ===

def _read_override(dir_path: Path) -> Optional[str]:
    """目录内 wca_id.txt 强制指定的 wcaId,失败返 None"""
    override = dir_path / "wca_id.txt"
    if not override.exists():
        return None
    try:
        text = override.read_text(encoding="utf-8").strip()
        return text or None
    except OSError:
        return None


def _search_wca_id(query: str, timeout: int = 15, retries: int = 3) -> Optional[dict]:
    """打 WCA REST search/users,返第一个 wca_id 非空的结果,或 None。
    失败重试,exponential backoff(WCA 服务器偶尔 SSL EOF)。"""
    url = f"{SEARCH_URL}?q={urllib.parse.quote(query)}"
    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=timeout,
                                headers={"User-Agent": "wca-monitor/1.0"})
            resp.raise_for_status()
            data = resp.json()
            for item in data.get("result", []):
                if item.get("wca_id"):
                    return item
            return None
        except requests.RequestException as e:
            if attempt == retries - 1:
                log.warning("search %s 失败(已重试%d次): %s", query, retries, e)
                return None
            time.sleep(2 ** attempt)
    return None


def resolve(dir_path: Path, search_key: str) -> Optional[str]:
    """单个目录 → wcaId(优先 override,其次 search 接口)"""
    override = _read_override(dir_path)
    if override:
        return override
    item = _search_wca_id(search_key)
    if item:
        log.info("  %s → %s (%s)", search_key, item["wca_id"], item.get("name"))
        return item["wca_id"]
    log.warning("  %s → 未找到", search_key)
    return None


def warm_cache(person_dir: str, *, refresh: bool = False,
               sleep_between: float = 0.2) -> Dict[str, str]:
    """
    遍历 watched 目录,把每个 search_key 解析为 wcaId,持久化。返回 {search_key: wcaId}。
    refresh=True 时忽略已有缓存,全部重打 search。
    """
    cache = {} if refresh else _load_cache()
    changed = False
    for dir_path, key in list_watched_dirs(person_dir):
        if key in cache and cache[key]:
            continue
        wid = resolve(dir_path, key)
        if wid:
            cache[key] = wid
            changed = True
            # NOTE: 善意限速,WCA REST 没明文限流但快打容易触发 cloudflare
            time.sleep(sleep_between)
    if changed:
        _save_cache(cache)
    return cache


def get_wca_id(search_key: str) -> Optional[str]:
    """从持久缓存读单个 key 的 wcaId(已预热场景常用)"""
    return _load_cache().get(search_key)


def all_watched_ids() -> Dict[str, str]:
    """读取整个缓存表"""
    return _load_cache()


# === CLI ===

def _setup_basic_logging():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%H:%M:%S")


def main():
    _setup_basic_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="忽略缓存重新解析全部")
    parser.add_argument("--print", action="store_true", help="只打印当前缓存表")
    args = parser.parse_args()

    cfg = load_config()
    person_dir = cfg.get("watched_persons_dir", "")

    if args.print:
        cache = _load_cache()
        for k, v in sorted(cache.items()):
            print(f"  {v}  {k}")
        print(f"total: {len(cache)}")
        return

    log.info("预热 watched_wca_ids,源目录: %s", person_dir)
    cache = warm_cache(person_dir, refresh=args.refresh)

    # 显示未解析的目录方便人工补 wca_id.txt
    missing = [k for _d, k in list_watched_dirs(person_dir) if k not in cache or not cache[k]]
    log.info("解析完成: %d 个有 wcaId, %d 个缺", len(cache), len(missing))
    if missing:
        log.warning("未解析(建议在目录里放 wca_id.txt): %s", ", ".join(missing))


if __name__ == "__main__":
    main()
