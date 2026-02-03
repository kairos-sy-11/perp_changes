# 币圈监控系统 - GitHub Actions 部署指南

## 快速部署步骤

### 1. 创建 GitHub 仓库

```bash
cd perp_changes
git init
git add .
git commit -m "初始化监控系统"
```

在 GitHub 创建新仓库（建议选择 **Public** 避免 Actions 分钟限制），然后:

```bash
git remote add origin https://github.com/你的用户名/仓库名.git
git branch -M main
git push -u origin main
```

### 2. 配置 Secrets

打开 GitHub 仓库 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

添加以下 Secrets:

| 名称 | 值 |
|------|-----|
| `TG_BOT_TOKEN` | 你的 Telegram Bot Token |
| `TG_CHAT_ID` | 目标群组/用户的 Chat ID |

### 3. 启动监控

方式一：手动触发
- 进入 **Actions** 页面
- 选择 **币圈监控系统** workflow
- 点击 **Run workflow**

方式二：等待定时触发
- 每 6 小时自动运行一次

---

## 本地开发

设置环境变量后即可本地运行:

**Windows (PowerShell):**
```powershell
$env:TG_BOT_TOKEN="你的token"
$env:TG_CHAT_ID="你的chat_id"
$env:HTTP_PROXY="http://127.0.0.1:7897"  # 如需代理
python main.py
```

**Linux/macOS:**
```bash
export TG_BOT_TOKEN="你的token"
export TG_CHAT_ID="你的chat_id"
export HTTP_PROXY="http://127.0.0.1:7897"  # 如需代理
python main.py
```

---

## 注意事项

1. **运行时限**：GitHub Actions 单次最多运行 6 小时，workflow 配置了自动重启机制
2. **费用**：公开仓库完全免费，私有仓库每月 2000 分钟免费额度
3. **日志**：在 Actions 页面可查看运行日志
4. **停止监控**：在 Actions 页面取消正在运行的 workflow
