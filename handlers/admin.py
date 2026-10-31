from __future__ import annotations

from datetime import date

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy import func, select

from config import settings
from db import SessionLocal
from models import Course, Lead, PendingReply, Receipt, ReceiptStatus, ReplyStatus
from services import (
    KEY_BUSINESS_CONNECTION,
    KEY_RECEIPTS_GROUP,
    KEY_REQUISITES,
    del_link,
    get_setting,
    list_links,
    set_link,
    set_setting,
)
from states import AddCourse

router = Router(name="admin")


def _is_admin(message: Message) -> bool:
    return bool(message.from_user and message.from_user.id in settings.admin_ids)


@router.message(Command("id", "whoami"))
async def cmd_id(message: Message) -> None:
    """Открыто всем — помогает узнать Telegram ID (для настройки NASIBA_CHAT_ID)."""
    await message.answer(f"Твой Telegram ID: `{message.from_user.id}`", parse_mode="Markdown")


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    if not _is_admin(message):
        # Если боту в НОРМАЛЬНЫЙ чат пишет педагог — вежливо направим в личку Насибы.
        await message.answer("Ассалому алейкум! Насибага ёзинг, мен ёрдам бераман 🙏")
        return
    await message.answer(
        "Привет! Я на связи 🤝\n\n"
        "Что я делаю на твоей личке:\n"
        "• отвечаю педагогам на вопросы про курсы;\n"
        "• принимаю чеки и проверяю их (дубли, чужие карты, сумма);\n"
        "• спорное и оплату показываю тебе на подтверждение.\n\n"
        "⚡️ Быстрая настройка курса Milliy (по одной команде):\n"
        "/setupmilliy — создать курс Milliy (начальные классы)\n"
        "/setprice milliy 300000 — цена\n"
        "/setchannel milliy https://t.me/... — открытый канал\n"
        "/setstart milliy 2026-11-15 — дата старта (секрет)\n"
        "/bindgroup milliy — привязать закрытую группу (внутри группы)\n\n"
        "Остальное:\n"
        "/courses — курсы (цены, даты)\n"
        "/addcourse — добавить курс вручную (мастер)\n"
        "/requisites — задать реквизиты для оплаты\n"
        "/bindreceipts — собирать все чеки в группу-архив (отправь внутри группы)\n"
        "/links — ссылки (канал, инстаграм) — бот даёт их по запросу\n"
        "/stats — статистика\n"
        "/id — узнать Telegram ID\n\n"
        "📋 Пошаговая инструкция с галочками (что готово, что осталось): /help"
    )


