import urllib.parse
from typing import List, Optional

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot_display import bot_display_name
from config import BOT_URL, SUPPORT_URL, TARIFF_KEYS
from tariff_resolve import OWNER_PRICE_SHORT, dct_desc

BTN_BACK = "🔙 Назад"
REVIEWS_URL = "https://t.me/otzividlyasvoi"

STYLE_PRIMARY = "primary"
STYLE_SUCCESS = "success"
STYLE_DANGER = "danger"

OPEN_SITE_CB = "open_site"


def keyboard_push_buy_reviews() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🛒 Купить подписку",
                callback_data="buy_vpn",
                style=STYLE_SUCCESS,
            ),
        ],
        [
            InlineKeyboardButton(
                text="📋 Отзывы",
                url=REVIEWS_URL,
                style=STYLE_PRIMARY,
            ),
        ],
    ])


def create_kb(
    width: int,
    *,
    styles: Optional[dict[str, str]] = None,
    **kwargs: str,
) -> InlineKeyboardMarkup:
    """
    Создает инлайн-клавиатуру. kwargs: callback_data -> текст кнопки.
    styles: callback_data -> 'primary' | 'success' | 'danger' (цвет кнопки в клиентах Telegram).
    """
    kb_builder = InlineKeyboardBuilder()
    buttons: List[InlineKeyboardButton] = []
    style_map = styles or {}

    for button_data, button_text in kwargs.items():
        st = style_map.get(button_data)
        if st:
            buttons.append(
                InlineKeyboardButton(
                    text=button_text,
                    callback_data=button_data,
                    style=st,
                )
            )
        else:
            buttons.append(
                InlineKeyboardButton(
                    text=button_text,
                    callback_data=button_data,
                )
            )

    kb_builder.row(*buttons, width=width)
    return kb_builder.as_markup()


def keyboard_push_buy_reviews() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🛒 Купить подписку",
                callback_data="buy_vpn",
                style=STYLE_SUCCESS,
            ),
        ],
        [
            InlineKeyboardButton(
                text="📋 Отзывы",
                url=REVIEWS_URL,
                style=STYLE_PRIMARY,
            ),
        ],
    ])


def chanel_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="👉Подписаться на канал",
                url=CHANEL_URL,
                style=STYLE_PRIMARY,
            )
        ]
    ])
    return keyboard


