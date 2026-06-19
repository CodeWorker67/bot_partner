from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware

from bot import sql
from config import OWNER_TG_ID


class UserActivityMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user and user.id != OWNER_TG_ID:
            try:
                if await sql.get_user(user.id):
                    await sql.touch_user_activity(user.id)
            except Exception:
                pass
        return await handler(event, data)
