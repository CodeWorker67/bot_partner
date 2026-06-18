import uuid
import time

from sqlalchemy import select, update, func, or_, and_, literal, union_all, case, delete, cast, Date
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from datetime import datetime, date, timezone, timedelta
from typing import Optional, List, Tuple, Dict, Any, Set

from config_bd.models import AsyncSessionLocal, Users, Payments, Gifts, PaymentsCryptobot, PaymentsStars, Online, \
    WhiteCounter, PaymentsCards, PaymentsPlategaCrypto, PaymentsWataSBP, PaymentsWataCard, PaymentsFkSBP, \
    LinkingCodes, PasswordResetCodes
from sqlalchemy.exc import IntegrityError
from lexicon import PAYMENT_MINOR_THRESHOLD_RUB, dct_price
from logging_config import logger
from tariff_resolve import tariff_days_for_x3

_CRYPTO_TARIFF_RUB = {
    'TON': {
        '0.9': 99, '1.9': 199, '2.5': 269, '2.8': 299, '3.4': 369, '3.9': 399, '4.6': 499, '6.5': 699,
    },
    'USDT': {
        '1.3': 99, '2.6': 199, '3.5': 269, '4.0': 299, '4.8': 369, '5.2': 399, '6.5': 499, '9.1': 699,
    },
}


def _cryptobot_payment_rub_equiv(currency: Optional[str], amount_str: str) -> int:
    if not currency:
        return 0
    return _CRYPTO_TARIFF_RUB.get(currency, {}).get(amount_str, 0)


# Пакетная обработка для /stat: меньше 999 — лимит переменных SQLite в одном запросе.
_STAT_IN_CHUNK = 900

_BILLING_OK_STATUSES = ("confirmed", "paid")

_MERGE_PAYMENT_MODELS = (
    Payments,
    PaymentsCards,
    PaymentsPlategaCrypto,
    PaymentsWataSBP,
    PaymentsWataCard,
    PaymentsFkSBP,
    PaymentsStars,
    PaymentsCryptobot,
)


def _payload_duration_to_panel_days(raw: Optional[str]) -> Optional[int]:
    if raw is None:
        return None
    s = str(raw).strip()
    if s == "30secret":
        return 30
    try:
        v = int(s)
        return v if v > 0 else None
    except (TypeError, ValueError):
        pass
    try:
        return tariff_days_for_x3(s)
    except (TypeError, ValueError):
        return None


def _white_days_from_amount_fallback(amount: Any) -> Optional[int]:
    try:
        target = int(round(float(amount)))
    except (TypeError, ValueError):
        return None
    for key, price in dct_price.items():
        if "white" not in key:
            continue
        if int(price) != target:
            continue
        if key == "white_30":
            return 30
    return None


def _billing_duration_from_amount_fallback(amount: Any) -> Optional[int]:
    try:
        target = int(round(float(amount)))
    except (TypeError, ValueError):
        return None
    if target == 1:
        return None
    candidates: list[int] = []
    for key, price in dct_price.items():
        if "white" in key:
            continue
        try:
            if int(price) != target:
                continue
            candidates.append(tariff_days_for_x3(key))
        except (TypeError, ValueError):
            continue
    return max(candidates) if candidates else None


def _norm_email(email: str) -> str:
    return email.strip().lower()


def _naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _user_tuple(user: Users) -> Tuple:
    return (
        user.id, user.user_id, user.ref, user.is_delete,
        user.in_panel, user.is_connect, user.create_user,
        user.in_chanel, user.reserve_field, user.subscription_end_date,
        user.white_subscription_end_date, user.last_notification_date,
        user.last_broadcast_status, user.last_broadcast_date,
        user.stamp, user.ttclid,
        user.subscribtion, user.white_subscription, user.email,
        user.password, user.activation_pass,
        user.field_str_1, user.field_str_2, user.field_str_3,
        user.field_bool_1, user.field_bool_2, user.field_bool_3,
        user.partner, user.partner_balance, user.partner_pay, user.partner_flag,
        user.password_hash, user.linked_telegram_id,
    )


def _users_column_value_for_api(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return str(v)
    return v


def user_row_to_api_dict(user: Users) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for col in Users.__table__.columns:
        out[col.key] = _users_column_value_for_api(getattr(user, col.key))
    return out


def _sum_subscription_end_dates(
    a: Optional[datetime], b: Optional[datetime], now: datetime
) -> Optional[datetime]:
    if a is None and b is None:
        return None
    now_n = _naive_utc(now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc))

    def rem(dt: Optional[datetime]) -> timedelta:
        if dt is None:
            return timedelta(0)
        n = _naive_utc(dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt)
        return max(timedelta(0), n - now_n)

    total = rem(a) + rem(b)
    if total == timedelta(0):
        if a is None:
            return _naive_utc(b) if b is not None else None
        if b is None:
            return _naive_utc(a) if a is not None else None
        return max(_naive_utc(a), _naive_utc(b))
    return now_n + total


def pro_subscription_end_active(end_dt: Optional[datetime]) -> bool:
    """PRO-подписка (3/5/10 устройств) активна по календарному дню UTC."""
    if end_dt is None:
        return False
    if end_dt.tzinfo is None:
        aware = end_dt.replace(tzinfo=timezone.utc)
    else:
        aware = end_dt.astimezone(timezone.utc)
    return aware.date() >= datetime.now(timezone.utc).date()


def user_has_active_pro_subscription(user: Users) -> bool:
    """Есть ли активная PRO-подписка хотя бы на одном тарифе (3, 5 или 10 устройств)."""
    return any(
        pro_subscription_end_active(dt)
        for dt in (
            user.subscription_end_date,
            user.subscription_3_end_date,
            user.subscription_10_end_date,
        )
    )


def resolve_trial_device_slots(user: Users) -> int:
    """
    Слот для +7 дней триала:
    — нет PRO-подписок → 5 устройств;
    — есть просроченные → тариф с максимальным числом устройств среди просроченных.
    """
    tiers = (
        (5, user.subscription_end_date),
        (3, user.subscription_3_end_date),
        (10, user.subscription_10_end_date),
    )
    expired = [slots for slots, dt in tiers if dt is not None and not pro_subscription_end_active(dt)]
    if not expired:
        return 5
    return max(expired)


