"""
Утилиты для работы с локациями серверов: флаги стран и локализация названий.
"""

# Словарь соответствий локаций и флагов стран
LOCATION_FLAGS: dict[str, str] = {
    # Польша
    "poland": "🇵🇱",
    "польша": "🇵🇱",
    # Финляндия
    "finland": "🇫🇮",
    "финляндия": "🇫🇮",
    # Франция
    "france": "🇫🇷",
    "франция": "🇫🇷",
    "paris": "🇫🇷",
    "париж": "🇫🇷",
    # Добавить другие страны по мере необходимости
}

# Словарь соответствий локаций и названий стран/городов
LOCATION_NAMES: dict[str, dict[str, str]] = {
    # Польша
    "poland": {"ru": "Польша", "en": "Poland"},
    "польша": {"ru": "Польша", "en": "Poland"},
    # Финляндия
    "finland": {"ru": "Финляндия", "en": "Finland"},
    "финляндия": {"ru": "Финляндия", "en": "Finland"},
    # Франция
    "france": {"ru": "Франция", "en": "France"},
    "франция": {"ru": "Франция", "en": "France"},
    # Париж (город с указанием страны)
    "paris": {
        "ru": "Париж",
        "en": "Paris",
        "country_ru": "Франция",
        "country_en": "France",
    },
    "париж": {
        "ru": "Париж",
        "en": "Paris",
        "country_ru": "Франция",
        "country_en": "France",
    },
    # Добавить другие страны/города по мере необходимости
}


def get_country_flag(location: str | None) -> str:
    """
    Получить флаг страны по названию локации.
    
    Args:
        location: Название локации (например, "Польша", "Poland", "Париж", "Paris")
    
    Returns:
        Эмодзи флага страны или пустая строка, если флаг не найден
    """
    if not location:
        return ""
    
    # Нормализуем локацию: приводим к нижнему регистру и убираем пробелы
    normalized = location.lower().strip()
    
    # Прямой поиск
    if normalized in LOCATION_FLAGS:
        return LOCATION_FLAGS[normalized]
    
    # Поиск по частичному совпадению (для случаев типа "Париж / Paris")
    for key, flag in LOCATION_FLAGS.items():
        if key in normalized or normalized in key:
            return flag
    
    return ""


def get_localized_location(location: str | None, locale: str = "ru") -> str:
    """
    Получить локализованное название локации.
    
    Args:
        location: Название локации (например, "Польша", "Poland", "Париж", "Paris")
        locale: Локаль ("ru" или "en")
    
    Returns:
        Локализованное название локации или оригинальное название, если локализация не найдена
    """
    if not location:
        return ""
    
    # Нормализуем локацию: приводим к нижнему регистру и убираем пробелы
    normalized = location.lower().strip()
    
    # Прямой поиск
    if normalized in LOCATION_NAMES:
        location_data = LOCATION_NAMES[normalized]
        if locale in location_data:
            return location_data[locale]
        # Если запрошена локаль, которой нет, возвращаем русскую или английскую
        return location_data.get("ru") or location_data.get("en") or location
    
    # Поиск по частичному совпадению
    for key, names in LOCATION_NAMES.items():
        if key in normalized or normalized in key:
            if locale in names:
                return names[locale]
            return names.get("ru") or names.get("en") or location
    
    # Если локализация не найдена, возвращаем оригинальное название
    return location


def format_server_location(
    location: str | None,
    server_name: str | None = None,
    locale: str = "ru",
) -> str:
    """
    Форматировать локацию сервера с флагом и локализацией.
    
    Для городов возвращает формат: "🇫🇷 Париж / Paris - Франция / France"
    Для стран возвращает формат: "🇵🇱 Польша" или "🇵🇱 Poland" (в зависимости от локали)
    
    Args:
        location: Название локации
        server_name: Название сервера (опционально, для случаев когда локация = название сервера)
        locale: Локаль ("ru" или "en")
    
    Returns:
        Отформатированная строка с флагом и локализованным названием
    """
    if not location:
        return ""
    
    normalized = location.lower().strip()
    flag = get_country_flag(location)
    
    # Проверяем, является ли локация городом (есть country_ru и country_en)
    if normalized in LOCATION_NAMES:
        location_data = LOCATION_NAMES[normalized]
        if "country_ru" in location_data and "country_en" in location_data:
            # Это город - используем формат "Город / City - Страна / Country"
            city_ru = location_data.get("ru", location)
            city_en = location_data.get("en", location)
            country_ru = location_data.get("country_ru", "")
            country_en = location_data.get("country_en", "")
            
            if locale == "ru":
                if country_ru:
                    return f"{flag} {city_ru} / {city_en} - {country_ru} / {country_en}"
                return f"{flag} {city_ru} / {city_en}"
            else:
                if country_en:
                    return f"{flag} {city_en} / {city_ru} - {country_en} / {country_ru}"
                return f"{flag} {city_en} / {city_ru}"
    
    # Это страна - используем локализованное название
    localized = get_localized_location(location, locale)
    return f"{flag} {localized}" if flag else localized

