from aiogram import Bot
from aiogram.client.default import DefaultBotProperties

from X3 import X3
from config import TG_TOKEN
from config_bd.partner_sql import PartnerSQL

bot: Bot = Bot(
    token=TG_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML", link_preview_is_disabled=True),
)
x3: X3 = X3()
sql: PartnerSQL = PartnerSQL()
