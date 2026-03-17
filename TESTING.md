# gen_title.py 测试指南

## 概述

`gen_title.py` 有两条执行路径，**选手身份始终先确认**：

1. **选手身份识别** — 选手名一律从 `--uploader` 获取，查 `channel_aliases.json` 得到 WCA ID
2. **纪录路径（优先）** — 用 WCA ID 过滤 `recentRecords`（只看该选手的纪录），再用关键词匹配
3. **Fallback 路径** — 纪录路径未命中，通过 WCA API 查选手历史成绩和比赛

## 测试命令

### 纪录视频（路径 2）

```bash
# 纪录快讯（命中 WCA Live recentRecords 且该选手确实有纪录）
python gen_title.py "5.55 3x3 NR Average Nahm" --uploader "Seung Hyuk Nahm"

# 列出所有近期纪录（无需 uploader）
python gen_title.py --list

# 写入文件（配合自动上传）
python gen_title.py "Nahm 5.55" --uploader "Seung Hyuk Nahm" --write D:\cube\upload-video --auto
```

### 非纪录视频（路径 3 — fallback）

```bash
# 基本用法：标题 + 频道名
python gen_title.py "4x4x4 PR single 19.02" --uploader "Teodor Zajder"

# 带频道 ID（用于 channel_aliases.json 匹配和自动缓存）
python gen_title.py "2.35 Clock Avg" --uploader "Volodymyr K" --channel-id "UCZUeFOwc3wSxRL0P7P6Xbwg"
```

## 关键参数

| 参数 | 说明 | 必需 |
|------|------|------|
| 第一个位置参数 | 视频标题（供解析成绩/项目/类型） | ✅ |
| `--uploader` | YouTube 频道名 → 选手名的**唯一来源** | ✅ |
| `--channel-id` | YouTube 频道 ID → 查/写 `channel_aliases.json` | 可选 |
| `--write <目录>` | 将标题写入 `info_chs.md` / `info_eng.md` | 可选 |
| `--auto` | 非交互模式，匹配失败静默退出 | 可选 |
| `--list` | 列出所有近期纪录 | 可选 |

## 核心设计原则

### 选手名一律从 `--uploader` 获取

**禁止从视频标题猜测选手名。** 标题中的人名、噪音词（`PR`, `WR21`, `CR5`, `Rubik's`）全部忽略。

### 纪录匹配先过滤选手

有 `--uploader` 时，先用 WCA ID 过滤 `recentRecords`，**只在该选手的纪录中做关键词匹配**。
杜绝标题关键词碰巧匹配到其他选手纪录的问题（如 `4x4 + single` 匹配到别人的 NR）。

## 核心测试用例

### 1. 事件识别（`_EVENT_ALIAS`）

| 输入 | 期望匹配 |
|------|----------|
| `3x3`, `3x3x3`, `cube` | 3x3x3 Cube (三阶魔方) |
| `4x4`, `4x4x4` | 4x4x4 Cube (四阶魔方) |
| `2x2` ~ `7x7`, `2x2x2` ~ `7x7x7` | 对应阶数魔方 |
| `oh` | 3x3x3 One-Handed (单手) |
| `3bld`, `4bld`, `5bld` | 盲拧 |
| `mega`, `megaminx` | Megaminx (五魔) |
| `pyra`, `pyraminx` | Pyraminx (金字塔) |
| `sq1`, `square-1` | Square-1 |
| `clock` | Clock |
| `fmc` | 3x3x3 Fewest Moves (最少步) |
| `mbld`, `multi`, `multibld` | 3x3x3 Multi-Blind (多盲) |
| `skewb` | Skewb |

**⚠️ 未识别的项目默认回退为 3x3x3 Cube。**

测试命令：
```bash
python gen_title.py "4x4x4 single 19.02" --uploader "Teodor Zajder"
# 期望: 四阶魔方，不是三阶

python gen_title.py "Clock Avg 2.35" --uploader "Volodymyr K" --channel-id "UCZUeFOwc3wSxRL0P7P6Xbwg"
# 期望: Clock
```

### 2. 选手身份验证

```bash
# 标题含别人的成绩/项目关键词，但 uploader 不同 → 不能匹配到别人的纪录
python gen_title.py "Rubik's cube WR holder - 4x4x4 PR single - 19.02 - WR21 / CR5" --uploader "Teodor Zajder"
# 期望: 纪录路径过滤后无命中 → fallback → 19.02 四阶单次 Teodor Zajder

# 选手确实有纪录 → 纪录路径命中
python gen_title.py "5.55 3x3 NR Average" --uploader "Seung Hyuk Nahm"
# 期望: 纪录快讯标题
```

### 3. 频道映射（`channel_aliases.json`）

```bash
# 缩写频道名：缓存命中
python gen_title.py "Clock Avg 2.35" --uploader "Volodymyr K" --channel-id "UCZUeFOwc3wSxRL0P7P6Xbwg"
# 期望: 频道映射命中 → Volodymyr Kapustianskyi (2022KAPU01)

# 精确频道名：首次自动缓存
python gen_title.py "4x4 single 25.00" --uploader "Max Park" --channel-id "UC_test"
# 期望: WCA API 搜到 Max Park → 自动写入 channel_aliases.json
```

### 4. 成绩与类型

| 成绩输入 | 期望 | 类型输入 | 期望 |
|----------|------|----------|------|
| `4.89` | 4.89 秒 | `single`, `s`, `solve` | 单次 |
| `19.02` | 19.02 秒 | `average`, `avg`, `a`, `ao5` | 平均 |
| `1:23.45` | 1:23.45 | `mean`, `mo3` | 平均 |
| 无类型关键词 | | | 默认为单次 |

## 频道映射维护

### `channel_aliases.json` 结构

```json
{
  "频道名": {
    "wca_id": "WCA ID",
    "channel_id": "YouTube 频道 ID（可选）"
  }
}
```

### 自动缓存

`fallback_wca_api` 通过 WCA API 搜到**唯一精确匹配**时，自动写入缓存。手动条目不会被覆盖。

### 批量填充

```bash
python build_channel_aliases.py "D:\cube\upload-video\subscriptions.txt"
```

增量运行，已有条目跳过，只保存精确匹配。缩写频道名需手动维护。

## 输出格式

```
info_chs: {成绩}{中文项目名}{中文类型} {选手名}{国旗} | {比赛名}{国旗}
info_eng: {成绩} {英文项目缩写} {英文类型} {选手名}{国旗} | {比赛名}{国旗}
```

## 常见问题排查

| 现象 | 原因 | 解决 |
|------|------|------|
| 项目错误（如 4x4 显示为 3x3） | `_EVENT_ALIAS` 缺少对应别名 | 在别名表中添加 |
| 匹配到别人的纪录 | 纪录列表未按选手过滤 | 必须传 `--uploader` |
| "未找到 WCA 选手" | 频道名与 WCA 真名不匹配 | 手动加入 `channel_aliases.json` |
| 成绩匹配 0 条 | REST API 延迟 | WCA Live 会自动补充最近比赛 |

## 批量测试

`test_input.csv` 包含测试用例，格式为 `Title,Uploader,Video URL,info_eng,info_chs`。

### 测试命令

```powershell
# 逐行读取 test.csv，运行 gen_title.py，完整输出到 test_output.txt
python run_tests.py
```

输出文件 `test_output.txt` 包含每个用例的完整运行日志和 PASS/FAIL 判定。
