"""
Сидирование демо-данных для партнёрского бота.

Пишет в БД из config.DATABASE_PATH (тот же путь, что в config_bd/models.py).

- 676 пользователей с create_user 08.07–10.07 (включительно)
- 78% in_panel=True
- из in_panel: 47% is_connect=True + trial 3 дня (subscription_end_date, field_bool_3)
- 63 оплаты по реальным тарифам, сумма ~17400 RUB
- partner_balance / balance_own_bot ~8700 RUB (50% от суммы оплат)

Запуск из корня проекта:
  set BOT_ID=50
  set OWNER_TG_ID=8603141868
  python seed_demo_data.py
  python seed_demo_data.py --bot-id 50 --clear-seeded
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import secrets
import sys
from datetime import datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

USERS_TOTAL = 676
IN_PANEL_PCT = 0.78
IS_CONNECT_PCT = 0.47  # от in_panel
PAYMENTS_TOTAL = 63
TARGET_PAYMENTS_SUM = 17_400
TARGET_SUM_TOLERANCE = 400
PARTNER_SHARE_PCT = 50  # доля владельца без партнёрской/реф ссылки
TRIAL_DAYS = 3
USER_ID_BASE = 9_100_000_000


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Seed demo users/payments for partner bot")
    p.add_argument(
        "--bot-id",
        type=int,
        default=int(os.environ["BOT_ID"]) if os.environ.get("BOT_ID") else 50,
    )
    p.add_argument(
        "--owner-id",
        type=int,
        default=int(os.environ["OWNER_TG_ID"]) if os.environ.get("OWNER_TG_ID") else 7526241265,
    )
    p.add_argument(
        "--db",
        type=str,
        default=None,
        help="Переопределить DATABASE_PATH (по умолчанию — путь из config/models)",
    )
    p.add_argument("--year", type=int, default=2026, help="Год для дат 08.07–10.07")
    p.add_argument("--seed", type=int, default=42, help="Random seed")
    p.add_argument(
        "--clear-seeded",
        action="store_true",
        help="Удалить ранее засеянных юзеров (user_id >= USER_ID_BASE) и их оплаты перед вставкой",
    )
    return p.parse_args()


def _random_dt(rng: random.Random, day_start: datetime, day_end: datetime) -> datetime:
    span = int((day_end - day_start).total_seconds())
    return day_start + timedelta(seconds=rng.randint(0, max(span, 0)))


def _profile(rng: random.Random, create_user: datetime) -> str:
    activity = create_user + timedelta(minutes=rng.randint(0, 48 * 60))
    return json.dumps(
        {
            "username": f"user_{rng.randint(1000, 999999)}",
            "full_name": f"Demo User {rng.randint(1, 99999)}",
            "language": rng.choice(["ru", "en", "uk"]),
            "last_activity": activity.isoformat(),
        },
        ensure_ascii=False,
    )


def _pick_tariffs(
    rng: random.Random,
    prices: dict[str, int],
    n: int,
    target: int,
    tolerance: int,
) -> list[str]:
    """Набор из n тарифов с суммой около target (реальные DEFAULT_PRICES)."""
    # База: 49 * m1_d5(299) + 14 * m1_d3(199) = 17437
    keys = ["m1_d5"] * 49 + ["m1_d3"] * 14
    if n < len(keys):
        keys = keys[:n]
    elif n > len(keys):
        keys.extend(["m1_d3"] * (n - len(keys)))

    # Свапы: разнообразие устройств/периодов, сумма остаётся рядом с target
    safe_swaps: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
        (("m1_d5", "m1_d3"), ("m3_d3",)),  # 498 → 499
        (("m1_d5", "m1_d5"), ("m3_d3", "m1_d3")),  # 598 → 698
        (("m1_d5", "m1_d5", "m1_d5"), ("m1_d10", "m1_d5")),  # 897 → 958
        (("m1_d5", "m1_d5", "m1_d3"), ("m3_d5", "m1_d3")),  # 797 → 948
        (("m1_d5",) * 4, ("m6_d3", "m1_d5")),  # 1196 → 1298
        (("m1_d5",) * 6, ("m3_d10", "m1_d5")),  # 1794 → 1648
        (("m1_d5",) * 6, ("m12_d3", "m1_d5", "m1_d3")),  # 1794 → 1686
        (("m1_d5",) * 8, ("m6_d5", "m1_d5", "m1_d3")),  # 2392 → 1847
    ]

    for _ in range(10):
        remove, add = rng.choice(safe_swaps)
        tmp = list(keys)
        if any(tmp.count(k) < remove.count(k) for k in set(remove)):
            continue
        for k in remove:
            tmp.remove(k)
        tmp.extend(add)
        if len(tmp) != n:
            continue
        if abs(sum(prices[k] for k in tmp) - target) <= tolerance + 600:
            keys = tmp

    candidates = list(prices)
    for _ in range(250):
        s = sum(prices[k] for k in keys)
        if abs(s - target) <= tolerance:
            break
        i = rng.randrange(n)
        old = keys[i]
        if s > target:
            options = [k for k in candidates if prices[k] < prices[old]] or candidates
        else:
            options = [k for k in candidates if prices[k] > prices[old]] or candidates
        keys[i] = min(options, key=lambda k: abs(s - prices[old] + prices[k] - target))

    rng.shuffle(keys)
    return keys


async def seed(args: argparse.Namespace) -> None:
    os.environ["BOT_ID"] = str(args.bot_id)
    os.environ["OWNER_TG_ID"] = str(args.owner_id)
    if args.db:
        os.environ["DATABASE_PATH"] = str(Path(args.db).resolve())
    # иначе — путь из config (default config_bd/partner.db), как в models.py

    from config import BOT_ID, DATABASE_PATH, DEFAULT_PRICES, DEFAULT_TRIAL_DAYS, TARIFF_KEYS
    from config_bd.models import (
        PaymentsCryptobot,
        PaymentsFkSBP,
        PaymentsStars,
        PartnerBotSettings,
        Users,
        create_tables,
        AsyncSessionLocal,
    )
    from sqlalchemy import delete, select, update
    from tariff_resolve import device_from_tariff_key, tariff_days_for_x3

    assert BOT_ID == args.bot_id, f"BOT_ID mismatch: {BOT_ID} != {args.bot_id}"
    trial_days = DEFAULT_TRIAL_DAYS or TRIAL_DAYS
    prices = {k: int(DEFAULT_PRICES[k]) for k in TARIFF_KEYS}

    rng = random.Random(args.seed)
    year = args.year
    period_start = datetime(year, 7, 8, 0, 0, 0)
    period_end = datetime(year, 7, 10, 23, 59, 59)

    await create_tables()

    tariff_keys = _pick_tariffs(
        rng, prices, PAYMENTS_TOTAL, TARGET_PAYMENTS_SUM, TARGET_SUM_TOLERANCE
    )
    payments_sum = sum(prices[k] for k in tariff_keys)
    partner_balance = payments_sum * PARTNER_SHARE_PCT // 100

    in_panel_n = round(USERS_TOTAL * IN_PANEL_PCT)
    is_connect_n = round(in_panel_n * IS_CONNECT_PCT)

    print(
        f"DB={DATABASE_PATH.resolve()}\n"
        f"bot_id={args.bot_id} owner_id={args.owner_id}\n"
        f"users={USERS_TOTAL} in_panel={in_panel_n} "
        f"is_connect={is_connect_n} payments={PAYMENTS_TOTAL}\n"
        f"payments_sum={payments_sum} partner_balance={partner_balance} "
        f"trial_days={trial_days}"
    )

    async with AsyncSessionLocal() as session:
        if args.clear_seeded:
            seeded_ids = (
                await session.execute(
                    select(Users.user_id).where(
                        Users.bot_id == BOT_ID,
                        Users.user_id >= USER_ID_BASE,
                    )
                )
            ).scalars().all()
            if seeded_ids:
                for model in (PaymentsFkSBP, PaymentsStars, PaymentsCryptobot):
                    await session.execute(
                        delete(model).where(
                            model.bot_id == BOT_ID,
                            model.user_id.in_(list(seeded_ids)),
                        )
                    )
                await session.execute(
                    delete(Users).where(
                        Users.bot_id == BOT_ID,
                        Users.user_id >= USER_ID_BASE,
                    )
                )
                await session.commit()
                print(f"cleared {len(seeded_ids)} previously seeded users")

        flags = [True] * in_panel_n + [False] * (USERS_TOTAL - in_panel_n)
        rng.shuffle(flags)
        panel_indices = [i for i, f in enumerate(flags) if f]
        connect_set = set(rng.sample(panel_indices, is_connect_n))

        users: list[Users] = []
        for i in range(USERS_TOTAL):
            create_user = _random_dt(rng, period_start, period_end)
            in_panel = flags[i]
            is_connect = i in connect_set
            sub_end = None
            field_bool_3 = False
            if is_connect:
                sub_end = create_user + timedelta(days=trial_days)
                field_bool_3 = True
                in_panel = True

            users.append(
                Users(
                    user_id=USER_ID_BASE + i,
                    bot_id=BOT_ID,
                    in_panel=in_panel,
                    is_connect=is_connect,
                    create_user=create_user,
                    subscription_end_date=sub_end,
                    field_bool_3=field_bool_3,
                    stamp=secrets.token_hex(4),
                    field_str_2=_profile(rng, create_user),
                    in_chanel=rng.random() < 0.6,
                    is_delete=False,
                    reserve_field=False,
                )
            )

        session.add_all(users)
        await session.flush()

        payers = rng.sample(users, min(PAYMENTS_TOTAL, len(users)))
        payments: list[PaymentsFkSBP] = []
        for idx, (user, tariff_key) in enumerate(zip(payers, tariff_keys)):
            devices = device_from_tariff_key(tariff_key)
            duration_days = tariff_days_for_x3(tariff_key)
            amount = prices[tariff_key]
            pay_time = _random_dt(
                rng,
                max(user.create_user, period_start),
                period_end + timedelta(hours=12),
            )
            sub_end = pay_time + timedelta(days=duration_days)

            if devices == 3:
                user.subscription_3_end_date = sub_end
            elif devices == 10:
                user.subscription_10_end_date = sub_end
            else:
                user.subscription_end_date = sub_end

            user.in_panel = True
            user.reserve_field = True

            payload = (
                f"user_id:{user.user_id},duration:{duration_days},white:False,"
                f"gift:False,method:fk_qr_sbp,amount:{amount},"
                f"device:{devices},bot_id:{BOT_ID},tariff:{tariff_key}"
            )
            payments.append(
                PaymentsFkSBP(
                    bot_id=BOT_ID,
                    user_id=user.user_id,
                    amount=amount,
                    time_created=pay_time,
                    is_gift=False,
                    status="confirmed",
                    transaction_id=f"seed_{args.seed}_{idx}_{secrets.token_hex(4)}",
                    fk_order_id=1_000_000 + idx,
                    payload=payload,
                    nonce=int(pay_time.timestamp() * 1_000_000) + idx,
                    signature=f"seed_sig_{secrets.token_hex(8)}",
                    method="fk_qr_sbp",
                )
            )

        session.add_all(payments)

        settings = (
            await session.execute(
                select(PartnerBotSettings).where(PartnerBotSettings.bot_id == BOT_ID)
            )
        ).scalar_one_or_none()
        if settings is None:
            session.add(
                PartnerBotSettings(
                    bot_id=BOT_ID,
                    owner_tg_id=args.owner_id,
                    partner_balance=partner_balance,
                    balance_own_bot=partner_balance,
                    balance_child_bots=0,
                    partner_pay=0,
                    trial_days=trial_days,
                    prices_json=json.dumps(DEFAULT_PRICES),
                    partner_since=period_start,
                )
            )
        else:
            await session.execute(
                update(PartnerBotSettings)
                .where(PartnerBotSettings.bot_id == BOT_ID)
                .values(
                    partner_balance=partner_balance,
                    balance_own_bot=partner_balance,
                    owner_tg_id=args.owner_id,
                )
            )

        await session.commit()

    async with AsyncSessionLocal() as session:
        total = (
            await session.execute(
                select(Users).where(Users.bot_id == BOT_ID, Users.is_delete == False)
            )
        ).scalars().all()
        seeded = [u for u in total if u.user_id >= USER_ID_BASE]
        in_panel = sum(1 for u in seeded if u.in_panel)
        is_connect = sum(1 for u in seeded if u.is_connect)
        with_trial = sum(1 for u in seeded if u.field_bool_3 and u.subscription_end_date)
        paid = sum(1 for u in seeded if u.reserve_field)
        pay_rows = (
            await session.execute(
                select(PaymentsFkSBP).where(
                    PaymentsFkSBP.bot_id == BOT_ID,
                    PaymentsFkSBP.status == "confirmed",
                    PaymentsFkSBP.transaction_id.like(f"seed_{args.seed}_%"),
                )
            )
        ).scalars().all()
        settings = (
            await session.execute(
                select(PartnerBotSettings).where(PartnerBotSettings.bot_id == BOT_ID)
            )
        ).scalar_one()
        from collections import Counter
        tariff_counts = Counter()
        for p in pay_rows:
            if p.payload and "tariff:" in p.payload:
                tariff_counts[p.payload.split("tariff:")[-1]] += 1

    print(
        f"OK seeded_users={len(seeded)} in_panel={in_panel} "
        f"is_connect={is_connect} trial={with_trial} paid_users={paid}\n"
        f"payments={len(pay_rows)} sum={sum(p.amount for p in pay_rows)} RUB "
        f"tariffs={dict(tariff_counts)}\n"
        f"partner_balance={settings.partner_balance} "
        f"balance_own_bot={settings.balance_own_bot}"
    )


def main() -> None:
    args = _parse_args()
    asyncio.run(seed(args))


if __name__ == "__main__":
    main()
