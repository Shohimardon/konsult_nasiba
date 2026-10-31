from __future__ import annotations

import io
import logging

from aiogram import Bot, F, Router
from aiogram.types import BufferedInputFile, BusinessConnection, Message

import access
import ai
import fraud
from config import settings
from keyboards import receipt_approval_kb, reply_approval_kb
from models import PendingReply, ReceiptStatus
from services import (
    KEY_BUSINESS_CONNECTION,
    KEY_RECEIPTS_GROUP,
    build_knowledge,
    get_setting,
    set_setting,
    upsert_lead,
)
from db import SessionLocal

logger = logging.getLogger(__name__)

router = Router(name="business")

# Короткий нейтральный ack на чек (НЕ подтверждение оплаты) на 3 языках.
RECEIPT_ACK = "Rahmat! Chekni oldik, tez orada tasdiqlaymiz 🙏\n(Чек получили, скоро подтвердим)"

# Вежливый ответ на вопрос НЕ по теме курсов: бот — помощница устоза, Насиба ответит сама.
ASSISTANT_DEFER = {
    "uz_cyrl": "Мен Насиба устознинг ёрдамчисиман 🙂 Асосан курслар бўйича ёрдам бераман. "
               "Бу савол бўйича устознинг ўзи сизга тез орада жавоб беради 🙏",
    "uz_latn": "Men Nasiba ustozning yordamchisiman 🙂 Asosan kurslar bo'yicha yordam beraman. "
               "Bu savol bo'yicha ustozning o'zi sizga tez orada javob beradi 🙏",
    "ru": "Я помощница устоза Насибы 🙂 Помогаю в основном по курсам. На этот вопрос Насиба "
          "ответит вам сама — скоро напишет 🙏",
}


def _assistant_defer(lang: str) -> str:
    return ASSISTANT_DEFER.get(lang, ASSISTANT_DEFER["uz_cyrl"])

_VERDICT = {
    ReceiptStatus.clean.value: "✅ ЧИСТО — карта и сумма совпали, дублей нет",
    ReceiptStatus.duplicate.value: "🚨 ДУБЛЬ — такой чек уже присылали! Возможно жульничество",
    ReceiptStatus.wrong_card.value: "⚠️ НЕ ТА КАРТА — получатель не из твоих карт",
    ReceiptStatus.wrong_amount.value: "⚠️ СУММА НЕ СОВПАЛА — не равна цене ни одного курса (недоплата?)",
    ReceiptStatus.unreadable.value: "❓ НЕ ПОХОЖЕ НА ЧЕК — не смог распознать",
    ReceiptStatus.pending.value: "⏳ На проверке",
}


@router.business_connection()
async def on_business_connection(event: BusinessConnection, bot: Bot) -> None:
    """Насиба подключила/отключила бота к своей личке через Telegram Business."""
    if event.user.id != settings.nasiba_chat_id:
        # Кто-то чужой пытается подключить бота к своему аккаунту — не обслуживаем.
        return
    async with SessionLocal() as session:
        if event.is_enabled:
            await set_setting(session, KEY_BUSINESS_CONNECTION, event.id)
            await session.commit()
            await bot.send_message(
                settings.nasiba_chat_id,
                "✅ Подключение к твоей личке активно. Я на связи — буду отвечать "
                "педагогам и проверять чеки.",
            )
        else:
            await bot.send_message(
                settings.nasiba_chat_id,
                "⚠️ Подключение к личке отключено. Пока не смогу отвечать педагогам.",
            )


