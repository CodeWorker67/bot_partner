import json
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    Column, Integer, String, DateTime, Boolean, BigInteger, Date, Float,
    UniqueConstraint, event,
)
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncAttrs
from sqlalchemy.orm import DeclarativeBase

from config import (
    BOT_ID,
    BOT_USERNAME,
    DATABASE_PATH,
    DEFAULT_PRICES,
    DEFAULT_TRIAL_DAYS,
    OWNER_TG_ID,
    SOURCE_BOT_ID,
)

_db_parent = DATABASE_PATH.parent
_db_parent.mkdir(parents=True, exist_ok=True)
DB_URL = f"sqlite+aiosqlite:///{DATABASE_PATH.as_posix()}"
engine = create_async_engine(DB_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


class Base(DeclarativeBase, AsyncAttrs):
    pass


class PartnerBotSettings(Base):
    __tablename__ = "partner_bot_settings"
    bot_id = Column(Integer, primary_key=True)
    owner_tg_id = Column(BigInteger, nullable=False)
    partner_balance = Column(Integer, default=0)
    balance_own_bot = Column(Integer, default=0)
    balance_child_bots = Column(Integer, default=0)
    partner_pay = Column(Integer, default=0)
    channel_id = Column(BigInteger, nullable=True)
    channel_url = Column(String(512), nullable=True)
    channel_required = Column(Boolean, default=False)
    trial_days = Column(Integer, default=DEFAULT_TRIAL_DAYS)
    prices_json = Column(String(4096), nullable=True)
    partner_since = Column(DateTime, default=datetime.now)
    partner_username = Column(String(255), nullable=True)
    bot_username = Column(String(255), nullable=True)
    bot_display_name = Column(String(255), nullable=True)
    source_bot_id = Column(BigInteger, nullable=True)


class Users(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("user_id", "bot_id", name="uq_users_bot"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, nullable=False)
    bot_id = Column(Integer, nullable=False, default=BOT_ID)
    ref = Column(String(100), nullable=True)
    ref_balance = Column(Integer, default=0)
    is_delete = Column(Boolean, default=False)
    in_panel = Column(Boolean, default=False)
    is_connect = Column(Boolean, default=False)
    create_user = Column(DateTime, default=datetime.now)
    in_chanel = Column(Boolean, default=False)
    reserve_field = Column(Boolean, default=False)
    subscription_end_date = Column(DateTime, nullable=True)
    subscription_3_end_date = Column(DateTime, nullable=True)
    subscription_10_end_date = Column(DateTime, nullable=True)
    last_notification_date = Column(Date, nullable=True)
    last_broadcast_status = Column(String(100), nullable=True)
    last_broadcast_date = Column(DateTime, nullable=True)
    stamp = Column(String(100), nullable=False, default="")
    ttclid = Column(String(100), nullable=True)
    subscribtion = Column(String(255), nullable=True)
    subscribtion_3 = Column(String(255), nullable=True)
    subscribtion_10 = Column(String(255), nullable=True)
    field_bool_3 = Column(Boolean, default=False)
    field_str_1 = Column(String(4096), nullable=True)
    field_str_2 = Column(String(4096), nullable=True)
    partner = Column(String(100), nullable=True)
    partner_balance = Column(Integer, default=0)
    partner_pay = Column(Integer, default=0)
    partner_flag = Column(Boolean, default=False)


class Gifts(Base):
    __tablename__ = "gifts"
    gift_id = Column(String(36), primary_key=True)
    bot_id = Column(Integer, nullable=False, default=BOT_ID)
    giver_id = Column(BigInteger, nullable=False)
    duration = Column(Integer, nullable=False)
    recepient_id = Column(BigInteger, nullable=True)
    device_slots = Column(Integer, default=5)
    flag = Column(Boolean, default=False)


class PaymentsFkSBP(Base):
    __tablename__ = "payments_fk_sbp"
    id = Column(Integer, primary_key=True, autoincrement=True)
    bot_id = Column(Integer, nullable=False, default=BOT_ID)
    user_id = Column(BigInteger, nullable=False)
    amount = Column(Integer, nullable=False)
    time_created = Column(DateTime, default=datetime.now)
    is_gift = Column(Boolean, default=False)
    status = Column(String, nullable=True)
    transaction_id = Column(String, nullable=True)
    fk_order_id = Column(Integer, nullable=True)
    payload = Column(String, nullable=True)
    nonce = Column(BigInteger, nullable=False)
    signature = Column(String, nullable=True)
    method = Column(String, nullable=False, default="fksbp")


class PaymentsStars(Base):
    __tablename__ = "payments_stars"
    id = Column(Integer, primary_key=True, autoincrement=True)
    bot_id = Column(Integer, nullable=False, default=BOT_ID)
    user_id = Column(BigInteger, nullable=False)
    amount = Column(Integer, nullable=False)
    time_created = Column(DateTime, default=datetime.now)
    is_gift = Column(Boolean, default=False)
    status = Column(String, default="confirmed")
    payload = Column(String, nullable=True)


class PaymentsCryptobot(Base):
    __tablename__ = "payments_cryptobot"
    id = Column(Integer, primary_key=True, autoincrement=True)
    bot_id = Column(Integer, nullable=False, default=BOT_ID)
    user_id = Column(BigInteger, nullable=False)
    amount = Column(Float, nullable=False)
    currency = Column(String(10), nullable=False)
    time_created = Column(DateTime, default=datetime.now)
    is_gift = Column(Boolean, default=False)
    status = Column(String, default="pending")
    invoice_id = Column(String, nullable=True)
    payload = Column(String, nullable=True)


class Online(Base):
    __tablename__ = "online"
    online_id = Column(Integer, primary_key=True, autoincrement=True)
    bot_id = Column(Integer, nullable=False, default=BOT_ID)
    online_date = Column(DateTime, default=datetime.now, nullable=False)
    users_panel = Column(Integer, nullable=False)
    users_active = Column(Integer, nullable=False)
    users_pay = Column(Integer, nullable=False)
    users_trial = Column(Integer, nullable=False)


async def create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _migrate_schema()
    await _ensure_bot_settings()


async def _migrate_schema():
    from sqlalchemy import text

    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(users)"))
        cols = {row[1] for row in result.fetchall()}
        if "field_str_1" not in cols:
            await conn.execute(text("ALTER TABLE users ADD COLUMN field_str_1 VARCHAR(4096)"))
        if "field_str_2" not in cols:
            await conn.execute(text("ALTER TABLE users ADD COLUMN field_str_2 VARCHAR(4096)"))
        for name, col_type in (
            ("partner", "VARCHAR(100)"),
            ("partner_balance", "INTEGER DEFAULT 0"),
            ("partner_pay", "INTEGER DEFAULT 0"),
            ("partner_flag", "BOOLEAN DEFAULT FALSE"),
        ):
            if name not in cols:
                await conn.execute(text(f"ALTER TABLE users ADD COLUMN {name} {col_type}"))

        result = await conn.execute(text("PRAGMA table_info(partner_bot_settings)"))
        settings_cols = {row[1] for row in result.fetchall()}
        if "partner_since" not in settings_cols:
            await conn.execute(text("ALTER TABLE partner_bot_settings ADD COLUMN partner_since DATETIME"))
            settings_cols.add("partner_since")
        # Только NULL — уже записанную дату не перезаписываем
        await conn.execute(
            text("UPDATE partner_bot_settings SET partner_since = CURRENT_TIMESTAMP WHERE partner_since IS NULL")
        )
        if "balance_own_bot" not in settings_cols:
            await conn.execute(text("ALTER TABLE partner_bot_settings ADD COLUMN balance_own_bot INTEGER DEFAULT 0"))
            await conn.execute(
                text(
                    "UPDATE partner_bot_settings SET balance_own_bot = COALESCE(partner_balance, 0) "
                    "WHERE balance_own_bot IS NULL OR balance_own_bot = 0"
                )
            )
        if "balance_child_bots" not in settings_cols:
            await conn.execute(text("ALTER TABLE partner_bot_settings ADD COLUMN balance_child_bots INTEGER DEFAULT 0"))
        for name, col_type in (
            ("partner_username", "VARCHAR(255)"),
            ("bot_username", "VARCHAR(255)"),
            ("bot_display_name", "VARCHAR(255)"),
            ("source_bot_id", "BIGINT"),
        ):
            if name not in settings_cols:
                await conn.execute(text(f"ALTER TABLE partner_bot_settings ADD COLUMN {name} {col_type}"))


async def _ensure_bot_settings():
    from sqlalchemy import select

    if not BOT_ID:
        return
    async with AsyncSessionLocal() as session:
        stmt = select(PartnerBotSettings).where(PartnerBotSettings.bot_id == BOT_ID)
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()
        if row:
            # Первый деплой мог создать запись без даты — дописываем один раз
            if row.partner_since is None:
                row.partner_since = datetime.now()
                await session.commit()
            return
        session.add(
            PartnerBotSettings(
                bot_id=BOT_ID,
                owner_tg_id=OWNER_TG_ID,
                trial_days=DEFAULT_TRIAL_DAYS,
                prices_json=json.dumps(DEFAULT_PRICES),
                bot_username=BOT_USERNAME or None,
                source_bot_id=SOURCE_BOT_ID,
                partner_since=datetime.now(),
            )
        )
        await session.commit()
