import asyncio
import logging
from dataclasses import dataclass
from functools import wraps
from typing import Callable, TypeVar

from py3xui import AsyncApi
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Config
from app.db.models import Server, User

logger = logging.getLogger(__name__)

T = TypeVar('T')


def retry_with_backoff(
    max_retries: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 10.0,
    exponential_base: float = 2.0,
    retryable_exceptions: tuple = (ConnectionError, TimeoutError),
):
    """
    Декоратор для retry логики с экспоненциальной задержкой.
    
    Args:
        max_retries: Максимальное количество попыток
        initial_delay: Начальная задержка в секундах
        max_delay: Максимальная задержка в секундах
        exponential_base: База для экспоненциального роста задержки
        retryable_exceptions: Кортеж исключений, при которых нужно повторять попытку
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            delay = initial_delay
            last_exception = None
            
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        logger.warning(
                            f"{func.__name__} failed (attempt {attempt + 1}/{max_retries}): {e}. "
                            f"Retrying in {delay:.2f}s..."
                        )
                        await asyncio.sleep(delay)
                        delay = min(delay * exponential_base, max_delay)
                    else:
                        logger.error(
                            f"{func.__name__} failed after {max_retries} attempts: {e}"
                        )
                        raise
                except Exception as e:
                    # Не повторяем для других исключений
                    logger.error(f"{func.__name__} failed with non-retryable error: {e}")
                    raise
            
            # Этот код не должен выполняться, но для type checker
            if last_exception:
                raise last_exception
            raise RuntimeError(f"{func.__name__} failed unexpectedly")
        
        return wrapper
    return decorator


@dataclass
class Connection:
    server: Server
    api: AsyncApi


class ServerPoolService:
    def __init__(self, config: Config, session: async_sessionmaker) -> None:
        self.config = config
        self.session = session
        self._servers: dict[int, Connection] = {}
        logger.info("Server Pool Service initialized.")

    async def _add_server(self, server: Server) -> None:
        if server.id not in self._servers:
            # Используем индивидуальные учетные данные, если они указаны, иначе общие из конфигурации
            username = server.username if server.username else self.config.xui.USERNAME
            password = server.password if server.password else self.config.xui.PASSWORD
            
            api = AsyncApi(
                host=server.host,
                username=username,
                password=password,
                token=self.config.xui.TOKEN,
                # use_tls_verify=False,
                logger=logging.getLogger(f"xui_{server.name}"),
            )
            try:
                await api.login()
                server.online = True
                server_conn = Connection(server=server, api=api)
                self._servers[server.id] = server_conn
                logger.info(f"Server {server.name} ({server.host}) added to pool successfully.")
            except (ConnectionError, TimeoutError) as exception:
                server.online = False
                logger.error(f"Network error adding server {server.name} ({server.host}): {exception}")
            except ValueError as exception:
                server.online = False
                logger.error(f"Invalid credentials for server {server.name} ({server.host}): {exception}")
            except Exception as exception:
                server.online = False
                logger.error(f"Unexpected error adding server {server.name} ({server.host}): {exception}", exc_info=True)

            async with self.session() as session:
                await Server.update(session=session, name=server.name, online=server.online)

    def _remove_server(self, server: Server) -> None:
        if server.id in self._servers:
            try:
                del self._servers[server.id]
            except Exception as exception:
                logger.error(f"Failed to remove server {server.name}: {exception}")

    async def refresh_server(self, server: Server) -> None:
        """Обновить статус сервера и синхронизировать с X-UI в реальном времени."""
        # Проверяем подключение к серверу
        if server.id in self._servers:
            conn = self._servers[server.id]
            try:
                # Проверяем реальный статус через ping API с retry
                await self._check_server_health(conn.api)
                server.online = True
                logger.debug(f"Server {server.name}: online status confirmed via API")
            except (ConnectionError, TimeoutError) as e:
                server.online = False
                logger.warning(f"Server {server.name}: offline - {e}")
            except Exception as e:
                server.online = False
                logger.warning(f"Server {server.name}: offline - {e}", exc_info=True)
        else:
            # Сервер не в пуле, пытаемся добавить
            await self._add_server(server)
        
    @retry_with_backoff(max_retries=2, initial_delay=0.5)
    async def _check_server_health(self, api: AsyncApi) -> None:
        """Проверить здоровье сервера через API."""
        await api.inbound.get_list()
        # Health check passed - server is online

    @retry_with_backoff(max_retries=3, initial_delay=1.0)
    async def get_inbound_id(self, api: AsyncApi) -> int | None:
        """
        Получить ID первого inbound с retry логикой.
        
        Returns:
            ID первого inbound или None если не найден
        """
        try:
            inbounds = await api.inbound.get_list()
            if not inbounds:
                logger.warning("No inbounds found on server")
                return None
            return inbounds[0].id
        except (ConnectionError, TimeoutError) as exception:
            logger.error(f"Network error fetching inbounds: {exception}")
            raise  # Пробрасываем для retry
        except IndexError as exception:
            logger.error(f"No inbounds found: {exception}")
            return None
        except Exception as exception:
            logger.error(f"Unexpected error fetching inbounds: {exception}", exc_info=True)
            return None

    async def get_connection(self, user: User) -> Connection | None:
        if not user.server_id:
            logger.debug(f"User {user.tg_id} not assigned to any server.")
            return None

        connection = self._servers.get(user.server_id)

        if not connection:
            available_servers = list(self._servers.keys())
            logger.critical(
                f"Server {user.server_id} not found in pool. "
                f"User assigned server: {user.server_id}, "
                f"Available servers in pool: {available_servers}"
            )

            async with self.session() as session:
                server = await Server.get_by_id(session=session, id=user.server_id)

            if server:
                logger.debug(f"Server {server.name} ({server.host}) found in database. Attempting to add to pool.")
                # Пытаемся добавить сервер в пул
                await self._add_server(server)
                # Проверяем, добавился ли сервер
                connection = self._servers.get(user.server_id)
                if connection:
                    logger.info(f"Server {server.name} successfully added to pool and connection retrieved.")
                    async with self.session() as session:
                        server = await Server.get_by_id(session=session, id=user.server_id)
                    connection.server = server
                    return connection
                else:
                    logger.warning(f"Server {server.name} could not be added to pool (offline or connection failed).")
            else:
                logger.error(f"Server {user.server_id} not found in database.")

            return None

        async with self.session() as session:
            server = await Server.get_by_id(session=session, id=user.server_id)

        connection.server = server
        return connection

    async def sync_servers(self) -> None:
        """Полная синхронизация серверов: статусы, количество клиентов из X-UI в БД."""
        async with self.session() as session:
            db_servers = await Server.get_all(session)

        if not db_servers and not self._servers:
            logger.warning("No servers found in the database.")
            return

        db_server_map = {server.id: server for server in db_servers}

        # Удаляем серверы, которых нет в БД
        for server_id in list(self._servers.keys()):
            if server_id not in db_server_map:
                self._remove_server(self._servers[server_id].server)

        # Обновляем существующие серверы - синхронизируем статусы из X-UI в реальном времени
        for server_id, conn in list(self._servers.items()):
            if db_server := db_server_map.get(server_id):
                conn.server = db_server
                # Проверяем реальный статус через API (real-time sync)
                try:
                    await conn.api.inbound.get_list()
                    db_server.online = True
                    logger.debug(f"Server {db_server.name}: online (real-time sync)")
                except Exception as e:
                    db_server.online = False
                    logger.warning(f"Server {db_server.name}: offline - {e}")
                await self.refresh_server(db_server)

        # Добавляем новые серверы из БД
        for server in db_servers:
            if server.id not in self._servers:
                await self._add_server(server)
                # После добавления синхронизируем количество клиентов
                await self.refresh_server(server)

        logger.info(f"Sync complete. Currently active servers: {len(self._servers)}")

    async def assign_server_to_user(self, user: User) -> None:
        async with self.session() as session:
            server = await self.get_available_server()
            user.server_id = server.id
            await User.update(session=session, tg_id=user.tg_id, server_id=server.id)

    async def assign_specific_server_to_user(self, user: User, server_id: int) -> bool:
        """
        Назначить конкретный сервер пользователю (без коммита в БД - только в памяти).
        Returns True if successful, False otherwise.
        """
        async with self.session() as session:
            server = await Server.get_by_id(session=session, id=server_id)
            if not server:
                logger.error(f"Server {server_id} not found for user {user.tg_id}")
                return False
            
            # Синхронизируем статус сервера перед проверкой
            await self.refresh_server(server)
            server = await Server.get_by_id(session=session, id=server_id)
            
            if not server.online:
                logger.warning(f"Server {server.name} is offline, falling back to available server for user {user.tg_id}")
                # Fallback to available server if requested server is offline
                available_server = await self.get_available_server()
                if available_server:
                    user.server_id = available_server.id
                    logger.info(f"Assigned fallback server {available_server.name} (ID: {available_server.id}) to user {user.tg_id} (in memory)")
                    return True
                else:
                    logger.error(f"No available servers found for user {user.tg_id}")
                    return False
            
            if server.current_clients >= server.max_clients:
                logger.warning(
                    f"Server {server.name} is full ({server.current_clients}/{server.max_clients}), "
                    f"falling back to available server for user {user.tg_id}"
                )
                # Fallback to available server if requested server is full
                available_server = await self.get_available_server()
                if available_server:
                    user.server_id = available_server.id
                    logger.info(f"Assigned fallback server {available_server.name} (ID: {available_server.id}) to user {user.tg_id} (in memory)")
                    return True
                else:
                    logger.error(f"No available servers found for user {user.tg_id}")
                    return False
            
            # Обновляем только в памяти, не коммитим в БД
            # Коммит будет выполнен в change_user_server после успешного создания клиента
            user.server_id = server.id
            logger.info(f"Assigned server {server.name} (ID: {server_id}) to user {user.tg_id} (in memory)")
            return True

    async def get_available_server(self) -> Server | None:
        await self.sync_servers()

        servers_with_free_slots = [
            conn.server
            for conn in self._servers.values()
            if conn.server.current_clients < conn.server.max_clients
        ]

        if servers_with_free_slots:
            server = sorted(servers_with_free_slots, key=lambda s: s.current_clients)[0]
            logger.debug(
                f"Found server with free slots: {server.name} "
                f"(clients: {server.current_clients}/{server.max_clients})"
            )
            return server

        servers_least_loaded = [conn.server for conn in self._servers.values()]
        if servers_least_loaded:
            server = sorted(servers_least_loaded, key=lambda s: s.current_clients)[0]
            logger.warning(
                f"No servers with free slots. Using least loaded server: {server.name} "
                f"(clients: {server.current_clients}/{server.max_clients})"
            )
            return server

        logger.critical("No available servers found in pool")
        return None
