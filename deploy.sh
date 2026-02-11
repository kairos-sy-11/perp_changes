#!/bin/bash
# VPS 一键部署脚本
# 用法: bash deploy.sh

set -e

echo "🚀 开始部署币圈监控系统..."

# 1. 更新系统 & 安装 Python
echo "📦 安装系统依赖..."
sudo apt update -y
sudo apt install -y python3 python3-venv python3-pip git

# 2. 创建项目目录
APP_DIR="$HOME/perp_monitor"
mkdir -p "$APP_DIR"

# 3. 从 GitHub 拉取代码（如果已存在则更新）
if [ -d "$APP_DIR/.git" ]; then
    echo "📥 更新代码..."
    cd "$APP_DIR"
    git pull origin main
else
    echo "📥 拉取代码..."
    git clone https://github.com/kairos-sy-11/perp_changes.git "$APP_DIR"
    cd "$APP_DIR"
fi

# 4. 创建虚拟环境 & 安装依赖
echo "🐍 配置 Python 环境..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install aiohttp>=3.9.0 web3>=6.0.0 ccxt>=4.0.0 requests>=2.28.0

# 5. VPS 上不需要代理，修改 config.py 中的 proxy 为空
# NOTE: 使用 sed 将代理地址替换为空字符串
echo "⚙️ 配置代理设置（VPS 不需要代理）..."
sed -i 's|"proxy": "http://127.0.0.1:7897"|"proxy": ""|g' config.py

# 6. 配置 systemd 服务
echo "🔧 配置系统服务..."
sudo tee /etc/systemd/system/perp-monitor.service > /dev/null << EOF
[Unit]
Description=币圈监控系统
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python main.py
Restart=always
RestartSec=10

# 日志配置
StandardOutput=journal
StandardError=journal

# 环境变量
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

# 7. 启动服务
echo "🟢 启动监控服务..."
sudo systemctl daemon-reload
sudo systemctl enable perp-monitor
sudo systemctl start perp-monitor

echo ""
echo "============================================"
echo "✅ 部署完成！"
echo "============================================"
echo ""
echo "常用命令:"
echo "  查看状态:  sudo systemctl status perp-monitor"
echo "  查看日志:  sudo journalctl -u perp-monitor -f"
echo "  重启服务:  sudo systemctl restart perp-monitor"
echo "  停止服务:  sudo systemctl stop perp-monitor"
echo ""
