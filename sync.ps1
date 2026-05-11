# sync.ps1 — 把 wca-monitor 的 .py 推到云服务器并重启服务
# 用法：
#   .\sync.ps1                        # 推所有 .py，重启 wca-record-monitor + wca-cubing-record-monitor
#   .\sync.ps1 -Service wca-comp-monitor
#   .\sync.ps1 -All                   # 重启全部四个服务

[CmdletBinding()]
param(
    [string] $Server = 'root@cuberoot.me',
    [string] $RemoteDir = '/opt/wca-monitor',
    [string] $Service = 'wca-record-monitor,wca-cubing-record-monitor',
    [switch] $All
)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot

# 与 deploy.sh DEPLOY_FILES 对齐
$names = @(
    'monitor_utils.py', 'record_format.py', 'wca_rankings.py',
    'wca_record_monitor.py', 'cubing_com_monitor.py', 'cubing_record_monitor.py',
    'wca_comp_monitor.py', 'email_notifier.py',
    'test_push.py', 'download_competitions.py'
)
$paths = $names | ForEach-Object { Join-Path $here $_ } | Where-Object { Test-Path -LiteralPath $_ }
if ($paths.Count -eq 0) { throw "No deployable .py found in $here" }

Write-Host "[sync] scp $($paths.Count) files -> ${Server}:${RemoteDir}/" -ForegroundColor Cyan
& scp @paths "${Server}:${RemoteDir}/"
if ($LASTEXITCODE -ne 0) { throw "scp failed (exit $LASTEXITCODE)" }

$services = if ($All) {
    @('wca-record-monitor', 'wca-cubing-record-monitor', 'wca-comp-monitor', 'wca-wca-comp-monitor')
} else {
    $Service -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ }
}
$restartCmd = ($services | ForEach-Object { "systemctl restart $_" }) -join ' && '
$tailCmd = "journalctl -u $($services[0]) -n 10 --no-pager"

Write-Host "[sync] $restartCmd" -ForegroundColor Cyan
& ssh $Server "$restartCmd && $tailCmd"
if ($LASTEXITCODE -ne 0) { throw "ssh restart failed (exit $LASTEXITCODE)" }

Write-Host "[sync] done." -ForegroundColor Green
