# onchain_monitor.py
import asyncio
import logging
import json
import os
from web3 import Web3
from config import CONFIG

logger = logging.getLogger(__name__)

ERC20_ABI = json.loads('[{"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"payable":false,"stateMutability":"view","type":"function"},{"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"payable":false,"stateMutability":"view","type":"function"},{"constant":true,"inputs":[],"name":"symbol","outputs":[{"name":"","type":"string"}],"payable":false,"stateMutability":"view","type":"function"}]')

class OnChainMonitor:
    def __init__(self, notifier_ref):
        self.notifier = notifier_ref
        self.rpcs = CONFIG['onchain']['rpcs']
        self.file_path = "onchain_targets.json"
        
        # 核心数据：从 JSON 加载
        self.targets = self._load_targets()
        
        self.w3_instances = {}
        self.last_balances = {}
        self.token_info_cache = {} # 缓存精度和符号

    def _load_targets(self):
        if not os.path.exists(self.file_path):
            with open(self.file_path, 'w') as f: json.dump([], f)
            return []
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []

    def _save_targets(self):
        with open(self.file_path, 'w', encoding='utf-8') as f:
            json.dump(self.targets, f, indent=2, ensure_ascii=False)

    # --- [新增] 动态管理接口 ---
    def add_dynamic_target(self, chain, wallet, token_addr, alias):
        """添加新监控目标"""
        # 简单的查重
        for t in self.targets:
            if t['chain'] == chain and t['wallet'] == wallet and t['token_address'] == token_addr:
                return False, "该监控项已存在"

        # 尝试连接链获取代币符号 (如果是 ERC20)
        symbol = "Unknown"
        if token_addr == "native":
            symbol = "ETH" if chain in ["ETH", "ARB", "OP"] else "BNB"
        else:
            # 尝试自动获取 Symbol
            if chain in self.w3_instances:
                try:
                    w3 = self.w3_instances[chain]
                    ctr = w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=ERC20_ABI)
                    symbol = ctr.functions.symbol().call()
                except:
                    symbol = "TOKEN"

        new_item = {
            "name": alias,
            "chain": chain,
            "wallet": wallet,
            "token_symbol": symbol,
            "token_address": token_addr
        }
        self.targets.append(new_item)
        self._save_targets()
        return True, f"成功添加监控:\n名称: {alias}\n币种: {symbol}\n地址: {wallet[:6]}..."

    def remove_dynamic_target(self, index):
        """按索引删除"""
        try:
            idx = int(index)
            if 0 <= idx < len(self.targets):
                removed = self.targets.pop(idx)
                self._save_targets()
                return True, f"已删除: {removed['name']} ({removed['token_symbol']})"
            else:
                return False, "索引超出范围"
        except ValueError:
            return False, "索引必须是数字"

    def get_target_list_str(self):
        """获取列表文本"""
        if not self.targets: return "当前没有监控目标。"
        lines = ["📋 <b>链上监控列表</b>", "------------------"]
        for i, t in enumerate(self.targets):
            lines.append(f"<b>{i}. {t['name']}</b>")
            lines.append(f"   [{t['chain']}] {t['token_symbol']}")
            lines.append(f"   👛 {t['wallet'][:6]}...{t['wallet'][-4:]}")
            lines.append("")
        return "\n".join(lines)

    # --- 之前的核心逻辑 ---
    async def start(self):
        logger.info("启动链上监控模块 (Web3 RPC)...")
        for chain, url in self.rpcs.items():
            try:
                w3 = Web3(Web3.HTTPProvider(url))
                if w3.is_connected():
                    self.w3_instances[chain] = w3
            except Exception: pass

        # 初始化基准
        await self._check_all(silent=True)
        
        while True:
            await asyncio.sleep(60) 
            try:
                await self._check_all(silent=False)
            except Exception as e:
                logger.error(f"链上监控循环异常: {e}")

    async def _check_all(self, silent=False):
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_check_logic, silent)

    def _sync_check_logic(self, silent):
        # 每次循环都使用最新的 self.targets
        for target in self.targets:
            chain = target['chain']
            if chain not in self.w3_instances: continue
            
            w3 = self.w3_instances[chain]
            try:
                wallet = Web3.to_checksum_address(target['wallet'])
                token_addr = target['token_address']
                
                current_balance = 0.0
                if token_addr == "native":
                    raw_bal = w3.eth.get_balance(wallet)
                    current_balance = float(w3.from_wei(raw_bal, 'ether'))
                else:
                    c_addr = Web3.to_checksum_address(token_addr)
                    # 简单缓存 decimals
                    if c_addr not in self.token_info_cache:
                        ctr = w3.eth.contract(address=c_addr, abi=ERC20_ABI)
                        self.token_info_cache[c_addr] = ctr.functions.decimals().call()
                    
                    decs = self.token_info_cache[c_addr]
                    ctr = w3.eth.contract(address=c_addr, abi=ERC20_ABI)
                    raw = ctr.functions.balanceOf(wallet).call()
                    current_balance = raw / (10 ** decs)

                cache_key = f"{chain}_{wallet}_{token_addr}"
                
                if cache_key in self.last_balances:
                    prev = self.last_balances[cache_key]
                    if current_balance != prev:
                        delta = current_balance - prev
                        # 过滤极小额
                        if abs(delta) > 0.000001:
                            if not silent:
                                self._notify(target['name'], chain, target['token_symbol'], current_balance, delta, wallet)
                
                self.last_balances[cache_key] = current_balance
            except Exception:
                pass

    def _notify(self, name, chain, symbol, balance, delta, wallet):
        emoji = "🟢" if delta > 0 else "🔴"
        action = "转入" if delta > 0 else "转出"
        msg = (
            f"🐋 <b>链上异动监控</b>\n"
            f"目标: <b>{name}</b>\n"
            f"链/币: {chain} / {symbol}\n"
            f"动作: {emoji} <b>{action} {abs(delta):,.4f} {symbol}</b>\n"
            f"当前余额: {balance:,.4f}\n"
            f"地址: <pre>{wallet}</pre>"
        )
        loop = asyncio.get_event_loop()
        asyncio.run_coroutine_threadsafe(self.notifier.send_message(msg), loop)