def keyboard_start_bonus(*, show_owner_panel: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="🔥 Попробовать бесплатно",
                callback_data="trial_vpn",
                style=STYLE_SUCCESS,
            ),
        ],
        [
            InlineKeyboardButton(
                text="🛒 Купить",
                callback_data="buy_vpn",
                style=STYLE_SUCCESS,
            ),
        ],
    ]
    if show_owner_panel:
        rows.append(
            [
                InlineKeyboardButton(
                    text="⚙️ Панель партнёра",
                    callback_data="owner_panel",
                    style=STYLE_PRIMARY,
                ),
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def keyboard_start():
    markup = create_kb(
        1,
        styles={
            "buy_vpn": STYLE_SUCCESS,
            "connect_vpn": STYLE_PRIMARY,
            "manage_devices": STYLE_PRIMARY,
            "ref": STYLE_PRIMARY,
            "buy_gift": STYLE_SUCCESS,
        },
        buy_vpn="🛒 Купить подписку",
        connect_vpn="🔗 Подключить ВПН",
        manage_devices="📱 Управление устройствами",
        ref="👥 Бесплатный VPN за приглашения",
        buy_gift="🎁 Подарить подписку",
    )
    rows = list(markup.inline_keyboard)
    rows.append(
        [
            InlineKeyboardButton(
                text="🌐 Наш сайт",
                callback_data=OPEN_SITE_CB,
                style=STYLE_PRIMARY,
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="💸 Зарабатывай с нами",
                callback_data="partner_earn",
                style=STYLE_SUCCESS,
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="📋 Отзывы",
                url=REVIEWS_URL,
                style=STYLE_PRIMARY,
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def keyboard_buy_device_tier():
    return create_kb(
        1,
        styles={
            "buy_tier_3": STYLE_PRIMARY,
            "buy_tier_5": STYLE_PRIMARY,
            "buy_tier_10": STYLE_SUCCESS,
        },
        buy_tier_3="🔹 Тарифы на 3️⃣ устройства",
        buy_tier_5="🔸 Тарифы на 5️⃣ устройств",
        buy_tier_10="🏆 Тарифы на 🔟 устройств",
        back_to_main=BTN_BACK,
    )


def _styles_buy_duration(devices: int) -> dict[str, str]:
    st: dict[str, str] = {"back_buy_tier": STYLE_PRIMARY}
    for months in (1, 3, 6, 12):
        key = f"r_m{months}_d{devices}"
        st[key] = STYLE_SUCCESS if months >= 6 else STYLE_PRIMARY
    return st


def keyboard_buy_duration(devices: int) -> InlineKeyboardMarkup:
    """Срок подписки после выбора числа устройств (callback вида r_m1_d3)."""
    kwargs: dict[str, str] = {}
    for months in (1, 3, 6, 12):
        ck = f"r_m{months}_d{devices}"
        dk = f"m{months}_d{devices}"
        kwargs[ck] = dct_desc[dk]
    kwargs["back_buy_tier"] = BTN_BACK
    return create_kb(1, styles=_styles_buy_duration(devices), **kwargs)


def keyboard_gift_device_tier():
    return create_kb(
        1,
        styles={
            "gift_tier_3": STYLE_PRIMARY,
            "gift_tier_5": STYLE_PRIMARY,
            "gift_tier_10": STYLE_SUCCESS,
        },
        gift_tier_3="🔹 Тарифы на 3️⃣ устройства",
        gift_tier_5="🔸 Тарифы на 5️⃣ устройств",
        gift_tier_10="🏆 Тарифы на 🔟 устройств",
        back_to_main=BTN_BACK,
    )


def _styles_gift_duration(devices: int) -> dict[str, str]:
    st: dict[str, str] = {"gift_back_tier": STYLE_PRIMARY}
    for months in (1, 3, 6, 12):
        key = f"gift_r_m{months}_d{devices}"
        st[key] = STYLE_SUCCESS if months >= 6 else STYLE_PRIMARY
    return st


def keyboard_gift_duration(devices: int) -> InlineKeyboardMarkup:
    kwargs: dict[str, str] = {}
    for months in (1, 3, 6, 12):
        ck = f"gift_r_m{months}_d{devices}"
        dk = f"m{months}_d{devices}"
        kwargs[ck] = dct_desc[dk]
    kwargs["gift_back_tier"] = BTN_BACK
    return create_kb(1, styles=_styles_gift_duration(devices), **kwargs)


def keyboard_subscription(links: list[tuple[str, str, str]]) -> InlineKeyboardMarkup:
    """
    links: (текст кнопки, https-ссылка на подписку, ключ слота). Только по активным слотам из панели.
    """
    buttons = []
    for text, url, _slot in links:
        if not url:
            continue
        buttons.append(
            [
                InlineKeyboardButton(
                    text=text[:64],
                    url=url,
                    style=STYLE_PRIMARY,
                )
            ]
        )
    buttons.append(
        [
            InlineKeyboardButton(
                text="⚠️ Если страница не загружается",
                callback_data="import",
                style=STYLE_DANGER,
            )
        ]
    )
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def keyboard_import_os():
    return create_kb(
        1,
        styles={
            "import_android": STYLE_PRIMARY,
            "import_ios": STYLE_PRIMARY,
            "import_windows": STYLE_PRIMARY,
            "import_macos": STYLE_PRIMARY,
        },
        import_android="🤖 Android",
        import_ios="🍎 iOS",
        import_windows="🖥️ Windows",
        import_macos="🍏 MacOS",
        back_to_main="🔙 Назад",
    )


def keyboard_import_app(os_callback: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⭐️ Happ",
                    callback_data=f"{os_callback}_happ",
                    style=STYLE_PRIMARY,
                )
            ],
            [
                InlineKeyboardButton(
                    text="📡 V2raytun",
                    callback_data=f"{os_callback}_v2",
                    style=STYLE_PRIMARY,
                )
            ],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")],
        ]
    )


def keyboard_import_sub(app_callback: str, links: list[tuple[str, str, str]]):
    buttons = []
    for label, _url, slot_key in links:
        buttons.append(
            [
                InlineKeyboardButton(
                    text=label[:64],
                    callback_data=f"{app_callback}_sub_{slot_key}",
                    style=STYLE_PRIMARY,
                )
            ]
        )
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def keyboard_sub_after_buy(sub_url):
    rows: list[list[InlineKeyboardButton]] = []
    if sub_url and sub_url != "-":
        rows.append(
            [
                InlineKeyboardButton(
                    text="📋 В личный кабинет",
                    url=sub_url,
                    style=STYLE_PRIMARY,
                )
            ]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="⚠️ Если страница не загружается",
                    callback_data="import",
                    style=STYLE_DANGER,
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎁 Подарить подписку",
                    callback_data="buy_gift",
                    style=STYLE_SUCCESS,
                )
            ],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def keyboard_sub_after_free(sub_url):
    rows: list[list[InlineKeyboardButton]] = []
    if sub_url and sub_url != "-":
        rows.append(
            [
                InlineKeyboardButton(
                    text="📋 В личный кабинет",
                    url=sub_url,
                    style=STYLE_PRIMARY,
                )
            ]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="⚠️ Если страница не загружается",
                    callback_data="import",
                    style=STYLE_DANGER,
                )
            ],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def keyboard_payment_cancel():
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🛒 Купить подписку",
                    callback_data="buy_vpn",
                    style=STYLE_PRIMARY,
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎁 Подарить подписку",
                    callback_data="start_gift",
                    style=STYLE_SUCCESS,
                )
            ],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")],
        ]
    )
    return keyboard


