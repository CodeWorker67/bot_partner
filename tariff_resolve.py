"""Цена и описание тарифа, username в панели с bot_id."""
from __future__ import annotations

import re
from typing import Dict, Tuple

from config import BOT_ID, DEFAULT_PRICES

_MONTHS_TO_DAYS = {1: 30, 3: 90, 6: 180, 12: 365}
DEFAULT_DEVICE_SLOTS = 5
_PRO_DEVICE_SUFFIXES = ("_3", "_10")
# {user_id}_{bot_id} или {user_id}_{bot_id}_3 / _10 (user_id может быть отрицательным для site)
_PANEL_USERNAME_RE = re.compile(r"^(-?\d+)_(\d+)(?:_(3|10))?$")

_TIER_BUTTON: Dict[int, Tuple[str, str]] = {
    3: ("🔹", "3️⃣ устройства"),
    5: ("🔸", "5️⃣ устройств"),
    10: ("🏆", "🔟 устройств"),
}

# Описания тарифов (статичные)
OWNER_PRICE_SHORT: Dict[str, str] = {
    "m1_d3": "1 мес · 3 устр.",
    "m3_d3": "3 мес · 3 устр.",
    "m6_d3": "6 мес · 3 устр.",
    "m12_d3": "12 мес · 3 устр.",
    "m1_d5": "1 мес · 5 устр.",
    "m3_d5": "3 мес · 5 устр.",
    "m6_d5": "6 мес · 5 устр.",
    "m12_d5": "12 мес · 5 устр.",
    "m1_d10": "1 мес · 10 устр.",
    "m3_d10": "3 мес · 10 устр.",
    "m6_d10": "6 мес · 10 устр.",
    "m12_d10": "12 мес · 10 устр.",
}

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
    "5000": "Навсегда · 5 устройств",
    "5000sale": "Навсегда (акция) · 5 устройств",
}


FOREVER_TARIFF_KEY = "5000"
FOREVER_SALE_TARIFF_KEY = "5000sale"


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


def panel_username_for_site_user(
    user_id: int,
    bot_id: int | None = None,
    *,
    device_slots: int = 5,
    white: bool = False,
) -> str:
    """Username в панели для записи users.user_id (в т.ч. отрицательный site id)."""
    return panel_username(user_id, bot_id, device_slots=device_slots, white=white)


def panel_usernames_for_lookup(billing_uid: int, bot_id: int | None = None) -> tuple[str, ...]:
    bid = bot_id if bot_id is not None else BOT_ID
    return (
        panel_username(billing_uid, bid, device_slots=5),
        panel_username(billing_uid, bid, device_slots=3),
        panel_username(billing_uid, bid, device_slots=10),
    )


def _panel_username_base(username: str) -> str:
    base = username.strip()
    if base.endswith("_white"):
        base = base[: -len("_white")]
    for suffix in _PRO_DEVICE_SUFFIXES:
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return base


def telegram_id_from_panel_username(username: str) -> int | None:
    if not username or username.startswith("gift_"):
        return None
    base = _panel_username_base(username)
    m = _PANEL_USERNAME_RE.match(base)
    if not m:
        return None
    return int(m.group(1))


def hwid_limit_from_panel_username(username: str, *, default: int = DEFAULT_DEVICE_SLOTS) -> int:
    if username.endswith("_10") or username.endswith("_10_white"):
        return 10
    if username.endswith("_3") or username.endswith("_3_white"):
        return 3
    return default


def subscription_db_slot_from_panel_username(username: str) -> str:
    if username.endswith("_10") or username.endswith("_10_white"):
        return "10"
    if username.endswith("_3") or username.endswith("_3_white"):
        return "3"
    return "5"


