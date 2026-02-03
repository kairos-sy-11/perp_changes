#MVP版本，集成模块在一起，手动建立池子，目前只建立了BTC和ETH
import asyncio
import aiohttp
import json
import time
import logging
from collections import deque
from datetime import datetime, timedelta

# ================= 配置区域 (Config) =================
CONFIG = {
    "telegram": {
        "bot_token": "8013902952:AAGDzLwUQaVyn8pKT5d7twMqk4rHMrJ6yLk",
        "chat_id": "-5047534252"
    },
    "proxy": "http://127.0.0.1:7897",
    
    "symbols": ["BTCUSDT", "ETHUSDT"],
    "thresholds": {
        "cvd_usd": 5_000_000,           # [cite: 4] CVD 阈值 500万U
        "oi_small_cap": 30_000_000,     # [cite: 4] 小盘股界限 3000万U
        "oi_change_abs": 1_000_000,     # [cite: 4] 小盘 OI 变动阈值
        "oi_change_pct": 0.05,          # [cite: 4] 大盘 OI 变动比例 5%
        "funding_rate": 0.001           # [cite: 5] 资金费率阈值 0.1%
    },
    "window_seconds": 300,              # [cite: 3] 5分钟窗口
    "cooldown_seconds": 900,            # [cite: 5] 15分钟冷却
    "warmup_seconds": 60                # 预热时间，避免启动误报
}

# ================= 日志设置 =================
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ================= 数据结构 =================
class MarketData:
    def __init__(self, symbol):
        self.symbol = symbol
        self.price = 0.0
        
        # CVD 桶：每秒一个聚合值 (timestamp, net_volume)
        self.cvd_buckets = deque(maxlen=600) 
        # 当前秒的累积器
        self.current_second_ts = 0
        self.current_second_vol = 0.0

        # OI 历史：(timestamp, oi_value_usd)
        self.oi_history = deque(maxlen=50) 
        
        self.funding_rate = 0.0
        
        # 冷却记录：key=alert_type, value=last_trigger_time
        self.cooldowns = {}

    def add_trade(self, price, quantity, is_buyer_maker):
        """
        处理 aggTrade 流
        Binance 逻辑: is_buyer_maker=True -> 主动卖出; False -> 主动买入
        """
        self.price = price
        volume_usd = price * quantity
        
        #  CVD 计算：主动买入 - 主动卖出
        signed_vol = -volume_usd if is_buyer_maker else volume_usd
        
        now_sec = int(time.time())
        
        # 简单的时间桶聚合（每秒一桶）
        if now_sec > self.current_second_ts:
            if self.current_second_ts > 0:
                self.cvd_buckets.append((self.current_second_ts, self.current_second_vol))
            self.current_second_ts = now_sec
            self.current_second_vol = signed_vol
        else:
            self.current_second_vol += signed_vol

    def get_cvd_5m(self):
        """计算过去 5 分钟的 CVD 总和"""
        now = time.time()
        cutoff = now - CONFIG["window_seconds"]
        total_cvd = 0.0
        # 加上当前这一秒还在累积的数据
        total_cvd += self.current_second_vol
        
        # 加上历史桶的数据
        for ts, vol in self.cvd_buckets:
            if ts >= cutoff:
                total_cvd += vol
        return total_cvd

    def get_oi_delta(self):
        """计算 OI 变化 (当前 - 5分钟前)"""
        if not self.oi_history:
            return 0, 0, 0 # delta, current, past
            
        current_oi = self.oi_history[-1][1]
        cutoff = time.time() - CONFIG["window_seconds"]
        
        # 寻找最接近 5 分钟前的 OI 记录
        past_oi = current_oi 
        for ts, val in self.oi_history:
            if ts >= cutoff:
                past_oi = val # 找到第一个进入窗口期的值
                break
                
        delta = current_oi - past_oi
        return delta, current_oi, past_oi

