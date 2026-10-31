from __future__ import annotations

import io
import logging
import re

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

# Короткий нейтральный ack на чек (НЕ подтверждение оплаты).
RECEIPT_ACK = "Rahmat! Chekni oldik, tez orada tasdiqlaymiz 🙏"

# Вежливый ответ на вопрос НЕ по теме курсов: бот — помощница устоза, Насиба ответит сама.
ASSISTANT_DEFER = (
    "Men Nasiba ustozning yordamchisiman 🙂 Asosan kurslar bo'yicha yordam beraman. "
    "Bu savol bo'yicha ustozning o'zi sizga tez orada javob beradi 🙏"
)

# Ответ на чистое приветствие — бот представляется как помощница-секретарь Насибы.
GREETING_INTRO = (
    "Assalomu alaykum! 🤝 Men Nasiba Abdullayevaning yordamchi kotibasiman. "
    "Qo'limdan kelganicha sizga javob beraman. Kurslar bo'yicha nimaga qiziqsangiz, "
    "bemalol so'rang 🙏"
)

# Слова-приветствия (латиница + кириллица). Если сообщение состоит ТОЛЬКО из них — здороваемся.
_GREETING_WORDS = {
    "salom", "assalom", "assalomu", "assalomualaykum", "alaykum", "alekum", "aleykum",
    "vaalaykum", "vassalom", "salam", "hormang", "charchamang", "hayrli", "kun", "tong",
    "kech", "hi", "hello", "hey", "privet", "zdravstvuyte", "ustoz", "opa", "hurmatli",
    "салом", "ассалом", "ассалому", "алайкум", "алейкум", "ваалайкум", "салам",
    "привет", "здравствуйте", "здравствуй", "хормасиз", "хайрли", "кун", "тонг", "кеч",
    "устоз", "опа", "хурматли",
}


def _is_greeting(text: str) -> bool:
    """True, если сообщение — только приветствие (без вопроса). Тогда отвечаем без ИИ."""
    tokens = re.findall(r"[a-zA-Zа-яёА-ЯЁ'ʻ’]+", text.lower())
    if not tokens or len(tokens) > 4:
        return False
    return all(t.strip("'ʻ’") in _GREETING_WORDS for t in tokens)

