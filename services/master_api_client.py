"""Клиент API мастер-бота для подачи заявок на создание VPN-бота."""
from __future__ import annotations

from typing import Any, Dict, Optional

import aiohttp

from config import BOT_ID, MASTER_BOT_API_KEY, MASTER_BOT_API_URL
from logging_config import logger


class MasterApiError(Exception):
    pass


async def submit_partner_bot_application(
    *,
    partner_tg_id: int,
    partner_username: Optional[str],
    partner_first_name: Optional[str],
    bot_token: str,
) -> Dict[str, Any]:
    if not MASTER_BOT_API_KEY:
        raise MasterApiError("MASTER_BOT_API_KEY не настроен в .env")

    url = f"{MASTER_BOT_API_URL}/api/partner/applications"
    body = {
        "source_bot_id": BOT_ID,
        "partner_tg_id": partner_tg_id,
        "partner_username": partner_username,
        "partner_first_name": partner_first_name,
        "bot_token": bot_token.strip(),
    }
    headers = {
        "X-Partner-Bot-Api-Key": MASTER_BOT_API_KEY,
        "Content-Type": "application/json",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            try:
                data = await resp.json()
            except Exception:
                data = {"detail": await resp.text()}
            if resp.status >= 400:
                detail = data.get("detail", data)
                logger.error("master API application failed: status={} detail={}", resp.status, detail)
                raise MasterApiError(str(detail))
            return data
