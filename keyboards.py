from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def reply_approval_kb(reply_id: int) -> InlineKeyboardMarkup:
    """Кнопки под черновиком ответа педагогу (гибридный режим)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Отправить", callback_data=f"reply:send:{reply_id}"),
            InlineKeyboardButton(text="✏️ Изменить", callback_data=f"reply:edit:{reply_id}"),
        ],
        [InlineKeyboardButton(text="🗑 Не отвечать", callback_data=f"reply:skip:{reply_id}")],
    ])


def receipt_approval_kb(reply_id: int) -> InlineKeyboardMarkup:
    """Кнопки под чеком: подтвердить/отклонить оплату."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Подтвердить оплату", callback_data=f"reply:send:{reply_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reply:skip:{reply_id}"),
        ],
        [InlineKeyboardButton(text="✏️ Ответить по-своему", callback_data=f"reply:edit:{reply_id}")],
    ])
