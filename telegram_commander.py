# telegram_commander.py
import asyncio
import aiohttp
import logging
import json
from config import CONFIG

logger = logging.getLogger(__name__)

class TelegramCommander:
    """监听 TG 消息并执行指令"""
    def __init__(self, onchain_monitor_ref):
        self.token = CONFIG['telegram']['bot_token']
        self.allowed_chat_id = CONFIG['telegram']['chat_id'] # 只允许管理员操作
        self.proxy = CONFIG['proxy'] or None
        self.onchain = onchain_monitor_ref
        self.last_update_id = 0

    async def start(self):
        logger.info("启动 Telegram 指令监听模块...")
        # 先清空积压的消息
        await self._get_updates(offset=-1)
        
        while True:
            try:
                updates = await self._get_updates(offset=self.last_update_id + 1)
                for u in updates:
                    self.last_update_id = u['update_id']
                    if 'message' in u:
                        await self._handle_message(u['message'])
            except Exception as e:
                logger.error(f"指令监听出错: {e}")
                await asyncio.sleep(5)
            
            # 轮询间隔 1秒，保证响应速度
            await asyncio.sleep(1)

    async def _get_updates(self, offset):
        url = f"https://api.telegram.org/bot{self.token}/getUpdates"
        params = {"offset": offset, "timeout": 10}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, proxy=self.proxy) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get('result', [])
        return []

    async def _handle_message(self, msg):
        # 1. 权限校验 (只处理 config 里配置的 chat_id 或者是群组内的消息)
        # 注意：Telegram 群组 ID 是负数，私聊是正数。这里做一个简单的鉴权。
        # 如果您是在私聊里控制，请确保 config 里的 chat_id 是您的私聊 ID，或者在这里暂时去掉鉴权方便测试。
        # sender_id = str(msg['chat']['id'])
        # if sender_id != self.allowed_chat_id:
        #    return 

        text = msg.get('text', '').strip()
        if not text.startswith('/'): return

        chat_id = msg['chat']['id']
        parts = text.split()
        cmd = parts[0]

        # --- 指令处理 ---
        
        # 1. 查看列表
        if cmd == "/list":
            resp = self.onchain.get_target_list_str()
            await self._reply(chat_id, resp)

        # 2. 添加监控
        # 格式: /add ETH 0x123... 0xabc... 巨鲸A
        elif cmd == "/add":
            if len(parts) < 5:
                await self._reply(chat_id, "❌ 格式错误\n用法: <code>/add 链 钱包地址 代币合约 备注</code>\n示例: /add ETH 0x123... native 我的钱包")
                return
            
            chain = parts[1].upper()
            wallet = parts[2]
            token = parts[3]
            alias = " ".join(parts[4:]) # 备注可以带空格
            
            success, info = self.onchain.add_dynamic_target(chain, wallet, token, alias)
            icon = "✅" if success else "❌"
            await self._reply(chat_id, f"{icon} {info}")

        # 3. 删除监控
        # 格式: /del 0
        elif cmd == "/del":
            if len(parts) < 2:
                await self._reply(chat_id, "❌ 格式错误。用法: /del 序号 (从 /list 获取)")
                return
            
            success, info = self.onchain.remove_dynamic_target(parts[1])
            icon = "✅" if success else "❌"
            await self._reply(chat_id, f"{icon} {info}")
            
        # 4. 帮助
        elif cmd == "/help":
            help_text = (
                "🤖 <b>Bot 控制台</b>\n\n"
                "/list - 查看链上监控列表\n"
                "/add - 添加监控\n"
                "  格式: <code>/add 链 钱包 代币合约 备注</code>\n"
                "  本币: 代币合约填 native\n"
                "/del - 删除监控\n"
                "  格式: <code>/del 序号</code>"
            )
            await self._reply(chat_id, help_text)

    async def _reply(self, chat_id, text):
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        async with aiohttp.ClientSession() as session:
            await session.post(url, json=payload, proxy=self.proxy)
