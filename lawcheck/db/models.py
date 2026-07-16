from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Scan(Base):
    __tablename__ = "scans"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    url: Mapped[str] = mapped_column(String(2048))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|running|done|error
    max_pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pages_crawled: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Владелец скана, если запущен залогиненным пользователем. NULL = аноним.
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"),
                                                nullable=True, index=True)

    findings: Mapped[list["Finding"]] = relationship(
        back_populates="scan", cascade="all, delete-orphan", order_by="Finding.id",
    )


class User(Base):
    """Зарегистрированный пользователь (email + пароль). Аккаунт — опция поверх
    магик-ссылок: заказы/сканы привязываются к нему по подтверждённому email."""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuthToken(Base):
    """Одноразовый токен с TTL: подтверждение email и сброс пароля.
    purpose = 'verify_email' | 'reset_password'."""
    __tablename__ = "auth_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(32))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scan_id: Mapped[str] = mapped_column(String(32), ForeignKey("scans.id", ondelete="CASCADE"), index=True)
    check_id: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(255))
    evidence: Mapped[str] = mapped_column(Text)
    location: Mapped[str] = mapped_column(String(2048), default="")
    law_reference: Mapped[str] = mapped_column(String(255), default="")
    recommendation: Mapped[str] = mapped_column(Text, default="")
    # Структурные факты проверки (ИНН, категории ПДн, трекеры…) — для авто-сборки
    # черновиков документов под сайт. NULL для старых сканов.
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    scan: Mapped["Scan"] = relationship(back_populates="findings")


class Lead(Base):
    """Email, оставленный на странице отчёта («прислать отчёт + следить за сайтом»).
    Рассылка пока ручная — это точка захвата контакта."""
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scan_id: Mapped[str] = mapped_column(String(32), index=True)
    url: Mapped[str] = mapped_column(String(2048), default="")
    email: Mapped[str] = mapped_column(String(255), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Inquiry(Base):
    """Вопрос из чат-виджета на сайте: сообщение + контакт для ответа.
    Сохраняется в БД и мгновенно уходит алертом владельцу в Telegram."""
    __tablename__ = "inquiries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message: Mapped[str] = mapped_column(Text)
    contact: Mapped[str] = mapped_column(String(255), default="")
    page: Mapped[str] = mapped_column(String(2048), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Order(Base):
    """Заказ платного тарифа. Создаётся при клике «Оплатить», оплачивается
    через интернет-эквайринг Точки (платёжная ссылка); статус обновляет
    вебхук + контрольный запрос к API банка."""
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    plan: Mapped[str] = mapped_column(String(16))  # pro | business
    amount: Mapped[int] = mapped_column(Integer)   # в рублях
    email: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(16), default="created", index=True)
    # created | pending (ссылка выдана) | paid | failed
    # Скан, с отчёта которого оформлена покупка. Пусто = куплено не из отчёта.
    # По нему на /report/{scan_id} открываются рецепты «Как исправить» после оплаты.
    scan_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    # Владелец-аккаунт, если заказ привязан к пользователю (по подтверждённому
    # email). NULL = доступ только по магик-ссылке /account/{order_id}.
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"),
                                                nullable=True, index=True)
    operation_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    payment_link: Mapped[str] = mapped_column(String(2048), default="")
    # Сайт, подключённый к еженедельному мониторингу (Pro). Пусто = не подключён.
    monitored_url: Mapped[str] = mapped_column(String(2048), default="")
    # Верификация владения: токен для TXT-записи/meta-тега и момент подтверждения.
    verify_token: Mapped[str] = mapped_column(String(64), default="")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Telegram-чат клиента для diff-уведомлений мониторинга (через deep-link бота).
    client_chat_id: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
