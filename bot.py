from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from config import settings
from db import init_db
from handlers import routers
from middlewares import AntiFloodMiddleware

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("nasiba-consultant")


async def main() -> None:
    await init_db()

    bot = Bot(token=settings.bot_token)
    dp = Dispatcher(storage=MemoryStorage())
    for r in routers:
        dp.include_router(r)

    # Анти-флуд: режем спам в личке ДО дорогих вызовов ИИ (бережём бюджет).
    dp.business_message.middleware(AntiFloodMiddleware())

    # Явно разрешаем business-апдейты (иначе Telegram их не пришлёт).
    allowed = dp.resolve_used_update_types()
    for upd in ("business_connection", "business_message",
                "edited_business_message", "deleted_business_messages"):
        if upd not in allowed:
            allowed.append(upd)

    logger.info("Запускаюсь. Разрешённые апдейты: %s", allowed)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=allowed)


if __name__ == "__main__":
    asyncio.run(main())
