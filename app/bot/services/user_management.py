"""
Сервис для управления пользователями в админ-панели.
Содержит бизнес-логику для поиска, фильтрации и операций над пользователями.
"""
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.bot.utils.constants import TransactionStatus
from app.bot.utils.time import days_to_timestamp, get_current_timestamp
from app.db.models import Server, Transaction, User

logger = logging.getLogger(__name__)

class UserManagementService:
    """Сервис для управления пользователями."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def search_users(
        self,
        query: str | None = None,
        subscription_status: str | None = None,
        date_filter: str | None = None,
        server_id: int | None = None,
        page: int = 0,
        limit: int = 10,
    ) -> tuple[list[User], int]:
        """
        Поиск и фильтрация пользователей.
        
        Args:
            query: Поисковый запрос (Telegram ID, username, имя)
            subscription_status: Статус подписки (active/expired/none)
            date_filter: Фильтр по дате регистрации (today/week/month/all)
            server_id: ID сервера для фильтрации
            page: Номер страницы (начиная с 0)
            limit: Количество результатов на странице
        
        Returns:
            Кортеж (список пользователей, общее количество)
        """
        try:
            # Базовый запрос с загрузкой связей
            base_query = select(User).options(
                selectinload(User.server),
                selectinload(User.transactions),
            )
            
            # Список всех условий (будут объединены через AND)
            all_conditions = []
            
            # Поиск по запросу
            query_clean = None
            if query and len(query.strip()) >= 3:
                query = query.strip()
                
                # Убираем @ из начала запроса, если есть (для поиска по username)
                query_clean = query.lstrip("@")
                
                # Пробуем преобразовать в число для поиска по tg_id
                query_int = None
                try:
                    query_int = int(query_clean)
                except ValueError:
                    pass
                
                # Создаем условия поиска (объединяются через OR)
                search_conditions = []
                
                # Поиск по Telegram ID (точное совпадение)
                if query_int:
                    search_conditions.append(User.tg_id == query_int)
                
                # Поиск по username (может быть NULL)
                # Username в базе хранится БЕЗ @, поэтому всегда ищем по очищенному запросу
                search_conditions.append(
                    and_(
                        User.username.isnot(None),
                        User.username.ilike(f"%{query_clean}%")
                    )
                )
                
                # Поиск по first_name (всегда не NULL)
                search_conditions.append(User.first_name.ilike(f"%{query_clean}%"))
                
                # Объединяем все условия поиска через OR и добавляем в общий список
                if search_conditions:
                    all_conditions.append(or_(*search_conditions))
            
            # Фильтр по статусу подписки
            if subscription_status == "active":
                all_conditions.append(User.server_id.isnot(None))
            elif subscription_status == "expired":
                all_conditions.append(User.server_id.is_(None))
            elif subscription_status == "none":
                all_conditions.append(User.server_id.is_(None))
            
            # Фильтр по дате регистрации
            if date_filter:
                now = datetime.now(timezone.utc)
                if date_filter == "today":
                    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                    all_conditions.append(User.created_at >= today_start)
                elif date_filter == "week":
                    week_start = now - timedelta(days=7)
                    all_conditions.append(User.created_at >= week_start)
                elif date_filter == "month":
                    month_start = now - timedelta(days=30)
                    all_conditions.append(User.created_at >= month_start)
            
            # Фильтр по серверу
            if server_id:
                all_conditions.append(User.server_id == server_id)
            
            # Применяем условия (объединяем через AND)
            if all_conditions:
                base_query = base_query.where(and_(*all_conditions))
            
            # Подсчет общего количества
            count_query = select(func.count(User.id))
            if all_conditions:
                count_query = count_query.where(and_(*all_conditions))
            total_result = await self.session.execute(count_query)
            total = total_result.scalar() or 0
            
            # Пагинация
            offset = page * limit
            base_query = base_query.order_by(User.created_at.desc()).offset(offset).limit(limit)
            
            # Выполняем запрос
            result = await self.session.execute(base_query)
            users = result.scalars().all()
            
            logger.info(f"Search completed: found {len(users)} users (page {page}, total {total}) for query '{query}'")
            return users, total
            
        except Exception as e:
            logger.error(f"Error searching users: {e}", exc_info=True)
            return [], 0

    async def get_user_details(self, tg_id: int) -> User | None:
        """
        Получить детальную информацию о пользователе.
        
        Args:
            tg_id: Telegram ID пользователя
        
        Returns:
            Пользователь с загруженными связями или None
        """
        try:
            query = select(User).options(
                selectinload(User.server),
                selectinload(User.transactions),
                selectinload(User.activated_promocodes),
                selectinload(User.referrals_sent),
                selectinload(User.referral),
            ).where(User.tg_id == tg_id)
            
            result = await self.session.execute(query)
            user = result.scalar_one_or_none()
            
            if user:
                logger.debug(f"User details retrieved for {tg_id}")
            else:
                logger.debug(f"User {tg_id} not found")
            
            return user
            
        except Exception as e:
            logger.error(f"Error getting user details for {tg_id}: {e}", exc_info=True)
            return None

    async def extend_subscription(
        self,
        tg_id: int,
        days: int,
        services: "ServicesContainer",
    ) -> bool:
        """
        Продлить подписку пользователя.
        
        Args:
            tg_id: Telegram ID пользователя
            days: Количество дней для продления
            services: Контейнер сервисов
        
        Returns:
            True если успешно, False в противном случае
        """
        try:
            user = await self.get_user_details(tg_id)
            if not user:
                logger.error(f"User {tg_id} not found for subscription extension")
                return False
            
            if not user.server_id:
                logger.error(f"User {tg_id} has no active subscription to extend")
                return False
            
            # Используем VPNService для продления подписки
            # Получаем текущие данные клиента для определения количества устройств
            client_data = await services.vpn.get_client_data(user)
            if not client_data:
                logger.error(f"Could not get client data for user {tg_id}")
                return False
            
            # Продлеваем подписку с сохранением текущего количества устройств
            # extend_subscription принимает duration в днях (не в timestamp)
            success = await services.vpn.extend_subscription(
                user=user,
                devices=client_data._max_devices if client_data._max_devices > 0 else 1,
                duration=days,  # Передаем дни, а не timestamp
            )
            
            if success:
                logger.info(f"Subscription extended for user {tg_id} by {days} days")
            else:
                logger.error(f"Failed to extend subscription for user {tg_id}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error extending subscription for user {tg_id}: {e}", exc_info=True)
            return False

    async def add_bonus_days(
        self,
        tg_id: int,
        days: int,
        services: "ServicesContainer",
    ) -> bool:
        """
        Добавить бонусные дни пользователю.
        
        Args:
            tg_id: Telegram ID пользователя
            days: Количество бонусных дней
            services: Контейнер сервисов
        
        Returns:
            True если успешно, False в противном случае
        """
        # Валидация входных данных
        if not isinstance(tg_id, int) or tg_id <= 0:
            logger.error(f"Invalid tg_id: {tg_id} (must be positive integer)")
            return False
        if not isinstance(days, int) or days < 1 or days > 365:
            logger.error(f"Invalid days value: {days} (must be between 1 and 365)")
            return False
        
        try:
            user = await self.get_user_details(tg_id)
            if not user:
                logger.error(f"User {tg_id} not found for adding bonus days")
                return False
            
            if not user.server_id:
                logger.error(f"User {tg_id} has no active subscription to add bonus days")
                return False
            
            # Используем VPNService для добавления бонусных дней
            # Получаем текущие данные клиента для определения количества устройств
            client_data = await services.vpn.get_client_data(user)
            devices = 1
            if client_data:
                devices = client_data._max_devices if client_data._max_devices > 0 else 1
            
            # Добавляем бонусные дни
            # process_bonus_days принимает duration в днях (не в timestamp)
            success = await services.vpn.process_bonus_days(
                user=user,
                duration=days,  # Передаем дни, а не timestamp
                devices=devices,
            )
            
            if success:
                logger.info(f"Added {days} bonus days for user {tg_id}")
            else:
                logger.error(f"Failed to add bonus days for user {tg_id}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error adding bonus days for user {tg_id}: {e}", exc_info=True)
            return False

    async def remove_days(
        self,
        tg_id: int,
        days: int,
        services: "ServicesContainer",
    ) -> bool:
        """
        Вычесть дни из подписки пользователя.
        
        Args:
            tg_id: Telegram ID пользователя
            days: Количество дней для вычета
            services: Контейнер сервисов
        
        Returns:
            True если успешно, False в противном случае
        """
        # Валидация входных данных
        if not isinstance(tg_id, int) or tg_id <= 0:
            logger.error(f"Invalid tg_id: {tg_id} (must be positive integer)")
            return False
        if not isinstance(days, int) or days < 1 or days > 365:
            logger.error(f"Invalid days value: {days} (must be between 1 and 365)")
            return False
        
        try:
            user = await self.get_user_details(tg_id)
            if not user:
                logger.error(f"User {tg_id} not found for removing days")
                return False
            
            if not user.server_id:
                logger.error(f"User {tg_id} has no active subscription to remove days from")
                return False
            
            # Используем VPNService для вычета дней
            # Получаем текущие данные клиента для определения количества устройств
            client_data = await services.vpn.get_client_data(user)
            if not client_data:
                logger.error(f"Could not get client data for user {tg_id}")
                return False
            
            # Проверяем, что после вычета дней подписка не истечет в прошлом
            from app.bot.utils.time import get_current_timestamp, add_days_to_timestamp
            current_time = get_current_timestamp()
            
            # Получаем текущее время истечения подписки
            # Используем _expiry_time (число), а не expiry_time (строка - отформатированное время)
            if hasattr(client_data, '_expiry_time') and isinstance(client_data._expiry_time, (int, float)) and client_data._expiry_time > 0:
                current_expiry = int(client_data._expiry_time)
            else:
                # Если expiry_time не доступен, используем текущее время
                current_expiry = current_time
                logger.warning(f"Expiry time not available for user {tg_id}, using current time")
            
            # Вычисляем новое время истечения
            new_expiry_time = add_days_to_timestamp(timestamp=current_expiry, days=-days)
            
            # Проверяем, что подписка не истечет в прошлом
            if new_expiry_time < current_time:
                logger.error(
                    f"Cannot remove {days} days from user {tg_id}: "
                    f"would expire in the past (new_expiry={new_expiry_time}, current_time={current_time})"
                )
                return False
            
            # Вычитаем дни из подписки с сохранением текущего количества устройств
            # extend_subscription принимает duration в днях, передаем отрицательное значение
            success = await services.vpn.extend_subscription(
                user=user,
                devices=client_data._max_devices if client_data._max_devices > 0 else 1,
                duration=-days,  # Отрицательное значение для вычитания дней
            )
            
            if success:
                logger.info(f"Removed {days} days from subscription for user {tg_id}")
            else:
                logger.error(f"Failed to remove days from subscription for user {tg_id}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error removing days for user {tg_id}: {e}", exc_info=True)
            return False

    async def change_user_server(
        self,
        tg_id: int,
        new_server_id: int,
        services: "ServicesContainer",
    ) -> bool:
        """
        Изменить сервер пользователя с полным откатом при неудаче.
        
        Args:
            tg_id: Telegram ID пользователя
            new_server_id: ID нового сервера
            services: Контейнер сервисов
        
        Returns:
            True если успешно, False в противном случае
        """
        old_client_backup = None
        old_server_id = None
        old_connection = None
        new_connection = None
        
        try:
            logger.info(f"[MIGRATION] Starting migration for user {tg_id} to server {new_server_id}")
            user = await self.get_user_details(tg_id)
            if not user:
                logger.error(f"[MIGRATION] User {tg_id} not found for server change")
                return False
            
            logger.info(f"[MIGRATION] User found: id={user.id}, current server_id={user.server_id}")
            
            if not user.server_id:
                logger.error(f"[MIGRATION] User {tg_id} has no active subscription to change server")
                return False
            
            # Синхронизируем статусы серверов перед миграцией
            await services.server_pool.sync_servers()
            
            # Получаем текущие данные клиента для бэкапа
            # Если клиента нет на старом сервере (удален вручную), это нормально - просто создадим на новом
            client_data = None
            try:
                client_data = await services.vpn.get_client_data(user)
            except Exception as e:
                logger.warning(f"Could not get client data for user {tg_id} (client may not exist on old server): {e}")
                # Продолжаем миграцию - клиент может быть уже удален вручную
            
            # Вычисляем оставшуюся длительность в днях
            # Используем _expiry_time (int timestamp), а не expiry_time (форматированная строка)
            current_time = get_current_timestamp()
            remaining_duration_days = 0
            remaining_timestamp = 0
            
            if client_data:
                expiry_timestamp = client_data._expiry_time
                if expiry_timestamp > 0 and expiry_timestamp != -1:
                    remaining_timestamp = max(0, expiry_timestamp - current_time)
                    remaining_duration_days = max(0, int(remaining_timestamp / (1000 * 60 * 60 * 24)))
            else:
                # Если клиента нет, используем дефолтные значения
                # Можно использовать данные из БД или задать дефолт
                logger.info(f"Client data not available for user {tg_id}, using default values for migration")
                remaining_duration_days = 30  # Дефолт: 30 дней
            
            # Валидация входных данных
            if new_server_id is None:
                logger.error(f"Invalid server_id: None")
                return False
            if not isinstance(new_server_id, int):
                logger.error(f"Invalid server_id type: {type(new_server_id)}, expected int")
                return False
            if new_server_id <= 0:
                logger.error(f"Invalid server_id: {new_server_id} (must be positive)")
                return False
            
            # Сохраняем старый server_id ПЕРЕД проверками и обновлениями
            # ВАЖНО: Сохраняем ДО любых изменений user.server_id
            # Также загружаем user из БД заново, чтобы убедиться, что server_id актуален
            # НЕ используем begin(), так как сессия уже активна
            user_from_db = await self.session.get(User, tg_id)
            if user_from_db:
                old_server_id = user_from_db.server_id
                # Обновляем user объект из БД
                user = user_from_db
            else:
                old_server_id = user.server_id if hasattr(user, 'server_id') else None
            
            
            logger.info(f"Migration: user {tg_id}, old_server_id={old_server_id}, new_server_id={new_server_id}")
            
            # Получаем новый сервер с загрузкой users для вычисления current_clients
            from sqlalchemy.orm import selectinload
            from sqlalchemy import select
            server_query = await self.session.execute(
                select(Server).options(selectinload(Server.users)).where(Server.id == new_server_id)
            )
            new_server = server_query.scalar_one_or_none()
            if not new_server:
                
                logger.error(f"Server {new_server_id} not found")
                return False
            
            # Проверяем, что новый сервер не совпадает со старым
            # Но если клиента нет на старом сервере (удален вручную), разрешаем "миграцию" на тот же сервер
            # для создания клиента заново
            if new_server_id == old_server_id:
                if client_data is None:
                    # Клиента нет на старом сервере, разрешаем создание на том же сервере
                    
                    logger.info(f"New server {new_server_id} is the same as current server, but client doesn't exist - will recreate client")
                else:
                    # Клиент существует, не разрешаем миграцию на тот же сервер
                    
                    logger.error(f"New server {new_server_id} is the same as current server")
                    return False
            
            if not new_server.online:
                
                logger.error(f"New server {new_server_id} is offline")
                return False
            
            # Проверяем доступность слотов на новом сервере (синхронизируем статус перед проверкой)
            await services.server_pool.sync_servers()
            # Перезагружаем сервер после синхронизации с загрузкой users для вычисления current_clients
            from sqlalchemy.orm import selectinload
            from sqlalchemy import select
            from app.db.models import UserClient
            
            server_query = await self.session.execute(
                select(Server).options(selectinload(Server.users)).where(Server.id == new_server_id)
            )
            new_server = server_query.scalar_one_or_none()
            
            # Получаем количество клиентов пользователя
            user_clients_count = await UserClient.count_by_user_id(session=self.session, user_id=user.id)
            # Если нет клиентов в новой таблице, считаем что 1 клиент (старая схема)
            if user_clients_count == 0:
                user_clients_count = 1
            
            # Проверяем, есть ли место для ВСЕХ клиентов пользователя
            if new_server and (new_server.current_clients + user_clients_count) > new_server.max_clients:
                
                logger.error(
                    f"New server {new_server_id} has no space for all user clients "
                    f"({new_server.current_clients}/{new_server.max_clients}, need {user_clients_count} more)"
                )
                return False
            
            # Получаем старый сервер для отката с загрузкой users
            old_server_query = await self.session.execute(
                select(Server).options(selectinload(Server.users)).where(Server.id == old_server_id)
            )
            old_server = old_server_query.scalar_one_or_none()
            
            if not old_server:
                logger.error(f"Old server {old_server_id} not found")
                return False
            
            
            
            # Бэкап старого клиента для возможного восстановления
            # ВАЖНО: Используем реальный old_server_id, а не user.server_id (который может быть уже изменен)
            # Убеждаемся, что старый сервер добавлен в пул перед получением connection
            if old_server_id not in services.server_pool._servers:
                logger.info(f"Old server {old_server_id} not in pool, adding it before deletion")
                await services.server_pool._add_server(old_server)
            
            temp_user = User()
            temp_user.tg_id = user.tg_id
            temp_user.server_id = old_server_id  # Используем сохраненный old_server_id
            temp_user.server = old_server
            
            old_connection = await services.server_pool.get_connection(temp_user)
            
            
            
            if old_connection:
                try:
                    # Пробуем получить клиента по email
                    old_client = None
                    try:
                        old_client = await old_connection.api.client.get_by_email(str(user.tg_id))
                    except Exception as get_email_error:
                        # Если get_by_email не работает, пробуем найти клиента в списке всех клиентов инбаунда
                        logger.warning(f"get_by_email failed for user {user.tg_id}, trying to find in inbound list: {get_email_error}")
                        
                        # Получаем список всех инбаундов и ищем клиента в них
                        try:
                            inbound_id = await services.server_pool.get_inbound_id(old_connection.api)
                            if inbound_id:
                                inbounds = await old_connection.api.inbound.get_list()
                                if inbounds:
                                    inbound = inbounds[0]  # Берем первый инбаунд
                                    if inbound.settings and inbound.settings.clients:
                                        # Ищем клиента по email в списке клиентов инбаунда
                                        for client in inbound.settings.clients:
                                            if client.email == str(user.tg_id):
                                                old_client = client
                                                logger.info(f"Found old client {user.tg_id} in inbound list on server {old_server_id}, id={getattr(old_client, 'id', 'N/A')}, type={type(old_client).__name__}")
                                                break
                        except Exception as inbound_error:
                            logger.warning(f"Could not search in inbound list: {inbound_error}")
                    
                    
                    
                    if old_client:
                        # ВАЖНО: get_by_email возвращает объект с числовым ID, а не UUID
                        # Нужно получить UUID из inbound list, где клиент хранится с правильным UUID
                        client_uuid = None
                        numeric_client_id = None
                        
                        # Получаем UUID и числовой ID из inbound list
                        try:
                            inbound_id_for_id = await services.server_pool.get_inbound_id(old_connection.api)
                            if inbound_id_for_id:
                                inbounds_for_id = await old_connection.api.inbound.get_list()
                                if inbounds_for_id:
                                    inbound_for_id = inbounds_for_id[0]
                                    if inbound_for_id.settings and inbound_for_id.settings.clients:
                                        for idx, client_in_inbound in enumerate(inbound_for_id.settings.clients):
                                            if client_in_inbound.email == str(user.tg_id):
                                                # UUID - это строка длиной > 10 символов (формат UUID)
                                                if hasattr(client_in_inbound, 'id'):
                                                    client_id_value = client_in_inbound.id
                                                    if isinstance(client_id_value, str) and len(client_id_value) > 10:
                                                        # Это UUID
                                                        client_uuid = client_id_value
                                                    elif isinstance(client_id_value, int):
                                                        numeric_client_id = client_id_value
                                                    elif isinstance(client_id_value, str) and client_id_value.isdigit():
                                                        numeric_client_id = int(client_id_value)
                                                
                                                # Если UUID не найден через id, пробуем другие поля
                                                if client_uuid is None:
                                                    # Пробуем найти UUID в других атрибутах
                                                    for attr_name in ['uuid', 'client_uuid', '_id']:
                                                        if hasattr(client_in_inbound, attr_name):
                                                            attr_value = getattr(client_in_inbound, attr_name)
                                                            if isinstance(attr_value, str) and len(attr_value) > 10:
                                                                client_uuid = attr_value
                                                                break
                                                
                                                # Если все еще не нашли UUID, используем числовой ID
                                                if numeric_client_id is None and hasattr(client_in_inbound, 'client_id'):
                                                    client_id_value = client_in_inbound.client_id
                                                    if isinstance(client_id_value, int):
                                                        numeric_client_id = client_id_value
                                                    elif isinstance(client_id_value, str) and client_id_value.isdigit():
                                                        numeric_client_id = int(client_id_value)
                                                
                                                # Если все еще не нашли, используем индекс + 1
                                                if numeric_client_id is None:
                                                    numeric_client_id = idx + 1
                                                
                                                
                                                logger.info(f"Extracted IDs for user {user.tg_id}: uuid={client_uuid}, numeric_id={numeric_client_id} (index={idx})")
                                                break
                        except Exception as id_extract_error:
                            logger.warning(f"Could not extract IDs from inbound: {id_extract_error}")
                        
                        # Если UUID не найден в inbound, пробуем использовать id из get_by_email (может быть числом)
                        if client_uuid is None and hasattr(old_client, 'id'):
                            old_client_id = old_client.id
                            if isinstance(old_client_id, str) and len(old_client_id) > 10:
                                client_uuid = old_client_id
                            elif isinstance(old_client_id, int):
                                numeric_client_id = old_client_id if numeric_client_id is None else numeric_client_id
                        
                        # Сохраняем данные старого клиента для отката
                        # Используем UUID как основной идентификатор для удаления, если найден
                        old_client_backup = {
                            "id": client_uuid if client_uuid else numeric_client_id,  # UUID предпочтительнее, но используем числовой ID если UUID нет
                            "uuid": client_uuid,  # UUID отдельно для справки
                            "numeric_id": numeric_client_id,  # Числовой ID как резервный вариант
                            "email": old_client.email,
                            "expiry_time": getattr(old_client, 'expiry_time', 0) if hasattr(old_client, 'expiry_time') else 0,
                            "limit_ip": client_data._max_devices if client_data and client_data._max_devices > 0 else (getattr(old_client, 'limit_ip', 1) if hasattr(old_client, 'limit_ip') else 1),
                            "total_gb": getattr(old_client, 'total', 0) if hasattr(old_client, 'total') else 0,
                        }
                        
                        logger.info(f"Created backup for user {user.tg_id}: uuid={client_uuid}, numeric_id={numeric_client_id}, backup_id={old_client_backup['id']}")
                    else:
                        logger.warning(f"Old client not found for user {user.tg_id} on server {old_server_id}")
                except Exception as e:
                    logger.warning(f"Could not backup old client data: {e}", exc_info=True)
            else:
                
                logger.warning(f"Could not get connection to old server {old_server_id} for user {user.tg_id}")
            
            # ШАГ 1: Создаем клиентов на новом сервере ПЕРЕД удалением старых
            # Проверяем, есть ли у пользователя несколько клиентов (новая схема)
            user_clients_to_migrate = await UserClient.get_by_user_id(session=self.session, user_id=user.id)
            
            logger.info(f"[MIGRATION] Starting migration for user {tg_id}")
            logger.info(f"[MIGRATION] Old server: {old_server_id}, New server: {new_server_id}")
            logger.info(f"[MIGRATION] User clients to migrate: {len(user_clients_to_migrate) if user_clients_to_migrate else 1}")
            if user_clients_to_migrate:
                logger.info(f"[MIGRATION] Client emails: {[uc.client_email for uc in user_clients_to_migrate]}")
            logger.info(f"[MIGRATION] Remaining duration: {remaining_duration_days} days")
            
            
            
            creation_success = False
            if remaining_duration_days > 0:
                if user_clients_to_migrate:
                    # Новая схема: несколько клиентов - создаем все на новом сервере
                    devices = len(user_clients_to_migrate)
                    logger.info(f"[MIGRATION] Creating {devices} clients on new server {new_server_id} (new schema)")
                    creation_success = await services.vpn.create_multiple_clients(
                        user=user,
                        devices=devices,
                        duration=remaining_duration_days,
                        server_id=new_server_id,
                    )
                    logger.info(f"[MIGRATION] create_multiple_clients result: {creation_success}")
                else:
                    # Старая схема: один клиент
                    # Используем _max_devices (int), а не max_devices (может быть строкой "∞")
                    if client_data:
                        max_devices_int = client_data._max_devices
                        devices = max_devices_int if max_devices_int > 0 else 1
                    else:
                        # Если клиента нет, используем дефолт: 1 устройство
                        devices = 1
                    logger.info(f"[MIGRATION] Creating 1 client on new server {new_server_id} (old schema, devices={devices})")
                    creation_success = await services.vpn.create_client(
                        user=user,
                        devices=devices,
                        duration=remaining_duration_days,
                        server_id=new_server_id,
                    )
                    logger.info(f"[MIGRATION] create_client result: {creation_success}")
            else:
                # Если подписка истекла, просто обновляем server_id
                logger.info(f"[MIGRATION] Subscription expired, skipping client creation, only updating server_id")
                creation_success = True
            
            
            
            if not creation_success:
                logger.error(f"[MIGRATION] Failed to create clients on new server {new_server_id} for user {tg_id}")
                logger.error(f"[MIGRATION] Rolling back transaction - old clients not deleted yet")
                # Откат не требуется, так как старые клиенты еще не удалены
                await self.session.rollback()
                return False
            
            logger.info(f"[MIGRATION] Successfully created clients on new server {new_server_id}")
            
            # Обновляем server_id для всех клиентов пользователя в UserClient таблице
            if user_clients_to_migrate:
                logger.info(f"[MIGRATION] Updating server_id for {len(user_clients_to_migrate)} UserClient records")
                await UserClient.update_server_id(session=self.session, user_id=user.id, new_server_id=new_server_id)
                logger.info(f"[MIGRATION] Updated UserClient records with new server_id")
            
            # ВАЖНО: Сохраняем old_server_id ПЕРЕД обновлением, если он еще не сохранен
            if not old_server_id:
                old_server_id = user.server_id
                logger.info(f"Recovered old_server_id {old_server_id} before DB update for user {tg_id}")
            
            # Обновляем server_id в памяти (но НЕ коммитим пока)
            user.server_id = new_server_id
            
            
            
            # ШАГ 2: Удаляем всех клиентов со старого сервера только после успешного создания на новом
            # ВСЕГДА пытаемся удалить всех клиентов, даже если backup не создан
            logger.info(f"[MIGRATION] Starting deletion of clients from old server {old_server_id} for user {tg_id}")
            logger.info(f"[MIGRATION] Clients to delete: {len(user_clients_to_migrate) if user_clients_to_migrate else 1}")
            
            deletion_successful = False
            
            # Если у пользователя несколько клиентов, удаляем все используя улучшенный метод
            if user_clients_to_migrate and old_connection:
                logger.info(f"[MIGRATION] Deleting {len(user_clients_to_migrate)} clients from old server {old_server_id} using improved deletion method")
                inbound_id = await services.server_pool.get_inbound_id(old_connection.api)
                if inbound_id:
                    # Используем улучшенный метод удаления с retry логикой и fallback через inbound.update
                    deletion_successful = await self._delete_user_clients_from_server(
                        connection=old_connection,
                        user_clients=user_clients_to_migrate,
                        inbound_id=inbound_id,
                        max_retries=3,
                    )
                    if deletion_successful:
                        logger.info(f"[MIGRATION] Successfully deleted all {len(user_clients_to_migrate)} clients from old server {old_server_id}")
                    else:
                        logger.error(f"[MIGRATION] Failed to delete clients from old server {old_server_id} after retries")
                else:
                    logger.error(f"[MIGRATION] Failed to get inbound ID for old server {old_server_id}")
            
            # ВАЖНО: Убеждаемся, что old_server_id установлен правильно
            # Если old_server_id не был сохранен ранее, используем текущий server_id пользователя ДО обновления
            if not old_server_id and hasattr(user, 'server_id') and user.server_id:
                # Получаем server_id из БД напрямую, так как user уже обновлен
                # НЕ используем begin(), так как сессия уже активна
                user_from_db = await self.session.get(User, tg_id)
                if user_from_db and user_from_db.server_id and user_from_db.server_id != new_server_id:
                    old_server_id = user_from_db.server_id
                    
                    logger.info(f"Recovered old_server_id {old_server_id} from DB for user {tg_id}")
            
            # Если old_connection не был получен ранее, но old_server_id известен, получаем connection заново
            if not old_connection and old_server_id:
                try:
                    temp_user_for_connection = User()
                    temp_user_for_connection.tg_id = user.tg_id
                    temp_user_for_connection.server_id = old_server_id
                    old_server_for_connection = await self.session.get(Server, old_server_id)
                    if old_server_for_connection:
                        temp_user_for_connection.server = old_server_for_connection
                        old_connection = await services.server_pool.get_connection(temp_user_for_connection)
                        
                        logger.info(f"Re-obtained connection to old server {old_server_id} for deletion")
                except Exception as conn_error:
                    logger.warning(f"Could not re-obtain connection to old server {old_server_id}: {conn_error}")
            
            # Способ 1: Удаление через backup (если создан) - только если не удалось удалить все клиенты выше
            if not deletion_successful and old_connection and old_client_backup:
                try:
                    inbound_id = await services.server_pool.get_inbound_id(old_connection.api)
                    
                    if inbound_id:
                        
                        
                        # Правильный синтаксис: delete(inbound_id, client_ids)
                        
                        
                        # ВАЖНО: Пробуем удалить клиента по email (Telegram ID) напрямую
                        # Это работает для некоторых протоколов и может быть более надежным способом
                        # Если не сработает, используем UUID или числовой ID из backup
                        client_id_to_delete = None
                        deletion_method = None
                        
                        # Способ 1: Пробуем удалить по email (Telegram ID) напрямую
                        deletion_successful_by_email = False
                        try:
                            logger.info(f"Attempting to delete client {user.tg_id} by email (Telegram ID)")
                            await old_connection.api.client.delete(
                                inbound_id,
                                [str(user.tg_id)]  # Используем email (Telegram ID) напрямую
                            )
                            
                            # ВАЖНО: Проверяем, что клиент действительно удален
                            import asyncio
                            await asyncio.sleep(1.0)  # Ждем обработку API
                            
                            # Проверяем через inbound list
                            inbounds_check_email = await old_connection.api.inbound.get_list()
                            if inbounds_check_email:
                                inbound_check_email = inbounds_check_email[0]
                                if inbound_check_email.settings and inbound_check_email.settings.clients:
                                    client_still_exists = any(c.email == str(user.tg_id) for c in inbound_check_email.settings.clients)
                                    if not client_still_exists:
                                        deletion_successful_by_email = True
                                        logger.info(f"Successfully deleted client {user.tg_id} by email (Telegram ID) - verified")
                                    else:
                                        logger.warning(f"Client {user.tg_id} still exists after deletion by email, will try with ID")
                                else:
                                    deletion_successful_by_email = True
                                    logger.info(f"Successfully deleted client {user.tg_id} by email (Telegram ID) - no clients in inbound")
                            else:
                                deletion_successful_by_email = True
                                logger.info(f"Successfully deleted client {user.tg_id} by email (Telegram ID) - no inbounds")
                        except Exception as email_delete_error:
                            logger.warning(f"Deletion by email failed: {email_delete_error}, trying with ID")
                        
                        # Если удаление по email не сработало или клиент все еще существует, используем ID из backup
                        if not deletion_successful_by_email:
                            client_id_to_delete = None
                            deletion_method = None
                            
                            # Способ 2: Используем UUID из get_by_email (как в delete_subscription)
                            if old_client_backup.get("uuid"):
                                client_id_to_delete = old_client_backup["uuid"]
                                deletion_method = "uuid"
                                logger.info(f"Using UUID from backup: {client_id_to_delete}")
                            # Способ 3: Используем числовой ID из backup
                            elif old_client_backup.get("numeric_id"):
                                client_id_to_delete = old_client_backup["numeric_id"]
                                deletion_method = "numeric_id"
                                logger.info(f"Using numeric ID from backup: {client_id_to_delete}")
                            # Способ 4: Используем id из backup
                            elif old_client_backup.get("id"):
                                backup_id = old_client_backup["id"]
                                if isinstance(backup_id, str) and len(backup_id) > 10:
                                    client_id_to_delete = backup_id  # UUID
                                    deletion_method = "uuid_from_backup_id"
                                elif isinstance(backup_id, (int, str)):
                                    client_id_to_delete = int(backup_id) if isinstance(backup_id, str) and backup_id.isdigit() else backup_id
                                    deletion_method = "numeric_from_backup_id"
                                logger.info(f"Using ID from backup.id: {client_id_to_delete} (method: {deletion_method})")
                            
                            # Если все еще не нашли, ищем в inbound list
                            if client_id_to_delete is None:
                                logger.warning(f"Client ID not found in backup for user {user.tg_id}, searching in inbound list")
                                inbounds_for_id = await old_connection.api.inbound.get_list()
                                if inbounds_for_id:
                                    inbound_for_id = inbounds_for_id[0]
                                    if inbound_for_id.settings and inbound_for_id.settings.clients:
                                        for idx, client_in_inbound in enumerate(inbound_for_id.settings.clients):
                                            if client_in_inbound.email == str(user.tg_id):
                                                # Пробуем UUID сначала
                                                if hasattr(client_in_inbound, 'id'):
                                                    client_id_value = client_in_inbound.id
                                                    if isinstance(client_id_value, str) and len(client_id_value) > 10:
                                                        client_id_to_delete = client_id_value  # UUID
                                                        deletion_method = "uuid_from_inbound"
                                                    elif isinstance(client_id_value, (int, str)):
                                                        client_id_to_delete = client_id_value  # Числовой ID
                                                        deletion_method = "numeric_from_inbound"
                                                
                                                # Если UUID не найден, пробуем числовой ID
                                                if client_id_to_delete is None and hasattr(client_in_inbound, 'client_id'):
                                                    client_id_value = client_in_inbound.client_id
                                                    if isinstance(client_id_value, int):
                                                        client_id_to_delete = client_id_value
                                                        deletion_method = "numeric_from_client_id"
                                                    elif isinstance(client_id_value, str) and client_id_value.isdigit():
                                                        client_id_to_delete = int(client_id_value)
                                                        deletion_method = "numeric_from_client_id_str"
                                                
                                                # Если все еще не нашли, используем индекс + 1
                                                if client_id_to_delete is None:
                                                    client_id_to_delete = idx + 1
                                                    deletion_method = "index_plus_one"
                                                
                                                logger.info(f"Found ID {client_id_to_delete} for user {user.tg_id} in inbound list (index={idx}, method={deletion_method})")
                                                break
                                
                                if client_id_to_delete is None:
                                    raise ValueError(f"Could not find client ID for user {user.tg_id}")
                            
                            # Пробуем удалить с ID
                            if client_id_to_delete is not None:
                                client_id_to_delete_str = str(client_id_to_delete)
                                
                                
                                logger.info(f"Deleting client {user.tg_id} with ID: {client_id_to_delete_str} (method: {deletion_method})")
                                
                                await old_connection.api.client.delete(
                                    inbound_id,
                                    [client_id_to_delete_str]
                                )
                                
                                # Проверяем удаление после использования ID
                                await asyncio.sleep(1.0)
                                inbounds_check_id = await old_connection.api.inbound.get_list()
                                if inbounds_check_id:
                                    inbound_check_id = inbounds_check_id[0]
                                    if inbound_check_id.settings and inbound_check_id.settings.clients:
                                        client_still_exists_id = any(c.email == str(user.tg_id) for c in inbound_check_id.settings.clients)
                                        if client_still_exists_id:
                                            logger.error(f"WARNING: Client {user.tg_id} still exists after deletion with {deletion_method}!")
                                            # ПОСЛЕДНЯЯ ПОПЫТКА: Удаляем клиента через обновление inbound, удаляя его из списка клиентов
                                            logger.warning(f"Attempting final deletion method: updating inbound to remove client from clients list")
                                            try:
                                                # Получаем текущий inbound
                                                inbounds_final = await old_connection.api.inbound.get_list()
                                                if inbounds_final:
                                                    inbound_final = inbounds_final[0]
                                                    if inbound_final.settings and inbound_final.settings.clients:
                                                        # Сохраняем оригинальную длину списка клиентов
                                                        original_clients_count = len(inbound_final.settings.clients)
                                                        
                                                        # Создаем новый список клиентов без удаляемого клиента
                                                        updated_clients = [c for c in inbound_final.settings.clients if c.email != str(user.tg_id)]
                                                        
                                                        logger.info(f"Client removal attempt: {original_clients_count} clients before, {len(updated_clients)} clients after filtering")
                                                        
                                                        if len(updated_clients) < original_clients_count:
                                                            # Клиент найден в списке, обновляем inbound
                                                            logger.info(f"Found client in inbound list: {original_clients_count} clients before, {len(updated_clients)} clients after removal")
                                                            inbound_final.settings.clients = updated_clients
                                                            
                                                            
                                                            
                                                            # Обновляем inbound через API
                                                            try:
                                                                await old_connection.api.inbound.update(inbound_id, inbound_final)
                                                                logger.info(f"Updated inbound to remove client {user.tg_id} from clients list")
                                                            except Exception as update_error:
                                                                logger.error(f"Failed to update inbound: {update_error}", exc_info=True)
                                                                # Пробуем альтернативный способ - передать только settings
                                                                try:
                                                                    # Возможно, нужно передать только settings, а не весь inbound
                                                                    if hasattr(inbound_final, 'settings'):
                                                                        await old_connection.api.inbound.update(inbound_id, inbound_final.settings)
                                                                        logger.info(f"Updated inbound settings to remove client {user.tg_id}")
                                                                except Exception as update_settings_error:
                                                                    logger.error(f"Failed to update inbound settings: {update_settings_error}", exc_info=True)
                                                            
                                                            # Проверяем еще раз
                                                            await asyncio.sleep(1.0)
                                                            inbounds_final_check = await old_connection.api.inbound.get_list()
                                                            if inbounds_final_check:
                                                                inbound_final_check_obj = inbounds_final_check[0]
                                                                if inbound_final_check_obj.settings and inbound_final_check_obj.settings.clients:
                                                                    client_still_exists_final = any(c.email == str(user.tg_id) for c in inbound_final_check_obj.settings.clients)
                                                                    if not client_still_exists_final:
                                                                        logger.info(f"SUCCESS: Client {user.tg_id} deleted via inbound update - verified")
                                                                    else:
                                                                        logger.error(f"CRITICAL: Client {user.tg_id} STILL EXISTS after inbound update!")
                                                                else:
                                                                    logger.info(f"SUCCESS: Client {user.tg_id} deleted via inbound update - no clients in inbound")
                                                            else:
                                                                logger.warning(f"Could not verify deletion after inbound update")
                                                        else:
                                                            logger.warning(f"Client {user.tg_id} not found in clients list for inbound update")
                                            except Exception as inbound_update_error:
                                                logger.error(f"Failed to delete client via inbound update: {inbound_update_error}", exc_info=True)
                                        else:
                                            logger.info(f"Successfully deleted client {user.tg_id} with {deletion_method} - verified")
                        
                        # Проверяем, что клиент действительно удален (финальная проверка)
                        # Ждем немного, чтобы API успел обработать удаление
                        import asyncio
                        await asyncio.sleep(0.5)
                        
                        # Проверяем через inbound list (более надежный способ)
                        try:
                            inbound_id_check = await services.server_pool.get_inbound_id(old_connection.api)
                            if inbound_id_check:
                                inbounds_check = await old_connection.api.inbound.get_list()
                                if inbounds_check:
                                    inbound_check = inbounds_check[0]
                                    if inbound_check.settings and inbound_check.settings.clients:
                                        client_found_in_inbound = False
                                        client_info_after_deletion = None
                                        for idx_check, client_check in enumerate(inbound_check.settings.clients):
                                            if client_check.email == str(user.tg_id):
                                                client_found_in_inbound = True
                                                # Получаем информацию о клиенте после попытки удаления
                                                client_id_after = None
                                                if hasattr(client_check, 'id'):
                                                    client_id_after = client_check.id
                                                
                                                logger.error(f"WARNING: Client {user.tg_id} found in inbound list on server {old_server_id} ({old_server.name if old_server else 'unknown'}) after deletion! Used ID: {client_id_to_delete_str}, Client ID after: {client_id_after}, Index: {idx_check}")
                                                client_info_after_deletion = {
                                                    "id": client_id_after,
                                                    "index": idx_check,
                                                    "email": client_check.email
                                                }
                                                break
                                        
                                        if not client_found_in_inbound:
                                            # Клиент не найден в inbound list - удаление успешно
                                            deletion_successful = True
                                            
                                            logger.info(f"Deleted client {user.tg_id} from old server {old_server_id} via backup (verified in inbound)")
                                        else:
                                            # Клиент все еще существует - попробуем удалить снова с новым ID
                                            deletion_successful = False
                                            if client_info_after_deletion:
                                                # Пробуем удалить снова с ID, который мы нашли после первой попытки
                                                retry_client_id = None
                                                if client_info_after_deletion.get("id"):
                                                    retry_client_id = client_info_after_deletion["id"]
                                                elif client_info_after_deletion.get("index") is not None:
                                                    retry_client_id = client_info_after_deletion["index"] + 1
                                                
                                                if retry_client_id:
                                                    logger.warning(f"Retrying deletion with ID found after first attempt: {retry_client_id}")
                                                    try:
                                                        await old_connection.api.client.delete(
                                                            inbound_id,
                                                            [str(retry_client_id)]
                                                        )
                                                        await asyncio.sleep(0.5)
                                                        # Проверяем еще раз
                                                        inbounds_retry = await old_connection.api.inbound.get_list()
                                                        if inbounds_retry:
                                                            inbound_retry = inbounds_retry[0]
                                                            if inbound_retry.settings and inbound_retry.settings.clients:
                                                                still_exists = any(c.email == str(user.tg_id) for c in inbound_retry.settings.clients)
                                                                if not still_exists:
                                                                    deletion_successful = True
                                                                    logger.info(f"Client {user.tg_id} deleted successfully on retry with ID {retry_client_id}")
                                                    except Exception as retry_error:
                                                        logger.error(f"Retry deletion failed: {retry_error}")
                                    else:
                                        # Нет клиентов в inbound - удаление успешно
                                        deletion_successful = True
                                        logger.info(f"Deleted client {user.tg_id} from old server {old_server_id} via backup (no clients in inbound)")
                                else:
                                    deletion_successful = True
                                    logger.info(f"Deleted client {user.tg_id} from old server {old_server_id} via backup (no inbounds)")
                            else:
                                deletion_successful = True
                                logger.info(f"Deleted client {user.tg_id} from old server {old_server_id} via backup (inbound_id is None)")
                        except Exception as check_error:
                            
                            logger.warning(f"Deletion check failed for {user.tg_id}, assuming deleted: {check_error}")
                            deletion_successful = True
                    else:
                        
                        logger.warning(f"inbound_id is None for old server {old_server_id}, cannot delete client via backup")
                except Exception as e:
                    logger.error(f"Failed to delete client from old server {old_server_id} via backup: {e}", exc_info=True)
            
            # Способ 2: Прямое удаление из inbound list (если backup не создан или удаление через backup не удалось)
            if old_connection and not deletion_successful:
                try:
                    logger.info(f"Attempting to delete client {user.tg_id} directly from inbound list on server {old_server_id}")
                    
                    inbound_id = await services.server_pool.get_inbound_id(old_connection.api)
                    if inbound_id:
                        inbounds = await old_connection.api.inbound.get_list()
                        if inbounds:
                            inbound = inbounds[0]
                            if inbound.settings and inbound.settings.clients:
                                # Ищем клиента по email и удаляем его
                                client_ids_to_delete = []
                                for idx, client in enumerate(inbound.settings.clients):
                                    if client.email == str(user.tg_id):
                                        # ВАЖНО: X-UI API требует числовой ID для удаления, а не UUID
                                        # Приоритет: числовой ID > индекс + 1
                                        client_id = None
                                        
                                        # Пробуем получить числовой ID
                                        if hasattr(client, 'id'):
                                            client_id_value = client.id
                                            # Если это число - используем его
                                            if isinstance(client_id_value, int):
                                                client_id = client_id_value
                                            # Если это строка с цифрами - преобразуем в число
                                            elif isinstance(client_id_value, str) and client_id_value.isdigit():
                                                client_id = int(client_id_value)
                                        
                                        # Если числовой ID не найден через id, пробуем client_id
                                        if client_id is None and hasattr(client, 'client_id'):
                                            client_id_value = client.client_id
                                            if isinstance(client_id_value, int):
                                                client_id = client_id_value
                                            elif isinstance(client_id_value, str) and client_id_value.isdigit():
                                                client_id = int(client_id_value)
                                        
                                        # Если все еще не нашли, используем индекс + 1 (стандартный способ X-UI)
                                        if client_id is None:
                                            client_id = idx + 1
                                        
                                        client_ids_to_delete.append(str(client_id))  # Преобразуем в строку для API
                                        
                                        logger.info(f"Found numeric client ID {client_id} for deletion (index={idx})")
                                
                                if client_ids_to_delete:
                                    # Правильный синтаксис: delete(inbound_id, client_ids)
                                    await old_connection.api.client.delete(
                                        inbound_id,
                                        client_ids_to_delete
                                    )
                                    
                                    # Проверяем удаление
                                    import asyncio
                                    await asyncio.sleep(0.5)
                                    
                                    # Проверяем через inbound list
                                    try:
                                        inbounds_verify = await old_connection.api.inbound.get_list()
                                        if inbounds_verify:
                                            inbound_verify = inbounds_verify[0]
                                            if inbound_verify.settings and inbound_verify.settings.clients:
                                                client_still_exists = any(
                                                    c.email == str(user.tg_id) for c in inbound_verify.settings.clients
                                                )
                                                if client_still_exists:
                                                    
                                                    logger.error(f"WARNING: Client {user.tg_id} still exists on server {old_server_id} after direct deletion!")
                                                    deletion_successful = False
                                                else:
                                                    
                                                    logger.info(f"Deleted client {user.tg_id} directly from inbound list on server {old_server_id}, ids={client_ids_to_delete} (verified)")
                                                    deletion_successful = True
                                            else:
                                                deletion_successful = True
                                        else:
                                            deletion_successful = True
                                    except Exception as verify_error:
                                        logger.warning(f"Verification after direct deletion failed: {verify_error}, assuming deleted")
                                        deletion_successful = True
                                else:
                                    
                                    logger.warning(f"Could not find client IDs to delete for {user.tg_id} on server {old_server_id}")
                    else:
                        
                        logger.warning(f"inbound_id is None for old server {old_server_id}, cannot delete client directly")
                except Exception as direct_delete_error:
                    
                    logger.error(f"Failed to delete client directly from inbound list on server {old_server_id}: {direct_delete_error}", exc_info=True)
            
            if not old_connection:
                logger.warning(f"No connection to old server {old_server_id}, cannot delete client")
            
            # ФИНАЛЬНАЯ ПРОВЕРКА: Если удаление не произошло, но old_server_id известен, пытаемся удалить напрямую
            # Это критически важно для гарантии удаления старого клиента
            if not deletion_successful and old_server_id and old_server_id != new_server_id:
                try:
                    logger.warning(f"Deletion not successful, attempting final direct deletion for user {user.tg_id} on server {old_server_id}")
                    
                    # Получаем connection к старому серверу заново
                    if not old_connection:
                        # Убеждаемся, что старый сервер в пуле
                        if old_server_id not in services.server_pool._servers:
                            old_server_final = await self.session.get(Server, old_server_id)
                            if old_server_final:
                                
                                await services.server_pool._add_server(old_server_final)
                        
                        temp_user_final = User()
                        temp_user_final.tg_id = user.tg_id
                        temp_user_final.server_id = old_server_id
                        old_server_final = await self.session.get(Server, old_server_id)
                        if old_server_final:
                            temp_user_final.server = old_server_final
                            old_connection = await services.server_pool.get_connection(temp_user_final)
                            
                    
                    if old_connection:
                        inbound_id = await services.server_pool.get_inbound_id(old_connection.api)
                        if inbound_id:
                            inbounds = await old_connection.api.inbound.get_list()
                            if inbounds:
                                inbound = inbounds[0]
                                if inbound.settings and inbound.settings.clients:
                                    # Ищем ВСЕХ клиентов с таким email и удаляем их
                                    client_ids_to_delete = []
                                    for idx, client in enumerate(inbound.settings.clients):
                                        if client.email == str(user.tg_id):
                                            # ВАЖНО: X-UI API требует числовой ID для удаления, а не UUID
                                            # Приоритет: числовой ID > индекс + 1
                                            client_id = None
                                            
                                            # Пробуем получить числовой ID
                                            if hasattr(client, 'id'):
                                                client_id_value = client.id
                                                # Если это число - используем его
                                                if isinstance(client_id_value, int):
                                                    client_id = client_id_value
                                                # Если это строка с цифрами - преобразуем в число
                                                elif isinstance(client_id_value, str) and client_id_value.isdigit():
                                                    client_id = int(client_id_value)
                                            
                                            # Если числовой ID не найден через id, пробуем client_id
                                            if client_id is None and hasattr(client, 'client_id'):
                                                client_id_value = client.client_id
                                                if isinstance(client_id_value, int):
                                                    client_id = client_id_value
                                                elif isinstance(client_id_value, str) and client_id_value.isdigit():
                                                    client_id = int(client_id_value)
                                            
                                            # Если все еще не нашли, используем индекс + 1 (стандартный способ X-UI)
                                            if client_id is None:
                                                client_id = idx + 1
                                            
                                            client_ids_to_delete.append(str(client_id))  # Преобразуем в строку для API
                                            
                                            logger.info(f"Found numeric client ID {client_id} in final attempt (index={idx})")
                                    
                                    if client_ids_to_delete:
                                        # Правильный синтаксис: delete(inbound_id, client_ids)
                                        await old_connection.api.client.delete(
                                            inbound_id,
                                            client_ids_to_delete
                                        )
                                        
                                        logger.info(f"SUCCESS: Deleted client {user.tg_id} in final attempt on server {old_server_id}, ids={client_ids_to_delete}")
                                        deletion_successful = True
                                    else:
                                        
                                        logger.warning(f"Final attempt: Could not find client IDs for {user.tg_id} on server {old_server_id}")
                except Exception as final_error:
                    
                    logger.error(f"Final deletion attempt failed for user {user.tg_id} on server {old_server_id}: {final_error}", exc_info=True)
            
            # ШАГ 3: Обновляем server_id в БД только после успешного создания на новом сервере и удаления со старого
            # ВАЖНО: Commit выполняется только после всех операций для обеспечения атомарности
            logger.info(f"[MIGRATION] Deletion result: {deletion_successful}, old_connection: {old_connection is not None}, old_client_backup: {old_client_backup is not None}")
            
            # Если удаление не удалось, но клиента не было на старом сервере - это нормально, коммитим
            # Если удаление не удалось, но клиент был - НЕ коммитим, откатываем изменения
            if deletion_successful or (not old_connection or not old_client_backup):
                logger.info(f"[MIGRATION] Committing database changes: user {tg_id} server_id changed to {new_server_id}")
                user.server_id = new_server_id
                await self.session.commit()
                logger.info(f"[MIGRATION] Database updated successfully: user {tg_id} server_id changed to {new_server_id}")
            else:
                # Удаление не удалось, но клиент был на старом сервере - откатываем изменения
                logger.error(f"[MIGRATION] Deletion failed for user {tg_id}, rolling back changes")
                logger.error(f"[MIGRATION] Clients were created on new server {new_server_id} but deletion from old server {old_server_id} failed")
                logger.error(f"[MIGRATION] Rolling back: deleting clients from new server and restoring old server_id")
                
                # Пытаемся удалить клиентов с нового сервера
                try:
                    new_connection = await services.server_pool.get_connection(user)
                    if new_connection:
                        logger.info(f"[MIGRATION] Attempting to delete clients from new server {new_server_id} as part of rollback")
                        new_inbound_id = await services.server_pool.get_inbound_id(new_connection.api)
                        if new_inbound_id and user_clients_to_migrate:
                            # Получаем новые UserClient записи, которые были созданы
                            new_user_clients = await UserClient.get_by_user_id(session=self.session, user_id=user.id)
                            if new_user_clients:
                                # Удаляем клиентов с нового сервера
                                rollback_deletion_success = await self._delete_user_clients_from_server(
                                    connection=new_connection,
                                    user_clients=new_user_clients,
                                    inbound_id=new_inbound_id,
                                    max_retries=2,
                                )
                                if rollback_deletion_success:
                                    logger.info(f"[MIGRATION] Successfully deleted clients from new server {new_server_id} during rollback")
                                else:
                                    logger.warning(f"[MIGRATION] Failed to delete clients from new server {new_server_id} during rollback")
                except Exception as rollback_delete_error:
                    logger.error(f"[MIGRATION] Error deleting clients from new server during rollback: {rollback_delete_error}", exc_info=True)
                
                # Откатываем транзакцию
                await self.session.rollback()
                # Восстанавливаем server_id в памяти
                if old_server_id:
                    user.server_id = old_server_id
                logger.error(f"[MIGRATION] Rollback completed: user {tg_id} server_id restored to {old_server_id}")
                return False
            
            # ШАГ 4: Синхронизируем статусы после миграции
            await services.server_pool.sync_servers()
            
            # ШАГ 5: Генерируем новый конфиг для пользователя
            try:
                new_key = await services.vpn.get_key(user)
                if new_key:
                    
                    logger.info(f"New config generated for user {tg_id} on server {new_server_id}")
            except Exception as e:
                logger.warning(f"Could not generate new config (non-critical): {e}")
            
            
            
            logger.info(f"[MIGRATION] Migration completed successfully for user {tg_id}: {old_server_id} -> {new_server_id}")
            
            # Update metrics
            try:
                from app.bot.services.metrics import migrations_total
                migrations_total.labels(status="success").inc()
            except Exception as metrics_error:
                logger.warning(f"Failed to update migration metrics: {metrics_error}")
            
            return True
            
        except Exception as e:
            logger.error(f"Error changing server for user {tg_id}: {e}", exc_info=True)
            
            # ОТКАТ: Восстанавливаем клиента на старом сервере если нужно
            if old_client_backup and old_connection and old_server_id:
                try:
                    
                    from py3xui import Client
                    inbound_id = await services.server_pool.get_inbound_id(old_connection.api)
                    if inbound_id:
                        # Проверяем, существует ли еще клиент на старом сервере
                        existing_client = await old_connection.api.client.get_by_email(str(tg_id))
                        if not existing_client:
                            # Восстанавливаем клиента на старом сервере
                            restore_client = Client(
                                email=old_client_backup["email"],
                                enable=True,
                                id=old_client_backup["id"],
                                expiry_time=old_client_backup["expiry_time"],
                                flow="xtls-rprx-vision",
                                limit_ip=old_client_backup["limit_ip"],
                                sub_id=old_client_backup["id"],
                                total_gb=old_client_backup["total_gb"],
                            )
                            await old_connection.api.client.add(inbound_id=inbound_id, clients=[restore_client])
                            
                            logger.info(f"Rollback: Restored client {tg_id} on old server {old_server_id}")
                except Exception as rollback_error:
                    
                    logger.error(f"Failed to rollback client restoration: {rollback_error}")
            
            # Откат БД
            try:
                await self.session.rollback()
                # Восстанавливаем server_id в памяти
                if old_server_id:
                    user.server_id = old_server_id
            except Exception as rollback_error:
                logger.error(f"Failed to rollback transaction: {rollback_error}")
            
            # Update metrics
            try:
                from app.bot.services.metrics import migrations_total
                migrations_total.labels(status="failed").inc()
            except Exception as metrics_error:
                logger.warning(f"Failed to update migration metrics: {metrics_error}")
            
            return False

    async def delete_subscription(
        self,
        tg_id: int,
        services: "ServicesContainer",
        max_retries: int = 3,
    ) -> bool:
        """
        Удалить подписку пользователя с проверкой успешности и retry логикой.
        
        Args:
            tg_id: Telegram ID пользователя
            services: Контейнер сервисов
            max_retries: Максимальное количество попыток удаления
        
        Returns:
            True если успешно, False в противном случае
        """
        logger.info(f"[DELETE_SUBSCRIPTION] Starting subscription deletion for user {tg_id} (max_retries={max_retries})")
        try:
            user = await self.get_user_details(tg_id)
            if not user:
                logger.error(f"[DELETE_SUBSCRIPTION] User {tg_id} not found for subscription deletion")
                return False
            
            logger.info(f"[DELETE_SUBSCRIPTION] User found: id={user.id}, server_id={user.server_id}")
            
            if not user.server_id:
                logger.warning(f"[DELETE_SUBSCRIPTION] User {tg_id} has no active subscription to delete (server_id is None)")
                return True  # Уже нет подписки
            
            # Проверяем, есть ли у пользователя несколько клиентов (новая схема)
            from app.db.models import UserClient
            from sqlalchemy import select
            
            user_clients_query = await self.session.execute(
                select(UserClient).where(UserClient.user_id == user.id)
            )
            user_clients = user_clients_query.scalars().all()
            logger.info(f"[DELETE_SUBSCRIPTION] Found {len(user_clients)} UserClient records for user {tg_id}")
            
            if user_clients:
                logger.info(f"[DELETE_SUBSCRIPTION] UserClient emails: {[uc.client_email for uc in user_clients]}")
            
            # Удаляем клиентов из VPN сервиса с retry логикой
            logger.info(f"[DELETE_SUBSCRIPTION] Getting connection for server_id={user.server_id}")
            connection = await services.server_pool.get_connection(user)
            if not connection:
                logger.warning(f"[DELETE_SUBSCRIPTION] Could not get connection for user {tg_id}, cannot verify deletion")
                # Продолжаем удаление из БД, так как сервер может быть недоступен
                success = True
            else:
                logger.info(f"[DELETE_SUBSCRIPTION] Connection established to server {connection.server.name} (id={connection.server.id})")
                success = False
                inbound_id = await services.server_pool.get_inbound_id(connection.api)
                if not inbound_id:
                    logger.error(f"[DELETE_SUBSCRIPTION] Failed to get inbound ID for user {tg_id}")
                else:
                    logger.info(f"[DELETE_SUBSCRIPTION] Inbound ID: {inbound_id}")
                    # Retry логика для удаления
                    for attempt in range(1, max_retries + 1):
                        try:
                            if user_clients:
                                # Новая схема: удаляем все клиенты пользователя
                                client_ids_to_delete = []
                                try:
                                    # Получаем список всех клиентов из inbound для поиска ID
                                    inbounds_list = await connection.api.inbound.get_list()
                                    if inbounds_list:
                                        inbound_list = inbounds_list[0]
                                        if inbound_list.settings and inbound_list.settings.clients:
                                            # Ищем ID для каждого клиента пользователя
                                            for user_client in user_clients:
                                                for client_in_inbound in inbound_list.settings.clients:
                                                    if client_in_inbound.email == user_client.client_email:
                                                        client_id = getattr(client_in_inbound, 'id', None)
                                                        if client_id:
                                                            client_ids_to_delete.append(client_id)
                                                            logger.debug(f"[DELETE_SUBSCRIPTION] Found client ID {client_id} for {user_client.client_email}")
                                                        break
                                    
                                    if client_ids_to_delete:
                                        # First try: delete via API
                                        logger.info(f"[DELETE_SUBSCRIPTION] Attempt {attempt}: Found {len(client_ids_to_delete)} client IDs to delete: {client_ids_to_delete}")
                                        logger.info(f"[DELETE_SUBSCRIPTION] Attempt {attempt}: Client emails to delete: {[uc.client_email for uc in user_clients]}")
                                        try:
                                            logger.info(f"[DELETE_SUBSCRIPTION] Attempt {attempt}: Calling client.delete API with inbound_id={inbound_id}, client_ids={client_ids_to_delete}")
                                            await connection.api.client.delete(inbound_id, client_ids_to_delete)
                                            logger.info(f"[DELETE_SUBSCRIPTION] Attempt {attempt}: client.delete API call completed successfully for {len(client_ids_to_delete)} clients from server {user.server_id}")
                                        except Exception as delete_api_error:
                                            logger.error(f"[DELETE_SUBSCRIPTION] Attempt {attempt}: Delete API call failed: {delete_api_error}", exc_info=True)
                                        
                                        # Wait for deletion to be processed
                                        import asyncio
                                        logger.info(f"[DELETE_SUBSCRIPTION] Attempt {attempt}: Waiting 1.5 seconds for X-UI to process deletion")
                                        await asyncio.sleep(1.5)
                                        
                                        # Проверяем успешность удаления
                                        logger.info(f"[DELETE_SUBSCRIPTION] Attempt {attempt}: Verifying deletion after API call")
                                        if await self._verify_client_deletion(connection, user_clients):
                                            success = True
                                            logger.info(f"[DELETE_SUBSCRIPTION] Attempt {attempt}: Verified deletion of {len(user_clients)} clients for user {tg_id} via API delete")
                                            break
                                        else:
                                            logger.warning(f"[DELETE_SUBSCRIPTION] Attempt {attempt}: API delete didn't work (verification failed), trying inbound update method...")
                                            
                                            # Alternative method: update inbound to remove clients
                                            try:
                                                logger.info(f"[DELETE_SUBSCRIPTION] Attempt {attempt}: Starting inbound update method for user {tg_id}")
                                                inbounds_for_update = await connection.api.inbound.get_list()
                                                if not inbounds_for_update:
                                                    logger.warning(f"[DELETE_SUBSCRIPTION] Attempt {attempt}: No inbounds found for update")
                                                else:
                                                    inbound_original = inbounds_for_update[0]
                                                    logger.info(f"[DELETE_SUBSCRIPTION] Attempt {attempt}: Got inbound, checking settings and clients")
                                                    
                                                    if not inbound_original.settings:
                                                        logger.warning(f"[DELETE_SUBSCRIPTION] Attempt {attempt}: Inbound has no settings")
                                                    elif not inbound_original.settings.clients:
                                                        logger.warning(f"[DELETE_SUBSCRIPTION] Attempt {attempt}: Inbound has no clients list")
                                                    else:
                                                        # Filter out user's clients
                                                        emails_to_remove = {uc.client_email for uc in user_clients}
                                                        logger.info(f"[DELETE_SUBSCRIPTION] Attempt {attempt}: Emails to remove: {emails_to_remove}")
                                                        
                                                        original_count = len(inbound_original.settings.clients)
                                                        logger.info(f"[DELETE_SUBSCRIPTION] Attempt {attempt}: Original clients count: {original_count}")
                                                        
                                                        # Create new clients list without user's clients
                                                        filtered_clients = [
                                                            c for c in inbound_original.settings.clients 
                                                            if c.email not in emails_to_remove
                                                        ]
                                                        new_count = len(filtered_clients)
                                                        logger.info(f"[DELETE_SUBSCRIPTION] Attempt {attempt}: Filtered clients count: {new_count}")
                                                        
                                                        if original_count > new_count:
                                                            removed_count = original_count - new_count
                                                            logger.info(f"[DELETE_SUBSCRIPTION] Attempt {attempt}: Removing {removed_count} clients via inbound update (from {original_count} to {new_count})")
                                                            
                                                            # Create a copy of the inbound with updated clients list
                                                            # We need to preserve all other fields of the inbound
                                                            inbound_to_update = inbound_original
                                                            inbound_to_update.settings.clients = filtered_clients
                                                            
                                                            logger.info(f"[DELETE_SUBSCRIPTION] Attempt {attempt}: Calling inbound.update with inbound_id={inbound_id}")
                                                            await connection.api.inbound.update(inbound_id, inbound_to_update)
                                                            logger.info(f"[DELETE_SUBSCRIPTION] Attempt {attempt}: inbound.update call completed")
                                                            
                                                            # Wait for X-UI to process the update
                                                            await asyncio.sleep(2.0)
                                                            logger.info(f"[DELETE_SUBSCRIPTION] Attempt {attempt}: Waiting completed, verifying deletion")
                                                            
                                                            # Verify deletion
                                                            if await self._verify_client_deletion(connection, user_clients):
                                                                success = True
                                                                logger.info(f"[DELETE_SUBSCRIPTION] Attempt {attempt}: Verified deletion via inbound update for user {tg_id}")
                                                                break
                                                            else:
                                                                logger.warning(f"[DELETE_SUBSCRIPTION] Attempt {attempt}: Verification failed, clients still exist")
                                                        else:
                                                            logger.warning(f"[DELETE_SUBSCRIPTION] Attempt {attempt}: No clients found to remove from inbound (original={original_count}, new={new_count})")
                                                            # Check if clients are already removed
                                                            if await self._verify_client_deletion(connection, user_clients):
                                                                logger.info(f"[DELETE_SUBSCRIPTION] Attempt {attempt}: Clients already deleted, verification passed")
                                                                success = True
                                                                break
                                            except Exception as update_error:
                                                logger.error(f"[DELETE_SUBSCRIPTION] Attempt {attempt}: Inbound update method failed: {update_error}", exc_info=True)
                                    else:
                                        # Клиентов уже нет в X-UI, проверяем что их действительно нет
                                        if await self._verify_client_deletion(connection, user_clients):
                                            logger.info(f"[DELETE_SUBSCRIPTION] Clients already deleted from X-UI for user {tg_id}")
                                            success = True
                                            break
                                        else:
                                            logger.warning(f"[DELETE_SUBSCRIPTION] Attempt {attempt}: Clients still exist, retrying...")
                                except Exception as e:
                                    logger.error(f"[DELETE_SUBSCRIPTION] Attempt {attempt}: Failed to delete multiple clients for user {tg_id}: {e}", exc_info=True)
                                    if attempt == max_retries:
                                        success = False
                            else:
                                # Старая схема: удаляем один клиент
                                try:
                                    client = await connection.api.client.get_by_email(str(user.tg_id))
                                    if client:
                                        await connection.api.client.delete(inbound_id, [client.id])
                                        logger.info(f"[DELETE_SUBSCRIPTION] Attempt {attempt}: Deleted client {user.tg_id} from server {user.server_id}")
                                        
                                        # Проверяем успешность удаления
                                        if await self._verify_client_deletion(connection, None, user.tg_id):
                                            success = True
                                            logger.info(f"[DELETE_SUBSCRIPTION] Verified deletion of client {user.tg_id}")
                                            break
                                        else:
                                            logger.warning(f"[DELETE_SUBSCRIPTION] Attempt {attempt}: Deletion not verified, retrying...")
                                    else:
                                        # Клиента уже нет, проверяем
                                        if await self._verify_client_deletion(connection, None, user.tg_id):
                                            logger.info(f"[DELETE_SUBSCRIPTION] Client {user.tg_id} already deleted from X-UI")
                                            success = True
                                            break
                                        else:
                                            logger.warning(f"[DELETE_SUBSCRIPTION] Attempt {attempt}: Client still exists, retrying...")
                                except Exception as e:
                                    logger.error(f"[DELETE_SUBSCRIPTION] Attempt {attempt}: Failed to delete client {user.tg_id}: {e}", exc_info=True)
                                    if attempt == max_retries:
                                        success = False
                            
                            if attempt < max_retries:
                                import asyncio
                                await asyncio.sleep(1 * attempt)  # Exponential backoff
                        except Exception as e:
                            logger.error(f"[DELETE_SUBSCRIPTION] Attempt {attempt}: Unexpected error: {e}", exc_info=True)
                            if attempt == max_retries:
                                success = False
            
            # Удаляем из БД только если удаление из X-UI успешно или сервер недоступен
            if success or not connection:
                try:
                    # Удаляем все UserClient записи для этого пользователя
                    logger.info(f"[DELETE_SUBSCRIPTION] Deleting UserClient records for user {tg_id} (user.id={user.id})")
                    user_clients_query = await self.session.execute(
                        select(UserClient).where(UserClient.user_id == user.id)
                    )
                    user_clients_to_delete = user_clients_query.scalars().all()
                    logger.info(f"[DELETE_SUBSCRIPTION] Found {len(user_clients_to_delete)} UserClient records to delete")
                    
                    for user_client in user_clients_to_delete:
                        await self.session.delete(user_client)
                        logger.info(f"[DELETE_SUBSCRIPTION] Deleted UserClient record {user_client.id} (client_email={user_client.client_email}) for user {tg_id}")
                    
                    # Обновляем запись пользователя в базе данных
                    logger.info(f"[DELETE_SUBSCRIPTION] Setting user.server_id=None for user {tg_id} (current server_id={user.server_id})")
                    user.server_id = None
                    
                    # Commit changes
                    await self.session.commit()
                    
                    logger.info(f"[DELETE_SUBSCRIPTION] Successfully deleted subscription for user {tg_id}, removed {len(user_clients_to_delete)} UserClient records, server_id set to None")
                    
                    # Update metrics
                    try:
                        from app.bot.services.metrics import subscription_deletions_total
                        subscription_deletions_total.labels(status="success").inc()
                    except Exception as metrics_error:
                        logger.warning(f"Failed to update deletion metrics: {metrics_error}")
                    
                    return True
                except Exception as e:
                    logger.error(f"[DELETE_SUBSCRIPTION] Failed to delete subscription from database for user {tg_id}: {e}", exc_info=True)
                    await self.session.rollback()
                    return False
            else:
                logger.error(f"[DELETE_SUBSCRIPTION] Failed to delete subscription from X-UI for user {tg_id} after {max_retries} attempts")
            
                # Update metrics
                try:
                    from app.bot.services.metrics import subscription_deletions_total
                    subscription_deletions_total.labels(status="failed").inc()
                except Exception as metrics_error:
                    logger.warning(f"Failed to update deletion metrics: {metrics_error}")
                
                return False
            
        except Exception as e:
            logger.error(f"[DELETE_SUBSCRIPTION] Unexpected error deleting subscription for user {tg_id}: {e}", exc_info=True)
            await self.session.rollback()
            return False
    
    async def _verify_client_deletion(
        self,
        connection,
        user_clients: list | None = None,
        tg_id: int | None = None,
    ) -> bool:
        """
        Проверить, что клиенты действительно удалены из X-UI.
        
        Args:
            connection: Подключение к серверу
            user_clients: Список UserClient записей (для новой схемы)
            tg_id: Telegram ID пользователя (для старой схемы)
        
        Returns:
            True если все клиенты удалены, False если хотя бы один найден
        """
        try:
            logger.debug(f"[VERIFY_DELETION] Starting verification - user_clients count: {len(user_clients) if user_clients else 0}, tg_id: {tg_id}")
            inbounds_list = await connection.api.inbound.get_list()
            
            if not inbounds_list:
                logger.debug(f"[VERIFY_DELETION] No inbounds found - all clients deleted")
                return True
            
            if not inbounds_list[0].settings:
                logger.debug(f"[VERIFY_DELETION] Inbound has no settings - all clients deleted")
                return True
            
            if not inbounds_list[0].settings.clients:
                logger.debug(f"[VERIFY_DELETION] Inbound has no clients list - all clients deleted")
                return True
            
            inbound_clients = inbounds_list[0].settings.clients
            logger.debug(f"[VERIFY_DELETION] Found {len(inbound_clients)} clients in inbound")
            
            if user_clients:
                # Новая схема: проверяем все клиенты пользователя
                logger.debug(f"[VERIFY_DELETION] Checking {len(user_clients)} user clients against {len(inbound_clients)} inbound clients")
                emails_to_check = {uc.client_email for uc in user_clients}
                logger.debug(f"[VERIFY_DELETION] Emails to check: {emails_to_check}")
                
                found_clients = []
                for user_client in user_clients:
                    for client_in_inbound in inbound_clients:
                        if client_in_inbound.email == user_client.client_email:
                            found_clients.append(user_client.client_email)
                            logger.warning(f"[VERIFY_DELETION] Client {user_client.client_email} still exists in X-UI")
                
                if found_clients:
                    logger.warning(f"[VERIFY_DELETION] Found {len(found_clients)} clients still in X-UI: {found_clients}")
                    return False
                else:
                    logger.info(f"[VERIFY_DELETION] All {len(user_clients)} clients successfully deleted from X-UI")
                return True
            elif tg_id:
                # Старая схема: проверяем один клиент
                logger.debug(f"[VERIFY_DELETION] Checking single client with tg_id: {tg_id}")
                for client_in_inbound in inbound_clients:
                    if client_in_inbound.email == str(tg_id):
                        logger.warning(f"[VERIFY_DELETION] Client {tg_id} still exists in X-UI")
                        return False
                logger.info(f"[VERIFY_DELETION] Client {tg_id} successfully deleted from X-UI")
                return True
            else:
                logger.error("[VERIFY_DELETION] Neither user_clients nor tg_id provided")
                return False
        except Exception as e:
            logger.error(f"[VERIFY_DELETION] Error verifying client deletion: {e}", exc_info=True)
            # В случае ошибки считаем что проверка не прошла
            return False
            
    async def _delete_user_clients_from_server(
        self,
        connection,
        user_clients: list,
        inbound_id: int,
        max_retries: int = 3,
    ) -> bool:
        """
        Удалить клиентов пользователя с сервера X-UI (для миграции).
        Использует ту же логику, что и delete_subscription, но без удаления из БД.
        
        Args:
            connection: Подключение к серверу
            user_clients: Список UserClient записей для удаления
            inbound_id: ID inbound на сервере
            max_retries: Максимальное количество попыток удаления
        
        Returns:
            True если успешно, False в противном случае
        """
        logger.info(f"[MIGRATION_DELETE] Starting deletion of {len(user_clients)} clients from server {connection.server.id} (name: {connection.server.name})")
        logger.info(f"[MIGRATION_DELETE] Client emails to delete: {[uc.client_email for uc in user_clients]}")
        
        success = False
        
        # Retry логика для удаления
        for attempt in range(1, max_retries + 1):
            try:
                client_ids_to_delete = []
                try:
                    # Получаем список всех клиентов из inbound для поиска ID
                    inbounds_list = await connection.api.inbound.get_list()
                    if inbounds_list:
                        inbound_list = inbounds_list[0]
                        if inbound_list.settings and inbound_list.settings.clients:
                            # Ищем ID для каждого клиента пользователя
                            for user_client in user_clients:
                                for client_in_inbound in inbound_list.settings.clients:
                                    if client_in_inbound.email == user_client.client_email:
                                        client_id = getattr(client_in_inbound, 'id', None)
                                        if client_id:
                                            client_ids_to_delete.append(client_id)
                                            logger.debug(f"[MIGRATION_DELETE] Found client ID {client_id} for {user_client.client_email}")
                                        break
                    
                    if client_ids_to_delete:
                        # First try: delete via API
                        logger.info(f"[MIGRATION_DELETE] Attempt {attempt}: Found {len(client_ids_to_delete)} client IDs to delete: {client_ids_to_delete}")
                        try:
                            logger.info(f"[MIGRATION_DELETE] Attempt {attempt}: Calling client.delete API with inbound_id={inbound_id}, client_ids={client_ids_to_delete}")
                            await connection.api.client.delete(inbound_id, client_ids_to_delete)
                            logger.info(f"[MIGRATION_DELETE] Attempt {attempt}: client.delete API call completed successfully")
                        except Exception as delete_api_error:
                            logger.error(f"[MIGRATION_DELETE] Attempt {attempt}: Delete API call failed: {delete_api_error}", exc_info=True)
                        
                        # Wait for deletion to be processed
                        import asyncio
                        logger.info(f"[MIGRATION_DELETE] Attempt {attempt}: Waiting 1.5 seconds for X-UI to process deletion")
                        await asyncio.sleep(1.5)
                        
                        # Проверяем успешность удаления
                        logger.info(f"[MIGRATION_DELETE] Attempt {attempt}: Verifying deletion after API call")
                        if await self._verify_client_deletion(connection, user_clients):
                            success = True
                            logger.info(f"[MIGRATION_DELETE] Attempt {attempt}: Verified deletion of {len(user_clients)} clients via API delete")
                            break
                        else:
                            logger.warning(f"[MIGRATION_DELETE] Attempt {attempt}: API delete didn't work (verification failed), trying inbound update method...")
                            
                            # Alternative method: update inbound to remove clients
                            try:
                                logger.info(f"[MIGRATION_DELETE] Attempt {attempt}: Starting inbound update method")
                                inbounds_for_update = await connection.api.inbound.get_list()
                                if not inbounds_for_update:
                                    logger.warning(f"[MIGRATION_DELETE] Attempt {attempt}: No inbounds found for update")
                                else:
                                    inbound_original = inbounds_for_update[0]
                                    logger.info(f"[MIGRATION_DELETE] Attempt {attempt}: Got inbound, checking settings and clients")
                                    
                                    if not inbound_original.settings:
                                        logger.warning(f"[MIGRATION_DELETE] Attempt {attempt}: Inbound has no settings")
                                    elif not inbound_original.settings.clients:
                                        logger.warning(f"[MIGRATION_DELETE] Attempt {attempt}: Inbound has no clients list")
                                    else:
                                        # Filter out user's clients
                                        emails_to_remove = {uc.client_email for uc in user_clients}
                                        logger.info(f"[MIGRATION_DELETE] Attempt {attempt}: Emails to remove: {emails_to_remove}")
                                        
                                        original_count = len(inbound_original.settings.clients)
                                        logger.info(f"[MIGRATION_DELETE] Attempt {attempt}: Original clients count: {original_count}")
                                        
                                        # Create new clients list without user's clients
                                        filtered_clients = [
                                            c for c in inbound_original.settings.clients 
                                            if c.email not in emails_to_remove
                                        ]
                                        new_count = len(filtered_clients)
                                        logger.info(f"[MIGRATION_DELETE] Attempt {attempt}: Filtered clients count: {new_count}")
                                        
                                        if original_count > new_count:
                                            removed_count = original_count - new_count
                                            logger.info(f"[MIGRATION_DELETE] Attempt {attempt}: Removing {removed_count} clients via inbound update (from {original_count} to {new_count})")
                                            
                                            # Create a copy of the inbound with updated clients list
                                            inbound_to_update = inbound_original
                                            inbound_to_update.settings.clients = filtered_clients
                                            
                                            logger.info(f"[MIGRATION_DELETE] Attempt {attempt}: Calling inbound.update with inbound_id={inbound_id}")
                                            await connection.api.inbound.update(inbound_id, inbound_to_update)
                                            logger.info(f"[MIGRATION_DELETE] Attempt {attempt}: inbound.update call completed")
                                            
                                            # Wait for X-UI to process the update
                                            await asyncio.sleep(2.0)
                                            logger.info(f"[MIGRATION_DELETE] Attempt {attempt}: Waiting completed, verifying deletion")
                                            
                                            # Verify deletion
                                            if await self._verify_client_deletion(connection, user_clients):
                                                success = True
                                                logger.info(f"[MIGRATION_DELETE] Attempt {attempt}: Verified deletion via inbound update")
                                                break
                                            else:
                                                logger.warning(f"[MIGRATION_DELETE] Attempt {attempt}: Verification failed, clients still exist")
                                        else:
                                            logger.warning(f"[MIGRATION_DELETE] Attempt {attempt}: No clients found to remove from inbound (original={original_count}, new={new_count})")
                                            # Check if clients are already removed
                                            if await self._verify_client_deletion(connection, user_clients):
                                                logger.info(f"[MIGRATION_DELETE] Attempt {attempt}: Clients already deleted, verification passed")
                                                success = True
                                                break
                            except Exception as update_error:
                                logger.error(f"[MIGRATION_DELETE] Attempt {attempt}: Inbound update method failed: {update_error}", exc_info=True)
                    else:
                        # Клиентов уже нет в X-UI, проверяем что их действительно нет
                        if await self._verify_client_deletion(connection, user_clients):
                            logger.info(f"[MIGRATION_DELETE] Attempt {attempt}: Clients already deleted from X-UI")
                            success = True
                            break
                        else:
                            logger.warning(f"[MIGRATION_DELETE] Attempt {attempt}: Clients still exist, retrying...")
                except Exception as e:
                    logger.error(f"[MIGRATION_DELETE] Attempt {attempt}: Failed to delete multiple clients: {e}", exc_info=True)
                    if attempt == max_retries:
                        success = False
                
                if attempt < max_retries and not success:
                    import asyncio
                    await asyncio.sleep(1 * attempt)  # Exponential backoff
            except Exception as e:
                logger.error(f"[MIGRATION_DELETE] Attempt {attempt}: Unexpected error: {e}", exc_info=True)
                if attempt == max_retries:
                    success = False
        
        if success:
            logger.info(f"[MIGRATION_DELETE] Successfully deleted {len(user_clients)} clients from server {connection.server.id}")
        else:
            logger.error(f"[MIGRATION_DELETE] Failed to delete clients from server {connection.server.id} after {max_retries} attempts")
        
        return success
