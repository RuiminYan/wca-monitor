"""下载粗饼比赛列表数据"""
import requests, json, time

for attempt in range(3):
    r = requests.get(
        "https://cubing.com/api/competition",
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        timeout=10,
    )
    print(f"Attempt {attempt+1}: status={r.status_code}, len={len(r.text)}")
    if r.status_code == 200 and r.text.strip():
        break
    time.sleep(2)

data = r.json()["data"]
print(f"Total: {len(data)} competitions")

with open("D:/cube/upload-video/wca/cubing_competitions.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print("Saved to cubing_competitions.json")
