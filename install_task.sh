#!/bin/bash
# WCA 纪录监控 - macOS launchd 安装/卸载脚本

LABEL="com.wca.record-monitor"
PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT_PATH="${SCRIPT_DIR}/wca_record_monitor.py"
LOG_PATH="${SCRIPT_DIR}/monitor.log"

# 卸载模式
if [ "$1" = "--uninstall" ]; then
    launchctl unload "$PLIST_PATH" 2>/dev/null
    rm -f "$PLIST_PATH"
    echo "✅ Task '${LABEL}' has been removed."
    exit 0
fi

# 检查配置文件
if [ ! -f "${SCRIPT_DIR}/config.json" ]; then
    echo "❌ config.json not found."
    echo "   Copy config.example.json to config.json and fill in your Bark device key."
    exit 1
fi

# 检查 python3
if ! command -v python3 &>/dev/null; then
    echo "❌ python3 not found. Install Python 3.10+ first."
    exit 1
fi

PYTHON_PATH="$(command -v python3)"

# 如果已存在，先卸载
launchctl unload "$PLIST_PATH" 2>/dev/null

# 生成 plist
# NOTE: 周五(5)、周六(6)、周日(0)、周一(1) 各在 00:00 启动
cat > "$PLIST_PATH" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PYTHON_PATH}</string>
    <string>${SCRIPT_PATH}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${SCRIPT_DIR}</string>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>6</integer><key>Hour</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>0</integer><key>Hour</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>0</integer></dict>
  </array>
  <key>StandardOutPath</key>
  <string>${LOG_PATH}</string>
  <key>StandardErrorPath</key>
  <string>${LOG_PATH}</string>
</dict>
</plist>
EOF

launchctl load "$PLIST_PATH"

echo ""
echo "✅ Successfully installed launchd task: '${LABEL}'"
echo "   Schedule : Every Friday-Monday at 00:00"
echo "   Script   : ${SCRIPT_PATH}"
echo "   Python   : ${PYTHON_PATH}"
echo "   Log      : ${LOG_PATH}"
echo ""
echo "To uninstall: bash install_task.sh --uninstall"
