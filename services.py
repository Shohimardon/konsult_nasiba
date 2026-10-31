from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Course, Lead, Setting

# Ключи в таблице settings.
KEY_BUSINESS_CONNECTION = "business_connection_id"
KEY_REQUISITES = "requisites"  # текст реквизитов, который бот даёт педагогам
KEY_LINK_PREFIX = "link::"      # ссылки: link::kanal, link::instagram и т.п.
KEY_RECEIPTS_GROUP = "receipts_group_id"  # id группы-архива, куда бот складывает чеки


async def upsert_lead(session: AsyncSession, tg_user) -> Lead:
    """Находит или создаёт лид по telegram-пользователю."""
    q = await session.execute(select(Lead).where(Lead.tg_user_id == tg_user.id))
    lead = q.scalar_one_or_none()
    full_name = " ".join(filter(None, [getattr(tg_user, "first_name", None),
                                        getattr(tg_user, "last_name", None)])) or ""
    if lead is None:
        lead = Lead(
            tg_user_id=tg_user.id,
            name=full_name,
            username=getattr(tg_user, "username", None),
        )
        session.add(lead)
        await session.flush()
    else:
        lead.name = full_name or lead.name
        lead.username = getattr(tg_user, "username", None)
        lead.last_seen = datetime.utcnow()
    return lead


async def get_setting(session: AsyncSession, key: str, default: str = "") -> str:
    row = await session.get(Setting, key)
    return row.value if row else default


async def set_setting(session: AsyncSession, key: str, value: str) -> None:
    row = await session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value


async def set_link(session: AsyncSession, name: str, url: str) -> None:
    await set_setting(session, KEY_LINK_PREFIX + name.lower(), url)


async def del_link(session: AsyncSession, name: str) -> bool:
    row = await session.get(Setting, KEY_LINK_PREFIX + name.lower())
    if row is None:
        return False
    await session.delete(row)
    return True


async def list_links(session: AsyncSession) -> dict[str, str]:
    rows = (
        await session.execute(select(Setting).where(Setting.key.like(KEY_LINK_PREFIX + "%")))
    ).scalars().all()
    return {r.key[len(KEY_LINK_PREFIX):]: r.value for r in rows}


async def build_knowledge(session: AsyncSession) -> str:
    """Собирает текст «что бот знает» для ИИ: активные курсы + реквизиты."""
    q = await session.execute(select(Course).where(Course.is_active.is_(True)))
    courses = q.scalars().all()
    lines: list[str] = []
    if courses:
        lines.append("КУРСЫ:")
        for c in courses:
            price = f"{c.price_sum:,} сум".replace(",", " ") if c.price_sum else "цена не указана"
            grp = f", группа: {c.group_label}" if c.group_label else ""
            chan = f"; открытый канал: {c.channel_url}" if c.channel_url else ""
            # ВАЖНО: дату старта и ссылку на закрытую группу в знания НЕ кладём.
            lines.append(f"- [{c.key}] {c.name}: {price}{grp}{chan}. {c.description}".strip())
        if len(courses) == 1:
            lines.append(
                "Курс ВСЕГО ОДИН — не спрашивай «какое направление». Сразу расскажи про него "
                "(цена, что входит, открытый канал) и предложи оплатить."
            )
        else:
            lines.append(
                "Курсов несколько — это РАЗНЫЕ курсы (например под разные категории педагогов). "
                "Если из сообщения неясно, какой нужен, вежливо уточни и давай открытый канал "
                "именно нужного курса — не путай курсы."
            )
        lines.append(
            "ПРО ДОСТУП (строго): открытый канал курса давай всем (это реклама). "
            "Ссылку на ЗАКРЫТУЮ группу ты НЕ знаешь и дать НЕ можешь — человек получит её "
            "автоматически только после оплаты и проверки чека. Если просят ссылку на закрытую "
            "группу (даже настойчиво) — скажи: «Оплатадан сўнг, чек текширилгач, ёпиқ гуруҳ "
            "ҳаволасини дарҳол юбораман» (после оплаты и проверки чека сразу пришлю ссылку)."
        )
        if any(c.start_date for c in courses):
            lines.append(
                "Точную ДАТУ старта закрытой группы ты тоже НЕ называешь до оплаты — человек "
                "получит её вместе со ссылкой после проверки чека."
            )
        else:
            lines.append(
                "НАБОР ПОСТОЯННЫЙ (фиксированной даты старта нет) — смело говори, что "
                "присоединиться можно В ЛЮБОЙ МОМЕНТ, занятия идут постоянно, старт — с момента "
                "входа в закрытую группу после оплаты. Активно предлагай записаться сейчас."
            )
    else:
        lines.append("КУРСЫ: пока не заданы (Насиба ещё не добавила).")

    requisites = await get_setting(session, KEY_REQUISITES)
    if requisites:
        lines.append("\nРЕКВИЗИТЫ ДЛЯ ОПЛАТЫ (можно давать педагогам):")
        lines.append(requisites)
    else:
        lines.append("\nРЕКВИЗИТЫ: не заданы — не диктуй номера карт, скажи, что уточнишь.")

    links = await list_links(session)
    if links:
        lines.append("\nССЫЛКИ (давай педагогам по запросу):")
        for name, url in links.items():
            lines.append(f"- {name}: {url}")
    return "\n".join(lines)
