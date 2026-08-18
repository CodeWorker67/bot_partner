"""Обработчики профиля и покупки трафика Антиглушилка."""
from __future__ import annotations

from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.types import CallbackQuery

from bot import sql, x3
from keyboard import (
    BTN_BACK,
    create_kb,
    keyboard_profile,
    keyboard_wl_traffic_payment_method,
    keyboard_wl_traffic_tariffs,
)
from lexicon import lexicon
from wl_traffic.constants import (
    BUY_VPN_CB,
    PROFILE_CB,
    WL_TRAFFIC_BUY_CB,
    WL_TRAFFIC_BUY_SUB_CB,
    WL_TRAFFIC_TARIFFS,
)
from wl_traffic.service import get_wl_used_gb_for_user

router = Router()


def _format_pro_sub_end(user) -> str:
    candidates = [
        user.subscription_end_date,
        user.subscription_3_end_date,
        user.subscription_10_end_date,
    ]
    ends = [d for d in candidates if d is not None]
    if not ends:
        return "—"
    sub_end = max(ends)
    if sub_end.tzinfo is None:
        aware = sub_end.replace(tzinfo=timezone.utc)
    else:
        aware = sub_end.astimezone(timezone.utc)
    if aware <= datetime.now(timezone.utc):
        return "истекла"
    return aware.strftime("%d.%m.%Y")


@router.callback_query(F.data == PROFILE_CB)
async def user_profile_cb(callback: CallbackQuery):
    await callback.answer()
    uid = callback.from_user.id
    user = await sql.get_user_object_by_user_id(uid)
    if not user:
        await callback.message.answer(
            "❌ Профиль не найден.",
            reply_markup=create_kb(1, back_to_main=BTN_BACK),
        )
        return

    trafic_wl, limit_gb = await sql.get_wl_limits(uid)
    used_gb = await get_wl_used_gb_for_user(x3, uid, trafic_wl)
    remaining_gb = max(0.0, round(limit_gb - used_gb, 2))

    await callback.message.answer(
        text=lexicon["user_profile"].format(
            sub_end=_format_pro_sub_end(user),
            limit_gb=limit_gb,
            used_gb=used_gb,
            remaining_gb=remaining_gb,
        ),
        parse_mode="HTML",
        reply_markup=keyboard_profile(),
    )


@router.callback_query(F.data.in_({WL_TRAFFIC_BUY_CB, WL_TRAFFIC_BUY_SUB_CB}))
async def wl_traffic_buy_cb(callback: CallbackQuery):
    back_callback = PROFILE_CB if callback.data == WL_TRAFFIC_BUY_CB else BUY_VPN_CB
    await callback.answer()
    await callback.message.answer(
        text=lexicon["wl_traffic_buy_prompt"],
        parse_mode="HTML",
        reply_markup=keyboard_wl_traffic_tariffs(back_callback=back_callback),
    )


@router.callback_query(F.data.regexp(r"^wl_traffic(_sub)?_\d+$"))
async def wl_traffic_tariff_cb(callback: CallbackQuery):
    await callback.answer()
    data = callback.data or ""
    from_sub = data.startswith("wl_traffic_sub_")
    gb = data.rsplit("_", 1)[-1]
    if gb not in WL_TRAFFIC_TARIFFS:
        return

    price = WL_TRAFFIC_TARIFFS[gb]
    back_cb = WL_TRAFFIC_BUY_SUB_CB if from_sub else WL_TRAFFIC_BUY_CB
    await callback.message.answer(
        text=lexicon["wl_traffic_payment_intro"].format(gb=gb, price=price),
        parse_mode="HTML",
        reply_markup=keyboard_wl_traffic_payment_method(gb, back_callback=back_cb),
    )
