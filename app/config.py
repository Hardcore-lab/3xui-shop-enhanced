import logging
from dataclasses import dataclass
from logging.handlers import MemoryHandler
from pathlib import Path
from urllib.parse import urlparse

from environs import Env
from marshmallow.validate import OneOf, Range

from app.bot.utils.constants import (
    DB_FORMAT,
    LOG_GZ_ARCHIVE_FORMAT,
    LOG_ZIP_ARCHIVE_FORMAT,
    Currency,
    ReferrerRewardType,
)

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = BASE_DIR / "data"
DEFAULT_LOCALES_DIR = BASE_DIR / "locales"
DEFAULT_PLANS_DIR = DEFAULT_DATA_DIR / "plans.json"

DEFAULT_BOT_HOST = "0.0.0.0"
DEFAULT_BOT_PORT = 8080
DEFAULT_BOT_DOMAIN = ""  # Development only; production must set BOT_DOMAIN

DEFAULT_SHOP_EMAIL = "admin@VPN.com"
DEFAULT_SHOP_CURRENCY = Currency.RUB.code
DEFAULT_SHOP_TRIAL_ENABLED = True
DEFAULT_SHOP_TRIAL_PERIOD = 3
DEFAULT_SHOP_REFERRED_TRIAL_ENABLED = False
DEFAULT_SHOP_REFERRED_TRIAL_PERIOD = 7
DEFAULT_SHOP_REFERRER_REWARD_ENABLED = True
DEFAULT_SHOP_REFERRER_REWARD_TYPE = ReferrerRewardType.DAYS.value
DEFAULT_SHOP_REFERRER_LEVEL_ONE_PERIOD = 10
DEFAULT_SHOP_REFERRER_LEVEL_TWO_PERIOD = 3
DEFAULT_SHOP_REFERRER_LEVEL_ONE_RATE = 50
DEFAULT_SHOP_REFERRER_LEVEL_TWO_RATE = 5
DEFAULT_SHOP_BONUS_DEVICES_COUNT = 1
DEFAULT_SHOP_PAYMENT_STARS_ENABLED = True
DEFAULT_SHOP_PAYMENT_HELEKET_ENABLED = False
DEFAULT_DB_NAME = "bot_database"

DEFAULT_HELEKET_WEBHOOK_IPS = ["31.133.220.8"]

DEFAULT_REDIS_DB_NAME = "0"
DEFAULT_REDIS_HOST = "3xui-shop-redis"
DEFAULT_REDIS_PORT = 6379

DEFAULT_SUBSCRIPTION_PORT = 2096
DEFAULT_SUBSCRIPTION_PATH = "/sub/"

DEFAULT_LOG_LEVEL = "DEBUG"
DEFAULT_LOG_FORMAT = "%(asctime)s | %(name)s | %(levelname)s | %(message)s"
DEFAULT_LOG_ARCHIVE_FORMAT = LOG_ZIP_ARCHIVE_FORMAT
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
memory_handler = MemoryHandler(capacity=100, flushLevel=logging.ERROR)
memory_handler.setFormatter(logging.Formatter(DEFAULT_LOG_FORMAT))
logger.addHandler(memory_handler)


@dataclass
class BotConfig:
    TOKEN: str
    ADMINS: list[int]
    DEV_ID: list[int]
    SUPPORT_ID: int
    DOMAIN: str
    PORT: int

    def public_base_url(self) -> str:
        """Normalized base URL for public links (connection, webhooks), no trailing slash."""
        return (self.DOMAIN or "").rstrip("/")


@dataclass
class ShopConfig:
    EMAIL: str
    CURRENCY: str
    TRIAL_ENABLED: bool
    TRIAL_PERIOD: int
    REFERRED_TRIAL_ENABLED: bool
    REFERRED_TRIAL_PERIOD: int
    REFERRER_REWARD_ENABLED: bool
    REFERRER_REWARD_TYPE: str
    REFERRER_LEVEL_ONE_PERIOD: int
    REFERRER_LEVEL_TWO_PERIOD: int
    REFERRER_LEVEL_ONE_RATE: int
    REFERRER_LEVEL_TWO_RATE: int
    BONUS_DEVICES_COUNT: int
    PAYMENT_STARS_ENABLED: bool
    PAYMENT_HELEKET_ENABLED: bool
    CUSTOM_OFFER_URL: str | None


