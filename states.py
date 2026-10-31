from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class EditReply(StatesGroup):
    """Насиба нажала «Изменить» — ждём её текст ответа педагогу."""
    waiting_text = State()


class AddCourse(StatesGroup):
    """Пошаговое добавление/правка курса."""
    waiting_key = State()
    waiting_name = State()
    waiting_price = State()
    waiting_start = State()
    waiting_group = State()
    waiting_channel = State()
    waiting_private = State()
    waiting_desc = State()
