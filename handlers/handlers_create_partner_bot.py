"""Создание VPN-бота через заявку в мастер-бот."""
import asyncio

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot import sql
from keyboard import create_kb
from lexicon import lexicon
from logging_config import logger
from services.master_api_client import MasterApiError, submit_partner_bot_application
from utils.menu_ui import edit_or_send_menu, show_main_menu

router = Router()


class CreatePartnerBotFSM(StatesGroup):
    waiting_token = State()


@router.callback_query(F.data == "create_partner_bot")
async def create_partner_bot_start(callback: CallbackQuery, state: FSMContext):
    if not await sql.is_partner_bot_creation_enabled():
        await callback.answer(lexicon["create_partner_bot_disabled"], show_alert=True)
        return
    await state.set_state(CreatePartnerBotFSM.waiting_token)
    await edit_or_send_menu(
        callback,
        lexicon["create_partner_bot_prompt"],
        create_kb(1, cancel_partner_apply="❌ Отмена"),
    )
    await callback.answer()


@router.callback_query(F.data == "cancel_partner_apply")
async def cancel_partner_apply(callback: CallbackQuery, state: FSMContext):
    current = await state.get_state()
    if current != CreatePartnerBotFSM.waiting_token.state:
        await callback.answer()
        return
    await state.clear()
    await callback.answer()
    await edit_or_send_menu(
        callback,
        lexicon["create_partner_bot_cancelled"],
        create_kb(1, earn_with_us="◀️ Назад"),
    )


@router.message(CreatePartnerBotFSM.waiting_token)
async def create_partner_bot_token(message: Message, state: FSMContext):
    if not await sql.is_partner_bot_creation_enabled():
        await state.clear()
        await show_main_menu(message)
        return
    token = (message.text or "").strip()
    if not token:
        await message.answer("❌ Отправьте токен бота от @BotFather.")
        return

    try:
        await submit_partner_bot_application(
            partner_tg_id=message.from_user.id,
            partner_username=message.from_user.username,
            partner_first_name=message.from_user.first_name,
            bot_token=token,
        )
    except MasterApiError as e:
        await message.answer(f"❌ {e}")
        if "уже" in str(e).lower():
            await state.clear()
        return
    except asyncio.TimeoutError:
        await message.answer(
            "❌ Таймаут при отправке заявки. Мастер-бот недоступен с VPS партнёров — "
            "проверьте MASTER_BOT_API_URL в .env."
        )
        return
    except Exception as e:
        logger.exception("create partner bot application: {}", e)
        await message.answer("❌ Не удалось отправить заявку. Попробуйте позже.")
        return

    await state.clear()
    from utils.menu_ui import send_menu_message

    await send_menu_message(
        message.chat.id,
        lexicon["create_partner_bot_success"],
        create_kb(1, back_to_main="◀️ Назад"),
    )