@router.message(Command("help", "setup", "guide"))
async def cmd_help(message: Message) -> None:
    """Живой чеклист настройки: показывает, что уже готово ✅ и что осталось ⬜️,
    с командой для каждого шага. Нетехническому человеку — идти сверху вниз."""
    if not _is_admin(message):
        return
    async with SessionLocal() as session:
        conn = await get_setting(session, KEY_BUSINESS_CONNECTION)
        requisites = await get_setting(session, KEY_REQUISITES)
        receipts_grp = await get_setting(session, KEY_RECEIPTS_GROUP)
        milliy = await _get_course(session, "milliy")

    def mark(ok) -> str:
        return "✅" if ok else "⬜️"

    has_course = milliy is not None
    lines = [
        "🧭 Инструкция по настройке — иди по шагам сверху вниз:",
        "",
        f"{mark(conn)} 1. Подключить бота к личке",
        "   Настройки Telegram → Telegram для бизнеса → Чат-боты → добавить бота,",
        "   право «Отвечать на сообщения».",
        "",
        f"{mark(requisites)} 2. Реквизиты для оплаты",
        "   /requisites 8600 1234 5678 9012, Насиба Х.",
        "",
        f"{mark(has_course)} 3. Создать курс Milliy",
        "   /setupmilliy",
        "",
        f"{mark(bool(milliy and milliy.price_sum))} 4. Задать цену курса",
        "   /setprice milliy 300000",
        "",
        f"{mark(bool(milliy and milliy.channel_url))} 5. Открытый канал (бот даёт всем)",
        "   /setchannel milliy https://t.me/...",
        "",
        f"{mark(bool(milliy and milliy.start_date))} 6. Дата старта (секрет) — или без срока",
        "   дата: /setstart milliy 2026-11-15   ·   постоянный набор: /setstart milliy -",
        "",
        f"{mark(bool(milliy and milliy.private_chat_id))} 7. Закрытая группа (одноразовые ссылки)",
        "   добавь меня админом в закрытую группу и отправь там: /bindgroup milliy",
        "",
        f"{mark(receipts_grp)} 8. Группа-архив чеков (по желанию)",
        "   создай приватную группу, добавь меня и отправь там: /bindreceipts",
        "",
        "Проверить курсы: /courses · Статистика: /stats · Это меню снова: /help",
    ]
    await message.answer("\n".join(lines))


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    if not _is_admin(message):
        return
    async with SessionLocal() as session:
        leads = await session.scalar(select(func.count(Lead.id)))
        pending = await session.scalar(
            select(func.count(PendingReply.id)).where(
                PendingReply.status == ReplyStatus.pending.value
            )
        )
        rows = await session.execute(
            select(Receipt.status, func.count(Receipt.id)).group_by(Receipt.status)
        )
        by_status = {s: c for s, c in rows.all()}
    lines = [
        "📊 Статистика",
        f"Педагогов написало: {leads or 0}",
        f"Ждут твоего решения: {pending or 0}",
        "",
        "Чеки:",
        f"• чистых: {by_status.get(ReceiptStatus.clean.value, 0)}",
        f"• подтверждено: {by_status.get(ReceiptStatus.approved.value, 0)}",
        f"• 🚨 дублей: {by_status.get(ReceiptStatus.duplicate.value, 0)}",
        f"• ⚠️ чужая карта: {by_status.get(ReceiptStatus.wrong_card.value, 0)}",
        f"• отклонено: {by_status.get(ReceiptStatus.rejected.value, 0)}",
    ]
    await message.answer("\n".join(lines))


@router.message(Command("requisites"))
async def cmd_requisites(message: Message, command: CommandObject) -> None:
    if not _is_admin(message):
        return
    async with SessionLocal() as session:
        if not command.args:
            cur = await get_setting(session, KEY_REQUISITES)
            await message.answer(
                "Текущие реквизиты для педагогов:\n\n"
                + (cur or "(не заданы)")
                + "\n\nЧтобы изменить: /requisites <текст с картой и именем>"
            )
            return
        await set_setting(session, KEY_REQUISITES, command.args.strip())
        await session.commit()
    await message.answer("✅ Реквизиты обновлены. Теперь я буду давать их педагогам.")


@router.message(Command("links"))
async def cmd_links(message: Message) -> None:
    if not _is_admin(message):
        return
    async with SessionLocal() as session:
        links = await list_links(session)
    if not links:
        await message.answer(
            "Ссылок пока нет.\n"
            "Добавить: /addlink <имя> <ссылка>\n"
            "Напр.: /addlink kanal https://t.me/nasiba"
        )
        return
    out = ["🔗 Ссылки (бот даёт их педагогам по запросу):"]
    for name, url in links.items():
        out.append(f"• {name}: {url}")
    out.append("\nДобавить: /addlink <имя> <ссылка>\nУдалить: /dellink <имя>")
    await message.answer("\n".join(out))


