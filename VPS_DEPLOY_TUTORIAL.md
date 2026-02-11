# �️ 如何把你的 Python 项目部署到 VPS 上 7×24 运行

写完代码只能在自己电脑上跑？关了电脑就停了？

今天教你把 Python 项目丢到云服务器上，开机自启 + 崩溃自动重启，全程 10 分钟。

---

## 📋 你需要准备

1. 一台 VPS（推荐 Ubuntu，1 核 1G 就够）
2. 你的代码已经推到 GitHub
3. SSH 工具（Windows 用 PowerShell 就行）

---

## Step 1️⃣ 登录 VPS

```bash
ssh root@你的VPS_IP
```

首次连接输入 `yes`，然后输入密码。

---

## Step 2️⃣ 安装 Python 环境

```bash
sudo apt update -y
sudo apt install -y python3 python3-venv python3-pip git
```

---

## Step 3️⃣ 拉取代码

```bash
cd ~
git clone https://github.com/你的用户名/你的仓库.git myproject
cd myproject
```

---

## Step 4️⃣ 创建虚拟环境 + 安装依赖

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

先手动跑一下确认没报错：

```bash
python main.py
```

能跑通就 `Ctrl+C` 停掉，进入下一步。

---

## Step 5️⃣ 配置 systemd 守护进程（核心）

这一步让你的程序变成系统服务：**开机自启 + 崩溃自动重启。**

```bash
sudo tee /etc/systemd/system/myproject.service > /dev/null << EOF
[Unit]
Description=My Python Project
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/myproject
ExecStart=/root/myproject/venv/bin/python main.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
```

> 💡 把 `myproject` 和路径替换成你自己的项目名和实际路径。

---

## Step 6️⃣ 启动服务

```bash
sudo systemctl daemon-reload
sudo systemctl enable myproject   # 开机自启
sudo systemctl start myproject    # 立即启动
```

---

## Step 7️⃣ 验证

```bash
# 查看状态（显示 active (running) 就是成功了）
sudo systemctl status myproject

# 实时查看输出日志
sudo journalctl -u myproject -f
```

---

## 🛠️ 日常运维速查

```bash
sudo systemctl restart myproject  # 重启
sudo systemctl stop myproject     # 停止
sudo journalctl -u myproject -f   # 看日志
```

---

## � 如何更新代码

本地改完 push 到 GitHub，VPS 上两行搞定：

```bash
cd ~/myproject
git pull origin main
sudo systemctl restart myproject
```

---

## 📌 小贴士

- **查看全部日志**：`sudo journalctl -u myproject --no-pager`
- **内存不够用**：加个 swap ↓
  ```bash
  sudo fallocate -l 1G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  ```
- **多个项目**：复制 Step 5 的 service 文件，改个名就行
- **非 root 用户**：把 `User=root` 改成你的用户名，路径也对应改

---

> � 总结：整个流程就 3 件事
> 
> **拉代码 → 装环境 → 配服务**
> 
> 配完之后，服务器会帮你 7×24 跑着，崩了自动拉起来。
> 
> 你只管写代码，跑的事交给机器。
