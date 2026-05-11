"""
对 cid=1548 (Please-Be-Quiet-Hefei-2026):
1) 从 live 页拉所有 event/round
2) 通过 WS 拉每个 round 的 result.all
3) 列出所有 sr / ar 非空的行
4) 保持连接监听,捕获任何实时 push 消息
"""
import argparse, html, json, re, sys, time, urllib.request
import websocket

WS_URL = "wss://cubing.com/ws"
LIVE_URL = "https://cubing.com/live/{slug}?lang=en"


def fetch_meta(slug):
    body = urllib.request.urlopen(
        urllib.request.Request(LIVE_URL.format(slug=slug),
                               headers={"User-Agent": "Mozilla/5.0"}),
        timeout=30).read().decode("utf-8")
    cid = int(re.search(r'data-c="(\d+)"', body).group(1))
    events = json.loads(html.unescape(re.search(r'data-events="([^"]+)"', body).group(1)))
    return cid, events


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slug", default="Please-Be-Quiet-Hefei-2026", nargs="?")
    ap.add_argument("--listen", type=int, default=180, help="拉完一遍后保持连接监听秒数")
    args = ap.parse_args()

    cid, events = fetch_meta(args.slug)
    rounds = [(ev["i"], rd["i"], rd.get("n", 0), rd.get("name", ""))
              for ev in events for rd in ev["rs"]]
    print(f"cid={cid} events={len(events)} rounds={len(rounds)}")
    for e, r, n, name in rounds:
        print(f"  {e:8s} round={r:2s} submissions={n:3d}  {name}")

    ws = websocket.create_connection(
        WS_URL, timeout=20, origin="https://cubing.com",
        header=["User-Agent: Mozilla/5.0"],
    )
    ws.settimeout(20)
    ws.send(json.dumps({"type": "competition", "competitionId": cid}))

    # 一次性 fetch 所有 round
    for e, r, _, _ in rounds:
        ws.send(json.dumps({
            "type": "result", "action": "fetch",
            "params": {"event": e, "round": r, "filter": "all"},
        }))

    user_map = {}
    record_rows = []
    received = 0
    deadline = time.time() + 60

    print("\n--- collecting initial snapshot ---")
    while time.time() < deadline and received < len(rounds):
        try:
            raw = ws.recv()
        except websocket.WebSocketTimeoutException:
            break
        except Exception as e:
            print(f"[recv-error] {e}")
            break
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except Exception:
            print(f"[non-json] {raw[:200]!r}")
            continue
        t = msg.get("type")
        if t == "users":
            for k, v in msg.get("data", {}).items():
                user_map[int(k)] = v
        elif t == "result.all":
            received += 1
            for row in msg.get("data", []):
                if row.get("sr") or row.get("ar"):
                    record_rows.append(row)
        else:
            print(f"  [unexpected during snapshot] {t}: {json.dumps(msg, ensure_ascii=False)[:500]}")

    print(f"\nsnapshot done: rounds_received={received}, users={len(user_map)}, record_rows={len(record_rows)}")
    print("\n--- record rows ---")
    for r in record_rows:
        u = user_map.get(r["n"], {})
        print(json.dumps({**r, "_who": u.get("name"), "_wcaid": u.get("wcaid"), "_region": u.get("region")}, ensure_ascii=False))

    # 统计 sr/ar 出现过的所有非空值
    tags_seen = set()
    for r in record_rows:
        if r.get("sr"):
            tags_seen.add(("sr", r["sr"]))
        if r.get("ar"):
            tags_seen.add(("ar", r["ar"]))
    print(f"\nrecord-tag values observed: {sorted(tags_seen)}")

    # 保持连接监听,看实时 push
    print(f"\n--- listening for {args.listen}s for push messages ---")
    ws.settimeout(args.listen)
    end = time.time() + args.listen
    push_count = 0
    while time.time() < end:
        try:
            raw = ws.recv()
        except websocket.WebSocketTimeoutException:
            print("[idle: no message]")
            break
        except Exception as e:
            print(f"[listen-end] {e}")
            break
        if not raw:
            continue
        push_count += 1
        try:
            msg = json.loads(raw)
            t = msg.get("type")
            preview = json.dumps(msg, ensure_ascii=False)
            print(f"[PUSH #{push_count}] type={t}\n  {preview[:1500]}")
        except Exception:
            print(f"[PUSH #{push_count} raw] {raw[:500]!r}")

    ws.close()
    print(f"\ndone. pushes received: {push_count}")


if __name__ == "__main__":
    main()
