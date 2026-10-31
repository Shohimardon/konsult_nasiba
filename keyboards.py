from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def reply_approval_kb(reply_id: int) -> InlineKeyboardMarkup:
    """Кнопки под черновиком ответа педагогу (гибридный режим)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Yuborish", callback_data=f"reply:send:{reply_id}"),
            InlineKeyboardButton(text="✏️ O'zgartirish", callback_data=f"reply:edit:{reply_id}"),
        ],
        [InlineKeyboardButton(text="🗑 Javob bermaslik", callback_data=f"reply:skip:{reply_id}")],
    ])


def receipt_approval_kb(reply_id: int) -> InlineKeyboardMarkup:
    """Кнопки под чеком: подтвердить/отклонить оплату."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ To'lovni tasdiqlash", callback_data=f"reply:send:{reply_id}"),
            InlineKeyboardButton(text="❌ Rad etish", callback_data=f"reply:skip:{reply_id}"),
        ],
        [InlineKeyboardButton(text="✏️ O'zim javob yozaman", callback_data=f"reply:edit:{reply_id}")],
    ])
