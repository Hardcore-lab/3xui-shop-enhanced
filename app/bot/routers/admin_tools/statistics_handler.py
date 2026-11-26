import logging
from datetime import datetime, timezone
from typing import Any, Dict

from aiogram import F, Router
from aiogram.types import CallbackQuery
from aiogram.utils.i18n import gettext as _
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import IsAdmin
from app.bot.models import ServicesContainer
from app.bot.utils.constants import TransactionStatus
from app.bot.utils.navigation import NavAdminTools, NavSubscription
from app.db.models import Server, Transaction, User

from .keyboard import statistics_keyboard

logger = logging.getLogger(__name__)
router = Router(name=__name__)

# Маппинг payment methods -> currencies для PaymentStatsService
PAYMENT_METHOD_CURRENCIES: Dict[str, str] = {
    NavSubscription.PAY_TELEGRAM_STARS: "XTR",
    NavSubscription.PAY_HELEKET: "USD",
}


async def _get_users_statistics(session: AsyncSession) -> Dict[str, int]:
    """Получить статистику пользователей."""
    try:
        # Общее количество пользователей
        total_users_query = await session.execute(select(func.count(User.id)))
        total_users = total_users_query.scalar() or 0

        # Новые пользователи за сегодня
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        today_users_query = await session.execute(
            select(func.count(User.id)).where(User.created_at >= today_start)
        )
        today_users = today_users_query.scalar() or 0

        return {
            "total": total_users,
            "today": today_users,
        }
    except Exception as e:
        logger.error(f"Error getting users statistics: {e}")
        return {"total": 0, "today": 0}


async def _get_subscriptions_statistics(session: AsyncSession) -> Dict[str, int]:
    """Получить статистику подписок."""
    try:
        # Пользователи с активной подпиской (имеют server_id)
        active_subscriptions_query = await session.execute(
            select(func.count(User.id)).where(User.server_id.isnot(None))
        )
        active_subscriptions = active_subscriptions_query.scalar() or 0

        # Всего пользователей
        total_users_query = await session.execute(select(func.count(User.id)))
        total_users = total_users_query.scalar() or 0

        # Пользователи, которые когда-либо имели подписку (имели завершенные транзакции)
        users_with_transactions_query = await session.execute(
            select(func.count(func.distinct(Transaction.tg_id))).where(
                Transaction.status == TransactionStatus.COMPLETED
            )
        )
        users_with_subscriptions = users_with_transactions_query.scalar() or 0

        # Истекшие подписки: пользователи, которые имели подписку, но сейчас не имеют активной
        expired_subscriptions = max(0, users_with_subscriptions - active_subscriptions)

        return {
            "active": active_subscriptions,
            "expired": expired_subscriptions,
            "total": total_users,
        }
    except Exception as e:
        logger.error(f"Error getting subscriptions statistics: {e}")
        return {"active": 0, "expired": 0, "total": 0}


async def _get_revenue_statistics(
    session: AsyncSession, services: ServicesContainer
) -> Dict[str, float]:
    """Получить статистику доходов."""
    try:
        revenue = await services.payment_stats.get_total_revenue_stats(
            session=session, payment_method_currencies=PAYMENT_METHOD_CURRENCIES
        )
        return revenue
    except Exception as e:
        logger.error(f"Error getting revenue statistics: {e}")
        return {}


async def _get_today_revenue(session: AsyncSession, services: ServicesContainer) -> Dict[str, float]:
    """Получить доход за сегодня по всем валютам."""
    try:
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        today_transactions_query = await session.execute(
            select(Transaction).where(
                Transaction.status == TransactionStatus.COMPLETED,
                Transaction.created_at >= today_start,
            )
        )
        today_transactions = today_transactions_query.scalars().all()

        revenue_by_currency: Dict[str, float] = {}
        for tx in today_transactions:
            try:
                from app.bot.models import SubscriptionData

                data = SubscriptionData.unpack(tx.subscription)
                payment_method = data.state.value

                currency = None
                for method, curr in PAYMENT_METHOD_CURRENCIES.items():
                    if method in payment_method:
                        currency = curr
                        break

                if currency:
                    if currency not in revenue_by_currency:
                        revenue_by_currency[currency] = 0.0
                    revenue_by_currency[currency] += float(data.price)
            except Exception as e:
                logger.warning(f"Could not parse transaction {tx.id}: {e}")

        return revenue_by_currency
    except Exception as e:
        logger.error(f"Error getting today revenue: {e}")
        return {}