def _payment_rows_without_trial_card(tarif: str) -> list[list[InlineKeyboardButton]]:
    """Строки клавиатуры способов оплаты."""
    return [
        [
            InlineKeyboardButton(
                text="⚡ СБП",
                callback_data=f"wata_sbp_{tarif}",
                style=STYLE_SUCCESS,
            )
        ],
        [
            InlineKeyboardButton(
                text="💳 Карта РФ",
                callback_data=f"wata_card_{tarif}",
                style=STYLE_PRIMARY,
            )
        ],
        [
            InlineKeyboardButton(
                text="⭐️ Telegram Stars",
                callback_data=f"stars_{tarif}",
                style=STYLE_PRIMARY,
            )
        ],
        [
            InlineKeyboardButton(
                text="💎 Crypto bot",
                callback_data=f"crypto_{tarif}",
                style=STYLE_PRIMARY,
            )
        ],
    ]


def keyboard_payment_method(tarif):
    rows = _payment_rows_without_trial_card(tarif)
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def keyboard_payment_method_stock(tarif):
    return InlineKeyboardMarkup(inline_keyboard=_payment_rows_without_trial_card(tarif))


def keyboard_payment_sbp(text, pay_url):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=text,
                    url=pay_url,
                    style=STYLE_SUCCESS,
                )
            ]
        ]
    )


def keyboard_payment_stars(stars_amount):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Оплатить {stars_amount} ⭐️",
                    pay=True,
                    style=STYLE_SUCCESS,
                )
            ]
        ]
    )


def ref_keyboard(user_id):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Пригласить друзей🫶",
                    url=f"https://t.me/share/url?url={BOT_URL}?start=ref{user_id}&text={urllib.parse.quote(f'Вот ссылка на {bot_display_name()}!')}",
                    style=STYLE_SUCCESS,
                )
            ],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")],
        ]
    )
    return keyboard


