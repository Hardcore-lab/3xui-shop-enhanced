import logging
from hmac import compare_digest
from urllib.parse import quote

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.utils.i18n import gettext as _
from aiohttp.web import HTTPFound, Request, Response

from app.bot.models import ServicesContainer
from app.bot.utils.constants import (
    APP_ANDROID_SCHEME,
    APP_IOS_SCHEME,
    APP_WINDOWS_SCHEME,
    CONNECT_SCHEME_DIRECT,
    MAIN_MESSAGE_ID_KEY,
    PREVIOUS_CALLBACK_KEY,
)
from app.bot.utils.navigation import NavDownload, NavMain
from app.bot.utils.network import connection_signature, connection_url
from app.config import Config
from app.db.models import User
from app.bot.routers.profile.keyboard import subscription_link_choice_keyboard

from .keyboard import download_keyboard, platforms_keyboard

logger = logging.getLogger(__name__)
router = Router(name=__name__)

ALLOWED_IMPORT_SCHEMES = {APP_IOS_SCHEME, APP_ANDROID_SCHEME, APP_WINDOWS_SCHEME}


async def redirect_to_connection(request: Request, *, secret: str) -> Response:
    scheme = request.query.get("scheme")
    key = request.query.get("key")
    signature = request.query.get("sig")

    if not scheme or not key or not signature or not signature.isascii():
        return Response(status=400, reason="Invalid parameters.")
    if not compare_digest(signature, connection_signature(secret, scheme, key)):
        return Response(status=403, reason="Invalid signature.")

    if scheme == CONNECT_SCHEME_DIRECT:
        raise HTTPFound(location=key)

    if scheme in ALLOWED_IMPORT_SCHEMES:
        key_encoded = quote(key, safe="")
        redirect_url = f"{scheme}{key_encoded}"
        raise HTTPFound(redirect_url)

    logger.debug("redirect_to_connection: unsupported scheme=%r", scheme)
    return Response(status=400, reason="Unsupported application.")


@router.callback_query(F.data == NavDownload.MAIN)
async def callback_download(callback: CallbackQuery, user: User, state: FSMContext) -> None:
    logger.info(f"User {user.tg_id} opened download apps page.")

    main_message_id = await state.get_value(MAIN_MESSAGE_ID_KEY)
    previous_callback = await state.get_value(PREVIOUS_CALLBACK_KEY)

    logger.debug("--------------------------------")
    logger.debug(f"callback.message.message_id: {callback.message.message_id}")
    logger.debug(f"main_message_id: {main_message_id}")
    logger.debug(f"previous_callback: {previous_callback}")
    logger.debug("--------------------------------")
    if callback.message.message_id != main_message_id:
        await state.update_data({PREVIOUS_CALLBACK_KEY: NavMain.MAIN_MENU})
        previous_callback = NavMain.MAIN_MENU
        await callback.bot.edit_message_text(
            text=_("download:message:choose_platform"),
            chat_id=user.tg_id,
            message_id=main_message_id,
            reply_markup=platforms_keyboard(previous_callback),
        )
    else:
        await callback.message.edit_text(
            text=_("download:message:choose_platform"),
            reply_markup=platforms_keyboard(previous_callback),
        )


@router.callback_query(F.data.startswith(NavDownload.PLATFORM))
async def callback_platform(
    callback: CallbackQuery,
    user: User,
    services: ServicesContainer,
    config: Config,
) -> None:
    logger.info(f"User {user.tg_id} selected platform: {callback.data}")
    keys_per_client = await services.vpn.get_subscription_keys_per_client(user)
    base = (config.bot.public_base_url() or "").rstrip("/")
    subscription_links: list[tuple[int, str]] | None = None
    key: str | None = None
    if keys_per_client and base:
        subscription_links = [
            (idx, connection_url(base, k, config.bot.TOKEN))
            for idx, k in keys_per_client
        ]
    elif keys_per_client and len(keys_per_client) == 1:
        key = keys_per_client[0][1]
    if not subscription_links and key is None:
        key = await services.vpn.get_key(user)

    match callback.data:
        case NavDownload.PLATFORM_IOS:
            platform = _("download:message:platform_ios")
        case NavDownload.PLATFORM_ANDROID:
            platform = _("download:message:platform_android")
        case _:
            platform = _("download:message:platform_windows")

    await callback.message.edit_text(
        text=_("download:message:connect_to_vpn").format(platform=platform),
        reply_markup=download_keyboard(
            platform=callback.data,
            subscription_url=connection_url(base, key, config.bot.TOKEN) if base and key else None,
            subscription_links=subscription_links,
        ),
    )


@router.callback_query(F.data == NavDownload.SUBSCRIPTION_LINK)
async def callback_download_subscription_link(
    callback: CallbackQuery,
    user: User,
    services: ServicesContainer,
    config: Config,
) -> None:
    """Show subscription link choice by device (download flow); Back returns to platform choice."""
    await callback.answer()
    keys_per_client = await services.vpn.get_subscription_keys_per_client(user)
    if not keys_per_client:
        return
    base = (config.bot.public_base_url() or "").rstrip("/")
    if not base:
        return
    links = [
        (idx, connection_url(base, k, config.bot.TOKEN))
        for idx, k in keys_per_client
    ]
    await callback.message.edit_text(
        text=_("profile:message:subscription_link_choice"),
        reply_markup=subscription_link_choice_keyboard(links, back_callback=NavDownload.MAIN),
    )
