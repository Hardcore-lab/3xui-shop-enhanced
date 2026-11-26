from aiogram import Dispatcher
from aiogram.utils.i18n import I18n
from sqlalchemy.ext.asyncio import async_sessionmaker

from .database import DBSessionMiddleware
from .garbage import GarbageMiddleware
from .i18n import CustomI18nMiddleware
from .maintenance import MaintenanceMiddleware
from .throttling import ThrottlingMiddleware


def register(dispatcher: Dispatcher, i18n: I18n, session: async_sessionmaker) -> None:
    # IMPORTANT: Order matters!
    # DBSessionMiddleware must come BEFORE CustomI18nMiddleware
    # so that CustomI18nMiddleware can use the locale from data dict (set by DBSessionMiddleware)
    middlewares = [
        ThrottlingMiddleware(),
        GarbageMiddleware(),
        DBSessionMiddleware(session),  # Load user from DB and set locale in data dict
        CustomI18nMiddleware(i18n),  # Use locale from data dict (set by DBSessionMiddleware)
        MaintenanceMiddleware(),
    ]

    for middleware in middlewares:
        dispatcher.update.middleware.register(middleware)
