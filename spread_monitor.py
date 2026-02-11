# spread_monitor.py
import asyncio
import logging
import ccxt.async_support as ccxt
import time
from config import CONFIG

logger = logging.getLogger(__name__)

class SpreadMonitor:
    def __init__(self, notifier_ref, data_store_ref):
        self.notifier = notifier_ref
        self.data_store = data_store_ref 
        self.proxy_url = CONFIG['proxy'] or None
        self.threshold = CONFIG['thresholds']['spread_pct']
        self.check_interval = CONFIG['thresholds']['spread_check_interval']
        self.cooldowns = {} 
        self.exchanges = {}
        self.latest_alerts = [] # Web端数据源
        
        self._init_exchanges()

    def _init_exchanges(self):
        common_config = {
            'timeout': 10000,
            'enableRateLimit': True,
            'proxies': {
                'http': self.proxy_url,
                'https': self.proxy_url,
            }
        }
        
        self.target_exchanges = [
            ('binance', 'spot'),
            ('bybit', 'spot'), ('bybit', 'swap'), 
            ('bitget', 'spot'), ('bitget', 'swap'),
            ('gate', 'spot'), ('gate', 'swap'),
            ('upbit', 'spot'),
            # [新增] OKX
            ('okx', 'spot'), ('okx', 'swap')
        ]

        for ex_name, market_type in self.target_exchanges:
            try:
                exchange_class = getattr(ccxt, ex_name)
                ex_inst = exchange_class(common_config)
                
                if market_type == 'swap':
                    ex_inst.options['defaultType'] = 'swap' 
                
                self.exchanges[f"{ex_name}_{market_type}"] = ex_inst
            except Exception as e:
                logger.error(f"初始化交易所 {ex_name} 失败: {e}")

    async def start(self):
        logger.info(f"启动全网价差监控 (含OKX, Interval: {self.check_interval}s)...")
        await asyncio.sleep(5)
        
        while True:
            start_time = time.time()
            try:
                await self._check_spreads()
            except Exception as e:
                logger.error(f"价差监控循环异常: {e}")
            
            elapsed = time.time() - start_time
            sleep_time = max(0, self.check_interval - elapsed)
            await asyncio.sleep(sleep_time)

    async def _close_exchanges(self):
        for ex in self.exchanges.values():
            await ex.close()

    async def _check_spreads(self):
        base_prices = {}
        for symbol, data in self.data_store.items():
            if data.price > 0:
                base_coin = symbol.replace("USDT", "")
                base_prices[base_coin] = data.price

        if not base_prices:
            return

        tasks = []
        for name, ex in self.exchanges.items():
            tasks.append(self._fetch_exchange_tickers(name, ex))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        krw_rate = 1400.0 
        for res in results:
            if isinstance(res, dict) and res.get('source') == 'upbit_spot':
                upbit_data = res['data']
                if 'KRW-USDT' in upbit_data:
                    krw_price = upbit_data['KRW-USDT']['last']
                    if krw_price and krw_price > 0:
                        krw_rate = krw_price
                break

        alerts = []
        
        for res in results:
            if isinstance(res, Exception) or not isinstance(res, dict):
                continue
                
            ex_name = res['source']
            tickers = res['data']
            
            for base_coin, bin_price in base_prices.items():
                target_price = 0.0
                
                if 'upbit' in ex_name:
                    u_symbol = f"KRW-{base_coin}"
                    if u_symbol in tickers and tickers[u_symbol]['last']:
                        target_price = tickers[u_symbol]['last'] / krw_rate
                else:
                    # 匹配符号: BTC/USDT, BTC/USDT:USDT (OKX/Bybit Perp), BTCUSDT
                    candidates = [
                        f"{base_coin}/USDT", 
                        f"{base_coin}/USDT:USDT", 
                        f"{base_coin}USDT"
                    ]
                    for sym in candidates:
                        if sym in tickers and tickers[sym]['last']:
                            target_price = tickers[sym]['last']
                            break

                if target_price > 0:
                    diff_pct = (target_price - bin_price) / bin_price
                    
                    if abs(diff_pct) >= self.threshold:
                        cd_key = f"{base_coin}_{ex_name}"
                        if self._check_cooldown(cd_key):
                            alerts.append({
                                'coin': base_coin,
                                'ex': ex_name,
                                'bin_p': bin_price,
                                'other_p': target_price,
                                'pct': diff_pct
                            })

        self.latest_alerts = alerts

        # 筛选需要发送 TG 通知的（带冷却）
        tg_alerts = []
        for a in alerts:
            # 这里需要再次检查冷却，因为 latest_alerts 是全量的供前端展示
            # 但上面的 _check_cooldown 已经更新了时间戳，所以这里逻辑稍微有点冗余但安全
            # 实际上我们在上面已经 check 过了，这里直接发即可
            tg_alerts.append(a)

        if tg_alerts:
            await self._send_batch_alert(tg_alerts, krw_rate)

    async def _fetch_exchange_tickers(self, name, exchange):
        try:
            # 15秒超时
            return {
                'source': name, 
                'data': await asyncio.wait_for(exchange.fetch_tickers(), timeout=15)
            }
        except Exception:
            return {'source': name, 'data': {}}

    def _check_cooldown(self, key):
        now = time.time()
        last = self.cooldowns.get(key, 0)
        cd_seconds = CONFIG['thresholds'].get('spread_cooldown', 1800)
        
        if now - last > cd_seconds:
            self.cooldowns[key] = now
            return True
        return False

    async def _send_batch_alert(self, alerts, krw_rate):
        alerts.sort(key=lambda x: abs(x['pct']), reverse=True)
        top_alerts = alerts[:10]
        
        lines = [f"🌊 <b>全网价差监控 (> {self.threshold*100:.0f}%)</b>", "------------------"]
        
        for a in top_alerts:
            icon = "🟢" if a['pct'] > 0 else "🔴"
            ex_display = a['ex'].upper().replace('_', ' ')
            
            lines.append(f"<b>{a['coin']}</b> vs {ex_display}")
            lines.append(f"{icon} 差价: <b>{a['pct']*100:+.2f}%</b>")
            lines.append(f"   BN: {a['bin_p']} | 他: {a['other_p']:.4f}")
            lines.append("")
            
        lines.append(f"------------------")
        lines.append(f"注: Upbit汇率按 {krw_rate:.1f} 换算")
        
        msg = "\n".join(lines)
        await self.notifier.send_message(msg)
