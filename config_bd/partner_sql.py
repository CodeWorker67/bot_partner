"""БД-операции бота-партнёра с фильтром bot_id."""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, date, timezone, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import and_, func, or_, select, update, delete, literal, case, cast, Date, union_all
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from config import BOT_ID, DEFAULT_PRICES, TRIAL_DAYS_MAX, TRIAL_DAYS_MIN
from lexicon import PAYMENT_MINOR_THRESHOLD_RUB
from config_bd.models import (
    AsyncSessionLocal,
    Gifts,
    Online,
    PartnerBotSettings,
    PaymentsCryptobot,
    PaymentsFkSBP,
    PaymentsStars,
    Users,
)
from logging_config import logger
from tariff_resolve import tariff_days_for_x3


def _user_tuple(user: Users) -> Tuple:
    return (
        user.id, user.user_id, user.ref, user.is_delete,
        user.in_panel, user.is_connect, user.create_user,
        user.in_chanel, user.reserve_field, user.subscription_end_date,
        None, user.last_notification_date,
        user.last_broadcast_status, user.last_broadcast_date,
        user.stamp, user.ttclid,
        user.subscribtion, None, None,
        None, None,
        None, None, None,
        None, None, user.field_bool_3,
        None, user.ref_balance, None, None,
        user.subscription_3_end_date, user.subscription_10_end_date,
    )


def _user_filter(user_id: int):
    return and_(Users.user_id == user_id, Users.bot_id == BOT_ID)


def pro_subscription_end_active(end_dt: Optional[datetime]) -> bool:
    if end_dt is None:
        return False
    aware = end_dt.replace(tzinfo=timezone.utc) if end_dt.tzinfo is None else end_dt.astimezone(timezone.utc)
    return aware.date() >= datetime.now(timezone.utc).date()


def user_has_active_pro_subscription(user: Users) -> bool:
    return any(
        pro_subscription_end_active(dt)
        for dt in (user.subscription_end_date, user.subscription_3_end_date, user.subscription_10_end_date)
    )