@router.business_message()
async def on_business_message(message: Message, bot: Bot) -> None:
    """Новое сообщение в личке Насибы от педагога."""
    # Сообщения, которые пишет сама Насиба — пропускаем (это её исходящие).
    if message.from_user and message.from_user.id == settings.nasiba_chat_id:
        return
    bcid = message.business_connection_id

    async with SessionLocal() as session:
        lead = await upsert_lead(session, message.from_user)

        # 1) Чек об оплате (фото или файл-картинка/PDF) -> анти-фрод -> на одобрение.
        file_bytes, media_type, file_uid = await _extract_payment_file(message, bot)
        if file_bytes is not None:
            await message.answer(RECEIPT_ACK)  # нейтральный ack, не подтверждение
            try:
                parsed = await ai.read_receipt(file_bytes, media_type)
            except Exception as e:  # noqa: BLE001
                await bot.send_message(
                    settings.nasiba_chat_id,
                    f"❗️ Пришёл чек от {_who(lead)}, но не смог его прочитать ({e!r}). Глянь сама.",
                )
                await session.commit()
                return
            receipt = await fraud.check_receipt(
                session, lead, parsed, fraud.file_sha256(file_bytes), file_uid
            )
            draft = _receipt_draft(receipt.status, lead.lang)
            reply = PendingReply(
                lead_id=lead.id,
                customer_chat_id=message.chat.id,
                business_connection_id=bcid,
                draft_text=draft,
                kind="receipt",
                receipt_id=receipt.id,
            )
            session.add(reply)
            await session.flush()
            await bot.send_message(
                settings.nasiba_chat_id,
                _receipt_card(receipt, lead, draft),
                reply_markup=receipt_approval_kb(reply.id),
            )
            # Архив: складываем сам чек + данные педагога в группу-«базу» (если привязана).
            await _archive_receipt(bot, session, receipt, lead, file_bytes, media_type)
            await session.commit()
            return

        # 2) Текст или голос -> вопрос по курсам.
        text = await _extract_text(message, bot)
        if not text:
            await session.commit()
            return

        # 2a) Это код доступа? Проверяем владельца (защита от передачи кода).
        ac = await access.find_code(session, text)
        if ac is not None:
            status, course = await access.redeem(session, ac, message.from_user.id)
            if status == "foreign":
                await message.answer(
                    "Бу код сизга тегишли эмас 🚫 Илтимос, ўзингиз тўлаб, шахсий код олинг "
                    "ёки Насиба билан боғланинг."
                )
                await bot.send_message(
                    settings.nasiba_chat_id,
                    f"🚨 Код {ac.code} выдан другому педагогу, а его прислал {_who(lead)}. "
                    f"Похоже, кто-то передал свой код (попыток: {ac.share_attempts}).",
                )
            elif course is None:
                await message.answer("Доступ активирован, но направление не найдено — Насиба свяжется с вами.")
            else:
                await message.answer(await access.build_access_message(bot, course))
            await session.commit()
            return

        knowledge = await build_knowledge(session)
        try:
            res = await ai.answer_customer(text, knowledge)
        except Exception as e:  # noqa: BLE001
            await bot.send_message(
                settings.nasiba_chat_id,
                f"❗️ Вопрос от {_who(lead)}: «{text[:300]}» — не смог обработать ({e!r}). Ответь сама.",
            )
            await session.commit()
            return

        lead.lang = res.get("lang") or lead.lang
        if res.get("interested_course"):
            lead.interested_course = res["interested_course"]

        # Вопрос НЕ по теме курсов — вежливо отвечаем как помощница, зовём Насибу ответить самой.
        if res.get("out_of_scope"):
            await message.answer(_assistant_defer(lead.lang))
            await bot.send_message(
                settings.nasiba_chat_id,
                f"🙋 Вопрос НЕ по курсам от {_who(lead)}:\n«{text[:400]}»\n\n"
                "Я вежливо ответил, что это не моя тема и что ты напишешь сама. "
                "Ответь ей, пожалуйста 🙏",
            )
            await session.commit()
            return

        draft = (res.get("reply") or "").strip()

        if res.get("needs_human") or not draft:
            # Деликатное/спорное — готовим черновик, но решает Насиба.
            reply = PendingReply(
                lead_id=lead.id,
                customer_chat_id=message.chat.id,
                business_connection_id=bcid,
                draft_text=draft or "(ИИ не смог сформулировать — ответь сама)",
                kind="manual",
            )
            session.add(reply)
            await session.flush()
            await bot.send_message(
                settings.nasiba_chat_id,
                f"🙋 Вопрос от {_who(lead)} требует тебя:\n\n«{text[:400]}»\n\n"
                f"Мой черновик:\n{reply.draft_text}",
                reply_markup=reply_approval_kb(reply.id),
            )
            await session.commit()
            return

        # Простой вопрос — отвечаем сами.
        await message.answer(draft)
        await session.commit()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

async def _extract_payment_file(message: Message, bot: Bot):
    """Возвращает (bytes, media_type, file_unique_id) если это чек-картинка/PDF, иначе (None, None, None)."""
    if message.photo:
        photo = message.photo[-1]
        return await _download(bot, photo), "image/jpeg", photo.file_unique_id
    doc = message.document
    if doc and doc.mime_type:
        if doc.mime_type == "application/pdf":
            return await _download(bot, doc), "application/pdf", doc.file_unique_id
        if doc.mime_type.startswith("image/"):
            return await _download(bot, doc), doc.mime_type, doc.file_unique_id
    return None, None, None