async def _get_servers_statistics(session: AsyncSession) -> Dict[str, Any]:
    """Получить статистику серверов."""
    try:
        servers_query = await session.execute(select(Server))
        servers = servers_query.scalars().all()

        online_count = sum(1 for s in servers if s.online)
        total_servers = len(servers)
        total_max = sum(s.max_clients for s in servers)
        
        # Используем SQL запрос для подсчета клиентов вместо доступа к relationship
        # чтобы избежать ошибки greenlet_spawn
        total_clients_query = await session.execute(
            select(func.count(User.id)).where(User.server_id.isnot(None))
        )
        total_clients = total_clients_query.scalar() or 0
        
        load_percent = int((total_clients / total_max * 100)) if total_max > 0 else 0

        return {
            "online": online_count,
            "total": total_servers,
            "clients": total_clients,
            "max": total_max,
            "load_percent": load_percent,
        }
    except Exception as e:
        logger.error(f"Error getting servers statistics: {e}")
        return {"online": 0, "total": 0, "clients": 0, "max": 0, "load_percent": 0}


async def _get_referral_statistics(session: AsyncSession) -> Dict[str, int]:
    """Получить статистику рефералов."""
    try:
        # Пользователи с source_invite_name (пришли по реферальной ссылке)
        referral_users_query = await session.execute(
            select(func.count(User.id)).where(User.source_invite_name.isnot(None))
        )
        referral_users = referral_users_query.scalar() or 0

        # Пользователи с рефералами (отправили реферальные ссылки)
        from app.db.models.referral import Referral

        referrers_query = await session.execute(
            select(func.count(func.distinct(Referral.referrer_tg_id)))
        )
        referrers = referrers_query.scalar() or 0

        return {
            "active": referral_users,
            "referrers": referrers,
        }
    except Exception as e:
        logger.error(f"Error getting referral statistics: {e}")
        return {"active": 0, "referrers": 0}


@router.callback_query(F.data == NavAdminTools.STATISTICS, IsAdmin())
async def callback_statistics(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
    services: ServicesContainer,
) -> None:
    logger.info(f"Admin {user.tg_id} opened statistics.")

    try:
        # Собираем статистику
        users_stats = await _get_users_statistics(session)
        subscriptions_stats = await _get_subscriptions_statistics(session)
        revenue_stats = await _get_revenue_statistics(session, services)
        today_revenue_dict = await _get_today_revenue(session, services)
        servers_stats = await _get_servers_statistics(session)
        referral_stats = await _get_referral_statistics(session)

        # Подсчет активных подписок, созданных сегодня
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        today_active_query = await session.execute(
            select(func.count(func.distinct(Transaction.tg_id))).where(
                Transaction.status == TransactionStatus.COMPLETED,
                Transaction.created_at >= today_start,
            )
        )
        today_active = today_active_query.scalar() or 0

        # Формируем текст дашборда
        # Сегодняшний доход - сумма всех валют (или только RUB, если нужен один формат)
        today_revenue_total = sum(today_revenue_dict.values())
        today_revenue_text = f"{today_revenue_total:.2f}"
        if today_revenue_dict:
            # Показываем все валюты, если есть
            currency_parts = []
            for curr, amount in sorted(today_revenue_dict.items()):
                currency_parts.append(f"{curr} {amount:.2f}")
            if len(currency_parts) > 1:
                today_revenue_text = " / ".join(currency_parts)

        text = _("statistics:message:dashboard").format(
            today_users=users_stats["today"],
            today_active=today_active,
            today_revenue=today_revenue_text,
            total_users=users_stats["total"],
            active_subscriptions=subscriptions_stats["active"],
            active_percent=int((subscriptions_stats["active"] / subscriptions_stats["total"] * 100))
            if subscriptions_stats["total"] > 0
            else 0,
            expired_subscriptions=subscriptions_stats["expired"],
            revenue_rub=f"{revenue_stats.get('RUB', 0):.2f}",
            revenue_usd=f"{revenue_stats.get('USD', 0):.2f}",
            revenue_xtr=f"{revenue_stats.get('XTR', 0):.0f}",
            servers_online=servers_stats["online"],
            servers_total=servers_stats["total"],
            servers_clients=servers_stats["clients"],
            servers_max=servers_stats["max"],
            servers_load=servers_stats["load_percent"],
            referral_active=referral_stats["active"],
            referral_referrers=referral_stats["referrers"],
        )

        await callback.message.edit_text(
            text=text,
            reply_markup=statistics_keyboard(),
        )
    except Exception as e:
        logger.error(f"Error displaying statistics: {e}")
        await callback.answer(
            text=_("statistics:message:error"), show_alert=True
        )
