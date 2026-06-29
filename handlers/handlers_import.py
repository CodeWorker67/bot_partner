from aiogram import Router, F
from aiogram.types import CallbackQuery

from bot import x3
from channel_gate import require_channel_sub
from config import BOT_ID
from keyboard import (
    create_kb,
    keyboard_import_app,
    keyboard_import_end,
    keyboard_import_os,
    keyboard_import_sub,
)
from lexicon import lexicon
from tariff_resolve import panel_username

router: Router = Router()

OS_CALLBACKS = {"import_android", "import_ios", "import_windows", "import_macos"}

OS_DISPLAY = {
    "android": "🤖 Android",
    "ios": "🍎 iOS",
    "windows": "🖥️ Windows",
    "macos": "🍏 MacOS",
}

APP_DISPLAY = {
    "incy": "🔥 INCY",
    "happ": "⭐️ Happ",
    "v2": "📡 V2raytun",
}

IMPORT_URLS = {
    "android": {
        "incy": {
            "url_app": "https://play.google.com/store/apps/details?id=llc.itdev.incy",
        },
        "happ": {
            "url_app": "https://play.google.com/store/apps/details?id=com.happproxy",
        },
        "v2": {
            "url_app": "https://play.google.com/store/apps/details?id=com.v2raytun.android",
        },
    },
    "ios": {
        "incy": {
            "url_app": "https://apps.apple.com/ru/app/incy/id6756943388",
        },
        "happ": {
            "url_app": "https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973",
        },
        "v2": {
            "url_app": "https://apps.apple.com/app/v2raytun/id6476628951",
        },
    },
    "windows": {
        "incy": {
            "url_app": "https://github.com/INCY-DEV/incy-platforms/releases/latest/download/incy-windows-setup.exe",
        },
        "happ": {
            "url_app": "https://github.com/Happ-proxy/happ-desktop/releases/latest/download/setup-Happ.x64.exe",
        },
        "v2": {
            "url_app": "https://v2raytun.com/",
        },
    },
    "macos": {
        "incy": {
            "url_app": "https://github.com/INCY-DEV/incy-platforms/releases/latest/download/incy-macos-arm64.dmg",
        },
        "happ": {
            "url_app": "https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973",
        },
        "v2": {
            "url_app": "https://apps.apple.com/ru/app/v2raytun/id6476628951",
        },
    },
}

_SLOT_DEVICE_SLOTS = {
    "main": 5,
    "3": 3,
    "10": 10,
}


def _panel_username_for_slot(telegram_id: int, slot_key: str) -> str:
    device_slots = _SLOT_DEVICE_SLOTS.get(slot_key, 5)
    return panel_username(telegram_id, BOT_ID, device_slots=device_slots)


@router.callback_query(F.data == "import")
@require_channel_sub
async def import_select_os(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        text=lexicon["import_start"],
        reply_markup=keyboard_import_os(),
    )


@router.callback_query(F.data.in_(OS_CALLBACKS))
@require_channel_sub
async def import_select_app(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        text=lexicon["import_select_app"],
        reply_markup=keyboard_import_app(callback.data),
    )


@router.callback_query(
    F.data.startswith("import_")
    & (F.data.endswith("_incy") | F.data.endswith("_happ") | F.data.endswith("_v2"))
)
@require_channel_sub
async def import_select_sub(callback: CallbackQuery):
    links = await x3.active_subscription_links(callback.from_user.id, BOT_ID)
    if not links:
        await callback.answer()
        await callback.message.answer(
            text=lexicon["no_sub"],
            reply_markup=create_kb(1, back_to_main="🔙 Назад"),
        )
        return

    await callback.answer()
    await callback.message.answer(
        text=lexicon["import_select_sub"],
        reply_markup=keyboard_import_sub(callback.data, links),
    )


@router.callback_query(F.data.startswith("import_") & F.data.contains("_sub_"))
@require_channel_sub
async def import_end(callback: CallbackQuery):
    await callback.answer()
    app_callback, slot_key = callback.data.rsplit("_sub_", 1)
    parts = app_callback.split("_")
    if len(parts) < 3:
        return
    os_key, app_key = parts[1], parts[2]

    username = _panel_username_for_slot(callback.from_user.id, slot_key)
    sub_url = await x3.sublink(username)
    if not sub_url:
        await callback.message.answer(
            "❌ Не удалось получить ссылку. Обратитесь в поддержку.",
            reply_markup=create_kb(1, back_to_main="🔙 Назад"),
        )
        return

    links = await x3.active_subscription_links(callback.from_user.id, BOT_ID)
    label = next((text for text, _, key in links if key == slot_key), "Подписка")

    urls = IMPORT_URLS.get(os_key, {}).get(app_key)
    if not urls:
        return
    url_app = urls["url_app"]

    if app_key == "incy":
        lexicon_key = "import_end_incy"
    elif app_key == "happ":
        lexicon_key = "import_end_happ"
    else:
        lexicon_key = "import_end_v2"

    caption = lexicon[lexicon_key].format(
        os=OS_DISPLAY.get(os_key, os_key),
        app=APP_DISPLAY.get(app_key, app_key),
        label=label,
        url_app=url_app,
        url_import=sub_url,
    )

    await callback.message.answer(
        caption,
        parse_mode="HTML",
        reply_markup=keyboard_import_end(url_app),
    )
