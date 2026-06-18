from bot import bot, sql
from config import ADMIN_IDS, BOT_ID
from keyboard import keyboard_payment_stars
from logging_config import logger

from aiogram import Router, F
from aiogram.types import CallbackQuery, LabeledPrice, PreCheckoutQuery, Message
from lexicon import dct_price, lexicon, payment_tariff_summary_pro
from payments.process_payload import process_confirmed_payment
from tariff_resolve import tariff_days_for_x3, device_from_tariff_key, tariff_rub_and_desc


router: Router = Router()


@router.callback_query(F.data.startswith('stars_'))
async def process_payment_stars(callback: CallbackQuery):
    gift_flag = False
    white_flag = False
    if 'gift_' in callback.data:
        gift_flag = True
    duration_key = callback.data.replace('stars_r_', '').replace('stars_gift_r_', '')

    white_flag = False
    if 'white' in duration_key:
        duration_plain = duration_key.replace('white_', '', 1)
        white_flag = True
    else:
        duration_plain = duration_key

    prices = await sql.get_prices()
    stars_amount, _ = tariff_rub_and_desc(duration_plain if not white_flag else duration_key, prices)
    if callback.from_user.id in ADMIN_IDS:
        stars_amount = 1
    user_id = str(callback.from_user.id)

    days_payload = str(tariff_days_for_x3(duration_plain))
    device_n = device_from_tariff_key(duration_plain)

    payload = (
        f"user_id:{user_id},duration:{days_payload},white:{white_flag},gift:{gift_flag},"
        f"method:stars,amount:{stars_amount},device:{device_n},bot_id:{BOT_ID}"
    )

    prices = [LabeledPrice(label="XTR", amount=stars_amount)]
    title = f"Оплата подписки {'в подарок другу ' if gift_flag else ''}на {days_payload} дней."
    if white_flag:
        description = lexicon['payment_link_white']
    else:
        description = payment_tariff_summary_pro(duration_key)
    await bot.send_invoice(
        callback.from_user.id,
        title=title,
        description=description,
        prices=prices,
        provider_token="",
        payload=payload,
        currency="XTR",
        reply_markup=keyboard_payment_stars(stars_amount),
    )


@router.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)


@router.message(F.content_type.in_({'successful_payment'}))
async def success_payment_handler(message: Message):
    payload = message.successful_payment.invoice_payload
    if not payload:
        logger.error(f"❌ Нет payload в платеже {message.successful_payment.invoice_payload}")
        return
    await process_confirmed_payment(payload)
