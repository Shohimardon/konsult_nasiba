from __future__ import annotations

import logging
import secrets
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import AccessCode, Course, Lead

logger = logging.getLogger(__name__)

# Алфавит без похожих символов (0/O, 1/I/L) — чтобы код не путали при наборе.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def generate_code(n: int = 6) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))


async def issue_code(
    session: AsyncSession, lead: Lead, course_key: str | None, receipt_id: int | None
) -> AccessCode:
    """Выдаёт новый уникальный код, привязанный к оплатившему педагогу."""
    code = generate_code()
    while (
        await session.execute(select(AccessCode).where(AccessCode.code == code))
    ).scalar_one_or_none() is not None:
        code = generate_code()
    ac = AccessCode(
        code=code,
        course_key=course_key,
        issued_to_user_id=lead.tg_user_id,
        issued_to_lead_id=lead.id,
        receipt_id=receipt_id,
    )
    session.add(ac)
    await session.flush()
    return ac


async def find_code(session: AsyncSession, text: str | None) -> AccessCode | None:
    """Ищет код по тексту сообщения. None — если это вообще не похоже на код."""
    norm = (text or "").strip().upper().replace(" ", "").replace("-", "")
    if not (5 <= len(norm) <= 12) or not norm.isalnum():
        return None
    return (
        await session.execute(select(AccessCode).where(AccessCode.code == norm))
    ).scalar_one_or_none()


async def redeem(session: AsyncSession, ac: AccessCode, user_id: int):
    """Пытается погасить код от имени user_id.

    Возвращает (status, course):
      - "foreign" — код прислал НЕ тот, кому он выдан (попытка поделиться) → доступ не даём;
      - "ok"      — владелец кода (первое или повторное обращение) → отдаём доступ.
    """
    if ac.issued_to_user_id != user_id:
        ac.share_attempts += 1
        return "foreign", None

    course = None
    if ac.course_key:
        course = (
            await session.execute(select(Course).where(Course.key == ac.course_key))
        ).scalar_one_or_none()
    if ac.redeemed_by_user_id is None:
        ac.redeemed_by_user_id = user_id
        ac.redeemed_at = datetime.utcnow()
    return "ok", course


async def build_access_message(bot, course: Course) -> str:
    """Текст с доступом в закрытую группу. Если бот — админ группы (private_chat_id),
    генерит ОДНОРАЗОВУЮ инвайт-ссылку (member_limit=1, переслать бесполезно),
    иначе отдаёт статичную ссылку private_access."""
    lines = ["🔐 Ёпиқ гуруҳ / Закрытая группа:"]
    link = None
    if course.private_chat_id:
        try:
            invite = await bot.create_chat_invite_link(
                course.private_chat_id, member_limit=1, name="one-time"
            )
            link = invite.invite_link
        except Exception as e:  # noqa: BLE001
            logger.warning("Не смог создать одноразовую ссылку для %s: %r", course.key, e)
    if link:
        lines.append(link)
        lines.append("(бир martalik havola — faqat siz uchun / одноразовая ссылка)")
    elif course.private_access:
        lines.append(course.private_access)
    else:
        lines.append("ҳаволани Насиба юборади.")
    if course.start_date:
        lines.append(f"Старт: {course.start_date.isoformat()}")
    else:
        lines.append("Дарслар доимий — исталган вақтда бошлашингиз мумкин 🙌")
    return "\n".join(lines)
