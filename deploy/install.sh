#!/usr/bin/env bash
# PyService Manager systemd 服务安装脚本
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SERVICE_NAME="pyservice"
UNIT_FILE="${SCRIPT_DIR}/${SERVICE_NAME}.service"
INSTALL_DIR="/etc/systemd/system"
INSTALLED_UNIT="${INSTALL_DIR}/${SERVICE_NAME}.service"

echo "=== PyService Manager systemd 服务安装 ==="

# 检查是否 root
if [[ $EUID -ne 0 ]]; then
    echo "错误: 请使用 sudo 运行此脚本"
    exit 1
fi

# 检查 uv 是否可用
if ! command -v uv &>/dev/null; then
    echo "错误: 未找到 uv 命令，请先安装 uv"
    exit 1
fi
UV_PATH="$(command -v uv)"

# 生成实际 unit 文件（替换路径）
echo "生成服务单元文件..."
sed -e "s|/usr/bin/env uv|${UV_PATH}|g" \
    -e "s|WorkingDirectory=.*|WorkingDirectory=${PROJECT_DIR}|g" \
    "${UNIT_FILE}" > "${INSTALLED_UNIT}"

echo "已安装: ${INSTALLED_UNIT}"
echo "  - uv 路径: ${UV_PATH}"
echo "  - 工作目录: ${PROJECT_DIR}"

# 重载 systemd
systemctl daemon-reload
echo "已执行 daemon-reload"

# 启用并启动服务
read -rp "是否立即启用并启动服务? [y/N] " confirm
if [[ "${confirm,,}" == "y" ]]; then
    systemctl enable "${SERVICE_NAME}"
    systemctl start "${SERVICE_NAME}"
    echo "服务已启用并启动"
    systemctl status "${SERVICE_NAME}" --no-pager
else
    echo "跳过启动。稍后可手动执行:"
    echo "  sudo systemctl enable ${SERVICE_NAME}"
    echo "  sudo systemctl start ${SERVICE_NAME}"
fi

echo ""
echo "=== 常用命令 ==="
echo "  查看状态: systemctl status ${SERVICE_NAME}"
echo "  查看日志: journalctl -u ${SERVICE_NAME} -f"
echo "  停止服务: systemctl stop ${SERVICE_NAME}"
echo "  重启服务: systemctl restart ${SERVICE_NAME}"
echo "  热重载:   systemctl reload ${SERVICE_NAME}"