async def _download(bot: Bot, obj) -> bytes:
    bio = io.BytesIO()
    await bot.download(obj, destination=bio)
    return bio.getvalue()


async def _extract_text(message: Message, bot: Bot) -> str:
    if message.text:
        return message.text.strip()
    if message.caption:
        return message.caption.strip()
    if message.voice and settings.elevenlabs_api_key:
        bio = io.BytesIO()
        await bot.download(message.voice, destination=bio)
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            f.write(bio.getvalue())
            path = f.name
        try:
            return (await ai.transcribe_voice(path)).strip()
        finally:
            os.unlink(path)
    return ""


def _who(lead) -> str:
    uname = f" (@{lead.username})" if lead.username else ""
    return f"{lead.name or 'педагог'}{uname}"


async def _archive_receipt(bot, session, receipt, lead, file_bytes: bytes, media_type: str) -> None:
    """Кладёт сам файл чека + карточку с данными педагога в группу-архив (база).
    Если группа не привязана (/bindreceipts) — тихо пропускаем."""
    group = await get_setting(session, KEY_RECEIPTS_GROUP)
    if not group:
        return
    caption = _archive_caption(receipt, lead)
    try:
        chat_id = int(group)
        if media_type == "application/pdf":
            file = BufferedInputFile(file_bytes, filename="chek.pdf")
            await bot.send_document(chat_id, file, caption=caption)
        else:
            file = BufferedInputFile(file_bytes, filename="chek.jpg")
            await bot.send_photo(chat_id, file, caption=caption)
    except Exception as e:  # noqa: BLE001 — архив не должен ломать основной поток
        logger.warning("Не смог отправить чек в группу-архив: %r", e)


def _archive_caption(receipt, lead) -> str:
    amount = f"{receipt.amount:,}".replace(",", " ") + " сум" if receipt.amount else "—"
    uname = f"@{lead.username}" if lead.username else "—"
    return (
        f"💳 ЧЕК В АРХИВ\n"
        f"Педагог: {lead.name or '—'}\n"
        f"Username: {uname}\n"
        f"Telegram ID: {lead.tg_user_id}\n"
        f"Вердикт: {_VERDICT.get(receipt.status, receipt.status)}\n"
        f"Сумма: {amount}\n"
        f"Курс: {receipt.course_key or '—'}\n"
        f"Карта: ***{receipt.card_last4 or '—'}\n"
        f"Транзакция: {receipt.txn_id or '—'}\n"
        f"Когда платил: {receipt.paid_at or '—'}\n"
        f"Плательщик (в чеке): {receipt.payer_name or '—'}"
    )


def _receipt_card(receipt, lead, draft: str) -> str:
    amount = f"{receipt.amount:,}".replace(",", " ") + " сум" if receipt.amount else "—"
    course = f"\nНаправление (по сумме): {receipt.course_key}" if receipt.course_key else ""
    return (
        f"💳 Чек от {_who(lead)}\n\n"
        f"Вердикт: {_VERDICT.get(receipt.status, receipt.status)}\n"
        f"Сумма: {amount}{course}\n"
        f"Карта: ***{receipt.card_last4 or '—'}\n"
        f"Транзакция: {receipt.txn_id or '—'}\n"
        f"Когда: {receipt.paid_at or '—'}\n"
        f"Плательщик: {receipt.payer_name or '—'}\n\n"
        f"Если подтвердишь — отправлю педагогу:\n«{draft}»"
    )


def _receipt_draft(status: str, lang: str) -> str:
    """Черновик ответа педагогу по итогу проверки (Насиба может изменить)."""
    if status == ReceiptStatus.clean.value:
        return "Раҳмат! Тўловингиз қабул қилинди ✅ Ҳозир шахсий кирик кодингизни юбораман 🙏"
    if status == ReceiptStatus.duplicate.value:
        return "Кечирасиз, бу чек аллақачон ишлатилган. Илтимос, ўзингизнинг тўловингиз чекини юборинг."
    if status == ReceiptStatus.wrong_card.value:
        return "Тўлов бошқа картага кетибди. Илтимос, тўғри реквизитларга қайта тўланг."
    if status == ReceiptStatus.wrong_amount.value:
        return "Тўлов суммаси курс нархига тўғри келмади. Илтимос, суммани текшириб, тўлиқ тўланг ёки чекни қайта юборинг."
    return "Раҳмат, чекни кўриб чиқамиз."
