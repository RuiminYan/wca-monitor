#!/bin/bash
# WCA 监控套件 — 阿里云服务器一键部署脚本
#
# 用法：
#   bash deploy.sh              # 安装并启动服务
#   bash deploy.sh --uninstall  # 卸载服务
#   bash deploy.sh --status     # 查看服务状态和最近日志
#
# NOTE: 需要 root 权限运行（systemd 服务注册需要）

set -e

# === 配置 ===

INSTALL_DIR="/opt/wca-monitor"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# systemd 服务名
SVC_RECORD="wca-record-monitor"
SVC_COMP="wca-comp-monitor"
SVC_WCA_COMP="wca-wca-comp-monitor"

# Python 最低版本要求
PYTHON_MIN="3.6"

# 需要部署的文件（核心代码 + 配置）
DEPLOY_FILES=(
    "monitor_utils.py"
    "wca_record_monitor.py"
    "wca_rankings.py"
    "cubing_com_monitor.py"
    "wca_comp_monitor.py"
    "email_notifier.py"
    "test_push.py"
    "download_competitions.py"
    "config.json"
)

# 可选文件（存在则一起部署）
OPTIONAL_FILES=(
    "credentials.json"
    "token.json"
    "config.example.json"
)

# === 辅助函数 ===

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# === 卸载模式 ===

if [ "$1" = "--uninstall" ]; then
    echo -e "${CYAN}=== WCA 监控套件卸载 ===${NC}"
    systemctl stop "$SVC_RECORD" 2>/dev/null || true
    systemctl stop "$SVC_COMP" 2>/dev/null || true
    systemctl stop "$SVC_WCA_COMP" 2>/dev/null || true
    systemctl stop "${SVC_RECORD}-start.timer" 2>/dev/null || true
    systemctl stop "${SVC_RECORD}-stop.timer" 2>/dev/null || true
    systemctl disable "$SVC_RECORD" 2>/dev/null || true
    systemctl disable "$SVC_COMP" 2>/dev/null || true
    systemctl disable "$SVC_WCA_COMP" 2>/dev/null || true
    systemctl disable "${SVC_RECORD}-start.timer" 2>/dev/null || true
    systemctl disable "${SVC_RECORD}-stop.timer" 2>/dev/null || true
    rm -f "/etc/systemd/system/${SVC_RECORD}.service"
    rm -f "/etc/systemd/system/${SVC_COMP}.service"
    rm -f "/etc/systemd/system/${SVC_WCA_COMP}.service"
    rm -f "/etc/systemd/system/${SVC_RECORD}-start.timer"
    rm -f "/etc/systemd/system/${SVC_RECORD}-start.service"
    rm -f "/etc/systemd/system/${SVC_RECORD}-stop.timer"
    rm -f "/etc/systemd/system/${SVC_RECORD}-stop.service"
    systemctl daemon-reload
    info "服务已卸载"
    info "数据目录 ${INSTALL_DIR} 已保留（如需删除请手动 rm -rf）"
    exit 0
fi

# === 状态查看模式 ===

if [ "$1" = "--status" ]; then
    echo -e "${CYAN}=== WCA 监控套件状态 ===${NC}"
    echo ""
    echo -e "${CYAN}--- 纪录监控 ---${NC}"
    systemctl status "$SVC_RECORD" --no-pager 2>/dev/null || warn "服务未安装"
    echo ""
    echo -e "${CYAN}--- 粗饼比赛监控 ---${NC}"
    systemctl status "$SVC_COMP" --no-pager 2>/dev/null || warn "服务未安装"
    echo ""
    echo -e "${CYAN}--- WCA 比赛监控 ---${NC}"
    systemctl status "$SVC_WCA_COMP" --no-pager 2>/dev/null || warn "服务未安装"
    echo ""
    echo -e "${CYAN}--- 纪录监控最近日志 (20行) ---${NC}"
    journalctl -u "$SVC_RECORD" -n 20 --no-pager 2>/dev/null || true
    echo ""
    echo -e "${CYAN}--- 粗饼比赛监控最近日志 (20行) ---${NC}"
    journalctl -u "$SVC_COMP" -n 20 --no-pager 2>/dev/null || true
    echo ""
    echo -e "${CYAN}--- WCA 比赛监控最近日志 (20行) ---${NC}"
    journalctl -u "$SVC_WCA_COMP" -n 20 --no-pager 2>/dev/null || true
    exit 0
fi

# === 安装模式 ===

echo -e "${CYAN}========================================${NC}"
echo -e "${CYAN}  WCA 监控套件 — 服务器部署${NC}"
echo -e "${CYAN}========================================${NC}"
echo ""