def keyboard_inline_ref(user_id):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔗 Подключить ВПН",
                    url=f"{BOT_URL}?start=ref{user_id}",
                    style=STYLE_PRIMARY,
                )
            ]
        ]
    )


def keyboard_import_end(url_app: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📥 Скачать приложение",
                    url=url_app,
                    style=STYLE_PRIMARY,
                )
            ],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")],
        ]
    )


def keyboard_devices_subscriptions(slots: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """slots: (ключ слота, текст кнопки)."""
    buttons = []
    for slot_key, label in slots:
        buttons.append(
            [
                InlineKeyboardButton(
                    text=label[:64],
                    callback_data=f"dev_sub_{slot_key}",
                    style=STYLE_PRIMARY,
                )
            ]
        )
    buttons.append([InlineKeyboardButton(text=BTN_BACK, callback_data="dev_back_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def keyboard_devices_list(
    slot_key: str,
    devices: list[tuple[int, str]],
) -> InlineKeyboardMarkup:
    """devices: (индекс, текст кнопки)."""
    buttons = []
    for idx, btn_text in devices:
        buttons.append(
            [
                InlineKeyboardButton(
                    text=btn_text[:64],
                    callback_data=f"dev_rm_{slot_key}_{idx}",
                    style=STYLE_DANGER,
                )
            ]
        )
    buttons.append([InlineKeyboardButton(text=BTN_BACK, callback_data="dev_back_subs")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def keyboard_partner_intro():
    return create_kb(
        1,
        styles={
            "partner_create_link": STYLE_SUCCESS,
            "back_to_main": STYLE_PRIMARY,
        },
        partner_create_link='🔗 Создать партнёрскую ссылку',
        back_to_main=BTN_BACK,
    )


def keyboard_partner_dashboard():
    return create_kb(
        1,
        styles={
            "partner_withdraw": STYLE_SUCCESS,
            "back_to_main": STYLE_PRIMARY,
        },
        partner_withdraw='💰 Создать заявку на вывод',
        back_to_main=BTN_BACK,
    )


def keyboard_partner_withdraw(support_url: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="💬 Вывести деньги",
                url=support_url,
                style=STYLE_SUCCESS,
            )
        ],
        [
            InlineKeyboardButton(
                text="🔙 Назад",
                callback_data="partner_earn",
                style=STYLE_PRIMARY,
            )
        ],
    ])


def channel_keyboard(channel_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👉 Подписаться на канал", url=channel_url, style=STYLE_PRIMARY)],
        [
            InlineKeyboardButton(
                text="✅ Я подписался!",
                callback_data="channel_sub_check",
                style=STYLE_SUCCESS,
            )
        ],
    ])


def keyboard_main(
    *,
    show_owner_panel: bool = False,
    welcome_only: bool = False,
    show_trial: bool = False,
) -> InlineKeyboardMarkup:
    if welcome_only:
        return keyboard_start_bonus(show_owner_panel=show_owner_panel)
    rows = []
    if show_trial:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🔥 Попробовать бесплатно",
                    callback_data="trial_vpn",
                    style=STYLE_SUCCESS,
                )
            ]
        )
    rows.extend([
        [InlineKeyboardButton(text="🛒 Купить подписку", callback_data="buy_vpn", style=STYLE_SUCCESS)],
        [InlineKeyboardButton(text="🔗 Подключить ВПН", callback_data="connect_vpn", style=STYLE_PRIMARY)],
    ])
    rows.extend([
        [InlineKeyboardButton(text="💸 Зарабатывай с нами", callback_data="ref_program", style=STYLE_SUCCESS)],
        [InlineKeyboardButton(text="🎁 Подарить подписку", callback_data="buy_gift", style=STYLE_SUCCESS)],
    ])
    if show_owner_panel:
        rows.append([InlineKeyboardButton(text="⚙️ Панель партнёра", callback_data="owner_panel", style=STYLE_PRIMARY)])
    if SUPPORT_URL:
        rows.append([InlineKeyboardButton(text="👷 Поддержка", url=SUPPORT_URL, style=STYLE_PRIMARY)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def keyboard_buy_tiers():
    return keyboard_buy_device_tier()


def keyboard_duration(device: int, prefix: str = "r"):
    return keyboard_buy_duration(device) if prefix == "r" else keyboard_buy_duration(device)


def keyboard_gift_tiers():
    return keyboard_gift_device_tier()


def keyboard_payment_methods(tarif_key: str, amount: int, is_gift: bool = False):
    tarif = f"gift_{tarif_key}" if is_gift else tarif_key
    if is_gift and not tarif.startswith("gift_r_"):
        tarif = f"gift_r_{tarif_key.lstrip('r_')}" if tarif_key.startswith("r_") else f"gift_{tarif_key}"
    if is_gift:
        rows = []
        for row in _payment_rows_without_trial_card(tarif_key if tarif_key.startswith("r_") else f"r_{tarif_key}"):
            new_row = []
            for btn in row:
                cd = btn.callback_data.replace("wata_sbp_r_", "wata_sbp_gift_r_").replace(
                    "wata_card_r_", "wata_card_gift_r_"
                ).replace("stars_r_", "stars_gift_r_").replace("crypto_r_", "crypto_gift_r_")
                new_row.append(InlineKeyboardButton(text=btn.text, callback_data=cd, style=btn.style))
            rows.append(new_row)
        rows.append([InlineKeyboardButton(text=BTN_BACK, callback_data="back_to_main")])
        return InlineKeyboardMarkup(inline_keyboard=rows)
    return keyboard_payment_method(tarif_key if tarif_key.startswith("r_") else f"r_{tarif_key}")


def keyboard_ref_dashboard():
    return create_kb(1, back_to_main=BTN_BACK)


def keyboard_owner_prices(prices: dict, overrides: dict) -> InlineKeyboardMarkup:
    rows = []
    for key in TARIFF_KEYS:
        price = prices.get(key, 0)
        suffix = " база" if key not in overrides else ""
        label = OWNER_PRICE_SHORT.get(key, key)
        rows.append([
            InlineKeyboardButton(
                text=f"{label}: {price}₽{suffix}",
                callback_data=f"owner_price_edit:{key}",
            )
        ])
    rows.append([InlineKeyboardButton(text=BTN_BACK, callback_data="owner_panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def keyboard_owner_users(
    user_buttons: list[tuple[int, str]],
    *,
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for user_id, label in user_buttons:
        rows.append([
            InlineKeyboardButton(
                text=label,
                callback_data=f"owner_user_view:{user_id}",
            )
        ])
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"owner_users_page:{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"owner_users_page:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([
        InlineKeyboardButton(text="🔍 Поиск по ID / @username", callback_data="owner_users_search"),
    ])
    rows.append([InlineKeyboardButton(text=BTN_BACK, callback_data="owner_panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def keyboard_owner_main():
    return create_kb(
        1,
        styles={
            "owner_stats": STYLE_PRIMARY,
            "owner_broadcast": STYLE_PRIMARY,
            "owner_channel": STYLE_PRIMARY,
            "owner_users": STYLE_PRIMARY,
            "owner_prices": STYLE_SUCCESS,
            "owner_trial": STYLE_PRIMARY,
            "owner_balance": STYLE_SUCCESS,
        },
        owner_stats="📊 Статистика",
        owner_broadcast="📣 Рассылка",
        owner_channel="📢 Канал для подписки",
        owner_users="👥 Мои юзеры",
        owner_prices="💰 Мои цены",
        owner_trial="🎁 Триал",
        owner_balance="💳 Баланс и вывод",
        back_to_main=BTN_BACK,
    )