# ================= 核心逻辑 =================
class CryptoMonitor:
    def __init__(self):
        self.data_store = {s: MarketData(s) for s in CONFIG["symbols"]}
        self.start_time = time.time()

    async def send_telegram(self, message):
        """发送 TG 消息"""
        url = f"https://api.telegram.org/bot{CONFIG['telegram']['bot_token']}/sendMessage"
        payload = {
            "chat_id": CONFIG["telegram"]["chat_id"],
            "text": message,
            "parse_mode": "HTML"
        }
        async with aiohttp.ClientSession() as session:
            try:
                # 【修改】添加 proxy 参数
                async with session.post(url, json=payload, proxy=CONFIG["proxy"]) as resp:
                    if resp.status != 200:
                        logger.error(f"TG 发送失败: {await resp.text()}")
            except Exception as e:
                logger.error(f"TG 网络错误: {e}")

    async def check_alerts(self):
        """
        定期检查策略逻辑
        [cite: 4, 5] 包含多空流入判断与资金费率判断
        """
        while True:
            # 等待预热期，防止数据不全导致误报
            if time.time() - self.start_time < CONFIG["warmup_seconds"]:
                await asyncio.sleep(10)
                continue

            for symbol, data in self.data_store.items():
                # 1. 获取指标
                cvd = data.get_cvd_5m()
                oi_delta, oi_now, oi_past = data.get_oi_delta()
                funding = data.funding_rate
                
                # ------ 策略 A: 资金费率极值 ------
                if abs(funding) > CONFIG["thresholds"]["funding_rate"]:
                    await self.trigger_alert(symbol, "FUNDING", data, cvd, oi_delta, oi_now)

                # ------ 策略 B: 资金流向 (CVD + OI) ------
                # 判断 OI 阈值逻辑 [cite: 4]
                is_large_cap = oi_now >= CONFIG["thresholds"]["oi_small_cap"]
                oi_condition_met = False
                
                if is_large_cap:
                    # 大盘看百分比
                    pct_change = (abs(oi_delta) / oi_now) if oi_now > 0 else 0
                    if pct_change >= CONFIG["thresholds"]["oi_change_pct"]:
                        oi_condition_met = True
                else:
                    # 小盘看绝对值
                    if abs(oi_delta) >= CONFIG["thresholds"]["oi_change_abs"]:
                        oi_condition_met = True

                # 多头流入: CVD > 5M 且 OI 显著增加
                if cvd >= CONFIG["thresholds"]["cvd_usd"] and oi_condition_met and oi_delta > 0:
                    await self.trigger_alert(symbol, "LONG_INFLOW", data, cvd, oi_delta, oi_now)
                
                # 空头流入: CVD < -5M 且 OI 显著增加
                elif cvd <= -CONFIG["thresholds"]["cvd_usd"] and oi_condition_met and oi_delta > 0:
                    await self.trigger_alert(symbol, "SHORT_INFLOW", data, cvd, oi_delta, oi_now)

            await asyncio.sleep(5) # 每 5 秒检查一次

    async def trigger_alert(self, symbol, alert_type, data, cvd, oi_delta, oi_now):
        """触发告警并处理冷却"""
        now = time.time()
        last_time = data.cooldowns.get(alert_type, 0)
        
        # [cite: 5] 冷却时间检查 (15分钟)
        if now - last_time < CONFIG["cooldown_seconds"]:
            return

        # 记录触发时间
        data.cooldowns[alert_type] = now
        
        # 构建消息 [cite: 6]
        direction = "🟢 多头流入" if alert_type == "LONG_INFLOW" else "🔴 空头流入"
        if alert_type == "FUNDING": direction = "⚠️ 费率异常"
        
        msg = (
            f"<b>[{symbol}] {direction}</b>\n"
            f"------------------\n"
            f"💰 价格: {data.price:.2f}\n"
            f"📊 CVD(5m): {cvd/1_000_000:.2f}M U\n"
            f"📈 OI: {oi_now/1_000_000:.1f}M (Δ {oi_delta/1_000_000:.2f}M)\n"
            f"💸 Funding: {data.funding_rate*100:.4f}%\n"
            f"⏱ {datetime.now().strftime('%H:%M:%S')}"
        )
        logger.info(f"Trigger Alert: {symbol} {alert_type}")
        await self.send_telegram(msg)

    async def task_ws_aggtrade(self):
        """WebSocket 任务：实时获取价格和成交量"""
        url = "wss://fstream.binance.com/ws/" + "/".join([f"{s.lower()}@aggTrade" for s in CONFIG["symbols"]])
        
        async with aiohttp.ClientSession() as session:
            # 【修改】在这里添加 proxy=CONFIG["proxy"]
            # 注意：WebSocket 的代理配置是在 ws_connect 中，而不是 session 中
            async with session.ws_connect(url, proxy=CONFIG["proxy"]) as ws:
                logger.info(f"WS Connected: {url}")
                async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            payload = json.loads(msg.data)
                            symbol = payload['s']
                            # 解析数据
                            price = float(payload['p'])
                            qty = float(payload['q'])
                            is_maker = payload['m'] #  用于 CVD 计算
                            
                            if symbol in self.data_store:
                                self.data_store[symbol].add_trade(price, qty, is_maker)

    async def task_rest_poller(self):
        """REST API 任务：轮询 OI 和 Funding"""
        async with aiohttp.ClientSession() as session:
            while True:
                for symbol in CONFIG["symbols"]:
                    try:
                        # 1. 获取 OI (持仓量)
                        # 注意：这里需要加上 proxy=CONFIG["proxy"]
                        url_oi = f"https://fapi.binance.com/fapi/v1/openInterest?symbol={symbol}"
                        async with session.get(url_oi, proxy=CONFIG["proxy"]) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                # 修正点：接口只返回 'openInterest' (数量)，我们需要乘以当前价格算出 U 本位价值
                                oi_qty = float(data['openInterest'])
                                current_price = self.data_store[symbol].price
                                
                                # 如果 WS 还没推送价格，暂时算作 0，等待下一次轮询
                                oi_val = oi_qty * current_price if current_price > 0 else 0.0
                                
                                self.data_store[symbol].oi_history.append((time.time(), oi_val))
                            else:
                                logger.error(f"OI Request Failed: {resp.status}")
                        
                        # 2. 获取 Funding (资金费率)
                        url_fund = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}"
                        async with session.get(url_fund, proxy=CONFIG["proxy"]) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                funding = float(data['lastFundingRate'])
                                self.data_store[symbol].funding_rate = funding
                            
                    except Exception as e:
                        logger.error(f"Rest API Error {symbol}: {e}")
                    
                    await asyncio.sleep(1) # 单个请求间隔
                
                await asyncio.sleep(30) # 每一轮间隔 30 秒
                    
    async def run(self):
        await asyncio.gather(
            self.task_ws_aggtrade(),
            self.task_rest_poller(),
            self.check_alerts()
        )

if __name__ == "__main__":
    # Windows 环境下的额外兼容性设置 (解决某些情况下 aiohttp 报错)
    import sys
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    monitor = CryptoMonitor()
    try:
        # 修改点：使用 asyncio.run()，它会自动创建、运行并关闭循环
        asyncio.run(monitor.run())
    except KeyboardInterrupt:
        logger.info("Bot Stopped")
