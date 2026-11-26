"""Exercise subscription provisioning through the payment success handler."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.utils.i18n import I18n

from app.bot.models import SubscriptionData
from app.bot.payment_gateways._gateway import PaymentGateway
from app.bot.utils.constants import TransactionStatus
from app.bot.utils.navigation import NavSubscription
from app.db.models import Transaction, User


@pytest.mark.asyncio
@pytest.mark.parametrize(("devices", "provisioned"), [(1, True), (2, True), (1, False)])
async def test_purchase_flow(monkeypatch, devices, provisioned):
    user = User(tg_id=123456789, language_code="en")
    subscription = SubscriptionData(
        state=NavSubscription.PAY, user_id=user.tg_id, devices=devices, duration=30, price=100
    )
    transaction = Transaction(subscription=subscription.pack(), status=TransactionStatus.PENDING)
    keys = [{"index": device, "key": f"https://example.com/sub/{device}"}
            for device in range(1, devices + 1)] if provisioned else []

    session = MagicMock(commit=AsyncMock())
    session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=user)))

    @asynccontextmanager
    async def session_factory():
        yield session

    gateway = MagicMock()
    gateway.session = session_factory
    gateway.i18n = I18n.get_current()
    gateway.config.shop.REFERRER_REWARD_ENABLED = False
    gateway.services.vpn.session = session_factory
    gateway.services.vpn.create_subscription = AsyncMock(return_value=provisioned)
    gateway.services.vpn.get_all_user_keys = AsyncMock(return_value=keys)
    gateway.services.notification.notify_developer = AsyncMock()
    gateway.services.notification.notify_purchase_success = AsyncMock()
    monkeypatch.setattr(Transaction, "get_by_id_for_update", AsyncMock(return_value=transaction))
    monkeypatch.setattr(User, "get", AsyncMock(return_value=user))
    monkeypatch.setattr("app.bot.payment_gateways._gateway.redirect_to_main_menu", AsyncMock())

    await PaymentGateway._on_payment_succeeded(gateway, "payment-123")
    await PaymentGateway._on_payment_succeeded(gateway, "payment-123")

    assert transaction.status == TransactionStatus.COMPLETED
    session.commit.assert_awaited_once()
    gateway.services.vpn.create_subscription.assert_awaited_once_with(
        user=user, devices=devices, duration=30, server_id=None
    )
    gateway.services.vpn.get_all_user_keys.assert_awaited_once_with(user)
    notification = gateway.services.notification.notify_purchase_success
    if provisioned:
        notification.assert_awaited_once_with(user_id=user.tg_id, all_keys=keys)
    else:
        notification.assert_not_awaited()
