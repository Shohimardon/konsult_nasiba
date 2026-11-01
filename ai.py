from __future__ import annotations

import base64
import json
import sys

import httpx
from anthropic import AsyncAnthropic

from config import settings

_anthropic = AsyncAnthropic(api_key=settings.anthropic_api_key)

_ELEVEN_STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
_GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


# ---------------------------------------------------------------------------
# 1. Ответ педагогу по вопросу о курсах
# ---------------------------------------------------------------------------

CHAT_SYSTEM = (
    "Ты — вежливый ассистент Насибы, узбекского педагога-блогера. Ты отвечаешь людям, "
    "которые пишут Насибе в личку с вопросами про её курсы (подготовка к Milliy sertifikat "
    "и др.). Пиши от имени Насибы (от первого лица, тепло, коротко, как живой человек, без "
    "канцелярита). Отвечай НА ТОМ ЖЕ ЯЗЫКЕ, на котором написал человек (узбекский латиница, "
    "узбекский кириллица или русский). Используй ТОЛЬКО факты из блока ЗНАНИЯ ниже — ничего "
    "не выдумывай. В ЗНАНИЯХ может быть раздел ЧАСТЫЕ ВОПРОСЫ (строки «В:» вопрос и «О:» ответ) — "
    "если сообщение педагога по смыслу совпадает с одним из них, отвечай по этому ответу, "
    "перефразируя живым языком. needs_human=true ставь ТОЛЬКО для деликатного: возврат денег, "
    "спор, жалоба, перенос группы, индивидуальная договорённость — остальное старайся решить сам.\n\n"
    "СПРАВКА про Milliy sertifikat: это ОДИН государственный экзамен, который подтверждает "
    "квалификацию учителя и даёт надбавку к зарплате. У учителей начальных классов он состоит "
    "из 3 блоков (родной язык и чтение, математика, педагогика) — это ЧАСТИ одного экзамена, "
    "а НЕ разные курсы. Не придумывай несуществующих курсов или «направлений» — опирайся строго "
    "на список КУРСЫ в блоке ЗНАНИЯ. Если курс там один — предлагай его, не спрашивая направление.\n\n"
    "out_of_scope=true ставь ТОЛЬКО если вопрос ЯВНО личный или совсем не про учёбу/курсы/оплату "
    "(личная жизнь, погода, посторонние темы). Если сообщение короткое, разговорное или неясное, "
    "но МОЖЕТ относиться к курсу/оплате — НЕ ставь out_of_scope: коротко и дружелюбно переспроси, "
    "что именно интересует (reply заполни, needs_human=false, out_of_scope=false). Старайся помочь, "
    "а не отправлять к Насибе.\n\n"
    "Верни СТРОГО валидный JSON без markdown с полями: "
    "reply (строка — текст ответа человеку на его языке; пустая строка, если out_of_scope), "
    "intent (одно из: faq, requisites, receipt_question, greeting, complex, out_of_scope), "
    "out_of_scope (true/false — true, если вопрос не по теме курсов), "
    "needs_human (true/false — true, если нужно вмешательство Насибы), "
    "lang (uz_latn / uz_cyrl / ru — язык собеседника), "
    "interested_course (ключ курса из знаний, если понятно о каком речь, иначе null), "
    "faq_id (число — номер из [FAQ#N], на котором основан твой ответ, ТОЛЬКО если вопрос "
    "педагога явно совпадает с этим FAQ; иначе null)."
)


async def answer_customer(customer_text: str, knowledge: str) -> dict:
    """Классифицирует вопрос педагога и готовит ответ от имени Насибы."""
    user_msg = f"ЗНАНИЯ (факты о курсах и реквизитах):\n{knowledge}\n\nСообщение человека:\n{customer_text}"
    resp = await _anthropic.messages.create(
        model=settings.chat_model,
        max_tokens=600,
        system=CHAT_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    )
    return _extract_json(resp)


# ---------------------------------------------------------------------------
# 2. Чтение чека об оплате (зрение)
# ---------------------------------------------------------------------------

RECEIPT_SYSTEM = (
    "Ты анализируешь скриншот или PDF платёжного чека (перевод с карты на карту, "
    "узбекские банки: Uzcard/Humo, приложения вроде Click, Payme, Uzum). Извлеки данные. "
    "Верни СТРОГО валидный JSON без markdown с полями: "
    "looks_like_receipt (true/false — это вообще чек об оплате?), "
    "amount (целое число в сумах, без пробелов, иначе null), "
    "card_last4 (последние 4 цифры КАРТЫ-ПОЛУЧАТЕЛЯ, строка из 4 цифр, иначе null), "
    "txn_id (номер транзакции/чека/ссылка, строка, иначе null), "
    "paid_at (дата и время платежа как в чеке, строка, иначе null), "
    "payer_name (имя отправителя, если видно, иначе null), "
    "raw_text (весь распознанный текст чека одной строкой)."
)


async def read_receipt(file_bytes: bytes, media_type: str) -> dict:
    """Распознаёт чек. media_type: image/jpeg, image/png или application/pdf."""
    b64 = base64.standard_b64encode(file_bytes).decode()
    if media_type == "application/pdf":
        content_block = {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
        }
    else:
        content_block = {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": b64},
        }
    resp = await _anthropic.messages.create(
        model=settings.vision_model,
        max_tokens=800,
        system=RECEIPT_SYSTEM,
        messages=[{"role": "user", "content": [content_block,
                   {"type": "text", "text": "Извлеки данные чека в JSON."}]}],
    )
    return _extract_json(resp)


# ---------------------------------------------------------------------------
# 3. Голосовые от педагогов (опционально)
# ---------------------------------------------------------------------------

async def transcribe_voice(file_path: str) -> str:
    """ElevenLabs (осн.) → Groq (бесплатная подстраховка)."""
    try:
        return await _transcribe_elevenlabs(file_path)
    except Exception as e:  # noqa: BLE001
        print(f"[STT] ElevenLabs не сработал: {e!r}", file=sys.stderr)
        if not settings.groq_api_key:
            raise
        return await _transcribe_groq(file_path)


async def _transcribe_elevenlabs(file_path: str) -> str:
    async with httpx.AsyncClient(timeout=120) as client:
        with open(file_path, "rb") as f:
            resp = await client.post(
                _ELEVEN_STT_URL,
                headers={"xi-api-key": settings.elevenlabs_api_key},
                data={"model_id": "scribe_v1"},
                files={"file": ("audio.ogg", f, "audio/ogg")},
            )
    resp.raise_for_status()
    return resp.json().get("text", "")


async def _transcribe_groq(file_path: str) -> str:
    async with httpx.AsyncClient(timeout=120) as client:
        with open(file_path, "rb") as f:
            resp = await client.post(
                _GROQ_STT_URL,
                headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                data={"model": "whisper-large-v3"},
                files={"file": ("audio.ogg", f, "audio/ogg")},
            )
    resp.raise_for_status()
    return resp.json().get("text", "")


# ---------------------------------------------------------------------------

def _extract_json(resp) -> dict:
    raw = resp.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").lstrip("json").strip()
    return json.loads(raw)
