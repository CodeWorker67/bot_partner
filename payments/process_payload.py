from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from bot import bot, sql, x3
from config import (
    BOT_ID,
    DEPLOYED_BOT_PROCENT,
    LEAD_TRACKER_STAR_RUB_PER_STAR,
    OWNER_TG_ID,
    PARTNER_PROCENT,
    PARTNER_SHARE_DEFAULT,
    PARTNER_SHARE_REF,
    REFERRAL_PROCENT,
    SOURCE_BOT_ID,
)
from keyboard import BTN_BACK, create_kb, keyboard_sub_after_buy
from lead_tracker import post_payment_success
from lexicon import lexicon
from logging_config import logger
from tariff_resolve import panel_username


def _payment_rub(method: str, amount: int | float) -> int:
    if method == "stars":
        stars = Decimal(str(amount))
        rate = Decimal(str(LEAD_TRACKER_STAR_RUB_PER_STAR))
        return int((stars * rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return int(Decimal(str(amount)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


async def _credit_partner_commission(payer_uid: int, method: str, amount: int | float) -> None:
    user_row = await sql.get_user(payer_uid)
    if not user_row or len(user_row) <= 25:
        return
    partner_str = user_row[25]
    if not partner_str:
        return
    try:
        partner_id = int(partner_str)
    except ValueError:
        return
    if partner_id <= 0 or partner_id == payer_uid:
        return

    rub = _payment_rub(method, amount)
    commission = rub * PARTNER_PROCENT // 100
    if commission <= 0:
        return

    credited = await sql.add_user_partner_balance(partner_id, commission)
    if not credited:
        logger.warning("Партнёр {} не найден, начисление {} ₽ пропущено", partner_id, commission)
        return

    try:
        await bot.send_message(
            partner_id,
            lexicon["partner_success"].format(commission),
            reply_markup=create_kb(1, back_to_main=BTN_BACK),
        )
    except Exception as e:
        logger.error("partner notify {}: {}", partner_id, e)


async def _distribute_commissions(payer_uid: int, method: str, amount: int | float) -> None:
    rub = _payment_rub(method, amount)
    if rub <= 0:
        return

    user_row = await sql.get_user(payer_uid)
    ref_id_str = user_row[2] if user_row else None

    if ref_id_str:
        try:
            ref_id = int(ref_id_str)
            if ref_id > 0 and ref_id != payer_uid:
                ref_commission = rub * REFERRAL_PROCENT // 100
                if ref_commission > 0:
                    await sql.add_ref_balance(ref_id, ref_commission)
                    try:
                        await bot.send_message(
                            ref_id,
                            lexicon["ref_commission_success"].format(ref_commission),
                            reply_markup=create_kb(1, back_to_main=BTN_BACK),
                        )
                    except Exception as e:
                        logger.error("ref notify {}: {}", ref_id, e)
        except ValueError:
            pass

    owner_share = PARTNER_SHARE_REF if ref_id_str else PARTNER_SHARE_DEFAULT
    owner_commission = rub * owner_share // 100
    if owner_commission > 0 and payer_uid != OWNER_TG_ID:
        await sql.add_partner_balance(owner_commission)
        try:
            await bot.send_message(
                OWNER_TG_ID,
                lexicon["owner_commission_success"].format(owner_commission, payer_uid),
            )
        except Exception as e:
            logger.error("owner notify: {}", e)

    if SOURCE_BOT_ID and SOURCE_BOT_ID != BOT_ID:
        parent_commission = rub * DEPLOYED_BOT_PROCENT // 100
        if parent_commission > 0:
            await sql.add_child_bot_balance(SOURCE_BOT_ID, parent_commission)
            logger.info(
                "child bot commission: {} ₽ to source bot #{} from bot #{}",
                parent_commission,
                SOURCE_BOT_ID,
                BOT_ID,
            )


async def process_confirmed_payment(payload: str) -> None:
    try:
        payload_parts = dict(item.split(":") for item in payload.split(","))
        user_id = int(payload_parts.get("user_id", 0))
        duration = int(payload_parts.get("duration", 0))
        is_gift = payload_parts.get("gift", "False") == "True"
        method = payload_parts.get("method", "")
        if method in ("sbp", "fksbp", "fk_sbp", "fk_card", "stars", "card", "crypto", "cryptobot"):
            amount = int(payload_parts.get("amount", 0))
        else:
            amount = float(payload_parts.get("amount", 0.0))

        device_raw = payload_parts.get("device")
        try:
            device_slots = int(device_raw) if device_raw is not None else 5
        except (TypeError, ValueError):
            device_slots = 5
        if device_slots not in (3, 5, 10):
            device_slots = 5

        if method == "stars":
            await sql.add_payment_stars(user_id, amount, is_gift, payload)

        if is_gift:
            gift_id = await sql.create_gift(user_id, duration, device_slots)
            await post_payment_success(user_id, method, amount)
            await _distribute_commissions(user_id, method, amount)
            await _credit_partner_commission(user_id, method, amount)
            gift_message = lexicon["payment_gift"].format(duration, "", gift_id)
            try:
                await bot.send_message(user_id, gift_message, disable_web_page_preview=True)
                await bot.send_message(
                    user_id,
                    lexicon["payment_gift_faq"],
                    reply_markup=create_kb(1, back_to_main=BTN_BACK),
                )
            except Exception as e:
                logger.error("gift msg: {}", e)
            return

        user_id_str = panel_username(user_id, BOT_ID, device_slots=device_slots)
        existing = await x3.get_user_by_username(user_id_str)
        if existing and existing.get("response"):
            response = await x3.updateClient(duration, user_id_str, user_id)
        else:
            response = await x3.addClient(duration, user_id_str, user_id, hwid_device_limit=device_slots)

        if not response:
            logger.error("panel update failed for {}", user_id_str)
            return

        result_active = await x3.activ(user_id_str)
        subscription_time = result_active.get("time", "-")
        if subscription_time != "-":
            try:
                end_date = datetime.strptime(subscription_time, "%d-%m-%Y %H:%M МСК")
                if device_slots == 3:
                    await sql.update_subscription_3_end_date(user_id, end_date)
                elif device_slots == 10:
                    await sql.update_subscription_10_end_date(user_id, end_date)
                else:
                    await sql.update_subscription_end_date(user_id, end_date)
            except ValueError as e:
                logger.error("date parse: {}", e)

        await sql.update_in_panel(user_id)
        await sql.update_reserve_field(user_id)
        await post_payment_success(user_id, method, amount)
        await _distribute_commissions(user_id, method, amount)
        await _credit_partner_commission(user_id, method, amount)

        sub_link = result_active.get("url", "-")
        try:
            await bot.send_message(
                user_id,
                lexicon["buy_success"].format(subscription_time, sub_link),
                reply_markup=keyboard_sub_after_buy(sub_link),
            )
        except Exception as e:
            logger.error("buy notify: {}", e)

    except Exception as e:
        logger.exception("process_confirmed_payment: {}", e)
