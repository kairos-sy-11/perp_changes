# web_server.py
import asyncio
import logging
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import FileResponse
from monitor_pool import MonitorSystem

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("WebServer")

monitor_system = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global monitor_system
    logger.info("正在启动监控系统核心...")
    monitor_system = MonitorSystem()
    task = asyncio.create_task(monitor_system.start())
    yield
    logger.info("正在关闭监控系统...")
    task.cancel()

app = FastAPI(lifespan=lifespan)

# --- 核心数据接口 ---

@app.get("/api/market/abnormal")
async def get_market_abnormal():
    """1. 市场内部异动 (Funding/OI/Price)"""
    if not monitor_system: return []
    return monitor_system.strategy.get_abnormal_list(monitor_system.data_store)

@app.get("/api/market/oi_compare")
async def get_oi_compare():
    """2. 跨交易所 OI 对比"""
    if not monitor_system: return []
    if hasattr(monitor_system, 'oi_comparer'):
        return monitor_system.oi_comparer.get_summary_data()
    return []

@app.get("/api/spread/all")
async def get_spreads():
    """3. 跨交易所价差"""
    if not monitor_system: return []
    if hasattr(monitor_system.spread_monitor, 'latest_alerts'):
        return monitor_system.spread_monitor.latest_alerts
    return []

# --- 独立板块数据接口 ---

@app.get("/api/onchain/targets")
async def get_onchain_targets():
    """4. 链上地址监控 (实时余额)"""
    if not monitor_system: return []
    if hasattr(monitor_system, 'onchain_monitor'):
        targets = monitor_system.onchain_monitor.targets
        balances = monitor_system.onchain_monitor.last_balances
        result = []
        for t in targets:
            key = f"{t['chain']}_{t['wallet']}_{t['token_address']}"
            bal = balances.get(key, 0)
            result.append({**t, "balance": bal})
        return result
    return []

@app.get("/api/listings/history")
async def get_listings():
    """5. 上币/公告监控 (历史记录)"""
    if not monitor_system: return []
    # 确保 listing_monitor 里有 history 属性 (之前步骤已添加)
    if hasattr(monitor_system, 'listing_monitor') and hasattr(monitor_system.listing_monitor, 'history'):
        return list(monitor_system.listing_monitor.history)
    return []

@app.get("/api/wallet/history")
async def get_wallet_status():
    """6. 充提状态监控 (历史记录)"""
    if not monitor_system: return []
    # 确保 wallet_monitor 里有 history 属性 (之前步骤已添加)
    if hasattr(monitor_system, 'wallet_monitor') and hasattr(monitor_system.wallet_monitor, 'history'):
        return list(monitor_system.wallet_monitor.history)
    return []

@app.get("/api/history/all")
async def get_all_history():
    """7. 全局 Telegram 发送记录"""
    if not monitor_system: return []
    return list(monitor_system.notifier.history)

# --- 页面路由 ---
@app.get("/")
async def read_root():
    return FileResponse("templates/index.html")

if __name__ == "__main__":
    uvicorn.run("web_server:app", host="0.0.0.0", port=8000, reload=False)
