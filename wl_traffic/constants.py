"""Константы лимита трафика Антиглушилка (белая нода YANDEX-RU-001)."""

from zoneinfo import ZoneInfo

WL_NODE_NAME = "YANDEX-RU-001"
WL_TIMEZONE = ZoneInfo("Europe/Moscow")
# Сутки WL-трафика: с 03:00 до 02:59 МСК (накопление в 02:57, проверка после 03:05).
WL_DAY_RESET_HOUR = 3
WL_ACCUMULATE_HOUR = 2
WL_ACCUMULATE_MINUTE = 57
WL_CHECK_SKIP_UNTIL_HOUR = 3
WL_CHECK_SKIP_UNTIL_MINUTE = 5
WL_LEGACY_RETRIES = 3
WL_TOP_USERS_LIMIT = 5000

# Сквады с белой нодой (Антиглушилка доступна)
WL_SQUAD_ACTIVE = (
    "6b8943e0-dc8b-4323-871c-3b2d017c56c5",
    "7d1024ee-e8b2-4f78-aa9b-51eb23b3bac1",
)

# Сквады без белой ноды (Антиглушилка заблокирована при превышении лимита)
WL_SQUAD_LIMITED = (
    "e34716de-9526-4ad6-96c8-e00d6259285f",
    "87f868a6-33e7-44ff-aa1b-97ee9fa91a1b",
)

WL_GB_PER_MONTH = 10
WL_TRIAL_LIMIT_GB = 2.0
WL_LOW_TRAFFIC_WARNING_GB = 1.0

# gb -> price (₽), от большего к меньшему
WL_TRAFFIC_TARIFFS: dict[str, int] = {
    "500": 1249,
    "250": 629,
    "100": 259,
    "50": 149,
    "20": 79,
    "10": 50,
}

# duration days -> months for +10 GB/month bonus on subscription payment
WL_SUBSCRIPTION_MONTHS: dict[int, int] = {
    7: 0,
    30: 1,
    90: 3,
    180: 6,
    365: 12,
}

PROFILE_CB = "user_profile"
WL_TRAFFIC_BUY_CB = "wl_traffic_buy"
WL_TRAFFIC_BUY_SUB_CB = "wl_traffic_buy_sub"
BUY_VPN_CB = "buy_vpn"
