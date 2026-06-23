import secrets

from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message, ChatMemberUpdated

from bot import bot, sql, x3
from channel_gate import needs_channel_block, require_channel_sub, send_channel_required, verify_channel_subscription
from config import BOT_ID, BOT_URL, OWNER_TG_ID, PARTNER_MIN, PARTNER_PROCENT, PARTNER_SUPPORT_URL, REFERRAL_PROCENT, SUPPORT_URL
from keyboard import (
    channel_keyboard,
    keyboard_buy_tiers,
    keyboard_duration,
    keyboard_gift_duration,
    keyboard_gift_tiers,
    keyboard_main,
    keyboard_partner_dashboard,
    keyboard_partner_intro,
    keyboard_partner_withdraw,
    keyboard_payment_methods,
    keyboard_ref_dashboard,
    keyboard_sub_after_buy,
    keyboard_subscription,
)
from lexicon import lexicon, payment_tariff_summary_pro
from logging_config import logger
from tariff_resolve import device_from_tariff_key, get_prices, panel_username, tariff_days_for_x3, tariff_rub_and_desc

router = Router()


async def _main_keyboard(user_id: int, *, welcome_only: bool = False):
    user = await sql.get_user_object_by_user_id(user_id)
    show_trial = not (user and user.in_panel)
    is_owner = user_id == OWNER_TG_ID
    return keyboard_main(
        show_owner_panel=is_owner,
        welcome_only=welcome_only,
        show_trial=show_trial,
    )


async def _ensure_user(message: Message, ref: str = "") -> None:
    tg_id = message.from_user.id
    if not await sql.get_user(tg_id):
        await sql.add_user(
            tg_id,
            in_panel=False,
            ref=ref,
            stamp=secrets.token_hex(4),
        )
    await sql.sync_user_profile(
        tg_id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
        language=message.from_user.language_code,
    )


async def _send_main_menu(
    target: Message | CallbackQuery,
    user_id: int,
    *,
    edit: bool = False,
) -> None:
    user = await sql.get_user_object_by_user_id(user_id)
    welcome_only = not (user and user.in_panel)
    text = lexicon["start_bonus"] if welcome_only else lexicon["start"]
    kb = await _main_keyboard(user_id, welcome_only=welcome_only)
    if isinstance(target, CallbackQuery):
        if edit:
            await target.message.edit_text(text, reply_markup=kb)
        else:
            await target.message.answer(text, reply_markup=kb)
    elif edit:
        await target.edit_text(text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)


@router.message(CommandStart())
async def process_start_command(message: Message):
    ref_login = ""
    partner_login = ""
    start_arg = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else ""
    if start_arg.startswith("partner_"):
        raw_partner = start_arg.replace("partner_", "", 1)
        if raw_partner.isdigit() and raw_partner != str(message.from_user.id):
            partner_login = raw_partner
    elif start_arg.startswith("ref"):
        raw = start_arg.replace("ref", "", 1)
        if raw.isdigit() and raw != str(message.from_user.id):
            ref_login = raw
    elif start_arg.startswith("gift_"):
        gift_id = start_arg.replace("gift_", "", 1)
        await _activate_gift(message, gift_id)
        return

    is_new = await sql.add_user(
        message.from_user.id,
        in_panel=False,
        ref=ref_login,
        partner=partner_login,
        stamp=secrets.token_hex(4),
    )
    await sql.sync_user_profile(
        message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
        language=message.from_user.language_code,
    )
    if is_new and ref_login:
        await sql.try_set_ref_from_invite(message.from_user.id, ref_login)

    blocked, url = await needs_channel_block(message.from_user.id)
    if blocked:
        await send_channel_required(message, url or "")
        return

    await _send_main_menu(message, message.from_user.id)


async def _activate_gift(message: Message, gift_id: str):
    gift = await sql.get_gift(gift_id)
    if not gift or gift.flag:
        await message.answer("❌ Подарок не найден или уже активирован.")
        return
    tg_id = message.from_user.id
    await sql.activate_gift(gift_id, tg_id)
    user_id_str = panel_username(tg_id, BOT_ID, device_slots=gift.device_slots or 5)
    days = gift.duration
    existing = await x3.get_user_by_username(user_id_str)
    if existing and existing.get("response"):
        await x3.updateClient(days, user_id_str, tg_id)
    else:
        await x3.addClient(days, user_id_str, tg_id, hwid_device_limit=gift.device_slots or 5)
    await sql.update_in_panel(tg_id)
    result = await x3.activ(user_id_str)
    sub_time = result.get("time", "-")
    await message.answer(
        lexicon["gift_activated"].format(sub_time),
        reply_markup=keyboard_sub_after_buy(result.get("url", "")),
    )