def parse_user_profile(raw: Optional[str]) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def user_last_visit(user: Users) -> datetime:
    profile = parse_user_profile(user.field_str_2)
    raw = profile.get("last_activity")
    if raw:
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError):
            pass
    dt = user.create_user or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class PartnerSQL:
    def __init__(self):
        self.session_factory = AsyncSessionLocal
        self.bot_id = BOT_ID

    async def get_user(self, user_id: int) -> Optional[Tuple]:
        async with self.session_factory() as session:
            stmt = select(Users).where(_user_filter(user_id))
            user = (await session.execute(stmt)).scalar_one_or_none()
            return _user_tuple(user) if user else None

    async def get_user_object_by_user_id(self, user_id: int) -> Optional[Users]:
        async with self.session_factory() as session:
            stmt = select(Users).where(_user_filter(user_id))
            return (await session.execute(stmt)).scalar_one_or_none()

    async def add_user(
        self,
        user_id: int,
        in_panel: bool = False,
        is_connect: bool = False,
        ref: str = "",
        is_delete: bool = False,
        in_chanel: bool = False,
        stamp: str = "",
    ) -> bool:
        async with self.session_factory() as session:
            stmt = sqlite_insert(Users).values(
                user_id=user_id,
                bot_id=BOT_ID,
                ref=ref or None,
                is_delete=is_delete,
                in_panel=in_panel,
                is_connect=is_connect,
                in_chanel=in_chanel,
                stamp=stamp or "",
            ).on_conflict_do_nothing(index_elements=["user_id", "bot_id"])
            try:
                result = await session.execute(stmt)
                await session.commit()
                return (result.rowcount or 0) > 0
            except Exception as e:
                await session.rollback()
                logger.error("add_user {}: {}", user_id, e)
                return False

    async def update_in_panel(self, user_id: int):
        async with self.session_factory() as session:
            await session.execute(update(Users).where(_user_filter(user_id)).values(in_panel=True))
            await session.commit()

    async def update_in_chanel(self, user_id: int, booly: bool):
        async with self.session_factory() as session:
            await session.execute(update(Users).where(_user_filter(user_id)).values(in_chanel=booly))
            await session.commit()

    async def update_is_connect(self, user_id: int, booly: bool):
        async with self.session_factory() as session:
            await session.execute(update(Users).where(_user_filter(user_id)).values(is_connect=booly))
            await session.commit()

    async def update_reserve_field(self, user_id: int):
        async with self.session_factory() as session:
            await session.execute(update(Users).where(_user_filter(user_id)).values(reserve_field=True))
            await session.commit()

    async def try_set_ref_from_invite(self, user_id: int, ref: str) -> bool:
        if not str(ref).strip():
            return False
        async with self.session_factory() as session:
            stmt = (
                update(Users)
                .where(_user_filter(user_id), or_(Users.ref.is_(None), Users.ref == ""))
                .values(ref=str(ref))
            )
            result = await session.execute(stmt)
            await session.commit()
            return (result.rowcount or 0) > 0

    async def update_subscription_end_date(self, user_id: int, end_date: datetime, slot: str = "main"):
        col = {
            "main": Users.subscription_end_date,
            "3": Users.subscription_3_end_date,
            "10": Users.subscription_10_end_date,
        }.get(slot, Users.subscription_end_date)
        async with self.session_factory() as session:
            await session.execute(update(Users).where(_user_filter(user_id)).values({col.key: end_date}))
            await session.commit()

    async def update_subscription_3_end_date(self, user_id: int, end_date: datetime):
        await self.update_subscription_end_date(user_id, end_date, slot="3")

    async def update_subscription_10_end_date(self, user_id: int, end_date: datetime):
        await self.update_subscription_end_date(user_id, end_date, slot="10")

    async def update_sub_url(self, user_id: int, url: str, slot: str = "main"):
        col = {
            "main": Users.subscribtion,
            "3": Users.subscribtion_3,
            "10": Users.subscribtion_10,
        }.get(slot, Users.subscribtion)
        async with self.session_factory() as session:
            await session.execute(update(Users).where(_user_filter(user_id)).values({col.key: url}))
            await session.commit()

    async def update_subscribtion(self, user_id: int, url: str):
        await self.update_sub_url(user_id, url, slot="main")

    async def update_subscribtion_3(self, user_id: int, url: str):
        await self.update_sub_url(user_id, url, slot="3")

    async def update_subscribtion_10(self, user_id: int, url: str):
        await self.update_sub_url(user_id, url, slot="10")

    async def set_field_bool_3(self, user_id: int, val: bool = True):
        async with self.session_factory() as session:
            await session.execute(update(Users).where(_user_filter(user_id)).values(field_bool_3=val))
            await session.commit()

    async def add_ref_balance(self, user_id: int, amount: int) -> bool:
        async with self.session_factory() as session:
            stmt = (
                update(Users)
                .where(_user_filter(user_id))
                .values(ref_balance=func.coalesce(Users.ref_balance, 0) + amount)
            )
            result = await session.execute(stmt)
            await session.commit()
            return (result.rowcount or 0) > 0

    async def select_ref_count(self, ref_id: int) -> int:
        async with self.session_factory() as session:
            stmt = select(func.count()).select_from(Users).where(
                Users.bot_id == BOT_ID, Users.ref == str(ref_id)
            )
            return (await session.execute(stmt)).scalar() or 0

    async def create_gift(self, giver_id: int, duration: int, device_slots: int = 5) -> str:
        gift_id = str(uuid.uuid4())
        async with self.session_factory() as session:
            session.add(
                Gifts(
                    gift_id=gift_id,
                    bot_id=BOT_ID,
                    giver_id=giver_id,
                    duration=duration,
                    device_slots=device_slots,
                )
            )
            await session.commit()
        return gift_id

    async def get_gift(self, gift_id: str) -> Optional[Gifts]:
        async with self.session_factory() as session:
            stmt = select(Gifts).where(Gifts.gift_id == gift_id, Gifts.bot_id == BOT_ID)
            return (await session.execute(stmt)).scalar_one_or_none()

    async def activate_gift(self, gift_id: str, recipient_id: int) -> bool:
        async with self.session_factory() as session:
            stmt = (
                update(Gifts)
                .where(Gifts.gift_id == gift_id, Gifts.bot_id == BOT_ID, Gifts.flag == False)
                .values(recepient_id=recipient_id, flag=True)
            )
            result = await session.execute(stmt)
            await session.commit()
            return (result.rowcount or 0) > 0

    async def get_bot_settings(self) -> Optional[Dict[str, Any]]:
        async with self.session_factory() as session:
            stmt = select(PartnerBotSettings).where(PartnerBotSettings.bot_id == BOT_ID)
            row = (await session.execute(stmt)).scalar_one_or_none()
            if not row:
                return None
            return {
                "bot_id": row.bot_id,
                "owner_tg_id": row.owner_tg_id,
                "partner_balance": row.partner_balance or 0,
                "partner_pay": row.partner_pay or 0,
                "channel_id": row.channel_id,
                "channel_url": row.channel_url,
                "channel_required": bool(row.channel_required),
                "trial_days": row.trial_days or 3,
                "prices_json": row.prices_json,
                "partner_since": row.partner_since,
            }

    async def update_bot_settings(self, **kwargs) -> None:
        async with self.session_factory() as session:
            await session.execute(
                update(PartnerBotSettings).where(PartnerBotSettings.bot_id == BOT_ID).values(**kwargs)
            )
            await session.commit()

    async def add_partner_balance(self, amount: int) -> None:
        async with self.session_factory() as session:
            await session.execute(
                update(PartnerBotSettings)
                .where(PartnerBotSettings.bot_id == BOT_ID)
                .values(partner_balance=func.coalesce(PartnerBotSettings.partner_balance, 0) + amount)
            )
            await session.commit()

    async def _load_prices_json_raw(self) -> Dict[str, int]:
        settings = await self.get_bot_settings()
        if not settings or not settings.get("prices_json"):
            return {}
        try:
            data = json.loads(settings["prices_json"])
            if not isinstance(data, dict):
                return {}
            return {
                k: int(v)
                for k, v in data.items()
                if k in DEFAULT_PRICES and isinstance(v, (int, float, str))
            }
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}

    async def get_price_overrides(self) -> Dict[str, int]:
        """Явные переопределения цен (отличаются от базовых)."""
        raw = await self._load_prices_json_raw()
        return {k: v for k, v in raw.items() if v != DEFAULT_PRICES[k]}

    async def get_prices(self) -> Dict[str, int]:
        overrides = await self.get_price_overrides()
        return {**DEFAULT_PRICES, **overrides}

    async def set_price(self, key: str, price: int) -> Tuple[bool, str]:
        if key not in DEFAULT_PRICES:
            return False, "Неизвестный тариф"
        min_p = DEFAULT_PRICES[key]
        if price < min_p:
            return False, f"Минимальная цена: {min_p} ₽ (нельзя ниже базового тарифа)"
        overrides = await self.get_price_overrides()
        if price == min_p:
            overrides.pop(key, None)
        else:
            overrides[key] = price
        await self.update_bot_settings(prices_json=json.dumps(overrides))
        return True, "OK"

    async def reset_price(self, key: str) -> Tuple[bool, str]:
        if key not in DEFAULT_PRICES:
            return False, "Неизвестный тариф"
        overrides = await self.get_price_overrides()
        overrides.pop(key, None)
        await self.update_bot_settings(prices_json=json.dumps(overrides))
        return True, "OK"

    async def set_trial_days(self, days: int) -> Tuple[bool, str]:
        if not TRIAL_DAYS_MIN <= days <= TRIAL_DAYS_MAX:
            return False, f"Допустимо {TRIAL_DAYS_MIN}–{TRIAL_DAYS_MAX} дней"
        await self.update_bot_settings(trial_days=days)
        return True, "OK"

    async def list_users(self, offset: int = 0, limit: int = 20) -> List[Users]:
        async with self.session_factory() as session:
            stmt = (
                select(Users)
                .where(Users.bot_id == BOT_ID, Users.is_delete == False)
                .order_by(Users.create_user.desc())
                .offset(offset)
                .limit(limit)
            )
            return list((await session.execute(stmt)).scalars().all())

    async def sync_user_profile(
        self,
        user_id: int,
        *,
        username: Optional[str] = None,
        full_name: Optional[str] = None,
        language: Optional[str] = None,
    ) -> None:
        async with self.session_factory() as session:
            stmt = select(Users).where(_user_filter(user_id))
            user = (await session.execute(stmt)).scalar_one_or_none()
            if not user:
                return
            profile = parse_user_profile(user.field_str_2)
            if username is not None:
                profile["username"] = username
            if full_name is not None:
                profile["full_name"] = full_name
            if language is not None:
                profile["language"] = language
            profile["last_activity"] = datetime.now(timezone.utc).isoformat()
            await session.execute(
                update(Users).where(_user_filter(user_id)).values(
                    field_str_2=json.dumps(profile, ensure_ascii=False)
                )
            )
            await session.commit()

    async def search_user_by_username(self, username: str) -> Optional[Users]:
        needle = username.lstrip("@").lower()
        if not needle:
            return None
        async with self.session_factory() as session:
            stmt = select(Users).where(Users.bot_id == BOT_ID, Users.is_delete == False)
            for user in (await session.execute(stmt)).scalars().all():
                profile = parse_user_profile(user.field_str_2)
                if (profile.get("username") or "").lower() == needle:
                    return user
        return None

    async def count_user_transactions(self, user_id: int) -> int:
        total = 0
        async with self.session_factory() as session:
            for model in (PaymentsFkSBP, PaymentsStars, PaymentsCryptobot):
                stmt = select(func.count()).select_from(model).where(
                    model.bot_id == BOT_ID,
                    model.user_id == user_id,
                    model.status.in_(("confirmed", "paid")),
                )
                total += (await session.execute(stmt)).scalar() or 0
        return total

    async def search_user_by_id(self, tg_id: int) -> Optional[Users]:
        return await self.get_user_object_by_user_id(tg_id)

    async def count_users(self) -> int:
        async with self.session_factory() as session:
            stmt = select(func.count()).select_from(Users).where(
                Users.bot_id == BOT_ID, Users.is_delete == False
            )
            return (await session.execute(stmt)).scalar() or 0

    async def count_bot_visits_since(self, since: datetime) -> int:
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        else:
            since = since.astimezone(timezone.utc)
        async with self.session_factory() as session:
            stmt = select(Users).where(Users.bot_id == BOT_ID, Users.is_delete == False)
            users = (await session.execute(stmt)).scalars().all()
        return sum(1 for user in users if user_last_visit(user) >= since)

    async def touch_user_activity(self, user_id: int) -> None:
        async with self.session_factory() as session:
            stmt = select(Users).where(_user_filter(user_id))
            user = (await session.execute(stmt)).scalar_one_or_none()
            if not user:
                return
            profile = parse_user_profile(user.field_str_2)
            profile["last_activity"] = datetime.now(timezone.utc).isoformat()
            await session.execute(
                update(Users).where(_user_filter(user_id)).values(
                    field_str_2=json.dumps(profile, ensure_ascii=False)
                )
            )
            await session.commit()

    async def count_active_subscriptions(self) -> int:
        async with self.session_factory() as session:
            stmt = select(Users).where(Users.bot_id == BOT_ID, Users.is_delete == False)
            users = (await session.execute(stmt)).scalars().all()
            return sum(1 for u in users if user_has_active_pro_subscription(u))

    async def count_trial_users(self) -> int:
        async with self.session_factory() as session:
            stmt = select(func.count()).select_from(Users).where(
                Users.bot_id == BOT_ID, Users.field_bool_3 == True
            )
            return (await session.execute(stmt)).scalar() or 0

    async def count_paid_users(self) -> int:
        async with self.session_factory() as session:
            stmt = select(func.count()).select_from(Users).where(
                Users.bot_id == BOT_ID, Users.reserve_field == True
            )
            return (await session.execute(stmt)).scalar() or 0

    async def sum_revenue(self) -> int:
        total = 0
        async with self.session_factory() as session:
            for model in (PaymentsFkSBP, PaymentsStars, PaymentsCryptobot):
                stmt = select(func.coalesce(func.sum(model.amount), 0)).where(
                    model.bot_id == BOT_ID, model.status.in_(("confirmed", "paid"))
                )
                val = (await session.execute(stmt)).scalar() or 0
                total += int(val)
        return total

    async def get_all_user_ids_for_broadcast(self) -> List[int]:
        async with self.session_factory() as session:
            stmt = select(Users.user_id).where(Users.bot_id == BOT_ID, Users.is_delete == False)
            return [r[0] for r in (await session.execute(stmt)).all()]

    async def get_users_not_in_panel(self) -> List[int]:
        async with self.session_factory() as session:
            stmt = select(Users.user_id).where(
                Users.bot_id == BOT_ID, Users.in_panel == False, Users.is_delete == False
            )
            return [r[0] for r in (await session.execute(stmt)).all()]

    async def get_users_not_connected(self) -> List[int]:
        async with self.session_factory() as session:
            stmt = select(Users.user_id).where(
                Users.bot_id == BOT_ID, Users.in_panel == True, Users.is_connect == False, Users.is_delete == False
            )
            return [r[0] for r in (await session.execute(stmt)).all()]

    async def alloc_fk_api_nonce(self) -> int:
        """Уникальный растущий nonce для FreeKassa API без отдельной таблицы."""
        return time.time_ns() // 1000

    async def add_fk_sbp_payment(
        self,
        user_id: int,
        amount: int,
        status: str,
        transaction_id: str,
        fk_order_id: int | None,
        payload: str,
        nonce: int,
        signature: str,
        is_gift: bool = False,
        method: str = "fk_qr_sbp",
    ) -> None:
        await self.insert_fk_payment(
            user_id=user_id,
            amount=amount,
            status=status,
            transaction_id=transaction_id,
            fk_order_id=fk_order_id,
            payload=payload,
            nonce=nonce,
            signature=signature,
            is_gift=is_gift,
            method=method,
        )

    async def insert_fk_payment(self, **kwargs) -> None:
        async with self.session_factory() as session:
            session.add(PaymentsFkSBP(bot_id=BOT_ID, **kwargs))
            await session.commit()

    async def add_payment_stars(self, user_id: int, amount: int, is_gift: bool, payload: str) -> None:
        await self.insert_stars_payment(
            user_id=user_id, amount=amount, is_gift=is_gift, payload=payload, status="confirmed"
        )

    async def insert_stars_payment(self, **kwargs) -> None:
        async with self.session_factory() as session:
            session.add(PaymentsStars(bot_id=BOT_ID, **kwargs))
            await session.commit()

    async def add_cryptobot_payment(
        self,
        user_id: int,
        amount: float,
        currency: str,
        is_gift: bool,
        invoice_id: str,
        payload: str,
        status: str = "active",
    ) -> None:
        await self.insert_cryptobot_payment(
            user_id=user_id,
            amount=amount,
            currency=currency,
            is_gift=is_gift,
            invoice_id=invoice_id,
            payload=payload,
            status=status,
        )

    async def insert_cryptobot_payment(self, **kwargs) -> None:
        async with self.session_factory() as session:
            session.add(PaymentsCryptobot(bot_id=BOT_ID, **kwargs))
            await session.commit()

    async def get_pending_fk_payments(self) -> List[PaymentsFkSBP]:
        async with self.session_factory() as session:
            stmt = select(PaymentsFkSBP).where(
                PaymentsFkSBP.bot_id == BOT_ID, PaymentsFkSBP.status == "pending"
            )
            return list((await session.execute(stmt)).scalars().all())

    async def update_fk_payment_status(self, payment_id: int, status: str) -> None:
        async with self.session_factory() as session:
            await session.execute(
                update(PaymentsFkSBP).where(PaymentsFkSBP.id == payment_id).values(status=status)
            )
            await session.commit()

    async def get_pending_cryptobot_payments(self) -> List[PaymentsCryptobot]:
        async with self.session_factory() as session:
            stmt = select(PaymentsCryptobot).where(
                PaymentsCryptobot.bot_id == BOT_ID, PaymentsCryptobot.status == "pending"
            )
            return list((await session.execute(stmt)).scalars().all())

    async def update_cryptobot_status(self, payment_id: int, status: str) -> None:
        async with self.session_factory() as session:
            await session.execute(
                update(PaymentsCryptobot).where(PaymentsCryptobot.id == payment_id).values(status=status)
            )
            await session.commit()

    async def count_pending_payments(self, user_id: int) -> int:
        count = 0
        async with self.session_factory() as session:
            for model in (PaymentsFkSBP, PaymentsCryptobot):
                stmt = select(func.count()).select_from(model).where(
                    model.bot_id == BOT_ID, model.user_id == user_id, model.status == "pending"
                )
                count += (await session.execute(stmt)).scalar() or 0
        return count

    async def save_online_stats(self, panel: int, active: int, pay: int, trial: int) -> None:
        async with self.session_factory() as session:
            session.add(
                Online(
                    bot_id=BOT_ID,
                    users_panel=panel,
                    users_active=active,
                    users_pay=pay,
                    users_trial=trial,
                )
            )
            await session.commit()

    async def get_pending_fk_sbp_payments(self) -> List[PaymentsFkSBP]:
        return await self.get_pending_fk_payments()

    async def update_fk_sbp_payment_status(self, transaction_id: str, status: str) -> None:
        async with self.session_factory() as session:
            await session.execute(
                update(PaymentsFkSBP)
                .where(
                    PaymentsFkSBP.bot_id == BOT_ID,
                    PaymentsFkSBP.transaction_id == transaction_id,
                )
                .values(status=status)
            )
    async def get_latest_online(self) -> Optional[Online]:
        async with self.session_factory() as session:
            stmt = (
                select(Online)
                .where(Online.bot_id == BOT_ID)
                .order_by(Online.online_date.desc())
                .limit(1)
            )
            return (await session.execute(stmt)).scalar_one_or_none()

    async def mark_notification_as_sent(self, user_id: int) -> None:
        async with self.session_factory() as session:
            utc_today = datetime.now(timezone.utc).date()
            await session.execute(
                update(Users)
                .where(_user_filter(user_id))
                .values(last_notification_date=utc_today)
            )
            await session.commit()

    async def update_field_str_1(self, user_id: int, value: Optional[str]) -> None:
        async with self.session_factory() as session:
            await session.execute(
                update(Users).where(_user_filter(user_id)).values(field_str_1=value)
            )
            await session.commit()

    async def select_all_users(self) -> List[int]:
        async with self.session_factory() as session:
            stmt = select(Users.user_id).where(Users.bot_id == BOT_ID, Users.is_delete == False)
            return [r[0] for r in (await session.execute(stmt)).all()]

    async def select_rows_for_subscription_expiry_push(
        self, now_utc_naive: datetime, window: timedelta
    ) -> List[Tuple[int, datetime, bool, Optional[str], str]]:
        w = window
        now = now_utc_naive

        def _window_or(col):
            active_7 = and_(
                col > now,
                col > now + timedelta(days=7) - w,
                col <= now + timedelta(days=7),
            )
            active_3 = and_(
                col > now,
                col > now + timedelta(days=3) - w,
                col <= now + timedelta(days=3),
            )
            active_1 = and_(
                col > now,
                col > now + timedelta(days=1) - w,
                col <= now + timedelta(days=1),
            )
            active_h = and_(
                col > now,
                col > now + timedelta(hours=1) - w,
                col <= now + timedelta(hours=1),
            )
            active_cond = or_(active_7, active_3, active_1, active_h)

            post_pn = []
            for n in range(1, 201):
                d = timedelta(days=3 * n)
                post_pn.append(
                    and_(
                        col <= now,
                        col > now - d - w,
                        col <= now - d,
                    )
                )
            expired_cond = or_(*post_pn)
            return or_(active_cond, expired_cond)

        user_bot = and_(Users.bot_id == BOT_ID, Users.is_delete == False)
        s_main = (
            select(
                Users.user_id,
                literal("main").label("tier"),
                Users.subscription_end_date.label("end_dt"),
            )
            .where(user_bot, Users.subscription_end_date.isnot(None))
        )
        s_3 = (
            select(
                Users.user_id,
                literal("3").label("tier"),
                Users.subscription_3_end_date.label("end_dt"),
            )
            .where(user_bot, Users.subscription_3_end_date.isnot(None))
        )
        s_10 = (
            select(
                Users.user_id,
                literal("10").label("tier"),
                Users.subscription_10_end_date.label("end_dt"),
            )
            .where(user_bot, Users.subscription_10_end_date.isnot(None))
        )
        uend = union_all(s_main, s_3, s_10).subquery("uend")
        tier_prio = case(
            (uend.c.tier == "main", 0),
            (uend.c.tier == "3", 1),
            (uend.c.tier == "10", 2),
            else_=9,
        )
        rn = func.row_number().over(
            partition_by=uend.c.user_id,
            order_by=(uend.c.end_dt.desc(), tier_prio.asc()),
        ).label("rn")
        ranked = select(uend.c.user_id, uend.c.tier, uend.c.end_dt, rn).subquery("ranked")
        best = (
            select(ranked.c.user_id, ranked.c.tier, ranked.c.end_dt)
            .where(ranked.c.rn == 1)
            .subquery("best")
        )

        cond = _window_or(best.c.end_dt)
        stmt = (
            select(
                Users.user_id,
                best.c.end_dt,
                Users.reserve_field,
                Users.field_str_1,
                best.c.tier,
            )
            .select_from(Users)
            .join(best, Users.user_id == best.c.user_id)
            .where(user_bot, cond)
            .order_by(Users.user_id)
        )

        rows_out: List[Tuple[int, datetime, bool, Optional[str], str]] = []
        async with self.session_factory() as session:
            result = await session.execute(stmt)
            for r in result.all():
                rows_out.append((r[0], r[1], bool(r[2]), r[3], r[4]))
        return rows_out

    async def get_active_cryptobot_payments(self) -> List[PaymentsCryptobot]:
        async with self.session_factory() as session:
            stmt = select(PaymentsCryptobot).where(
                PaymentsCryptobot.bot_id == BOT_ID,
                or_(
                    PaymentsCryptobot.status == "active",
                    PaymentsCryptobot.status == "pending",
                ),
            )
            return list((await session.execute(stmt)).scalars().all())

    async def update_cryptobot_payment_status(self, payment_id: int, status: str) -> None:
        await self.update_cryptobot_status(payment_id, status)

    async def count_open_payment_slots_for_user(self, user_id: int) -> int:
        uid = int(user_id)
        async with self.session_factory() as session:
            total = 0
            pairs = (
                (
                    PaymentsFkSBP,
                    and_(
                        PaymentsFkSBP.bot_id == BOT_ID,
                        PaymentsFkSBP.user_id == uid,
                        PaymentsFkSBP.status == "pending",
                    ),
                ),
                (
                    PaymentsCryptobot,
                    and_(
                        PaymentsCryptobot.bot_id == BOT_ID,
                        PaymentsCryptobot.user_id == uid,
                        or_(
                            PaymentsCryptobot.status == "active",
                            PaymentsCryptobot.status == "pending",
                        ),
                    ),
                ),
            )
            for model, cond in pairs:
                q = select(func.count()).select_from(model).where(cond)
                total += int((await session.execute(q)).scalar_one())
            return total

    async def user_ids_with_full_tariff_payment(self, user_ids: List[int]) -> Set[int]:
        if not user_ids:
            return set()
        minor_floor = PAYMENT_MINOR_THRESHOLD_RUB
        uniq = list({int(u) for u in user_ids})
        out: Set[int] = set()
        chunk_size = 400
        chunks = [uniq[i : i + chunk_size] for i in range(0, len(uniq), chunk_size)]
        async with self.session_factory() as session:
            for chunk in chunks:
                stmt_fk = select(PaymentsFkSBP.user_id).distinct().where(
                    PaymentsFkSBP.bot_id == BOT_ID,
                    PaymentsFkSBP.user_id.in_(chunk),
                    PaymentsFkSBP.status == "confirmed",
                    PaymentsFkSBP.is_gift == False,
                    PaymentsFkSBP.amount > minor_floor,
                    PaymentsFkSBP.amount != 1,
                )
                for (uid,) in (await session.execute(stmt_fk)).all():
                    out.add(int(uid))

                stmt_st = select(PaymentsStars.user_id).distinct().where(
                    PaymentsStars.bot_id == BOT_ID,
                    PaymentsStars.user_id.in_(chunk),
                    PaymentsStars.status == "confirmed",
                    PaymentsStars.is_gift == False,
                    PaymentsStars.amount > minor_floor,
                )
                for (uid,) in (await session.execute(stmt_st)).all():
                    out.add(int(uid))

                stmt_cr = select(PaymentsCryptobot.user_id, PaymentsCryptobot.amount).where(
                    PaymentsCryptobot.bot_id == BOT_ID,
                    PaymentsCryptobot.user_id.in_(chunk),
                    PaymentsCryptobot.status == "paid",
                    PaymentsCryptobot.is_gift == False,
                    PaymentsCryptobot.amount > 0.02,
                )
                for uid, amt in (await session.execute(stmt_cr)).all():
                    if float(amt) > minor_floor:
                        out.add(int(uid))
        return out

    async def add_online_stats(
        self, users_panel: int, users_active: int, users_pay: int, users_trial: int
    ) -> None:
        await self.save_online_stats(users_panel, users_active, users_pay, users_trial)