# 检查 root 权限
if [ "$EUID" -ne 0 ]; then
    error "需要 root 权限运行，请使用: sudo bash deploy.sh"
fi

# === 第一步：检查 Python 版本 ===

info "检查 Python 环境..."
if ! command -v python3 &>/dev/null; then
    error "python3 未安装！请先安装 Python ${PYTHON_MIN}+：
    Ubuntu/Debian: apt install python3 python3-pip
    CentOS/RHEL:   yum install python3 python3-pip"
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
MIN_MAJOR=$(echo "$PYTHON_MIN" | cut -d. -f1)
MIN_MINOR=$(echo "$PYTHON_MIN" | cut -d. -f2)

if [ "$PY_MAJOR" -lt "$MIN_MAJOR" ] || { [ "$PY_MAJOR" -eq "$MIN_MAJOR" ] && [ "$PY_MINOR" -lt "$MIN_MINOR" ]; }; then
    error "Python 版本过低: ${PY_VERSION}（需要 ${PYTHON_MIN}+）
    脚本使用了 tuple[str, str] 等 3.10+ 语法。
    请升级 Python 或联系作者修改类型注解。"
fi

PYTHON_PATH=$(command -v python3)
info "Python ${PY_VERSION} ✓ (${PYTHON_PATH})"

# === 第二步：安装 pip 依赖 ===

info "安装 Python 依赖..."
pip3 install --quiet requests 2>/dev/null || python3 -m pip install --quiet requests

# 检查是否需要 Gmail 依赖
if [ -f "${SCRIPT_DIR}/credentials.json" ]; then
    info "检测到 credentials.json，安装 Gmail API 依赖..."
    pip3 install --quiet google-api-python-client google-auth-oauthlib 2>/dev/null \
        || python3 -m pip install --quiet google-api-python-client google-auth-oauthlib
fi

info "Python 依赖安装完成 ✓"

# === 第三步：部署文件 ===

# NOTE: 如果脚本已经在安装目录中运行，跳过复制（避免 cp 自引用）
if [ "$SCRIPT_DIR" = "$INSTALL_DIR" ]; then
    info "脚本已在安装目录运行，跳过文件复制 ✓"
else
    info "部署文件到 ${INSTALL_DIR}..."
    mkdir -p "$INSTALL_DIR"

    # 复制核心文件
    for f in "${DEPLOY_FILES[@]}"; do
        if [ -f "${SCRIPT_DIR}/${f}" ]; then
            cp "${SCRIPT_DIR}/${f}" "${INSTALL_DIR}/${f}"
        else
            if [ "$f" = "config.json" ]; then
                error "config.json 不存在！请先从 config.example.json 复制并填写配置。"
            fi
            warn "跳过不存在的文件: ${f}"
        fi
    done

    # 复制可选文件
    for f in "${OPTIONAL_FILES[@]}"; do
        if [ -f "${SCRIPT_DIR}/${f}" ]; then
            cp "${SCRIPT_DIR}/${f}" "${INSTALL_DIR}/${f}"
            info "  已复制: ${f}"
        fi
    done

    info "文件部署完成 ✓"
fi

# === 第四步：创建 systemd 服务 ===

info "配置 systemd 服务..."

# 纪录监控服务（不再开机自启，由 timer 控制）
cat > "/etc/systemd/system/${SVC_RECORD}.service" <<EOF
[Unit]
Description=WCA Record Monitor — 纪录快讯推送
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
ExecStart=${PYTHON_PATH} ${INSTALL_DIR}/wca_record_monitor.py
Restart=always
RestartSec=10
# 环境变量：确保 Python 输出不缓冲（实时日志）
Environment=PYTHONUNBUFFERED=1
# NOTE: 不设 [Install] 段，由 timer 控制启停
EOF

# 纪录监控定时器：周五 00:00 启动
cat > "/etc/systemd/system/${SVC_RECORD}-start.timer" <<EOF
[Unit]
Description=Start WCA Record Monitor on Friday

[Timer]
OnCalendar=Fri *-*-* 00:00:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

cat > "/etc/systemd/system/${SVC_RECORD}-start.service" <<EOF
[Unit]
Description=Start WCA Record Monitor

[Service]
Type=oneshot
ExecStart=/bin/systemctl start ${SVC_RECORD}.service
EOF

# 纪录监控定时器：周二 00:00 停止
cat > "/etc/systemd/system/${SVC_RECORD}-stop.timer" <<EOF
[Unit]
Description=Stop WCA Record Monitor on Tuesday