@dataclass
class XUIConfig:
    USERNAME: str
    PASSWORD: str
    TOKEN: str | None
    SUBSCRIPTION_PORT: int
    SUBSCRIPTION_PATH: str


@dataclass
class HeleketConfig:
    API_KEY: str | None
    MERCHANT_ID: str | None
    WEBHOOK_IPS: list[str]

@dataclass
class DatabaseConfig:
    HOST: str | None
    PORT: int | None
    NAME: str
    USERNAME: str | None
    PASSWORD: str | None

    def url(self, driver: str = "sqlite+aiosqlite") -> str:
        if driver.startswith("sqlite"):
            return f"{driver}:////{DEFAULT_DATA_DIR}/{self.NAME}.{DB_FORMAT}"
        return f"{driver}://{self.USERNAME}:{self.PASSWORD}@{self.HOST}:{self.PORT}/{self.NAME}"


@dataclass
class RedisConfig:
    HOST: str
    PORT: int
    DB_NAME: str
    USERNAME: str | None
    PASSWORD: str | None

    def url(self) -> str:
        if self.USERNAME and self.PASSWORD:
            return f"redis://{self.USERNAME}:{self.PASSWORD}@{self.HOST}:{self.PORT}/{self.DB_NAME}"
        return f"redis://{self.HOST}:{self.PORT}/{self.DB_NAME}"


@dataclass
class LoggingConfig:
    LEVEL: str
    FORMAT: str
    ARCHIVE_FORMAT: str


@dataclass
class Config:
    bot: BotConfig
    shop: ShopConfig
    xui: XUIConfig
    heleket: HeleketConfig
    database: DatabaseConfig
    redis: RedisConfig
    logging: LoggingConfig


def _normalize_bot_domain(raw: str) -> str:
    """Normalize BOT_DOMAIN: strip, ensure https, no trailing slash. Returns empty string if invalid."""
    if not raw or not raw.strip():
        return raw.strip() if raw is not None else ""
    value = raw.strip()
    if not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    value = value.rstrip("/")
    parsed = urlparse(value)
    if not parsed.hostname:
        logger.warning(
            "BOT_DOMAIN has no valid hostname after normalization: %r. "
            "Connection and payment links may not work.",
            raw,
        )
    return value


