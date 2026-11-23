"""Dedicated Admin Control Bot entrypoint — independent of the customer storefront bot."""
from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from loguru import logger

from src.config import settings, validate_production_secrets
from src.admin_bot.middlewares.admin_acl import AdminAclMiddleware
from src.admin_bot.handlers import (
    hub, treasury, orders, binance_pay,
    suppliers, products_pricing, crm_support, marketing,
)
from src.admin_bot.handlers.live_feed import listen_admin_live_feed


async def main():
    validate_production_secrets()
    if not settings.ADMIN_BOT_TOKEN:
        logger.warning("ADMIN_BOT_TOKEN is not set. Admin Bot cannot start.")
        return
    if not settings.admin_telegram_id_set:
        logger.warning(
            "ADMIN_TELEGRAM_IDS / ADMIN_TELEGRAM_ID empty — Admin Bot will deny everyone."
        )

    bot = Bot(token=settings.ADMIN_BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    # Strict ACL on every update
    dp.message.middleware(AdminAclMiddleware())
    dp.callback_query.middleware(AdminAclMiddleware())

    dp.include_router(hub.router)
    dp.include_router(treasury.router)
    dp.include_router(orders.router)
    dp.include_router(binance_pay.router)
    dp.include_router(suppliers.router)
    dp.include_router(products_pricing.router)
    dp.include_router(crm_support.router)
    dp.include_router(marketing.router)

    feed_task = asyncio.create_task(listen_admin_live_feed(bot))
    logger.info(
        f"Admin Control Bot starting. Whitelisted IDs: {sorted(settings.admin_telegram_id_set)}"
    )
    try:
        await dp.start_polling(bot)
    finally:
        feed_task.cancel()
        try:
            await feed_task
        except asyncio.CancelledError:
            pass
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
