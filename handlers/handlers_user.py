from html import escape

from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    ChatMemberUpdated,
    InaccessibleMessage,
    InputMediaPhoto,
    InlineKeyboardMarkup,
    Message,
)

from bot import bot, sql, x3
from bot_display import is_forever_tariff_bot
from channel_gate import needs_channel_block, require_channel_sub, send_channel_required, verify_channel_subscription
from config import BOT_ID, BOT_URL, PARTNER_MIN_WITHDRAW, PARTNER_PROCENT, PARTNER_SUPPORT_URL, REFERRAL_PROCENT, SUPPORT_URL
from keyboard import (
    channel_keyboard,
    keyboard_buy_menu,
    keyboard_buy_tiers,
    keyboard_duration,
    keyboard_earn_with_us,
    keyboard_gift_duration,
    keyboard_gift_tiers,
    keyboard_partner_dashboard,
    keyboard_partner_intro,
    partner_bot_link,
    keyboard_partner_withdraw,
    keyboard_payment_methods,
    keyboard_ref_dashboard,
    keyboard_sub_after_buy,
    keyboard_subscription_manage,
)
from utils.custom_emoji import emojify
from utils.menu_ui import (
    MAIN_MENU_BUTTON_TEXT,
    edit_or_send_menu,
    has_any_device_subscription,
    send_menu_message,
    show_main_menu,
    subscription_manage_caption,
)
from utils.ref_qr import referral_link_qr_png
from lexicon import lexicon, payment_tariff_summary_pro
from lead_tracker import (
    post_user_registered,
    post_user_trial,
    tracker_source_from_ref_and_stamp,
)
from handlers.handlers_start_prize import schedule_start_prize
from logging_config import logger
from tariff_resolve import device_from_tariff_key, get_prices, panel_username, tariff_days_for_x3, tariff_rub_and_desc

router = Router()


async def _ensure_user(message: Message, ref: str = "", stamp: str = "") -> None:
    tg_id = message.from_user.id
    if not await sql.get_user(tg_id):
        await sql.add_user(
            tg_id,
            in_panel=False,
            ref=ref,
            stamp=stamp,
        )
    await sql.sync_user_profile(
        tg_id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
        language=message.from_user.language_code,
    )


@router.message(CommandStart())
async def process_start_command(message: Message):
    ref_login = ""
    partner_login = ""
    stamp = ""
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
    elif start_arg:
        stamp = start_arg

    is_new = await sql.add_user(
        message.from_user.id,
        in_panel=False,
        ref=ref_login,
        partner=partner_login,
        stamp=stamp,
    )
    await sql.sync_user_profile(
        message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
        language=message.from_user.language_code,
    )
    if is_new and ref_login:
        await sql.try_set_ref_from_invite(message.from_user.id, ref_login)

    if is_new:
        src = tracker_source_from_ref_and_stamp(ref_login, stamp, partner_login)
        await post_user_registered(
            message.from_user.id,
            message.from_user.username,
            message.from_user.full_name,
            src,
        )
        schedule_start_prize(message.from_user.id)

    blocked, url = await needs_channel_block(message.from_user.id)
    if blocked:
        await send_channel_required(message, url or "")
        return

    await show_main_menu(message, send_hint=is_new)


async def _activate_gift(message: Message, gift_id: str):
    gift = await sql.get_gift(gift_id)
    if not gift or gift.flag:
        await message.answer("❌ Подарок не найден или уже активирован.")
        return
    tg_id = message.from_user.id
    is_new = await sql.add_user(tg_id, in_panel=False)
    await sql.sync_user_profile(
        tg_id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
        language=message.from_user.language_code,
    )
    if is_new:
        await post_user_registered(
            tg_id,
            message.from_user.username,
            message.from_user.full_name,
            None,
        )
    await sql.activate_gift(gift_id, tg_id)
    user_id_str = panel_username(tg_id, BOT_ID, device_slots=gift.device_slots or 5)
    days = gift.duration
    existing = await x3.get_user_by_username(user_id_str)
    created_in_panel = not (existing and existing.get("response"))
    if existing and existing.get("response"):
        await x3.updateClient(days, user_id_str, tg_id)
    else:
        await x3.addClient(days, user_id_str, tg_id, hwid_device_limit=gift.device_slots or 5)
    await sql.update_in_panel(tg_id)
    if created_in_panel:
        await post_user_trial(tg_id)
    result = await x3.activ(user_id_str)
    sub_time = result.get("time", "-")
    from wl_traffic.service import apply_wl_subscription_bonus
    await apply_wl_subscription_bonus(sql, x3, tg_id, int(days))
    await send_menu_message(
        message.chat.id,
        lexicon["gift_activated"].format(sub_time),
        keyboard_sub_after_buy(result.get("url", "")),
    )


