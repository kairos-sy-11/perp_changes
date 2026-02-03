# config.py
import os

# NOTE: 敏感信息通过环境变量读取，本地开发可设置环境变量或使用 .env 文件
CONFIG = {
    "telegram": {
        "bot_token": os.getenv("TG_BOT_TOKEN", ""),
        "chat_id": os.getenv("TG_CHAT_ID", "")
    },
    # NOTE: 代理配置，GitHub Actions 不需要代理，本地可通过环境变量设置
    "proxy": os.getenv("HTTP_PROXY", ""),
    
    "pool": {
        "filename": "monitor_pool.json",
        "full_refresh_days": 7,
        "incremental_interval_minutes": 20,
        "blacklist": ["USDCUSDT", "USDPUSDT", "FDUSDUSDT"]
    },

    "thresholds": {
        # --- [新增] 跨交易所 OI 对比阈值 ---
        "oi_compare": {
            "bitget": 0.30,  # Bitget/Binance > 30%
            "bybit": 0.50,   # Bybit/Binance > 50%
            "gate": 0.30,    # Gate/Binance > 30%
            "okx": 0.50      # [新增] OKX 阈值
        },
        
        # --- 价格异动 ---
        "price_small_1m": 0.05, "price_small_3m": 0.10,
        "price_large_1m": 0.03, "price_large_3m": 0.05,

        # --- OI 阈值 ---
        "oi_small_cap": 30_000_000, "oi_change_abs": 1_000_000, "oi_change_pct": 0.05,

        # --- 资金费率阈值 ---
        "funding_levels": [0.005, 0.010, 0.018], "funding_critical": 0.020,
        
        "spread_pct": 0.05,
        "spread_check_interval": 15,
        "spread_cooldown": 1800 
    },
    
    "window_seconds": 300, "cvd_long_window": 1800, "cooldown_seconds": 300, "warmup_seconds": 60,

    "onchain": {
        "rpcs": {
            "ETH": "https://rpc.ankr.com/eth",
            "BSC": "https://rpc.ankr.com/bsc",
            "ARB": "https://rpc.ankr.com/arbitrum",
            "OP": "https://rpc.ankr.com/optimism",
            "POLYGON": "https://rpc.ankr.com/polygon"
        },
        "targets": []
    }
}
