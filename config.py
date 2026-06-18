import os
from pathlib import Path
from typing import Dict, Set

from dotenv import load_dotenv

load_dotenv()

TG_TOKEN: str = os.environ.get("TG_TOKEN", "")
BOT_ID: int = int(os.environ.get("BOT_ID", "0"))
OWNER_TG_ID: int = int(os.environ.get("OWNER_TG_ID", "0"))
BOT_USERNAME: str = (os.environ.get("BOT_USERNAME") or "").lstrip("@")

_db_path = os.environ.get("DATABASE_PATH", "config_bd/partner.db")
DATABASE_PATH: Path = Path(_db_path)

ADMIN_IDS: Set[int] = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}
_cid = os.environ.get("CHECKER_ID")
CHECKER_ID = int(_cid) if _cid else None

PANEL_URL = os.environ.get("PANEL_URL")
PANEL_API_TOKEN = os.environ.get("PANEL_API_TOKEN")
SHORT_UUID_SECRET = os.environ.get("SHORT_UUID_SECRET")
BOT_URL: str = os.environ.get("BOT_URL") or (f"https://t.me/{BOT_USERNAME}" if BOT_USERNAME else "")
SUPPORT_URL = os.environ.get("SUPPORT_URL", "https://t.me/suppzoomvpn")
DOCUMENT_URL_1 = os.environ.get("DOCUMENT_URL_1", "")
DOCUMENT_URL_2 = os.environ.get("DOCUMENT_URL_2", "")
TRUE_SUB_LINK = os.environ.get("TRUE_SUB_LINK", "")
MIRROR_SUB_LINK = os.environ.get("MIRROR_SUB_LINK", "")

API_FREEKASSA = (os.environ.get("API_FREEKASSA") or "").strip() or None
SHOP_ID_FREEKASSA = int(os.environ["SHOP_ID_FREEKASSA"]) if os.environ.get("SHOP_ID_FREEKASSA") else None
FREEKASSA_SERVER_IP = os.environ.get("FREEKASSA_SERVER_IP", "72.56.14.94")
CRYPTOBOT_API_TOKEN = os.environ.get("CRYPTOBOT_API_TOKEN")

REFERRAL_PROCENT: int = int(os.environ.get("REFERRAL_PROCENT", "30"))
PARTNER_SHARE_REF: int = int(os.environ.get("PARTNER_SHARE_REF", "20"))
PARTNER_SHARE_DEFAULT: int = int(os.environ.get("PARTNER_SHARE_DEFAULT", "50"))
PARTNER_MIN_WITHDRAW: int = int(os.environ.get("PARTNER_MIN_WITHDRAW", "3000"))
PARTNER_SUPPORT_URL: str = os.environ.get("PARTNER_SUPPORT_URL") or SUPPORT_URL

DEFAULT_TRIAL_DAYS: int = int(os.environ.get("DEFAULT_TRIAL_DAYS", "3"))
TRIAL_DAYS_MIN: int = int(os.environ.get("TRIAL_DAYS_MIN", "1"))
TRIAL_DAYS_MAX: int = int(os.environ.get("TRIAL_DAYS_MAX", "7"))

LEAD_TRACKER_BASE = (os.environ.get("LEAD_TRACKER_BASE") or "").strip() or None
LEAD_TRACKER_API_KEY = (os.environ.get("LEAD_TRACKER_API_KEY") or "").strip() or None
LEAD_TRACKER_STAR_RUB_PER_STAR: str = os.environ.get("LEAD_TRACKER_STAR_RUB_PER_STAR", "1.0")

PAYMENT_MAX_PENDING_PER_USER: int = int(os.environ.get("PAYMENT_MAX_PENDING_PER_USER", "8"))

THROTTLE_MAX_UPDATES: int = int(os.environ.get("THROTTLE_MAX_UPDATES", "25"))
THROTTLE_WINDOW_SEC: float = float(os.environ.get("THROTTLE_WINDOW_SEC", "8"))

TARIFF_KEYS = [
    "m1_d3", "m3_d3", "m6_d3", "m12_d3",
    "m1_d5", "m3_d5", "m6_d5", "m12_d5",
    "m1_d10", "m3_d10", "m6_d10", "m12_d10",
]


def _price_env(key: str, default: int) -> int:
    return int(os.environ.get(f"DEFAULT_PRICE_{key.upper()}", str(default)))


def _min_price_env(key: str, default: int) -> int:
    return int(os.environ.get(f"MIN_PRICE_{key.upper()}", str(default)))


DEFAULT_PRICES: Dict[str, int] = {
    "m1_d3": _price_env("m1_d3", 199),
    "m3_d3": _price_env("m3_d3", 499),
    "m6_d3": _price_env("m6_d3", 999),
    "m12_d3": _price_env("m12_d3", 1188),
    "m1_d5": _price_env("m1_d5", 299),
    "m3_d5": _price_env("m3_d5", 749),
    "m6_d5": _price_env("m6_d5", 1349),
    "m12_d5": _price_env("m12_d5", 1799),
    "m1_d10": _price_env("m1_d10", 659),
    "m3_d10": _price_env("m3_d10", 1349),
    "m6_d10": _price_env("m6_d10", 2399),
    "m12_d10": _price_env("m12_d10", 3239),
}

MIN_PRICES: Dict[str, int] = {
    k: _min_price_env(k, max(50, v // 2)) for k, v in DEFAULT_PRICES.items()
}
