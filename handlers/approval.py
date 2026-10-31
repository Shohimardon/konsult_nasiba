from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from sqlalchemy import select

import access
from config import settings
from db import SessionLocal
from models import Course, Lead, PendingReply, Receipt, ReceiptStatus, ReplyStatus
from states import EditReply

router = Router(name="approval")


@router.callback_query(F.data.startswith("reply:"))
async def on_reply_action(cb: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    if cb.from_user.id != settings.nasiba_chat_id:
        await cb.answer("Bu sizning tugmangiz emas 🙂", show_alert=True)
        return
    _, action, raw_id = cb.data.split(":", 2)
    reply_id = int(raw_id)

    async with SessionLocal() as session:
        reply = await session.get(PendingReply, reply_id)
        if reply is None or reply.status != ReplyStatus.pending.value:
            await cb.answer("Allaqachon ko'rib chiqilgan", show_alert=False)
            return

        if action == "send":
            await _deliver(bot, reply)
            reply.status = ReplyStatus.sent.value
            note = await _release_private_access(bot, session, reply)
            await _mark_receipt(session, reply, approved=True)
            await session.commit()
            await cb.message.edit_text(cb.message.text + "\n\n— ✅ Pedagogga yuborildi" + note)
            await cb.answer("Yuborildi")
            return

        if action == "skip":
            reply.status = ReplyStatus.rejected.value
            await _mark_receipt(session, reply, approved=False)
            await session.commit()
            await cb.message.edit_text(cb.message.text + "\n\n— 🗑 O'tkazib yuborildi (pedagogga yuborilmadi)")
            await cb.answer("O'tkazib yuborildi")
            return

        if action == "edit":
            await state.set_state(EditReply.waiting_text)
            await state.update_data(reply_id=reply_id)
            await cb.message.answer("✏️ Javob matningizni yozing — men uni pedagogga yuboraman.")
            await cb.answer()
            return


@router.message(EditReply.waiting_text)
async def on_edited_text(message: Message, bot: Bot, state: FSMContext) -> None:
    data = await state.get_data()
    reply_id = data.get("reply_id")
    await state.clear()
    if not reply_id or not message.text:
        await message.answer("Bekor qilindi — matn bo'sh.")
        return
    async with SessionLocal() as session:
        reply = await session.get(PendingReply, reply_id)
        if reply is None or reply.status != ReplyStatus.pending.value:
            await message.answer("Bu javob allaqachon ko'rib chiqilgan.")
            return
        reply.draft_text = message.text
        await _deliver(bot, reply)
        reply.status = ReplyStatus.sent.value
        note = await _release_private_access(bot, session, reply)
        await _mark_receipt(session, reply, approved=True)
        await session.commit()
    await message.answer("✅ Matningiz pedagogga yuborildi." + note)


async def _deliver(bot: Bot, reply: PendingReply) -> None:
    """Отправляет текст педагогу от имени Насибы через Business-подключение."""
    await bot.send_message(
        chat_id=reply.customer_chat_id,
        text=reply.draft_text,
        business_connection_id=reply.business_connection_id,
    )


async def _mark_receipt(session, reply: PendingReply, approved: bool) -> None:
    if not reply.receipt_id:
        return
    receipt = await session.get(Receipt, reply.receipt_id)
    if receipt is None:
        return
    receipt.status = ReceiptStatus.approved.value if approved else ReceiptStatus.rejected.value


async def _release_private_access(bot: Bot, session, reply: PendingReply) -> str:
    """После подтверждения ЧИСТОЙ оплаты выдаёт педагогу закрытую группу + дату старта.

    Возвращает короткую пометку для Насибы. Закрытый доступ НЕ выдаётся, если чек не
    был «чистым» (дубль/чужая карта/не чек) — тогда возвращает пустую строку.
    """
    if reply.kind != "receipt" or not reply.receipt_id:
        return ""
    receipt = await session.get(Receipt, reply.receipt_id)
    if receipt is None or receipt.status != ReceiptStatus.clean.value:
        return ""

    # Направление: сперва по сумме чека (course_key), иначе по интересу/единственному курсу.
    course = None
    if receipt.course_key:
        course = (
            await session.execute(select(Course).where(Course.key == receipt.course_key))
        ).scalar_one_or_none()
    if course is None:
        course = await _guess_course(session, reply.lead_id)
    if course is None:
        await bot.send_message(
            settings.nasiba_chat_id,
            "⚠️ To'lov toza, lekin yo'nalish avtomatik aniqlanmadi. "
            "Yopiq guruh havolasini pedagogga o'zingiz yuboring (yoki yo'nalishni aniqlang).",
        )
        return "\n— ⚠️ yo'nalish noaniq, yopiq havolani qo'lda yuboring"
    if not course.private_access and not course.private_chat_id and not course.start_date:
        return "\n— (yo'nalishga yopiq guruh biriktirilmagan)"

    # Выдаём не ссылку напрямую, а персональный код — его нельзя передать другому.
    lead = await session.get(Lead, reply.lead_id)
    ac = await access.issue_code(session, lead, course.key, reply.receipt_id)
    await bot.send_message(
        chat_id=reply.customer_chat_id,
        text=(
            f"✅ To'lovingiz tasdiqlandi!\n"
            f"Shaxsiy kirish kodingiz: {ac.code}\n"
            f"Ushbu kodni shu yerga yuboring — yopiq guruh havolasini olasiz.\n"
            f"⚠️ Kod faqat sizga tegishli, boshqalarga bermang."
        ),
        business_connection_id=reply.business_connection_id,
    )
    return f"\n— 🔐 kirish kodi berildi {ac.code} ({course.name})"


async def _guess_course(session, lead_id: int) -> Course | None:
    """Пытается понять направление педагога: по интересу из переписки, иначе — если
    активное направление ровно одно. При неоднозначности возвращает None (не гадаем)."""
    lead = await session.get(Lead, lead_id)
    if lead and lead.interested_course:
        c = (
            await session.execute(select(Course).where(Course.key == lead.interested_course))
        ).scalar_one_or_none()
        if c:
            return c
    rows = (
        await session.execute(select(Course).where(Course.is_active.is_(True)))
    ).scalars().all()
    return rows[0] if len(rows) == 1 else None
