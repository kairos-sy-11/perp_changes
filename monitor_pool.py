# monitor_pool.py
import asyncio
import time
import json
import os
import aiohttp
import logging
from datetime import datetime, timedelta
from config import CONFIG
from data_collector import MarketData, DataCollector
from strategy import StrategyEngine
from notifier import TelegramNotifier
from listing_monitor import ListingMonitor
from announcement_monitor import AnnouncementMonitor
from onchain_monitor import OnChainMonitor
from telegram_commander import TelegramCommander
from spread_monitor import SpreadMonitor
from oi_comparer import OIComparer
from wallet_monitor import WalletMonitor # [新增]

logger = logging.getLogger(__name__)

class SymbolPoolManager:
    """[PRD] 监控池核心管理类 (集成 CoinGecko 缓存过滤)"""
    def __init__(self, data_store_ref, collector_ref, notifier_ref):
        self.data_store = data_store_ref
        self.collector = collector_ref
        self.notifier = notifier_ref
        self.file_path = CONFIG["pool"]["filename"]
        self.proxy = CONFIG["proxy"] or None  # NOTE: 空字符串转换为 None
        self.blacklist = set(CONFIG["pool"].get("blacklist", []))

    async def initialize(self):
        pool_data = self._load_local_file()
        symbols = pool_data.get("symbols", [])
        last_updated = pool_data.get("last_updated", "2000-01-01T00:00:00")
        
        updated_at = datetime.fromisoformat(last_updated)
        days_diff = (datetime.now() - updated_at).days
        
        need_refresh = False
        if not symbols:
            logger.info("本地监控池为空，执行首次全量初始化...")
            need_refresh = True
        elif days_diff >= CONFIG["pool"]["full_refresh_days"]:
            logger.info(f"监控池已过期 ({days_diff}天)，执行全量刷新...")
            need_refresh = True
        
        if need_refresh:
            symbols = await self._fetch_and_filter_symbols()
            self._save_local_file(symbols)
        else:
            logger.info(f"加载本地监控池，共 {len(symbols)} 个合约")

        self._update_data_store(symbols)

    async def loop_incremental_check(self):
        while True:
            interval = CONFIG["pool"]["incremental_interval_minutes"] * 60
            await asyncio.sleep(interval)
            try:
                latest_symbols = await self._fetch_and_filter_symbols()
                current_symbols = set(self.data_store.keys())
                new_symbols = [s for s in latest_symbols if s not in current_symbols]
                
                if new_symbols:
                    logger.info(f"🚀 发现符合条件的新合约: {new_symbols}")
                    self._update_data_store(new_symbols)
                    await self.collector.dynamic_subscribe(new_symbols)
                    all_symbols = list(self.data_store.keys())
                    self._save_local_file(all_symbols)
                    msg = f"🆕 <b>监控池更新</b>\n新上线合约: {', '.join(new_symbols)}"
                    await self.notifier.send_message(msg)
            except Exception as e:
                logger.error(f"增量检查失败: {e}")

    async def _fetch_and_filter_symbols(self):
        url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
        target_symbols = []
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, proxy=self.proxy) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for s in data["symbols"]:
                            if (s["contractType"] == "PERPETUAL" and s["status"] == "TRADING" and s["quoteAsset"] == "USDT"):
                                if s["symbol"] in self.blacklist: continue
                                target_symbols.append(s["symbol"])
                        return target_symbols
                    return []
        except Exception:
            return []

    def _update_data_store(self, symbol_list):
        for s in symbol_list:
            if s not in self.data_store:
                self.data_store[s] = MarketData(s)

    def _load_local_file(self):
        if not os.path.exists(self.file_path): return {}
        try:
            with open(self.file_path, 'r') as f: return json.load(f)
        except Exception: return {}

    def _save_local_file(self, symbols):
        data = {"last_updated": datetime.now().isoformat(), "symbols": symbols}
        with open(self.file_path, 'w') as f: json.dump(data, f, indent=2)