@router.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery):
    blocked, url = await needs_channel_block(callback.from_user.id)
    if blocked:
        await callback.message.edit_text(
            lexicon["channel_required"],
            reply_markup=channel_keyboard(url or ""),
        )
        await callback.answer()
        return
    await _send_main_menu(callback, callback.from_user.id, edit=True)
    await callback.answer()


@router.callback_query(F.data == "channel_sub_check")
async def channel_sub_check(callback: CallbackQuery):
    settings = await sql.get_bot_settings()
    if not settings or not settings.get("channel_required"):
        await _send_main_menu(callback, callback.from_user.id, edit=True)
        await callback.answer()
        return

    if await verify_channel_subscription(callback.from_user.id):
        await sql.update_in_chanel(callback.from_user.id, True)
        await _send_main_menu(callback, callback.from_user.id, edit=True)
        await callback.answer()
        return

    await callback.answer(lexicon["channel_not_subscribed"], show_alert=True)


@router.callback_query(F.data == "buy_vpn")
@require_channel_sub
async def buy_vpn_cb(callback: CallbackQuery):
    await callback.message.edit_text(lexicon["buy"], reply_markup=keyboard_buy_tiers())
    await callback.answer()


@router.callback_query(F.data.startswith("buy_tier_"))
@require_channel_sub
async def buy_tier_chosen(callback: CallbackQuery):
    tier = callback.data.replace("buy_tier_", "")
    device = int(tier)
    prices = await get_prices(sql)
    await callback.message.edit_text(
        lexicon["choose_tariff"],
        reply_markup=keyboard_duration(device, prefix="r", prices=prices),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("r_m"))
@require_channel_sub
async def process_payment_method(callback: CallbackQuery):
    prices = await get_prices(sql)
    tarif_cb = callback.data
    price_key = tarif_cb.replace("r_", "", 1)
    amount, desc = tariff_rub_and_desc(price_key, prices)
    device = device_from_tariff_key(price_key)
    summary = payment_tariff_summary_pro(price_key, prices)
    await callback.message.edit_text(
        summary,
        reply_markup=keyboard_payment_methods(tarif_cb, amount, is_gift=False),
    )
    await callback.answer()


@router.callback_query(F.data == "trial_vpn")
@require_channel_sub
async def trial_vpn_cb(callback: CallbackQuery):
    user = await sql.get_user_object_by_user_id(callback.from_user.id)
    if user and user.field_bool_3:
        await callback.answer(lexicon["trial_already"], show_alert=True)
        return
    settings = await sql.get_bot_settings()
    days = (settings or {}).get("trial_days", 3)
    tg_id = callback.from_user.id
    user_id_str = panel_username(tg_id, BOT_ID, device_slots=5)
    ok = await x3.addClient(days, user_id_str, tg_id, hwid_device_limit=5)
    if not ok:
        await callback.answer("❌ Не удалось активировать триал.", show_alert=True)
        return
    await sql.update_in_panel(tg_id)
    await sql.set_field_bool_3(tg_id, True)
    result = await x3.activ(user_id_str)
    await callback.message.edit_text(
        lexicon["trial_success"].format(days, result.get("time", "-")),
        reply_markup=keyboard_sub_after_buy(result.get("url", "")),
    )
    await callback.answer()


@router.callback_query(F.data == "connect_vpn")
@require_channel_sub
async def connect_vpn_cb(callback: CallbackQuery):
    tg_id = callback.from_user.id
    links = await x3.active_subscription_links(tg_id, BOT_ID)
    if not links:
        await callback.answer(lexicon["no_sub"], show_alert=True)
        return
    await callback.message.edit_text(
        lexicon["to_sub"],
        reply_markup=keyboard_subscription(links),
    )
    await callback.answer()


@router.callback_query(F.data == "ref_program")
async def ref_program_cb(callback: CallbackQuery):
    tg_id = callback.from_user.id
    count = await sql.select_ref_count(tg_id)
    user = await sql.get_user_object_by_user_id(tg_id)
    balance = (user.ref_balance or 0) if user else 0
    link = f"{BOT_URL}?start=ref{tg_id}"
    await callback.message.edit_text(
        lexicon["ref_info"].format(count, tg_id, REFERRAL_PROCENT, balance, link),
        reply_markup=keyboard_ref_dashboard(),
    )
    await callback.answer()