@router.message(F.text == MAIN_MENU_BUTTON_TEXT)
async def main_menu_reply(message: Message):
    blocked, url = await needs_channel_block(message.from_user.id)
    if blocked:
        await send_channel_required(message, url or "")
        return
    await show_main_menu(message)


@router.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery):
    blocked, url = await needs_channel_block(callback.from_user.id)
    if blocked:
        await edit_or_send_menu(
            callback,
            lexicon["channel_required"],
            channel_keyboard(
                url or "",
                show_owner_panel=await sql.can_access_partner_panel(callback.from_user.id),
            ),
        )
        await callback.answer()
        return
    await show_main_menu(callback)
    await callback.answer()


@router.callback_query(F.data == "channel_sub_check")
async def channel_sub_check(callback: CallbackQuery):
    settings = await sql.get_bot_settings()
    if not settings or not settings.get("channel_required"):
        await show_main_menu(callback)
        await callback.answer()
        return

    if await verify_channel_subscription(callback.from_user.id):
        await sql.update_in_chanel(callback.from_user.id, True)
        await show_main_menu(callback)
        await callback.answer()
        return

    await callback.answer(lexicon["channel_not_subscribed"], show_alert=True)


@router.callback_query(F.data == "buy_vpn")
@require_channel_sub
async def buy_vpn_cb(callback: CallbackQuery):
    await callback.answer()
    await edit_or_send_menu(
        callback,
        lexicon["buy_menu"],
        keyboard_buy_menu(),
    )


@router.callback_query(F.data == "buy_vpn_self")
@require_channel_sub
async def buy_vpn_self_cb(callback: CallbackQuery):
    await callback.answer()
    await edit_or_send_menu(
        callback,
        lexicon["buy"],
        keyboard_buy_tiers(),
    )


@router.callback_query(F.data.startswith("buy_tier_"))
@require_channel_sub
async def buy_tier_chosen(callback: CallbackQuery):
    tier = callback.data.replace("buy_tier_", "")
    device = int(tier)
    prices = await get_prices(sql)
    await callback.answer()
    await edit_or_send_menu(
        callback,
        lexicon["choose_tariff"],
        keyboard_duration(device, prefix="r", prices=prices),
    )


@router.callback_query(F.data.startswith("r_m") | (F.data == "r_5000"))
@require_channel_sub
async def process_payment_method(callback: CallbackQuery):
    prices = await get_prices(sql)
    tarif_cb = callback.data
    price_key = tarif_cb.replace("r_", "", 1)
    if price_key == "5000" and not is_forever_tariff_bot():
        await callback.answer("Тариф недоступен", show_alert=True)
        return
    amount, desc = tariff_rub_and_desc(price_key, prices)
    device = device_from_tariff_key(price_key)
    summary = payment_tariff_summary_pro(price_key, prices)
    await callback.answer()
    await edit_or_send_menu(
        callback,
        summary,
        keyboard_payment_methods(tarif_cb, amount, is_gift=False),
    )


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
    await sql.init_wl_trial_limits(tg_id)
    await post_user_trial(tg_id)
    result = await x3.activ(user_id_str)
    await callback.answer()
    await edit_or_send_menu(
        callback,
        lexicon["trial_success"].format(days, result.get("time", "-")),
        keyboard_sub_after_buy(result.get("url", "")),
    )


