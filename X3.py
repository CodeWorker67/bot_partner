import base64
import datetime
import hashlib
import hmac
import uuid
from typing import Any, Dict, List, Optional, Tuple

import urllib3
import aiohttp

from config import PANEL_API_TOKEN, PANEL_URL, TRUE_SUB_LINK, MIRROR_SUB_LINK, SHORT_UUID_SECRET
from config_bd.partner_sql import PartnerSQL
from logging_config import logger
import random
import string

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class X3:
    def __init__(self):
        """Инициализация класса с настройками подключения"""
        self.target_url = PANEL_URL
        self.api_token = PANEL_API_TOKEN
        
        self.headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.api_token}'
        }
        
        self.params = {
            "vyWdoTBH": "VmsLiQrN"
        }

        self._session: aiohttp.ClientSession = None
        self.working_host = self.target_url
        self.is_authenticated = True

    async def _get_session(self) -> aiohttp.ClientSession:
        """Возвращает активную сессию aiohttp, создавая её при необходимости."""
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(ssl=False)
            self._session = aiohttp.ClientSession(
                headers=self.headers,
                connector=connector
            )
        return self._session

    async def close(self):
        """Закрывает сессию aiohttp (вызывать при завершении работы)."""
        if self._session and not self._session.closed:
            await self._session.close()

    def generate_client_id(self, tg_id, panel_username: str) -> str:
        """shortUuid: HMAC от panel_username (разные слоты — разные id); white — как раньше по tg_id*100."""
        if not SHORT_UUID_SECRET:
            raise ValueError(
                "SHORT_UUID_SECRET не задан в окружении (.env) — нужен для генерации shortUuid"
            )
        key = SHORT_UUID_SECRET.encode("utf-8")
        if 'white' in panel_username:
            msg = str(int(tg_id) * 100).encode("utf-8")
        else:
            msg = panel_username.encode("utf-8")
        digest = hmac.new(key, msg, hashlib.sha256).digest()
        token = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        return token[:15]

    async def _persist_subscription_db(
        self,
        sql_inst: PartnerSQL,
        user_id: int,
        user_id_str: str,
        subscription_end_date: datetime.datetime,
        *,
        client_id: Optional[str] = None,
    ) -> None:
        """Сохраняем окончание подписки (и shortUuid при создании клиента) по образцу username в панели."""
        if user_id_str.endswith('_3'):
            await sql_inst.update_subscription_3_end_date(user_id, subscription_end_date)
            if client_id is not None:
                await sql_inst.update_subscribtion_3(user_id, client_id)
        elif user_id_str.endswith('_10'):
            await sql_inst.update_subscription_10_end_date(user_id, subscription_end_date)
            if client_id is not None:
                await sql_inst.update_subscribtion_10(user_id, client_id)
        else:
            await sql_inst.update_subscription_end_date(user_id, subscription_end_date)
            if client_id is not None:
                await sql_inst.update_subscribtion(user_id, client_id)

    def list_from_host(self, host):
        """Заглушка для совместимости со старым кодом"""
        return {'obj': [{'settings': '{"clients": []}'}]}

    async def test_connect(self):
        try:
            session = await self._get_session()
            async with session.get(
                    f"{self.target_url}/api/auth/status",
                    params=self.params,
                    timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                logger.info(f"Тест подключения: {response.status}")
                return response.status == 200
        except Exception as e:
            logger.error(f"Ошибка подключения: {e}")
            return False

    async def list(self, start):
        try:
            params = self.params
            params['size'] = 1000
            params['start'] = start
            session = await self._get_session()
            async with session.get(
                    f'{self.target_url}/api/users',
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 200:
                    logger.info(f'Получены юзеры с {start}')
                    return await resp.json()
                else:
                    logger.error(f"HTTP {resp.status}: {await resp.text()}")
                    return {'response': {'users': []}}
        except Exception as e:
            logger.error(f"Ошибка запроса: {e}")
            return {'response': {'users': []}}

    def _generate_password(self, length=12):
        """Генерирует случайный пароль"""
        chars = string.ascii_letters + string.digits
        return ''.join(random.choice(chars) for _ in range(length))

    async def addClient(
        self,
        day,
        user_id_str,
        user_id,
        hwid_device_limit: Optional[int] = None,
    ):
        """Добавляет нового клиента. hwid_device_limit — лимит устройств PRO (по умолчанию 5)."""
        try:
            client_id = self.generate_client_id(user_id, user_id_str)
            current_time = datetime.datetime.now(datetime.timezone.utc)
            expire_time = current_time + datetime.timedelta(days=day)
            vless_uuid = str(uuid.uuid1())

            if 'white' in user_id_str:
                squad_1 = ['a4edd9cf-33f7-4439-b9cf-1da5e319bf91']
                squad = squad_1
                trafficLimitStrategy = "MONTH"
                trafficLimitBytes = 80530636800
                hwidDeviceLimit = 1
            else:
                squad_1 = ['a4edd9cf-33f7-4439-b9cf-1da5e319bf91']
                squad_2 = ['a4edd9cf-33f7-4439-b9cf-1da5e319bf91']
                squad = random.choice([squad_1, squad_2])
                trafficLimitStrategy = "NO_RESET"
                trafficLimitBytes = 0
                hwidDeviceLimit = 5 if hwid_device_limit is None else int(hwid_device_limit)
            desc = 'VPN for friends'
            data = {
                "username": user_id_str,
                "status": "ACTIVE",
                "shortUuid": client_id,
                "trojanPassword": self._generate_password(),
                "vlessUuid": vless_uuid,
                "ssPassword": self._generate_password(),
                "trafficLimitStrategy": trafficLimitStrategy,
                "trafficLimitBytes": trafficLimitBytes,
                "expireAt": expire_time.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
                "createdAt": current_time.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
                "hwidDeviceLimit": hwidDeviceLimit,
                "telegramId": int(user_id),
                "description": desc,
                "activeInternalSquads": squad
            }

            logger.info(f"Добавление клиента {user_id_str}, срок до: {expire_time}")

            session = await self._get_session()
            async with session.post(
                    f"{self.target_url}/api/users",
                    json=data,
                    params=self.params,
                    timeout=aiohttp.ClientTimeout(total=15)
            ) as response:
                logger.info(f"Код ответа: {response.status}")

                if response.status in [200, 201]:
                    sql = PartnerSQL()
                    try:
                        response_data = await response.json()
                    except (aiohttp.ClientConnectionError, aiohttp.ContentTypeError, ValueError) as e:
                        # Сервер мог не вернуть JSON, но статус успешный
                        logger.warning(f"Не удалось прочитать JSON при добавлении {user_id}: {e}. Считаем успехом.")
                        subscription_end_date = expire_time.replace(tzinfo=datetime.timezone.utc)
                        await self._persist_subscription_db(
                            sql, user_id, user_id_str, subscription_end_date, client_id=client_id
                        )
                        logger.info(f"✅ Клиент {user_id} успешно добавлен (без JSON)")
                        return True
                    else:
                        if response_data.get("success", True):
                            subscription_end_date = expire_time.replace(tzinfo=datetime.timezone.utc)
                            await self._persist_subscription_db(
                                sql, user_id, user_id_str, subscription_end_date, client_id=client_id
                            )
                            logger.info(f"✅ Клиент {user_id} успешно добавлен")
                            return True
                        else:
                            logger.warning(f"❌ API вернул ошибку: {response_data}")
                            return False
                else:
                    error_text = await response.text() if response.content else "No body"
                    logger.error(f"❌ Ошибка добавления клиента: HTTP {response.status} - {error_text}")
                    return False

        except Exception as e:
            logger.error(f"❌ Ошибка при добавлении клиента {user_id}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    async def updateClient(self, day, user_id_str, user_id):
        """Обновляет клиента - добавляет дни к подписке"""
        try:
            # Получаем данные пользователя
            user_response = await self.get_user_by_username(user_id_str)

            if not user_response or 'response' not in user_response:
                logger.error(f"❌ Пользователь {user_id_str} не найден")
                return False

            user = user_response['response']
            
            # Проверяем обязательные поля
            if 'uuid' not in user or 'expireAt' not in user:
                logger.error(f"❌ У пользователя {user_id_str} отсутствуют обязательные поля")
                return False

            uuid_user = user['uuid']
            
            # Парсим текущую дату истечения
            expire_at_str = user['expireAt']
            current_expire_at = datetime.datetime.fromisoformat(expire_at_str.replace('Z', '+00:00'))
            now = datetime.datetime.now(datetime.timezone.utc)

            # Определяем новую дату истечения
            if current_expire_at < now:
                # Подписка истекла - начинаем с текущего момента
                new_expire_at = now + datetime.timedelta(days=day)
                status = 'ACTIVE'  # Активируем подписку
                logger.info(f"Подписка пользователя {user_id_str} истекла. Активируем и добавляем {day} дней")
            else:
                # Подписка активна - добавляем к существующей дате
                new_expire_at = current_expire_at + datetime.timedelta(days=day)
                status = user.get('status', 'ACTIVE')
                logger.info(f"Подписка пользователя {user_id_str} активна. Добавляем {day} дней")

            # Обрабатываем activeInternalSquads
            raw_squads = user.get('activeInternalSquads', [])
            squads = []
            for s in raw_squads:
                if isinstance(s, dict) and 'uuid' in s:
                    squads.append(s['uuid'])
                elif isinstance(s, str):
                    squads.append(s)

            # Формируем данные для обновления
            data = {
                "uuid": uuid_user,
                "status": status,
                "expireAt": new_expire_at.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
                "trafficLimitBytes": user.get('trafficLimitBytes', 0),
                "trafficLimitStrategy": user.get('trafficLimitStrategy', "NO_RESET"),
                "activeInternalSquads": squads
            }

            logger.info(f"Обновление пользователя {user_id_str}:")
            logger.info(f"  Старая дата: {current_expire_at.strftime('%Y-%m-%d %H:%M:%S')}")
            logger.info(f"  Новая дата: {new_expire_at.strftime('%Y-%m-%d %H:%M:%S')}")
            logger.info(f"  Добавлено дней: {day}")

            session = await self._get_session()
            async with session.patch(
                    f"{self.target_url}/api/users",
                    json=data,
                    params=self.params,
                    timeout=aiohttp.ClientTimeout(total=15)
            ) as response:
                logger.info(f"Код ответа updateClient: {response.status}")
                if response.status == 200:
                    sql = PartnerSQL()
                    try:
                        response_data = await response.json()
                    except (aiohttp.ClientConnectionError, aiohttp.ContentTypeError, ValueError) as e:
                        logger.warning(f"Не удалось прочитать JSON при обновлении {user_id}: {e}. Считаем успехом.")
                        await self._persist_subscription_db(sql, user_id, user_id_str, new_expire_at)
                        logger.info(f"✅ Клиент {user_id} успешно обновлён (без JSON), добавлено {day} дней")
                        return True
                    else:
                        if response_data.get("success", True):
                            await self._persist_subscription_db(sql, user_id, user_id_str, new_expire_at)
                            logger.info(f"✅ Клиент {user_id} успешно обновлён, добавлено {day} дней")
                            return True
                        else:
                            logger.error(f"❌ API вернул success=false: {response_data}")
                            return False
                else:
                    error_text = await response.text() if response.content else "No body"
                    logger.error(f"❌ Ошибка обновления: HTTP {response.status}, {error_text}")
                    return False

        except Exception as e:
            logger.error(f"❌ Ошибка при обновлении клиента {user_id}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    async def get_user_by_username(self, username):
        try:
            session = await self._get_session()
            async with session.get(
                    f"{self.target_url}/api/users/by-username/{username}",
                    params=self.params,
                    timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    try:
                        return await resp.json()
                    except:
                        logger.error(f"Не удалось прочитать JSON для пользователя {username}")
                        return None
                else:
                    logger.error(f"Ошибка получения пользователя {username}: {await resp.text()}")
                    return None
        except Exception as e:
            logger.error(f"Ошибка получения пользователя {username}: {e}")
            return None

    async def get_user_by_telegram_id(self, telegram_id):
        try:
            session = await self._get_session()
            async with session.get(
                    f"{self.target_url}/api/users/by-telegram-id/{telegram_id}",
                    params=self.params,
                    timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    try:
                        return await resp.json()
                    except:
                        return None
                else:
                    return None
        except Exception as e:
            logger.error(f"Ошибка получения пользователя по telegram_id {telegram_id}: {e}")
            return None

    async def sublink(self, user_id: str):
        try:
            users = await self.get_user_by_username(user_id)
            if users and 'response' in users and users['response']:
                raw = users['response']
                user = raw[0] if isinstance(raw, list) else raw
                true_sublink = user.get('subscriptionUrl', '')
                return true_sublink.replace(TRUE_SUB_LINK, MIRROR_SUB_LINK)
        except Exception as e:
            logger.error(f"Ошибка при получении ссылки для {user_id}: {e}")
        return ""

    SUBSCRIPTION_SLOTS: Tuple[Tuple[str, str, str], ...] = (
        ("main", "", "💫 Подписка · 5 устройств"),
        ("3", "_3", "💫 Подписка · 3 устройства"),
        ("10", "_10", "💫 Подписка · 10 устройств"),
        ("white", "_white", "🦾 Мобильный тариф"),
    )

    def username_for_slot(self, telegram_id: int, slot_key: str) -> str:
        for key, suffix, _ in self.SUBSCRIPTION_SLOTS:
            if key == slot_key:
                return f"{telegram_id}{suffix}"
        return str(telegram_id)

    @staticmethod
    def _panel_user_from_response(users: Optional[dict]) -> Optional[dict]:
        if not users or 'response' not in users or not users['response']:
            return None
        raw = users['response']
        return raw[0] if isinstance(raw, list) else raw

    @staticmethod
    def _panel_user_is_active(user: dict) -> bool:
        expiry_time_str = user.get('expireAt')
        if not expiry_time_str:
            return False
        expiry_dt = datetime.datetime.fromisoformat(expiry_time_str.replace('Z', '+00:00'))
        now = datetime.datetime.now(datetime.timezone.utc)
        expiry_time = int(expiry_dt.timestamp() * 1000)
        current_time = int(now.timestamp() * 1000)
        return user.get('status') == 'ACTIVE' and expiry_time > current_time

    async def active_subscription_links(
        self, telegram_id: int, bot_id: int | None = None,
    ) -> List[Tuple[str, str, str]]:
        from config import BOT_ID
        bid = bot_id if bot_id is not None else BOT_ID
        out: List[Tuple[str, str, str]] = []
        slots = (
            ("main", "", "💫 Подписка · 5 устройств"),
            ("3", "_3", "💫 Подписка · 3 устройства"),
            ("10", "_10", "💫 Подписка · 10 устройств"),
        )
        for slot_key, suffix, label in slots:
            username = f"{telegram_id}_{bid}{suffix}"
            users = await self.get_user_by_username(username)
            user = self._panel_user_from_response(users)
            if not user or not self._panel_user_is_active(user):
                continue
            url = await self.sublink(username)
            if url:
                out.append((label, url, slot_key))
        return out

    async def active_subscription_slots(
        self, telegram_id: int,
    ) -> List[Tuple[str, str, str, str]]:
        """Активные подписки: (ключ слота, подпись, uuid в панели, username)."""
        out: List[Tuple[str, str, str, str]] = []
        for slot_key, suffix, label in self.SUBSCRIPTION_SLOTS:
            username = f"{telegram_id}{suffix}"
            users = await self.get_user_by_username(username)
            user = self._panel_user_from_response(users)
            if not user or not self._panel_user_is_active(user):
                continue
            user_uuid = user.get('uuid')
            if not user_uuid:
                continue
            out.append((slot_key, label, user_uuid, username))
        return out

    async def get_user_hwid_devices(self, user_uuid: str) -> Tuple[List[Dict[str, Any]], int]:
        """Список HWID-устройств пользователя и их количество."""
        try:
            session = await self._get_session()
            async with session.get(
                f"{self.target_url}/api/hwid/devices/{user_uuid}",
                params=self.params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    logger.error(
                        f"get_user_hwid_devices {user_uuid}: HTTP {resp.status} — {await resp.text()}"
                    )
                    return [], 0
                data = await resp.json()
        except Exception as e:
            logger.error(f"get_user_hwid_devices {user_uuid}: {e}")
            return [], 0

        response = data.get('response') if isinstance(data, dict) else None
        if isinstance(response, list):
            devices = response
            total = len(devices)
        elif isinstance(response, dict):
            devices = response.get('devices') or []
            total = response.get('total', len(devices))
        else:
            devices = []
            total = 0
        return devices, int(total)

    async def delete_user_hwid_device(self, user_uuid: str, hwid: str) -> bool:
        """Удаляет одно HWID-устройство пользователя."""
        try:
            session = await self._get_session()
            async with session.post(
                f"{self.target_url}/api/hwid/devices/delete",
                json={"userUuid": user_uuid, "hwid": hwid},
                params=self.params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    logger.error(
                        f"delete_user_hwid_device {user_uuid}: HTTP {resp.status} — {await resp.text()}"
                    )
                    return False
                data = await resp.json()
                if isinstance(data, dict) and data.get('success') is False:
                    logger.error(f"delete_user_hwid_device API: {data}")
                    return False
                return True
        except Exception as e:
            logger.error(f"delete_user_hwid_device {user_uuid}: {e}")
            return False

    async def activ(self, user_id: str):
        result = {'activ': '🔎 - Не подключён', 'time': '-'}
        try:
            users = await self.get_user_by_username(user_id)
            if not users or 'response' not in users or not users['response']:
                logger.info(f"Пользователь {user_id} не найден в системе")
                return result

            raw = users['response']
            user = raw[0] if isinstance(raw, list) else raw
            current_time = int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)

            expiry_time_str = user.get('expireAt')
            if not expiry_time_str:
                return result

            expiry_dt = datetime.datetime.fromisoformat(expiry_time_str.replace('Z', '+00:00'))
            expiry_time = int(expiry_dt.timestamp() * 1000)

            expiry_dt_msk = expiry_dt + datetime.timedelta(hours=3)
            readable_time = expiry_dt_msk.strftime('%d-%m-%Y %H:%M') + ' МСК'
            result['time'] = readable_time

            if user.get('status') == 'ACTIVE' and expiry_time > current_time:
                result['activ'] = '✅ - Активен'
            else:
                result['activ'] = '❌ - Не Активен'

            true_sublink = user.get('subscriptionUrl', '')
            if true_sublink:
                result['url'] = true_sublink.replace(TRUE_SUB_LINK, MIRROR_SUB_LINK)

            return result

        except Exception as e:
            logger.error(f"Ошибка в методе activ для {user_id}: {e}")
            result['activ'] = '❌ - Внутренняя ошибка'
            return result

    async def activ_list(self):
        lst_users = []
        try:
            users_all = []
            for i in range(200):
                data = await self.list(1000 * i + 1)
                if data['response']['users']:
                    users_all.extend(data['response']['users'])
                else:
                    break
            logger.info(f'Всего юзеров в панели - {len(users_all)}')
            for user in users_all:
                if user.get('userTraffic', {}).get('firstConnectedAt'):
                    telegram_id = user.get('telegramId')
                    if telegram_id is not None:
                        lst_users.append(int(telegram_id))
            logger.info(f'Всего юзеров подключенных - {len(lst_users)}')
        except Exception as e:
            logger.error(f"Ошибка при получении списка активности: {e}")
        return lst_users

    async def get_all_users(self):
        """
        Возвращает список всех пользователей из панели (объекты пользователей),
        у которых description == 'New user - without pay'.
        """
        users_all = []
        try:
            for i in range(200):  # максимум 50 страниц
                data = await self.list(1000 * i + 1)
                if data['response']['users']:
                    users_all.extend(data['response']['users'])
                else:
                    break
            logger.info(f'Всего юзеров в панели - {len(users_all)}')
        except Exception as e:
            logger.error(f"Ошибка при получении всех пользователей: {e}")
        return users_all

    async def update_user_squads(self, user_uuid: str, squads: list):
        """
        Обновляет поле activeInternalSquads у пользователя по его UUID.
        :param user_uuid: UUID пользователя в панели
        :param squads: список squad UUID (например, ['2fcfd928-6f45-4a8c-a36b-742fca8efea0'])
        :return: True при успехе, False при ошибке
        """
        try:
            data = {
                "uuid": user_uuid,
                "activeInternalSquads": squads
            }
            session = await self._get_session()
            async with session.patch(
                    f"{self.target_url}/api/users",
                    json=data,
                    params=self.params,
                    timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    try:
                        response_data = await response.json()
                    except (aiohttp.ClientConnectionError, aiohttp.ContentTypeError, ValueError) as e:
                        logger.warning(
                            f"Не удалось прочитать JSON при обновлении squads для UUID {user_uuid}: {e}. Считаем успехом.")
                        return True
                    else:
                        if response_data.get("success", True):
                            logger.info(f"✅ Squad обновлён для UUID {user_uuid}")
                            return True
                        else:
                            logger.error(f"❌ API вернул ошибку: {response_data}")
                            return False
                else:
                    error_text = await response.text() if response.content else "No body"
                    logger.error(f"❌ Ошибка HTTP {response.status}: {error_text}")
                    return False
        except Exception as e:
            logger.error(f"❌ Исключение при обновлении squads: {e}")
            return False

    async def get_all_panel(self):
        """
        Возвращает список всех пользователей из панели (объекты пользователей),
        у которых description == 'New user - without pay'.
        """
        lst_users = []
        try:
            users_all = []
            for i in range(200):  # максимум 100 страниц
                data = await self.list(1000 * i + 1)
                if data['response']['users']:
                    users_all.extend(data['response']['users'])
                else:
                    break
            logger.info(f'Всего юзеров в панели - {len(users_all)}')
            for user in users_all:
                lst_users.append(user)
        except Exception as e:
            logger.error(f"Ошибка при получении всех пользователей: {e}")
        return lst_users

    async def set_expiration_date(
        self,
        username: str,
        target_date: datetime,
        user_id: int,
        hwid_device_limit: Optional[int] = None,
    ):
        """
        Устанавливает точную дату окончания подписки для пользователя в панели.
        - Если пользователь не существует, создаёт его через addClient (с day=0).
        - Если target_date меньше текущего времени UTC, заменяет на текущее время + 1 минута.
        - Возвращает (успех, реальная_установленная_дата_UTC) или (False, None).
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        effective_date = target_date if target_date > now else now + datetime.timedelta(minutes=1)

        # Проверяем существование пользователя
        user_data = await self.get_user_by_username(username)
        if not user_data or 'response' not in user_data:
            # Пользователь отсутствует – создаём
            if not await self.addClient(0, username, user_id, hwid_device_limit=hwid_device_limit):
                logger.error(f"Не удалось создать пользователя {username} для установки даты")
                return False, None
            # После создания получаем данные заново
            user_data = await self.get_user_by_username(username)
            if not user_data or 'response' not in user_data:
                logger.error(f"Не удалось получить данные созданного пользователя {username}")
                return False, None

        user = user_data['response']
        uuid_user = user['uuid']

        # Формируем данные для обновления (сохраняем остальные поля)
        traffic_limit_bytes = user.get('trafficLimitBytes', 0)
        traffic_limit_strategy = user.get('trafficLimitStrategy', 'NO_RESET')
        status = 'ACTIVE'  # Активируем подписку
        raw_squads = user.get('activeInternalSquads', [])
        squads = [s['uuid'] if isinstance(s, dict) else s for s in raw_squads]

        data = {
            "uuid": uuid_user,
            "expireAt": effective_date.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
            "status": status,
            "trafficLimitBytes": traffic_limit_bytes,
            "trafficLimitStrategy": traffic_limit_strategy,
            "activeInternalSquads": squads
        }

        session = await self._get_session()
        try:
            async with session.patch(
                    f"{self.target_url}/api/users",
                    json=data,
                    params=self.params,
                    timeout=aiohttp.ClientTimeout(total=15)
            ) as response:
                if response.status == 200:
                    try:
                        resp_json = await response.json()
                        if resp_json.get('success', True):
                            logger.info(f"✅ Установлена дата {effective_date} для {username}")
                            return True, effective_date
                        else:
                            logger.error(f"Ошибка API при установке даты: {resp_json}")
                            return False, None
                    except:
                        # Нет JSON, но статус 200 – считаем успехом
                        logger.warning(f"Установка даты для {username} вернула 200 без JSON, считаем успешной")
                        return True, effective_date
                else:
                    error_text = await response.text() if response.content else "No body"
                    logger.error(f"Ошибка HTTP {response.status} при установке даты: {error_text}")
                    return False, None
        except Exception as e:
            logger.error(f"Исключение при установке даты для {username}: {e}")
            return False, None
