# WCA 监控套件 - Windows 任务计划程序 安装/卸载 脚本
# 同时注册纪录监控和比赛监控两个计划任务

param([switch]$Uninstall)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition

# 两个监控任务的定义
$Tasks = @(
    @{
        Name        = "WCA Record Monitor"
        Script      = Join-Path $ScriptDir "wca_record_monitor.py"
        Description = "Monitor WCA Live for new WR/CR/NR records and push via Bark (Fri-Mon)"
        # NOTE: 纪录只在比赛日产生，周五到周一覆盖全球时区的周末比赛
        Trigger     = { New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday, Saturday, Sunday, Monday -At "00:00" }
    },
    @{
        Name        = "Cubing Competition Monitor"
        Script      = Join-Path $ScriptDir "cubing_com_monitor.py"
        Description = "Monitor cubing.com for new competitions and push via Bark (persistent)"
        # NOTE: 新比赛随时可能发布，每天 0 点启动后持续运行，每 15 分钟轮询
        # HACK: 理想方案是 AtStartup，但该触发器需要管理员权限，改用 Daily 代替
        Trigger     = { New-ScheduledTaskTrigger -Daily -At "00:00" }
    },
    @{
        Name        = "WCA Competition Monitor"
        Script      = Join-Path $ScriptDir "wca_comp_monitor.py"
        Description = "Monitor WCA for new competitions and push via Bark (persistent)"
        Trigger     = { New-ScheduledTaskTrigger -Daily -At "00:00" }
    }
)

# === 卸载模式 ===
if ($Uninstall)
{
    foreach ($task in $Tasks)
    {
        $existing = Get-ScheduledTask -TaskName $task.Name -ErrorAction SilentlyContinue
        if ($existing)
        {
            Unregister-ScheduledTask -TaskName $task.Name -Confirm:$false
            Write-Host "Removed: '$($task.Name)'" -ForegroundColor Green
        }
        else
        {
            Write-Host "Not found: '$($task.Name)'" -ForegroundColor Yellow
        }
    }
    exit 0
}

# === 安装模式 ===

# 检查配置文件
$ConfigPath = Join-Path $ScriptDir "config.json"
if (-not (Test-Path $ConfigPath))
{
    Write-Host "[ERROR] config.json not found. Copy config.example.json to config.json and fill in your Bark device key." -ForegroundColor Red
    exit 1
}

# 检查 pythonw.exe
$Pythonw = Get-Command pythonw.exe -ErrorAction SilentlyContinue
if (-not $Pythonw)
{
    Write-Host "[ERROR] pythonw.exe not found. Make sure Python is installed and in PATH." -ForegroundColor Red
    exit 1
}

# 注册任务
foreach ($task in $Tasks)
{
    # 覆盖已有任务
    $existing = Get-ScheduledTask -TaskName $task.Name -ErrorAction SilentlyContinue
    if ($existing)
    {
        Write-Host "Overwriting existing task: '$($task.Name)'..."
        Unregister-ScheduledTask -TaskName $task.Name -Confirm:$false
    }

    $action = New-ScheduledTaskAction `
        -Execute $Pythonw.Source `
        -Argument "`"$($task.Script)`"" `
        -WorkingDirectory $ScriptDir

    $trigger = & $task.Trigger

    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit (New-TimeSpan -Hours 23 -Minutes 50)

    Register-ScheduledTask `
        -TaskName $task.Name `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Description $task.Description | Out-Null

    Write-Host "Installed: '$($task.Name)'" -ForegroundColor Green
    Write-Host "  Script : $($task.Script)"
}

Write-Host ""
Write-Host "Python : $($Pythonw.Source)"
Write-Host ""
Write-Host "To uninstall all:" -ForegroundColor Yellow
Write-Host "  powershell -ExecutionPolicy Bypass -File install_task.ps1 -Uninstall"
