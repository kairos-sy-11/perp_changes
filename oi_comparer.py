# oi_comparer.py
import asyncio
import logging
import ccxt.async_support as ccxt
import time
from config import CONFIG

logger = logging.getLogger(__name__)

class OIComparer:
    def __init__(self, notifier_ref, data_store_ref):
        self.notifier = notifier_ref
        self.data_store = data_store_ref
        self.proxy_url = CONFIG['proxy'] or None  # NOTE: 空字符串转换为 None
        self.ratios = CONFIG['thresholds']['oi_compare']
        self.active_alerts = set()
        self.latest_abnormal_data = [] # Web端数据源

        self.exchanges = {}
        self._init_exchanges()

    def _init_exchanges(self):
        common_config = {
            'timeout': 15000,
            'enableRateLimit': True,
        }
        # NOTE: 只有当 proxy 不为空时才配置代理
        if self.proxy_url:
            common_config['proxies'] = {'http': self.proxy_url, 'https': self.proxy_url}
        
        # [新增] okx
        target_list = ['bybit', 'bitget', 'gate', 'okx']
        
        for name in target_list:
            try:
                exchange_class = getattr(ccxt, name)
                ex_inst = exchange_class(common_config)
                ex_inst.options['defaultType'] = 'swap'
                self.exchanges[name] = ex_inst
            except Exception as e:
                logger.error(f"OI监控: 初始化 {name} 失败: {e}")

    async def start(self):
        logger.info("启动跨交易所 OI 占比监控 (含OKX)...")
        await asyncio.sleep(10)
        
        while True:
            try:
                await self._check_oi_ratios()
            except Exception as e:
                logger.error(f"OI对比循环异常: {e}")
            await asyncio.sleep(60)

    async def _check_oi_ratios(self):
        bn_oi_map = {}
        for symbol, data in self.data_store.items():
            if data.oi_history:
                current_oi = data.oi_history[-1][1]
                if current_oi > 0:
                    base = symbol.replace("USDT", "")
                    bn_oi_map[base] = current_oi

        if not bn_oi_map: return

        tasks = []
        names = []
        for name, ex in self.exchanges.items():
            names.append(name)
            tasks.append(self._fetch_tickers_safe(name, ex))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        current_abnormal_list = []
        detected_keys = set()

        for i, res in enumerate(results):
            ex_name = names[i]
            if isinstance(res, Exception) or not res:
                continue
            
            tickers = res
            threshold = self.ratios.get(ex_name, 0.3)
            
            for symbol_code, ticker in tickers.items():
                open_interest = ticker.get('openInterest')
                price = ticker.get('last')
                
                if not open_interest or not price:
                    continue
                
                base = symbol_code.split('/')[0]
                if base not in bn_oi_map:
                    continue
                
                # 计算 OI 价值 (U)
                # 警告：OKX 的 openInterest 通常是张数。
                # CCXT 尽力标准化，但在 MVP 阶段，我们假设 价值 = 张数 * 价格
                # 如果发现 OKX 数据异常巨大（差几百倍），则说明 OKX 1张 != 1币
                # 通常 OKX USDT合约 1张=1币 或者 0.1币 等，这里粗略计算，主要抓巨大异动
                target_oi_val = open_interest * price
                
                if target_oi_val < 100_000:
                    continue

                bn_val = bn_oi_map[base]
                ratio = target_oi_val / bn_val
                
                if ratio > threshold:
                    alert_key = f"{base}_{ex_name}"
                    detected_keys.add(alert_key)
                    
                    data_entry = {
                        "symbol": base,
                        "ex": ex_name,
                        "ratio": ratio,
                        "target_oi": target_oi_val,
                        "bn_oi": bn_val
                    }
                    current_abnormal_list.append(data_entry)
                    
                    if alert_key not in self.active_alerts:
                        self.active_alerts.add(alert_key)
                        await self._send_first_alert(data_entry)

        self.latest_abnormal_data = current_abnormal_list
        self.active_alerts = self.active_alerts.intersection(detected_keys)

    async def _fetch_tickers_safe(self, name, exchange):
        try:
            return await asyncio.wait_for(exchange.fetch_tickers(), timeout=15)
        except Exception:
            return {}

    async def _send_first_alert(self, data):
        ex_display = data['ex'].upper()
        tgt_fmt = f"{data['target_oi']/1_000_000:.1f}M"
        bn_fmt = f"{data['bn_oi']/1_000_000:.1f}M"
        
        msg = (
            f"🦈 <b>OI 异常堆积警报</b>\n"
            f"标的: <b>{data['symbol']}</b>\n"
            f"来源: {ex_display}\n"
            f"占比: <b>{data['ratio']*100:.1f}%</b> (基准: Binance)\n"
            f"------------------\n"
            f"{ex_display} OI: {tgt_fmt}\n"
            f"Binance OI: {bn_fmt}\n"
            f"⚠️ 外部持仓过重，警惕定点爆破!"
        )
        logger.info(f"OI报警: {data['symbol']} {ex_display} Ratio {data['ratio']:.2f}")
        await self.notifier.send_message(msg)

    def get_summary_data(self):
        return sorted(self.latest_abnormal_data, key=lambda x: x['ratio'], reverse=True)
