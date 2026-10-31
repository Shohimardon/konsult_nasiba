from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models import Course, Lead, Receipt, ReceiptStatus


def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def check_receipt(
    session: AsyncSession,
    lead: Lead,
    parsed: dict,
    file_hash: str,
    file_unique_id: str | None,
) -> Receipt:
    """Создаёт запись чека и прогоняет анти-фрод проверки.

    Порядок вердикта (по убыванию опасности):
      1. не чек            → unreadable
      2. дубль             → duplicate    (тот же файл ИЛИ тот же номер транзакции)
      3. не та карта       → wrong_card   (получатель не из карт Насибы)
      4. сумма ≠ цене      → wrong_amount (сумма не совпала ни с одной ценой курса)
      5. всё чисто         → clean        (+ course_key — направление по сумме)
    Финальное подтверждение (approved) всё равно за Насибой.
    """
    card_last4 = parsed.get("card_last4")
    if isinstance(card_last4, str):
        card_last4 = "".join(ch for ch in card_last4 if ch.isdigit())[-4:] or None
    txn_id = parsed.get("txn_id") or None

    receipt = Receipt(
        lead_id=lead.id,
        file_hash=file_hash,
        tg_file_unique_id=file_unique_id,
        amount=_as_int(parsed.get("amount")),
        card_last4=card_last4,
        txn_id=str(txn_id) if txn_id else None,
        paid_at=parsed.get("paid_at"),
        payer_name=parsed.get("payer_name"),
        raw_ocr=parsed.get("raw_text", "") or "",
    )

    if not parsed.get("looks_like_receipt"):
        receipt.status = ReceiptStatus.unreadable.value
        session.add(receipt)
        await session.flush()
        return receipt

    # --- дубль: тот же файл или тот же номер транзакции у любого педагога ---
    dup = await _find_duplicate(session, file_hash, receipt.txn_id)
    if dup is not None:
        receipt.status = ReceiptStatus.duplicate.value
        receipt.duplicate_of_id = dup.id
        session.add(receipt)
        await session.flush()
        return receipt

    # --- сверка карты-получателя ---
    if settings.card_last4 and card_last4 and card_last4 not in settings.card_last4:
        receipt.status = ReceiptStatus.wrong_card.value
        session.add(receipt)
        await session.flush()
        return receipt

    # --- сверка суммы с ценой курса (сумма заодно определяет направление) ---
    priced = (
        await session.execute(
            select(Course).where(Course.is_active.is_(True), Course.price_sum > 0)
        )
    ).scalars().all()
    if priced:
        if receipt.amount is None:
            # Цены заданы, но сумму из чека не прочитали — автоматически не подтверждаем.
            receipt.status = ReceiptStatus.wrong_amount.value
            session.add(receipt)
            await session.flush()
            return receipt
        matches = [c for c in priced if c.price_sum == receipt.amount]
        if not matches:
            receipt.status = ReceiptStatus.wrong_amount.value
            session.add(receipt)
            await session.flush()
            return receipt
        if len(matches) == 1:
            # Сумма однозначно указывает на направление.
            receipt.course_key = matches[0].key
        # Если несколько курсов с одинаковой ценой — направление по сумме неоднозначно,
        # course_key оставляем пустым (разрулит выдача: по интересу из переписки или Насиба).

    receipt.status = ReceiptStatus.clean.value
    session.add(receipt)
    await session.flush()
    return receipt


async def _find_duplicate(session: AsyncSession, file_hash: str, txn_id: str | None) -> Receipt | None:
    # Точное совпадение файла.
    q = await session.execute(select(Receipt).where(Receipt.file_hash == file_hash).limit(1))
    hit = q.scalar_one_or_none()
    if hit is not None:
        return hit
    # Совпадение номера транзакции (пересняли/пересохранили картинку, но транзакция та же).
    if txn_id:
        q = await session.execute(
            select(Receipt).where(Receipt.txn_id == txn_id).limit(1)
        )
        hit = q.scalar_one_or_none()
        if hit is not None:
            return hit
    return None


def _as_int(v) -> int | None:
    if v is None:
        return None
    if isinstance(v, int):
        return v
    digits = "".join(ch for ch in str(v) if ch.isdigit())
    return int(digits) if digits else None
