import pytest
from aiogram.utils.i18n import I18n

from app.bot.utils.constants import I18N_DOMAIN
from app.config import DEFAULT_LOCALES_DIR


@pytest.fixture(autouse=True)
def i18n_context():
    i18n = I18n(path=DEFAULT_LOCALES_DIR, default_locale="en", domain=I18N_DOMAIN)
    with i18n.context(), i18n.use_locale("en"):
        yield
