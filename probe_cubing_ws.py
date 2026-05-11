"""
cubing.com WS 协议探针 — 摸清实时推送的消息结构。

跑法:
  python probe_cubing_ws.py 1548          # Please Be Quiet Hefei 2026
  python probe_cubing_ws.py 1548 --seconds 600

输出:
  - 列出所有出现过的 type
  - 每种 type 第一次出现时打印完整 payload
  - 之后只打印 type + 顶层 key + 关键字段摘要
  - 重点抓含 "record" / "tag" / "AsR" / "NR" / "WR" 等字段的消息
"""
import argparse
import json
import sys
import time
from collections import defaultdict

import websocket

WS_URL = "wss://cubing.com/ws"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cid", type=int)
    ap.add_argument("--seconds", type=int, default=300)
    ap.add_argument("--events", default="", help="逗号分隔事件 ID;空=全部")
    args = ap.parse_args()

    ws = websocket.create_connection(
        WS_URL, timeout=20,
        origin="https://cubing.com",
        header=["User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"],
    )
    ws.settimeout(20)
    print(f"[connect] cid={args.cid}", flush=True)
    ws.send(json.dumps({"type": "competition", "competitionId": args.cid}))

    # 先拉一次全量,顺便看 'a'/'b' 之外有没有 record 标记字段
    for eid in (args.events.split(",") if args.events else ["333", "222", "444", "333oh", "pyram", "skewb"]):
        if not eid:
            continue
        for rid in ("1", "2", "3", "f"):
            ws.send(json.dumps({
                "type": "result", "action": "fetch",
                "params": {"event": eid, "round": rid, "filter": "all"},
            }))

    seen_types = defaultdict(int)
    first_seen = {}
    deadline = time.time() + args.seconds
    hits_record_keys = 0

    while time.time() < deadline:
        try:
            raw = ws.recv()
        except websocket.WebSocketTimeoutException:
            print("[idle] no message in 20s", flush=True)
            continue
        except Exception as e:
            print(f"[error] {e}", flush=True)
            break
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except Exception:
            print(f"[raw-non-json] {raw[:200]!r}", flush=True)
            continue

        t = msg.get("type", "?")
        seen_types[t] += 1
        top_keys = sorted(msg.keys())
        flat = json.dumps(msg, ensure_ascii=False)
        record_hit = any(k in flat for k in ('"tag"', '"record"', '"AsR"', '"WR"', '"NR"', 'asr', 'asianRecord'))

        if t not in first_seen:
            first_seen[t] = True
            preview = flat if len(flat) < 1500 else flat[:1500] + "..."
            print(f"\n[NEW TYPE #{seen_types[t]}] type={t} keys={top_keys}\n  {preview}", flush=True)
        else:
            # 简短摘要
            data = msg.get("data")
            shape = type(data).__name__
            n = len(data) if hasattr(data, "__len__") else "-"
            extra = ""
            if isinstance(data, list) and data and isinstance(data[0], dict):
                extra = f" item_keys={sorted(data[0].keys())}"
            elif isinstance(data, dict):
                extra = f" data_keys={sorted(data.keys())[:8]}"
            print(f"[{t} #{seen_types[t]}] keys={top_keys} data={shape}(len={n}){extra}", flush=True)

        if record_hit:
            hits_record_keys += 1
            preview = flat if len(flat) < 2000 else flat[:2000] + "..."
            print(f"  >>> RECORD-LIKE FIELD HIT #{hits_record_keys}: {preview}", flush=True)

    ws.close()
    print("\n=== summary ===")
    for t, c in sorted(seen_types.items(), key=lambda x: -x[1]):
        print(f"  {t}: {c}")
    print(f"record-like hits: {hits_record_keys}")


if __name__ == "__main__":
    main()