@router.callback_query(F.data == "connect_vpn")
@require_channel_sub
async def connect_vpn_cb(callback: CallbackQuery):
    tg_id = callback.from_user.id
    user_obj = await sql.get_user_object_by_user_id(tg_id)
    if not has_any_device_subscription(user_obj):
        await callback.answer(lexicon["no_sub"], show_alert=True)
        return
    caption = await subscription_manage_caption(callback.from_user, user_obj, tg_id)
    await edit_or_send_menu(
        callback,
        caption,
        keyboard_subscription_manage(),
    )
    await callback.answer()


@router.callback_query(F.data == "ref_program")
async def ref_program_cb(callback: CallbackQuery):
    tg_id = callback.from_user.id
    count = await sql.select_ref_count(tg_id)
    user = await sql.get_user_object_by_user_id(tg_id)
    balance = (user.ref_balance or 0) if user else 0
    link = f"{BOT_URL}?start=ref{tg_id}"
    await callback.answer()
    await edit_or_send_menu(
        callback,
        lexicon["ref_info"].format(count, tg_id, REFERRAL_PROCENT, balance, link),
        keyboard_ref_dashboard(),
    )


async def _partner_dashboard_caption(tg_id: int) -> str:
    user = await sql.get_user_object_by_user_id(tg_id)
    referrals = await sql.select_partner_count(tg_id)
    try:
        payments_sum = await sql.select_partner_referrals_payments_sum(tg_id)
    except Exception as e:
        logger.warning("partner payments_sum failed user={}: {}", tg_id, e)
        payments_sum = 0
    balance = (user.partner_balance or 0) if user else 0
    paid_out = (user.partner_pay or 0) if user else 0
    total_earned = balance + paid_out
    available = balance
    link = partner_bot_link(tg_id)
    return lexicon["partner_dashboard"].format(
        link=escape(link),
        procent=PARTNER_PROCENT,
        referrals=referrals,
        payments_sum=payments_sum,
        total_earned=total_earned,
        paid_out=paid_out,
        balance=available,
    )


async def _partner_dashboard_keyboard(tg_id: int, *, show_bot_qr: bool = True):
    user = await sql.get_user_object_by_user_id(tg_id)
    available = (user.partner_balance or 0) if user else 0
    return keyboard_partner_dashboard(
        tg_id,
        show_withdraw=available >= PARTNER_MIN_WITHDRAW,
        show_bot_qr=show_bot_qr,
    )


async def _edit_message_qr_photo(
    callback: CallbackQuery,
    qr_url: str,
    caption: str,
    reply_markup: InlineKeyboardMarkup,
) -> None:
    message = callback.message
    if message is None or isinstance(message, InaccessibleMessage) or not message.photo:
        return
    qr_bytes = referral_link_qr_png(qr_url)
    try:
        await message.edit_media(
            media=InputMediaPhoto(
                media=BufferedInputFile(qr_bytes, filename="partner_qr.png"),
                caption=caption,
                parse_mode="HTML",
            ),
            reply_markup=reply_markup,
        )
    except TelegramBadRequest as e:
        logger.warning("partner QR edit_media failed uid={}: {}", callback.from_user.id, e)


async def _send_partner_dashboard(callback: CallbackQuery) -> None:
    tg_id = callback.from_user.id
    await edit_or_send_menu(
        callback,
        await _partner_dashboard_caption(tg_id),
        await _partner_dashboard_keyboard(tg_id),
    )


@router.callback_query(F.data == "earn_with_us")
async def earn_with_us_cb(callback: CallbackQuery):
    await callback.answer()
    show_create = await sql.is_partner_bot_creation_enabled()
    await edit_or_send_menu(
        callback,
        lexicon["earn_menu"],
        keyboard_earn_with_us(show_create_partner_bot=show_create),
    )


@router.callback_query(F.data == "back_to_earn")
async def back_to_earn_cb(callback: CallbackQuery):
    await callback.answer()
    show_create = await sql.is_partner_bot_creation_enabled()
    await edit_or_send_menu(
        callback,
        lexicon["earn_menu"],
        keyboard_earn_with_us(show_create_partner_bot=show_create),
    )


