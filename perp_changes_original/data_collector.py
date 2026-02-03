# data_collector.py
import asyncio
import aiohttp
import json
import time
import logging
from collections import deque
from config import CONFIG

logger = logging.getLogger(__name__)

class MarketData:
    """单一币种的数据容器"""
    def __init__(self, symbol):
        self.symbol = symbol
        self.price = 0.0
        
        # CVD 桶 (30分钟容量)
        self.cvd_buckets = deque(maxlen=1800) 
        self.curr_sec_ts = 0
        self.curr_sec_vol = 0.0
        
        # OI 历史
        self.oi_history = deque(maxlen=300) 
        
        # 资金费率历史
        self.funding_history = deque(maxlen=300)
        self.funding_rate = 0.0

        # [新增] 价格历史 (用于计算涨跌幅)
        self.price_history = deque(maxlen=300)

    def add_trade(self, price, qty, is_buyer_maker):
        self.price = price # 实时更新最新价
        vol_usd = price * qty
        signed = -vol_usd if is_buyer_maker else vol_usd
        
        now = int(time.time())
        if now > self.curr_sec_ts:
            if self.curr_sec_ts > 0:
                self.cvd_buckets.append((self.curr_sec_ts, self.curr_sec_vol))
            self.curr_sec_ts = now
            self.curr_sec_vol = signed
        else:
            self.curr_sec_vol += signed

    def get_cvd_sum(self, seconds):
        now = time.time()
        cutoff = now - seconds
        total = self.curr_sec_vol
        for ts, vol in self.cvd_buckets:
            if ts >= cutoff: total += vol
        return total

    def get_oi_delta(self, seconds=300):
        if not self.oi_history: return 0, 0, 0
        curr = self.oi_history[-1][1]
        cutoff = time.time() - seconds
        past = curr 
        for ts, val in self.oi_history:
            if ts >= cutoff:
                past = val
                break
        return curr - past, curr, past

    def get_funding_delta(self, seconds=300):
        if not self.funding_history: return 0.0
        curr = self.funding_history[-1][1]
        cutoff = time.time() - seconds
        past = curr
        for ts, val in self.funding_history:
            if ts >= cutoff:
                past = val
                break
        return curr - past

    def get_price_delta(self, seconds=300):
        """[新增] 获取价格变化 (当前价, 5分钟前价格)"""
        curr = self.price
        if not self.price_history: return curr, curr
        
        cutoff = time.time() - seconds
        past = curr
        # 寻找历史价格
        for ts, val in self.price_history:
            if ts >= cutoff:
                past = val
                break
        return curr, past

class DataCollector:
    def __init__(self, data_store):
        self.data_store = data_store 
        self.proxy = CONFIG['proxy']
        self.ws_connection = None 

    async def dynamic_subscribe(self, new_symbols):
        if not self.ws_connection or self.ws_connection.closed:
            return
        params = [f"{s.lower()}@aggTrade" for s in new_symbols]
        payload = {"method": "SUBSCRIBE", "params": params, "id": int(time.time())}
        try:
            await self.ws_connection.send_json(payload)
            logger.info(f"✅ 动态订阅: {new_symbols}")
        except Exception as e:
            logger.error(f"动态订阅失败: {e}")

    async def run_ws(self):
        while True:
            current_symbols = list(self.data_store.keys())
            if not current_symbols:
                await asyncio.sleep(2)
                continue

            base_streams = [f"{s.lower()}@aggTrade" for s in current_symbols]
            url = "wss://fstream.binance.com/ws/" + "/".join(base_streams[:10])
            
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(url, proxy=self.proxy) as ws:
                        self.ws_connection = ws
                        logger.info(f"WS已连接 (Pool: {len(current_symbols)})")
                        
                        if len(base_streams) > 10:
                            remaining = base_streams[10:]
                            for i in range(0, len(remaining), 50):
                                batch = remaining[i:i+50]
                                await ws.send_json({"method": "SUBSCRIBE", "params": batch, "id": 1})
                                await asyncio.sleep(0.2)

                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                payload = json.loads(msg.data)
                                if 'e' in payload and payload['e'] == 'aggTrade':
                                    s = payload['s']
                                    if s in self.data_store:
                                        self.data_store[s].add_trade(
                                            float(payload['p']), float(payload['q']), payload['m']
                                        )
            except Exception as e:
                logger.error(f"WS重连: {e}")
                self.ws_connection = None
                await asyncio.sleep(5)

    async def run_rest_poller(self):
        async with aiohttp.ClientSession() as session:
            while True:
                symbols = list(self.data_store.keys())
                start_time = time.time()
                batch_size = 20
                for i in range(0, len(symbols), batch_size):
                    batch = symbols[i : i + batch_size]
                    tasks = [self._fetch_single_symbol(session, s) for s in batch]
                    await asyncio.gather(*tasks)
                    await asyncio.sleep(0.2)

                elapsed = time.time() - start_time
                if elapsed < 30:
                    await asyncio.sleep(30 - elapsed)

    async def _fetch_single_symbol(self, session, symbol):
        try:
            # OI
            u_oi = f"https://fapi.binance.com/fapi/v1/openInterest?symbol={symbol}"
            async with session.get(u_oi, proxy=self.proxy) as r:
                if r.status == 200:
                    d = await r.json()
                    price = self.data_store[symbol].price
                    if price > 0:
                        val = float(d['openInterest']) * price
                        self.data_store[symbol].oi_history.append((time.time(), val))
                        # [新增] 顺便记录价格历史快照
                        self.data_store[symbol].price_history.append((time.time(), price))
            
            # Funding
            u_fund = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}"
            async with session.get(u_fund, proxy=self.proxy) as r:
                if r.status == 200:
                    d = await r.json()
                    f_rate = float(d['lastFundingRate'])
                    self.data_store[symbol].funding_rate = f_rate
                    self.data_store[symbol].funding_history.append((time.time(), f_rate))
                    
        except Exception:
            pass
