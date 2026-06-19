"""Цена и описание тарифа, username в панели с bot_id."""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Tuple

from config import BOT_ID, DEFAULT_PRICES

_MONTHS_TO_DAYS = {1: 30, 3: 90, 6: 180, 12: 365}
DEFAULT_DEVICE_SLOTS = 5

# Формат username в панели: {user_id}-{bot_id}[_3|_10|_white]
# Примеры: 8603141868-5, 8603141868-5_3, 8603141868-5_10, 8603141868-5_white
_PANEL_USERNAME_FULL_RE = re.compile(r"^(-?\d+)-(\d+)(?:_(3|10|white))?$")

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


def panel_username(
    tg_id: int,
    bot_id: int | None = None,
    *,
    device_slots: int = 5,
    white: bool = False,
) -> str:
    bid = bot_id if bot_id is not None else BOT_ID
    base = f"{tg_id}-{bid}"
    if white:
        return f"{base}_white"
    if device_slots == 3:
        return f"{base}_3"
    if device_slots == 10:
        return f"{base}_10"
    return base


def panel_username_for_site_user(
    user_id: int,
    bot_id: int | None = None,
    *,
    device_slots: int = 5,
    white: bool = False,
) -> str:
    """Username в панели для пользователей сайта (отрицательный internal user_id)."""
    return panel_username(user_id, bot_id, device_slots=device_slots, white=white)


def panel_usernames_for_lookup(user_id: int, bot_id: int | None = None) -> List[str]:
    """Все возможные username в панели для поиска пользователя."""
    bid = bot_id if bot_id is not None else BOT_ID
    return [
        panel_username(user_id, bid, device_slots=5),
        panel_username(user_id, bid, device_slots=3),
        panel_username(user_id, bid, device_slots=10),
        panel_username(user_id, bid, white=True),
    ]


def device_slots_from_panel_username(username: str) -> int:
    if username.endswith("_white") or "_white" in username:
        return 1
    if username.endswith("_10"):
        return 10
    if username.endswith("_3"):
        return 3
    return DEFAULT_DEVICE_SLOTS


def hwid_limit_from_panel_username(username: str, *, default: int = DEFAULT_DEVICE_SLOTS) -> int:
    slots = device_slots_from_panel_username(username)
    if slots == 1:
        return 1
    if slots in (3, 10):
        return slots
    return default


def subscription_db_slot_from_panel_username(username: str) -> str:
    """Слот БД: main / 3 / 10 (white → main)."""
    if username.endswith("_white"):
        return "main"
    if username.endswith("_10"):
        return "10"
    if username.endswith("_3"):
        return "3"
    return "main"


def telegram_id_from_panel_username(username: str) -> Optional[int]:
    """Извлекает user_id / telegram_id из username панели ({id}-{bot_id}[_suffix])."""
    if not username:
        return None
    m = _PANEL_USERNAME_FULL_RE.fullmatch(username.strip())
    if m:
        return int(m.group(1))
    if username.isdigit():
        return int(username)
    return None


def parse_sub_target(raw: str, bot_id: int | None = None) -> Tuple[int, str, str]:
    """
    Разбор цели /sub: telegram_id (или site user_id), username в панели, метка тарифа.

    Принимает:
      8603141868           → 8603141868-{bot_id}
      8603141868_3         → 8603141868-{bot_id}_3
      8603141868-5_3       → как есть
    """
    raw = raw.strip()
    bid = bot_id if bot_id is not None else BOT_ID

    m = _PANEL_USERNAME_FULL_RE.fullmatch(raw)
    if m:
        uid = int(m.group(1))
        suffix = m.group(3)
        tier = suffix if suffix else "5"
        return uid, raw, tier

    if raw.endswith("_white"):
        uid = int(raw[: -len("_white")])
        return uid, panel_username(uid, bid, white=True), "white"
    if raw.endswith("_10"):
        uid = int(raw[: -len("_10")])
        return uid, panel_username(uid, bid, device_slots=10), "10"
    if raw.endswith("_3"):
        uid = int(raw[: -len("_3")])
        return uid, panel_username(uid, bid, device_slots=3), "3"

    uid = int(raw)
    return uid, panel_username(uid, bid), "5"


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