@router.callback_query(F.data == "partner_earn")
async def partner_program(callback: CallbackQuery):
    try:
        user = await sql.get_user_object_by_user_id(callback.from_user.id)
        if user and user.partner_flag:
            await _send_partner_dashboard(callback)
        else:
            await edit_or_send_menu(
                callback,
                lexicon["partner_intro"].format(
                    procent=PARTNER_PROCENT,
                    min_sum=PARTNER_MIN_WITHDRAW,
                ),
                keyboard_partner_intro(),
            )
    except Exception as e:
        logger.exception("partner_earn failed user={}: {}", callback.from_user.id, e)
        await callback.answer(
            "Не удалось открыть партнёрский раздел. Попробуйте позже.",
            show_alert=True,
        )
        return
    await callback.answer()


@router.callback_query(F.data == "partner_qr_bot")
async def partner_qr_bot_cb(callback: CallbackQuery):
    await callback.answer()
    uid = int(callback.from_user.id)
    caption = emojify(await _partner_dashboard_caption(uid))
    await _edit_message_qr_photo(
        callback,
        partner_bot_link(uid),
        caption,
        await _partner_dashboard_keyboard(uid, show_bot_qr=False),
    )


@router.callback_query(F.data == "partner_create_link")
async def partner_create_link(callback: CallbackQuery):
    try:
        await sql.update_partner_flag(callback.from_user.id, True)
        await _send_partner_dashboard(callback)
    except Exception as e:
        logger.exception("partner_create_link failed user={}: {}", callback.from_user.id, e)
        await callback.answer(
            "Не удалось создать ссылку. Попробуйте позже.",
            show_alert=True,
        )
        return
    await callback.answer()


@router.callback_query(F.data == "partner_withdraw")
async def partner_withdraw(callback: CallbackQuery):
    user = await sql.get_user_object_by_user_id(callback.from_user.id)
    if user is None:
        await callback.answer()
        return

    available = user.partner_balance or 0
    if available < PARTNER_MIN_WITHDRAW:
        await callback.answer(
            lexicon["partner_withdraw_alert"].format(min_sum=PARTNER_MIN_WITHDRAW),
            show_alert=True,
        )
        return

    await callback.answer()
    support_url = PARTNER_SUPPORT_URL or "https://t.me/"
    await edit_or_send_menu(
        callback,
        lexicon["partner_withdraw_info"].format(
            balance=available,
            min_sum=PARTNER_MIN_WITHDRAW,
        ),
        keyboard_partner_withdraw(support_url),
    )


@router.callback_query(F.data == "buy_gift")
@require_channel_sub
async def gift_start(callback: CallbackQuery):
    await callback.answer()
    await edit_or_send_menu(
        callback,
        lexicon["gift_start"],
        keyboard_gift_tiers(),
    )


@router.callback_query(F.data.startswith("gift_tier_"))
async def gift_tier_chosen(callback: CallbackQuery):
    device = int(callback.data.replace("gift_tier_", ""))
    prices = await get_prices(sql)
    await callback.answer()
    await edit_or_send_menu(
        callback,
        lexicon["choose_tariff"],
        keyboard_gift_duration(device, prices=prices),
    )


@router.callback_query(F.data.startswith("gift_r_m"))
async def gift_payment_method(callback: CallbackQuery):
    prices = await get_prices(sql)
    tarif_cb = callback.data.replace("gift_", "")
    price_key = tarif_cb.replace("r_", "", 1)
    amount, desc = tariff_rub_and_desc(price_key, prices)
    device = device_from_tariff_key(price_key)
    summary = payment_tariff_summary_pro(price_key, prices)
    await callback.answer()
    await edit_or_send_menu(
        callback,
        summary,
        keyboard_payment_methods(tarif_cb, amount, is_gift=True),
    )


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
    if not await sql.can_access_partner_panel(message.from_user.id):
        return
    from handlers.handlers_owner import send_owner_menu
    await send_owner_menu(message)
