# wallet_monitor.py
import asyncio
import logging
import ccxt.async_support as ccxt
import time
from collections import deque
from config import CONFIG

logger = logging.getLogger(__name__)

class WalletMonitor:
    def __init__(self, notifier_ref, data_store_ref):
        self.notifier = notifier_ref
        self.data_store = data_store_ref 
        self.proxy_url = CONFIG['proxy'] or None  # NOTE: 空字符串转换为 None
        
        self.last_status = {}
        self.is_initialized = False
        
        # [新增] 历史记录 (供 Web UI 使用)
        self.history = deque(maxlen=50)
        
        self.exchanges = {}
        self._init_exchanges()

    def _init_exchanges(self):
        common_config = {
            'timeout': 30000, 
            'enableRateLimit': True,
        }
        # NOTE: 只有当 proxy 不为空时才配置代理
        if self.proxy_url:
            common_config['proxies'] = {'http': self.proxy_url, 'https': self.proxy_url}
        
        target_list = ['binance', 'okx', 'bybit', 'bitget', 'gate']
        
        for name in target_list:
            try:
                exchange_class = getattr(ccxt, name)
                self.exchanges[name] = exchange_class(common_config)
            except Exception as e:
                logger.error(f"充提监控: 初始化 {name} 失败: {e}")

    async def start(self):
        logger.info("启动交易所充提状态监控 (Wallet Status)...")
        await asyncio.sleep(15) 
        
        while True:
            try:
                await self._check_wallet_status()
                if not self.is_initialized:
                    self.is_initialized = True
                    logger.info("充提状态基准已建立，开始监听变更...")
            except Exception as e:
                logger.error(f"充提监控循环异常: {e}")
            
            await asyncio.sleep(300)

    async def _close_exchanges(self):
        for ex in self.exchanges.values():
            await ex.close()

    async def _check_wallet_status(self):
        # 1. 确定监控目标名单
        target_coins = {'USDT', 'USDC', 'BTC', 'ETH'}
        for symbol in self.data_store.keys():
            base = symbol.replace("USDT", "")
            target_coins.add(base)

        # 2. 并发查询各交易所
        tasks = []
        ex_names = []
        for name, ex in self.exchanges.items():
            ex_names.append(name)
            tasks.append(self._fetch_currencies_safe(name, ex))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 3. 处理数据与对比
        for i, res in enumerate(results):
            ex_name = ex_names[i].upper()
            if isinstance(res, Exception) or not res:
                continue
            
            currencies = res 
            
            for coin in target_coins:
                if coin not in currencies:
                    continue
                
                info = currencies[coin]
                is_active = info.get('active', True)
                can_dep = info.get('deposit', True) 
                can_wd = info.get('withdraw', True)
                
                if is_active is False:
                    can_dep = False
                    can_wd = False
                
                if can_dep is None: can_dep = True
                if can_wd is None: can_wd = True

                cache_key = f"{ex_name}_{coin}"
                current_state = {'dep': can_dep, 'wd': can_wd}
                
                if cache_key in self.last_status:
                    prev = self.last_status[cache_key]
                    
                    if prev['dep'] != can_dep or prev['wd'] != can_wd:
                        if self.is_initialized:
                            await self._send_alert(ex_name, coin, prev, current_state)
                
                self.last_status[cache_key] = current_state

    async def _fetch_currencies_safe(self, name, exchange):
        try:
            if exchange.has['fetchCurrencies']:
                return await exchange.fetch_currencies()
            return {}
        except Exception:
            return {}

    async def _send_alert(self, exchange, coin, prev, curr):
        def status_str(is_open):
            return "✅ 开启" if is_open else "⛔ 关闭"
        
        change_desc = []
        if prev['dep'] != curr['dep']:
            icon = "🟢" if curr['dep'] else "🔴"
            change_desc.append(f"{icon} 充值: {status_str(curr['dep'])}")
        
        if prev['wd'] != curr['wd']:
            icon = "🟢" if curr['wd'] else "🔴"
            change_desc.append(f"{icon} 提现: {status_str(curr['wd'])}")
            
        # [新增] 记录到历史列表
        self.history.appendleft({
            "time": time.time(),
            "exchange": exchange,
            "coin": coin,
            "change": change_desc
        })
        
        changes = "\n".join(change_desc)
        msg = (
            f"🚧 <b>充提状态变更警告</b>\n"
            f"交易所: <b>{exchange}</b>\n"
            f"币种: <b>{coin}</b>\n"
            f"------------------\n"
            f"{changes}\n"
            f"------------------\n"
            f"⚠️ 请留意官方公告，防范关门打狗或流动性风险。"
        )
        logger.info(f"充提状态变更: {exchange} {coin} {curr}")
        await self.notifier.send_message(msg)
