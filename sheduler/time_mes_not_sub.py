from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from bot import bot, sql
from config import CHECKER_ID
from keyboard import create_kb, keyboard_push_buy_reviews, STYLE_PRIMARY, STYLE_SUCCESS
from lexicon import lexicon
from logging_config import logger
from telegram_ids import is_telegram_chat_id

VIDEO_FILE_ID = 'BAACAgQAAxkBAAEruMxqBamHrfafk-HiCQxgz0O7cKwgPQAC_SAAApwDMVCjetgWmRs7KDsE'

NOT_SUB_CYCLE_MINUTES = 7 * 24 * 60
NOT_CONNECT_CYCLE_MINUTES = 24 * 60


@dataclass(frozen=True)
class PushStage:
    window_start: int
    window_end: int
    lexicon_key: str
    with_video: bool = False
    keyboard: str = 'buy_reviews'


NOT_SUB_STAGES = (
    PushStage(30, 60, 'push_not_subscribed_30m', keyboard='buy_reviews'),
    PushStage(180, 210, 'push_not_subscribed_3h', with_video=True, keyboard='buy_reviews'),
    PushStage(1410, 1440, 'push_not_subscribed_day2_0h', keyboard='buy_reviews'),
    PushStage(2130, 2160, 'push_not_subscribed_day2_12h', keyboard='buy_reviews'),
    PushStage(2850, 2880, 'push_not_subscribed_day3_0h', keyboard='buy_reviews'),
    PushStage(4290, 4320, 'push_not_subscribed_day4_0h', keyboard='buy_reviews'),
    PushStage(5730, 5760, 'push_not_subscribed_day5_0h', keyboard='buy_reviews'),
    PushStage(7170, 7200, 'push_not_subscribed_day6_0h', keyboard='buy_reviews'),
    PushStage(8610, 8640, 'push_not_subscribed_day7_0h', keyboard='buy_reviews'),
)

NOT_CONNECT_STAGES = (
    PushStage(30, 60, 'push_not_connected_30m', keyboard='connect_mes'),
    PushStage(180, 210, 'push_not_connected_3h', with_video=True, keyboard='connect_video'),
    PushStage(1410, 1440, 'push_not_connected_24h', keyboard='connect_mes'),
)


def _find_stage(offset_minutes: int, stages: tuple[PushStage, ...]) -> Optional[PushStage]:
    for stage in stages:
        if stage.window_start <= offset_minutes <= stage.window_end:
            return stage
    return None


def _keyboard_for(stage: PushStage):
    if stage.keyboard == 'buy_reviews':
        return keyboard_push_buy_reviews()
    if stage.keyboard == 'connect_mes':
        return create_kb(
            1,
            styles={
                'connect_vpn': STYLE_PRIMARY,
                'video_faq': STYLE_PRIMARY,
            },
            connect_vpn='🔗 Подключить ВПН',
            video_faq='🎥 Видеоинструкция',
        )
    if stage.keyboard == 'connect_video':
        return create_kb(
            1,
            styles={'connect_vpn': STYLE_PRIMARY},
            connect_vpn='🔗 Подключить ВПН',
        )
    return None


async def _send_push(user_id: int, stage: PushStage) -> None:
    message_text = lexicon[stage.lexicon_key]
    keyboard = _keyboard_for(stage)
    if stage.with_video:
        await bot.send_video(
            chat_id=user_id,
            video=VIDEO_FILE_ID,
            caption=message_text,
            reply_markup=keyboard,
        )
    else:
        await bot.send_message(
            chat_id=user_id,
            text=message_text,
            reply_markup=keyboard,
        )


async def send_push_cron(debug: bool = False):
    """
    Push по этапам после регистрации (create_user):
    1) Нет в панели (in_panel=False) — недельный цикл из 9 сообщений.
    2) В панели, но VPN не подключён (is_connect=False) — суточный цикл из 3 пушей.
    """
    try:
        all_users = await sql.select_all_users()

        if not all_users:
            logger.info("Нет пользователей для отправки push-уведомлений")
            return

        sent_count_not_sub = 0
        failed_count_not_sub = 0
        sent_count_not_connect = 0
        failed_count_not_connect = 0
        failed_count = 0
        now = datetime.now()

        for user_id in all_users:
            if not is_telegram_chat_id(user_id):
                continue
            try:
                user_data = await sql.get_user(user_id)
                if not user_data:
                    continue

                create_time = user_data[6]
                if not create_time:
                    continue

                minutes_diff = (now - create_time).total_seconds() / 60
                in_panel = user_data[4]
                is_connect = user_data[5]

                if not in_panel:
                    offset = minutes_diff % NOT_SUB_CYCLE_MINUTES
                    stage = _find_stage(int(offset), NOT_SUB_STAGES)
                    if stage:
                        try:
                            await _send_push(user_id, stage)
                            sent_count_not_sub += 1
                            logger.info(
                                f"Отправлено push-уведомление (не в панели) пользователю {user_id}"
                            )
                        except Exception as e:
                            failed_count_not_sub += 1
                            logger.error(f"Не удалось отправить сообщение пользователю {user_id}: {e}")

                elif not is_connect:
                    offset = minutes_diff % NOT_CONNECT_CYCLE_MINUTES
                    stage = _find_stage(int(offset), NOT_CONNECT_STAGES)
                    if stage:
                        try:
                            await _send_push(user_id, stage)
                            sent_count_not_connect += 1
                            logger.info(
                                f"Отправлено push-уведомление (не подключен) пользователю {user_id}"
                            )
                        except Exception as e:
                            failed_count_not_connect += 1
                            logger.error(f"Не удалось отправить сообщение пользователю {user_id}: {e}")
            except Exception as e:
                failed_count += 1
                logger.error(f"Ошибка обработки пользователя {user_id}: {e}")

        if CHECKER_ID is not None:
            try:
                await bot.send_message(
                    chat_id=CHECKER_ID,
                    text=f"📊 Отчет по push-уведомлениям:\n\n"
                         f"✅ Отправлено не в панели: {sent_count_not_sub}\n"
                         f"❌ Не удалось отправить не в панели: {failed_count_not_sub}\n\n"
                         f"✅ Отправлено не подключенным: {sent_count_not_connect}\n"
                         f"❌ Не удалось отправить не подключенным: {failed_count_not_connect}\n\n"
                         f"❌ Не удалось обработать: {failed_count}\n\n"
                         f"⏰ Время: {now.strftime('%H:%M:%S')}"
                )
                logger.info(
                    f"Отчет отправлен: отправлено {sent_count_not_connect + sent_count_not_sub}, "
                    f"не удалось {failed_count + failed_count_not_connect + failed_count_not_sub}"
                )
            except Exception as e:
                logger.error(f"Не удалось отправить отчет: {e}")

    except Exception as e:
        logger.error(f"Критическая ошибка в send_push_cron: {e}")
