"""Bark 文案格式化 CLI:cuberoot.me Hono server 跨 repo 调用入口。

协议:
  stdin  ← JSON `{"events": [<format_record_message kwargs>, ...]}`
  stdout → JSON `{"cn": "...", "en": "...", "url": "..."}`

events 长度 1 = 单条,长度 2 = 同 round 合并(走 format_combined_records)。
错误时返 `{"error": "..."}` 并 exit 1。

依赖 `/opt/wca-monitor/` 整个目录(record_format + wca_rankings cache),
所以 Hono spawn 必须 cwd=该目录(或 PYTHONPATH=)。
"""
import json
import sys

from record_format import (
    format_combined_records, EVENT_NAME_BY_ID, COUNTRY_CN_MAP,
)
from wca_pr_cache import is_tied_value
from wca_rankings import RANKINGS


def _read_stdin_utf8() -> str:
    """直接读 binary buffer 解 UTF-8,绕过 sys.stdin.encoding (Windows GBK / 老服务器
    Python 3.6 默认 ASCII 都会挂)。"""
    return sys.stdin.buffer.read().decode("utf-8")


def _write_stdout_utf8(s: str) -> None:
    sys.stdout.buffer.write(s.encode("utf-8"))


def _enrich(ev: dict) -> dict:
    """给前端少传字段:event_name / person_country_en / tied 由 API 端补全,
    前端只传 raw 数据(成绩值 + 历史 PR 值),tag/tied 等业务判定全在 Python 单源。"""
    if not ev.get("event_name") and ev.get("event_id"):
        ev["event_name"] = EVENT_NAME_BY_ID.get(ev["event_id"], ev["event_id"])
    if not ev.get("person_country_en"):
        # 仅 NR 模板用到,PR/WR/CR 不用;前端通常不传,这里给空串
        ev["person_country_en"] = COUNTRY_CN_MAP.get(ev.get("person_iso2", ""), "")
    # 前端传 previous_pr(从 wca_pb 拉的历史 PR),这里转成 tied(同 wca_pr_cache.is_tied_pr 判定)
    prev = ev.pop("previous_pr", None)
    if "tied" not in ev:
        ev["tied"] = is_tied_value(ev.get("attempt_result", 0), prev)
    return ev


def main():
    try:
        payload = json.loads(_read_stdin_utf8())
        events = payload.get("events") or []
        if not events:
            raise ValueError("events 为空")
        events = [_enrich(e) for e in events]
        # RANKINGS.update_all 优先用磁盘 cache (3天有效);cache 失效会拉网络,
        # 服务器 wca-record-monitor 启动时已写过 cache,通常 ~ms 完事
        RANKINGS.update_all()
        cn, en, url = format_combined_records(events)
        _write_stdout_utf8(json.dumps(
            {"cn": cn, "en": en, "url": url}, ensure_ascii=False))
    except Exception as e:
        _write_stdout_utf8(json.dumps({"error": str(e)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
