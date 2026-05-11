# CLAUDE.md

## 服务器
- 云服务器部署目录 `/opt/wca-monitor/`
- **统一用 SSH 别名 `cuberoot`**（已配 `~/.ssh/config`），不要写 `root@cuberoot.me`：`ssh cuberoot 'cmd'` / `scp file cuberoot:/path/`
- **服务器拉不到 GitHub**，禁用 `git pull` / `git clone` 假设
- 部署：本地跑 `D:\cube\wca-monitor\sync.ps1`（默认推全部 .py + 重启两个 record monitor，`-All` 重启四个服务）
- 终端不能 ssh 时（云控制台 Web 终端）：`base64 <file>` 编码 + heredoc 粘贴 + `base64 -d > 目标` 写入

## systemd 服务（四个）
- `wca-record-monitor` —— WCA Live 纪录快讯，**仅 Fri 00:00 → Tue 00:00 由 timer 控制**，非比赛日重启需手动 start
- `wca-cubing-record-monitor` —— cubing.com 中国比赛纪录快讯，全天候（依赖 `websocket-client`）
- `wca-comp-monitor` —— 粗饼新比赛，全天候
- `wca-wca-comp-monitor` —— WCA 新比赛，全天候

## 终端（pwsh 7）
- 跑含中文的 .py 前先 `[Console]::OutputEncoding=[Text.Encoding]::UTF8; $env:PYTHONIOENCODING='utf-8'`，否则 GBK 编码报错或乱码

## 改 `format_record_message`
- 共享实现在 `record_format.py`，两个 record monitor 都用它；`wca_record_monitor.format_record_message(record)` 是 thin adapter
- 返回签名固定为 `(cn, en, url)` 三元组，Bark 推送依赖
- `comp_name` 由 `format_record_message` 内部拼国旗（`{comp_name}{comp_flag}`），调用方传不含国旗的纯名字
- 模板规则记在 `breaking_news_prompt.md`（不存在源文件时按代码内注释为准）

## cubing.com 协议
- WS `wss://cubing.com/ws`，subscribe `{"type":"competition","competitionId":<cid>}` 然后 fetch `{"type":"result","action":"fetch","params":{"event":<eid>,"round":<rid>,"filter":"all"}}`
- result row 关键字段：`i`(id) `n`(competitor#) `e/r`(event/round) `b/a/v`(单次/平均/attempts cs) `sr/ar`(single/average record tag, e.g. "AsR" / "WR" / "NR" / "")
- 不主动 push，~40s 空闲服务端断连，走轮询
- 测试历史比赛：`python cubing_record_monitor.py --once --comp <slug> --dry-run`
- 比赛 `live=0` 表示用 WCA Live 等其他系统，`/live/<slug>` 没 data-c，已在 `is_china_in_window` 过滤掉
- **PR(橙色字)**：`{"type":"result","action":"user","user":{"number":N,"wcaid":...}}` → `result.user`，r 项含 `nb/na` 标记。服务端比对的是 WCA 职业生涯 PR（源码 `CubingChina/cubingchina protected/websocket/handler/ResultHandler.php::actionUser`）
- `watched_persons_dir` 配置（服务器 `/opt/wca-monitor/watched_persons`）：每个子目录名 = 一位关注选手，首字母 A-Z 前缀剥掉，余下与 cubing.com `user.name` 括号内中文名 / 整名匹配。空则不开 PR 监控
- 源码 clone：`D:/cube/cubingchina/`（cubing.com 网站完整源码，确认协议细节首选）

## 测试
- 改 `record_format` / 合并逻辑 / `wca_local_names` / monitor 聚合后,**必跑 `python test_push_samples.py`** 推送一批样本到 Bark 肉眼核对格式
- `python test_push.py record N --dry-run` 预览不推送
- 删 `known_ids.json` / `known_comp_ids.json` 重新进静默期，**不补推历史**

## 服务器 SSH host key
- 已重装过，`~/.ssh/known_hosts` 旧 IP 条目失效；遇到 fingerprint mismatch 时先在云控制台 Web 终端跑 `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` 比对再决定