async def _send_partner_dashboard(callback: CallbackQuery) -> None:
    tg_id = callback.from_user.id
    user = await sql.get_user_object_by_user_id(tg_id)
    referrals = await sql.select_partner_count(tg_id)
    payments_sum = await sql.select_partner_referrals_payments_sum(tg_id)
    balance = (user.partner_balance or 0) if user else 0
    paid_out = (user.partner_pay or 0) if user else 0
    total_earned = balance + paid_out
    link = f"{BOT_URL}?start=partner_{tg_id}"
    await callback.message.edit_text(
        lexicon["partner_dashboard"].format(
            link=link,
            procent=PARTNER_PROCENT,
            referrals=referrals,
            payments_sum=payments_sum,
            total_earned=total_earned,
            paid_out=paid_out,
            balance=balance,
        ),
        parse_mode="HTML",
        reply_markup=keyboard_partner_dashboard(),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data == "partner_earn")
async def partner_program(callback: CallbackQuery):
    await callback.answer()
    user = await sql.get_user_object_by_user_id(callback.from_user.id)
    if user and user.partner_flag:
        await _send_partner_dashboard(callback)
    else:
        await callback.message.edit_text(
            lexicon["partner_intro"].format(
                procent=PARTNER_PROCENT,
                min_sum=PARTNER_MIN,
            ),
            parse_mode="HTML",
            reply_markup=keyboard_partner_intro(),
        )


@router.callback_query(F.data == "partner_create_link")
async def partner_create_link(callback: CallbackQuery):
    await callback.answer()
    await sql.update_partner_flag(callback.from_user.id, True)
    await _send_partner_dashboard(callback)


@router.callback_query(F.data == "partner_withdraw")
async def partner_withdraw(callback: CallbackQuery):
    user = await sql.get_user_object_by_user_id(callback.from_user.id)
    if user is None:
        await callback.answer()
        return

    balance = user.partner_balance or 0
    if balance < PARTNER_MIN:
        await callback.answer(
            lexicon["partner_withdraw_alert"].format(min_sum=PARTNER_MIN),
            show_alert=True,
        )
        return

    await callback.answer()
    support_url = PARTNER_SUPPORT_URL or "https://t.me/"
    await callback.message.edit_text(
        lexicon["partner_withdraw_info"].format(
            balance=balance,
            min_sum=PARTNER_MIN,
        ),
        parse_mode="HTML",
        reply_markup=keyboard_partner_withdraw(support_url),
    )


@router.callback_query(F.data == "buy_gift")
@require_channel_sub
async def gift_start(callback: CallbackQuery):
    await callback.message.edit_text(lexicon["gift_start"], reply_markup=keyboard_gift_tiers())
    await callback.answer()


@router.callback_query(F.data.startswith("gift_tier_"))
async def gift_tier_chosen(callback: CallbackQuery):
    device = int(callback.data.replace("gift_tier_", ""))
    prices = await get_prices(sql)
    await callback.message.edit_text(
        lexicon["choose_tariff"],
        reply_markup=keyboard_gift_duration(device, prices=prices),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("gift_r_m"))
async def gift_payment_method(callback: CallbackQuery):
    prices = await get_prices(sql)
    tarif_cb = callback.data.replace("gift_", "")
    price_key = tarif_cb.replace("r_", "", 1)
    amount, desc = tariff_rub_and_desc(price_key, prices)
    device = device_from_tariff_key(price_key)
    summary = payment_tariff_summary_pro(price_key, prices)
    await callback.message.edit_text(
        summary,
        reply_markup=keyboard_payment_methods(tarif_cb, amount, is_gift=True),
    )
    await callback.answer()


@router.chat_member()
async def handle_chat_member_update(event: ChatMemberUpdated):
    settings = await sql.get_bot_settings()
    if not settings or not settings.get("channel_id"):
        return
    if event.chat.id != settings["channel_id"]:
        return
    user_id = event.from_user.id
    if not await sql.get_user(user_id):
        return
    new_status = event.new_chat_member.status
    if new_status in ("member", "administrator", "creator"):
        await sql.update_in_chanel(user_id, True)
    elif new_status in ("left", "kicked", "banned"):
        await sql.update_in_chanel(user_id, False)


@router.message(Command("panel"))
async def panel_command(message: Message):
    if message.from_user.id != OWNER_TG_ID:
        return
    from handlers.handlers_owner import send_owner_menu
    await send_owner_menu(message)
