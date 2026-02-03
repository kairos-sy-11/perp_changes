# strategy.py
import time
from datetime import datetime
from config import CONFIG

class StrategyEngine:
    def __init__(self):
        self.cooldowns = {} 
        self.fund_states = {} 
        
        # [新增] 短期事件缓存 (用于让瞬间异动在网页上停留)
        # 格式: { "BTCUSDT_PRICE": {data...}, "ETHUSDT_OI": {data...} }
        self.event_cache = {} 

    def check(self, symbol, data):
        """核心判断逻辑"""
        oi_delta_5m, oi_now, _ = data.get_oi_delta(300)
        funding = data.funding_rate
        abs_fund = abs(funding)
        now = time.time()
        
        # 基础数据快照 (用于缓存)
        snapshot_data = {
            "symbol": symbol,
            "price_now": data.price,
            "price_past": data.get_price_delta(300)[1],
            "oi_now": oi_now,
            "oi_delta": oi_delta_5m,
            "rate": funding,
            "fund_delta": data.get_funding_delta(300),
            "cvd_total": data.get_cvd_sum(1800),
            "cvd_5m": data.get_cvd_sum(300),
            "level": 0, # 默认为0
            "tags": []  # 存储标签: [价格异动], [OI异动]
        }

        # --- Funding Logic ---
        if symbol not in self.fund_states:
            self.fund_states[symbol] = {'level': 0, 'last_val': 0.0, 'exit_mode': False, 'exit_start': 0, 'exit_count': 0}
        f_state = self.fund_states[symbol]
        prev_level = f_state['level']
        f_msg = None
        current_level = 0
        thresholds = CONFIG["thresholds"]["funding_levels"]
        crit_thresh = CONFIG["thresholds"]["funding_critical"]
        
        if abs_fund >= crit_thresh: current_level = 4
        elif abs_fund > thresholds[2]: current_level = 3
        elif abs_fund > thresholds[1]: current_level = 2
        elif abs_fund > thresholds[0]: current_level = 1
        
        if f_state['exit_mode']:
            if current_level == 4: f_state['exit_mode'] = False
            else:
                if f_state['exit_count'] == 1 and (now - f_state['exit_start'] > 60):
                    f_msg = f"📉 <b>费率回落 (连报 2/3)</b>\n风险解除确认中\n当前: {funding*100:.4f}%"
                    f_state['exit_count'] = 2
                elif f_state['exit_count'] == 2 and (now - f_state['exit_start'] > 120):
                    f_msg = f"📉 <b>费率回落 (连报 3/3)</b>\n已回归常态区间\n当前: {funding*100:.4f}%"
                    f_state['exit_count'] = 3
                    f_state['exit_mode'] = False 

        if not f_msg:
            if current_level == 4:
                if prev_level < 4: f_msg = f"🚨 <b>费率极值 (>=2%)</b>\n进入高危区域！"
                elif funding != f_state['last_val']: f_msg = f"🚨 <b>费率变动 (>=2%)</b>\n数值改变: {funding*100:.4f}%"
            elif prev_level == 4 and current_level < 4:
                f_state['exit_mode'] = True
                f_state['exit_start'] = now
                f_state['exit_count'] = 1
                f_msg = f"📉 <b>费率回落 (连报 1/3)</b>\n脱离高危区 (<2%)\n当前: {funding*100:.4f}%"
            elif current_level > 0:
                if current_level > prev_level:
                    if not f_state['exit_mode']: f_msg = f"⚠️ <b>费率异动 (Lv.{current_level})</b>\n突破 {abs_fund*100:.2f}%"

        f_state['level'] = current_level
        f_state['last_val'] = funding
        
        # --- 价格异动检测 ---
        p_now, p_1m_ago = data.get_price_delta(60)
        p_3m_ago = data.get_price_delta(180)[1] 
        pct_1m = (p_now - p_1m_ago) / p_1m_ago if p_1m_ago > 0 else 0
        pct_3m = (p_now - p_3m_ago) / p_3m_ago if p_3m_ago > 0 else 0
        
        is_large_cap = oi_now >= CONFIG["thresholds"]["oi_small_cap"]
        p_thresh_1m = CONFIG["thresholds"]["price_large_1m"] if is_large_cap else CONFIG["thresholds"]["price_small_1m"]
        p_thresh_3m = CONFIG["thresholds"]["price_large_3m"] if is_large_cap else CONFIG["thresholds"]["price_small_3m"]
        
        price_msg = None
        price_tag = None
        
        if abs(pct_1m) >= p_thresh_1m:
            if self._check_cooldown(f"{symbol}_PRICE_1M", 60):
                emoji = "🚀" if pct_1m > 0 else "🩸"
                price_msg = f"{emoji} <b>极速异动 (1m)</b>\n幅度: {pct_1m*100:+.2f}%"
                price_tag = "🚀 1m极速" if pct_1m > 0 else "🩸 1m极速"
        elif abs(pct_3m) >= p_thresh_3m:
            if self._check_cooldown(f"{symbol}_PRICE_3M", 60):
                emoji = "📈" if pct_3m > 0 else "📉"
                price_msg = f"{emoji} <b>趋势异动 (3m)</b>\n幅度: {pct_3m*100:+.2f}%"
                price_tag = "📈 3m趋势" if pct_3m > 0 else "📉 3m趋势"

        # [新增] 如果触发价格异动，存入缓存
        if price_tag:
            self.event_cache[f"{symbol}_PRICE"] = {
                "ts": now, "tag": price_tag, "data": snapshot_data
            }

        # --- OI 异动检测 ---
        oi_triggered = False
        if is_large_cap:
            pct_change = (abs(oi_delta_5m) / oi_now) if oi_now > 0 else 0
            if pct_change >= CONFIG["thresholds"]["oi_change_pct"]: oi_triggered = True
        else:
            if abs(oi_delta_5m) >= CONFIG["thresholds"]["oi_change_abs"]: oi_triggered = True

        oi_msg = None
        if oi_triggered and self._check_cooldown(f"{symbol}_OI", CONFIG["cooldown_seconds"]):
            direction = "📈 OI 激增" if oi_delta_5m > 0 else "📉 OI 骤降"
            oi_msg = f"<b>{direction}</b>"
            # [新增] 存入缓存
            self.event_cache[f"{symbol}_OI"] = {
                "ts": now, "tag": direction, "data": snapshot_data
            }

        # 优先级返回消息 (Funding > Price > OI)
        if f_msg: return "FUNDING", self._fmt_msg(symbol, f_msg, data, oi_now)
        if price_msg: return "PRICE", self._fmt_msg(symbol, price_msg, data, oi_now)
        if oi_msg: return "OI", self._fmt_msg(symbol, oi_msg, data, oi_now)
        
        return None, None

    def get_abnormal_list(self, data_store):
        """
        获取异常列表：合并 实时费率异常 + 近期(5min内)价格/OI异动
        """
        now = time.time()
        # 使用字典按 symbol 去重，同一币种合并显示
        merged_data = {}

        # 1. 扫描实时 Funding 状态
        for symbol, state in self.fund_states.items():
            level = state.get('level', 0)
            if level > 0:
                if symbol in data_store:
                    data = data_store[symbol]
                    oi_delta, oi_now, _ = data.get_oi_delta(300)
                    entry = {
                        'symbol': symbol,
                        'level': level,
                        'rate': state.get('last_val', 0.0),
                        'fund_delta': data.get_funding_delta(300),
                        'oi_now': oi_now,
                        'oi_delta': oi_delta,
                        'cvd_total': data.get_cvd_sum(1800),
                        'cvd_5m': data.get_cvd_sum(300),
                        'price_now': data.get_price_delta(300)[0],
                        'price_past': data.get_price_delta(300)[1],
                        'tags': [],
                        'event_ts': 0 # 费率是持续状态，无特定触发时间
                    }
                    merged_data[symbol] = entry

        # 2. 扫描短期缓存 (Price/OI 异动)
        expired_keys = []
        for key, event in self.event_cache.items():
            # 5分钟 (300s) 后过期
            if now - event['ts'] > 300:
                expired_keys.append(key)
                continue
            
            symbol = key.split('_')[0]
            tag = event['tag']
            
            # 如果该币已经在列表中 (因为费率异常)，则追加标签
            if symbol in merged_data:
                if tag not in merged_data[symbol]['tags']:
                    merged_data[symbol]['tags'].append(tag)
                    # 更新时间戳为最新的事件时间
                    if event['ts'] > merged_data[symbol]['event_ts']:
                        merged_data[symbol]['event_ts'] = event['ts']
            else:
                # 如果不在，则使用快照数据创建新条目
                entry = event['data'].copy() # 浅拷贝快照
                entry['tags'] = [tag]
                entry['event_ts'] = event['ts']
                merged_data[symbol] = entry

        # 清理过期缓存
        for k in expired_keys:
            del self.event_cache[k]

        # 转为列表并排序
        # 排序优先级: 有事件发生(时间倒序) > 费率等级(高到低)
        result_list = list(merged_data.values())
        result_list.sort(key=lambda x: (x['event_ts'], abs(x['rate'])), reverse=True)
        
        return result_list

    def _check_cooldown(self, key, seconds):
        now = time.time()
        last = self.cooldowns.get(key, 0)
        if now - last > seconds:
            self.cooldowns[key] = now
            return True
        return False

    def _format_volume(self, value):
        abs_val = abs(value)
        sign = "+" if value >= 0 else "-"
        if abs_val >= 1_000_000: return f"{sign}{abs_val/1_000_000:.1f}M"
        elif abs_val >= 1_000: return f"{sign}{abs_val/1_000:.0f}K"
        else: return f"{sign}{abs_val:.0f}"

    def _fmt_msg(self, symbol, title_line, data, oi_now):
        rows = []
        for window in [300, 600, 900]:
            label = f"{int(window/60):02d}m" 
            cvd_val = data.get_cvd_sum(window)
            cvd_str = self._format_volume(cvd_val)
            oi_d, _, _ = data.get_oi_delta(window)
            oi_str = self._format_volume(oi_d)
            rows.append(f"{label} {cvd_str:>7} {oi_str:>7}")
        matrix_str = "\n".join(rows)
        return f"""[{symbol}] {title_line}
------------------
💰 价格: {data.price}
💸 费率: {data.funding_rate*100:.4f}%
📊 持仓: {oi_now/1_000_000:.1f}M (总量)
------------------
<pre>
⏱窗口   CVD(U)   OI变化
{matrix_str}
</pre>
⏱ {datetime.now().strftime('%H:%M:%S')}"""
