"""
Сервис для операций с клиентами X-UI.
Изолирует логику создания, удаления и миграции клиентов.
"""
import logging
from typing import TYPE_CHECKING

from py3xui import Client
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User, UserClient

if TYPE_CHECKING:
    from app.bot.models import ServicesContainer
    from app.bot.services.server_pool import ServerPoolService

logger = logging.getLogger(__name__)


class ClientOperationsService:
    """Сервис для операций с клиентами X-UI."""
    
    def __init__(
        self,
        server_pool_service: "ServerPoolService",
        session_factory,
    ) -> None:
        self.server_pool_service = server_pool_service
        self.session_factory = session_factory
        logger.info("Client Operations Service initialized.")
    
    async def get_client_ids(
        self,
        user: User,
        connection,
        user_clients: list[UserClient] | None = None,
    ) -> list[str | int]:
        """
        Получить ID клиентов для удаления.
        
        Args:
            user: Пользователь
            connection: Подключение к серверу
            user_clients: Список UserClient записей (для новой схемы)
        
        Returns:
            Список ID клиентов для удаления
        """
        client_ids = []
        
        try:
            inbound_id = await self.server_pool_service.get_inbound_id(connection.api)
            if not inbound_id:
                logger.warning(f"Could not get inbound ID for user {user.tg_id}")
                return []
            
            inbounds_list = await connection.api.inbound.get_list()
            if not inbounds_list or not inbounds_list[0].settings or not inbounds_list[0].settings.clients:
                return []
            
            inbound_clients = inbounds_list[0].settings.clients
            
            if user_clients:
                # Новая схема: ищем все клиенты пользователя
                for user_client in user_clients:
                    for client_in_inbound in inbound_clients:
                        if client_in_inbound.email == user_client.client_email:
                            client_id = getattr(client_in_inbound, 'id', None)
                            if client_id:
                                client_ids.append(client_id)
                                logger.debug(f"Found client ID {client_id} for {user_client.client_email}")
                            break
            else:
                # Старая схема: ищем один клиент по tg_id
                for client_in_inbound in inbound_clients:
                    if client_in_inbound.email == str(user.tg_id):
                        client_id = getattr(client_in_inbound, 'id', None)
                        if client_id:
                            client_ids.append(client_id)
                            logger.debug(f"Found client ID {client_id} for {user.tg_id}")
                        break
            
            return client_ids
        except Exception as e:
            logger.error(f"Error getting client IDs for user {user.tg_id}: {e}", exc_info=True)
            return []
    
    async def delete_clients(
        self,
        user: User,
        connection,
        user_clients: list[UserClient] | None = None,
        max_retries: int = 3,
    ) -> bool:
        """
        Удалить всех клиентов пользователя из X-UI.
        
        Args:
            user: Пользователь
            connection: Подключение к серверу
            user_clients: Список UserClient записей (для новой схемы)
            max_retries: Максимальное количество попыток
        
        Returns:
            True если успешно, False в противном случае
        """
        for attempt in range(1, max_retries + 1):
            try:
                client_ids = await self.get_client_ids(user, connection, user_clients)
                
                if not client_ids:
                    # Проверяем, что клиентов действительно нет
                    if await self.verify_clients_deleted(user, connection, user_clients):
                        logger.info(f"All clients already deleted for user {user.tg_id}")
                        return True
                    else:
                        logger.warning(f"Attempt {attempt}: Could not find client IDs, retrying...")
                        if attempt < max_retries:
                            import asyncio
                            await asyncio.sleep(1 * attempt)
                        continue
                
                inbound_id = await self.server_pool_service.get_inbound_id(connection.api)
                if not inbound_id:
                    logger.error(f"Could not get inbound ID for deletion attempt {attempt}")
                    if attempt < max_retries:
                        import asyncio
                        await asyncio.sleep(1 * attempt)
                    continue
                
                await connection.api.client.delete(inbound_id, client_ids)
                logger.info(f"Attempt {attempt}: Deleted {len(client_ids)} clients for user {user.tg_id}")
                
                # Проверяем успешность удаления
                if await self.verify_clients_deleted(user, connection, user_clients):
                    logger.info(f"Verified deletion of {len(client_ids)} clients for user {user.tg_id}")
                    return True
                else:
                    logger.warning(f"Attempt {attempt}: Deletion not verified, retrying...")
                    if attempt < max_retries:
                        import asyncio
                        await asyncio.sleep(1 * attempt)
            except Exception as e:
                logger.error(f"Attempt {attempt}: Error deleting clients for user {user.tg_id}: {e}", exc_info=True)
                if attempt < max_retries:
                    import asyncio
                    await asyncio.sleep(1 * attempt)
        
        logger.error(f"Failed to delete clients for user {user.tg_id} after {max_retries} attempts")
        return False
    
    async def verify_clients_deleted(
        self,
        user: User,
        connection,
        user_clients: list[UserClient] | None = None,
    ) -> bool:
        """
        Проверить, что клиенты действительно удалены из X-UI.
        
        Args:
            user: Пользователь
            connection: Подключение к серверу
            user_clients: Список UserClient записей (для новой схемы)
        
        Returns:
            True если все клиенты удалены, False если хотя бы один найден
        """
        try:
            inbounds_list = await connection.api.inbound.get_list()
            if not inbounds_list or not inbounds_list[0].settings or not inbounds_list[0].settings.clients:
                # Нет клиентов в inbound - все удалено
                return True
            
            inbound_clients = inbounds_list[0].settings.clients
            
            if user_clients:
                # Новая схема: проверяем все клиенты пользователя
                for user_client in user_clients:
                    for client_in_inbound in inbound_clients:
                        if client_in_inbound.email == user_client.client_email:
                            logger.warning(f"Client {user_client.client_email} still exists in X-UI")
                            return False
                return True
            else:
                # Старая схема: проверяем один клиент
                for client_in_inbound in inbound_clients:
                    if client_in_inbound.email == str(user.tg_id):
                        logger.warning(f"Client {user.tg_id} still exists in X-UI")
                        return False
                return True
        except Exception as e:
            logger.error(f"Error verifying client deletion: {e}", exc_info=True)
            # В случае ошибки считаем что проверка не прошла
            return False
    
    async def verify_clients_exist(
        self,
        user: User,
        connection,
        user_clients: list[UserClient] | None = None,
    ) -> bool:
        """
        Проверить, что клиенты существуют в X-UI.
        
        Args:
            user: Пользователь
            connection: Подключение к серверу
            user_clients: Список UserClient записей (для новой схемы)
        
        Returns:
            True если все клиенты существуют, False в противном случае
        """
        try:
            inbounds_list = await connection.api.inbound.get_list()
            if not inbounds_list or not inbounds_list[0].settings or not inbounds_list[0].settings.clients:
                return False
            
            inbound_clients = inbounds_list[0].settings.clients
            
            if user_clients:
                # Новая схема: проверяем все клиенты пользователя
                found_count = 0
                for user_client in user_clients:
                    for client_in_inbound in inbound_clients:
                        if client_in_inbound.email == user_client.client_email:
                            found_count += 1
                            break
                return found_count == len(user_clients)
            else:
                # Старая схема: проверяем один клиент
                for client_in_inbound in inbound_clients:
                    if client_in_inbound.email == str(user.tg_id):
                        return True
                return False
        except Exception as e:
            logger.error(f"Error verifying client existence: {e}", exc_info=True)
            return False