def load_config() -> Config:
    env = Env()
    env.read_env()

    bot_admins = env.list("BOT_ADMINS", subcast=int, default=[], required=False)
    if not bot_admins:
        logger.warning("BOT_ADMINS list is empty.")

    bot_dev_ids = env.list("BOT_DEV_ID", subcast=int, default=[], required=False)
    if not bot_dev_ids:
        logger.warning("BOT_DEV_ID list is empty.")

    xui_token = env.str("XUI_TOKEN", default=None)
    if not xui_token:
        logger.warning("XUI_TOKEN is not set.")

    payment_stars_enabled = env.bool(
        "SHOP_PAYMENT_STARS_ENABLED",
        default=DEFAULT_SHOP_PAYMENT_STARS_ENABLED,
    )

    payment_heleket_enabled = env.bool(
        "SHOP_PAYMENT_HELEKET_ENABLED",
        default=DEFAULT_SHOP_PAYMENT_HELEKET_ENABLED,
    )
    heleket_webhook_ips = env.list(
        "HELEKET_WEBHOOK_IPS",
        default=DEFAULT_HELEKET_WEBHOOK_IPS,
    )
    if payment_heleket_enabled:
        heleket_api_key = env.str("HELEKET_API_KEY", default=None)
        heleket_merchant_id = env.str("HELEKET_MERCHANT_ID", default=None)
        if not heleket_api_key or not heleket_merchant_id:
            logger.error(
                "HELEKET_API_KEY or HELEKET_MERCHANT_ID is not set. Payment Heleket is disabled."
            )
            payment_heleket_enabled = False

    if (
        not payment_stars_enabled
        and not payment_heleket_enabled
    ):
        logger.warning("No payment methods are enabled. Enabling Stars payment method.")
        payment_stars_enabled = True

    bot_domain_raw = env.str("BOT_DOMAIN", default=DEFAULT_BOT_DOMAIN)
    if not bot_domain_raw or not bot_domain_raw.strip():
        logger.warning(
            "BOT_DOMAIN is not set or empty. Set it to your public bot domain (e.g. bot.example.com) "
            "for connection and payment links to work."
        )
    bot_domain = _normalize_bot_domain(bot_domain_raw or "")

    referrer_reward_type = env.str(
        "SHOP_REFERRED_REWARD_TYPE",
        default=DEFAULT_SHOP_REFERRER_REWARD_TYPE,
        validate=OneOf(
            [reward_type.value for reward_type in ReferrerRewardType],
            error="SHOP_REFERRER_REWARD_TYPE must be one of: {choices}",
        ),
    )
    referrer_reward_enabled = env.bool(
        "SHOP_REFERRER_REWARD_ENABLED", default=DEFAULT_SHOP_REFERRER_REWARD_ENABLED
    )
    if referrer_reward_type != ReferrerRewardType.DAYS.value:
        logger.error(
            "Only 'days' option is now available for SHOP_REFERRER_REWARD_TYPE. Referrer reward disabled."
        )
        referrer_reward_enabled = False

    return Config(
        bot=BotConfig(
            TOKEN=env.str("BOT_TOKEN"),
            ADMINS=bot_admins,
            DEV_ID=bot_dev_ids,
            SUPPORT_ID=env.int("BOT_SUPPORT_ID"),
            DOMAIN=bot_domain,
            PORT=env.int("BOT_PORT", default=DEFAULT_BOT_PORT),
        ),
        shop=ShopConfig(
            EMAIL=env.str("SHOP_EMAIL", default=DEFAULT_SHOP_EMAIL),
            CURRENCY=env.str(
                "SHOP_CURRENCY",
                default=DEFAULT_SHOP_CURRENCY,
                validate=OneOf(
                    [currency.code for currency in Currency]
                    + [currency.code.lower() for currency in Currency],
                    error="SHOP_CURRENCY must be one of: {choices}",
                ),
            ).upper(),
            TRIAL_ENABLED=env.bool("SHOP_TRIAL_ENABLED", default=DEFAULT_SHOP_TRIAL_ENABLED),
            TRIAL_PERIOD=env.int("SHOP_TRIAL_PERIOD", default=DEFAULT_SHOP_TRIAL_PERIOD),
            REFERRED_TRIAL_ENABLED=env.bool(
                "SHOP_REFERRED_TRIAL_ENABLED", default=DEFAULT_SHOP_REFERRED_TRIAL_ENABLED
            ),
            REFERRED_TRIAL_PERIOD=env.int(
                "SHOP_REFERRED_TRIAL_PERIOD",
                default=DEFAULT_SHOP_REFERRED_TRIAL_PERIOD,
                validate=Range(min=1, error="SHOP_REFERRED_TRIAL_PERIOD must be >= 1"),
            ),
            REFERRER_REWARD_ENABLED=referrer_reward_enabled,
            REFERRER_REWARD_TYPE=referrer_reward_type,
            REFERRER_LEVEL_ONE_PERIOD=env.int(
                "SHOP_REFERRER_LEVEL_ONE_PERIOD",
                default=DEFAULT_SHOP_REFERRER_LEVEL_ONE_PERIOD,
                validate=Range(min=1, error="SHOP_REFERRER_LEVEL_ONE_PERIOD must be >= 1"),
            ),
            REFERRER_LEVEL_TWO_PERIOD=env.int(
                "SHOP_REFERRER_LEVEL_TWO_PERIOD",
                default=DEFAULT_SHOP_REFERRER_LEVEL_TWO_PERIOD,
                validate=Range(min=1, error="SHOP_REFERRER_LEVEL_TWO_PERIOD must be >= 1"),
            ),
            REFERRER_LEVEL_ONE_RATE=env.int(
                "SHOP_REFERRER_LEVEL_ONE_RATE",
                default=DEFAULT_SHOP_REFERRER_LEVEL_ONE_RATE,
                validate=Range(
                    min=1,
                    max=100,
                    error="SHOP_REFERRER_LEVEL_ONE_RATE must be between 1 and 100",
                ),
            ),
            REFERRER_LEVEL_TWO_RATE=env.int(
                "SHOP_REFERRER_LEVEL_TWO_RATE",
                default=DEFAULT_SHOP_REFERRER_LEVEL_TWO_RATE,
                validate=Range(
                    min=1,
                    max=100,
                    error="SHOP_REFERRER_LEVEL_TWO_RATE must be between 1 and 100",
                ),
            ),
            BONUS_DEVICES_COUNT=env.int(
                "SHOP_BONUS_DEVICES_COUNT", default=DEFAULT_SHOP_BONUS_DEVICES_COUNT
            ),
            PAYMENT_STARS_ENABLED=payment_stars_enabled,
            PAYMENT_HELEKET_ENABLED=payment_heleket_enabled,
            CUSTOM_OFFER_URL=env.str("SHOP_CUSTOM_OFFER_URL", default=None),
        ),
        xui=XUIConfig(
            USERNAME=env.str("XUI_USERNAME"),
            PASSWORD=env.str("XUI_PASSWORD"),
            TOKEN=xui_token,
            SUBSCRIPTION_PORT=env.int("XUI_SUBSCRIPTION_PORT", default=DEFAULT_SUBSCRIPTION_PORT),
            SUBSCRIPTION_PATH=env.str(
                "XUI_SUBSCRIPTION_PATH",
                default=DEFAULT_SUBSCRIPTION_PATH,
            ),
        ),
        heleket=HeleketConfig(
            API_KEY=env.str("HELEKET_API_KEY", default=None),
            MERCHANT_ID=env.str("HELEKET_MERCHANT_ID", default=None),
            WEBHOOK_IPS=heleket_webhook_ips,
        ),
        database=DatabaseConfig(
            HOST=env.str("DB_HOST", default=None),
            PORT=env.int("DB_PORT", default=None),
            USERNAME=env.str("DB_USERNAME", default=None),
            PASSWORD=env.str("DB_PASSWORD", default=None),
            NAME=env.str("DB_NAME", default=DEFAULT_DB_NAME),
        ),
        redis=RedisConfig(
            HOST=env.str("REDIS_HOST", default=DEFAULT_REDIS_HOST),
            PORT=env.int("REDIS_PORT", default=DEFAULT_REDIS_PORT),
            DB_NAME=env.str("REDIS_DB_NAME", default=DEFAULT_REDIS_DB_NAME),
            USERNAME=env.str("REDIS_USERNAME", default=None),
            PASSWORD=env.str("REDIS_PASSWORD", default=None),
        ),
        logging=LoggingConfig(
            LEVEL=env.str("LOG_LEVEL", default=DEFAULT_LOG_LEVEL),
            FORMAT=env.str("LOG_FORMAT", default=DEFAULT_LOG_FORMAT),
            ARCHIVE_FORMAT=env.str(
                "LOG_ARCHIVE_FORMAT",
                default=DEFAULT_LOG_ARCHIVE_FORMAT,
                validate=OneOf(
                    [LOG_ZIP_ARCHIVE_FORMAT, LOG_GZ_ARCHIVE_FORMAT],
                    error="LOG_ARCHIVE_FORMAT must be one of: {choices}",
                ),
            ),
        ),
    )
