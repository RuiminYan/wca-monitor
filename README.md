# WCA 监控套件

监控 [WCA Live](https://live.worldcubeassociation.org/) 的 **WR/CR/NR 纪录** 和 [粗饼网](https://cubing.com) 的 **新比赛公示**，通过 [Bark](https://github.com/Finb/Bark) 实时推送到 iPhone，可选邮件通知。

## 功能

- **纪录监控** (`wca_record_monitor.py`) — 监控 WCA Live 的世界纪录、洲际纪录、国家纪录
- **粗饼比赛监控** (`cubing_com_monitor.py`) — 监控粗饼网新比赛发布（中国大陆）
- **WCA 比赛监控** (`wca_comp_monitor.py`) — 监控 WCA 官网新比赛发布（全球）
- **邮件通知** (`email_notifier.py`) — 新比赛和 WR 时发送邮件（可选，需配置 SMTP）

## 快速开始

### 1. 安装 Bark App

在 iPhone App Store 搜索 **Bark**，安装后打开，复制首页显示的设备密钥。

> 推送 URL 格式为 `https://api.day.app/XXXXXX/...`，其中 `XXXXXX` 即为设备密钥。

### 2. 创建配置文件

将 `config.example.json` 复制为 `config.json`，填入你的设备密钥：

```json
{
  "bark_device_key": "你的设备密钥",
  "bark_server": "https://api.day.app",
  "poll_interval": 60,
  "tags": ["WR", "CR", "NR"],
  "nr_countries": ["CN", "US", "AU"]
}
```

| 字段 | 说明 | 默认值 |
|---|---|---|
| `bark_device_key` | Bark 设备密钥（必填） | - |
| `bark_server` | Bark 服务器地址 | `https://api.day.app` |
| `poll_interval` | 轮询间隔（秒） | `60` |
| `tags` | 监控的纪录类型 | `["WR", "CR"]` |
| `nr_countries` | NR 国家过滤（ISO2 代码） | `[]` |
| `email_enabled` | 是否启用邮件通知 | `false` |
| `email_sender` | 发件人邮箱 | - |
| `email_recipients_record` | 纪录快讯（WR）邮件收件人 | `[]` |
| `email_recipients_competition` | 新比赛公示邮件收件人 | `[]` |

> `tags` 可选值：`WR`（世界纪录）、`CR`（洲际纪录）、`NR`（国家纪录）。
> 添加 `NR` 时建议配合 `nr_countries` 过滤，否则通知量很大。
>
> 邮件仅在 **新比赛** 和 **WR** 时发送，CR/NR 不发邮件（Bark 照常推送所有纪录）。
### 3. 邮件通知（可选）

邮件通过 Gmail API (OAuth2) 发送，完全绕过 SMTP 端口限制。

**首次配置：**

1. 打开 [Google Cloud Console](https://console.cloud.google.com/)
2. 启用 **Gmail API**（API 和服务 → 库 → 搜索 Gmail API → 启用）
3. 创建 OAuth 凭据（凭据 → 创建凭据 → OAuth 客户端 ID → 桌面应用）
4. 下载 JSON，重命名为 `credentials.json`，放到本目录
5. 运行授权：
   ```bash
   python email_notifier.py
   ```
   浏览器弹出 Google 登录页，授权后会自动生成 `token.json`，后续无需再次授权。

6. 在 `config.json` 中启用：
   ```json
   "email_enabled": true,
   "email_sender": "你的邮箱@gmail.com",
   "email_recipients_record": ["关注纪录的人"],
   "email_recipients_competition": ["关注比赛的人"]
   ```
   > 两个收件人列表独立配置，支持不同的人接收不同类型的通知。

> 邮件仅在 **新比赛** 和 **WR** 时发送，CR/NR 不发邮件（Bark 照常推送所有纪录）。

### 4. 启动监控

**纪录监控：**
```bash
python wca_record_monitor.py
```

**粗饼比赛监控（中国大陆）：**
```bash
python cubing_com_monitor.py
```

**WCA 比赛监控（全球）：**
```bash
python wca_comp_monitor.py
```

首次运行会静默记录当前所有数据（不推送），之后只推送新出现的纪录/比赛。

### 5. 自动启动（可选）

安装后会注册三个计划任务：
- **WCA Record Monitor** — 周五至周一运行（比赛集中在周末）
- **Cubing Competition Monitor** — 每天 0:00 启动，持续运行，每 1 分钟轮询
- **WCA Competition Monitor** — 每天 0:00 启动，持续运行，每 1 分钟轮询（全球）

**Windows：**
```powershell
# 安装（覆盖已有任务）
powershell -ExecutionPolicy Bypass -File install_task.ps1
# 卸载
powershell -ExecutionPolicy Bypass -File install_task.ps1 -Uninstall
```

**macOS：**
```bash
# 安装
bash install_task.sh
# 卸载
bash install_task.sh --uninstall
```

### 6. 测试推送

使用 `test_push.py` 可以随时手动测试推送是否正常：

```bash
# === 纪录快讯 ===
python test_push.py record --dry-run     # 预览最新 10 条纪录（不推送）
python test_push.py record 3 --dry-run   # 预览最新 3 条
python test_push.py record               # 推送最新 10 条纪录到手机
python test_push.py record 5             # 推送最新 5 条
python test_push.py record --tags WR,CR  # 只推送 WR 和 CR（过滤 NR）

# === 新比赛通知 ===
python test_push.py comp --dry-run       # 预览最新 10 条比赛（不推送）
python test_push.py comp 3               # 推送最新 3 条比赛到手机

# === 同时测试邮件 ===
python test_push.py record 1 --email     # 推送 1 条纪录到手机 + 发邮件
python test_push.py comp 1 --email       # 推送 1 条比赛到手机 + 发邮件
```

> 此脚本不会修改 `known_ids.json`，可放心反复测试。
> `--email` 仅在非 `--dry-run` 模式下生效，收件人分别对应 `email_recipients_record` 和 `email_recipients_competition`。
> `--tags` 先过滤再取 count 条，支持逗号分隔多个 tag（WR/CR/NR）。

> **WCA 比赛测试：**
> ```bash
> python test_push.py wca-comp --dry-run     # 预览最新 10 条 WCA 比赛
> python test_push.py wca-comp 3             # 推送最新 3 条 WCA 比赛到手机
> ```

### 7. 标题生成工具

转发纪录视频时，用 `gen_title.py` 快速生成规范的中英文标题：

```bash
# 命令行模式：输入关键词即可匹配
python gen_title.py "5.55 3x3 NR Average" "Nahm"

# 交互模式：循环输入关键词搜索
python gen_title.py

# 列出所有近期纪录
python gen_title.py --list
```

输出示例：
```
标题: 纪录快讯! 5.55三阶魔方平均韩国纪录🇰🇷NR/WR43 Seung Hyuk Nahm | Paradise Park Bangkok NxNxN 2026🇹🇭
正文: Breaking News! 5.55 3x3 🇰🇷 NR/WR43 Avg Seung Hyuk Nahm | Paradise Park Bangkok NxNxN 2026🇹🇭
链接: https://live.worldcubeassociation.org/competitions/10095/rounds/133501
```

> 只能匹配 WCA Live `recentRecords` 中的纪录（最近 ~200 条），已过期的纪录无法使用。

## 通知格式

严格遵循 WCA 纪录快讯模板，中英双语推送：

**WR 示例：**
```
标题: 纪录快讯! 2.76三阶魔方单次世界纪录WR Teodor Zajder🇵🇱| GLS Big Cubes Gdańsk 2026🇵🇱
正文: BREAKING NEWS! 2.76 3x3 WR Single Teodor Zajder🇵🇱| GLS Big Cubes Gdańsk 2026🇵🇱
```

**CR 示例：**
```
标题: 纪录快讯! 22.70五魔单次亚洲纪录AsR 吴子钰🇨🇳| Guangdong Open 2026🇨🇳
正文: Breaking News! 22.70 Megaminx AsR Single Ziyu Wu🇨🇳| Guangdong Open 2026🇨🇳
```

**NR 示例：**
```
标题: 纪录快讯! 1.18二阶魔方平均韩国纪录🇰🇷NR/WR15 Kyeongmin Choi | Ansan Favorites 2026🇰🇷
正文: Breaking News! 1.18 2x2 🇰🇷 NR/WR15 Avg Kyeongmin Choi | Ansan Favorites 2026🇰🇷
```

> **排名说明**：如果 CR/NR 成绩进入世界前 100 名，会在缩写后追加 `/WRxx`（如 `/WR3` 表示世界第 3）。
> WR 成绩不显示额外排名（默认为世界第 1）。
> 排名数据通过 WCA 官网 JSON API 获取（Top 100），本地缓存 7 天有效，首次启动约需 30 秒。

**比赛公示示例：**
```
标题: 比赛公示快讯! 2026WCA珠海圆周率日魔方赛
正文: 📅 2026-03-14 | 📍 广东珠海 | 👥 159/220
```

> 中国/港澳台选手的中文通知使用中文名，英文通知使用英文名。

点击通知会跳转到对应比赛的轮次页面。

**WCA 比赛的通知示例：**
```
标题: 🌍WCA新赛! Teknostallen 2026
正文: 📅 Mar 28 - 29, 2026 | 📍 Trondheim 🇳🇴 | 🏷️ 10个项目 | 👥 上限1
```

## 数据来源

**纪录数据** — WCA Live GraphQL API（无需认证）：
```
POST https://live.worldcubeassociation.org/api
```

**比赛数据（粗饼网）** — 粗饼网 REST API：
```
GET https://cubing.com/api/competition
```

**比赛数据（WCA 官网）** — WCA REST API（无需认证）：
```
GET https://www.worldcubeassociation.org/api/v0/competitions
```

## 文件说明

| 文件 | 说明 | Git |
|---|---|---|
| `monitor_utils.py` | 共享底座模块（配置/推送/持久化/信号处理） | ✅ |
| `wca_record_monitor.py` | 纪录监控脚本 | ✅ |
| `wca_rankings.py` | 世界排名模块（Top 100，JSON API + 7d 缓存） | ✅ |
| `test_push.py` | 推送测试工具（`--dry-run` 可预览） | ✅ |
| `gen_title.py` | 纪录标题生成工具（转发视频用） | ✅ |
| `cubing_com_monitor.py` | 粗饼比赛监控脚本 | ✅ |
| `wca_comp_monitor.py` | WCA 比赛监控脚本（全球） | ✅ |
| `email_notifier.py` | 邮件通知模块（新比赛 + WR） | ✅ |
| `download_competitions.py` | 比赛数据下载工具 | ✅ |
| `README.md` | 本文档 | ✅ |
| `config.example.json` | 配置模板 | ✅ |
| `install_task.ps1` | Windows 自动启动（三任务） | ✅ |
| `install_task.sh` | macOS 自动启动 | ✅ |
| `deploy.sh` | Linux 服务器一键部署（systemd 服务） | ✅ |
| `config.json` | 你的配置（含密钥） | ❌ |
| `credentials.json` | Gmail API OAuth2 凭据 | ❌ |
| `token.json` | Gmail API 授权令牌（自动生成） | ❌ |
| `known_ids.json` | 已推送纪录 ID 缓存 | ❌ |
| `known_comp_ids.json` | 已推送粗饼比赛 ID 缓存 | ❌ |
| `known_wca_comp_ids.json` | 已推送 WCA 比赛 ID 缓存 | ❌ |
| `rankings_cache.json` | 世界排名缓存（7 天有效，自动更新） | ❌ |
| `cubing_competitions.json` | 比赛数据缓存（自动更新） | ❌ |

## 依赖

- Python 3.10+
- `requests` (`pip install requests`)
- `google-api-python-client`, `google-auth-oauthlib` (`pip install google-api-python-client google-auth-oauthlib`)（仅邮件通知需要）

## 服务器部署（阿里云 / Linux）

将监控部署到云服务器，实现 7×24 无人值守运行。

### 一键部署

```bash
# 1. 把 wca/ 目录上传到服务器
scp -r wca/ root@你的服务器IP:/opt/wca-monitor/

# 2. SSH 登录，运行部署脚本
ssh root@你的服务器IP
cd /opt/wca-monitor
sudo bash deploy.sh
```

部署脚本会自动：检查 Python 版本 → 安装依赖 → 部署文件 → 创建 systemd 服务 → 启动。

> 如需邮件通知，请在本地先完成 Gmail API OAuth2 授权（`python email_notifier.py`），再把 `token.json` 和 `credentials.json` 一起上传到服务器。

### 运维命令

```bash
bash deploy.sh --status            # 查看服务状态和最近日志
bash deploy.sh --uninstall         # 卸载服务
journalctl -u wca-record-monitor -f   # 实时查看纪录监控日志
journalctl -u wca-comp-monitor -f     # 实时查看比赛监控日志
systemctl restart wca-record-monitor  # 重启纪录监控
```

### 资源消耗

| 项目 | 消耗 |
|------|------|
| 月流量 | ~70 MB（纪录监控仅周五至周一运行） |
| 内存 | ~50 MB（两个 Python 进程合计） |
| CPU | 接近 0%（99.9% 时间在 sleep） |
| 磁盘 | < 1 MB（缓存文件）+ 日志由 journald 自动轮转 |

### 注意事项

- **服务器重启**：比赛监控开机自启；纪录监控由 systemd timer 控制（周五启动、周二停止），非比赛日重启后需手动 `systemctl start wca-record-monitor`
- **修改配置**：编辑 `/opt/wca-monitor/config.json` 后需重启对应服务
- **更新代码**：重新上传 `.py` 文件到 `/opt/wca-monitor/` 后重启服务即可
- **Gmail token**：长期有效，超 6 个月不使用可能需重新授权（不影响 Bark 推送）
- **删除缓存**：删除 `known_ids.json` / `known_comp_ids.json` 会重新进入静默期（不推送历史数据）
- **宝塔面板更新**：宝塔升级或重启 Nginx/MariaDB 不影响监控服务（独立的 systemd 进程）
- **网站迁移/重装**：监控安装在 `/opt/wca-monitor/`，与网站目录 `/www/wwwroot/` 完全隔离，互不影响
- **磁盘空间不足**：`journalctl --disk-usage` 查看日志占用；`journalctl --vacuum-size=100M` 可清理旧日志
- **服务器内存不足**：监控仅占 ~50 MB，如网站流量暴增导致内存紧张，可临时 `systemctl stop wca-record-monitor` 释放资源
- **API 变更**：如果 WCA Live 或粗饼网的 API 发生变更，监控会持续报错并自动重启（日志中可见），需要更新对应的 Python 代码