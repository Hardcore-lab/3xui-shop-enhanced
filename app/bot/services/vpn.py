from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .server_pool import ServerPoolService

import asyncio
import json
import logging
import re
from datetime import datetime
from uuid import uuid4
from urllib.parse import urlparse
import aiohttp

from py3xui import Client, Inbound
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.bot.models import ClientData
from app.bot.utils.network import extract_base_url
from app.bot.utils.time import (
    add_days_to_timestamp,
    days_to_timestamp,
    get_current_timestamp,
)
from app.config import Config
from app.db.models import Promocode, Server, User, UserClient

logger = logging.getLogger(__name__)


class VPNService:
    def __init__(
        self,
        config: Config,
        session: async_sessionmaker,
        server_pool_service: ServerPoolService,
        cache = None,
    ) -> None:
        self.config = config
        self.session = session
        self.server_pool_service = server_pool_service
        self.cache = cache
        logger.info("VPN Service initialized.")

    async def is_client_exists(self, user: User) -> Client | None:
        """
        Check if user has any active clients.
        For new schema (multiple clients): checks UserClient records and returns first client.
        For old schema (single client): checks by user.tg_id email.
        """
        connection = await self.server_pool_service.get_connection(user)

        if not connection:
            return None

        # Check if user has multiple clients (new schema)
        async with self.session() as session:
            from sqlalchemy import select
            user_query = await session.execute(
                select(User).where(User.tg_id == user.tg_id)
            )
            user_with_id = user_query.scalar_one_or_none()
            if not user_with_id:
                logger.error(f"User {user.tg_id} not found in database")
                return None
            
            user_clients = await UserClient.get_by_user_id(session=session, user_id=user_with_id.id)
            
            if user_clients:
                # New schema: multiple clients - check first client
                first_client_email = user_clients[0].client_email
                try:
                    client = await connection.api.client.get_by_email(first_client_email)
                    if client:
                        logger.debug(f"Client {first_client_email} exists on server {connection.server.name} (new schema, {len(user_clients)} clients).")
                        return client
                    else:
                        logger.warning(f"Client {first_client_email} not found on server {connection.server.name} (new schema).")
                        return None
                except (ConnectionError, TimeoutError) as e:
                    logger.error(f"Network error checking client {first_client_email}: {e}")
                    return None
                except ValueError as e:
                    logger.error(f"Client {first_client_email} not found: {e}")
                    return None
                except Exception as e:
                    logger.error(f"Unexpected error checking client {first_client_email}: {e}", exc_info=True)
                    return None
            else:
                # Old schema: single client - check by user.tg_id
                try:
                    client = await connection.api.client.get_by_email(str(user.tg_id))
                    if client:
                        logger.debug(f"Client {user.tg_id} exists on server {connection.server.name} (old schema).")
                    else:
                        logger.critical(f"Client {user.tg_id} not found on server {connection.server.name} (old schema).")
                    return client
                except (ConnectionError, TimeoutError) as e:
                    logger.error(f"Network error checking client {user.tg_id}: {e}")
                    return None
                except ValueError as e:
                    logger.error(f"Client {user.tg_id} not found: {e}")
                    return None
                except Exception as e:
                    logger.error(f"Unexpected error checking client {user.tg_id}: {e}", exc_info=True)
                    return None

    async def get_limit_ip(self, user: User, client: Client) -> int | None:
        connection = await self.server_pool_service.get_connection(user)

        if not connection:
            return None

        try:
            inbounds: list[Inbound] = await connection.api.inbound.get_list()
        except (ConnectionError, TimeoutError) as exception:
            logger.error(f"Network error fetching inbounds: {exception}")
            return None
        except Exception as exception:
            logger.error(f"Unexpected error fetching inbounds: {exception}", exc_info=True)
            return None

        for inbound in inbounds:
            for inbound_client in inbound.settings.clients:
                if inbound_client.email == client.email:
                    logger.debug(f"Client {client.email} limit ip: {inbound_client.limit_ip}")
                    return inbound_client.limit_ip

        logger.critical(f"Client {client.email} not found in inbounds.")
        return None

    async def _get_inbound_client_data(
        self, 
        connection, 
        client_email: str,
        inbounds_cache: list | None = None
    ) -> dict | None:
        """
        Получить данные клиента из inbound (источник истины для панели).
        
        Args:
            connection: Подключение к серверу 3xUI
            client_email: Email клиента для поиска
            inbounds_cache: Кэш списка inbounds (опционально, для оптимизации)
        
        Returns:
            dict с ключами: expiry_time, limit_ip, flow, id, или None если не найден
        """
        try:
            # Используем кэш если передан, иначе получаем новый список
            inbounds = inbounds_cache
            if inbounds is None:
                inbounds = await connection.api.inbound.get_list()
            
            if not inbounds or not inbounds[0].settings or not inbounds[0].settings.clients:
                return None
            
            for inbound_client in inbounds[0].settings.clients:
                if inbound_client.email == client_email:
                    expiry_time = getattr(inbound_client, 'expiryTime', 
                                         getattr(inbound_client, 'expiry_time', 0))
                    
                    # Валидация expiry_time
                    if expiry_time < 0 and expiry_time != -1:
                        logger.warning(f"Invalid expiry_time {expiry_time} for {client_email}, using -1")
                        expiry_time = -1
                    
                    limit_ip = getattr(inbound_client, 'limitIp', 
                                      getattr(inbound_client, 'limit_ip', 0))
                    # Валидация limit_ip
                    if limit_ip < 0:
                        logger.warning(f"Invalid limit_ip {limit_ip} for {client_email}, using 0")
                        limit_ip = 0
                    
                    return {
                        'expiry_time': expiry_time if expiry_time > 0 else -1,
                        'limit_ip': limit_ip,
                        'flow': getattr(inbound_client, 'flow', None),
                        'id': getattr(inbound_client, 'id', None),
                    }
            return None
        except Exception as e:
            logger.warning(f"Failed to get inbound data for {client_email}: {e}")
            return None

    async def get_client_data(self, user: User) -> ClientData | None:
        logger.error(f"[PROFILE FLOW] STEP 3.1: GET_CLIENT_DATA_ENTRY - Starting to retrieve client data for {user.tg_id}")
        logger.debug(f"Starting to retrieve client data for {user.tg_id}.")

        # Check if user has multiple clients (new schema)
        user_clients = None
        user_with_id = None
        user_clients_list = []  # Will store extracted data
        logger.error(f"[PROFILE FLOW] STEP 3.1.1: CREATE_SESSION - Creating session for user {user.tg_id}")
        try:
            async with self.session() as session:
                # Load user with id first
                from sqlalchemy import select
                
                logger.error(f"[PROFILE FLOW] STEP 3.1.2: BEFORE_USER_QUERY - About to query User for tg_id={user.tg_id}")
                try:
                    logger.error(f"[PROFILE FLOW] STEP 3.1.2.1: EXECUTING_QUERY - Starting execute")
                    user_query = await session.execute(
                        select(User).where(User.tg_id == user.tg_id)
                    )
                    logger.error(f"[PROFILE FLOW] STEP 3.1.3: AFTER_USER_QUERY - Query executed successfully")
                    logger.error(f"[PROFILE FLOW] STEP 3.1.3.1: BEFORE_SCALAR - About to call scalar_one_or_none")
                    user_with_id = user_query.scalar_one_or_none()
                    logger.error(f"[PROFILE FLOW] STEP 3.1.4: USER_RESULT - user_with_id={user_with_id.id if user_with_id else None}")
                except Exception as query_error:
                    logger.error(f"[PROFILE FLOW] ERROR in user query: {query_error}", exc_info=True)
                    raise
                if not user_with_id:
                    logger.error(f"[PROFILE FLOW] ERROR: User {user.tg_id} not found in database")
                    logger.debug(f"User {user.tg_id} not found.")
                    return None
                
                logger.error(f"[PROFILE FLOW] STEP 3.2: GET_USER_CLIENTS - Loading UserClient records for user_id={user_with_id.id}")
                user_clients_raw = await UserClient.get_by_user_id(session=session, user_id=user_with_id.id)
                logger.error(f"[PROFILE FLOW] STEP 3.2: GET_USER_CLIENTS - Found {len(user_clients_raw) if user_clients_raw else 0} UserClient records")
                # Store user_clients data for use after session closes
                # Extract essential data to avoid detached object issues
                if user_clients_raw:
                    for uc in user_clients_raw:
                        # Create a simple object with essential data
                        class UserClientData:
                            def __init__(self, uc_obj):
                                self.id = uc_obj.id
                                self.client_email = uc_obj.client_email
                                self.server_id = uc_obj.server_id
                                self.vpn_id = uc_obj.vpn_id
                                # Extract expires_at as datetime to avoid detached object issues
                                self.expires_at = uc_obj.expires_at
                        user_clients_list.append(UserClientData(uc))
                    logger.error(f"[SYNC] Extracted {len(user_clients_list)} UserClientData objects from session for user {user.tg_id}")
        except Exception as session_error:
            logger.error(f"[PROFILE FLOW] ERROR in session block: {session_error}", exc_info=True)
            return None
        
        # Assign user_clients after session closes
        user_clients = user_clients_list if user_clients_list else None
        
        # Load server for first client if we have multiple clients
        if user_clients:
            # New schema: multiple clients - get data from first client
            first_client = user_clients[0]
            # Load server for this client in a new session
            async with self.session() as server_session:
                from sqlalchemy.orm import selectinload
                from sqlalchemy import select
                from app.db.models import Server
                
                server_query = await server_session.execute(
                    select(Server).where(Server.id == first_client.server_id)
                )
                client_server = server_query.scalar_one_or_none()
                
                if not client_server:
                    logger.warning(f"Server {first_client.server_id} not found for client {first_client.client_email}")
                    return None
                
                # Create temporary user object for this client
                temp_user = User()
                temp_user.tg_id = user.tg_id
                temp_user.vpn_id = first_client.vpn_id
                temp_user.server_id = first_client.server_id
                temp_user.server = client_server
                user = temp_user
        
        logger.error(f"[SYNC] After session close: user_clients={user_clients is not None}, count={len(user_clients) if user_clients else 0}")
        
        connection = await self.server_pool_service.get_connection(user)

        if not connection:
            return None

        try:
            # Determine email to search
            email_to_search = None
            if user_clients:
                # New schema: try first client email
                email_to_search = user_clients[0].client_email
            else:
                # Old schema: try user.tg_id
                email_to_search = str(user.tg_id)
            
            # 1. Сначала получаем данные из inbound (конфигурация - источник истины для панели)
            inbound_data = await self._get_inbound_client_data(connection, email_to_search)
            
            # 2. Получаем статистику трафика из client API (если нужно)
            client = None
            if inbound_data:
                # Конфигурация получена из inbound, теперь получаем статистику из API
                try:
                    if user_clients:
                        client = await connection.api.client.get_by_email(user_clients[0].client_email)
                    else:
                        client = await connection.api.client.get_by_email(str(user.tg_id))
                except ValueError as e:
                    # API недоступен, создаем объект только с конфигурацией из inbound
                    logger.warning(f"get_by_email failed for {email_to_search}, traffic stats unavailable: {e}")
                    class ClientFromInbound:
                        def __init__(self, inbound_data_dict, email):
                            self.email = email
                            self.id = inbound_data_dict.get('id')
                            # Traffic data not available via API, use defaults
                            self.total = 0
                            self.up = 0
                            self.down = 0
                            self.expiry_time = inbound_data_dict.get('expiry_time', -1)
                    
                    client = ClientFromInbound(inbound_data, email_to_search)
            else:
                # Inbound данные не найдены, пытаемся получить через API (fallback)
                try:
                    if user_clients:
                        client = await connection.api.client.get_by_email(user_clients[0].client_email)
                    else:
                        client = await connection.api.client.get_by_email(str(user.tg_id))
                except ValueError as e:
                    logger.warning(f"get_by_email failed for {email_to_search}: {e}")
                    client = None
            

            if not client:
                logger.warning(
                    f"Client {user.tg_id} not found on server {connection.server.name}."
                )
                
                # Fallback: If client not found on server but we have UserClient records in DB,
                # return ClientData based on DB data (without traffic stats)
                if user_clients:
                    logger.info(f"Client not found on server, but {len(user_clients)} UserClient records exist in DB. Returning ClientData from DB.")
                    # Get expiry time from first client (all should have same expiry)
                    first_client = user_clients[0]
                    # Convert expires_at datetime to timestamp (milliseconds)
                    from datetime import datetime
                    if isinstance(first_client.expires_at, datetime):
                        expiry_timestamp = int(first_client.expires_at.timestamp() * 1000)
                    else:
                        expiry_timestamp = -1
                    
                    # Create ClientData with DB data (traffic stats unavailable)
                    client_data = ClientData(
                        max_devices=len(user_clients),
                        traffic_total=-1,  # Unavailable
                        traffic_remaining=-1,  # Unavailable
                        traffic_used=-1,  # Unavailable
                        traffic_up=-1,  # Unavailable
                        traffic_down=-1,  # Unavailable
                        expiry_time=expiry_timestamp,
                    )
                    logger.warning(f"Returning ClientData from DB for user {user.tg_id}: {len(user_clients)} devices, expiry={expiry_timestamp}")
                    return client_data
                
                # No UserClient records either, return None
                return None

            # For multiple clients, max_devices is the total number of clients
            # Don't call get_limit_ip for multiple clients as it doesn't work correctly
            logger.error(f"[SYNC] REACHED_SYNC_BLOCK - Client found, checking user_clients: exists={user_clients is not None}, count={len(user_clients) if user_clients else 0}, user.tg_id={user.tg_id}")
            logger.error(f"[SYNC] Checking if user_clients exists: {user_clients is not None}, count: {len(user_clients) if user_clients else 0}, user.tg_id={user.tg_id}")
            if user_clients:
                max_devices = len(user_clients)
                # Synchronize expiry_time from 3xUI to database for all clients
                from datetime import datetime
                expiry_times_from_3xui = []
                
                # ОПТИМИЗАЦИЯ: Группируем клиентов по server_id для минимизации API-запросов
                clients_by_server: dict[int, list] = {}
                for user_client_db in user_clients:
                    server_id = user_client_db.server_id
                    if server_id not in clients_by_server:
                        clients_by_server[server_id] = []
                    clients_by_server[server_id].append(user_client_db)
                
                logger.error(f"[SYNC] Starting expiry_time sync for {len(user_clients)} clients for user {user.tg_id} across {len(clients_by_server)} servers")
                async with self.session() as sync_session:
                    # Обрабатываем каждый сервер отдельно
                    for server_id, server_clients in clients_by_server.items():
                        # Получаем connection для этого сервера
                        if server_id == connection.server.id:
                            client_connection = connection
                        else:
                            temp_user_for_client = User()
                            temp_user_for_client.tg_id = user.tg_id
                            temp_user_for_client.server_id = server_id
                            client_connection = await self.server_pool_service.get_connection(temp_user_for_client)
                        
                        if not client_connection:
                            # Fallback на БД для всех клиентов этого сервера
                            logger.warning(f"[SYNC] Connection failed for server {server_id}, using DB values for {len(server_clients)} clients")
                            for user_client_db in server_clients:
                                if isinstance(user_client_db.expires_at, datetime):
                                    expiry_times_from_3xui.append(int(user_client_db.expires_at.timestamp() * 1000))
                                else:
                                    expiry_times_from_3xui.append(-1)
                            continue
                        
                        # ОПТИМИЗАЦИЯ: Получаем inbound список ОДИН РАЗ для всех клиентов на сервере
                        inbounds_cache = None
                        try:
                            inbounds_cache = await client_connection.api.inbound.get_list()
                        except Exception as e:
                            logger.error(f"[SYNC] Failed to get inbound list for server {server_id}: {e}")
                            # Fallback на БД для всех клиентов этого сервера
                            for user_client_db in server_clients:
                                if isinstance(user_client_db.expires_at, datetime):
                                    expiry_times_from_3xui.append(int(user_client_db.expires_at.timestamp() * 1000))
                                else:
                                    expiry_times_from_3xui.append(-1)
                            continue
                        
                        # Обрабатываем всех клиентов на этом сервере
                        for user_client_db in server_clients:
                            try:
                                # Используем вспомогательный метод с кэшем
                                inbound_data = await self._get_inbound_client_data(
                                    client_connection, 
                                    user_client_db.client_email,
                                    inbounds_cache
                                )
                                
                                expiry_time_3xui = None
                                if inbound_data:
                                    expiry_time_3xui = inbound_data['expiry_time']
                                else:
                                    # Fallback на БД (НЕ на client.expiry_time)
                                    logger.warning(f"[SYNC] expiry_time not found in inbound for {user_client_db.client_email}, using DB value")
                                    if isinstance(user_client_db.expires_at, datetime):
                                        expiry_time_3xui = int(user_client_db.expires_at.timestamp() * 1000)
                                    else:
                                        expiry_time_3xui = -1
                                
                                # Синхронизация с БД
                                if expiry_time_3xui and expiry_time_3xui > 0:
                                    # Convert to datetime and update database
                                    expiry_datetime = datetime.fromtimestamp(expiry_time_3xui / 1000)
                                    
                                    # Update UserClient record
                                    from sqlalchemy import select
                                    user_client_query = await sync_session.execute(
                                        select(UserClient).where(UserClient.id == user_client_db.id)
                                    )
                                    db_user_client = user_client_query.scalar_one_or_none()
                                    if db_user_client:
                                        # Only update if different (avoid unnecessary commits)
                                        db_expiry_timestamp = int(db_user_client.expires_at.timestamp() * 1000) if isinstance(db_user_client.expires_at, datetime) else 0
                                        old_expiry = db_user_client.expires_at
                                        if abs(db_expiry_timestamp - expiry_time_3xui) > 1000:  # More than 1 second difference
                                            db_user_client.expires_at = expiry_datetime
                                            await sync_session.commit()
                                            logger.error(f"[SYNC] Synced expiry_time for client {user_client_db.client_email}: NEW={expiry_datetime}, OLD={old_expiry}, diff_ms={abs(db_expiry_timestamp - expiry_time_3xui)}")
                                        else:
                                            logger.debug(f"[SYNC] expiry_time for {user_client_db.client_email} already in sync (diff < 1s)")
                                    
                                    expiry_times_from_3xui.append(expiry_time_3xui)
                                else:
                                    logger.debug(f"[SYNC] Client {user_client_db.client_email} has unlimited expiry or not found")
                                    expiry_times_from_3xui.append(expiry_time_3xui if expiry_time_3xui else -1)
                                    
                            except Exception as sync_error:
                                logger.warning(f"[SYNC] Failed to sync expiry_time for client {user_client_db.client_email}: {sync_error}", exc_info=True)
                                # Fallback to database value
                                if isinstance(user_client_db.expires_at, datetime):
                                    expiry_times_from_3xui.append(int(user_client_db.expires_at.timestamp() * 1000))
                                else:
                                    expiry_times_from_3xui.append(-1)
                
                # Use the minimum expiry_time (earliest expiration) among all clients
                if expiry_times_from_3xui:
                    # Filter out -1 (unlimited) values
                    valid_expiry_times = [et for et in expiry_times_from_3xui if et > 0]
                    if valid_expiry_times:
                        expiry_time = min(valid_expiry_times)
                        logger.error(f"[SYNC] Using minimum expiry_time from synced 3xUI data for user {user.tg_id}: {expiry_time} (from {len(valid_expiry_times)} clients)")
                    else:
                        # All clients are unlimited
                        expiry_time = -1
                        logger.debug(f"All clients are unlimited for user {user.tg_id}")
                else:
                    # Fallback to database if sync failed
                    expiry_times_from_db = []
                    for user_client_db in user_clients:
                        if isinstance(user_client_db.expires_at, datetime):
                            expiry_times_from_db.append(int(user_client_db.expires_at.timestamp() * 1000))
                    
                    if expiry_times_from_db:
                        expiry_time = min(expiry_times_from_db)
                        logger.debug(f"[SYNC] Using minimum expiry_time from DB (sync failed) for user {user.tg_id}: {expiry_time}")
                    else:
                        # Используем только данные из БД, не из API
                        expiry_time = -1
                        logger.debug(f"[SYNC] No DB expiry_time available, using -1 (unlimited)")
            else:
                # For single client, get limit_ip and expiry_time from inbound
                limit_ip = await self.get_limit_ip(user=user, client=client)
                max_devices = -1 if limit_ip == 0 else limit_ip
                
                # Получаем expiry_time из inbound для одиночного клиента
                if inbound_data:
                    expiry_time = inbound_data['expiry_time']
                else:
                    # Fallback только на БД, не на API
                    async with self.session() as temp_session:
                        from sqlalchemy import select
                        user_query = await temp_session.execute(
                            select(User).where(User.tg_id == user.tg_id)
                        )
                        user_with_id = user_query.scalar_one_or_none()
                        if user_with_id:
                            user_clients_single = await UserClient.get_by_user_id(session=temp_session, user_id=user_with_id.id)
                            if user_clients_single and len(user_clients_single) == 1:
                                user_client_db = user_clients_single[0]
                                if isinstance(user_client_db.expires_at, datetime):
                                    expiry_time = int(user_client_db.expires_at.timestamp() * 1000)
                                else:
                                    expiry_time = -1
                            else:
                                expiry_time = -1
                        else:
                            expiry_time = -1
                
                # Sync expiry_time from 3xUI to database for single client
                async with self.session() as sync_session:
                    from sqlalchemy import select
                    user_query = await sync_session.execute(
                        select(User).where(User.tg_id == user.tg_id)
                    )
                    user_with_id = user_query.scalar_one_or_none()
                    if user_with_id:
                        # Check if there's a UserClient record for this user
                        user_clients_single = await UserClient.get_by_user_id(session=sync_session, user_id=user_with_id.id)
                        if user_clients_single and len(user_clients_single) == 1:
                            # Update single UserClient record
                            user_client_db = user_clients_single[0]
                            if expiry_time and expiry_time > 0:
                                expiry_datetime = datetime.fromtimestamp(expiry_time / 1000)
                                if user_client_db.expires_at != expiry_datetime:
                                    user_client_db.expires_at = expiry_datetime
                                    await sync_session.commit()
                                    logger.info(f"Synced expiry_time for single client {user_client_db.client_email}: {expiry_datetime}")
            traffic_total = client.total

            if traffic_total <= 0:
                traffic_remaining = -1
                traffic_total = -1
            else:
                traffic_remaining = client.total - (client.up + client.down)

            traffic_used = client.up + client.down
            client_data = ClientData(
                max_devices=max_devices,
                traffic_total=traffic_total,
                traffic_remaining=traffic_remaining,
                traffic_used=traffic_used,
                traffic_up=client.up,
                traffic_down=client.down,
                expiry_time=expiry_time,
            )
            logger.debug(f"Successfully retrieved client data for {user.tg_id}: {client_data}.")
            return client_data
        except (ConnectionError, TimeoutError) as exception:
            logger.error(f"Network error retrieving client data for {user.tg_id}: {exception}")
            return None
        except AttributeError as exception:
            logger.error(f"Invalid client data structure for {user.tg_id}: {exception}")
            return None
        except Exception as exception:
            logger.error(f"Unexpected error retrieving client data for {user.tg_id}: {exception}", exc_info=True)
            return None

    async def get_key(self, user: User) -> str | None:
        """
        Get subscription key for user.
        For multiple clients, returns key for first client.
        """
        logger.error(f"[GET_KEY] STEP 1: ENTRY - Getting key for user {user.tg_id}, server_id={user.server_id}")
        async with self.session() as session:
            # Load user with id first
            from sqlalchemy.orm import selectinload
            from sqlalchemy import select
            
            user_query = await session.execute(
                select(User).where(User.tg_id == user.tg_id)
            )
            user_with_id = user_query.scalar_one_or_none()
            if not user_with_id:
                logger.error(f"[GET_KEY] ERROR: User {user.tg_id} not found in database")
                logger.debug(f"User {user.tg_id} not found.")
                return None
            
            logger.error(f"[GET_KEY] STEP 2: USER_FOUND - User found: id={user_with_id.id}, server_id={user_with_id.server_id}")
            
            # Check if user has multiple clients (new schema)
            user_clients = await UserClient.get_by_user_id(session=session, user_id=user_with_id.id)
            logger.error(f"[GET_KEY] STEP 3: USER_CLIENTS - Found {len(user_clients) if user_clients else 0} UserClient records")
            
            if user_clients:
                # New schema: get key for first client
                first_client = user_clients[0]
                logger.error(f"[GET_KEY] STEP 4: FIRST_CLIENT - First client: email={first_client.client_email}, server_id={first_client.server_id}, vpn_id={first_client.vpn_id}")
                # Load server
                from sqlalchemy.orm import selectinload
                from sqlalchemy import select
                from app.db.models import Server
                
                server_query = await session.execute(
                    select(Server).where(Server.id == first_client.server_id)
                )
                client_server = server_query.scalar_one_or_none()
                
                if not client_server:
                    logger.error(f"[GET_KEY] ERROR: Server {first_client.server_id} not found for client {first_client.client_email}")
                    logger.warning(f"Server {first_client.server_id} not found")
                    return None
                
                logger.error(f"[GET_KEY] STEP 5: SERVER_FOUND - Server found: name={client_server.name}, host={client_server.host}")
                subscription = extract_base_url(
                    url=client_server.host,
                    port=self.config.xui.SUBSCRIPTION_PORT,
                    path=self.config.xui.SUBSCRIPTION_PATH,
                )
                key = f"{subscription}{first_client.vpn_id}"
                return key
            
            # Old schema: single client
            # Загружаем пользователя с сервером
            from sqlalchemy.orm import selectinload
            from sqlalchemy import select
            
            user_query = await session.execute(
                select(User)
                .options(selectinload(User.server))
                .where(User.tg_id == user.tg_id)
            )
            user_with_server = user_query.scalar_one_or_none()
            
            if not user_with_server:
                logger.debug(f"User {user.tg_id} not found.")
                return None

            if not user_with_server.server_id or not user_with_server.server:
                logger.debug(f"Server ID or server object for user {user.tg_id} not found.")
                return None

            subscription = extract_base_url(
                url=user_with_server.server.host,
                port=self.config.xui.SUBSCRIPTION_PORT,
                path=self.config.xui.SUBSCRIPTION_PATH,
            )
            key = f"{subscription}{user_with_server.vpn_id}"
            return key

    async def get_subscription_keys_per_client(self, user: User) -> list[tuple[int, str]]:
        """
        Return subscription keys per device: list of (client_index, key) for each UserClient.
        Key = subscription base URL + vpn_id. For old schema (no UserClients), returns one element (1, key).
        """
        result: list[tuple[int, str]] = []
        async with self.session() as session:
            from sqlalchemy import select
            from sqlalchemy.orm import selectinload
            from app.db.models import Server

            user_query = await session.execute(
                select(User).where(User.tg_id == user.tg_id)
            )
            user_with_id = user_query.scalar_one_or_none()
            if not user_with_id:
                return result

            user_clients = await UserClient.get_by_user_id(session=session, user_id=user_with_id.id)
            if user_clients:
                first = user_clients[0]
                server_query = await session.execute(
                    select(Server).where(Server.id == first.server_id)
                )
                server = server_query.scalar_one_or_none()
                if not server:
                    return result
                subscription_base = extract_base_url(
                    url=server.host,
                    port=self.config.xui.SUBSCRIPTION_PORT,
                    path=self.config.xui.SUBSCRIPTION_PATH,
                )
                for uc in sorted(user_clients, key=lambda c: c.client_index):
                    result.append((uc.client_index, f"{subscription_base}{uc.vpn_id}"))
                return result

            # Old schema: single client
            user_with_server_query = await session.execute(
                select(User)
                .options(selectinload(User.server))
                .where(User.tg_id == user.tg_id)
            )
            user_with_server = user_with_server_query.scalar_one_or_none()
            if not user_with_server or not user_with_server.server_id or not user_with_server.server:
                return result
            subscription_base = extract_base_url(
                url=user_with_server.server.host,
                port=self.config.xui.SUBSCRIPTION_PORT,
                path=self.config.xui.SUBSCRIPTION_PATH,
            )
            key = f"{subscription_base}{user_with_server.vpn_id}"
            result.append((1, key))
            return result

    async def get_vless_key(self, user: User) -> str | None:
        """
        Получить vless URI ключ для пользователя.
        Для нескольких клиентов возвращает ключ первого клиента.
        Сначала пытается получить через API, затем через HTTP запрос к subscription URL.
        """
        
        # Check if user has multiple clients (new schema)
        user_clients = None
        async with self.session() as session:
            # Load user with id first
            from sqlalchemy import select
            
            user_query = await session.execute(
                select(User).where(User.tg_id == user.tg_id)
            )
            user_with_id = user_query.scalar_one_or_none()
            if not user_with_id:
                logger.debug(f"User {user.tg_id} not found")
                return await self._get_vless_key_from_subscription_url(user)
            
            user_clients = await UserClient.get_by_user_id(session=session, user_id=user_with_id.id)
            
            if user_clients:
                # New schema: get vless key for first client
                first_client = user_clients[0]
                # Load server
                from sqlalchemy.orm import selectinload
                from sqlalchemy import select
                from app.db.models import Server
                
                server_query = await session.execute(
                    select(Server).where(Server.id == first_client.server_id)
                )
                client_server = server_query.scalar_one_or_none()
                
                if not client_server:
                    logger.warning(f"Server {first_client.server_id} not found")
                    return None
                
                # Create temporary user object
                temp_user = User()
                temp_user.tg_id = user.tg_id
                temp_user.vpn_id = first_client.vpn_id
                temp_user.server_id = first_client.server_id
                temp_user.server = client_server
                user = temp_user
            else:
                # Old schema: load user with server
                from sqlalchemy.orm import selectinload
                from sqlalchemy import select
                
                user_query = await session.execute(
                    select(User)
                    .options(selectinload(User.server))
                    .where(User.tg_id == user.tg_id)
                )
                user_with_server = user_query.scalar_one_or_none()
                if not user_with_server or not user_with_server.server:
                    logger.debug(f"User or server not found for user {user.tg_id}")
                    return await self._get_vless_key_from_subscription_url(user)
                user = user_with_server

        # Основной метод: получение через API
        try:
            connection = await self.server_pool_service.get_connection(user)
            if not connection:
                logger.debug(f"Connection not found for user {user.tg_id}")
                return await self._get_vless_key_from_subscription_url(user)

            # Try to get client by email
            client_email = str(user.tg_id)
            if user_clients:
                client_email = user_clients[0].client_email
            
            client = await connection.api.client.get_by_email(client_email)
            if not client:
                logger.debug(f"Client not found for user {user.tg_id}")
                return await self._get_vless_key_from_subscription_url(user)

            # Получаем inbound, где находится клиент
            inbounds: list[Inbound] = await connection.api.inbound.get_list()
            if not inbounds:
                logger.debug(f"No inbounds found for user {user.tg_id}")
                return await self._get_vless_key_from_subscription_url(user)

            # Ищем inbound с нашим клиентом
            inbound = None
            inbound_client = None
            for inbound_item in inbounds:
                if hasattr(inbound_item, 'settings') and hasattr(inbound_item.settings, 'clients'):
                    for ic in inbound_item.settings.clients:
                        if ic.email == client.email or ic.id == client.id:
                            inbound = inbound_item
                            inbound_client = ic
                            break
                if inbound:
                    break

            if not inbound:
                logger.debug(f"Inbound not found for client {user.tg_id}")
                return await self._get_vless_key_from_subscription_url(user)

            # Извлекаем данные для формирования vless URI
            # Используем user.vpn_id как UUID (это правильный UUID из базы данных)
            uuid = user.vpn_id
            if not uuid:
                logger.debug(f"UUID not found for client {user.tg_id}")
                return await self._get_vless_key_from_subscription_url(user)

            # Получаем адрес сервера из host
            server_host = user.server.host
            parsed_host = urlparse(server_host)
            address = parsed_host.hostname or parsed_host.netloc.split(':')[0]

            # Получаем порт из inbound
            port = getattr(inbound, 'port', None)
            if not port:
                logger.debug(f"Port not found in inbound for user {user.tg_id}, trying to get from settings")
                # Пытаемся получить порт из настроек
                if hasattr(inbound, 'settings') and hasattr(inbound.settings, 'port'):
                    port = inbound.settings.port
                if not port:
                    logger.debug(f"Port not found in inbound settings for user {user.tg_id}")
                    return await self._get_vless_key_from_subscription_url(user)

            # Получаем flow из client или используем по умолчанию
            flow = getattr(client, 'flow', None) or 'xtls-rprx-vision'
            # Если flow пустой, используем значение по умолчанию
            if not flow or flow == '':
                flow = 'xtls-rprx-vision'

            # Получаем security из stream_settings (snake_case, не camelCase!)
            security = 'tls'
            reality_settings = None
            stream_settings = getattr(inbound, 'stream_settings', None) or getattr(inbound, 'streamSettings', None)
            
            if stream_settings:
                # Получаем security
                if hasattr(stream_settings, 'security'):
                    security = getattr(stream_settings, 'security', 'tls') or 'tls'
                elif hasattr(stream_settings, 'tls_settings'):
                    security = 'tls'
                
                # Проверяем наличие reality_settings (snake_case!)
                reality_settings = getattr(stream_settings, 'reality_settings', None) or getattr(stream_settings, 'realitySettings', None)
                if reality_settings:
                    security = 'reality'
            
            # Также проверяем в настройках протокола
            if hasattr(inbound, 'protocol') and inbound.protocol:
                if 'reality' in inbound.protocol.lower():
                    security = 'reality'
                elif 'tls' in inbound.protocol.lower() and security != 'reality':
                    security = 'tls'
            
            # Получаем type из network или используем по умолчанию
            network_type = 'tcp'
            if stream_settings:
                if hasattr(stream_settings, 'network'):
                    network_type = getattr(stream_settings, 'network', 'tcp') or 'tcp'

            # Формируем vless URI с параметрами
            remark = f"{user.server.name or 'VPN'}"
            
            # Базовые параметры
            params = [f"type={network_type}", f"security={security}"]
            
            # Добавляем encryption=none для reality
            if security == 'reality':
                params.append("encryption=none")
            
            # Добавляем flow если не reality или если указан
            if security != 'reality' or flow:
                params.append(f"flow={flow}")
            
            # Добавляем параметры reality если они есть
            if security == 'reality' and reality_settings:
                # reality_settings может быть словарем или объектом
                # Извлекаем параметры reality
                if isinstance(reality_settings, dict):
                    # Если это словарь, обращаемся как к словарю
                    settings_dict = reality_settings.get('settings', {}) if 'settings' in reality_settings else reality_settings
                    pbk = settings_dict.get('publicKey') or settings_dict.get('pbk')
                    fp = settings_dict.get('fingerprint') or settings_dict.get('fp')
                    # serverName может быть в settings, но также может быть в serverNames (список)
                    sni = settings_dict.get('serverName') or settings_dict.get('sni')
                    if not sni:
                        # Пробуем получить из serverNames (список)
                        server_names = reality_settings.get('serverNames', [])
                        if server_names and isinstance(server_names, list) and len(server_names) > 0:
                            sni = server_names[0]
                    spx = settings_dict.get('spiderX') or settings_dict.get('spx')
                    pqv = settings_dict.get('mldsa65Verify') or settings_dict.get('pqv')
                    # shortIds - это список, берем первый элемент
                    short_ids = reality_settings.get('shortIds', [])
                    sid = short_ids[0] if short_ids else None
                else:
                    # Если это объект, используем getattr
                    settings_obj = getattr(reality_settings, 'settings', None) if hasattr(reality_settings, 'settings') else reality_settings
                    if settings_obj:
                        pbk = getattr(settings_obj, 'publicKey', None) or getattr(settings_obj, 'pbk', None)
                        fp = getattr(settings_obj, 'fingerprint', None) or getattr(settings_obj, 'fp', None)
                        sni = getattr(settings_obj, 'serverName', None) or getattr(settings_obj, 'sni', None)
                        spx = getattr(settings_obj, 'spiderX', None) or getattr(settings_obj, 'spx', None)
                        pqv = getattr(settings_obj, 'mldsa65Verify', None) or getattr(settings_obj, 'pqv', None)
                    else:
                        pbk = getattr(reality_settings, 'publicKey', None) or getattr(reality_settings, 'pbk', None)
                        fp = getattr(reality_settings, 'fingerprint', None) or getattr(reality_settings, 'fp', None)
                        sni = getattr(reality_settings, 'serverName', None) or getattr(reality_settings, 'sni', None)
                        spx = getattr(reality_settings, 'spiderX', None) or getattr(reality_settings, 'spx', None)
                        pqv = getattr(reality_settings, 'mldsa65Verify', None) or getattr(reality_settings, 'pqv', None)
                    
                    # serverNames может быть списком на верхнем уровне
                    if not sni:
                        server_names = getattr(reality_settings, 'serverNames', None) or getattr(reality_settings, 'server_names', None)
                        if server_names and isinstance(server_names, list) and len(server_names) > 0:
                            sni = server_names[0]
                    
                    # shortIds - это атрибут объекта
                    short_ids = getattr(reality_settings, 'shortIds', None) or getattr(reality_settings, 'short_ids', None)
                    if short_ids:
                        sid = short_ids[0] if isinstance(short_ids, list) and len(short_ids) > 0 else str(short_ids) if short_ids else None
                    else:
                        sid = getattr(reality_settings, 'shortId', None) or getattr(reality_settings, 'sid', None)
                
                if pbk:
                    params.append(f"pbk={pbk}")
                if fp:
                    params.append(f"fp={fp}")
                if sni:
                    params.append(f"sni={sni}")
                if sid:
                    params.append(f"sid={sid}")
                if spx:
                    # spx может быть строкой, нужно URL-encode если содержит специальные символы
                    from urllib.parse import quote
                    params.append(f"spx={quote(str(spx), safe='')}")
                if pqv:
                    # pqv (mldsa65Verify) - это очень длинный параметр
                    from urllib.parse import quote
                    params.append(f"pqv={quote(str(pqv), safe='')}")
            
            # Формируем финальный URI
            query_string = "&".join(params)
            vless_uri = f"vless://{uuid}@{address}:{port}?{query_string}#{remark}"

            logger.debug(f"Generated vless URI for user {user.tg_id} via API: length={len(vless_uri)}")
            return vless_uri

        except (ConnectionError, TimeoutError) as exception:
            logger.warning(f"Network error getting vless key via API for user {user.tg_id}: {exception}")
            # Fallback: получение через HTTP запрос
            return await self._get_vless_key_from_subscription_url(user)
        except (AttributeError, KeyError) as exception:
            logger.warning(f"Invalid API response structure for user {user.tg_id}: {exception}")
            # Fallback: получение через HTTP запрос
            return await self._get_vless_key_from_subscription_url(user)
        except Exception as exception:
            logger.warning(f"Unexpected error getting vless key via API for user {user.tg_id}: {exception}", exc_info=True)
            # Fallback: получение через HTTP запрос
            return await self._get_vless_key_from_subscription_url(user)

    async def _get_vless_key_from_subscription_url(self, user: User) -> str | None:
        """
        Fallback метод: получение vless ключа через HTTP запрос к subscription URL.
        """
        try:
            subscription_url = await self.get_key(user)
            if not subscription_url:
                logger.debug(f"Subscription URL not found for user {user.tg_id}")
                return None

            async with aiohttp.ClientSession() as session:
                async with session.get(subscription_url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status != 200:
                        logger.warning(f"Failed to fetch subscription for user {user.tg_id}: HTTP {response.status}")
                        return None

                    content = await response.text()

                    # Пытаемся найти vless URI в ответе
                    vless_pattern = r'vless://[^\s<>"\'{}|\\^`\[\]]+'
                    match = re.search(vless_pattern, content)
                    if match:
                        vless_uri = match.group(0)
                        logger.debug(f"Extracted vless URI from subscription URL for user {user.tg_id}")
                        return vless_uri

                    # Если не нашли vless URI, пытаемся парсить JSON ответ
                    try:
                        json_data = await response.json()
                        if isinstance(json_data, dict):
                            # Ищем vless URI в различных полях
                            for key in ['vless', 'uri', 'link', 'config', 'connection']:
                                if key in json_data:
                                    value = json_data[key]
                                    if isinstance(value, str) and value.startswith('vless://'):
                                        logger.debug(f"Found vless URI in JSON field '{key}' for user {user.tg_id}")
                                        return value
                                    elif isinstance(value, str):
                                        match = re.search(vless_pattern, value)
                                        if match:
                                            logger.debug(f"Found vless URI in JSON field '{key}' for user {user.tg_id}")
                                            return match.group(0)
                    except Exception:
                        pass

                    logger.debug(f"Vless URI not found in subscription response for user {user.tg_id}")
                    return None

        except (aiohttp.ClientError, TimeoutError) as exception:
            logger.warning(f"Network error getting vless key from subscription URL for user {user.tg_id}: {exception}")
            return None
        except Exception as exception:
            logger.warning(f"Unexpected error getting vless key from subscription URL for user {user.tg_id}: {exception}", exc_info=True)
            return None

    async def create_client(
        self,
        user: User,
        devices: int,
        duration: int,
        enable: bool = True,
        flow: str = "xtls-rprx-vision",
        total_gb: int = 0,
        inbound_id: int = 1,
        server_id: int | None = None,
    ) -> bool:
        # Валидация входных данных
        if not isinstance(devices, int) or devices < 1 or devices > 100:
            logger.error(f"Invalid devices value: {devices} (must be between 1 and 100)")
            return False
        if not isinstance(duration, int) or duration < 1 or duration > 365:
            logger.error(f"Invalid duration value: {duration} (must be between 1 and 365)")
            return False
        if server_id is not None and (not isinstance(server_id, int) or server_id <= 0):
            logger.error(f"Invalid server_id: {server_id} (must be positive integer or None)")
            return False
        if not isinstance(total_gb, int) or total_gb < 0:
            logger.error(f"Invalid total_gb value: {total_gb} (must be non-negative)")
            return False
        
        logger.info(f"Creating new client {user.tg_id} | {devices} devices {duration} days.")

        if server_id:
            await self.server_pool_service.assign_specific_server_to_user(user, server_id)
        else:
            await self.server_pool_service.assign_server_to_user(user)
        connection = await self.server_pool_service.get_connection(user)

        if not connection:
            return False

        new_client = Client(
            email=str(user.tg_id),
            enable=enable,
            id=user.vpn_id,
            expiry_time=days_to_timestamp(duration),
            flow=flow,
            limit_ip=devices,
            sub_id=user.vpn_id,
            total_gb=total_gb,
        )
        inbound_id = await self.server_pool_service.get_inbound_id(connection.api)

        try:
            await connection.api.client.add(inbound_id=inbound_id, clients=[new_client])
            logger.info(f"Successfully created client for {user.tg_id}")
            return True
        except (ConnectionError, TimeoutError) as exception:
            logger.error(f"Network error creating client for {user.tg_id}: {exception}")
            return False
        except ValueError as exception:
            logger.error(f"Invalid data creating client for {user.tg_id}: {exception}")
            return False
        except Exception as exception:
            logger.error(f"Unexpected error creating client for {user.tg_id}: {exception}", exc_info=True)
            return False

    async def update_client(
        self,
        user: User,
        devices: int,
        duration: int,
        replace_devices: bool = False,
        replace_duration: bool = False,
        enable: bool = True,
        flow: str = "xtls-rprx-vision",
        total_gb: int = 0,
    ) -> bool:
        # Валидация входных данных
        if not isinstance(devices, int) or devices < 1 or devices > 100:
            logger.error(f"Invalid devices value: {devices} (must be between 1 and 100)")
            return False
        # Allow negative duration for removing days (but limit to reasonable range)
        if not isinstance(duration, int) or abs(duration) > 365:
            logger.error(f"Invalid duration value: {duration} (must be between -365 and 365)")
            return False
        if not isinstance(total_gb, int) or total_gb < 0:
            logger.error(f"Invalid total_gb value: {total_gb} (must be non-negative)")
            return False
        
        logger.info(f"Updating client {user.tg_id} | {devices} devices {duration} days.")
        connection = await self.server_pool_service.get_connection(user)

        if not connection:
            return False

        try:
            current_time = get_current_timestamp()
            
            # Получаем данные из inbound (источник истины для панели)
            inbound_data = await self._get_inbound_client_data(connection, str(user.tg_id))
            
            # Получаем текущий expiry_time из inbound или БД
            expiry_time_to_use = None
            if not replace_duration:
                if inbound_data and inbound_data['expiry_time'] > 0:
                    expiry_time_to_use = max(inbound_data['expiry_time'], current_time)
                else:
                    # Fallback на БД
                    async with self.session() as session:
                        from sqlalchemy import select
                        user_query = await session.execute(
                            select(User).where(User.tg_id == user.tg_id)
                        )
                        user_with_id = user_query.scalar_one_or_none()
                        if user_with_id:
                            # Проверяем UserClient для новой схемы
                            user_clients = await UserClient.get_by_user_id(session=session, user_id=user_with_id.id)
                            if user_clients and len(user_clients) == 1:
                                user_client_db = user_clients[0]
                                if isinstance(user_client_db.expires_at, datetime):
                                    expiry_time_to_use = max(int(user_client_db.expires_at.timestamp() * 1000), current_time)
                                else:
                                    expiry_time_to_use = current_time
                            else:
                                expiry_time_to_use = current_time
                        else:
                            expiry_time_to_use = current_time
            else:
                expiry_time_to_use = current_time
            
            if expiry_time_to_use is None:
                expiry_time_to_use = current_time

            expiry_time = add_days_to_timestamp(timestamp=expiry_time_to_use, days=duration)
            
            # Validation: ensure subscription doesn't expire in the past
            if expiry_time < current_time:
                logger.warning(
                    f"Cannot remove {abs(duration)} days from client {user.tg_id}: "
                    f"would expire in the past (new_expiry={expiry_time}, current_time={current_time})"
                )
                return False

            # Получаем текущий limit_ip из inbound если не заменяем
            if not replace_devices:
                if inbound_data and inbound_data.get('limit_ip', 0) > 0:
                    current_device_limit = inbound_data['limit_ip']
                else:
                    # Fallback: получаем через get_limit_ip
                    client = await connection.api.client.get_by_email(str(user.tg_id))
                    if client:
                        current_device_limit = await self.get_limit_ip(user=user, client=client) or 0
                    else:
                        current_device_limit = 0
                devices = current_device_limit + devices

            # Обновляем через inbound (источник истины для панели)
            inbounds = await connection.api.inbound.get_list()
            if not inbounds or not inbounds[0].settings or not inbounds[0].settings.clients:
                logger.error(f"No inbounds found for updating client {user.tg_id}")
                return False
            
            inbound = inbounds[0]
            client_updated = False
            for inbound_client in inbound.settings.clients:
                if inbound_client.email == str(user.tg_id):
                    # Обновляем данные в inbound
                    inbound_client.expiry_time = expiry_time
                    inbound_client.limit_ip = devices
                    inbound_client.flow = flow
                    if hasattr(inbound_client, 'enable'):
                        inbound_client.enable = enable
                    client_updated = True
                    break
            
            if client_updated:
                # Обновляем inbound
                await connection.api.inbound.update(inbound.id, inbound)
                logger.info(f"Client {user.tg_id} updated successfully via inbound.")
                
                # Также обновляем через client API для совместимости
                try:
                    client = await connection.api.client.get_by_email(str(user.tg_id))
                    if client:
                        client.enable = enable
                        client.id = user.vpn_id
                        client.expiry_time = expiry_time
                        client.flow = flow
                        client.limit_ip = devices
                        client.sub_id = user.vpn_id
                        client.total_gb = total_gb
                        await connection.api.client.update(client_uuid=client.id, client=client)
                except Exception as api_error:
                    logger.warning(f"Failed to update client via API (inbound update succeeded): {api_error}")
                
                return True
            else:
                logger.error(f"Client {user.tg_id} not found in inbound list for update")
                return False
        except (ConnectionError, TimeoutError) as exception:
            logger.error(f"Network error updating client for {user.tg_id}: {exception}")
            return False
        except ValueError as exception:
            logger.error(f"Invalid data updating client for {user.tg_id}: {exception}")
            return False
        except Exception as exception:
            logger.error(f"Unexpected error updating client for {user.tg_id}: {exception}", exc_info=True)
            return False

    async def create_subscription(
        self, user: User, devices: int, duration: int, server_id: int | None = None
    ) -> bool:
        """
        Create a subscription for a user.
        If devices > 1, creates multiple separate clients (one per device).
        Otherwise, creates a single client (backward compatibility).
        """
        # Валидация входных данных
        if not isinstance(devices, int) or devices < 1 or devices > 100:
            logger.error(f"Invalid devices value: {devices} (must be between 1 and 100)")
            return False
        if not isinstance(duration, int) or duration < 1 or duration > 365:
            logger.error(f"Invalid duration value: {duration} (must be between 1 and 365)")
            return False
        if server_id is not None and (not isinstance(server_id, int) or server_id <= 0):
            logger.error(f"Invalid server_id: {server_id} (must be positive integer or None)")
            return False
        
        logger.error(f"[PURCHASE FLOW] STEP 9.1: CREATE_SUBSCRIPTION_ENTRY - User {user.tg_id}, devices={devices}, duration={duration}, server_id={server_id}")
        if devices > 1:
            logger.error(f"[PURCHASE FLOW] STEP 9.2: MULTIPLE_CLIENTS - Creating {devices} clients for user {user.tg_id}")
            result = await self.create_multiple_clients(
                user=user, devices=devices, duration=duration, server_id=server_id
            )
            logger.error(f"[PURCHASE FLOW] STEP 9.2: MULTIPLE_CLIENTS - Result: {result}")
            return result
        else:
            client_exists = await self.is_client_exists(user)
            logger.error(f"[PURCHASE FLOW] STEP 9.3: SINGLE_CLIENT - Client exists: {client_exists} for user {user.tg_id}")
            if not client_exists:
                result = await self.create_client(user=user, devices=devices, duration=duration, server_id=server_id)
                logger.error(f"[PURCHASE FLOW] STEP 9.3: SINGLE_CLIENT - Creation result: {result}")
                return result
            logger.error(f"[PURCHASE FLOW] STEP 9.3: SINGLE_CLIENT - Client already exists, returning False")
            return False

    async def create_multiple_clients(
        self, user: User, devices: int, duration: int, server_id: int | None = None
    ) -> bool:
        """
        Create multiple separate clients for a user (one per device).
        Each client has limit_ip=1 and unique email: {tg_id}_{index}
        
        Uses distributed Redis lock to prevent race conditions from concurrent purchases.
        """
        from app.bot.utils.redis_lock import redis_lock, LockAcquisitionError
        import redis.asyncio as redis
        
        # Get Redis client from config
        redis_client = redis.from_url(self.config.redis.url())
        lock_key = f"create_client:{user.tg_id}"
        
        try:
            async with redis_lock(redis_client, lock_key, timeout=60, wait_timeout=30):
                logger.error(f"[PURCHASE FLOW] STEP 9.2.1: MULTIPLE_CLIENTS_START - Lock acquired, creating {devices} clients for user {user.tg_id}, duration={duration} days")
                logger.info(f"Creating {devices} clients for user {user.tg_id} | {duration} days.")
                
                return await self._create_multiple_clients_locked(user, devices, duration, server_id)
        except LockAcquisitionError:
            logger.error(f"[PURCHASE FLOW] ERROR: Could not acquire lock for user {user.tg_id} client creation (timeout or conflict)")
            return False
        except Exception as e:
            logger.error(f"[PURCHASE FLOW] ERROR: Unexpected error in create_multiple_clients for user {user.tg_id}: {e}", exc_info=True)
            return False
        finally:
            await redis_client.close()
    
    async def _create_multiple_clients_locked(
        self, user: User, devices: int, duration: int, server_id: int | None = None
    ) -> bool:
        """
        Internal method that performs actual client creation.
        Should only be called within the distributed lock.
        """

        # Assign server to user
        logger.error(f"[PURCHASE FLOW] STEP 9.2.2: ASSIGN_SERVER - Assigning server to user {user.tg_id}, server_id={server_id}")
        if server_id:
            assign_result = await self.server_pool_service.assign_specific_server_to_user(user, server_id)
            if not assign_result:
                logger.error(f"[PURCHASE FLOW] ERROR: Failed to assign server {server_id} to user {user.tg_id}")
                return False
        else:
            await self.server_pool_service.assign_server_to_user(user)
        logger.error(f"[PURCHASE FLOW] STEP 9.2.2: ASSIGN_SERVER - Server assigned, user.server_id={user.server_id}")
        
        if not user.server_id:
            logger.error(f"[PURCHASE FLOW] ERROR: user.server_id is None after assignment for user {user.tg_id}")
            return False
        
        connection = await self.server_pool_service.get_connection(user)
        if not connection:
            logger.error(f"[PURCHASE FLOW] ERROR: Failed to get connection for user {user.tg_id}, server_id={user.server_id}")
            logger.error(f"Failed to get connection for user {user.tg_id}")
            return False

        # Get inbound ID
        inbound_id = await self.server_pool_service.get_inbound_id(connection.api)
        if not inbound_id:
            logger.error(f"[PURCHASE FLOW] ERROR: Failed to get inbound ID for user {user.tg_id}, server_id={user.server_id}")
            return False
        logger.error(f"[PURCHASE FLOW] STEP 9.2.3: GET_INBOUND - Inbound ID: {inbound_id}")
        
        # Check for existing clients on X-UI server BEFORE checking DB
        logger.error(f"[PURCHASE FLOW] STEP 9.2.3.0: CHECK_XUI_CLIENTS - Checking for existing clients on X-UI server for user {user.tg_id}")
        existing_xui_clients = []
        inbounds_list = None
        try:
            inbounds_list = await connection.api.inbound.get_list()
            if inbounds_list and len(inbounds_list) > 0:
                inbound_list = inbounds_list[0]
                if inbound_list.settings and inbound_list.settings.clients:
                    for client_in_inbound in inbound_list.settings.clients:
                        if client_in_inbound.email.startswith(f"{user.tg_id}_"):
                            existing_xui_clients.append(client_in_inbound)
                            logger.warning(f"[PURCHASE FLOW] STEP 9.2.3.0: FOUND_XUI_CLIENT - Found existing client on X-UI: {client_in_inbound.email}")
            elif not inbounds_list or len(inbounds_list) == 0:
                logger.error(f"[PURCHASE FLOW] ERROR: No inbounds found on server for user {user.tg_id}, server_id={user.server_id}")
                return False
        except Exception as xui_check_error:
            logger.warning(f"[PURCHASE FLOW] STEP 9.2.3.0: CHECK_XUI_CLIENTS - Error checking X-UI clients: {xui_check_error}")
        
        # Check and delete existing clients before creating new ones
        logger.error(f"[PURCHASE FLOW] STEP 9.2.3.1: CHECK_EXISTING_CLIENTS - Checking for existing clients for user {user.tg_id}, found {len(existing_xui_clients)} on X-UI")
        try:
            # Load user with id from DB
            async with self.session() as session:
                from sqlalchemy import select
                from app.db.models import User as UserModel
                user_query = await session.execute(
                    select(UserModel).where(UserModel.tg_id == user.tg_id)
                )
                user_with_id = user_query.scalar_one_or_none()
                if user_with_id:
                    user.id = user_with_id.id
                    # Check for existing UserClient records
                    from app.db.models import UserClient as UserClientModel
                    existing_user_clients = await UserClientModel.get_by_user_id(session=session, user_id=user_with_id.id)
                    
                    # If we have existing clients on X-UI but not in DB, delete them first (orphaned clients)
                    # We don't sync them because they might have wrong expiry/duration from previous failed purchase
                    if existing_xui_clients and not existing_user_clients:
                        logger.warning(f"[PURCHASE FLOW] STEP 9.2.3.1: DELETE_ORPHANED_XUI_CLIENTS - Found {len(existing_xui_clients)} orphaned clients on X-UI (not in DB), deleting them before creating new ones")
                        try:
                            if not inbounds_list:  # Reload if not already loaded
                                inbounds_list = await connection.api.inbound.get_list()
                            if inbounds_list and len(inbounds_list) > 0:
                                inbound_list = inbounds_list[0]
                                if inbound_list.settings and inbound_list.settings.clients:
                                    client_ids_to_delete = []
                                    for xui_client in existing_xui_clients:
                                        client_id = getattr(xui_client, 'id', None)
                                        if client_id:
                                            client_ids_to_delete.append(client_id)
                                    
                                    if client_ids_to_delete:
                                        logger.error(f"[PURCHASE FLOW] STEP 9.2.3.1: DELETE_ORPHANED_XUI_CLIENTS - Deleting {len(client_ids_to_delete)} orphaned clients from X-UI")
                                        await connection.api.client.delete(inbound_id, client_ids_to_delete)
                                        logger.error(f"[PURCHASE FLOW] STEP 9.2.3.1: DELETE_ORPHANED_XUI_CLIENTS - Successfully deleted {len(client_ids_to_delete)} orphaned clients")
                                        
                                        # Verify deletion
                                        import asyncio
                                        await asyncio.sleep(0.5)
                                        verify_inbounds = await connection.api.inbound.get_list()
                                        if verify_inbounds and len(verify_inbounds) > 0:
                                            verify_inbound = verify_inbounds[0]
                                            if verify_inbound.settings and verify_inbound.settings.clients:
                                                remaining_clients = [c.email for c in verify_inbound.settings.clients if c.email.startswith(f"{user.tg_id}_")]
                                                if remaining_clients:
                                                    logger.warning(f"[PURCHASE FLOW] STEP 9.2.3.1: DELETE_ORPHANED_VERIFY - Some orphaned clients still exist: {remaining_clients}, will retry in final check")
                            else:
                                logger.error(f"[PURCHASE FLOW] ERROR: No inbounds found when trying to delete orphaned clients")
                        except (ConnectionError, TimeoutError) as delete_error:
                            logger.warning(f"[PURCHASE FLOW] STEP 9.2.3.1: DELETE_ORPHANED_XUI_CLIENTS - Network error deleting orphaned clients: {delete_error}, continuing with creation")
                        except Exception as delete_error:
                            logger.warning(f"[PURCHASE FLOW] STEP 9.2.3.1: DELETE_ORPHANED_XUI_CLIENTS - Unexpected error deleting orphaned clients: {delete_error}, continuing with creation")
                    
                    
                    if existing_user_clients:
                        logger.error(f"[PURCHASE FLOW] STEP 9.2.3.1: FOUND_EXISTING_CLIENTS - Found {len(existing_user_clients)} existing clients in DB, deleting them")
                        # Get client IDs from X-UI for deletion
                        try:
                            if not inbounds_list:  # Reload if not already loaded
                                inbounds_list = await connection.api.inbound.get_list()
                            if inbounds_list and len(inbounds_list) > 0:
                                inbound_list = inbounds_list[0]
                                if inbound_list.settings and inbound_list.settings.clients:
                                    client_ids_to_delete = []
                                    for user_client in existing_user_clients:
                                        for client_in_inbound in inbound_list.settings.clients:
                                            if client_in_inbound.email == user_client.client_email:
                                                client_id = getattr(client_in_inbound, 'id', None)
                                                if client_id:
                                                    client_ids_to_delete.append(client_id)
                                                    logger.debug(f"Found existing client ID {client_id} for {user_client.client_email}")
                                                break
                                    
                                    if client_ids_to_delete:
                                        logger.error(f"[PURCHASE FLOW] STEP 9.2.3.1: DELETE_EXISTING_CLIENTS - Deleting {len(client_ids_to_delete)} existing clients from X-UI")
                                        await connection.api.client.delete(inbound_id, client_ids_to_delete)
                                        logger.error(f"[PURCHASE FLOW] STEP 9.2.3.1: DELETE_EXISTING_CLIENTS - Successfully deleted {len(client_ids_to_delete)} clients")
                                        
                                        # Verify deletion - wait a bit and check
                                        import asyncio
                                        await asyncio.sleep(0.5)
                                        verify_inbounds = await connection.api.inbound.get_list()
                                        if verify_inbounds:
                                            verify_inbound = verify_inbounds[0]
                                            if verify_inbound.settings and verify_inbound.settings.clients:
                                                remaining_clients = [c.email for c in verify_inbound.settings.clients if c.email.startswith(f"{user.tg_id}_")]
                                                if remaining_clients:
                                                    logger.warning(f"[PURCHASE FLOW] STEP 9.2.3.1: DELETE_VERIFY - Some clients still exist after deletion: {remaining_clients}, retrying deletion")
                                                    # Retry deletion
                                                    retry_ids = []
                                                    for client_in_inbound in verify_inbound.settings.clients:
                                                        if client_in_inbound.email.startswith(f"{user.tg_id}_"):
                                                            client_id = getattr(client_in_inbound, 'id', None)
                                                            if client_id:
                                                                retry_ids.append(client_id)
                                                    if retry_ids:
                                                        await connection.api.client.delete(inbound_id, retry_ids)
                                                        await asyncio.sleep(0.5)
                                                        logger.info(f"[PURCHASE FLOW] STEP 9.2.3.1: DELETE_RETRY - Retried deletion of {len(retry_ids)} clients")
                                    
                                    # Delete UserClient records from DB
                                    for user_client in existing_user_clients:
                                        await session.delete(user_client)
                                    await session.commit()
                                    logger.error(f"[PURCHASE FLOW] STEP 9.2.3.1: DELETE_EXISTING_CLIENTS - Deleted {len(existing_user_clients)} UserClient records from DB")
                        except (ConnectionError, TimeoutError) as delete_error:
                            logger.warning(f"[PURCHASE FLOW] STEP 9.2.3.1: DELETE_EXISTING_CLIENTS - Network error deleting existing clients: {delete_error}, continuing with creation")
                        except SQLAlchemyError as delete_error:
                            logger.warning(f"[PURCHASE FLOW] STEP 9.2.3.1: DELETE_EXISTING_CLIENTS - Database error deleting existing clients: {delete_error}, continuing with creation")
                            await session.rollback()
                        except Exception as delete_error:
                            logger.warning(f"[PURCHASE FLOW] STEP 9.2.3.1: DELETE_EXISTING_CLIENTS - Unexpected error deleting existing clients: {delete_error}, continuing with creation")
                    
                else:
                    logger.error(f"[PURCHASE FLOW] STEP 9.2.3.1: USER_NOT_FOUND - User {user.tg_id} not found in DB")
        except SQLAlchemyError as check_error:
            logger.warning(f"[PURCHASE FLOW] STEP 9.2.3.1: CHECK_EXISTING_CLIENTS - Database error checking existing clients: {check_error}, continuing with creation")
        except Exception as check_error:
            logger.warning(f"[PURCHASE FLOW] STEP 9.2.3.1: CHECK_EXISTING_CLIENTS - Unexpected error checking existing clients: {check_error}, continuing with creation")
        
        # Calculate expiry time
        expiry_timestamp = days_to_timestamp(duration)
        expires_at = datetime.fromtimestamp(expiry_timestamp / 1000)
        logger.error(f"[PURCHASE FLOW] STEP 9.2.4: CALCULATE_EXPIRY - Expires at: {expires_at}")        # Create clients list
        clients_to_create = []
        user_clients_to_save = []
        logger.error(f"[PURCHASE FLOW] STEP 9.2.5: PREPARE_CLIENTS - Preparing {devices} clients for user {user.tg_id}")
        for index in range(1, devices + 1):
            client_email = f"{user.tg_id}_{index}"
            vpn_id = str(uuid4())
            
            # Create Client object for X-UI
            new_client = Client(
                email=client_email,
                enable=True,
                id=vpn_id,
                expiry_time=expiry_timestamp,
                flow="xtls-rprx-vision",
                limit_ip=1,  # One device per client
                sub_id=vpn_id,
                total_gb=0,
            )
            clients_to_create.append(new_client)
            
            # Prepare UserClient record for database
            user_clients_to_save.append({
                "user_id": user.id,
                "client_index": index,
                "client_email": client_email,
                "vpn_id": vpn_id,
                "server_id": user.server_id,
                "expires_at": expires_at,
            })
            logger.debug(f"[PURCHASE FLOW] STEP 9.2.5: PREPARE_CLIENTS - Prepared client {index}/{devices}: {client_email}")

        try:
            # Final check before creating - ensure no duplicate clients exist
            logger.error(f"[PURCHASE FLOW] STEP 9.2.6: FINAL_CHECK - Final check for existing clients before creation")
            import asyncio
            await asyncio.sleep(1.0)  # Wait to ensure previous deletions are fully processed
            
            final_inbounds = await connection.api.inbound.get_list()
            if final_inbounds and len(final_inbounds) > 0:
                final_inbound = final_inbounds[0]
                if final_inbound.settings and final_inbound.settings.clients:
                    final_existing = [c.email for c in final_inbound.settings.clients if c.email.startswith(f"{user.tg_id}_")]
                    if final_existing:
                        logger.warning(f"[PURCHASE FLOW] STEP 9.2.6: FINAL_CHECK - Found {len(final_existing)} existing clients before creation: {final_existing}, deleting them")
                        final_ids_to_delete = []
                        for client_in_inbound in final_inbound.settings.clients:
                            if client_in_inbound.email.startswith(f"{user.tg_id}_"):
                                client_id = getattr(client_in_inbound, 'id', None)
                                if client_id:
                                    final_ids_to_delete.append(client_id)
                        if final_ids_to_delete:
                            logger.error(f"[PURCHASE FLOW] STEP 9.2.6: FINAL_CHECK - Deleting {len(final_ids_to_delete)} remaining clients")
                            await connection.api.client.delete(inbound_id, final_ids_to_delete)
                            await asyncio.sleep(1.0)  # Wait after deletion
                            logger.info(f"[PURCHASE FLOW] STEP 9.2.6: FINAL_CHECK - Deleted {len(final_ids_to_delete)} remaining clients, waiting before creation")
            
            logger.error(f"[PURCHASE FLOW] STEP 9.2.6: CREATE_CLIENTS_XUI - Creating {len(clients_to_create)} clients in X-UI")
            # Create all clients in X-UI
            await connection.api.client.add(inbound_id=inbound_id, clients=clients_to_create)
            logger.error(f"[PURCHASE FLOW] STEP 9.2.6: CREATE_CLIENTS_XUI - Successfully created {devices} clients in X-UI")
            logger.info(f"Successfully created {devices} clients in X-UI for user {user.tg_id}")
            
            # Ensure user.id is set (needed for UserClient records)
            if not user.id:
                logger.error(f"[PURCHASE FLOW] STEP 9.2.6.1: GET_USER_ID - user.id is None, loading user from DB")
                async with self.session() as temp_session:
                    from sqlalchemy import select
                    user_query = await temp_session.execute(
                        select(User).where(User.tg_id == user.tg_id)
                    )
                    user_with_id = user_query.scalar_one_or_none()
                    if user_with_id:
                        user.id = user_with_id.id
                        logger.error(f"[PURCHASE FLOW] STEP 9.2.6.1: GET_USER_ID - Loaded user.id={user.id} from DB")
                    else:
                        logger.error(f"[PURCHASE FLOW] STEP 9.2.6.1: GET_USER_ID - Failed to load user from DB")
                        return False
            
            # Update user_id in user_clients_to_save
            for client_data in user_clients_to_save:
                client_data["user_id"] = user.id
            
            # Save UserClient records to database
            logger.error(f"[PURCHASE FLOW] STEP 9.2.7: SAVE_TO_DB - Saving {len(user_clients_to_save)} UserClient records to database, user.id={user.id}")
            async with self.session() as session:
                # Ensure user.server_id is saved to DB
                logger.error(f"[PURCHASE FLOW] STEP 9.2.7.1: SAVE_USER_SERVER_ID - Saving user.server_id={user.server_id} to DB for user {user.tg_id}")
                from app.db.models import User
                from sqlalchemy import select
                await User.update(session=session, tg_id=user.tg_id, server_id=user.server_id)
                await session.commit()  # Commit server_id update first
                logger.error(f"[PURCHASE FLOW] STEP 9.2.7.1: SAVE_USER_SERVER_ID - User.server_id saved and committed")                # Reload user from DB to ensure we have the latest server_id
                user_query = await session.execute(
                     select(User).where(User.tg_id == user.tg_id)
                )
                updated_user = user_query.scalar_one_or_none()
                if updated_user:
                    user.server_id = updated_user.server_id
                    logger.error(f"[PURCHASE FLOW] STEP 9.2.7.1: RELOAD_USER - Reloaded user.server_id={user.server_id} from DB")
                else:
                    logger.error(f"[PURCHASE FLOW] STEP 9.2.7.1: RELOAD_USER - Failed to reload user from DB")
                
                saved_count = 0
                for client_data in user_clients_to_save:
                    await UserClient.create(
                        session=session,
                        user_id=client_data["user_id"],
                        client_index=client_data["client_index"],
                        client_email=client_data["client_email"],
                        vpn_id=client_data["vpn_id"],
                        server_id=client_data["server_id"],
                        expires_at=client_data["expires_at"],
                    )
                    saved_count += 1
                await session.commit()
                logger.error(f"[PURCHASE FLOW] STEP 9.2.7: SAVE_TO_DB - Saved {saved_count} UserClient records")
                # Verify UserClient records were saved
                from sqlalchemy import select
                verify_query = await session.execute(
                    select(UserClient).where(UserClient.user_id == user.id)
                )
                verified_clients = verify_query.scalars().all()
                logger.error(f"[PURCHASE FLOW] STEP 9.2.7.2: VERIFY_USERCLIENTS - Verified {len(verified_clients)} UserClient records in DB (expected {devices})")
            logger.error(f"[PURCHASE FLOW] STEP 9.2.8: COMPLETE - Successfully created subscription with {devices} clients for user {user.tg_id}")
            logger.info(f"Successfully saved {devices} UserClient records for user {user.tg_id}")
            return True
            
        except (ConnectionError, TimeoutError) as exception:
            logger.error(f"Network error creating clients for {user.tg_id}: {exception}")
            return False
        except ValueError as exception:
            logger.error(f"Invalid data creating clients for {user.tg_id}: {exception}")
            return False
        except IntegrityError as exception:
            logger.error(f"Database integrity error creating clients for {user.tg_id}: {exception}", exc_info=True)
            async with self.session() as session:
                await session.rollback()
            return False
        except SQLAlchemyError as exception:
            logger.error(f"Database error creating clients for {user.tg_id}: {exception}", exc_info=True)
            async with self.session() as session:
                await session.rollback()
            return False
        except Exception as exception:
            logger.error(f"Unexpected error creating clients for {user.tg_id}: {exception}", exc_info=True)
            return False

    async def get_all_user_keys(self, user: User) -> list[dict[str, str | int]]:
        """
        Get all keys for a user (all clients).
        Returns a list of dictionaries with keys: index, key, vless_key, email
        Cached for 60 seconds to improve performance on repeated access.
        """
        # Try to get from cache first
        cache_key = f"user_keys:{user.tg_id}"
        if hasattr(self, 'cache') and self.cache:
            try:
                cached_keys = await self.cache.redis.get(cache_key)
                if cached_keys:
                    logger.info(f"[PROFILE FLOW] CACHE HIT - Returning cached keys for user {user.tg_id}")
                    keys = json.loads(cached_keys)
                    logger.info(f"[PROFILE FLOW] CACHE HIT RETURN - Returning {len(keys)} cached keys instantly")
                    return keys
            except Exception as e:
                logger.warning(f"Cache read failed for user {user.tg_id}: {e}")
        
        logger.error(f"[PROFILE FLOW] STEP 4.1: GET_ALL_USER_KEYS_ENTRY - Getting all keys for user {user.tg_id}")
        async with self.session() as session:
            # Load user with id first
            from sqlalchemy import select
            
            user_query = await session.execute(
                select(User).where(User.tg_id == user.tg_id)
            )
            user_with_id = user_query.scalar_one_or_none()
            if not user_with_id:
                logger.error(f"[PROFILE FLOW] ERROR: User {user.tg_id} not found in get_all_user_keys")
                logger.debug(f"User {user.tg_id} not found.")
                return []
            
            logger.error(f"[PROFILE FLOW] STEP 4.2: GET_USER_CLIENTS_FOR_KEYS - Loading UserClient records for user_id={user_with_id.id}")
            user_clients = await UserClient.get_by_user_id(session=session, user_id=user_with_id.id)
            logger.error(f"[PROFILE FLOW] STEP 4.2: GET_USER_CLIENTS_FOR_KEYS - Found {len(user_clients) if user_clients else 0} UserClient records")
            if not user_clients:
                logger.error(f"[PROFILE FLOW] STEP 4.3: FALLBACK_OLD_SCHEMA - No UserClient records, using old schema")
                # Fallback to old single client approach
                key = await self.get_key(user)
                vless_key = await self.get_vless_key(user)
                logger.error(f"[PROFILE FLOW] STEP 4.3: FALLBACK_OLD_SCHEMA - Old schema keys: key={bool(key)}, vless_key={bool(vless_key)}")
                if key:
                    return [{"index": 1, "key": key, "vless_key": vless_key or "", "email": str(user.tg_id)}]
                return []
            
            # Оптимизация: загружаем все серверы одним запросом
            from sqlalchemy.orm import selectinload
            from sqlalchemy import select
            
            # Получаем уникальные server_id из user_clients
            server_ids = {uc.server_id for uc in user_clients if uc.server_id}
            servers_dict = {}
            if server_ids:
                servers_query = await session.execute(
                    select(Server).where(Server.id.in_(server_ids))
                )
                servers = servers_query.scalars().all()
                servers_dict = {server.id: server for server in servers}
            
            logger.error(f"[PROFILE FLOW] STEP 4.4: PROCESS_CLIENTS - Processing {len(user_clients)} clients in parallel")
            
            # Prepare tasks for parallel execution
            async def get_client_keys(idx: int, user_client):
                logger.error(f"[PROFILE FLOW] STEP 4.4.{idx+1}: PROCESS_CLIENT - Processing client {user_client.client_index}: {user_client.client_email}, server_id={user_client.server_id}")
                # Получаем сервер из словаря (уже загружен)
                client_server = servers_dict.get(user_client.server_id) if user_client.server_id else None
                
                if not client_server:
                    logger.warning(f"Server {user_client.server_id} not found for client {user_client.client_email}")
                    return None
                
                # Create a temporary user object for this client
                temp_user = User()
                temp_user.tg_id = user.tg_id
                temp_user.vpn_id = user_client.vpn_id
                temp_user.server_id = user_client.server_id
                temp_user.server = client_server
                
                # Get key and vless key for this client in parallel
                logger.error(f"[PROFILE FLOW] STEP 4.4.{idx+1}.1: GET_KEY - Getting keys for client {user_client.client_email}")
                key, vless_key = await asyncio.gather(
                    self._get_key_for_client(temp_user, user_client.client_email),
                    self._get_vless_key_for_client(temp_user, user_client.client_email),
                )
                logger.error(f"[PROFILE FLOW] STEP 4.4.{idx+1}.2: GET_KEYS_RESULT - Key: {bool(key)}, Vless: {bool(vless_key)}")
                
                return {
                    "index": user_client.client_index,
                    "key": key or "",
                    "vless_key": vless_key or "",
                    "email": user_client.client_email,
                }
            
            # Execute all client key retrievals in parallel
            results = await asyncio.gather(
                *[get_client_keys(idx, uc) for idx, uc in enumerate(user_clients)],
                return_exceptions=True
            )
            
            # Filter out None results and exceptions
            keys_list = []
            for idx, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"[PROFILE FLOW] STEP 4.4.{idx+1}: ERROR - {result}")
                elif result is not None:
                    keys_list.append(result)
                    logger.error(f"[PROFILE FLOW] STEP 4.4.{idx+1}.3: CLIENT_ADDED - Added client {result['index']} to keys list")
            
            logger.error(f"[PROFILE FLOW] STEP 4.5: GET_ALL_USER_KEYS_COMPLETE - Returning {len(keys_list)} keys")
            
            # Cache the result for 60 seconds
            if hasattr(self, 'cache') and self.cache and keys_list:
                try:
                    cache_key = f"user_keys:{user.tg_id}"
                    await self.cache.redis.setex(cache_key, 60, json.dumps(keys_list))
                    logger.info(f"[PROFILE FLOW] CACHE SET - Cached {len(keys_list)} keys for user {user.tg_id} (TTL: 60s)")
                except Exception as e:
                    logger.warning(f"Cache write failed for user {user.tg_id}: {e}")
            
            return keys_list

    async def _get_key_for_client(self, user: User, client_email: str) -> str | None:
        """Get subscription URL key for a specific client."""
        if not user.server_id or not user.server:
            return None
        
        try:
            subscription = extract_base_url(
                url=user.server.host,
                port=self.config.xui.SUBSCRIPTION_PORT,
                path=self.config.xui.SUBSCRIPTION_PATH,
            )
            # Extract vpn_id from client_email (format: {tg_id}_{index})
            # For subscription URL, we need to get the vpn_id from UserClient
            async with self.session() as session:
                user_client = await UserClient.get_by_email(session=session, email=client_email)
                if user_client:
                    return f"{subscription}{user_client.vpn_id}"
            return None
        except SQLAlchemyError as exception:
            logger.error(f"Database error getting key for client {client_email}: {exception}", exc_info=True)
            return None
        except Exception as exception:
            logger.error(f"Unexpected error getting key for client {client_email}: {exception}", exc_info=True)
        return None

    async def _get_vless_key_for_client(self, user: User, client_email: str) -> str | None:
        """Get vless key for a specific client."""
        connection = await self.server_pool_service.get_connection(user)
        if not connection:
            return None
        
        client = None
        inbound_for_fallback = None
        try:
            client = await connection.api.client.get_by_email(client_email)
        except ValueError as e:
            # get_by_email failed, try to find in inbound list
            logger.warning(f"get_by_email failed for {client_email}, trying to find in inbound list: {e}")
            try:
                inbounds = await connection.api.inbound.get_list()
                if inbounds:
                    inbound_for_fallback = inbounds[0]  # Take first inbound for fallback
                    if inbound_for_fallback.settings and inbound_for_fallback.settings.clients:
                        # Search for client by email in inbound clients list
                        for inbound_client in inbound_for_fallback.settings.clients:
                            if inbound_client.email == client_email:
                                # Found client in inbound list, create a mock Client object
                                class MockClient:
                                    def __init__(self, inbound_client, email):
                                        self.email = email
                                        self.id = getattr(inbound_client, 'id', None)
                                        self.flow = getattr(inbound_client, 'flow', 'xtls-rprx-vision')
                                
                                client = MockClient(inbound_client, client_email)
                                logger.info(f"Found client {client_email} in inbound list for VLESS key generation")
                                break
            except (ConnectionError, TimeoutError) as inbound_error:
                logger.error(f"Network error searching inbound list for {client_email}: {inbound_error}")
            except Exception as inbound_error:
                logger.error(f"Unexpected error searching inbound list for {client_email}: {inbound_error}", exc_info=True)
        
        # If client not found, try to generate VLESS key using vpn_id from UserClient
        if not client:
            logger.warning(f"Client {client_email} not found via API or in inbound list, trying fallback with UserClient vpn_id")
            try:
                async with self.session() as session:
                    user_client = await UserClient.get_by_email(session=session, email=client_email)
                    if user_client and user_client.vpn_id:
                        # Get inbound for server parameters
                        if not inbound_for_fallback:
                            inbounds = await connection.api.inbound.get_list()
                            if inbounds:
                                inbound_for_fallback = inbounds[0]
                        
                        if inbound_for_fallback:
                            # Create a mock Client object using vpn_id from UserClient
                            class MockClientFromDB:
                                def __init__(self, vpn_id, email):
                                    self.email = email
                                    self.id = vpn_id
                                    self.flow = 'xtls-rprx-vision'
                            
                            client = MockClientFromDB(user_client.vpn_id, client_email)
                            logger.info(f"Using vpn_id {user_client.vpn_id} from UserClient for VLESS key generation")
                        else:
                            logger.warning(f"Cannot generate VLESS key: no inbound found for server")
                            return None
                    else:
                        logger.warning(f"Cannot generate VLESS key: UserClient not found or vpn_id missing")
                        return None
            except SQLAlchemyError as db_error:
                logger.error(f"Database error getting UserClient for {client_email}: {db_error}", exc_info=True)
                return None
            except Exception as db_error:
                logger.error(f"Unexpected error getting UserClient for {client_email}: {db_error}", exc_info=True)
                return None
        
        if not client:
            logger.warning(f"Client {client_email} not found for VLESS key generation")
            return None
        
        try:
            # Use the same logic as get_vless_key but for specific client
            return await self._get_vless_key_from_client(user, client, connection)
        except Exception as exception:
            logger.error(f"Error getting vless key for client {client_email}: {exception}")
            return None

    async def _get_vless_key_from_client(self, user: User, client, connection) -> str | None:
        """Extract vless key from client object."""
        try:
            inbounds: list[Inbound] = await connection.api.inbound.get_list()
            if not inbounds:
                logger.warning(f"[PROFILE FLOW] STEP 4.4.X: VLESS_ERROR - No inbounds found for server")
                return None

            inbound = None
            inbound_client = None
            for inbound_item in inbounds:
                if hasattr(inbound_item, 'settings') and hasattr(inbound_item.settings, 'clients'):
                    for ic in inbound_item.settings.clients:
                        if ic.email == client.email or ic.id == client.id:
                            inbound = inbound_item
                            inbound_client = ic
                            break
                if inbound:
                    break

            # If client not found in inbound list, use first inbound and client.id (vpn_id) directly
            if not inbound:
                inbound = inbounds[0]  # Use first inbound for parameters
                logger.warning(f"[PROFILE FLOW] STEP 4.4.X: VLESS_FALLBACK - Client {client.email} not in inbound list, using first inbound and client.id")
            
            # Use UUID from inbound_client if found, otherwise use client.id (vpn_id from DB)
            uuid = None
            if inbound_client:
                uuid = getattr(inbound_client, 'id', None)
            
            if not uuid:
                # Fallback to client.id (vpn_id from UserClient) if inbound_client not found
                uuid = client.id
                
            if not uuid:
                logger.error(f"[PROFILE FLOW] STEP 4.4.X: VLESS_ERROR - No UUID found for client {client.email}")
                return None
            
            logger.error(f"[PROFILE FLOW] STEP 4.4.X: VLESS_UUID - Using UUID for client {client.email}: {uuid}")

            server_host = user.server.host
            parsed_host = urlparse(server_host)
            address = parsed_host.hostname or parsed_host.netloc.split(':')[0]

            port = getattr(inbound, 'port', None)
            if not port:
                if hasattr(inbound, 'settings') and hasattr(inbound.settings, 'port'):
                    port = inbound.settings.port
                if not port:
                    return None

            flow = getattr(client, 'flow', None) or 'xtls-rprx-vision'
            if not flow or flow == '':
                flow = 'xtls-rprx-vision'

            security = 'tls'
            reality_settings = None
            stream_settings = getattr(inbound, 'stream_settings', None) or getattr(inbound, 'streamSettings', None)
            
            if stream_settings:
                if hasattr(stream_settings, 'security'):
                    security = getattr(stream_settings, 'security', 'tls') or 'tls'
                elif hasattr(stream_settings, 'tls_settings'):
                    security = 'tls'
                
                reality_settings = getattr(stream_settings, 'reality_settings', None) or getattr(stream_settings, 'realitySettings', None)
                if reality_settings:
                    security = 'reality'
            
            if hasattr(inbound, 'protocol') and inbound.protocol:
                if 'reality' in inbound.protocol.lower():
                    security = 'reality'
                elif 'tls' in inbound.protocol.lower() and security != 'reality':
                    security = 'tls'
            
            network_type = 'tcp'
            if stream_settings:
                if hasattr(stream_settings, 'network'):
                    network_type = getattr(stream_settings, 'network', 'tcp') or 'tcp'

            remark = f"{user.server.name or 'VPN'}"
            
            params = [f"type={network_type}", f"security={security}"]
            
            if security == 'reality':
                params.append("encryption=none")
            
            if security != 'reality' or flow:
                params.append(f"flow={flow}")
            
            if security == 'reality' and reality_settings:
                # Полная логика извлечения параметров Reality (как в основном методе get_vless_key)
                if isinstance(reality_settings, dict):
                    settings_dict = reality_settings.get('settings', {}) if 'settings' in reality_settings else reality_settings
                    pbk = settings_dict.get('publicKey') or settings_dict.get('pbk')
                    fp = settings_dict.get('fingerprint') or settings_dict.get('fp')
                    sni = settings_dict.get('serverName') or settings_dict.get('sni')
                    if not sni:
                        server_names = reality_settings.get('serverNames', [])
                        if server_names and isinstance(server_names, list) and len(server_names) > 0:
                            sni = server_names[0]
                    spx = settings_dict.get('spiderX') or settings_dict.get('spx')
                    pqv = settings_dict.get('mldsa65Verify') or settings_dict.get('pqv')
                    short_ids = reality_settings.get('shortIds', [])
                    sid = short_ids[0] if short_ids else None
                else:
                    settings_obj = getattr(reality_settings, 'settings', None) if hasattr(reality_settings, 'settings') else reality_settings
                    if settings_obj:
                        pbk = getattr(settings_obj, 'publicKey', None) or getattr(settings_obj, 'pbk', None)
                        fp = getattr(settings_obj, 'fingerprint', None) or getattr(settings_obj, 'fp', None)
                        sni = getattr(settings_obj, 'serverName', None) or getattr(settings_obj, 'sni', None)
                        spx = getattr(settings_obj, 'spiderX', None) or getattr(settings_obj, 'spx', None)
                        pqv = getattr(settings_obj, 'mldsa65Verify', None) or getattr(settings_obj, 'pqv', None)
                    else:
                        pbk = getattr(reality_settings, 'publicKey', None) or getattr(reality_settings, 'pbk', None)
                        fp = getattr(reality_settings, 'fingerprint', None) or getattr(reality_settings, 'fp', None)
                        sni = getattr(reality_settings, 'serverName', None) or getattr(reality_settings, 'sni', None)
                        spx = getattr(reality_settings, 'spiderX', None) or getattr(reality_settings, 'spx', None)
                        pqv = getattr(reality_settings, 'mldsa65Verify', None) or getattr(reality_settings, 'pqv', None)
                    
                    if not sni:
                        server_names = getattr(reality_settings, 'serverNames', None) or getattr(reality_settings, 'server_names', None)
                        if server_names and isinstance(server_names, list) and len(server_names) > 0:
                            sni = server_names[0]
                    
                    short_ids = getattr(reality_settings, 'shortIds', None) or getattr(reality_settings, 'short_ids', None)
                    if short_ids:
                        sid = short_ids[0] if isinstance(short_ids, list) and len(short_ids) > 0 else str(short_ids) if short_ids else None
                    else:
                        sid = getattr(reality_settings, 'shortId', None) or getattr(reality_settings, 'sid', None)
                
                if pbk:
                    params.append(f"pbk={pbk}")
                if fp:
                    params.append(f"fp={fp}")
                if sni:
                    params.append(f"sni={sni}")
                if sid:
                    params.append(f"sid={sid}")
                if spx:
                    from urllib.parse import quote
                    params.append(f"spx={quote(str(spx), safe='')}")
                if pqv:
                    from urllib.parse import quote
                    params.append(f"pqv={quote(str(pqv), safe='')}")

            params_str = "&".join(params)
            vless_uri = f"vless://{uuid}@{address}:{port}?{params_str}#{remark}"
            
            logger.error(f"[PROFILE FLOW] STEP 4.4.X: VLESS_GENERATED - Generated vless URI for client {client.email}, length={len(vless_uri)}, params_count={len(params)}")
            return vless_uri
            
        except Exception as exception:
            logger.error(f"Error generating vless key for client {client.email}: {exception}", exc_info=True)
            return None

    async def extend_subscription(self, user: User, devices: int, duration: int) -> bool:
        """
        Extend subscription for user.
        For multiple clients: extends expiry time for all clients.
        For single client: extends expiry time and optionally updates device limit.
        """
        logger.info(f"Extending subscription for user {user.tg_id}, devices={devices}, duration={duration} days")
        logger.error(f"[EXTEND FLOW] STEP 5: EXTEND_SUBSCRIPTION_CALLED - Extending subscription for user {user.tg_id}, devices={devices}, duration={duration} days")
        
        # Check if user has multiple clients (new schema)
        async with self.session() as session:
            from sqlalchemy import select
            user_query = await session.execute(
                select(User).where(User.tg_id == user.tg_id)
            )
            user_with_id = user_query.scalar_one_or_none()
            if not user_with_id:
                logger.error(f"User {user.tg_id} not found for subscription extension")
                return False
            
            user_clients = await UserClient.get_by_user_id(session=session, user_id=user_with_id.id)
            
            if user_clients:
                # New schema: extend all clients
                return await self._extend_multiple_clients(user, user_clients, duration)
            else:
                # Old schema: extend single client
                return await self.update_client(
                    user=user,
                    devices=devices,
                    duration=duration,
                    replace_devices=True,
                )

    async def change_subscription(self, user: User, devices: int, duration: int) -> bool:
        """
        Change subscription for user (change devices and duration).
        For multiple clients: recreates clients with new device count.
        For single client: updates device limit and duration.
        """
        # Валидация входных данных
        if not isinstance(devices, int) or devices < 1 or devices > 100:
            logger.error(f"Invalid devices value: {devices} (must be between 1 and 100)")
            return False
        if not isinstance(duration, int) or duration < 1 or duration > 365:
            logger.error(f"Invalid duration value: {duration} (must be between 1 and 365)")
            return False
        
        logger.info(f"Changing subscription for user {user.tg_id}, devices={devices}, duration={duration} days")
        
        if not await self.is_client_exists(user):
            logger.error(f"User {user.tg_id} has no active subscription to change")
            return False
        
        # Check if user has multiple clients (new schema)
        async with self.session() as session:
            from sqlalchemy import select
            user_query = await session.execute(
                select(User).where(User.tg_id == user.tg_id)
            )
            user_with_id = user_query.scalar_one_or_none()
            if not user_with_id:
                logger.error(f"User {user.tg_id} not found for subscription change")
                return False
            
            user_clients = await UserClient.get_by_user_id(session=session, user_id=user_with_id.id)
            
            if user_clients:
                # New schema: change multiple clients (recreate with new device count)
                return await self._change_multiple_clients(user, user_clients, devices, duration)
            else:
                # Old schema: change single client
                return await self.update_client(
                    user,
                    devices,
                    duration,
                    replace_devices=True,
                    replace_duration=True,
                )

    async def _extend_multiple_clients(
        self, user: User, user_clients: list, duration: int
    ) -> bool:
        """
        Extend expiry time for all user clients.
        
        Args:
            user: User object
            user_clients: List of UserClient records
            duration: Days to extend
        
        Returns:
            True if successful, False otherwise
        """
        logger.info(f"Extending {len(user_clients)} clients for user {user.tg_id} by {duration} days")
        
        connection = await self.server_pool_service.get_connection(user)
        if not connection:
            logger.error(f"Failed to get connection for user {user.tg_id}")
            return False
        
        try:
            from app.bot.utils.time import add_days_to_timestamp, get_current_timestamp
            from datetime import datetime
            
            inbound_id = await self.server_pool_service.get_inbound_id(connection.api)
            if not inbound_id:
                logger.error(f"Failed to get inbound ID for user {user.tg_id}")
                return False
            
            current_time = get_current_timestamp()
            success_count = 0
            
            # Get inbound to update clients in the list
            inbounds = await connection.api.inbound.get_list()
            if not inbounds:
                logger.error(f"Failed to get inbound list for user {user.tg_id}")
                return False
            
            inbound = None
            for inv in inbounds:
                if inv.id == inbound_id:
                    inbound = inv
                    break
            
            if not inbound or not inbound.settings or not inbound.settings.clients:
                logger.error(f"Failed to get inbound or clients list for user {user.tg_id}")
                return False
            
            for user_client in user_clients:
                try:
                    # Get client from X-UI
                    client = await connection.api.client.get_by_email(user_client.client_email)
                    if not client:
                        logger.warning(f"Client {user_client.client_email} not found in X-UI, skipping")
                        continue
                    
                    # Use expiry_time from database (user_client.expires_at) as source of truth
                    # This is more reliable than server data, especially after recent updates
                    from datetime import datetime
                    if isinstance(user_client.expires_at, datetime):
                        db_expiry_time = int(user_client.expires_at.timestamp() * 1000)
                    else:
                        # Используем только БД, не API. Если БД пусто, используем текущее время (для новых клиентов)
                        db_expiry_time = current_time
                    
                    # Calculate new expiry time
                    # For positive duration (extending): use max(current_expiry, current_time) to extend from now if expired
                    # For negative duration (removing): always use db_expiry_time to subtract from actual expiry
                    if duration > 0:
                        # Extending: extend from current expiry or now, whichever is later
                        current_expiry = max(db_expiry_time, current_time) if db_expiry_time > 0 else current_time
                    else:
                        # Removing: always subtract from actual expiry time from DB (don't use max with current_time)
                        current_expiry = db_expiry_time if db_expiry_time > 0 else current_time
                    new_expiry_time = add_days_to_timestamp(timestamp=current_expiry, days=duration)
                    
                    # Validation: ensure subscription doesn't expire in the past
                    if new_expiry_time < current_time:
                        logger.warning(
                            f"Cannot remove {abs(duration)} days from client {user_client.client_email}: "
                            f"would expire in the past (new_expiry={new_expiry_time}, current_time={current_time})"
                        )
                        continue
                    
                    # Update client in inbound clients list
                    client_updated = False
                    for idx, client_in_inbound in enumerate(inbound.settings.clients):
                        if client_in_inbound.email == user_client.client_email:
                            # Update expiry time in the inbound client
                            client_in_inbound.expiry_time = new_expiry_time
                            client_updated = True
                            logger.debug(f"Updated client {user_client.client_email} expiry to {new_expiry_time} in inbound list")
                            break
                    
                    if not client_updated:
                        logger.warning(f"Client {user_client.client_email} not found in inbound clients list, trying direct update")
                        # Fallback: try direct client update
                        client.expiry_time = new_expiry_time
                        # Use client.id or try to find the correct ID
                        client_uuid = getattr(client, 'id', None) or getattr(client, 'uuid', None)
                        if client_uuid:
                            await connection.api.client.update(client_uuid=client_uuid, client=client)
                        else:
                            logger.error(f"Cannot update client {user_client.client_email}: no ID found")
                            continue
                    else:
                        # Update inbound with modified clients list
                        # IMPORTANT: Update inbound after EACH client modification to ensure changes are applied
                        await connection.api.inbound.update(inbound_id, inbound)
                        logger.debug(f"Updated inbound with new expiry for client {user_client.client_email}")
                        # Small delay to allow X-UI to process the update
                        import asyncio
                        await asyncio.sleep(0.5)
                    
                    # Update UserClient record in database
                    async with self.session() as session:
                        from sqlalchemy import select
                        user_client_query = await session.execute(
                            select(UserClient).where(UserClient.id == user_client.id)
                        )
                        db_user_client = user_client_query.scalar_one_or_none()
                        if db_user_client:
                            db_user_client.expires_at = datetime.fromtimestamp(new_expiry_time / 1000)
                            await session.commit()
                            logger.debug(f"Updated UserClient {user_client.id} expiry to {db_user_client.expires_at}")
                    
                    success_count += 1
                    logger.debug(f"Extended client {user_client.client_email} by {duration} days")
                    
                except Exception as e:
                    logger.error(f"Error extending client {user_client.client_email}: {e}", exc_info=True)
                    continue
            
            if success_count == len(user_clients):
                logger.info(f"Successfully extended all {success_count} clients for user {user.tg_id}")
                return True
            elif success_count > 0:
                logger.warning(f"Extended {success_count}/{len(user_clients)} clients for user {user.tg_id}")
                return True  # Partial success is still considered success
            else:
                logger.error(f"Failed to extend any clients for user {user.tg_id}")
                return False
                
        except Exception as e:
            logger.error(f"Unexpected error extending multiple clients for user {user.tg_id}: {e}", exc_info=True)
            return False

    async def _change_multiple_clients(
        self, user: User, user_clients: list, new_devices: int, duration: int
    ) -> bool:
        """
        Change subscription for multiple clients (recreate with new device count).
        This deletes old clients and creates new ones.
        
        Args:
            user: User object
            user_clients: List of UserClient records (old clients)
            new_devices: New number of devices
            duration: Duration in days
        
        Returns:
            True if successful, False otherwise
        """
        logger.info(f"Changing subscription for user {user.tg_id} from {len(user_clients)} to {new_devices} devices, duration={duration} days")
        
        # Get current server_id from first client
        if not user_clients or not user_clients[0].server_id:
            logger.error(f"User {user.tg_id} has no server_id for subscription change")
            return False
        
        server_id = user_clients[0].server_id
        
        # Delete old clients
        connection = await self.server_pool_service.get_connection(user)
        if not connection:
            logger.error(f"Failed to get connection for user {user.tg_id}")
            return False
        
        try:
            inbound_id = await self.server_pool_service.get_inbound_id(connection.api)
            if not inbound_id:
                logger.error(f"Failed to get inbound ID for user {user.tg_id}")
                return False
            
            # Delete all old clients from X-UI
            client_ids_to_delete = []
            for user_client in user_clients:
                try:
                    client = await connection.api.client.get_by_email(user_client.client_email)
                    if client:
                        client_ids_to_delete.append(client.id)
                except Exception as e:
                    logger.warning(f"Error getting client {user_client.client_email} for deletion: {e}")
            
            if client_ids_to_delete:
                await connection.api.client.delete(inbound_id, client_ids_to_delete)
                logger.info(f"Deleted {len(client_ids_to_delete)} old clients from X-UI")
            
            # Delete UserClient records from database
            async with self.session() as session:
                for user_client in user_clients:
                    await session.delete(user_client)
                await session.commit()
                logger.info(f"Deleted {len(user_clients)} UserClient records from database")
            
            # Create new clients with new device count
            result = await self.create_multiple_clients(
                user=user,
                devices=new_devices,
                duration=duration,
                server_id=server_id,
            )
            
            if result:
                logger.info(f"Successfully changed subscription for user {user.tg_id} to {new_devices} devices")
            else:
                logger.error(f"Failed to create new clients for user {user.tg_id}")
            
            return result
            
        except Exception as e:
            logger.error(f"Unexpected error changing multiple clients for user {user.tg_id}: {e}", exc_info=True)
            return False

    async def process_bonus_days(self, user: User, duration: int, devices: int) -> bool:
        """
        Add bonus days to user subscription.
        For multiple clients: extends expiry time for all clients.
        For single client: extends expiry time or creates client if doesn't exist.
        """
        # Check if user has multiple clients (new schema)
        async with self.session() as session:
            from sqlalchemy import select
            user_query = await session.execute(
                select(User).where(User.tg_id == user.tg_id)
            )
            user_with_id = user_query.scalar_one_or_none()
            if not user_with_id:
                logger.error(f"User {user.tg_id} not found for bonus days")
                return False
            
            user_clients = await UserClient.get_by_user_id(session=session, user_id=user_with_id.id)
            
            if user_clients:
                # New schema: extend all clients
                logger.info(f"Adding {duration} bonus days to {len(user_clients)} clients for user {user.tg_id}")
                return await self._extend_multiple_clients(user, user_clients, duration)
            else:
                # Old schema: single client - use existing logic
                if await self.is_client_exists(user):
                    updated = await self.update_client(user=user, devices=0, duration=duration)
                    if updated:
                        logger.info(f"Updated client {user.tg_id} with additional {duration} days(-s).")
                        return True
                else:
                    created = await self.create_client(user=user, devices=devices, duration=duration)
                    if created:
                        logger.info(f"Created client {user.tg_id} with additional {duration} days(-s)")
                        return True

        return False

    async def activate_promocode(self, user: User, promocode: Promocode) -> bool:
        """
        Activate promocode for user.
        
        Note: This method could be moved to a dedicated PromocodeService in the future
        for better separation of concerns. Current implementation is functional and stable.
        """

        async with self.session() as session:
            activated = await Promocode.set_activated(
                session=session,
                code=promocode.code,
                user_id=user.tg_id,
            )

        if not activated:
            logger.critical(f"Failed to activate promocode {promocode.code} for user {user.tg_id}.")
            return False

        logger.info(f"Begun applying promocode ({promocode.code}) to a client {user.tg_id}.")
        success = await self.process_bonus_days(
            user,
            duration=promocode.duration,
            devices=self.config.shop.BONUS_DEVICES_COUNT,
        )

        if success:
            return True

        async with self.session() as session:
            await Promocode.set_deactivated(session=session, code=promocode.code)

        logger.warning(f"Promocode {promocode.code} not activated due to failure.")
        return False