def _max_subscription_end_dates(
    a: Optional[datetime], b: Optional[datetime], now: datetime
) -> Optional[datetime]:
    if a is None and b is None:
        return None

    def norm(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return _naive_utc(dt)
        return _naive_utc(dt.astimezone(timezone.utc).replace(tzinfo=None))

    vals: List[datetime] = []
    if a is not None:
        vals.append(norm(a))
    if b is not None:
        vals.append(norm(b))
    return max(vals)


def _payload_white_flag(payload: Optional[str]) -> bool:
    if not payload or not str(payload).strip():
        return False
    try:
        parts = dict(item.split(":", 1) for item in str(payload).split(","))
    except ValueError:
        return False
    return parts.get("white", "False") == "True"


async def _merge_user_paid_subscription_flags(session, user_id: int) -> Tuple[bool, bool]:
    has_pro = False
    has_white = False
    for model in _MERGE_PAYMENT_MODELS:
        if has_pro and has_white:
            break
        stmt = select(model.payload).where(
            model.user_id == user_id,
            model.is_gift.is_(False),
            model.status.in_(_BILLING_OK_STATUSES),
        )
        result = await session.execute(stmt)
        for (payload,) in result.all():
            if _payload_white_flag(payload):
                has_white = True
            else:
                has_pro = True
            if has_pro and has_white:
                break
    return has_pro, has_white


class AsyncSQL:
    def __init__(self):
        self.session_factory = AsyncSessionLocal

    async def get_user(self, user_id: int) -> Optional[Tuple]:
        async with self.session_factory() as session:
            stmt = select(Users).where(Users.user_id == user_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            if user:
                return _user_tuple(user)
            return None

    async def get_user_by_internal_id(self, internal_id: int) -> Optional[Tuple]:
        async with self.session_factory() as session:
            user = await session.get(Users, internal_id)
            if user:
                return _user_tuple(user)
            return None

    async def get_user_by_email(self, email: str) -> Optional[Tuple]:
        em = _norm_email(email)
        async with self.session_factory() as session:
            stmt = select(Users).where(func.lower(Users.email) == em)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            if user:
                return _user_tuple(user)
            return None

    async def get_user_object_by_internal_id(self, internal_id: int) -> Optional[Users]:
        async with self.session_factory() as session:
            return await session.get(Users, internal_id)

    async def get_user_object_by_user_id(self, user_id: int) -> Optional[Users]:
        async with self.session_factory() as session:
            stmt = select(Users).where(Users.user_id == user_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def user_ids_with_full_tariff_payment(self, user_ids: List[int]) -> Set[int]:
        """
        Пользователи с подтверждённым не-подарочным платежом дороже порога мелкой суммы (10 ₽ / 10 XTR).
        Платежи только на уровне этого порога сюда не входят.
        """
        if not user_ids:
            return set()
        minor_floor = PAYMENT_MINOR_THRESHOLD_RUB
        uniq = list({int(u) for u in user_ids})
        out: Set[int] = set()
        chunks = [uniq[i : i + _STAT_IN_CHUNK] for i in range(0, len(uniq), _STAT_IN_CHUNK)]
        async with self.session_factory() as session:
            for chunk in chunks:
                stmt_p = select(Payments.user_id).distinct().where(
                    Payments.user_id.in_(chunk),
                    Payments.status == 'confirmed',
                    Payments.is_gift == False,
                    Payments.amount > minor_floor,
                    Payments.amount != 1,
                )
                for (uid,) in (await session.execute(stmt_p)).all():
                    out.add(int(uid))

                stmt_cards = select(PaymentsCards.user_id).distinct().where(
                    PaymentsCards.user_id.in_(chunk),
                    PaymentsCards.status == 'confirmed',
                    PaymentsCards.is_gift == False,
                    PaymentsCards.amount > minor_floor,
                    PaymentsCards.amount != 1,
                )
                for (uid,) in (await session.execute(stmt_cards)).all():
                    out.add(int(uid))

                stmt_pc = select(PaymentsPlategaCrypto.user_id).distinct().where(
                    PaymentsPlategaCrypto.user_id.in_(chunk),
                    PaymentsPlategaCrypto.status == 'confirmed',
                    PaymentsPlategaCrypto.is_gift == False,
                    PaymentsPlategaCrypto.amount > minor_floor,
                    PaymentsPlategaCrypto.amount != 1,
                )
                for (uid,) in (await session.execute(stmt_pc)).all():
                    out.add(int(uid))

                stmt_ws = select(PaymentsWataSBP.user_id).distinct().where(
                    PaymentsWataSBP.user_id.in_(chunk),
                    PaymentsWataSBP.status == 'confirmed',
                    PaymentsWataSBP.is_gift == False,
                    PaymentsWataSBP.amount > minor_floor,
                    PaymentsWataSBP.amount != 1,
                )
                for (uid,) in (await session.execute(stmt_ws)).all():
                    out.add(int(uid))

                stmt_wc = select(PaymentsWataCard.user_id).distinct().where(
                    PaymentsWataCard.user_id.in_(chunk),
                    PaymentsWataCard.status == 'confirmed',
                    PaymentsWataCard.is_gift == False,
                    PaymentsWataCard.amount > minor_floor,
                    PaymentsWataCard.amount != 1,
                )
                for (uid,) in (await session.execute(stmt_wc)).all():
                    out.add(int(uid))

                stmt_fk = select(PaymentsFkSBP.user_id).distinct().where(
                    PaymentsFkSBP.user_id.in_(chunk),
                    PaymentsFkSBP.status == 'confirmed',
                    PaymentsFkSBP.is_gift == False,
                    PaymentsFkSBP.amount > minor_floor,
                    PaymentsFkSBP.amount != 1,
                )
                for (uid,) in (await session.execute(stmt_fk)).all():
                    out.add(int(uid))

                stmt_st = select(PaymentsStars.user_id).distinct().where(
                    PaymentsStars.user_id.in_(chunk),
                    PaymentsStars.status == 'confirmed',
                    PaymentsStars.is_gift == False,
                    PaymentsStars.amount > minor_floor,
                )
                for (uid,) in (await session.execute(stmt_st)).all():
                    out.add(int(uid))

                stmt_cr = select(
                    PaymentsCryptobot.user_id,
                    PaymentsCryptobot.amount,
                    PaymentsCryptobot.currency,
                ).where(
                    PaymentsCryptobot.user_id.in_(chunk),
                    PaymentsCryptobot.status == 'paid',
                    PaymentsCryptobot.is_gift == False,
                    PaymentsCryptobot.amount > 0.02,
                )
                for uid, amt, cur in (await session.execute(stmt_cr)).all():
                    rub = _cryptobot_payment_rub_equiv(cur, str(amt))
                    if rub > minor_floor:
                        out.add(int(uid))
        return out

    async def add_user(self, user_id: int, in_panel: bool, is_connect: bool = False,
                     ref: str = '', is_delete: bool = False, in_chanel: bool = False,
                     stamp='', partner: str = '') -> bool:
        """Возвращает True, если пользователь был вставлен; False если уже существовал (гонки /start)."""
        async with self.session_factory() as session:
            stmt = sqlite_insert(Users).values(
                user_id=user_id,
                ref=ref or None,
                partner=partner or None,
                is_delete=is_delete,
                in_panel=in_panel,
                is_connect=is_connect,
                in_chanel=in_chanel,
                stamp=stamp,
            ).on_conflict_do_nothing(index_elements=[Users.user_id])
            try:
                result = await session.execute(stmt)
                await session.commit()
                return (result.rowcount or 0) > 0
            except Exception as e:
                await session.rollback()
                logger.error(f"Error inserting user {user_id}: {e}")
                return False

    async def update_in_panel(self, user_id: int):
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.user_id == user_id).values(in_panel=True)
            await session.execute(stmt)
            await session.commit()

    async def update_in_chanel(self, user_id: int, booly: bool):
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.user_id == user_id).values(in_chanel=booly)
            await session.execute(stmt)
            await session.commit()

    async def update_is_connect(self, user_id: int, booly: bool):
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.user_id == user_id).values(is_connect=booly)
            await session.execute(stmt)
            await session.commit()

    async def update_ttclid(self, user_id: int, ttclid: str):
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.user_id == user_id).values(ttclid=ttclid)
            await session.execute(stmt)
            await session.commit()

    async def try_set_ref_from_invite(self, user_id: int, ref: str) -> bool:
        if not str(ref).strip():
            return False
        async with self.session_factory() as session:
            stmt = (
                update(Users)
                .where(
                    Users.user_id == user_id,
                    or_(Users.ref.is_(None), Users.ref == ''),
                )
                .values(ref=str(ref))
            )
            result = await session.execute(stmt)
            await session.commit()
            return (result.rowcount or 0) > 0

    async def try_set_stamp_from_invite(self, user_id: int, stamp: str) -> bool:
        if not str(stamp).strip():
            return False
        async with self.session_factory() as session:
            stmt = (
                update(Users)
                .where(
                    Users.user_id == user_id,
                    or_(Users.stamp.is_(None), Users.stamp == ''),
                )
                .values(stamp=str(stamp))
            )
            result = await session.execute(stmt)
            await session.commit()
            return (result.rowcount or 0) > 0

    async def update_reserve_field(self, user_id: int):
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.user_id == user_id).values(reserve_field=True)
            await session.execute(stmt)
            await session.commit()

    async def update_delete(self, user_id: int, booly: bool):
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.user_id == user_id).values(is_delete=booly)
            await session.execute(stmt)
            await session.commit()

    async def select_ref_count(self, user_id: int) -> int:
        async with self.session_factory() as session:
            stmt = select(func.count(Users.user_id)).where(Users.ref == str(user_id))
            result = await session.execute(stmt)
            return result.scalar() or 0

    async def select_partner_count(self, partner_id: int) -> int:
        async with self.session_factory() as session:
            stmt = select(func.count(Users.user_id)).where(Users.partner == str(partner_id))
            result = await session.execute(stmt)
            return result.scalar() or 0

    async def select_partner_referrals_payments_sum(self, partner_id: int) -> int:
        async with self.session_factory() as session:
            stmt = select(Users.user_id).where(Users.partner == str(partner_id))
            result = await session.execute(stmt)
            user_ids = [row[0] for row in result.all()]
            if not user_ids:
                return 0

            total = 0
            for i in range(0, len(user_ids), _STAT_IN_CHUNK):
                chunk = user_ids[i : i + _STAT_IN_CHUNK]
                for model in _MERGE_PAYMENT_MODELS:
                    stmt_sum = select(func.coalesce(func.sum(model.amount), 0)).where(
                        model.user_id.in_(chunk),
                        model.status.in_(_BILLING_OK_STATUSES),
                    )
                    val = (await session.execute(stmt_sum)).scalar() or 0
                    total += int(val)
            return total

    async def update_partner_flag(self, user_id: int, flag: bool = True) -> None:
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.user_id == user_id).values(partner_flag=flag)
            await session.execute(stmt)
            await session.commit()

    async def add_partner_balance(self, partner_user_id: int, amount: int) -> bool:
        if amount <= 0:
            return False
        async with self.session_factory() as session:
            stmt = (
                update(Users)
                .where(Users.user_id == partner_user_id)
                .values(partner_balance=func.coalesce(Users.partner_balance, 0) + amount)
            )
            result = await session.execute(stmt)
            await session.commit()
            return (result.rowcount or 0) > 0

    async def partner_record_payout(self, partner_user_id: int, amount: int) -> Tuple[bool, str]:
        if amount <= 0:
            return False, "Сумма должна быть больше 0"
        async with self.session_factory() as session:
            stmt = select(Users).where(Users.user_id == partner_user_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            if user is None:
                return False, "Пользователь не найден"
            balance = user.partner_balance or 0
            if balance < amount:
                return False, f"Недостаточно на балансе: {balance} ₽, запрошено {amount} ₽"
            user.partner_balance = balance - amount
            user.partner_pay = (user.partner_pay or 0) + amount
            await session.commit()
            return True, ""

    async def update_subscription_end_date(self, user_id: int, end_date: datetime):
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.user_id == user_id).values(subscription_end_date=end_date)
            await session.execute(stmt)
            await session.commit()

    async def update_white_subscription_end_date(self, user_id: int, end_date: datetime):
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.user_id == user_id).values(white_subscription_end_date=end_date)
            await session.execute(stmt)
            await session.commit()

    async def update_subscribtion(self, user_id: int, subscribtion: Optional[str]):
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.user_id == user_id).values(subscribtion=subscribtion)
            await session.execute(stmt)
            await session.commit()

    async def update_subscription_3_end_date(self, user_id: int, end_date: datetime):
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.user_id == user_id).values(subscription_3_end_date=end_date)
            await session.execute(stmt)
            await session.commit()

    async def update_subscribtion_3(self, user_id: int, subscribtion: Optional[str]):
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.user_id == user_id).values(subscribtion_3=subscribtion)
            await session.execute(stmt)
            await session.commit()

    async def update_subscription_10_end_date(self, user_id: int, end_date: datetime):
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.user_id == user_id).values(subscription_10_end_date=end_date)
            await session.execute(stmt)
            await session.commit()

    async def update_subscribtion_10(self, user_id: int, subscribtion: Optional[str]):
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.user_id == user_id).values(subscribtion_10=subscribtion)
            await session.execute(stmt)
            await session.commit()

    async def update_white_subscription(self, user_id: int, white_subscription: Optional[str]):
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.user_id == user_id).values(white_subscription=white_subscription)
            await session.execute(stmt)
            await session.commit()

    async def get_subscription_end_date(self, user_id: int) -> Optional[datetime]:
        async with self.session_factory() as session:
            stmt = select(Users.subscription_end_date).where(Users.user_id == user_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def notification_sent_today(self, user_id: int) -> bool:
        async with self.session_factory() as session:
            stmt = select(Users.last_notification_date).where(Users.user_id == user_id)
            result = await session.execute(stmt)
            last = result.scalar_one_or_none()
            today = date.today()
            if last:
                if isinstance(last, datetime):
                    last = last.date()
                return last == today
            return False

    async def mark_notification_as_sent(self, user_id: int):
        async with self.session_factory() as session:
            utc_today = datetime.now(timezone.utc).date()
            stmt = update(Users).where(Users.user_id == user_id).values(last_notification_date=utc_today)
            await session.execute(stmt)
            await session.commit()

    async def update_field_str_1(self, user_id: int, value: Optional[str]):
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.user_id == user_id).values(field_str_1=value)
            await session.execute(stmt)
            await session.commit()

    async def update_field_bool_3(self, user_id: int, value: bool):
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.user_id == user_id).values(field_bool_3=value)
            await session.execute(stmt)
            await session.commit()

    async def reset_field_bool_3_all(self) -> int:
        """Всем строкам users: field_bool_3 = False. Возвращает число обновлённых записей."""
        async with self.session_factory() as session:
            result = await session.execute(update(Users).values(field_bool_3=False))
            await session.commit()
            return int(result.rowcount or 0)

    async def SELECT_USER_IDS_NO_ACTIVE_PRO_SUBSCRIPTION(self) -> List[int]:
        """
        Не удалены; для каждого тарифа PRO (3/5/10 устройств):
        дата пуста (нет подписки) или окончание не позже чем 2 календарных дня назад (UTC).
        """
        today_utc = datetime.now(timezone.utc).date()
        cutoff = today_utc - timedelta(days=2)

        def _tier_eligible(col):
            return or_(col.is_(None), cast(col, Date) <= cutoff)

        async with self.session_factory() as session:
            stmt = (
                select(Users.user_id)
                .where(
                    Users.is_delete == False,
                    _tier_eligible(Users.subscription_end_date),
                    _tier_eligible(Users.subscription_3_end_date),
                    _tier_eligible(Users.subscription_10_end_date),
                )
                .order_by(Users.user_id)
            )
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]

    async def get_last_notification_date(self, user_id: int) -> Optional[date]:
        async with self.session_factory() as session:
            stmt = select(Users.last_notification_date).where(Users.user_id == user_id)
            result = await session.execute(stmt)
            val = result.scalar_one_or_none()
            if isinstance(val, datetime):
                return val.date()
            return val

    async def update_broadcast_status(self, user_id: int, status: str):
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.user_id == user_id).values(
                last_broadcast_status=status,
                last_broadcast_date=datetime.now()
            )
            await session.execute(stmt)
            await session.commit()

    async def select_all_users(self) -> List[int]:
        async with self.session_factory() as session:
            stmt = select(Users.user_id).where(Users.is_delete == False)
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]

    async def select_rows_for_subscription_expiry_push(
        self, now_utc_naive: datetime, window: timedelta
    ) -> List[Tuple[int, datetime, bool, Optional[str], str]]:
        """
        Строки для sheduler.time_mes: user_id, дата окончания ведущей подписки,
        reserve_field, field_str_1, tier ('main'|'3'|'10'|'white').

        Ведущая подписка — с самой поздней датой окончания среди непустых полей
        subscription_end_date, subscription_3_end_date, subscription_10_end_date,
        white_subscription_end_date. При равной дате: main, затем 3, 10, white.
        Пуши считаются только по ней (одна строка на пользователя).
        """
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

        s_main = (
            select(
                Users.user_id,
                literal("main").label("tier"),
                Users.subscription_end_date.label("end_dt"),
            )
            .where(
                Users.is_delete == False,
                Users.subscription_end_date.isnot(None),
            )
        )
        s_3 = (
            select(
                Users.user_id,
                literal("3").label("tier"),
                Users.subscription_3_end_date.label("end_dt"),
            )
            .where(
                Users.is_delete == False,
                Users.subscription_3_end_date.isnot(None),
            )
        )
        s_10 = (
            select(
                Users.user_id,
                literal("10").label("tier"),
                Users.subscription_10_end_date.label("end_dt"),
            )
            .where(
                Users.is_delete == False,
                Users.subscription_10_end_date.isnot(None),
            )
        )
        s_white = (
            select(
                Users.user_id,
                literal("white").label("tier"),
                Users.white_subscription_end_date.label("end_dt"),
            )
            .where(
                Users.is_delete == False,
                Users.white_subscription_end_date.isnot(None),
            )
        )
        uend = union_all(s_main, s_3, s_10, s_white).subquery("uend")
        tier_prio = case(
            (uend.c.tier == "main", 0),
            (uend.c.tier == "3", 1),
            (uend.c.tier == "10", 2),
            (uend.c.tier == "white", 3),
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
            .where(Users.is_delete == False, cond)
            .order_by(Users.user_id)
        )

        rows_out: List[Tuple[int, datetime, bool, Optional[str], str]] = []
        async with self.session_factory() as session:
            result = await session.execute(stmt)
            for r in result.all():
                rows_out.append((r[0], r[1], bool(r[2]), r[3], r[4]))

        return rows_out

    async def select_not_connected_subscribe_yes(self) -> List[int]:
        async with self.session_factory() as session:
            current_time = datetime.now()
            stmt = select(Users.user_id).where(
                Users.in_panel == True,
                Users.is_connect == False,
                Users.is_delete == False,
                Users.subscription_end_date > current_time
            )
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]

    async def select_not_connected_subscribe_off(self):
        async with self.session_factory() as session:
            current_time = datetime.now()
            stmt = select(Users.user_id).where(
                Users.in_panel == True,
                Users.is_connect == False,
                Users.is_delete == False,
                (Users.subscription_end_date < current_time) |
                (Users.subscription_end_date.is_(None))
            )
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]

    async def select_connected_subscribe_off(self):
        async with self.session_factory() as session:
            current_time = datetime.now()
            stmt = select(Users.user_id).where(
                Users.in_panel == True,
                Users.is_connect == True,
                Users.is_delete == False,
                (Users.subscription_end_date < current_time) |
                (Users.subscription_end_date.is_(None))
            )
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]

    async def select_connected_subscribe_yes(self):
        async with self.session_factory() as session:
            current_time = datetime.now()
            stmt = select(Users.user_id).where(
                Users.in_panel == True,
                Users.is_connect == True,
                Users.is_delete == False,
                Users.subscription_end_date > current_time
            )
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]

    async def select_subscribe_off(self):
        async with self.session_factory() as session:
            stmt = select(Users.user_id).where(
                Users.in_panel == False,
                Users.is_connect == False,
                Users.is_delete == False
            )
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]


    async def select_subscribe_yes(self):
        async with self.session_factory() as session:
            stmt = select(Users.user_id).where(
                Users.in_panel == True,
                Users.is_delete == False
            )
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]


    async def select_connected_never_paid(self) -> List[int]:
        """
        Возвращает список user_id, у которых is_tarif=True, is_delete=False,
        и нет ни одной успешной оплаты (статус 'confirmed' в Payments или PaymentsStars,
        или статус 'paid' в PaymentsCryptobot).
        """
        async with self.session_factory() as session:
            # Подзапрос: все пользователи с успешными платежами
            today = datetime.now().date()
            paid_subq = (
                select(Payments.user_id)
                .where(Payments.status == 'confirmed')
                .union(
                    select(PaymentsStars.user_id).where(PaymentsStars.status == 'confirmed'),
                    select(PaymentsCryptobot.user_id).where(PaymentsCryptobot.status == 'paid'),
                    select(PaymentsCards.user_id).where(PaymentsCards.status == 'confirmed'),
                    select(PaymentsPlategaCrypto.user_id).where(PaymentsPlategaCrypto.status == 'confirmed'),
                    select(PaymentsWataSBP.user_id).where(PaymentsWataSBP.status == 'confirmed'),
                    select(PaymentsWataCard.user_id).where(PaymentsWataCard.status == 'confirmed'),
                    select(PaymentsFkSBP.user_id).where(PaymentsFkSBP.status == 'confirmed'),
                )
                .subquery()
            )
            stmt = select(Users.user_id).where(
                Users.is_connect == True,
                Users.is_delete == False,
                Users.user_id.notin_(paid_subq)
            )
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]

    def _build_broadcast_where(self, category: str, exclude_today: bool):
        """
        Условие выборки пользователей для рассылки.
        exclude_today: только те, у кого last_broadcast_date пусто или дата (UTC) не сегодня.
        """
        current_time = datetime.now()
        today_d = datetime.now(timezone.utc).date()
        skip_today_cond = or_(
            Users.last_broadcast_date.is_(None),
            func.date(Users.last_broadcast_date) != today_d,
        )

        def wrap(base):
            return and_(base, skip_today_cond) if exclude_today else base

        if category == "all_users":
            return wrap(Users.is_delete == False)
        if category == "not_connected_subscribe_yes":
            return wrap(
                and_(
                    Users.in_panel == True,
                    Users.is_connect == False,
                    Users.is_delete == False,
                    Users.subscription_end_date > current_time,
                )
            )
        if category == "not_connected_subscribe_off":
            return wrap(
                and_(
                    Users.in_panel == True,
                    Users.is_connect == False,
                    Users.is_delete == False,
                    or_(
                        Users.subscription_end_date < current_time,
                        Users.subscription_end_date.is_(None),
                    ),
                )
            )
        if category == "connected_subscribe_off":
            return wrap(
                and_(
                    Users.in_panel == True,
                    Users.is_connect == True,
                    Users.is_delete == False,
                    or_(
                        Users.subscription_end_date < current_time,
                        Users.subscription_end_date.is_(None),
                    ),
                )
            )
        if category == "connected_subscribe_yes":
            return wrap(
                and_(
                    Users.in_panel == True,
                    Users.is_connect == True,
                    Users.is_delete == False,
                    Users.subscription_end_date > current_time,
                )
            )
        if category == "not_subscribed":
            return wrap(
                and_(
                    Users.in_panel == False,
                    Users.is_connect == False,
                    Users.is_delete == False,
                )
            )
        if category == "connected_never_paid":
            paid_subq = (
                select(Payments.user_id)
                .where(Payments.status == "confirmed")
                .union(
                    select(PaymentsStars.user_id).where(PaymentsStars.status == "confirmed"),
                    select(PaymentsCryptobot.user_id).where(PaymentsCryptobot.status == "paid"),
                    select(PaymentsCards.user_id).where(PaymentsCards.status == "confirmed"),
                    select(PaymentsPlategaCrypto.user_id).where(PaymentsPlategaCrypto.status == "confirmed"),
                    select(PaymentsWataSBP.user_id).where(PaymentsWataSBP.status == "confirmed"),
                    select(PaymentsWataCard.user_id).where(PaymentsWataCard.status == "confirmed"),
                    select(PaymentsFkSBP.user_id).where(PaymentsFkSBP.status == "confirmed"),
                )
                .subquery()
            )
            return wrap(
                and_(
                    Users.is_connect == True,
                    Users.is_delete == False,
                    Users.user_id.notin_(paid_subq),
                )
            )
        if category == "subscribed_all":
            return wrap(
                and_(
                    Users.in_panel == True,
                    Users.subscription_end_date != None,
                    Users.is_delete == False,
                )
            )
        return None

    async def count_users_for_broadcast(self, category: str, exclude_today: bool) -> int:
        where_clause = self._build_broadcast_where(category, exclude_today)
        if where_clause is None:
            return 0
        async with self.session_factory() as session:
            stmt = select(func.count()).select_from(Users).where(where_clause)
            return int((await session.execute(stmt)).scalar_one())

    async def select_user_ids_for_broadcast(self, category: str, exclude_today: bool) -> List[int]:
        where_clause = self._build_broadcast_where(category, exclude_today)
        if where_clause is None:
            return []
        async with self.session_factory() as session:
            stmt = select(Users.user_id).where(where_clause)
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]

    async def select_subscribed_not_in_chanel(self):
        async with self.session_factory() as session:
            # Подзапрос: все пользователи с успешными платежами
            stmt = select(Users.user_id).where(
                Users.in_panel == True,
                Users.subscription_end_date == None,
                Users.is_delete == False
            )
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]

    async def select_user_by_parameter(self, parameter: str, value: str) -> List[int]:
        """
        Возвращает список user_id, у которых значение указанного параметра равно value.
        Допустимые параметры: 'Ref', 'Is_pay_null', 'stamp'.
        """
        # Маппинг имён параметров на атрибуты модели
        param_map = {
            'ref': Users.ref,
            'in_panel': Users.in_panel,
            'stamp': Users.stamp,
        }
        if parameter not in param_map:
            logger.info(f"Invalid parameter: {parameter}")
            return []

        attr = param_map[parameter]

        # Преобразование значения для булевых полей
        if parameter == 'in_panel':
            try:
                val = bool(int(value))
            except ValueError:
                logger.error(f"Invalid value type for parameter {parameter}: {value}")
                return []
        else:
            val = value

        async with self.session_factory() as session:
            stmt = select(Users.user_id).where(attr == val)
            result = await session.execute(stmt)
            rows = result.all()
            logger.info(f"Query result for parameter '{parameter}' with value '{value}': {len(rows)}")
            return [row[0] for row in rows]

    async def get_stat_by_ref_or_stamp(self, arg: str) -> Tuple[Optional[int], Optional[int], Optional[int], Optional[int], Optional[int], Optional[str]]:
        """
        Возвращает статистику по пользователям, у которых Ref == arg,
        если таких нет – по пользователям с stamp == arg.
        total_payments — сумма в ₽ по всем подтверждённым каналам: Payments, карты, Platega crypto,
        WATA СБП/карта, Stars (сумма amount 1:1), Cryptobot (по таблице тарифов).
        Без уполовинивания итога.
        Возвращает (total, with_sub, with_tarif, with_tarif_not_blocked, total_payments, source)
        или (None, None, None, None, None, None) если нет совпадений.
        """
        # 1. Ищем по Ref
        users = await self.select_user_by_parameter('ref', arg)
        source = 'ref'
        if not users:
            # 2. Ищем по stamp
            users = await self.select_user_by_parameter('stamp', arg)
            source = 'stamp'

        if not users:
            return None, None, None, None, None, None

        total = len(users)
        with_sub = 0
        with_tarif = 0
        with_tarif_not_blocked = 0
        total_payments = 0

        async with self.session_factory() as session:
            for i in range(0, len(users), _STAT_IN_CHUNK):
                chunk = users[i : i + _STAT_IN_CHUNK]
                stmt_users = select(
                    Users.subscription_end_date,
                    Users.is_connect,
                    Users.is_delete,
                ).where(Users.user_id.in_(chunk))
                result = await session.execute(stmt_users)
                for sub_end, is_connect, is_delete in result.all():
                    if sub_end is not None:
                        with_sub += 1
                    if is_connect:
                        with_tarif += 1
                    if is_connect and not is_delete:
                        with_tarif_not_blocked += 1

            with_tarif //= 2
            with_tarif_not_blocked //= 2

            for i in range(0, len(users), _STAT_IN_CHUNK):
                chunk = users[i : i + _STAT_IN_CHUNK]
                stmt_pay = select(func.coalesce(func.sum(Payments.amount), 0)).where(
                    Payments.user_id.in_(chunk),
                    Payments.status == 'confirmed',
                )
                total_payments += (await session.execute(stmt_pay)).scalar() or 0
                stmt_wata_sbp = select(func.coalesce(func.sum(PaymentsWataSBP.amount), 0)).where(
                    PaymentsWataSBP.user_id.in_(chunk),
                    PaymentsWataSBP.status == 'confirmed',
                )
                total_payments += (await session.execute(stmt_wata_sbp)).scalar() or 0
                stmt_wata_card = select(func.coalesce(func.sum(PaymentsWataCard.amount), 0)).where(
                    PaymentsWataCard.user_id.in_(chunk),
                    PaymentsWataCard.status == 'confirmed',
                )
                total_payments += (await session.execute(stmt_wata_card)).scalar() or 0

                stmt_fk = select(func.coalesce(func.sum(PaymentsFkSBP.amount), 0)).where(
                    PaymentsFkSBP.user_id.in_(chunk),
                    PaymentsFkSBP.status == 'confirmed',
                )
                total_payments += (await session.execute(stmt_fk)).scalar() or 0

                stmt_cards = select(func.coalesce(func.sum(PaymentsCards.amount), 0)).where(
                    PaymentsCards.user_id.in_(chunk),
                    PaymentsCards.status == 'confirmed',
                )
                total_payments += (await session.execute(stmt_cards)).scalar() or 0

                stmt_platega = select(func.coalesce(func.sum(PaymentsPlategaCrypto.amount), 0)).where(
                    PaymentsPlategaCrypto.user_id.in_(chunk),
                    PaymentsPlategaCrypto.status == 'confirmed',
                )
                total_payments += (await session.execute(stmt_platega)).scalar() or 0

                stmt_stars = select(func.coalesce(func.sum(PaymentsStars.amount), 0)).where(
                    PaymentsStars.user_id.in_(chunk),
                    PaymentsStars.status == 'confirmed',
                )
                total_payments += (await session.execute(stmt_stars)).scalar() or 0

                stmt_cryptobot = select(PaymentsCryptobot.amount, PaymentsCryptobot.currency).where(
                    PaymentsCryptobot.user_id.in_(chunk),
                    PaymentsCryptobot.status == 'paid',
                )
                for amt, cur in (await session.execute(stmt_cryptobot)).all():
                    total_payments += _cryptobot_payment_rub_equiv(cur, str(amt))

        return total, with_sub, with_tarif, with_tarif_not_blocked, total_payments, source

    def get_parameters(self) -> List[str]:
        """Ключи сегментов (в т.ч. для /broadcast): совпадают с категориями рассылки."""
        return [
            "not_connected_subscribe_yes",
            "not_connected_subscribe_off",
            "connected_subscribe_off",
            "connected_subscribe_yes",
            "not_subscribed",
            "connected_never_paid",
            "subscribed_all",
            "all_users",
        ]

    async def delete_from_db(self, user_id: int) -> bool:
        """Полностью удаляет пользователя из БД по User_id."""
        async with self.session_factory() as session:
            stmt = select(Users).where(Users.user_id == user_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            if not user:
                logger.warning(f"User {user_id} not found for deletion")
                return False
            await session.delete(user)
            await session.commit()
            logger.info(f"✅ Удалено пользователей: 1 (User_id: {user_id})")
            return True

    async def reset_all_delete_flag(self) -> int:
        """Устанавливает Is_delete = False для всех записей в таблице users."""
        async with self.session_factory() as session:
            stmt = update(Users).values(is_delete=False)
            result = await session.execute(stmt)
            await session.commit()
            updated = result.rowcount
            logger.info(f"✅ Сброшен флаг Is_delete для {updated} пользователей")
            return updated

    async def get_users_with_confirmed_payments(self, user_ids: Optional[List[int]] = None) -> List[int]:
        """
        Возвращает список user_id, у которых есть хотя бы один платёж со статусом 'confirmed'.
        Если передан список user_ids, возвращаются только те, кто есть в этом списке.
        """
        async with self.session_factory() as session:
            stmt = select(Payments.user_id).where(Payments.status == 'confirmed').distinct()
            if user_ids:
                stmt = stmt.where(Payments.user_id.in_(user_ids))
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]

    async def get_payment_stats_by_period(self, start_date: datetime, end_date: datetime) -> Tuple[Dict[str, int], Dict[str, int]]:
        """
        Возвращает статистику платежей за период по группам ref и stamp.
        Для каждого платежа с суммой != 1, статус 'confirmed', дата между start_date и end_date включительно,
        находим пользователя и добавляем сумму в группы ref и stamp (если они заданы).
        Возвращает два словаря: ref_totals, stamp_totals.
        """
        # Приводим даты к началу и концу суток для включительности
        start = datetime.combine(start_date.date(), datetime.min.time())
        end = datetime.combine(end_date.date(), datetime.max.time())

        async with self.session_factory() as session:
            # Получаем платежи за период, исключая сумму 1
            stmt_payments = select(
                Payments.user_id,
                Payments.amount
            ).where(
                Payments.status == 'confirmed',
                Payments.amount != 1,
                Payments.time_created.between(start, end)
            )
            payments_result = await session.execute(stmt_payments)
            payments_data = payments_result.all()

            if not payments_data:
                return {}, {}

            # Собираем уникальные user_id из платежей
            user_ids = list(set(p[0] for p in payments_data))

            # Получаем данные всех этих пользователей одним запросом
            stmt_users = select(
                Users.user_id,
                Users.ref,
                Users.stamp
            ).where(Users.user_id.in_(user_ids))
            users_result = await session.execute(stmt_users)
            users_data = users_result.all()

        # Словарь для быстрого поиска ref и stamp по user_id
        user_map = {u[0]: (u[1], u[2]) for u in users_data}

        ref_totals = {}
        stamp_totals = {}

        for user_id, amount in payments_data:
            ref, stamp = user_map.get(user_id, (None, None))
            if ref:
                ref_totals[ref] = ref_totals.get(ref, 0) + amount
            if stamp:
                stamp_totals[stamp] = stamp_totals.get(stamp, 0) + amount

        return ref_totals, stamp_totals

    async def update_broadcast_status(self, user_id: int, status: str) -> None:
        """
        Обновляет статус последней рассылки и дату для указанного пользователя.
        """
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.user_id == user_id).values(
                last_broadcast_status=status,
                last_broadcast_date=datetime.now()  # сохраняем полную дату и время
            )
            try:
                await session.execute(stmt)
                await session.commit()
            except Exception as e:
                await session.rollback()
                logger.error(f"Error updating broadcast status for user {user_id}: {e}")

    async def activate_gift(
        self, gift_id: str, recipient_id: int
    ) -> Tuple[bool, Optional[int], Optional[bool], Optional[int], Optional[int]]:
        """
        Активирует подарок по gift_id для указанного получателя.
        Возвращает (успех, duration, white_flag, giver_id, device_slots) или
        (False, None, None, None, None) если подарок не найден или уже активирован.
        """
        async with self.session_factory() as session:
            # Проверяем существование и статус подарка
            stmt = select(Gifts).where(
                Gifts.gift_id == gift_id,
                Gifts.flag == False,
                Gifts.recepient_id == None
            )
            result = await session.execute(stmt)
            gift = result.scalar_one_or_none()

            if not gift:
                logger.warning(f"Gift {gift_id} not found or already activated")
                return False, None, None, None, None

            giver_id = int(gift.giver_id)
            device_slots = gift.device_slots if gift.device_slots is not None else 5
            # Активируем подарок
            gift.flag = True
            gift.recepient_id = recipient_id
            try:
                await session.commit()
                logger.info(f"Gift {gift_id} activated for user {recipient_id}")
                return True, gift.duration, gift.white_flag, giver_id, int(device_slots)
            except Exception as e:
                await session.rollback()
                logger.error(f"Error activating gift {gift_id} for user {recipient_id}: {e}")
                return False, None, None, None, None

    async def get_pending_platega_payments(self) -> List[Payments]:
        """Возвращает все платежи из таблицы payments со статусом 'pending'."""
        async with self.session_factory() as session:
            stmt = select(Payments).where(Payments.status == 'pending')
            result = await session.execute(stmt)
            return result.scalars().all()

    async def get_pending_platega_card_payments(self) -> List[PaymentsCards]:
        """Возвращает все платежи из таблицы payments со статусом 'pending'."""
        async with self.session_factory() as session:
            stmt = select(PaymentsCards).where(PaymentsCards.status == 'pending')
            result = await session.execute(stmt)
            return result.scalars().all()

    async def get_pending_platega_crypto_payments(self) -> List[PaymentsPlategaCrypto]:
        """Возвращает все платежи из таблицы payments со статусом 'pending'."""
        async with self.session_factory() as session:
            stmt = select(PaymentsPlategaCrypto).where(PaymentsPlategaCrypto.status == 'pending')
            result = await session.execute(stmt)
            return result.scalars().all()

    async def update_payment_status(self, transaction_id: str, new_status: str) -> None:
        """Обновляет статус платежа по transaction_id."""
        async with self.session_factory() as session:
            stmt = update(Payments).where(Payments.transaction_id == transaction_id).values(status=new_status)
            await session.execute(stmt)
            await session.commit()

    async def update_payment_card_status(self, transaction_id: str, new_status: str) -> None:
        """Обновляет статус платежа по transaction_id."""
        async with self.session_factory() as session:
            stmt = update(PaymentsCards).where(PaymentsCards.transaction_id == transaction_id).values(status=new_status)
            await session.execute(stmt)
            await session.commit()

    async def update_payment_platega_crypto_status(self, transaction_id: str, new_status: str) -> None:
        """Обновляет статус платежа по transaction_id."""
        async with self.session_factory() as session:
            stmt = update(PaymentsPlategaCrypto).where(PaymentsPlategaCrypto.transaction_id == transaction_id).values(status=new_status)
            await session.execute(stmt)
            await session.commit()

    async def alloc_fk_api_nonce(self) -> int:
        return time.time_ns() // 1000

    async def get_pending_fk_sbp_payments(self) -> List[PaymentsFkSBP]:
        async with self.session_factory() as session:
            stmt = select(PaymentsFkSBP).where(PaymentsFkSBP.status == 'pending')
            result = await session.execute(stmt)
            return result.scalars().all()

    async def update_fk_sbp_payment_status(self, transaction_id: str, new_status: str) -> None:
        async with self.session_factory() as session:
            stmt = update(PaymentsFkSBP).where(
                PaymentsFkSBP.transaction_id == transaction_id
            ).values(status=new_status)
            await session.execute(stmt)
            await session.commit()

    async def add_fk_sbp_payment(
            self,
            user_id: int,
            amount: int,
            status: str,
            transaction_id: str,
            fk_order_id: Optional[int],
            payload: str,
            nonce: int,
            signature: str,
            is_gift: bool = False,
            method: str = 'fk_qr_card',
    ) -> None:
        async with self.session_factory() as session:
            payment = PaymentsFkSBP(
                user_id=user_id,
                amount=amount,
                status=status,
                transaction_id=transaction_id,
                fk_order_id=fk_order_id,
                payload=payload,
                nonce=nonce,
                signature=signature,
                method=method,
                is_gift=is_gift,
            )
            session.add(payment)
            try:
                await session.commit()
                logger.success(
                    f"💰 Платёж FreeKassa записан: user_id={user_id}, amount={amount}, is_gift={is_gift}, method={method}")
            except Exception as e:
                await session.rollback()
                logger.error(f"❌ Ошибка записи платежа FreeKassa: {e}")
                raise

    async def get_pending_wata_sbp_payments(self) -> List[PaymentsWataSBP]:
        async with self.session_factory() as session:
            stmt = select(PaymentsWataSBP).where(PaymentsWataSBP.status == 'pending')
            result = await session.execute(stmt)
            return result.scalars().all()

    async def count_pending_wata_sbp(self) -> int:
        async with self.session_factory() as session:
            stmt = select(func.count()).select_from(PaymentsWataSBP).where(PaymentsWataSBP.status == "pending")
            return int((await session.execute(stmt)).scalar_one())

    async def count_pending_wata_card(self) -> int:
        async with self.session_factory() as session:
            stmt = select(func.count()).select_from(PaymentsWataCard).where(PaymentsWataCard.status == "pending")
            return int((await session.execute(stmt)).scalar_one())

    async def get_pending_wata_card_payments(self) -> List[PaymentsWataCard]:
        async with self.session_factory() as session:
            stmt = select(PaymentsWataCard).where(PaymentsWataCard.status == 'pending')
            result = await session.execute(stmt)
            return result.scalars().all()

    async def get_pending_wata_sbp_payments_polled(
        self,
        recent_hours: int = 72,
        recent_limit: int = 100,
        stale_limit: int = 50,
    ) -> List[PaymentsWataSBP]:
        cutoff = datetime.now() - timedelta(hours=recent_hours)
        async with self.session_factory() as session:
            q_recent = (
                select(PaymentsWataSBP)
                .where(PaymentsWataSBP.status == "pending", PaymentsWataSBP.time_created >= cutoff)
                .order_by(PaymentsWataSBP.time_created.desc())
                .limit(recent_limit)
            )
            q_stale = (
                select(PaymentsWataSBP)
                .where(PaymentsWataSBP.status == "pending", PaymentsWataSBP.time_created < cutoff)
                .order_by(PaymentsWataSBP.time_created.asc())
                .limit(stale_limit)
            )
            r1 = (await session.execute(q_recent)).scalars().all()
            r2 = (await session.execute(q_stale)).scalars().all()
        seen: set[int] = set()
        out: List[PaymentsWataSBP] = []
        for p in (*r1, *r2):
            if p.id in seen:
                continue
            seen.add(p.id)
            out.append(p)
        return out

    async def get_pending_wata_card_payments_polled(
        self,
        recent_hours: int = 72,
        recent_limit: int = 100,
        stale_limit: int = 50,
    ) -> List[PaymentsWataCard]:
        cutoff = datetime.now() - timedelta(hours=recent_hours)
        async with self.session_factory() as session:
            q_recent = (
                select(PaymentsWataCard)
                .where(PaymentsWataCard.status == "pending", PaymentsWataCard.time_created >= cutoff)
                .order_by(PaymentsWataCard.time_created.desc())
                .limit(recent_limit)
            )
            q_stale = (
                select(PaymentsWataCard)
                .where(PaymentsWataCard.status == "pending", PaymentsWataCard.time_created < cutoff)
                .order_by(PaymentsWataCard.time_created.asc())
                .limit(stale_limit)
            )
            r1 = (await session.execute(q_recent)).scalars().all()
            r2 = (await session.execute(q_stale)).scalars().all()
        seen: set[int] = set()
        out: List[PaymentsWataCard] = []
        for p in (*r1, *r2):
            if p.id in seen:
                continue
            seen.add(p.id)
            out.append(p)
        return out

    async def update_wata_sbp_status(self, transaction_id: str, new_status: str) -> None:
        async with self.session_factory() as session:
            stmt = update(PaymentsWataSBP).where(PaymentsWataSBP.transaction_id == transaction_id).values(status=new_status)
            await session.execute(stmt)
            await session.commit()

    async def update_wata_card_status(self, transaction_id: str, new_status: str) -> None:
        async with self.session_factory() as session:
            stmt = update(PaymentsWataCard).where(PaymentsWataCard.transaction_id == transaction_id).values(status=new_status)
            await session.execute(stmt)
            await session.commit()

    async def add_wata_sbp_payment(
        self, user_id: int, amount: int, status: str, transaction_id: str, payload: str, is_gift: bool = False
    ) -> None:
        async with self.session_factory() as session:
            payment = PaymentsWataSBP(
                user_id=user_id,
                amount=amount,
                status=status,
                transaction_id=transaction_id,
                payload=payload,
                is_gift=is_gift,
            )
            session.add(payment)
            try:
                await session.commit()
                logger.success(f"💰 Платёж WATA СБП записан: user_id={user_id}, amount={amount}, is_gift={is_gift}")
            except Exception as e:
                await session.rollback()
                logger.error(f"❌ Ошибка записи платежа WATA СБП: {e}")
                raise

    async def add_wata_card_payment(
        self, user_id: int, amount: int, status: str, transaction_id: str, payload: str, is_gift: bool = False
    ) -> None:
        async with self.session_factory() as session:
            payment = PaymentsWataCard(
                user_id=user_id,
                amount=amount,
                status=status,
                transaction_id=transaction_id,
                payload=payload,
                is_gift=is_gift,
            )
            session.add(payment)
            try:
                await session.commit()
                logger.success(f"💰 Платёж WATA Карта записан: user_id={user_id}, amount={amount}, is_gift={is_gift}")
            except Exception as e:
                await session.rollback()
                logger.error(f"❌ Ошибка записи платежа WATA Карта: {e}")
                raise

    async def get_active_cryptobot_payments(self) -> List[PaymentsCryptobot]:
        """
        Возвращает все платежи Cryptobot со статусом 'active'.
        """
        async with self.session_factory() as session:
            stmt = select(PaymentsCryptobot).where(PaymentsCryptobot.status == 'active')
            result = await session.execute(stmt)
            return result.scalars().all()

    async def update_cryptobot_payment_status(self, payment_id: int, status: str) -> None:
        """
        Обновляет статус платежа Cryptobot.
        """
        async with self.session_factory() as session:
            stmt = update(PaymentsCryptobot).where(PaymentsCryptobot.id == payment_id).values(status=status)
            await session.execute(stmt)
            await session.commit()

    async def add_payment_stars(self, user_id: int, amount: int, is_gift: bool, payload: str) -> None:
        """Добавляет запись в таблицу payments_stars."""
        async with self.session_factory() as session:
            payment = PaymentsStars(
                user_id=user_id,
                amount=amount,
                is_gift=is_gift,
                status='confirmed',
                payload=payload
            )
            session.add(payment)
            try:
                await session.commit()
                logger.success(
                    f"💰 Платёж Telegram Stars записан: user_id={user_id}, amount={amount}, is_gift={is_gift}")
            except Exception as e:
                await session.rollback()
                logger.error(f"❌ Ошибка записи платежа Telegram Stars: {e}")

    async def create_gift(
        self, giver_id: int, duration: int, white_flag: bool, device_slots: int = 5
    ) -> str:
        """Создаёт запись о подарке и возвращает gift_id."""
        gift_id = str(uuid.uuid4())
        async with self.session_factory() as session:
            gift = Gifts(
                gift_id=gift_id,
                giver_id=giver_id,
                duration=duration,
                recepient_id=None,
                white_flag=white_flag,
                device_slots=device_slots,
                flag=False
            )
            session.add(gift)
            try:
                await session.commit()
                logger.info(f"✅ Запись о подарке создана: gift_id={gift_id}")
                return gift_id
            except Exception as e:
                await session.rollback()
                logger.error(f"❌ Ошибка создания подарка: {e}")
                raise

    async def add_online_stats(self, users_panel: int, users_active: int, users_pay: int, users_trial: int) -> None:
        """
        Сохраняет ежедневную статистику онлайн-активности.
        """
        async with self.session_factory() as session:
            online_record = Online(
                users_panel=users_panel,
                users_active=users_active,
                users_pay=users_pay,
                users_trial=users_trial
            )
            session.add(online_record)
            await session.commit()

    async def add_platega_payment(self, user_id: int, amount: int, status: str, transaction_id: str, payload: str,
                                  is_gift: bool = False) -> None:
        """
        Записывает платёж Platega в таблицу payments.
        """
        async with self.session_factory() as session:
            payment = Payments(
                user_id=user_id,
                amount=amount,
                status=status,
                transaction_id=transaction_id,
                is_gift=is_gift,
                payload=payload
            )
            session.add(payment)
            try:
                await session.commit()
                logger.success(f"💰 Платёж Platega SBP записан: user_id={user_id}, amount={amount}, is_gift={is_gift}")
            except Exception as e:
                await session.rollback()
                logger.error(f"❌ Ошибка записи платежа Platega: {e}")
                raise

    async def add_platega_card_payment(self, user_id: int, amount: int, status: str, transaction_id: str, payload: str,
                                       is_gift: bool = False) -> None:
        """
        Записывает платёж PlategaCard в таблицу payments.
        """
        async with self.session_factory() as session:
            payment = PaymentsCards(
                user_id=user_id,
                amount=amount,
                status=status,
                transaction_id=transaction_id,
                payload=payload,
                is_gift=is_gift
            )
            session.add(payment)
            try:
                await session.commit()
                logger.success(f"💰 Платёж Platega Card записан: user_id={user_id}, amount={amount}, is_gift={is_gift}")
            except Exception as e:
                await session.rollback()
                logger.error(f"❌ Ошибка записи платежа Platega: {e}")
                raise

    async def add_platega_crypto_payment(self, user_id: int, amount: int, status: str, transaction_id: str, payload: str,
                                       is_gift: bool = False) -> None:
        """
        Записывает платёж PlategaCard в таблицу payments.
        """
        async with self.session_factory() as session:
            payment = PaymentsPlategaCrypto(
                user_id=user_id,
                amount=amount,
                status=status,
                transaction_id=transaction_id,
                payload=payload,
                is_gift=is_gift
            )
            session.add(payment)
            try:
                await session.commit()
                logger.success(f"💰 Платёж Platega Crypto записан: user_id={user_id}, amount={amount}, is_gift={is_gift}")
            except Exception as e:
                await session.rollback()
                logger.error(f"❌ Ошибка записи платежа Platega: {e}")
                raise

    async def add_cryptobot_payment(self, user_id: int, amount: float, currency: str, is_gift: bool, invoice_id: str,
                                    payload: str) -> None:
        """
        Запись платежа Cryptobot в таблицу payments_cryptobot.
        """
        async with self.session_factory() as session:
            payment = PaymentsCryptobot(
                user_id=user_id,
                amount=amount,
                currency=currency,
                is_gift=is_gift,
                status='active',
                invoice_id=invoice_id,
                payload=payload
            )
            session.add(payment)
            await session.commit()
            logger.info(f"Cryptobot invoice created: {invoice_id} for user {user_id}")

    async def get_all_users(self) -> List[Users]:
        """Возвращает список всех пользователей."""
        async with self.session_factory() as session:
            result = await session.execute(select(Users))
            return result.scalars().all()

    async def get_all_payments(self) -> List[Payments]:
        """Возвращает список всех платежей Platega."""
        async with self.session_factory() as session:
            result = await session.execute(select(Payments))
            return result.scalars().all()

    async def get_all_payments_cards(self) -> List[PaymentsCards]:
        """Возвращает список всех платежей по картам (PaymentsCards)."""
        async with self.session_factory() as session:
            result = await session.execute(select(PaymentsCards))
            return result.scalars().all()

    async def get_all_payments_platega_crypto(self) -> List[PaymentsPlategaCrypto]:
        async with self.session_factory() as session:
            result = await session.execute(select(PaymentsPlategaCrypto))
            return result.scalars().all()

    async def get_all_payments_stars(self) -> List[PaymentsStars]:
        """Возвращает список всех платежей Telegram Stars."""
        async with self.session_factory() as session:
            result = await session.execute(select(PaymentsStars))
            return result.scalars().all()

    async def get_all_payments_cryptobot(self) -> List[PaymentsCryptobot]:
        """Возвращает список всех крипто-платежей."""
        async with self.session_factory() as session:
            result = await session.execute(select(PaymentsCryptobot))
            return result.scalars().all()

    async def get_all_gifts(self) -> List[Gifts]:
        """Возвращает список всех подарков."""
        async with self.session_factory() as session:
            result = await session.execute(select(Gifts))
            return result.scalars().all()

    async def get_all_online(self) -> List[Online]:
        """Возвращает список всех записей онлайн-статистики."""
        async with self.session_factory() as session:
            result = await session.execute(select(Online))
            return result.scalars().all()

    async def get_all_white_counter(self) -> List[WhiteCounter]:
        """Возвращает список всех записей white_counter."""
        async with self.session_factory() as session:
            result = await session.execute(select(WhiteCounter))
            return result.scalars().all()

    async def get_export_snapshot(self) -> Dict[str, List[Any]]:
        """
        Одна сессия БД: все SELECT для /export подряд.
        Меньше открытий соединения и накладных расходов, чем десять отдельных get_all_*.
        """
        async with self.session_factory() as session:
            users_list = (await session.execute(select(Users))).scalars().all()
            payments_list = (await session.execute(select(Payments))).scalars().all()
            payments_cards_list = (await session.execute(select(PaymentsCards))).scalars().all()
            payments_platega_crypto_list = (await session.execute(select(PaymentsPlategaCrypto))).scalars().all()
            payments_wata_sbp_list = (await session.execute(select(PaymentsWataSBP))).scalars().all()
            payments_wata_card_list = (await session.execute(select(PaymentsWataCard))).scalars().all()
            payments_fk_sbp_list = (await session.execute(select(PaymentsFkSBP))).scalars().all()
            payments_stars_list = (await session.execute(select(PaymentsStars))).scalars().all()
            payments_cryptobot_list = (await session.execute(select(PaymentsCryptobot))).scalars().all()
            gifts_list = (await session.execute(select(Gifts))).scalars().all()
            online_list = (await session.execute(select(Online))).scalars().all()
            white_counter_list = (await session.execute(select(WhiteCounter))).scalars().all()
        return {
            "users": users_list,
            "payments": payments_list,
            "payments_cards": payments_cards_list,
            "payments_platega_crypto": payments_platega_crypto_list,
            "payments_wata_sbp": payments_wata_sbp_list,
            "payments_wata_card": payments_wata_card_list,
            "payments_fk_sbp": payments_fk_sbp_list,
            "payments_stars": payments_stars_list,
            "payments_cryptobot": payments_cryptobot_list,
            "gifts": gifts_list,
            "online": online_list,
            "white_counter": white_counter_list,
        }

    async def add_white_counter_if_not_exists(self, user_id: int) -> None:
        """
        Добавляет запись в white_counter, если её ещё нет для данного пользователя.
        """
        async with self.session_factory() as session:
            stmt = select(WhiteCounter).where(WhiteCounter.user_id == user_id)
            result = await session.execute(stmt)
            if not result.scalar_one_or_none():
                session.add(WhiteCounter(user_id=user_id))
                await session.commit()
                logger.info(f"✅ Добавлена запись в white_counter для пользователя {user_id}")

    async def set_reserve_field_for_paid_users(self) -> int:
        """
        Устанавливает reserve_field = True для всех пользователей,
        у которых есть хотя бы один подтверждённый платёж в любой из таблиц.
        Возвращает количество обновлённых записей.
        """
        async with self.session_factory() as session:
            # Подзапросы для каждой таблицы с нужным статусом
            from sqlalchemy import union, select, update

            subq_payments = select(Payments.user_id).where(Payments.status == 'confirmed')
            subq_cards = select(PaymentsCards.user_id).where(PaymentsCards.status == 'confirmed')
            subq_platega_crypto = select(PaymentsPlategaCrypto.user_id).where(
                PaymentsPlategaCrypto.status == 'confirmed')
            subq_stars = select(PaymentsStars.user_id).where(PaymentsStars.status == 'confirmed')
            subq_cryptobot = select(PaymentsCryptobot.user_id).where(PaymentsCryptobot.status == 'paid')
            subq_wata_sbp = select(PaymentsWataSBP.user_id).where(PaymentsWataSBP.status == 'confirmed')
            subq_wata_card = select(PaymentsWataCard.user_id).where(PaymentsWataCard.status == 'confirmed')
            subq_fk_sbp = select(PaymentsFkSBP.user_id).where(PaymentsFkSBP.status == 'confirmed')

            union_query = union(
                subq_payments,
                subq_cards,
                subq_platega_crypto,
                subq_stars,
                subq_cryptobot,
                subq_wata_sbp,
                subq_wata_card,
                subq_fk_sbp,
            ).subquery()

            stmt = (
                update(Users)
                .where(Users.user_id.in_(union_query))
                .values(reserve_field=True)
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount

    async def get_users_with_payment(self) -> List[int]:
        """Возвращает список user_id пользователей с has_discount=True и is_delete=False."""
        async with self.session_factory() as session:
            stmt = select(Users.user_id).where(
                Users.reserve_field == True
            )
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]

    async def get_user_subscription_payment_report(
        self, user_id: int
    ) -> List[Tuple[datetime, str, str]]:
        """
        Успешные платежи пользователя (confirmed/paid) по всем таблицам оплат.
        Возвращает список (time_created UTC naive, тип подписки, кол-во дней как строка).
        """
        rows_acc: List[Tuple[datetime, str, str]] = []

        def _parse_map(payload: Optional[str]) -> Dict[str, str]:
            if not payload:
                return {}
            out: Dict[str, str] = {}
            for part in payload.split(","):
                if ":" not in part:
                    continue
                k, _, v = part.partition(":")
                out[k.strip()] = v.strip()
            return out

        def _device_slots_from_payload(m: Dict[str, str]) -> int:
            raw = m.get("device")
            if raw is None:
                return 5
            try:
                n = int(raw)
            except (TypeError, ValueError):
                return 5
            if n in (3, 5, 10):
                return n
            return 5

        def _device_tariff_label(device_slots: int) -> str:
            if device_slots == 3:
                return "3 устройства"
            if device_slots == 10:
                return "10 устройств"
            return "5 устройств"

        def _row_kind_and_days(
            payload: Optional[str], is_gift: bool, amount: Any
        ) -> Tuple[str, str]:
            m = _parse_map(payload)
            white = m.get("white", "False").lower() == "true"
            gift = bool(is_gift) or m.get("gift", "False").lower() == "true"
            device_slots = _device_slots_from_payload(m)
            dur = _payload_duration_to_panel_days(m.get("duration"))
            if dur is None:
                try:
                    amt_f = float(amount)
                except (TypeError, ValueError):
                    amt_f = None
                if amt_f is not None:
                    if white:
                        dur = _white_days_from_amount_fallback(amt_f)
                    else:
                        dur = _billing_duration_from_amount_fallback(amt_f)

            tariff = _device_tariff_label(device_slots)
            if gift and white:
                label = "Подарок, вайт (mobile)"
            elif gift:
                label = f"Подарок, {tariff}"
            elif white:
                label = "Вайт (mobile)"
            else:
                label = tariff

            days_s = str(dur) if dur is not None else "—"
            return label, days_s

        async with self.session_factory() as session:
            queries: List[Any] = [
                select(
                    Payments.user_id,
                    Payments.time_created,
                    Payments.amount,
                    Payments.payload,
                    Payments.is_gift,
                ).where(
                    Payments.user_id == user_id,
                    Payments.status.in_(_BILLING_OK_STATUSES),
                ),
                select(
                    PaymentsCards.user_id,
                    PaymentsCards.time_created,
                    PaymentsCards.amount,
                    PaymentsCards.payload,
                    PaymentsCards.is_gift,
                ).where(
                    PaymentsCards.user_id == user_id,
                    PaymentsCards.status.in_(_BILLING_OK_STATUSES),
                ),
                select(
                    PaymentsPlategaCrypto.user_id,
                    PaymentsPlategaCrypto.time_created,
                    PaymentsPlategaCrypto.amount,
                    PaymentsPlategaCrypto.payload,
                    PaymentsPlategaCrypto.is_gift,
                ).where(
                    PaymentsPlategaCrypto.user_id == user_id,
                    PaymentsPlategaCrypto.status.in_(_BILLING_OK_STATUSES),
                ),
                select(
                    PaymentsStars.user_id,
                    PaymentsStars.time_created,
                    PaymentsStars.amount,
                    PaymentsStars.payload,
                    PaymentsStars.is_gift,
                ).where(
                    PaymentsStars.user_id == user_id,
                    PaymentsStars.status.in_(_BILLING_OK_STATUSES),
                ),
                select(
                    PaymentsCryptobot.user_id,
                    PaymentsCryptobot.time_created,
                    PaymentsCryptobot.amount,
                    PaymentsCryptobot.payload,
                    PaymentsCryptobot.is_gift,
                ).where(
                    PaymentsCryptobot.user_id == user_id,
                    PaymentsCryptobot.status.in_(_BILLING_OK_STATUSES),
                ),
                select(
                    PaymentsFkSBP.user_id,
                    PaymentsFkSBP.time_created,
                    PaymentsFkSBP.amount,
                    PaymentsFkSBP.payload,
                    PaymentsFkSBP.is_gift,
                ).where(
                    PaymentsFkSBP.user_id == user_id,
                    PaymentsFkSBP.status.in_(_BILLING_OK_STATUSES),
                ),
                select(
                    PaymentsWataSBP.user_id,
                    PaymentsWataSBP.time_created,
                    PaymentsWataSBP.amount,
                    PaymentsWataSBP.payload,
                    PaymentsWataSBP.is_gift,
                ).where(
                    PaymentsWataSBP.user_id == user_id,
                    PaymentsWataSBP.status.in_(_BILLING_OK_STATUSES),
                ),
                select(
                    PaymentsWataCard.user_id,
                    PaymentsWataCard.time_created,
                    PaymentsWataCard.amount,
                    PaymentsWataCard.payload,
                    PaymentsWataCard.is_gift,
                ).where(
                    PaymentsWataCard.user_id == user_id,
                    PaymentsWataCard.status.in_(_BILLING_OK_STATUSES),
                ),
            ]
            for q in queries:
                for _uid, tc, amt, pl, ig in (await session.execute(q)).all():
                    kind, days_s = _row_kind_and_days(pl, bool(ig), amt)
                    rows_acc.append((tc, kind, days_s))

        rows_acc.sort(key=lambda x: (x[0], x[1]))
        return rows_acc

    async def next_negative_user_id(self) -> int:
        async with self.session_factory() as session:
            stmt = select(func.min(Users.user_id)).where(Users.user_id < 0)
            result = await session.execute(stmt)
            m = result.scalar_one_or_none()
            if m is None:
                return -10
            nxt = int(m) - 1
            while nxt < 0 and len(str(nxt)) < 3:
                nxt -= 1
            return nxt

    async def register_email_user(self, email: str, password_hash: str) -> int:
        em = _norm_email(email)
        uid = await self.next_negative_user_id()
        async with self.session_factory() as session:
            u = Users(
                user_id=uid,
                email=em,
                password_hash=password_hash,
                stamp="email",
                create_user=datetime.now(),
            )
            session.add(u)
            await session.commit()
            await session.refresh(u)
            return int(u.id)

    async def set_password_hash_by_internal_id(self, internal_id: int, password_hash: str) -> bool:
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.id == internal_id).values(password_hash=password_hash)
            r = await session.execute(stmt)
            await session.commit()
            return (r.rowcount or 0) > 0

    async def set_activation_pass_by_email(self, email: str, value) -> bool:
        em = _norm_email(email)
        async with self.session_factory() as session:
            stmt = update(Users).where(func.lower(Users.email) == em).values(activation_pass=value)
            r = await session.execute(stmt)
            await session.commit()
            return (r.rowcount or 0) > 0

    async def set_email_verified(self, internal_id: int, verified: bool) -> bool:
        async with self.session_factory() as session:
            stmt = update(Users).where(Users.id == internal_id).values(field_bool_1=verified)
            r = await session.execute(stmt)
            await session.commit()
            return (r.rowcount or 0) > 0

    async def replace_password_reset_codes(self, email: str, code: str, expires_at: datetime) -> None:
        em = _norm_email(email)
        exp = _naive_utc(expires_at)
        async with self.session_factory() as session:
            await session.execute(delete(PasswordResetCodes).where(PasswordResetCodes.email == em))
            session.add(PasswordResetCodes(email=em, code=code, expires_at=exp))
            await session.commit()

    async def verify_password_reset_code(self, email: str, code: str) -> bool:
        em = _norm_email(email)
        now = _naive_utc(datetime.now(timezone.utc))
        async with self.session_factory() as session:
            stmt = select(PasswordResetCodes).where(
                PasswordResetCodes.email == em,
                PasswordResetCodes.code == code,
                PasswordResetCodes.expires_at > now,
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            return row is not None

    async def delete_password_reset_codes_for_email(self, email: str) -> None:
        em = _norm_email(email)
        async with self.session_factory() as session:
            await session.execute(delete(PasswordResetCodes).where(PasswordResetCodes.email == em))
            await session.commit()

    async def replace_linking_code(self, creator_internal_id: int, code: str, expires_at: datetime) -> None:
        exp = _naive_utc(expires_at)
        now = _naive_utc(datetime.now(timezone.utc))
        async with self.session_factory() as session:
            await session.execute(delete(LinkingCodes).where(LinkingCodes.user_id == creator_internal_id))
            session.add(
                LinkingCodes(
                    code=code,
                    user_id=creator_internal_id,
                    created_at=now,
                    expires_at=exp,
                )
            )
            await session.commit()

    async def get_valid_linking_code(self, code: str) -> Optional[Tuple[int, int]]:
        now = _naive_utc(datetime.now(timezone.utc))
        async with self.session_factory() as session:
            stmt = select(LinkingCodes).where(
                LinkingCodes.code == code,
                LinkingCodes.expires_at > now,
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                return None
            return (int(row.code_id), int(row.user_id))

    async def delete_linking_code_by_id(self, code_id: int) -> None:
        async with self.session_factory() as session:
            await session.execute(delete(LinkingCodes).where(LinkingCodes.code_id == code_id))
            await session.commit()

    async def merge_email_placeholder_into_telegram(
        self,
        email_row_internal_id: int,
        telegram_user_id: int,
    ) -> bool:
        async with self.session_factory() as session:
            e = await session.get(Users, email_row_internal_id)
            if e is None or e.user_id >= 0:
                return False
            stmt = select(Users).where(Users.user_id == telegram_user_id)
            t = (await session.execute(stmt)).scalar_one_or_none()
            if t is None or t.user_id <= 0:
                return False

            merge_now = datetime.now(timezone.utc)
            merged_email = e.email
            merged_password_hash = e.password_hash
            e.email = None
            e.password_hash = None
            await session.flush()

            t_paid_pro, t_paid_white = await _merge_user_paid_subscription_flags(session, t.user_id)
            e_paid_pro, e_paid_white = await _merge_user_paid_subscription_flags(session, e.user_id)

            if t_paid_pro and e_paid_pro:
                t.subscription_end_date = _sum_subscription_end_dates(
                    t.subscription_end_date, e.subscription_end_date, merge_now
                )
                t.subscription_3_end_date = _sum_subscription_end_dates(
                    t.subscription_3_end_date, e.subscription_3_end_date, merge_now
                )
                t.subscription_10_end_date = _sum_subscription_end_dates(
                    t.subscription_10_end_date, e.subscription_10_end_date, merge_now
                )
            else:
                t.subscription_end_date = _max_subscription_end_dates(
                    t.subscription_end_date, e.subscription_end_date, merge_now
                )
                t.subscription_3_end_date = _max_subscription_end_dates(
                    t.subscription_3_end_date, e.subscription_3_end_date, merge_now
                )
                t.subscription_10_end_date = _max_subscription_end_dates(
                    t.subscription_10_end_date, e.subscription_10_end_date, merge_now
                )

            if t_paid_white and e_paid_white:
                t.white_subscription_end_date = _sum_subscription_end_dates(
                    t.white_subscription_end_date, e.white_subscription_end_date, merge_now
                )
            else:
                t.white_subscription_end_date = _max_subscription_end_dates(
                    t.white_subscription_end_date, e.white_subscription_end_date, merge_now
                )

            t.in_panel = bool(t.in_panel or e.in_panel)
            t.in_chanel = bool(t.in_chanel or e.in_chanel)
            t.is_connect = bool(t.is_connect or e.is_connect)
            t.is_delete = bool(t.is_delete or e.is_delete)
            t.reserve_field = bool(t.reserve_field or e.reserve_field)
            if not (t.ref or "") and (e.ref or ""):
                t.ref = e.ref
            if merged_email:
                t.email = merged_email
            if merged_password_hash:
                t.password_hash = merged_password_hash
            if (e.stamp or "") and (e.stamp or "") != "email":
                if not (t.stamp or "") or (t.stamp or "") == "email":
                    t.stamp = e.stamp
            if not (t.ttclid or "") and (e.ttclid or ""):
                t.ttclid = e.ttclid
            if not (t.subscribtion or "") and (e.subscribtion or ""):
                t.subscribtion = e.subscribtion
            if not (t.subscribtion_3 or "") and (e.subscribtion_3 or ""):
                t.subscribtion_3 = e.subscribtion_3
            if not (t.subscribtion_10 or "") and (e.subscribtion_10 or ""):
                t.subscribtion_10 = e.subscribtion_10
            if not (t.white_subscription or "") and (e.white_subscription or ""):
                t.white_subscription = e.white_subscription
            t.field_bool_1 = bool(t.field_bool_1 or e.field_bool_1)
            t.field_bool_2 = bool(t.field_bool_2 or e.field_bool_2)
            t.field_bool_3 = bool(t.field_bool_3 or e.field_bool_3)

            old_uid = e.user_id
            await session.delete(e)
            await session.flush()

            await session.execute(
                update(Users).where(Users.ref == str(old_uid)).values(ref=str(telegram_user_id))
            )
            await session.execute(
                update(Gifts).where(Gifts.giver_id == old_uid).values(giver_id=telegram_user_id)
            )
            await session.execute(
                update(Gifts).where(Gifts.recepient_id == old_uid).values(recepient_id=telegram_user_id)
            )
            for model in _MERGE_PAYMENT_MODELS + (WhiteCounter,):
                await session.execute(
                    update(model).where(model.user_id == old_uid).values(user_id=telegram_user_id)
                )
            await session.execute(delete(LinkingCodes).where(LinkingCodes.user_id == email_row_internal_id))
            await session.commit()

        try:
            from bot import x3
            from tariff_resolve import panel_username_for_site_user

            for white in (False, True):
                await x3.delete_panel_user_by_username(panel_username_for_site_user(old_uid, white=white))
                for dev in (3, 10):
                    await x3.delete_panel_user_by_username(
                        panel_username_for_site_user(old_uid, white=white, device_slots=dev)
                    )
        except Exception as ex:
            logger.warning("post-merge panel cleanup: {}", ex)

        return True

    async def count_open_payment_slots_for_user(self, user_id: int) -> int:
        uid = int(user_id)
        async with self.session_factory() as session:
            total = 0
            pairs = (
                (PaymentsWataSBP, and_(PaymentsWataSBP.user_id == uid, PaymentsWataSBP.status == "pending")),
                (PaymentsWataCard, and_(PaymentsWataCard.user_id == uid, PaymentsWataCard.status == "pending")),
                (
                    PaymentsCryptobot,
                    and_(
                        PaymentsCryptobot.user_id == uid,
                        or_(
                            PaymentsCryptobot.status == "active",
                            PaymentsCryptobot.status == "pending",
                        ),
                    ),
                ),
                (Payments, and_(Payments.user_id == uid, Payments.status == "pending")),
                (PaymentsCards, and_(PaymentsCards.user_id == uid, PaymentsCards.status == "pending")),
                (PaymentsPlategaCrypto, and_(PaymentsPlategaCrypto.user_id == uid, PaymentsPlategaCrypto.status == "pending")),
                (PaymentsFkSBP, and_(PaymentsFkSBP.user_id == uid, PaymentsFkSBP.status == "pending")),
            )
            for model, cond in pairs:
                q = select(func.count()).select_from(model).where(cond)
                total += int((await session.execute(q)).scalar_one())
        return total

    async def get_payment_by_transaction_id(self, transaction_id: str, user_id: int) -> Optional[str]:
        async with self.session_factory() as session:
            for model in (
                Payments, PaymentsCards, PaymentsPlategaCrypto,
                PaymentsWataSBP, PaymentsWataCard, PaymentsFkSBP,
            ):
                stmt = select(model).where(
                    model.transaction_id == transaction_id,
                    model.user_id == user_id,
                )
                row = (await session.execute(stmt)).scalar_one_or_none()
                if row is not None:
                    return row.status
        return None
