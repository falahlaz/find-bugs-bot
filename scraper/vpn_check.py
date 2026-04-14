import asyncio
import logging

import config

logger = logging.getLogger(__name__)


async def is_vpn_connected() -> bool:
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(config.VPN_CHECK_HOST, 443),
            timeout=3,
        )
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        logger.warning("VPN check failed: cannot reach %s:443", config.VPN_CHECK_HOST)
        return False