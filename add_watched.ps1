# add_watched.ps1 — 加新关注选手一键脚本
#
# 前置:已在 D:\cube\video-by-face\person\ 下 mkdir 好新选手目录,目录命名约定:
#   - `Z张三` / `Feliks Zemdegs` / `Đỗ Quang Hưng`(首字母 A-Z 紧跟 CJK 时会被剥)
#   - 同名歧义时,在选手目录内放 `wca_id.txt`,内容是 wcaId(如 `2009ZEMD01`),
#     watched_wca_ids 会优先采用
#
# 这个脚本做的事(全部幂等,已存在的不重复):
#   1. python watched_wca_ids.py — WCA REST 搜出 wcaId 并写入 cache
#   2. python wca_pr_cache.py    — 拉新 wcaId 的 PR 基线
#   3. sync_watched_persons.ps1  — 目录同步到服务器(cubing.com 监控用)
#   4. scp 两个 cache JSON       — 服务器更新名单
#   5. systemctl restart         — wca-record-monitor + wca-cubing-record-monitor
#
# 用法:
#   .\add_watched.ps1                # 全跑
#   .\add_watched.ps1 -NoRestart     # 跳过最后重启(改名 / 试错用)
#   .\add_watched.ps1 -Server other  # 指定其他服务器别名

[CmdletBinding()]
param(
    [string] $Server = 'cuberoot',
    [string] $RemoteDir = '/opt/wca-monitor',
    [switch] $NoRestart
)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot

function Step($n, $msg) { Write-Host "[$n/5] $msg" -ForegroundColor Cyan }

Step 1 "WCA REST 搜索补 watched_wca_ids 缓存"
& python (Join-Path $here 'watched_wca_ids.py')
if ($LASTEXITCODE -ne 0) { throw "watched_wca_ids.py exit $LASTEXITCODE" }

Step 2 "WCA REST 拉新 wcaId 的 PR 基线"
& python (Join-Path $here 'wca_pr_cache.py')
if ($LASTEXITCODE -ne 0) { throw "wca_pr_cache.py exit $LASTEXITCODE" }

Step 3 "目录同步到服务器(cubing.com 监控用)"
& (Join-Path $here 'sync_watched_persons.ps1') -Server $Server -RemoteDir "$RemoteDir/watched_persons"

Step 4 "scp 缓存 JSON 到服务器"
$caches = @('watched_wca_ids_cache.json', 'wca_pr_cache.json') |
    ForEach-Object { Join-Path $here $_ } |
    Where-Object { Test-Path -LiteralPath $_ }
if ($caches.Count -eq 0) { throw "缓存 JSON 都不存在,前面两步可能失败了" }
& scp @caches "${Server}:${RemoteDir}/"
if ($LASTEXITCODE -ne 0) { throw "scp failed exit $LASTEXITCODE" }

if ($NoRestart) {
    Write-Host "[5/5] (跳过重启,需手动 ssh $Server 'systemctl restart wca-record-monitor wca-cubing-record-monitor')" -ForegroundColor Yellow
    return
}

Step 5 "重启 wca-record-monitor + wca-cubing-record-monitor"
& ssh $Server 'systemctl restart wca-record-monitor wca-cubing-record-monitor && systemctl is-active wca-record-monitor wca-cubing-record-monitor'
if ($LASTEXITCODE -ne 0) { throw "ssh restart failed exit $LASTEXITCODE" }

Write-Host "[add-watched] done." -ForegroundColor Green