def parse_sub_target(raw: str) -> tuple[int, str, str]:
    """
    /sub: (user_id в БД, username в панели, tier: 5|3|10|white).
    Принимает telegram_id, суффиксы _3/_10/_white или полный username (123_5_3, 123-5_3).
    """
    original = raw.strip()
    if not original:
        raise ValueError("empty target")

    token = original
    if re.match(r"^\d+-\d+", token):
        token = re.sub(r"^(\d+)-(\d+)", r"\1_\2", token, count=1)

    white = False
    if token.endswith("_white"):
        white = True
        token = token[: -len("_white")]

    device_slots = 5
    tier = "5"
    if token.endswith("_10"):
        device_slots = 10
        tier = "10"
        token = token[: -len("_10")]
    elif token.endswith("_3"):
        device_slots = 3
        tier = "3"
        token = token[: -len("_3")]

    if white:
        tier = "white"

    m = re.fullmatch(r"(-?\d+)_(\d+)", token)
    if m:
        user_id = int(m.group(1))
        bid = int(m.group(2))
        username = panel_username(user_id, bid, device_slots=device_slots, white=white)
        return user_id, username, tier

    if re.fullmatch(r"-?\d+", token):
        user_id = int(token)
        username = panel_username(user_id, device_slots=device_slots, white=white)
        return user_id, username, tier

    raise ValueError(f"cannot parse sub target: {raw!r}")


def device_from_tariff_key(duration_key_plain: str) -> int:
    m = re.fullmatch(r"m\d+_d(\d+)", duration_key_plain)
    if m:
        return int(m.group(1))
    return DEFAULT_DEVICE_SLOTS


def tariff_days_for_x3(duration_key_plain: str) -> int:
    if duration_key_plain in (FOREVER_TARIFF_KEY, FOREVER_SALE_TARIFF_KEY):
        return 5000
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
    return await sql.get_prices()


def tariff_rub_and_desc(duration_key: str, prices: Dict[str, int] | None = None) -> Tuple[int, str]:
    p = prices or DEFAULT_PRICES
    return p.get(duration_key, DEFAULT_PRICES.get(duration_key, 0)), dct_desc.get(duration_key, duration_key)


def tariff_savings_pct(tariff_key: str, prices: Dict[str, int] | None = None) -> int | None:
    """Выгода % относительно помесячной оплаты по тому же числу устройств (целые проценты)."""
    m = re.fullmatch(r"m(\d+)_d(\d+)", tariff_key)
    if not m:
        return None
    months = int(m.group(1))
    if months <= 1:
        return None
    devices = int(m.group(2))
    p = prices or DEFAULT_PRICES
    base_monthly = p.get(f"m1_d{devices}", DEFAULT_PRICES.get(f"m1_d{devices}", 0))
    if base_monthly <= 0:
        return None
    price = p.get(tariff_key, DEFAULT_PRICES.get(tariff_key, 0))
    full_price = base_monthly * months
    if full_price <= 0 or price >= full_price:
        return None
    pct = round((1 - price / full_price) * 100)
    return pct if pct > 0 else None


def forever_tariff_button_label(prices: Dict[str, int] | None = None) -> str:
    p = prices or DEFAULT_PRICES
    price = p.get(FOREVER_TARIFF_KEY, DEFAULT_PRICES.get(FOREVER_TARIFF_KEY, 4990))
    return f"♾ Навсегда — {price} ₽"


def tariff_button_label(tariff_key: str, prices: Dict[str, int] | None = None) -> str:
    """Текст кнопки тарифа: срок, устройства, цена и выгода (если есть)."""
    m = re.fullmatch(r"m(\d+)_d(\d+)", tariff_key)
    if not m:
        return tariff_key
    months = int(m.group(1))
    devices = int(m.group(2))
    p = prices or DEFAULT_PRICES
    price = p.get(tariff_key, DEFAULT_PRICES.get(tariff_key, 0))
    emoji, devices_text = _TIER_BUTTON.get(devices, ("🔹", f"{devices} устройств"))
    label = f"{emoji} {months} мес - {devices_text} - {price} ₽"
    savings = tariff_savings_pct(tariff_key, p)
    if savings:
        label += f" (выгода -{savings}%)"
    return label
