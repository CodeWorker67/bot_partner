"""Отправка транзакционных писем через Unisender Go Web API (HTTPS)."""
from __future__ import annotations

from email.utils import parseaddr
from typing import Optional

import aiohttp

from config import (
    SMTP_FROM,
    UNISENDER_GO_API_KEY,
    UNISENDER_GO_API_URL,
    UNISENDER_GO_FROM_NAME,
)
from logging_config import logger


class UnisenderGoError(RuntimeError):
    """Ошибка вызова Unisender Go API."""


def unisender_go_configured() -> bool:
    return bool(UNISENDER_GO_API_KEY and SMTP_FROM)


def _send_endpoint() -> str:
    base = UNISENDER_GO_API_URL.rstrip("/")
    if base.endswith("email/send.json"):
        return base
    return f"{base}/email/send.json"


def _from_fields() -> tuple[str, str]:
    if not SMTP_FROM:
        raise UnisenderGoError("SMTP_FROM не задан (адрес отправителя для API)")
    name, addr = parseaddr(SMTP_FROM)
    from_email = addr or SMTP_FROM.strip()
    from_name = name or UNISENDER_GO_FROM_NAME
    return from_email, from_name


async def send_transactional_email(
    *,
    to_email: str,
    subject: str,
    plaintext: str,
    html: Optional[str] = None,
) -> None:
    """
    POST email/send.json — см. https://godocs.unisender.ru/web-api-ref
    """
    if not UNISENDER_GO_API_KEY:
        raise UnisenderGoError("UNISENDER_GO_API_KEY не задан")
    from_email, from_name = _from_fields()

    body: dict = {"plaintext": plaintext}
    if html:
        body["html"] = html

    payload = {
        "message": {
            "recipients": [{"email": to_email.strip()}],
            "body": body,
            "subject": subject,
            "from_email": from_email,
            "from_name": from_name,
            "reply_to": from_email,
            "track_links": 0,
            "track_read": 0,
            "tags": ["auth"],
        }
    }

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-API-KEY": UNISENDER_GO_API_KEY,
    }

    url = _send_endpoint()
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            if resp.status != 200:
                err_text = await resp.text()
                raise UnisenderGoError(
                    f"HTTP {resp.status}: {err_text[:500]}"
                )
            try:
                data = await resp.json(content_type=None)
            except Exception:
                logger.info("Unisender Go: письмо отправлено (ответ не JSON)")
                return

            if isinstance(data, dict):
                if data.get("status") == "error" or data.get("error"):
                    raise UnisenderGoError(str(data))
                failed = data.get("failed_emails")
                if failed:
                    raise UnisenderGoError(f"failed_emails: {failed}")
