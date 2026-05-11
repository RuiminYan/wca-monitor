"""探测 cubing.com result row 里有没有 PR 字段。
拉 Deqing 333 final 全部 24 行,打印所有字段并列出"非空 sr/ar 之外的有趣字段"。"""
import json, time
import websocket

WS_URL = "wss://cubing.com/ws"
CID = 1541  # Deqing-Small-Special-2026

ws = websocket.create_connection(
    WS_URL, timeout=20, origin="https://cubing.com",
    header=["User-Agent: Mozilla/5.0"],
)
ws.settimeout(15)
ws.send(json.dumps({"type": "competition", "competitionId": CID}))
ws.send(json.dumps({
    "type": "result", "action": "fetch",
    "params": {"event": "333", "round": "f", "filter": "all"},
}))

rows = []
deadline = time.time() + 20
while time.time() < deadline:
    try:
        raw = ws.recv()
    except Exception:
        break
    if not raw:
        continue
    msg = json.loads(raw)
    if msg.get("type") == "result.all":
        rows = msg.get("data", [])
        break
ws.close()

print(f"Got {len(rows)} rows. Listing union of all keys:")
all_keys = set()
for r in rows:
    all_keys.update(r.keys())
print(f"  UNION: {sorted(all_keys)}")

print("\nAll 24 rows (full dump):")
for r in sorted(rows, key=lambda x: x.get("n", 0)):
    print(f"  {json.dumps(r, ensure_ascii=False)}")

# 张博藩 n=130
print("\n>>> n=130 (Bofan Zhang):")
for r in rows:
    if r.get("n") == 130:
        print(json.dumps(r, ensure_ascii=False, indent=2))
