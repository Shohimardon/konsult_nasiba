from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message

from config import settings

logger = logging.getLogger(__name__)

# --- Настройки анти-флуда (подобраны «по-человечески», легко менять) ---
MIN_INTERVAL = 2.0        # минимум секунд между сообщениями одного человека
BURST_WINDOW = 60.0       # окно подсчёта всплеска, сек
BURST_LIMIT = 15          # макс. сообщений за окно всплеска
RECEIPT_WINDOW = 3600.0   # окно лимита чеков, сек (1 час)
RECEIPT_LIMIT = 6         # макс. чеков в час от одного человека
MAX_TEXT_LEN = 1500       # макс. длина текста, который отдаём ИИ
WARN_COOLDOWN = 30.0      # не чаще раза в N сек шлём человеку предупреждение

# Короткие статичные ответы (НЕ жгут ИИ) — на 3 языках сразу.
FLOOD_MSG = (
    "Секундгина 🙂 Сал шошмай ёзинг, барча саволларингизга жавоб бераман.\n"
    "(Чуть помедленнее, пожалуйста — я всё увижу и отвечу 🙏)"
)
LONG_TEXT_MSG = (
    "Хабар жуда узун чиқди 🙂 Илтимос, саволни қисқароқ ёзинг — шунда аниқроқ ёрдам бераман.\n"
    "(Сообщение слишком длинное — напишите вопрос покороче, так я отвечу точнее 🙏)"
)
RECEIPT_MSG = (
    "Жуда кўп чек юбордингиз 🙂 Насиба устоз уларни кўриб чиқмоқда, бироз кутинг.\n"
    "(Слишком много чеков подряд — Насиба их уже смотрит, немного подождите 🙏)"
)


class AntiFloodMiddleware(BaseMiddleware):
    """Бережёт AI-бюджет и нервы: режет спам ДО дорогих вызовов ИИ.

    Защиты (по порядку):
      1. слишком длинный текст — не кормим им ИИ;
      2. слишком часто (< MIN_INTERVAL) — мягко просим помедленнее;
      3. всплеск (BURST_LIMIT за BURST_WINDOW) — то же;
      4. лимит чеков в час (каждый чек = дорогой vision-вызов).

    Насибу и админов никогда не трогаем. Предупреждение шлём не чаще
    раза в WARN_COOLDOWN, чтобы самим не спамить флудеру в ответ.
    Состояние — in-memory (для одного polling-инстанса; для масштаба — Redis).
    """

    def __init__(self) -> None:
        self._last_ts: dict[int, float] = {}
        self._events: dict[int, deque[float]] = defaultdict(deque)
        self._receipts: dict[int, deque[float]] = defaultdict(deque)
        self._warned: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        user = event.from_user
        if user is None:
            return await handler(event, data)

        # Насибу (её исходящие) и админов пропускаем без ограничений.
        if user.id == settings.nasiba_chat_id or user.id in settings.admin_ids:
            return await handler(event, data)

        uid = user.id
        now = time.monotonic()

        # 1) Слишком длинный текст — отбиваем, ИИ не трогаем.
        text = event.text or event.caption
        if text and len(text) > MAX_TEXT_LEN:
            await self._warn(event, uid, now, LONG_TEXT_MSG)
            return

        # 2) Минимальный интервал между сообщениями.
        last = self._last_ts.get(uid, 0.0)
        if now - last < MIN_INTERVAL:
            await self._warn(event, uid, now, FLOOD_MSG)
            return

        # 3) Всплеск за окно.
        events = self._events[uid]
        while events and now - events[0] > BURST_WINDOW:
            events.popleft()
        if len(events) >= BURST_LIMIT:
            await self._warn(event, uid, now, FLOOD_MSG)
            return

        # 4) Лимит чеков в час (каждый чек = дорогой vision-вызов).
        if self._is_receipt(event):
            receipts = self._receipts[uid]
            while receipts and now - receipts[0] > RECEIPT_WINDOW:
                receipts.popleft()
            if len(receipts) >= RECEIPT_LIMIT:
                await self._warn(event, uid, now, RECEIPT_MSG)
                return
            receipts.append(now)

        # Пропускаем дальше — учитываем сообщение.
        events.append(now)
        self._last_ts[uid] = now
        return await handler(event, data)

    @staticmethod
    def _is_receipt(event: Message) -> bool:
        if event.photo:
            return True
        doc = event.document
        if doc and doc.mime_type and (
            doc.mime_type == "application/pdf" or doc.mime_type.startswith("image/")
        ):
            return True
        return False

    async def _warn(self, event: Message, uid: int, now: float, msg: str) -> None:
        """Шлём предупреждение не чаще раза в WARN_COOLDOWN (сами не спамим)."""
        if now - self._warned.get(uid, 0.0) < WARN_COOLDOWN:
            return
        self._warned[uid] = now
        try:
            await event.answer(msg)
        except Exception as e:  # noqa: BLE001 — предупреждение не должно ломать поток
            logger.warning("Не смог отправить анти-флуд предупреждение: %r", e)
