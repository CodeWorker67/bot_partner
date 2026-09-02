"""Бизнес-логика лимита трафика на белой ноде Антиглушилка (partner bot)."""
from __future__ import annotations

import asyncio
import random
import re
from datetime import date, datetime, timedelta
from typing import Optional

from config import BOT_ID
from tariff_resolve import panel_username

from wl_traffic.constants import (
    WL_DAY_RESET_HOUR,
    WL_GB_PER_MONTH,
    WL_LEGACY_RETRIES,
    WL_LOW_TRAFFIC_WARNING_GB,
    WL_NODE_NAME,
    WL_TOP_USERS_LIMIT,
    WL_SQUAD_ACTIVE,
    WL_SQUAD_LIMITED,
    WL_SUBSCRIPTION_MONTHS,
    WL_TIMEZONE,
)

_BYTES_PER_GB = 1024 ** 3
_PRO_SUFFIXES = ("_3", "_10")
# {tg_id}_{bot_id} или {tg_id}_{bot_id}_3 / _10
_PARTNER_PANEL_USERNAME_RE = re.compile(r"^(\d+)_(\d+)(?:_(3|10))?$")


def bytes_to_gb(value: float) -> float:
    return round(value / _BYTES_PER_GB, 2)


def wl_traffic_day(now: datetime | None = None) -> date:
    """Текущий WL-день (МСК): до 03:00 — предыдущий календарный день."""
    if now is None:
        now = datetime.now(WL_TIMEZONE)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=WL_TIMEZONE)
    else:
        now = now.astimezone(WL_TIMEZONE)
    day = now.date()
    if now.hour < WL_DAY_RESET_HOUR:
        day -= timedelta(days=1)
    return day


def is_wl_check_skip_window(now: datetime | None = None) -> bool:
    """02:57–03:05 МСК — окно ежедневного накопления trafic_wl, проверку лимита пропускаем."""
    if now is None:
        now = datetime.now(WL_TIMEZONE)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=WL_TIMEZONE)
    else:
        now = now.astimezone(WL_TIMEZONE)
    if now.hour == 2 and now.minute >= 57:
        return True
    if now.hour == 3 and now.minute <= 5:
        return True
    return False


