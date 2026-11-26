"""
Сервис для экспорта данных в CSV формат.
"""
import csv
import io
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.utils.constants import TransactionStatus
from app.db.models import Server, Transaction, User

logger = logging.getLogger(__name__)


class ExportService:
    """Сервис для экспорта данных."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def export_users(
        self,
        users: list[User],
        include_transactions: bool = False,
        include_server: bool = True,
    ) -> str:
        """
        Экспортировать пользователей в CSV формат.
        
        Args:
            users: Список пользователей для экспорта
            include_transactions: Включить информацию о транзакциях
            include_server: Включить информацию о сервере
        
        Returns:
            CSV строка с данными пользователей
        """
        try:
            output = io.StringIO()
            writer = csv.writer(output)
            
            # Заголовки
            headers = [
                "ID",
                "Telegram ID",
                "Username",
                "First Name",
                "Language",
                "Created At",
                "Trial Used",
                "Source Invite",
            ]
            
            if include_server:
                headers.extend(["Server ID", "Server Name", "Server Location"])
            
            if include_transactions:
                headers.extend(["Transactions Count", "Last Transaction Date"])
            
            writer.writerow(headers)
            
            # Данные
            for user in users:
                row = [
                    user.id,
                    user.tg_id,
                    user.username or "",
                    user.first_name,
                    user.language_code,
                    user.created_at.isoformat() if user.created_at else "",
                    user.is_trial_used,
                    user.source_invite_name or "",
                ]
                
                if include_server:
                    if user.server:
                        row.extend([
                            user.server.id,
                            user.server.name,
                            user.server.location or "",
                        ])
                    else:
                        row.extend(["", "", ""])
                
                if include_transactions:
                    transactions = await self._get_user_transactions_count(user)
                    row.extend([
                        transactions["count"],
                        transactions["last_date"] or "",
                    ])
                
                writer.writerow(row)
            
            csv_data = output.getvalue()
            output.close()
            
            logger.info(f"Exported {len(users)} users to CSV")
            return csv_data
            
        except Exception as e:
            logger.error(f"Error exporting users: {e}")
            return ""

    async def export_transactions(
        self,
        transactions: list[Transaction],
        include_user_info: bool = True,
    ) -> str:
        """
        Экспортировать транзакции в CSV формат.
        
        Args:
            transactions: Список транзакций для экспорта
            include_user_info: Включить информацию о пользователе
        
        Returns:
            CSV строка с данными транзакций
        """
        try:
            output = io.StringIO()
            writer = csv.writer(output)
            
            # Заголовки
            headers = [
                "ID",
                "Payment ID",
                "Status",
                "Created At",
                "Updated At",
            ]
            
            if include_user_info:
                headers.extend([
                    "User Telegram ID",
                    "User Username",
                    "User First Name",
                ])
            
            headers.append("Subscription Data")
            
            writer.writerow(headers)
            
            # Данные
            for transaction in transactions:
                row = [
                    transaction.id,
                    transaction.payment_id,
                    transaction.status.value,
                    transaction.created_at.isoformat() if transaction.created_at else "",
                    transaction.updated_at.isoformat() if transaction.updated_at else "",
                ]
                
                if include_user_info:
                    if transaction.user:
                        row.extend([
                            transaction.user.tg_id,
                            transaction.user.username or "",
                            transaction.user.first_name,
                        ])
                    else:
                        row.extend(["", "", ""])
                
                row.append(transaction.subscription)
                
                writer.writerow(row)
            
            csv_data = output.getvalue()
            output.close()
            
            logger.info(f"Exported {len(transactions)} transactions to CSV")
            return csv_data
            
        except Exception as e:
            logger.error(f"Error exporting transactions: {e}")
            return ""

    async def export_statistics(
        self,
        users_stats: dict[str, Any],
        subscriptions_stats: dict[str, Any],
        revenue_stats: dict[str, float],
        servers_stats: dict[str, Any],
    ) -> str:
        """
        Экспортировать статистику в CSV формат.
        
        Args:
            users_stats: Статистика пользователей
            subscriptions_stats: Статистика подписок
            revenue_stats: Статистика доходов
            servers_stats: Статистика серверов
        
        Returns:
            CSV строка со статистикой
        """
        try:
            output = io.StringIO()
            writer = csv.writer(output)
            
            # Заголовок
            writer.writerow(["Category", "Metric", "Value"])
            
            # Пользователи
            writer.writerow(["Users", "Total", users_stats.get("total", 0)])
            writer.writerow(["Users", "Today", users_stats.get("today", 0)])
            
            # Подписки
            writer.writerow([
                "Subscriptions",
                "Active",
                subscriptions_stats.get("active", 0),
            ])
            writer.writerow([
                "Subscriptions",
                "Expired",
                subscriptions_stats.get("expired", 0),
            ])
            writer.writerow([
                "Subscriptions",
                "Total",
                subscriptions_stats.get("total", 0),
            ])
            
            # Доходы
            for currency, amount in revenue_stats.items():
                writer.writerow(["Revenue", currency, f"{amount:.2f}"])
            
            # Серверы
            writer.writerow(["Servers", "Online", servers_stats.get("online", 0)])
            writer.writerow(["Servers", "Total", servers_stats.get("total", 0)])
            writer.writerow(["Servers", "Clients", servers_stats.get("clients", 0)])
            writer.writerow(["Servers", "Max Clients", servers_stats.get("max", 0)])
            writer.writerow([
                "Servers",
                "Load Percent",
                servers_stats.get("load_percent", 0),
            ])
            
            csv_data = output.getvalue()
            output.close()
            
            logger.info("Exported statistics to CSV")
            return csv_data
            
        except Exception as e:
            logger.error(f"Error exporting statistics: {e}")
            return ""

    async def _get_user_transactions_count(self, user: User) -> dict[str, Any]:
        """Получить количество транзакций пользователя и дату последней."""
        try:
            query = select(Transaction).where(Transaction.tg_id == user.tg_id)
            result = await self.session.execute(query)
            transactions = result.scalars().all()
            
            last_date = None
            if transactions:
                last_transaction = max(transactions, key=lambda t: t.created_at)
                last_date = last_transaction.created_at.isoformat() if last_transaction.created_at else None
            
            return {
                "count": len(transactions),
                "last_date": last_date,
            }
        except Exception as e:
            logger.error(f"Error getting transactions count for user {user.tg_id}: {e}")
            return {"count": 0, "last_date": None}