[Timer]
OnCalendar=Tue *-*-* 00:00:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

cat > "/etc/systemd/system/${SVC_RECORD}-stop.service" <<EOF
[Unit]
Description=Stop WCA Record Monitor

[Service]
Type=oneshot
ExecStart=/bin/systemctl stop ${SVC_RECORD}.service
EOF

# 比赛监控服务（7×24 全天候运行）
cat > "/etc/systemd/system/${SVC_COMP}.service" <<EOF
[Unit]
Description=Cubing Competition Monitor — 新比赛公示推送
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
ExecStart=${PYTHON_PATH} ${INSTALL_DIR}/cubing_com_monitor.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

# WCA 比赛监控服务（7×24 全天候运行）
cat > "/etc/systemd/system/${SVC_WCA_COMP}.service" <<EOF
[Unit]
Description=WCA Competition Monitor — WCA 新比赛推送
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
ExecStart=${PYTHON_PATH} ${INSTALL_DIR}/wca_comp_monitor.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

# === 第五步：启动服务 ===

systemctl daemon-reload

# 停止旧服务（如果存在）
systemctl stop "$SVC_RECORD" 2>/dev/null || true
systemctl stop "$SVC_COMP" 2>/dev/null || true

# 启用定时器
systemctl enable --now "${SVC_RECORD}-start.timer"
systemctl enable --now "${SVC_RECORD}-stop.timer"

# 比赛监控全天候运行
systemctl enable --now "$SVC_COMP"
systemctl enable --now "$SVC_WCA_COMP"

# 判断今天是否是周五至周一（5,6,0,1），如果是则立即启动纪录监控
DOW=$(date +%u)  # 1=Mon, ..., 5=Fri, 6=Sat, 7=Sun
if [ "$DOW" -ge 5 ] || [ "$DOW" -le 1 ]; then
    systemctl start "$SVC_RECORD"
    info "今天是比赛日，纪录监控已启动 ✓"
else
    info "今天非比赛日 (周五~周一)，纪录监控未启动（定时器已就绪）"
fi

info "比赛监控已启动（全天候） ✓"

# === 第六步：验证 ===

echo ""
echo -e "${CYAN}========================================${NC}"
echo -e "${CYAN}  部署完成！${NC}"
echo -e "${CYAN}========================================${NC}"
echo ""

# 等待 2 秒让服务初始化
sleep 2

# 显示服务状态
RECORD_STATUS=$(systemctl is-active "$SVC_RECORD" 2>/dev/null || echo "failed")
COMP_STATUS=$(systemctl is-active "$SVC_COMP" 2>/dev/null || echo "failed")
WCA_COMP_STATUS=$(systemctl is-active "$SVC_WCA_COMP" 2>/dev/null || echo "failed")

if [ "$RECORD_STATUS" = "active" ]; then
    echo -e "  纪录监控: ${GREEN}● 运行中${NC}"
else
    echo -e "  纪录监控: ${RED}● 未运行${NC}"
fi

if [ "$COMP_STATUS" = "active" ]; then
    echo -e "  粗饼比赛监控: ${GREEN}● 运行中${NC}"
else
    echo -e "  粗饼比赛监控: ${RED}● 未运行${NC}"
fi

if [ "$WCA_COMP_STATUS" = "active" ]; then
    echo -e "  WCA比赛监控: ${GREEN}● 运行中${NC}"
else
    echo -e "  WCA比赛监控: ${RED}● 未运行${NC}"
fi

echo ""
echo -e "  安装目录: ${INSTALL_DIR}"
echo -e "  Python:   ${PYTHON_PATH} (${PY_VERSION})"
echo ""
echo -e "${CYAN}常用命令:${NC}"
echo "  bash deploy.sh --status           # 查看状态和日志"
echo "  bash deploy.sh --uninstall        # 卸载服务"
echo "  journalctl -u ${SVC_RECORD} -f    # 实时查看纪录监控日志"
echo "  journalctl -u ${SVC_COMP} -f      # 实时查看粗饼比赛监控日志"
echo "  journalctl -u ${SVC_WCA_COMP} -f  # 实时查看WCA比赛监控日志"
echo "  systemctl restart ${SVC_RECORD}   # 重启纪录监控"
echo ""
echo -e "${CYAN}测试推送:${NC}"
echo "  cd ${INSTALL_DIR}"
echo "  python3 test_push.py record 1 --dry-run   # 预览纪录（不推送）"
echo "  python3 test_push.py wca-comp 1 --dry-run # 预览WCA比赛（不推送）"
echo "  python3 test_push.py record 1              # 推送 1 条到手机"
echo ""
