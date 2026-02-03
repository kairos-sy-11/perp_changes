# main.py
import asyncio
import logging
import sys
from monitor_pool import MonitorSystem

# 配置日志
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s', 
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        system = MonitorSystem()
        asyncio.run(system.start())
    except KeyboardInterrupt:
        logger.info("监控已手动停止")
    except Exception as e:
        logger.error(f"程序崩溃: {e}")
        import traceback
        traceback.print_exc()
