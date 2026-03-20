# WCA API 参考文档

本项目使用两套 WCA API：REST API（官方数据，有延迟）和 WCA Live GraphQL API（实时数据）。

---

## 1. WCA REST API

**Base URL:** `https://www.worldcubeassociation.org/api/v0`

### 1.1 搜索选手

```
GET /search/users?q={name}&persons_table=true
```

- 返回 `{"result": [{wca_id, name, country_iso2, ...}, ...]}`
- `persons_table=true` 只搜正式选手（排除非选手的 WCA 账号）
- 同名选手会返回多个结果

**注意事项：**
- WCA 名字可能含本地名，如 `Seung Hyuk Nahm (남승혁)`
- YouTube 频道名常用连字符（`Seung-Hyuk`），WCA 用空格（`Seung Hyuk`），搜索前需替换
- 精确匹配时需去掉括号内的本地名再比较

### 1.2 选手成绩

```
GET /persons/{wca_id}/results
```

- 返回该选手所有历史成绩，按时间顺序排列（三阶通常 200+ 条）
- **延迟：比赛结束后可能几天才同步**

**返回字段（每条记录）：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int | 记录唯一 ID |
| `round_id` | int | 轮次内部 ID |
| `pos` | int | 该轮排名 |
| `best` | int | 最佳单次（厘秒） |
| `average` | int | 平均（厘秒）；`0` 表示无有效平均（如 BestOf3 格式） |
| `name` | string | 选手名（含本地名，如 `"Xuanyi Geng (耿暄一)"`) |
| `country_iso2` | string | 国家 ISO 代码 |
| `competition_id` | string | 比赛 ID |
| `event_id` | string | 项目 ID（如 `"333"`, `"222"`, `"pyram"`） |
| `round_type_id` | string | 轮次类型（见下表） |
| `format_id` | string | 格式（`"a"` = Ao5, `"m"` = Mo3, `"1"`/`"2"`/`"3"` = BestOf1/2/3） |
| `wca_id` | string | 选手 WCA ID |
| `attempts` | int[] | 每轮全部单次成绩（厘秒）；元素数由 `format_id` 决定（Ao5=5, Mo3=3, BestOfN=N） |
| `best_index` | int | `attempts` 中最佳成绩的索引（0-based） |
| `worst_index` | int | `attempts` 中最差成绩的索引（0-based） |
| `regional_single_record` | string? | 单次纪录标记（`"WR"`, `"CR"`, `"NR"` 或 `null`） |
| `regional_average_record` | string? | 平均纪录标记（同上） |

**`round_type_id` 常见值：**

| ID | 轮次 |
|----|------|
| `"1"` | First round |
| `"d"` | Combined First round |
| `"2"` | Second round |
| `"b"` | Combined Second round |
| `"3"` | Semi Final |
| `"c"` | Combined Final |
| `"f"` | Final |

**`attempts` 特殊值：**

| 值 | 含义 |
|----|------|
| `> 0` | 有效成绩（厘秒），如 `442` = 4.42 秒 |
| `-1` | DNF |
| `-2` | DNS |
| `0` | 未参加（如 BestOf3 格式第 3 次未执行） |

**真实响应示例**（耿暄一，仙居 NxN 2026 三阶决赛）：

```json
{
  "id": 7980380,
  "round_id": 932073,
  "pos": 1,
  "best": 402,
  "average": 426,
  "name": "Xuanyi Geng (耿暄一)",
  "country_iso2": "CN",
  "competition_id": "XianjuNxN2026",
  "event_id": "333",
  "round_type_id": "f",
  "format_id": "a",
  "wca_id": "2023GENG02",
  "attempts": [523, 442, 409, 402, 426],
  "best_index": 3,
  "worst_index": 0,
  "regional_single_record": null,
  "regional_average_record": null
}
```

### 1.3 选手参加的比赛

```
GET /persons/{wca_id}/competitions
```

- 返回选手参加过的所有比赛（有结果的），按时间顺序排列
- 每条含 `id`, `name`, `country_iso2`, `start_date`
- 最新的比赛可能因成绩未同步而不在列表中

### 1.4 排名

```
GET /results/rankings/{event_id}/{type}
Header: Accept: application/json
```

- `type` = `single` 或 `average`
- 返回 `{"rows": [{pos, best, average, person_name, ...}, ...]}`
- 需要模拟浏览器 `Accept: application/json` 头，否则返回 HTML
- 本项目缓存 3 天（`rankings_cache.json`），手动刷新：删除缓存文件即可

---


## 2. WCA Live GraphQL API

**Endpoint:** `POST https://live.worldcubeassociation.org/api`  
**Content-Type:** `application/json`  
**Body:** `{"query": "{ ... }"}`

### 2.1 近期纪录

