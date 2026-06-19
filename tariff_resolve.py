"""Цена и описание тарифа, username в панели с bot_id."""
from __future__ import annotations

import json
import re
from typing import Dict, Tuple

from config import BOT_ID, DEFAULT_PRICES, MIN_PRICES

_MONTHS_TO_DAYS = {1: 30, 3: 90, 6: 180, 12: 365}
DEFAULT_DEVICE_SLOTS = 5

# Описания тарифов (статичные)
dct_desc: Dict[str, str] = {
    "m1_d3": "1 месяц · 3 устройства",
    "m3_d3": "3 месяца · 3 устройства",
    "m6_d3": "6 месяцев · 3 устройства",
    "m12_d3": "12 месяцев · 3 устройства",
    "m1_d5": "1 месяц · 5 устройств",
    "m3_d5": "3 месяца · 5 устройств",
    "m6_d5": "6 месяцев · 5 устройств",
    "m12_d5": "12 месяцев · 5 устройств",
    "m1_d10": "1 месяц · 10 устройств",
    "m3_d10": "3 месяца · 10 устройств",
    "m6_d10": "6 месяцев · 10 устройств",
    "m12_d10": "12 месяцев · 10 устройств",
}


def panel_username(tg_id: int, bot_id: int | None = None, *, device_slots: int = 5, white: bool = False) -> str:
    bid = bot_id if bot_id is not None else BOT_ID
    base = f"{tg_id}_{bid}"
    if white:
        return f"{base}_white"
    if device_slots == 3:
        return f"{base}_3"
    if device_slots == 10:
        return f"{base}_10"
    return base


def device_from_tariff_key(duration_key_plain: str) -> int:
    m = re.fullmatch(r"m\d+_d(\d+)", duration_key_plain)
    if m:
        return int(m.group(1))
    return DEFAULT_DEVICE_SLOTS


def tariff_days_for_x3(duration_key_plain: str) -> int:
    if duration_key_plain.startswith("new_"):
        if duration_key_plain == "new_3000":
            return 3000
        return int(duration_key_plain.replace("new_", "", 1))
    m_md = re.fullmatch(r"m(\d+)_d(\d+)", duration_key_plain)
    if m_md:
        months = int(m_md.group(1))
        return _MONTHS_TO_DAYS.get(months, 30 * months)
    return int(duration_key_plain)


async def get_prices(sql) -> Dict[str, int]:
    settings = await sql.get_bot_settings()
    if settings and settings.get("prices_json"):
        try:
            return {**DEFAULT_PRICES, **json.loads(settings["prices_json"])}
        except json.JSONDecodeError:
            pass
    return dict(DEFAULT_PRICES)


def tariff_rub_and_desc(duration_key: str, prices: Dict[str, int] | None = None) -> Tuple[int, str]:
    p = prices or DEFAULT_PRICES
    return p.get(duration_key, DEFAULT_PRICES.get(duration_key, 0)), dct_desc.get(duration_key, duration_key)
