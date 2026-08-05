from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from bot import bot, sql
from config import CHECKER_ID
from keyboard import create_kb, STYLE_PRIMARY, STYLE_SUCCESS
from lexicon import lexicon
from logging_config import logger
from telegram_ids import is_telegram_chat_id

PUSH_ACTIVE_MINUTES = 7 * 24 * 60  # первые 7 дней после регистрации


@dataclass(frozen=True)
class PushStage:
    window_start: int
    window_end: int
    lexicon_key: str
    keyboard: str = 'buy_free'


NOT_SUB_STAGES = (
    PushStage(30, 60, 'push_not_subscribed_30m', keyboard='buy_free'),
    PushStage(180, 210, 'push_not_subscribed_3h', keyboard='buy_free'),
    PushStage(1410, 1440, 'push_not_subscribed_day2_0h', keyboard='buy_free'),
    PushStage(2130, 2160, 'push_not_subscribed_day2_12h', keyboard='buy_free'),
    PushStage(2850, 2880, 'push_not_subscribed_day3_0h', keyboard='buy_free'),
    PushStage(4290, 4320, 'push_not_subscribed_day4_0h', keyboard='buy_free'),
    PushStage(5730, 5760, 'push_not_subscribed_day5_0h', keyboard='buy_free'),
    PushStage(7170, 7200, 'push_not_subscribed_day6_0h', keyboard='buy_free'),
    PushStage(8610, 8640, 'push_not_subscribed_day7_0h', keyboard='buy_free'),
)

NOT_CONNECT_STAGES = (
    # День 1
    PushStage(30, 60, 'push_not_connected_30m', keyboard='connect'),
    PushStage(180, 210, 'push_not_connected_3h', keyboard='connect'),
    PushStage(1410, 1440, 'push_not_connected_24h', keyboard='connect'),
    # День 2 (48ч) — 1-е, день 3 (72ч) — 2-е, день 4 (96ч) — 3-е
    PushStage(2850, 2880, 'push_not_connected_30m', keyboard='connect'),
    PushStage(4290, 4320, 'push_not_connected_3h', keyboard='connect'),
    PushStage(5730, 5760, 'push_not_connected_24h', keyboard='connect'),
    # День 5 (120ч) — 1-е, день 6 (144ч) — 2-е, день 7 (168ч) — 3-е
    PushStage(7170, 7200, 'push_not_connected_30m', keyboard='connect'),
    PushStage(8610, 8640, 'push_not_connected_3h', keyboard='connect'),
    PushStage(10050, 10080, 'push_not_connected_24h', keyboard='connect'),
)


def _find_stage(offset_minutes: int, stages: tuple[PushStage, ...]) -> Optional[PushStage]:
    for stage in stages:
        if stage.window_start <= offset_minutes <= stage.window_end:
            return stage
    return None


def _keyboard_for(stage: PushStage):
    if stage.keyboard == 'buy_free':
        return create_kb(
            1,
            styles={'buy_vpn': STYLE_PRIMARY, 'trial_vpn': STYLE_SUCCESS},
            buy_vpn='💰 Купить подписку',
            trial_vpn='✨ Попробовать бесплатно',
        )
    if stage.keyboard == 'connect':
        return create_kb(
            1,
            styles={'connect_vpn': STYLE_PRIMARY},
            connect_vpn='🔗 Подключить ВПН',
        )
    return None


async def _send_push(user_id: int, stage: PushStage) -> None:
    message_text = lexicon[stage.lexicon_key]
    keyboard = _keyboard_for(stage)
    await bot.send_message(
        chat_id=user_id,
        text=message_text,
        reply_markup=keyboard,
    )


async def send_push_cron(debug: bool = False):
    """
    Push по этапам после регистрации (create_user), только первые 7 дней:
    1) Нет в панели (in_panel=False) — 9 сообщений.
    2) В панели, но VPN не подключён (is_connect=False):
       день 1 — 3 пуша (+30м, +3ч, +24ч);
       далее по одному в сутки (48/72/96/120/144/168ч), чередуя те же 3 текста.
    После 7 дней рассылка не продолжается.
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

                minutes_diff = int((now - create_time).total_seconds() / 60)
                if minutes_diff > PUSH_ACTIVE_MINUTES:
                    continue

                in_panel = user_data[4]
                is_connect = user_data[5]

                if not in_panel:
                    stage = _find_stage(minutes_diff, NOT_SUB_STAGES)
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
                    stage = _find_stage(minutes_diff, NOT_CONNECT_STAGES)
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