_VERDICT = {
    ReceiptStatus.clean.value: "✅ TOZA — karta va summa mos keldi, dubl yo'q",
    ReceiptStatus.duplicate.value: "🚨 DUBL — bu chek allaqachon yuborilgan! Firibgarlik bo'lishi mumkin",
    ReceiptStatus.wrong_card.value: "⚠️ KARTA NOTO'G'RI — qabul qiluvchi sizning kartalaringizdan emas",
    ReceiptStatus.wrong_amount.value: "⚠️ SUMMA MOS EMAS — hech bir kurs narxiga teng emas (kam to'langan?)",
    ReceiptStatus.unreadable.value: "❓ CHEKKA O'XSHAMAYDI — o'qiy olmadim",
    ReceiptStatus.pending.value: "⏳ Tekshiruvda",
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
                "✅ Lichkangizga ulanish faol. Men aloqadaman — pedagoglarga javob beraman "
                "va cheklarni tekshiraman.",
            )
        else:
            await bot.send_message(
                settings.nasiba_chat_id,
                "⚠️ Lichkaga ulanish o'chirildi. Hozircha pedagoglarga javob bera olmayman.",
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
                    f"❗️ {_who(lead)} dan chek keldi, lekin o'qiy olmadim ({e!r}). O'zingiz qarang.",
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
                    "Bu kod sizga tegishli emas 🚫 Iltimos, o'zingiz to'lab, shaxsiy kod oling "
                    "yoki Nasiba bilan bog'laning."
                )
                await bot.send_message(
                    settings.nasiba_chat_id,
                    f"🚨 {ac.code} kodi boshqa pedagogga berilgan edi, uni {_who(lead)} yubordi. "
                    f"Kimdir o'z kodini bergan ko'rinadi (urinishlar: {ac.share_attempts}).",
                )
            elif course is None:
                await message.answer("Kirish faollashtirildi, lekin yo'nalish topilmadi — Nasiba siz bilan bog'lanadi.")
            else:
                await message.answer(await access.build_access_message(bot, course))
            await session.commit()
            return

        # 2b) Чистое приветствие — представляемся как помощница-секретарь (без траты ИИ).
        if _is_greeting(text):
            await message.answer(GREETING_INTRO)
            await session.commit()
            return

        knowledge = await build_knowledge(session)
        try:
            res = await ai.answer_customer(text, knowledge)
        except Exception as e:  # noqa: BLE001
            await bot.send_message(
                settings.nasiba_chat_id,
                f"❗️ {_who(lead)} dan savol: «{text[:300]}» — qayta ishlay olmadim ({e!r}). O'zingiz javob bering.",
            )
            await session.commit()
            return

        lead.lang = res.get("lang") or lead.lang
        if res.get("interested_course"):
            lead.interested_course = res["interested_course"]

        # Вопрос НЕ по теме курсов — вежливо отвечаем как помощница, зовём Насибу ответить самой.
        if res.get("out_of_scope"):
            await message.answer(ASSISTANT_DEFER)
            await bot.send_message(
                settings.nasiba_chat_id,
                f"🙋 {_who(lead)} dan kurslarga OID BO'LMAGAN savol:\n«{text[:400]}»\n\n"
                "Men bu mening mavzuyim emasligini va o'zingiz yozishingizni muloyim aytdim. "
                "Iltimos, unga javob bering 🙏",
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
                draft_text=draft or "(AI matn tuza olmadi — o'zingiz javob bering)",
                kind="manual",
            )
            session.add(reply)
            await session.flush()
            await bot.send_message(
                settings.nasiba_chat_id,
                f"🙋 {_who(lead)} dan savol sizni talab qiladi:\n\n«{text[:400]}»\n\n"
                f"Mening qoralamam:\n{reply.draft_text}",
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
    return f"{lead.name or 'pedagog'}{uname}"


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
    amount = f"{receipt.amount:,}".replace(",", " ") + " so'm" if receipt.amount else "—"
    uname = f"@{lead.username}" if lead.username else "—"
    return (
        f"💳 CHEK ARXIVGA\n"
        f"Pedagog: {lead.name or '—'}\n"
        f"Username: {uname}\n"
        f"Telegram ID: {lead.tg_user_id}\n"
        f"Xulosa: {_VERDICT.get(receipt.status, receipt.status)}\n"
        f"Summa: {amount}\n"
        f"Kurs: {receipt.course_key or '—'}\n"
        f"Karta: ***{receipt.card_last4 or '—'}\n"
        f"Tranzaksiya: {receipt.txn_id or '—'}\n"
        f"Qachon to'langan: {receipt.paid_at or '—'}\n"
        f"To'lovchi (chekda): {receipt.payer_name or '—'}"
    )


def _receipt_card(receipt, lead, draft: str) -> str:
    amount = f"{receipt.amount:,}".replace(",", " ") + " so'm" if receipt.amount else "—"
    course = f"\nYo'nalish (summa bo'yicha): {receipt.course_key}" if receipt.course_key else ""
    return (
        f"💳 {_who(lead)} dan chek\n\n"
        f"Xulosa: {_VERDICT.get(receipt.status, receipt.status)}\n"
        f"Summa: {amount}{course}\n"
        f"Karta: ***{receipt.card_last4 or '—'}\n"
        f"Tranzaksiya: {receipt.txn_id or '—'}\n"
        f"Qachon: {receipt.paid_at or '—'}\n"
        f"To'lovchi: {receipt.payer_name or '—'}\n\n"
        f"Tasdiqlasangiz — pedagogga yuboraman:\n«{draft}»"
    )


def _receipt_draft(status: str, lang: str) -> str:
    """Черновик ответа педагогу по итогу проверки (Насиба может изменить)."""
    if status == ReceiptStatus.clean.value:
        return "Rahmat! To'lovingiz qabul qilindi ✅ Hozir shaxsiy kirish kodingizni yuboraman 🙏"
    if status == ReceiptStatus.duplicate.value:
        return "Kechirasiz, bu chek allaqachon ishlatilgan. Iltimos, o'zingizning to'lov chekingizni yuboring."
    if status == ReceiptStatus.wrong_card.value:
        return "To'lov boshqa kartaga tushibdi. Iltimos, to'g'ri rekvizitlarga qayta to'lang."
    if status == ReceiptStatus.wrong_amount.value:
        return "To'lov summasi kurs narxiga to'g'ri kelmadi. Iltimos, summani tekshirib, to'liq to'lang yoki chekni qayta yuboring."
    return "Rahmat, chekni ko'rib chiqamiz."