@router.message(Command("addlink"))
async def cmd_addlink(message: Message, command: CommandObject) -> None:
    if not _is_admin(message):
        return
    parts = (command.args or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Формат: /addlink <имя> <ссылка>\nНапр.: /addlink kanal https://t.me/nasiba")
        return
    name, url = parts[0], parts[1].strip()
    async with SessionLocal() as session:
        await set_link(session, name, url)
        await session.commit()
    await message.answer(f"✅ Ссылка «{name}» сохранена. Теперь бот даёт её, когда педагог просит.")


@router.message(Command("dellink"))
async def cmd_dellink(message: Message, command: CommandObject) -> None:
    if not _is_admin(message):
        return
    name = (command.args or "").strip()
    if not name:
        await message.answer("Формат: /dellink <имя>")
        return
    async with SessionLocal() as session:
        ok = await del_link(session, name)
        await session.commit()
    await message.answer("✅ Удалил." if ok else "Такой ссылки нет.")


@router.message(Command("bindgroup"))
async def cmd_bindgroup(message: Message, command: CommandObject) -> None:
    """Отправляется ВНУТРИ закрытой группы (где бот — админ). Привязывает группу к курсу,
    чтобы бот выдавал одноразовые инвайт-ссылки именно в неё."""
    if not _is_admin(message):
        return
    if message.chat.type not in ("group", "supergroup"):
        await message.answer(
            "Эту команду надо отправить ВНУТРИ закрытой группы (где я админ): "
            "/bindgroup <ключ_курса>, напр. /bindgroup milliy"
        )
        return
    key = (command.args or "").strip().lower()
    if not key:
        await message.answer("Укажи ключ курса: /bindgroup milliy")
        return
    async with SessionLocal() as session:
        c = (await session.execute(select(Course).where(Course.key == key))).scalar_one_or_none()
        if c is None:
            await message.answer(f"Курс [{key}] не найден. Сначала добавь его: /addcourse")
            return
        c.private_chat_id = message.chat.id
        await session.commit()
        name = c.name
    await message.answer(
        f"✅ Привязал эту группу к курсу «{name}». Теперь после оплаты я выдаю сюда "
        f"одноразовые ссылки. Убедись, что я админ с правом «Пригласить по ссылке»."
    )


@router.message(Command("bindreceipts"))
async def cmd_bindreceipts(message: Message) -> None:
    """Отправляется ВНУТРИ группы-архива (бот должен быть в ней). Запоминает группу,
    куда бот складывает все присланные чеки + данные педагога."""
    if not _is_admin(message):
        return
    if message.chat.type not in ("group", "supergroup"):
        await message.answer(
            "Эту команду надо отправить ВНУТРИ группы, куда собирать чеки "
            "(я должен быть в этой группе): просто /bindreceipts"
        )
        return
    async with SessionLocal() as session:
        await set_setting(session, KEY_RECEIPTS_GROUP, str(message.chat.id))
        await session.commit()
    await message.answer(
        "✅ Готово. Теперь каждый присланный чек с данными педагога я складываю сюда — "
        "будет визуальный архив (база).\n\n"
        "⚠️ Держи эту группу приватной, с минимумом людей: тут платёжные данные."
    )


@router.message(Command("courses"))
async def cmd_courses(message: Message) -> None:
    if not _is_admin(message):
        return
    async with SessionLocal() as session:
        rows = (await session.execute(select(Course))).scalars().all()
    if not rows:
        await message.answer("Курсов пока нет. Добавь: /addcourse")
        return
    out = ["📚 Курсы:"]
    for c in rows:
        price = f"{c.price_sum:,}".replace(",", " ") + " сум" if c.price_sum else "цена —"
        mark = "" if c.is_active else " (скрыт)"
        line = f"• [{c.key}] {c.name} — {price}{mark}"
        if c.channel_url:
            line += f"\n   📢 открытый канал (даю всем): {c.channel_url}"
        start = c.start_date.isoformat() if c.start_date else "—"
        if c.private_chat_id:
            line += f"\n   🔒 закрытая группа: одноразовые ссылки ВКЛ · старт {start}"
        elif c.private_access or c.start_date:
            line += f"\n   🔒 закрытая группа (после оплаты): {c.private_access or '—'} · старт {start}"
        out.append(line)
    out.append("\nДобавить/обновить: /addcourse")
    await message.answer("\n".join(out))


# --- быстрая настройка курса (по одной команде, без мастера) ---

MILLIY_NAME = "Milliy sertifikat — boshlang'ich sinf o'qituvchilari"
MILLIY_DESC = (
    "Подготовка учителей начальных классов к экзамену Milliy sertifikat. "
    "Один курс, охватывает все 3 блока экзамена: "
    "1) Она тили ва o'qish savodxonligi (методика родного языка и чтения); "
    "2) Математика ва методика; "
    "3) Педагогик махорат (tabiiy fanlar + педагогика). "
    "Формат экзамена: 90 вопросов. Для boshlang'ich sinf o'qituvchilari."
)


async def _get_course(session, key: str):
    return (
        await session.execute(select(Course).where(Course.key == key))
    ).scalar_one_or_none()


@router.message(Command("setupmilliy"))
async def cmd_setupmilliy(message: Message, command: CommandObject) -> None:
    """Создаёт/обновляет единственный курс Milliy с готовым названием и описанием.
    Цену можно сразу передать: /setupmilliy 300000."""
    if not _is_admin(message):
        return
    digits = "".join(ch for ch in (command.args or "") if ch.isdigit())
    price = int(digits) if digits else None
    async with SessionLocal() as session:
        c = await _get_course(session, "milliy")
        if c is None:
            c = Course(key="milliy", name=MILLIY_NAME, description=MILLIY_DESC, is_active=True)
            session.add(c)
        else:
            c.name = MILLIY_NAME
            c.description = MILLIY_DESC
            c.is_active = True
        if price is not None:
            c.price_sum = price
        await session.commit()
        cur_price = c.price_sum
    price_line = f"{cur_price:,}".replace(",", " ") + " сум" if cur_price else "НЕ задана ⚠️"
    await message.answer(
        "✅ Курс «Milliy сертификат (начальные классы)» готов.\n\n"
        f"Цена: {price_line}\n\n"
        "Осталось заполнить (по одной команде):\n"
        "• /setprice milliy 300000 — цена (нужна для сверки суммы чека)\n"
        "• /setchannel milliy https://t.me/... — открытый канал (бот даёт всем)\n"
        "• /setstart milliy 2026-11-15 — дата старта (секрет, бот даст после оплаты)\n"
        "• /bindgroup milliy — отправь ВНУТРИ закрытой группы (я там админ)\n\n"
        "Проверить: /courses"
    )


@router.message(Command("setprice"))
async def cmd_setprice(message: Message, command: CommandObject) -> None:
    if not _is_admin(message):
        return
    parts = (command.args or "").split()
    if len(parts) < 2:
        await message.answer("Формат: /setprice <ключ> <сумма>\nНапр.: /setprice milliy 300000")
        return
    key = parts[0].lower()
    digits = "".join(ch for ch in parts[1] if ch.isdigit())
    if not digits:
        await message.answer("Сумма должна быть числом. Напр.: /setprice milliy 300000")
        return
    async with SessionLocal() as session:
        c = await _get_course(session, key)
        if c is None:
            await message.answer(f"Курс [{key}] не найден. Сначала /setupmilliy или /addcourse.")
            return
        c.price_sum = int(digits)
        await session.commit()
        name = c.name
    pretty = f"{int(digits):,}".replace(",", " ")
    await message.answer(f"✅ Цена курса «{name}»: {pretty} сум. Теперь сверяю сумму чека по ней.")


@router.message(Command("setchannel"))
async def cmd_setchannel(message: Message, command: CommandObject) -> None:
    if not _is_admin(message):
        return
    parts = (command.args or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "Формат: /setchannel <ключ> <ссылка>\nНапр.: /setchannel milliy https://t.me/nasiba"
        )
        return
    key, url = parts[0].lower(), parts[1].strip()
    async with SessionLocal() as session:
        c = await _get_course(session, key)
        if c is None:
            await message.answer(f"Курс [{key}] не найден. Сначала /setupmilliy или /addcourse.")
            return
        c.channel_url = url
        await session.commit()
        name = c.name
    await message.answer(f"✅ Открытый канал курса «{name}» задан. Бот даёт его всем для прогрева.")


@router.message(Command("setstart"))
async def cmd_setstart(message: Message, command: CommandObject) -> None:
    if not _is_admin(message):
        return
    parts = (command.args or "").split()
    if len(parts) < 2:
        await message.answer(
            "Формат: /setstart <ключ> <ГГГГ-ММ-ДД>\nНапр.: /setstart milliy 2026-11-15\n"
            "Или /setstart milliy - — сделать курс БЕЗ срока (постоянный набор)."
        )
        return
    key = parts[0].lower()
    clear = parts[1].lower() in {"-", "нет", "0", "yoq", "yo'q", "doimiy", "бессрочно"}
    start = None
    if not clear:
        try:
            start = date.fromisoformat(parts[1])
        except ValueError:
            await message.answer(
                "Дата в формате ГГГГ-ММ-ДД, или «-» для бессрочного курса.\n"
                "Напр.: /setstart milliy 2026-11-15"
            )
            return
    async with SessionLocal() as session:
        c = await _get_course(session, key)
        if c is None:
            await message.answer(f"Курс [{key}] не найден. Сначала /setupmilliy или /addcourse.")
            return
        c.start_date = start
        await session.commit()
        name = c.name
    if start:
        await message.answer(
            f"✅ Дата старта «{name}»: {start.isoformat()}. Это СЕКРЕТ — бот назовёт её "
            "только после подтверждённой оплаты."
        )
    else:
        await message.answer(
            f"✅ Курс «{name}» — БЕЗ срока (постоянный набор). Бот будет говорить, что "
            "присоединиться можно в любой момент, и продолжит рекомендовать курс новым педагогам."
        )


# --- добавление курса пошагово ---

@router.message(Command("addcourse"))
async def cmd_addcourse(message: Message, state: FSMContext) -> None:
    if not _is_admin(message):
        return
    await state.set_state(AddCourse.waiting_key)
    await message.answer("Короткий ключ курса (латиницей, напр. milliy):")


@router.message(AddCourse.waiting_key)
async def ac_key(message: Message, state: FSMContext) -> None:
    await state.update_data(key=message.text.strip().lower())
    await state.set_state(AddCourse.waiting_name)
    await message.answer("Название курса:")


@router.message(AddCourse.waiting_name)
async def ac_name(message: Message, state: FSMContext) -> None:
    await state.update_data(name=message.text.strip())
    await state.set_state(AddCourse.waiting_price)
    await message.answer("Цена в сумах (только число, или 0 если не указывать):")


@router.message(AddCourse.waiting_price)
async def ac_price(message: Message, state: FSMContext) -> None:
    digits = "".join(ch for ch in (message.text or "") if ch.isdigit())
    await state.update_data(price=int(digits) if digits else 0)
    await state.set_state(AddCourse.waiting_start)
    await message.answer(
        "Дата старта ЗАКРЫТОЙ группы в формате ГГГГ-ММ-ДД (секрет — бот назовёт её "
        "только после оплаты), или «-»:"
    )


@router.message(AddCourse.waiting_start)
async def ac_start(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    start = None
    if raw and raw != "-":
        try:
            start = date.fromisoformat(raw)
        except ValueError:
            await message.answer("Не похоже на дату. Повтори ГГГГ-ММ-ДД или «-»:")
            return
    await state.update_data(start=start.isoformat() if start else None)
    await state.set_state(AddCourse.waiting_group)
    await message.answer("Группа / тоифа (или «-»):")


@router.message(AddCourse.waiting_group)
async def ac_group(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    await state.update_data(group=None if raw == "-" else raw)
    await state.set_state(AddCourse.waiting_channel)
    await message.answer(
        "Ссылка на ОТКРЫТЫЙ канал направления (его бот даёт всем для прогрева), или «-»:"
    )


@router.message(AddCourse.waiting_channel)
async def ac_channel(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    await state.update_data(channel=None if raw == "-" else raw)
    await state.set_state(AddCourse.waiting_private)
    await message.answer(
        "Ссылка на ЗАКРЫТУЮ группу (бот даёт её ТОЛЬКО после подтверждённой оплаты), или «-»:"
    )


@router.message(AddCourse.waiting_private)
async def ac_private(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    await state.update_data(private=None if raw == "-" else raw)
    await state.set_state(AddCourse.waiting_desc)
    await message.answer("Короткое описание (что входит, кому, длительность) или «-»:")


@router.message(AddCourse.waiting_desc)
async def ac_desc(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    raw = (message.text or "").strip()
    desc = "" if raw == "-" else raw
    start = date.fromisoformat(data["start"]) if data.get("start") else None
    async with SessionLocal() as session:
        existing = (
            await session.execute(select(Course).where(Course.key == data["key"]))
        ).scalar_one_or_none()
        if existing:
            existing.name = data["name"]
            existing.price_sum = data["price"]
            existing.start_date = start
            existing.group_label = data.get("group")
            existing.channel_url = data.get("channel")
            existing.private_access = data.get("private")
            existing.description = desc
            existing.is_active = True
        else:
            session.add(Course(
                key=data["key"], name=data["name"], price_sum=data["price"],
                start_date=start, group_label=data.get("group"),
                channel_url=data.get("channel"), private_access=data.get("private"),
                description=desc,
            ))
        await session.commit()
    await message.answer(f"✅ Курс «{data['name']}» сохранён. Проверь: /courses")
