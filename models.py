from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(AsyncAttrs, DeclarativeBase):
    pass


class ReceiptStatus(str, enum.Enum):
    pending = "pending"        # прочитан, ждёт решения Насибы
    clean = "clean"            # прошёл все проверки (карта + сумма + не дубль)
    duplicate = "duplicate"    # такой чек уже присылали → жульничество
    wrong_card = "wrong_card"  # платили не на карту Насибы
    wrong_amount = "wrong_amount"  # сумма не совпала ни с одной ценой курса
    unreadable = "unreadable"  # не удалось распознать как чек
    approved = "approved"      # Насиба подтвердила оплату
    rejected = "rejected"      # Насиба отклонила


class ReplyStatus(str, enum.Enum):
    pending = "pending"   # ждёт решения Насибы
    sent = "sent"         # отправлен педагогу
    rejected = "rejected" # Насиба отклонила


class Lead(Base):
    """Педагог, который написал Насибе в личку."""

    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160), default="")
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lang: Mapped[str] = mapped_column(String(8), default="uz")
    interested_course: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    receipts: Mapped[list["Receipt"]] = relationship(back_populates="lead")


class Course(Base):
    """Актуальная инфа по курсу. Насиба правит через /courses."""

    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # напр. "milliy"
    name: Mapped[str] = mapped_column(String(160))
    price_sum: Mapped[int] = mapped_column(BigInteger, default=0)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    group_label: Mapped[str | None] = mapped_column(String(64), nullable=True)  # тоифа 1/2...
    # ОТКРЫТЫЙ канал направления — реклама, бот даёт всем.
    channel_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # ЗАКРЫТАЯ группа — СЕКРЕТ. В «знания» бота НЕ попадает. Выдаётся только после
    # подтверждённой Насибой оплаты (вместе с точной датой старта из start_date).
    # private_chat_id — id закрытой группы, где бот админ: тогда он выдаёт ОДНОРАЗОВУЮ
    # инвайт-ссылку (переслать бесполезно). private_access — запасная статичная ссылка.
    private_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    private_access: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Receipt(Base):
    """Присланный чек об оплате + результат проверки."""

    __tablename__ = "receipts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"), index=True)

    # Отпечаток файла — ловит повторную пересылку того же изображения.
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    tg_file_unique_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Распознанные поля чека.
    amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    card_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    txn_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    paid_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payer_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    raw_ocr: Mapped[str] = mapped_column(Text, default="")
    # Направление, определённое по сумме чека (= цене курса). Для выдачи закрытой группы.
    course_key: Mapped[str | None] = mapped_column(String(64), nullable=True)

    status: Mapped[ReceiptStatus] = mapped_column(
        String(16), default=ReceiptStatus.pending.value
    )
    # Если дубль — на какой чек похож.
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("receipts.id"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    lead: Mapped["Lead"] = relationship(back_populates="receipts")


class PendingReply(Base):
    """Черновик ответа педагогу, ждущий одобрения Насибы (гибридный режим)."""

    __tablename__ = "pending_replies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"), index=True)
    customer_chat_id: Mapped[int] = mapped_column(BigInteger)
    business_connection_id: Mapped[str] = mapped_column(String(128))
    draft_text: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16), default="manual")  # faq/receipt/manual
    receipt_id: Mapped[int | None] = mapped_column(ForeignKey("receipts.id"), nullable=True)
    status: Mapped[ReplyStatus] = mapped_column(String(16), default=ReplyStatus.pending.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AccessCode(Base):
    """Персональный одноразовый код доступа, привязанный к оплатившему педагогу.

    Защита от передачи: погасить код может ТОЛЬКО тот аккаунт, кому он выдан
    (issued_to_user_id). Если код присылает другой пользователь — это попытка
    поделиться кодом: бот отказывает и сигналит Насибе.
    """

    __tablename__ = "access_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    course_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    issued_to_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    issued_to_lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"))
    receipt_id: Mapped[int | None] = mapped_column(ForeignKey("receipts.id"), nullable=True)
    redeemed_by_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    share_attempts: Mapped[int] = mapped_column(Integer, default=0)  # сколько раз чужой пытался
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Setting(Base):
    """Key-value: business_connection_id, текст реквизитов и т.п."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class FaqItem(Base):
    """Частый вопрос + эталонный ответ. Админ добавляет через /addfaq.
    Все записи идут в базу знаний ИИ — так у бота «богаче словарь» и он отвечает сам."""

    __tablename__ = "faq_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text, default="")
    # Необязательный скрин/фото к ответу (Telegram file_id). Бот приложит картинку,
    # когда вопрос педагога совпадёт с этим FAQ.
    photo_file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
