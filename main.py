import asyncio

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot import bot
from bot_display import init_bot_display_name
from config import BOT_ID, OWNER_TG_ID, TG_TOKEN
from config_bd.models import create_tables, engine
from handlers import handlers_user, handlers_devices, handlers_owner, handlers_import, handlers_partner_admin, handlers_create_partner_bot
from logging_config import logger
from middleware.user_activity import UserActivityMiddleware
from payments import pay_cryptobot, pay_freekassa, pay_stars
from sheduler.backup_db import send_db_backup_cron
from sheduler.check_connect import check_connect
from sheduler.check_cryptobot import check_cryptobot_payments
from sheduler.check_fk import check_fk
from sheduler.check_online import check_online_daily
from sheduler.time_mes import send_message_cron
from sheduler.time_mes_not_sub import send_push_cron


async def set_commands(bot: Bot):
    commands = [BotCommand(command="start", description="Запустить бота")]
    await bot.set_my_commands(commands)


async def main() -> None:
    if not BOT_ID or not TG_TOKEN:
        raise RuntimeError("BOT_ID and TG_TOKEN must be set in .env")

    await create_tables()

    dp = Dispatcher()
    dp.update.middleware(UserActivityMiddleware())
    dp.include_router(handlers_owner.router)
    dp.include_router(handlers_create_partner_bot.router)
    dp.include_router(handlers_partner_admin.router)
    dp.include_router(handlers_user.router)
    dp.include_router(handlers_import.router)
    dp.include_router(handlers_devices.router)
    dp.include_router(pay_freekassa.router)
    dp.include_router(pay_stars.router)
    dp.include_router(pay_cryptobot.router)

    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    scheduler.add_job(send_message_cron, trigger="interval", minutes=10, args=[bot], misfire_grace_time=120)
    scheduler.add_job(check_connect, trigger="interval", minutes=14, misfire_grace_time=60)
    scheduler.add_job(check_fk, trigger="interval", minutes=1, misfire_grace_time=10)
    scheduler.add_job(check_cryptobot_payments, trigger="interval", minutes=1, misfire_grace_time=10)
    scheduler.add_job(send_push_cron, trigger="interval", minutes=30, misfire_grace_time=60)
    scheduler.add_job(check_online_daily, "cron", hour=2, minute=55, id="daily_online_stats", misfire_grace_time=60)
    scheduler.add_job(
        send_db_backup_cron,
        trigger="interval",
        minutes=30,
        args=[bot],
        id="db_backup_checker",
        max_instances=30,
        misfire_grace_time=300,
    )
    scheduler.start()

    await init_bot_display_name(bot)
    await set_commands(bot)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Partner bot {} (owner {}) started polling.", BOT_ID, OWNER_TG_ID)
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        await engine.dispose()
        logger.info("Bot session closed.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped manually.")