class MonitorSystem:
    def __init__(self):
        self.data_store = {}
        self.notifier = TelegramNotifier()
        self.collector = DataCollector(self.data_store)
        self.strategy = StrategyEngine()
        self.pool_manager = SymbolPoolManager(self.data_store, self.collector, self.notifier)
        
        self.listing_monitor = ListingMonitor(self.notifier)
        self.announcement_monitor = AnnouncementMonitor(self.notifier)
        self.onchain_monitor = OnChainMonitor(self.notifier)
        self.commander = TelegramCommander(self.onchain_monitor)
        self.spread_monitor = SpreadMonitor(self.notifier, self.data_store)
        self.oi_comparer = OIComparer(self.notifier, self.data_store)
        
        # [新增] 充提监控
        self.wallet_monitor = WalletMonitor(self.notifier, self.data_store)
        
        self.start_time = time.time()

    async def start(self):
        await self.pool_manager.initialize()
        await asyncio.gather(
            self.collector.run_ws(),
            self.collector.run_rest_poller(),
            self.pool_manager.loop_incremental_check(),
            self._strategy_loop(),
            self._loop_summary_report(),
            self.listing_monitor.start(),
            self.announcement_monitor.start(),
            self.onchain_monitor.start(),
            self.commander.start(),
            self.spread_monitor.start(),
            self.oi_comparer.start(),
            
            # [新增] 启动
            self.wallet_monitor.start()
        )

    async def _strategy_loop(self):
        logger.info(f"策略引擎启动，预热 {CONFIG['warmup_seconds']} 秒...")
        while True:
            if time.time() - self.start_time < CONFIG['warmup_seconds']:
                await asyncio.sleep(5)
                continue
            current_symbols = list(self.data_store.keys())
            for symbol in current_symbols:
                data = self.data_store[symbol]
                alert_type, msg = self.strategy.check(symbol, data)
                if msg:
                    logger.info(f"触发警报: {symbol} {alert_type}")
                    await self.notifier.send_message(msg)
            await asyncio.sleep(5)

    def _fmt_num(self, val, is_delta=False):
        abs_v = abs(val)
        sign = ""
        if is_delta: sign = "+" if val >= 0 else "-"
        if abs_v >= 1_000_000: s = f"{sign}{abs_v/1_000_000:.1f}M"
        elif abs_v >= 1_000: s = f"{sign}{abs_v/1_000:.0f}K"
        else: s = f"{sign}{abs_v:.0f}"
        return s

    async def _loop_summary_report(self):
        logger.info("启动定期汇总报告任务 (每10分钟)...")
        while True:
            await asyncio.sleep(600)
            try:
                abnormals = self.strategy.get_abnormal_list(self.data_store)
                oi_abnormals = self.oi_comparer.get_summary_data()
                
                if abnormals or oi_abnormals:
                    lines = ["📋 <b>异常状态定期汇总</b>", "------------------"]
                    
                    if abnormals:
                        for item in abnormals:
                            s = item['symbol']
                            v = item['rate'] * 100
                            f_delta = item['fund_delta'] * 100
                            l = item['level']
                            oi_now_str = self._fmt_num(item['oi_now'])
                            oi_delta_str = self._fmt_num(item['oi_delta'], is_delta=True)
                            cvd_tot_str = self._fmt_num(item['cvd_total'], is_delta=True)
                            cvd_5m_str = self._fmt_num(item['cvd_5m'], is_delta=True)
                            
                            p_now = item['price_now']
                            p_past = item['price_past']
                            p_pct = 0.0
                            if p_past > 0:
                                p_pct = (p_now - p_past) / p_past * 100
                            
                            icon = "⚠️"
                            if l == 4: icon = "🚨"
                            
                            lines.append(f"{icon} <b>{s}</b>: {p_now} (5m {p_pct:+.2f}%)")
                            lines.append(f"   Lv.{l} 资金费率异动: {v:.4f}% (5m {f_delta:+.4f}%)")
                            lines.append(f"   OI: {oi_now_str} (5m {oi_delta_str})")
                            lines.append(f"   CVD: {cvd_tot_str} (5m {cvd_5m_str})")
                            lines.append("")
                    
                    if oi_abnormals:
                        lines.append("🦈 <b>跨所持仓异常 (Top)</b>")
                        for item in oi_abnormals[:5]:
                            s = item['symbol']
                            ex = item['ex'].upper()
                            ratio = item['ratio'] * 100
                            bn_fmt = self._fmt_num(item['bn_oi'])
                            lines.append(f"• {ex} > <b>{s}</b>: {ratio:.0f}% (BN {bn_fmt})")
                        lines.append("")

                    lines.append(f"------------------\n⏱ {datetime.now().strftime('%H:%M')}")
                    
                    msg = "\n".join(lines)
                    if len(msg) > 3800:
                        part1 = "\n".join(lines[:len(lines)//2])
                        part2 = "\n".join(lines[len(lines)//2:])
                        await self.notifier.send_message(part1)
                        await self.notifier.send_message(part2)
                    else:
                        await self.notifier.send_message(msg)
                        
                    logger.info(f"发送汇总: 内盘异常 {len(abnormals)}, 外盘OI异常 {len(oi_abnormals)}")
            except Exception as e:
                logger.error(f"定期汇总失败: {e}")
