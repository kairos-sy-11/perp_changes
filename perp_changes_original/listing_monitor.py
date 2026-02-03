# listing_monitor.py
import asyncio
import aiohttp
import logging
import json
import time
from collections import deque
from config import CONFIG

logger = logging.getLogger(__name__)

class ListingMonitor:
    def __init__(self, notifier_ref):
        self.notifier = notifier_ref
        self.proxy = CONFIG['proxy']
        
        # 缓存已知的交易对
        self.known_symbols = {
            "BINANCE_SPOT": set(),
            "BINANCE_PERP": set(),
            "UPBIT": set()
        }
        self.is_initialized = False
        
        # [新增] 历史记录 (供 Web UI 使用)
        self.history = deque(maxlen=50)

    async def start(self):
        """启动监控循环"""
        logger.info("启动上币监控模块 (Binance Spot/Perp + Upbit)...")
        
        # 1. 首次运行：只填充数据，不报警
        await self._refresh_all(silent=True)
        self.is_initialized = True
        logger.info(f"上币监控初始化完成。当前收录: Binance现货 {len(self.known_symbols['BINANCE_SPOT'])}, 合约 {len(self.known_symbols['BINANCE_PERP'])}, Upbit {len(self.known_symbols['UPBIT'])}")

        # 2. 循环监控
        while True:
            try:
                await self._refresh_all(silent=False)
            except Exception as e:
                logger.error(f"上币监控循环错误: {e}")
            
            # 每 60 秒轮询一次
            await asyncio.sleep(60)

    async def _refresh_all(self, silent=False):
        await asyncio.gather(
            self._check_binance_spot(silent),
            self._check_binance_perp(silent),
            self._check_upbit(silent)
        )

    async def _check_binance_spot(self, silent):
        url = "https://api.binance.com/api/v3/exchangeInfo"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, proxy=self.proxy, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        current_set = set()
                        new_listings = []

                        for item in data['symbols']:
                            symbol = item['symbol']
                            status = item['status']
                            current_set.add(symbol)

                            # 只有当缓存非空时，才进行新币判定 (防网络波动误报)
                            if (self.is_initialized 
                                and len(self.known_symbols['BINANCE_SPOT']) > 0
                                and symbol not in self.known_symbols['BINANCE_SPOT']):
                                new_listings.append(f"{symbol} ({status})")

                        # 处理逻辑
                        if len(self.known_symbols['BINANCE_SPOT']) == 0 and len(current_set) > 0:
                            # 缓存为空但取到了数据 -> 静默填充
                            self.known_symbols['BINANCE_SPOT'] = current_set
                        elif not silent and new_listings:
                            await self._send_alert("Binance Spot", new_listings)
                            self.known_symbols['BINANCE_SPOT'].update(item.split()[0] for item in new_listings)
                        elif silent:
                            self.known_symbols['BINANCE_SPOT'] = current_set
                    else:
                        logger.error(f"Binance Spot 请求失败: Status {resp.status}")

        except Exception as e:
            logger.error(f"Binance Spot 监控异常: {e}")

    async def _check_binance_perp(self, silent):
        """监控 Binance U本位合约"""
        url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, proxy=self.proxy, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        current_set = set()
                        new_listings = []

                        for item in data['symbols']:
                            symbol = item['symbol']
                            status = item['status']
                            if item['contractType'] == 'PERPETUAL':
                                current_set.add(symbol)
                                
                                if (self.is_initialized 
                                    and len(self.known_symbols['BINANCE_PERP']) > 0
                                    and symbol not in self.known_symbols['BINANCE_PERP']):
                                    new_listings.append(f"{symbol} ({status})")

                        if len(self.known_symbols['BINANCE_PERP']) == 0 and len(current_set) > 0:
                            logger.info(f"Binance Futures 初始化/恢复连接，收录 {len(current_set)} 个合约 (静默同步)")
                            self.known_symbols['BINANCE_PERP'] = current_set
                        elif not silent and new_listings:
                            await self._send_alert("Binance Futures", new_listings)
                            self.known_symbols['BINANCE_PERP'].update(item.split()[0] for item in new_listings)
                        elif silent:
                            self.known_symbols['BINANCE_PERP'] = current_set
                    else:
                        # 403/451 错误通常意味着 IP 地区受限
                        logger.error(f"⚠️ Binance合约接口请求失败: Status {resp.status} (可能是IP地区被禁)")
        except Exception as e:
            logger.error(f"Binance Perp 监控异常: {e}")

    async def _check_upbit(self, silent):
        url = "https://api.upbit.com/v1/market/all?isDetails=true" 
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, proxy=self.proxy, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        current_set = set()
                        new_listings = []

                        for item in data:
                            symbol = item['market']
                            current_set.add(symbol)

                            if (self.is_initialized 
                                and len(self.known_symbols['UPBIT']) > 0
                                and symbol not in self.known_symbols['UPBIT']):
                                new_listings.append(symbol)

                        if len(self.known_symbols['UPBIT']) == 0 and len(current_set) > 0:
                            self.known_symbols['UPBIT'] = current_set
                        elif not silent and new_listings:
                            await self._send_alert("Upbit Spot", new_listings)
                            self.known_symbols['UPBIT'].update(new_listings)
                        elif silent:
                            self.known_symbols['UPBIT'] = current_set
                    else:
                        logger.error(f"Upbit 请求失败: Status {resp.status}")
        except Exception as e:
            logger.error(f"Upbit 监控异常: {e}")

    async def _send_alert(self, exchange_name, symbols):
        # [新增] 记录到历史列表
        self.history.appendleft({
            "time": time.time(),
            "exchange": exchange_name,
            "symbols": symbols
        })
        
        msg = (
            f"🚀 <b>新币上线监控</b>\n"
            f"交易所: {exchange_name}\n"
            f"发现新交易对:\n"
            f"<b>{', '.join(symbols)}</b>\n"
            f"------------------\n"
            f"⚠️ 请注意：API检测到新币，可能尚未开放交易，请查阅官方公告。"
        )
        logger.info(f"上币报警: {exchange_name} - {symbols}")
        await self.notifier.send_message(msg)
