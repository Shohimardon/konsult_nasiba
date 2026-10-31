from __future__ import annotations

from functools import cached_property

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Telegram ---
    bot_token: str
    bot_username: str = ""

    # Telegram ID самой Насибы — сюда бот шлёт черновики на одобрение и отчёты.
    # Это её личный user_id (узнать: написать боту /start, он подскажет).
    nasiba_chat_id: int = Field(validation_alias="NASIBA_CHAT_ID")

    # Доп. админы (помощники), через запятую. Насиба добавляется автоматически.
    admin_ids_raw: str = Field(default="", validation_alias="ADMIN_IDS")

    @cached_property
    def admin_ids(self) -> list[int]:
        ids = {int(x) for x in self.admin_ids_raw.replace(" ", "").split(",") if x}
        ids.add(self.nasiba_chat_id)
        return list(ids)

    # --- Карты Насибы (для сверки получателя в чеке) ---
    # Полные номера или хвосты, через запятую. Сравниваем по последним 4 цифрам.
    nasiba_cards_raw: str = Field(default="", validation_alias="NASIBA_CARDS")

    @cached_property
    def card_last4(self) -> set[str]:
        out: set[str] = set()
        for raw in self.nasiba_cards_raw.split(","):
            digits = "".join(ch for ch in raw if ch.isdigit())
            if len(digits) >= 4:
                out.add(digits[-4:])
        return out

    # --- ИИ ---
    anthropic_api_key: str
    # Ответы педагогам (текст) — Haiku: простой разбор + ответ из базы знаний, дёшево.
    chat_model: str = "claude-haiku-4-5"
    # Чтение чеков (зрение, анти-фрод) — Sonnet: на кону деньги, надёжность важнее цены.
    vision_model: str = "claude-sonnet-4-5"

    # Распознавание голосовых от педагогов (не обязательно для MVP).
    elevenlabs_api_key: str = ""
    groq_api_key: str = ""

    # --- БД ---
    database_url: str

    @cached_property
    def async_database_url(self) -> str:
        """Railway даёт postgresql:// — приводим к asyncpg-драйверу."""
        url = self.database_url
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+asyncpg://", 1)
        return url

    tz: str = "Asia/Tashkent"

    # Если уверенность ИИ ниже порога — не отвечаем сами, отдаём черновик Насибе.
    # (confidence у нас строковый high/low, порог — на случай будущей числовой логики)
    autoreply_on_low_confidence: bool = False


settings = Settings()
