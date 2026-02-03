# notifier.py
import aiohttp
import logging
import time
from collections import deque
from config import CONFIG

logger = logging.getLogger(__name__)

class TelegramNotifier:
    def __init__(self):
        self.base_url = f"https://api.telegram.org/bot{CONFIG['telegram']['bot_token']}/sendMessage"
        self.chat_id = CONFIG['telegram']['chat_id']
        self.proxy = CONFIG['proxy'] or None  # NOTE: 空字符串转换为 None
        
        # [新增] 历史消息记录 (最近100条)
        self.history = deque(maxlen=100)

    async def send_message(self, text):
        """异步发送消息并记录历史"""
        # 1. 记录到内存历史
        self.history.appendleft({
            "time": time.time(),
            "text": text
        })

        # 2. 发送到 Telegram
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML"
        }
        async with aiohttp.ClientSession() as session:
            try:
                # NOTE: 只有当 proxy 不为空时才传递 proxy 参数
                async with session.post(self.base_url, json=payload, proxy=self.proxy) as resp:
                    if resp.status != 200:
                        logger.error(f"TG发送失败: {await resp.text()}")
            except Exception as e:
                logger.error(f"TG网络错误: {e}")

