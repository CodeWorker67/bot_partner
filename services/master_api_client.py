"""Клиент API мастер-бота для подачи заявок на создание VPN-бота."""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

import aiohttp

from config import (
    BOT_ID,
    MASTER_BOT_API_CONNECT_TIMEOUT,
    MASTER_BOT_API_KEY,
    MASTER_BOT_API_TIMEOUT,
    MASTER_BOT_API_URL,
)
from logging_config import logger


class MasterApiError(Exception):
    pass


def _timeout() -> aiohttp.ClientTimeout:
    return aiohttp.ClientTimeout(
        total=MASTER_BOT_API_TIMEOUT,
        connect=MASTER_BOT_API_CONNECT_TIMEOUT,
        sock_connect=MASTER_BOT_API_CONNECT_TIMEOUT,
        sock_read=MASTER_BOT_API_TIMEOUT,
    )


async def check_master_api_reachable() -> bool:
    """Проверка доступности API мастер-бота (без авторизации)."""
    url = f"{MASTER_BOT_API_URL}/api/partner/health"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=_timeout()) as resp:
                return resp.status == 200
    except Exception as e:
        logger.warning("master API health check failed: {} url={}", e, url)
        return False


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
    logger.info("master API application request: url={} source_bot_id={}", url, BOT_ID)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=body, headers=headers, timeout=_timeout()) as resp:
                try:
                    data = await resp.json()
                except Exception:
                    data = {"detail": (await resp.text())[:500]}
                if resp.status >= 400:
                    detail = data.get("detail", data)
                    logger.error("master API application failed: status={} detail={}", resp.status, detail)
                    raise MasterApiError(str(detail))
                return data
    except MasterApiError:
        raise
    except asyncio.TimeoutError as e:
        logger.error("master API timeout: url={}", url)
        raise MasterApiError(
            f"Таймаут при обращении к мастер-боту ({MASTER_BOT_API_URL}). "
            "Проверьте доступность API с VPS партнёров и значение MASTER_BOT_API_URL."
        ) from e
    except aiohttp.ClientConnectorError as e:
        logger.error("master API connection error: url={} err={}", url, e)
        raise MasterApiError(
            f"Не удалось подключиться к мастер-боту ({MASTER_BOT_API_URL}). "
            "Откройте порт API на VPS мастер-бота или укажите прямой URL, например "
            "http://176.125.241.211:8080"
        ) from e
    except aiohttp.ClientError as e:
        logger.error("master API client error: url={} err={}", url, e)
        raise MasterApiError(f"Ошибка сети при обращении к мастер-боту: {e}") from e
