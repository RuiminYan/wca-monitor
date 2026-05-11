# 把本地 video-by-face\person 的目录名同步到服务器 /opt/wca-monitor/watched_persons
# 增量 mkdir,不动现有目录,不删除多余目录(目录是 monitor 的关注名单)
#
# 用法:
#   .\sync_watched_persons.ps1              # 默认源 D:\cube\video-by-face\person
#   .\sync_watched_persons.ps1 -PersonDir C:\xxx
#   .\sync_watched_persons.ps1 -Prune       # 同时删除服务器上本地没有的目录

[CmdletBinding()]
param(
    [string] $Server = 'cuberoot',
    [string] $RemoteDir = '/opt/wca-monitor/watched_persons',
    [string] $PersonDir = 'D:\cube\video-by-face\person',
    [switch] $Prune
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $PersonDir)) {
    throw "PersonDir not found: $PersonDir"
}

# 列本地目录名,写到临时 UTF-8 文件
$names = Get-ChildItem -LiteralPath $PersonDir -Directory | ForEach-Object Name
$tmp = New-TemporaryFile
# 用 LF 行末(否则 bash 读到末尾的 \r 会让 [ ! -d -- "$name" ] 解析失败)
[IO.File]::WriteAllText(
    $tmp.FullName,
    (($names -join "`n") + "`n"),
    [System.Text.UTF8Encoding]::new($false))
Write-Host "[sync-watched] 本地选手目录数: $($names.Count)" -ForegroundColor Cyan

# scp 到服务器 /tmp,然后 ssh 跑 mkdir 循环
& scp $tmp.FullName "${Server}:/tmp/watched_names.txt"
if ($LASTEXITCODE -ne 0) { throw "scp failed (exit $LASTEXITCODE)" }
Remove-Item $tmp.FullName -Force

$remoteScript = @"
set -e
mkdir -p '$RemoteDir'
cd '$RemoteDir'
created=0
while IFS= read -r name; do
  [ -z "`$name" ] && continue
  if [ ! -d "`$name" ]; then
    mkdir "`$name"
    created=`$((created + 1))
    echo "  + `$name"
  fi
done < /tmp/watched_names.txt
echo "[server] 新建目录数: `$created  当前总目录数: `$(ls | wc -l)"
"@

if ($Prune) {
    $remoteScript += @"

# 删除本地不存在的目录
python3 -c "
import os
local = set(open('/tmp/watched_names.txt', encoding='utf-8').read().splitlines())
local = {x for x in local if x}
remote = set(os.listdir('$RemoteDir'))
extras = remote - local
for d in extras:
    p = os.path.join('$RemoteDir', d)
    try:
        os.rmdir(p)
        print(f'  - {d}')
    except OSError as e:
        print(f'  ? cannot remove {d}: {e}')
print(f'[server] 删除目录数: {len(extras)}')
"
"@
}

# PowerShell 多行字符串含 CRLF,bash 端 \r 会让 [ ! -d -- "$name" ] 解析挂
$remoteScript = $remoteScript -replace "`r", ""
& ssh $Server $remoteScript
if ($LASTEXITCODE -ne 0) { throw "ssh sync failed (exit $LASTEXITCODE)" }

Write-Host "[sync-watched] done. 服务会在下一轮轮询(<=60s)自动用上新名单。" -ForegroundColor Green