def subscription_bonus_gb(duration_days: int) -> float:
    if duration_days == 7:
        return 3.0
    months = WL_SUBSCRIPTION_MONTHS.get(duration_days, max(0, duration_days // 30))
    return float(months * WL_GB_PER_MONTH)


async def credit_wl_subscription_bonus(sql, user_id: int, duration_days: int) -> None:
    bonus_gb = subscription_bonus_gb(duration_days)
    if bonus_gb > 0:
        await sql.add_wl_limit(user_id, bonus_gb)


async def apply_wl_subscription_bonus(sql, x3, billing_uid: int, duration_days: int) -> None:
    await credit_wl_subscription_bonus(sql, billing_uid, duration_days)
    trafic_wl, limit_wl = await sql.get_wl_limits(billing_uid)
    used_gb = await get_wl_used_gb_for_user(x3, billing_uid, trafic_wl)
    await restore_pro_squads_if_under_limit(x3, billing_uid, used_gb, limit_wl)


def parse_traffic_duration(duration: str) -> Optional[int]:
    if not duration.startswith("traffic"):
        return None
    try:
        return int(duration.replace("traffic", ""))
    except ValueError:
        return None


def billing_key_from_panel_username(username: str) -> Optional[tuple[int, int]]:
    """
    (telegram_id, bot_id) из username панели партнёрского бота.
    None для white/gift/неизвестных форматов.
    """
    if not username or "_white" in username or username.startswith("gift_"):
        return None
    base = username
    for suffix in _PRO_SUFFIXES:
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    m = _PARTNER_PANEL_USERNAME_RE.match(base)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def billing_uid_from_panel_username(username: str, *, bot_id: int | None = None) -> Optional[int]:
    """Telegram user_id для текущего бота; None если username другого бота или не PRO."""
    key = billing_key_from_panel_username(username)
    if key is None:
        return None
    tg_id, panel_bot_id = key
    bid = bot_id if bot_id is not None else BOT_ID
    if panel_bot_id != bid:
        return None
    return tg_id


def pro_panel_usernames_for_billing_uid(
    billing_uid: int,
    bot_id: int | None = None,
) -> tuple[str, ...]:
    bid = bot_id if bot_id is not None else BOT_ID
    return (
        panel_username(billing_uid, bid, device_slots=5),
        panel_username(billing_uid, bid, device_slots=3),
        panel_username(billing_uid, bid, device_slots=10),
    )


def extract_squad_uuids(panel_user: dict) -> list[str]:
    raw = panel_user.get("activeInternalSquads") or []
    uuids: list[str] = []
    for s in raw:
        if isinstance(s, dict):
            uuids.append(str(s.get("uuid", "")))
        else:
            uuids.append(str(s))
    return [u for u in uuids if u]


def user_on_limited_squad(panel_user: dict) -> bool:
    squads = set(extract_squad_uuids(panel_user))
    return bool(squads & set(WL_SQUAD_LIMITED))


def user_on_active_squad(panel_user: dict) -> bool:
    squads = set(extract_squad_uuids(panel_user))
    return bool(squads & set(WL_SQUAD_ACTIVE))


def any_pro_user_on_limited_squad(panel_users: list[dict]) -> bool:
    return any(user_on_limited_squad(pu) for pu in panel_users)


def all_pro_users_on_limited_squad(panel_users: list[dict]) -> bool:
    return bool(panel_users) and all(user_on_limited_squad(pu) for pu in panel_users)


def _record_date(item: dict) -> Optional[date]:
    raw = item.get("date")
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def filter_records_for_day(records: list[dict], day: date) -> list[dict]:
    filtered = [r for r in records if _record_date(r) == day]
    return filtered if filtered else records


def aggregate_bandwidth_by_username(records: list[dict]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        uname = str(item.get("username") or "")
        if not uname:
            continue
        totals[uname] = totals.get(uname, 0.0) + float(item.get("total") or 0)
    return totals


def aggregate_bandwidth_by_user_uuid(records: list[dict]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        user_uuid = str(item.get("userId") or item.get("userUuid") or "")
        if not user_uuid:
            continue
        totals[user_uuid] = totals.get(user_uuid, 0.0) + float(item.get("total") or 0)
    return totals


async def fetch_wl_traffic_gb_for_day(
    x3,
    day: date | None = None,
    *,
    retries: int = WL_LEGACY_RETRIES,
) -> tuple[dict[str, float], dict[str, float]]:
    day = day or wl_traffic_day()
    day_str = day.isoformat()

    node_uuid = await x3.get_node_uuid_by_name(WL_NODE_NAME)
    if not node_uuid:
        return {}, {}

    for attempt in range(max(1, retries)):
        records = await x3.get_node_users_bandwidth_legacy(
            node_uuid, day_str, day_str, top_users_limit=WL_TOP_USERS_LIMIT,
        )
        if records:
            filtered = filter_records_for_day(records, day)
            by_username = {
                u: bytes_to_gb(b) for u, b in aggregate_bandwidth_by_username(filtered).items()
            }
            by_uuid = {
                u: bytes_to_gb(b) for u, b in aggregate_bandwidth_by_user_uuid(filtered).items()
            }
            return by_username, by_uuid
        if attempt < retries - 1:
            await asyncio.sleep(2.0 * (attempt + 1))

    return {}, {}


def wl_traffic_gb_for_panel_user(
    panel_user: dict,
    traffic_by_username: dict[str, float],
    traffic_by_uuid: dict[str, float],
) -> float:
    """Расход за WL-день из bulk-мапы: сначала по id панели, затем по username."""
    panel_user_id = panel_user.get("id")
    if panel_user_id is not None:
        key = str(panel_user_id)
        if key in traffic_by_uuid:
            return traffic_by_uuid[key]

    uname = str(panel_user.get("username") or "")
    if uname and uname in traffic_by_username:
        return traffic_by_username[uname]

    return 0.0


def compute_wl_used_gb(trafic_wl_db: float, day_gb: float) -> float:
    return round(float(trafic_wl_db or 0.0) + float(day_gb or 0.0), 2)


def should_send_wl_low_traffic_warning(used_gb: float, limit_gb: float) -> bool:
    return used_gb < limit_gb and used_gb + WL_LOW_TRAFFIC_WARNING_GB > limit_gb


async def fetch_all_pro_panel_users(x3, billing_uid: int, bot_id: int | None = None) -> list[dict]:
    result: list[dict] = []
    for uname in pro_panel_usernames_for_billing_uid(billing_uid, bot_id):
        data = await x3.get_user_by_username(uname)
        if not data:
            continue
        panel_user = x3._panel_user_from_response(data)
        if panel_user:
            result.append(panel_user)
    return result


def wl_day_gb_for_panel_users(
    panel_users: list[dict],
    traffic_by_username: dict[str, float],
    traffic_by_uuid: dict[str, float],
) -> float:
    total = 0.0
    for panel_user in panel_users:
        total += wl_traffic_gb_for_panel_user(
            panel_user, traffic_by_username, traffic_by_uuid,
        )
    return round(total, 2)


async def get_wl_used_gb_for_user(
    x3,
    billing_uid: int,
    trafic_wl_db: float,
    *,
    bot_id: int | None = None,
    day: date | None = None,
    traffic_by_username: dict[str, float] | None = None,
    traffic_by_uuid: dict[str, float] | None = None,
    panel_users: list[dict] | None = None,
) -> float:
    if traffic_by_username is None or traffic_by_uuid is None:
        traffic_by_username, traffic_by_uuid = await fetch_wl_traffic_gb_for_day(
            x3, day, retries=1,
        )

    if panel_users is None:
        panel_users = await fetch_all_pro_panel_users(x3, billing_uid, bot_id)

    if not panel_users:
        return round(float(trafic_wl_db or 0.0), 2)

    day_gb = wl_day_gb_for_panel_users(
        panel_users, traffic_by_username, traffic_by_uuid,
    )
    return compute_wl_used_gb(trafic_wl_db, day_gb)


async def reassign_squad(x3, panel_user: dict, pool: tuple[str, ...]) -> bool:
    username = str(panel_user.get("username") or "").strip()
    if not username:
        return False
    squad = [random.choice(pool)]
    return await x3.update_user_squads(username, squad)


async def reassign_to_active_squad(x3, panel_user: dict) -> bool:
    return await reassign_squad(x3, panel_user, WL_SQUAD_ACTIVE)


async def reassign_to_limited_squad(x3, panel_user: dict) -> bool:
    return await reassign_squad(x3, panel_user, WL_SQUAD_LIMITED)


async def reassign_all_pro_to_active(x3, billing_uid: int, bot_id: int | None = None) -> int:
    updated = 0
    for panel_user in await fetch_all_pro_panel_users(x3, billing_uid, bot_id):
        if user_on_limited_squad(panel_user):
            if await reassign_to_active_squad(x3, panel_user):
                updated += 1
    return updated


async def reassign_all_pro_to_limited(x3, billing_uid: int, bot_id: int | None = None) -> int:
    updated = 0
    for panel_user in await fetch_all_pro_panel_users(x3, billing_uid, bot_id):
        if not user_on_limited_squad(panel_user):
            if await reassign_to_limited_squad(x3, panel_user):
                updated += 1
    return updated


async def restore_pro_squads_if_under_limit(
    x3,
    billing_uid: int,
    used_gb: float,
    limit_gb: float,
    bot_id: int | None = None,
) -> int:
    if used_gb > limit_gb:
        return 0
    return await reassign_all_pro_to_active(x3, billing_uid, bot_id)
