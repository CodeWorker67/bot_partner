"""Создание VPN-бота через заявку в мастер-бот."""
import asyncio

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from handlers.handlers_user import _main_keyboard
from keyboard import create_kb
from lexicon import lexicon
from logging_config import logger
from services.master_api_client import MasterApiError, submit_partner_bot_application

router = Router()


class CreatePartnerBotFSM(StatesGroup):
    waiting_token = State()


@router.callback_query(F.data == "create_partner_bot")
async def create_partner_bot_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(CreatePartnerBotFSM.waiting_token)
    await callback.message.edit_text(
        lexicon["create_partner_bot_prompt"],
        reply_markup=create_kb(1, cancel_partner_apply="❌ Отмена"),
    )
    await callback.answer()


@router.callback_query(F.data == "cancel_partner_apply")
async def cancel_partner_apply(callback: CallbackQuery, state: FSMContext):
    current = await state.get_state()
    if current != CreatePartnerBotFSM.waiting_token.state:
        await callback.answer()
        return
    await state.clear()
    await callback.message.edit_text(
        lexicon["create_partner_bot_cancelled"],
        reply_markup=await _main_keyboard(callback.from_user.id),
    )
    await callback.answer()


@router.message(CreatePartnerBotFSM.waiting_token)
async def create_partner_bot_token(message: Message, state: FSMContext):
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
    await message.answer(
        lexicon["create_partner_bot_success"],
        reply_markup=await _main_keyboard(message.from_user.id),
    )
