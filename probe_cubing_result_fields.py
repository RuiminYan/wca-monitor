"""
对带成绩的比赛拉一轮 result.all,打印一条 result 的所有字段,
重点检查有没有 record / tag / NR / AsR / WR 之类的标记。
"""
import argparse, json, time, sys
import websocket

WS_URL = "wss://cubing.com/ws"


def fetch_round(cid: int, event: str, rnd: str):
    ws = websocket.create_connection(
        WS_URL, timeout=20, origin="https://cubing.com",
        header=["User-Agent: Mozilla/5.0"],
    )
    ws.settimeout(15)
    ws.send(json.dumps({"type": "competition", "competitionId": cid}))
    ws.send(json.dumps({
        "type": "result", "action": "fetch",
        "params": {"event": event, "round": rnd, "filter": "all"},
    }))
    rows = []
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            raw = ws.recv()
        except Exception as e:
            print(f"[recv-end] {e}")
            break
        if not raw:
            continue
        msg = json.loads(raw)
        if msg.get("type") == "result.all":
            rows = msg.get("data", [])
            break
    ws.close()
    return rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cid", type=int)
    ap.add_argument("--events", default="333,222,444,555,666,777,333oh,333bf,pyram,skewb,minx,sq1,clock,333fm,444bf,555bf,333mbf")
    args = ap.parse_args()

    for eid in args.events.split(","):
        for rnd in ("1", "2", "3", "f"):
            rows = fetch_round(args.cid, eid, rnd)
            if not rows:
                continue
            print(f"\n=== event={eid} round={rnd} rows={len(rows)} ===")
            # 取前 3 条完整 dump
            for r in rows[:3]:
                print(json.dumps(r, ensure_ascii=False))
            # 字段并集
            all_keys = set()
            for r in rows:
                all_keys.update(r.keys())
            print("UNION keys:", sorted(all_keys))
            # 任何成绩字段里含 record-like 值的
            flat = json.dumps(rows, ensure_ascii=False)
            for kw in ("record", "Record", "tag", "AsR", "asr", "WR", "NR", "CR"):
                if f'"{kw}"' in flat or f'":{kw}' in flat:
                    print(f"  HIT keyword: {kw}")
            # 找 sr / ar 非空的行(可能的 record 标记)
            for r in rows:
                if r.get("sr") or r.get("ar"):
                    print(f"  RECORD-LIKE ROW: {json.dumps(r, ensure_ascii=False)}")