```graphql
{ recentRecords {
    id type tag
    result { person { name country { iso2 } }
             best average
             round { competitionEvent { event { id name }
                     competition { id name } } } }
    attemptResult
} }
```

- 返回约 90 条近期纪录（WR/CR/NR）
- `type` = `"single"` 或 `"average"`
- `tag` = `"WR"`, `"CR"`, `"NR"` 等
- `attemptResult` 用于反查具体还原视频（单次纪录时有值）

### 2.2 比赛列表

```graphql
{ competitions(from: "2026-03-01") {
    id name startDate endDate
    venues { country { iso2 } }
    competitors { wcaId }
} }
```

- `from` 参数过滤起始日期（ISO 格式），返回该日期之后的所有比赛
- **包含 `competitors` 时可一次拿到所有参赛者的 wcaId**，用于本地匹配
- 不带 `from` 返回全部（~640 场），建议带日期缩小范围
- **复杂度限制 5000**：查 `competitionEvents > rounds > results` 会超限，但 `competitors { wcaId }` 不会

### 2.3 单场比赛详情

```graphql
{ competition(id: "10109") {
    name
    venues { country { iso2 } }
    competitors { wcaId name country { iso2 } }
} }
```

- `id` 是 WCA Live 内部数字 ID（非 WCA 比赛 ID 字符串）
- 查全部 `competitionEvents > rounds > results` 容易超复杂度限制

### 2.4 不可用的查询

```graphql
# person(id) — id 是 WCA Live 内部 ID，不是 WCA ID
# 且是「比赛内的参赛者」概念，不支持全局按 WCA ID 查人
{ person(id: "2018KHAN28") { name } }  # → Bad Request
{ person(id: 12345) { name } }         # → null（需要知道内部 ID）
```

---

## 3. 项目中的典型使用流程

### 纪录视频标题（主路径）

```
WCA Live recentRecords → 匹配关键词/成绩 → format_record_message
```

### 非纪录视频标题（回退路径）

```
1. _extract_title_parts    — 从视频标题拆出 成绩/项目/类型
2. search_wca_person       — REST API 搜选手（处理连字符、重名）
3. find_competition_by_result — REST API 按成绩反查比赛
4. find_latest_live_competition — WCA Live 补充最新比赛（REST 有延迟）
5. format_general_title    — 组装中英文标题
```

### 排名数据

```
REST API /results/rankings → 本地缓存 rankings_cache.json（3天有效）
→ 用于标题中 /WRxx 后缀
```

---

## 4. 常见坑

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| REST API 成绩匹配到旧比赛 | 成绩同步有几天延迟 | 用 WCA Live `competitions(from)` 补充查最新比赛 |
| WCA 搜人返回错误的人 | 同名选手（如两个 Brian Sun） | 精确名字匹配 + 成绩消歧 |
| YouTube 频道名搜不到人 | 连字符 vs 空格（Seung-Hyuk vs Seung Hyuk） | 搜索前 `-` → 空格 |
| 频道名含非拉丁字符 | 如 `남승혁`（韩文） | 过滤非 ASCII 字符后再搜 |
| WCA Live 查询超复杂度 | 查 competitionEvents > rounds > results 超 5000 | 只查 competitors 不查成绩 |
| 厘秒转换精度问题 | `float('4.89') * 100 = 488.999...` | 用 `round()` 而非 `int()` |

---

## 5. WCA REST API — 比赛列表

```
GET /competitions?sort=-announced_at&per_page=50
```

- `sort=-announced_at` 按公布时间倒序（最新的在前）
- `per_page` 控制每页数量（默认 25）
- 每条含 `id`, `name`, `country_iso2`, `start_date`, `date_range`, `city`, `event_ids`, `competitor_limit`, `url`
- 使用者：`wca_comp_monitor.py`（轮询新比赛推送通知）

---

## 6. 粗饼网 API

**Base URL:** `https://cubing.com/api`

```
GET /competition
Header: Accept: application/json
```

- 返回 `{"data": [{id, name, date: {from, to}, locations: [...], ...}, ...]}`
- `date.from` / `date.to` 是 Unix 时间戳
- `locations[0]` 含 `province`, `city`
- 使用者：`cubing_com_monitor.py`（轮询新比赛推送通知）

---

## 7. Bark 推送 API

**Endpoint:** `POST {bark_server}/push`

```json
{
  "device_key": "设备密钥",
  "title": "标题",
  "body": "正文",
  "url": "点击链接",
  "group": "分组名",
  "level": "timeSensitive",
  "isArchive": "1"
}
```

- `bark_server` 默认 `https://api.day.app`
- `group` 用于通知分组：`"WCA Records"` / `"cubing-comp"` / `"wca-comp"`
- `level` = `"timeSensitive"` 即使静音也显示通知
- 返回 `{"code": 200}` 表示成功
- 使用者：`monitor_utils.py` `send_bark()`
