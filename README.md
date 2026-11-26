<div align="center">

# 3X-UI Shop Enhanced

[![Баннер оригинального проекта 3xui-shop](https://github.com/user-attachments/assets/282d10db-a355-4c65-a2cf-eb0e8ec8eed1)](https://github.com/snoups/3xui-shop)

**Telegram-бот для продажи VPN-подписок через 3X-UI**

[Обзор](#overview) · [Установка](#installation-guide) · [Настройки](#configuration) · [Разработка](#development)

[![Лицензия MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](#development)
[![Docker Compose](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](#installation-guide)

</div>

> [!NOTE]
> Проект основан на [3xui-shop](https://github.com/snoups/3xui-shop) автора [snoups](https://github.com/snoups).

<a id="overview"></a>

## 📝 Обзор

Бот автоматизирует продажу VPN-подписок через Telegram. Панель **3X-UI** управляет VPN-клиентами, а бот принимает оплату, выдаёт ссылки и помогает пользователям подключиться.

- **Подписки**
  - Покупка, продление и смена тарифа.
  - Тарифы на несколько устройств с отдельной ссылкой для каждого устройства.
  - Профиль со статусом подписки и инструкции для Android, iOS и Windows.
- **Оплата**
  - Telegram Stars включены по умолчанию.
  - Heleket можно включить отдельно для приёма оплаты через его платёжную страницу.
- **Пробный период, промокоды и приглашения**
  - Настраиваемый пробный период для новых пользователей.
  - Промокоды на дополнительные дни подписки и двухуровневые реферальные вознаграждения.

### ⚙️ Панель управления

- **Управление пользователями** — поиск, просмотр и изменение подписок.
- **Управление серверами** — добавление и проверка панелей, синхронизация и перенос пользователей. Доступ определяется через `BOT_DEV_ID`.
- **Промокоды и рассылки** — управление кодами и отправка уведомлений из Telegram.
- **Статистика и резервное копирование** — просмотр статистики и создание резервных копий из панели.

Интерфейс бота доступен на русском и английском. Бот получает обновления Telegram через polling. HTTP-сервер обслуживает ссылки подключения, уведомления Heleket и внутренние проверки состояния.

<a id="installation-guide"></a>

## 🛠️ Установка

### Требования

Нужны [Docker с Compose](https://docs.docker.com/compose/), бот от [@BotFather](https://t.me/BotFather), панель 3X-UI с настроенным inbound и Telegram ID разработчика для добавления панели. Для публичных ссылок и Heleket понадобятся домен, DNS и TLS-сертификат. Штатная конфигурация Traefik принимает трафик на портах 80 и 443.

### Запуск через Docker Compose

1. **Скачайте проект и подготовьте конфигурацию.**

   ```bash
   git clone https://github.com/Hardcore-lab/3xui-shop-enhanced.git
   cd 3xui-shop-enhanced
   cp .env.example .env
   chmod 600 .env
   ```

2. **Заполните `.env` и проверьте тарифы.** Используйте [таблицу настроек](#configuration) и [описание файла тарифов](#subscription-plans). Для Traefik укажите `BOT_DOMAIN` без схемы и пути, оставьте `BOT_PORT=8080` и разместите цепочку сертификатов в `certs/fullchain.crt`, закрытый ключ — в `certs/server.key`.

3. **Соберите и запустите контейнеры.**

   ```bash
   docker compose up -d --build
   docker compose ps
   docker compose logs -f bot
   ```

4. **Проверьте подключение.** Контейнер компилирует переводы, применяет миграции Alembic и запускает polling. Откройте бота командой `/start` с аккаунта из `BOT_DEV_ID`. Добавьте панель в меню управления серверами, проверьте inbound и URL подписки. Перед запуском продаж пройдите покупку и подключение с тестового аккаунта.

<a id="configuration"></a>

### Переменные окружения

| Переменные | Назначение |
| --- | --- |
| `BOT_TOKEN` | Токен бота из BotFather |
| `BOT_SUPPORT_ID` | Telegram ID для связи с поддержкой |
| `BOT_ADMINS`, `BOT_DEV_ID` | Telegram ID администраторов и разработчиков через запятую |
| `BOT_DOMAIN`, `BOT_PORT` | Публичный домен и порт внутреннего HTTP-сервера |
| `XUI_USERNAME`, `XUI_PASSWORD` | Учётные данные панели 3X-UI по умолчанию |
| `XUI_SUBSCRIPTION_PORT`, `XUI_SUBSCRIPTION_PATH` | Порт и путь ссылки подписки на стороне панели |
| `SHOP_PAYMENT_STARS_ENABLED` | Включает Telegram Stars, по умолчанию включено |
| `SHOP_PAYMENT_HELEKET_ENABLED`, `HELEKET_API_KEY`, `HELEKET_MERCHANT_ID`, `HELEKET_WEBHOOK_IPS` | Настройки Heleket. Для приёма уведомлений нужен доступный HTTPS-маршрут `/heleket` |
| `DB_NAME`, `DB_HOST`, `DB_PORT`, `DB_USERNAME`, `DB_PASSWORD` | SQLite при пустом `DB_HOST`, PostgreSQL при заданном |
| `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB_NAME`, `REDIS_USERNAME`, `REDIS_PASSWORD` | Redis для состояния диалогов, кэша и блокировок |
| `ENCRYPTION_KEY` | Ключ Fernet для шифрования сохраняемых в БД паролей панелей. Храните отдельно от базы и резервных копий |

<a id="subscription-plans"></a>

### Тарифы

Тарифы определены в [`plans.json`](plans.json). Файл задаёт сроки и варианты подписки по числу устройств. При запуске бот читает тарифы из БД и заполняет их из JSON, когда отсутствуют необходимые активные варианты. Проверяйте фактические цены и валюту в интерфейсе перед включением реальных платежей.

Пример структуры для тарифа на одно устройство приведён ниже. Поле `durations` содержит сроки в днях, `devices` задаёт число устройств, а `prices` группирует цены по валюте и сроку. Для Telegram Stars используется код `XTR`. Сверьтесь с [`plans.example.json`](plans.example.json) перед изменением рабочего файла.

```json
{
  "durations": [30, 60],
  "plans": [
    {
      "devices": 1,
      "prices": {
        "XTR": {"30": 100, "60": 200}
      }
    }
  ]
}
```

## 🔧 Эксплуатация

- Храните `.env`, закрытый TLS-ключ, ключ шифрования и резервные копии вне Git. При утечке отзовите и перевыпустите раскрытые данные. После ротации токена ранее выданные подписанные ссылки `/connection` нужно получить из бота заново.
- Перед обновлением сохраните согласованную резервную копию SQLite из `app/data/` или снимок PostgreSQL, а также текущий файл тарифов. Затем запустите `docker compose up -d --build` и проверьте журнал миграций.
- Traefik публикует маршруты `/connection` и `/heleket`. Проверки состояния и метрики доступны на внутреннем HTTP-сервере. Для проверки SQLite/PostgreSQL и Redis используйте `/health/ready`, для проверки процесса — `/health/live`. Метрики Prometheus доступны по `/metrics`.
- При сбое polling проверьте токен, доступ к Telegram API и отсутствие второго процесса с тем же токеном. Если не выдаются ключи, проверьте доступ бота к панели, учётные данные, inbound и URL подписки. Для ошибок Heleket проверьте ключ мерчанта, подпись уведомлений и публичный маршрут.

<a id="development"></a>

## 🧪 Разработка

Для разработки установите Python 3.12 и Poetry. В корне репозитория выполните:

```bash
poetry install --extras dev
poetry run pytest -q
```

Тесты имитируют внешние вызовы Telegram и 3X-UI. Для локального запуска приложения задайте `REDIS_HOST=127.0.0.1` и порт своего локального Redis. Используйте отдельный токен тестового бота и отдельную БД.

```bash
poetry run pybabel compile -d app/locales -D bot
poetry run alembic -c app/db/alembic.ini upgrade head
poetry run python -m app
```

| Путь | Содержимое |
| --- | --- |
| [`app/__main__.py`](app/__main__.py) | Запуск приложения, polling и HTTP-сервер |
| [`app/bot/routers/`](app/bot/routers/) | Пользовательские и административные сценарии Telegram |
| [`app/bot/services/`](app/bot/services/) | Подписки, взаимодействие с 3X-UI, уведомления и метрики |
| [`app/bot/payment_gateways/`](app/bot/payment_gateways/) | Telegram Stars и Heleket |
| [`app/db/`](app/db/) | Модели SQLAlchemy и миграции Alembic |
| [`tests/`](tests/) | Модульные и интеграционные тесты |

## 📄 Лицензия

Проект основан на [3xui-shop](https://github.com/snoups/3xui-shop) автора [snoups](https://github.com/snoups). Доработки поддерживает Hardcore-lab. Условия распространения приведены в [MIT License](LICENSE).
