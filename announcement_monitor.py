# announcement_monitor.py
import asyncio
import aiohttp
import logging
import json
import re
from config import CONFIG

logger = logging.getLogger(__name__)

class AnnouncementMonitor:
    def __init__(self, notifier_ref):
        self.notifier = notifier_ref
        self.proxy = CONFIG['proxy'] or None
        self.seen_ids = {
            "BINANCE": set(),
            "UPBIT": set()
        }
        self.is_initialized = False

    async def start(self):
        logger.info("启动公告监控模块 (Binance & Upbit Articles)...")
        await self._refresh_all(silent=True)
        self.is_initialized = True
        logger.info("公告监控初始化完成，开始监听新公告...")

        while True:
            try:
                await self._refresh_all(silent=False)
            except Exception as e:
                logger.error(f"公告监控循环异常: {e}")
            await asyncio.sleep(30)

    async def _refresh_all(self, silent=False):
        await asyncio.gather(
            self._check_binance_news(silent),
            self._check_upbit_news(silent)
        )

    async def _check_binance_news(self, silent):
        # 监控 "New Cryptocurrency Listing"
        url = "https://www.binance.com/bapi/composite/v1/public/cms/article/catalog/list?catalogs=48&pageNo=1&pageSize=15"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, proxy=self.proxy) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        articles = data.get('data', [])[0].get('articles', [])
                        for item in articles[:5]:
                            article_id = str(item['id'])
                            title = item['title']
                            if article_id not in self.seen_ids["BINANCE"]:
                                self.seen_ids["BINANCE"].add(article_id)
                                if self.is_initialized and not silent:
                                    if any(k in title.lower() for k in ["list", "launch", "open trading"]):
                                        await self._send_alert("Binance", title, f"https://www.binance.com/en/support/announcement/{item['code']}")
        except Exception:
            pass

    async def _check_upbit_news(self, silent):
        # 监控 "Trade Support"
        url = "https://api-manager.upbit.com/api/v1/notices?page=1&per_page=20&thread_name=trade_support"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, proxy=self.proxy) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        items = data.get('data', {}).get('list', [])
                        for item in items[:5]:
                            notice_id = str(item['id'])
                            title = item['title']
                            if notice_id not in self.seen_ids["UPBIT"]:
                                self.seen_ids["UPBIT"].add(notice_id)
                                if self.is_initialized and not silent:
                                    if "마켓" in title or "Market" in title or "Addition" in title:
                                        link = f"https://upbit.com/service_center/notice?id={notice_id}"
                                        await self._send_alert("Upbit", title, link)
        except Exception:
            pass

    async def _send_alert(self, exchange, title, link):
        symbols = re.findall(r'[\[\(]([A-Z0-9]{2,10})[\]\)]', title)
        symbol_str = ", ".join(symbols) if symbols else "Unknown"
        msg = (
            f"📢 <b>交易所公告监控</b>\n"
            f"来源: {exchange}\n"
            f"标题: <b>{title}</b>\n"
            f"可能币种: <b>{symbol_str}</b>\n"
            f"------------------\n"
            f"🔗 <a href='{link}'>点击查看公告详情</a>"
        )
        logger.info(f"公告报警: {exchange} - {title}")
        await self.notifier.send_message(msg)
