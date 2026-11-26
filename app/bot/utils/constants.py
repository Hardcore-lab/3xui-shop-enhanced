# region: Download
# iOS: V2BOX - популярный клиент для iOS
APP_IOS_LINK = "https://apps.apple.com/ru/app/v2box-v2ray-client/id6446814690"
# Android: V2BOX - популярный клиент для Android
APP_ANDROID_LINK = "https://play.google.com/store/apps/details?id=dev.hexasoftware.v2box&hl=ru"
# Windows: v2rayN - популярный клиент для Windows
APP_WINDOWS_LINK = "https://en.v2rayn.org/download/"

# Схемы для автоматического подключения через deep links (как v2raytun://import/<url> на скриншоте)
# Формат: app://import/<url> — приложение открывается и импортирует конфиг по URL (подписка или vless://)
# Для v2box пробуем v2box://import/; если приложение не поддерживает, запасной вариант — редирект на URL подписки (scheme=direct)
V2BOX_IMPORT_SCHEME = "v2box://import/"
# Кнопка «Подключиться»: iOS/Android — v2box, Windows — тот же (или редирект на подписку)
APP_IOS_SCHEME = V2BOX_IMPORT_SCHEME
APP_ANDROID_SCHEME = V2BOX_IMPORT_SCHEME
APP_WINDOWS_SCHEME = V2BOX_IMPORT_SCHEME
# Прямой редирект на URL подписки (для «Открыть в приложении» в браузере)
CONNECT_SCHEME_DIRECT = "direct"

# endregion

# region: Keys
MAIN_MESSAGE_ID_KEY = "main_message_id"
PREVIOUS_CALLBACK_KEY = "previous_callback"

INPUT_PROMOCODE_KEY = "input_promocode"

SERVER_NAME_KEY = "server_name"
SERVER_HOST_KEY = "server_host"
SERVER_MAX_CLIENTS_KEY = "server_max_clients"
SERVER_USERNAME_KEY = "server_username"
SERVER_PASSWORD_KEY = "server_password"
SERVER_LOCATION_KEY = "server_location"

NOTIFICATION_CHAT_IDS_KEY = "notification_chat_ids"
NOTIFICATION_LAST_MESSAGE_IDS_KEY = "notification_last_message_ids"
NOTIFICATION_MESSAGE_TEXT_KEY = "notification_message_text"
NOTIFICATION_PRE_MESSAGE_TEXT_KEY = "notification_pre_message_text"
# endregion

# region: Webhook paths
TELEGRAM_WEBHOOK = "/webhook"  # Webhook path for Telegram bot updates
CONNECTION_WEBHOOK = "/connection"  # Webhook path for receiving connection requests
HELEKET_WEBHOOK = "/heleket"  # Webhook path for receiving Heleket payment notifications
# endregion

# region: Notification tags
BOT_STARTED_TAG = "#BotStarted"
BOT_STOPPED_TAG = "#BotStopped"
BACKUP_CREATED_TAG = "#BackupCreated"
EVENT_PAYMENT_SUCCEEDED_TAG = "#EventPaymentSucceeded"
EVENT_PAYMENT_CANCELED_TAG = "#EventPaymentCanceled"
# endregion

# region: I18n settings
DEFAULT_LANGUAGE = "en"
I18N_DOMAIN = "bot"
# endregion

# region: Constants
UNLIMITED = "∞"
DB_FORMAT = "sqlite3"
LOG_ZIP_ARCHIVE_FORMAT = "zip"
LOG_GZ_ARCHIVE_FORMAT = "gz"
MESSAGE_EFFECT_IDS = {
    "🔥": "5104841245755180586",
    "👍": "5107584321108051014",
    "👎": "5104858069142078462",
    "❤️": "5044134455711629726",
    "🎉": "5046509860389126442",
    "💩": "5046589136895476101",
}
# endregion

# region: Enums
from enum import Enum
from typing import Any, Optional


class TransactionStatus(Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    CANCELED = "canceled"
    REFUNDED = "refunded"


class Currency(Enum):
    RUB = ("RUB", "₽")
    USD = ("USD", "$")
    XTR = ("XTR", "★")

    @property
    def symbol(self) -> str:
        return self.value[1]

    @property
    def code(self) -> str:
        return self.value[0]

    @classmethod
    def from_code(cls, code: str) -> "Currency":
        code = code.upper()
        for currency in cls:
            if currency.code == code:
                return currency
        raise ValueError(f"Invalid currency code: {code}")


class ReferrerRewardType(Enum):
    DAYS = "days"
    MONEY = "money"  # Note: Consider using Currency enum when balance system is implemented

    @classmethod
    def from_str(cls, value: str) -> Optional["ReferrerRewardType"]:
        try:
            return cls[value.upper()]
        except KeyError:
            try:
                return cls(value.lower())
            except ValueError:
                return None


class ReferrerRewardLevel(Enum):
    FIRST_LEVEL = 1
    SECOND_LEVEL = 2

    @classmethod
    def from_value(cls, value: Any) -> Optional["ReferrerRewardLevel"]:
        try:
            return cls(int(value))
        except (ValueError, KeyError):
            return None


# endregion
