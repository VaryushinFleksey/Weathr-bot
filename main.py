import os
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, BotCommand, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
import requests
from dotenv import load_dotenv
import asyncio
from datetime import datetime, timedelta, time
import pytz
import signal
import sys
from aiohttp import web
import aiohttp
import math
from urllib.parse import quote
import json
from collections import defaultdict
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from contextlib import suppress

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Get environment variables
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
OPENWEATHER_API_KEY = os.getenv('OPENWEATHER_API_KEY')

# Initialize bot and dispatcher
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
scheduler = None
app = None
runner = None

# Bot commands for menu
COMMANDS = [
    BotCommand(command='start', description='Запустить бота'),
    BotCommand(command='help', description='Показать помощь'),
    BotCommand(command='forecast', description='Прогноз погоды на 5 дней'),
    BotCommand(command='detailed', description='Подробная информация о погоде'),
    BotCommand(command='air', description='Качество воздуха'),
    BotCommand(command='compare', description='Сравнить погоду в двух городах'),
    BotCommand(command='alerts', description='Погодные предупреждения'),
    BotCommand(command='wear', description='Рекомендации по одежде'),
    BotCommand(command='rain', description='Карта осадков'),
    BotCommand(command='subscribe', description='Подписаться на уведомления о погоде'),
    BotCommand(command='unsubscribe', description='Отписаться от уведомлений'),
    BotCommand(command='stats', description='Статистика погоды'),
    BotCommand(command='shelter', description='Найти укрытие от непогоды'),
    BotCommand(command='preferences', description='Настройка умных уведомлений'),
    BotCommand(command='activities', description='Настройка предпочитаемых активностей'),
    BotCommand(command='notifytime', description='Установить время уведомлений')
]

# Добавим глобальные переменные для хранения подписок и статистики
weather_subscriptions = defaultdict(list)  # {user_id: [(city, lat, lon), ...]}
weather_stats = defaultdict(lambda: defaultdict(list))  # {city: {'temp': [], 'humidity': [], ...}}

# Настройки пользователей для умных уведомлений
user_preferences = defaultdict(lambda: {
    'activities': [],  # Список предпочитаемых активностей
    'notification_time': '09:00',  # Время ежедневных уведомлений
    'temp_range': {'min': 15, 'max': 25},  # Комфортный диапазон температур
    'notify_changes': True,  # Уведомлять о резких изменениях погоды
    'rain_alerts': True,  # Уведомления о дожде
    'wind_threshold': 10,  # Порог скорости ветра для уведомлений
    'uv_alerts': True,  # Уведомления об УФ-индексе
})

# Активности и их оптимальные условия
ACTIVITIES = {
    'running': {
        'temp_range': (5, 20),
        'wind_max': 5,
        'no_rain': True,
        'description': 'бега'
    },
    'cycling': {
        'temp_range': (10, 25),
        'wind_max': 7,
        'no_rain': True,
        'description': 'велопрогулки'
    },
    'walking': {
        'temp_range': (15, 25),
        'wind_max': 10,
        'no_rain': True,
        'description': 'прогулки'
    },
    'picnic': {
        'temp_range': (20, 27),
        'wind_max': 5,
        'no_rain': True,
        'description': 'пикника'
    }
}

def get_clothing_recommendations(weather_data):
    """Формирует рекомендации по одежде на основе погодных условий"""
    temp = weather_data['main']['temp']
    feels_like = weather_data['main']['feels_like']
    wind_speed = weather_data['wind']['speed']
    description = weather_data['weather'][0]['description'].lower()
    humidity = weather_data['main']['humidity']
    
    recommendations = []
    
    # Базовые рекомендации по температуре
    if feels_like <= -20:
        recommendations.extend([
            "🧥 Теплый зимний пуховик или шуба",
            "🧣 Теплый шарф",
            "🧤 Теплые перчатки или варежки",
            "👢 Зимние утепленные ботинки",
            "🧦 Теплые носки, желательно шерстяные",
            "👖 Теплые зимние брюки или термобелье"
        ])
    elif -20 < feels_like <= -10:
        recommendations.extend([
            "🧥 Зимняя куртка или пуховик",
            "🧣 Шарф",
            "🧤 Перчатки",
            "👢 Зимняя обувь",
            "🧦 Теплые носки"
        ])
    elif -10 < feels_like <= 0:
        recommendations.extend([
            "🧥 Демисезонная куртка или легкий пуховик",
            "🧣 Легкий шарф",
            "🧤 Перчатки",
            "👞 Утепленная обувь"
        ])
    elif 0 < feels_like <= 10:
        recommendations.extend([
            "🧥 Легкая куртка или плащ",
            "🧥 Свитер или кофта",
            "👞 Закрытая обувь"
        ])
    elif 10 < feels_like <= 20:
        recommendations.extend([
            "👕 Легкая кофта или рубашка",
            "👖 Брюки или джинсы",
            "👟 Легкая обувь"
        ])
    elif 20 < feels_like <= 25:
        recommendations.extend([
            "👕 Футболка или рубашка с коротким рукавом",
            "👖 Легкие брюки или шорты",
            "👟 Легкая обувь или сандалии"
        ])
    else:  # > 25
        recommendations.extend([
            "👕 Легкая одежда из натуральных тканей",
            "🩳 Шорты или легкая юбка",
            "👡 Сандалии или открытая обувь"
        ])
    
    # Дополнительные рекомендации в зависимости от условий
    if "дождь" in description or "ливень" in description:
        recommendations.extend([
            "☔️ Зонт",
            "🧥 Водонепроницаемая куртка или плащ",
            "👢 Непромокаемая обувь"
        ])
    
    if "снег" in description:
        recommendations.append("👢 Водонепроницаемая обувь с нескользящей подошвой")
    
    if wind_speed > 10:
        recommendations.append("🧥 Ветрозащитная куртка или плащ")
    
    if humidity > 80 and temp > 20:
        recommendations.append("👕 Легкая дышащая одежда из натуральных тканей")
    
    if "солнечно" in description or "ясно" in description:
        if temp > 20:
            recommendations.extend([
                "🧢 Головной убор от солнца",
                "🕶 Солнцезащитные очки"
            ])
    
    return recommendations

def lat_lon_to_tile(lat, lon, zoom):
    """Конвертирует координаты в номера тайлов"""
    lat_rad = math.radians(lat)
    n = 2.0 ** zoom
    xtile = int((lon + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return xtile, ytile

async def get_precipitation_map(lat, lon, zoom=8):
    """Получает карту осадков для заданных координат"""
    try:
        # Конвертируем координаты в тайлы
        xtile, ytile = lat_lon_to_tile(lat, lon, zoom)
        
        # Формируем URL для карты осадков
        map_url = (
            f"https://tile.openweathermap.org/map/precipitation_new/{zoom}/{xtile}/{ytile}.png"
            f"?appid={OPENWEATHER_API_KEY}"
        )
        
        logging.info(f"Requesting precipitation map: {map_url}")
        
        # Проверяем доступность тайла
        async with aiohttp.ClientSession() as session:
            async with session.get(map_url) as response:
                if response.status == 200:
                    content = await response.read()
                    if len(content) > 1000:  # Проверяем, что получили реальное изображение
                        return map_url
                    else:
                        logging.warning(f"Empty or invalid tile received: {len(content)} bytes")
                        return None
                else:
                    logging.error(f"Failed to fetch tile: {response.status}")
                    return None
    except Exception as e:
        logging.error(f"Error fetching precipitation map: {e}")
        return None

def create_main_keyboard():
    """Create main keyboard with location button."""
    keyboard = [
        [{"text": "📍 Отправить геолокацию", "request_location": True}],
        [{"text": "ℹ️ Помощь"}]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def format_detailed_weather(weather_data, city_name):
    """Format detailed weather information."""
    # Basic weather info
    temp = weather_data['main']['temp']
    feels_like = weather_data['main']['feels_like']
    description = weather_data['weather'][0]['description']
    humidity = weather_data['main']['humidity']
    wind_speed = weather_data['wind']['speed']
    pressure = weather_data['main']['pressure']
    
    # Convert sunrise and sunset timestamps to datetime
    timezone_offset = weather_data['timezone']
    sunrise_time = datetime.fromtimestamp(weather_data['sys']['sunrise'] + timezone_offset).strftime('%H:%M')
    sunset_time = datetime.fromtimestamp(weather_data['sys']['sunset'] + timezone_offset).strftime('%H:%M')
    
    # Calculate wind direction
    wind_deg = weather_data.get('wind', {}).get('deg', 0)
    wind_directions = ['С', 'СВ', 'В', 'ЮВ', 'Ю', 'ЮЗ', 'З', 'СЗ']
    wind_direction = wind_directions[round(wind_deg / 45) % 8]
    
    # Visibility in kilometers
    visibility = weather_data.get('visibility', 0) / 1000
    
    # Clouds percentage
    clouds = weather_data['clouds']['all']
    
    return (
        f"🌍 Подробная информация о погоде в городе {city_name}:\n\n"
        f"🌡 Температура: {temp:.1f}°C\n"
        f"🤔 Ощущается как: {feels_like:.1f}°C\n"
        f"☁️ Условия: {description}\n"
        f"💧 Влажность: {humidity}%\n"
        f"💨 Ветер: {wind_speed} м/с, направление: {wind_direction}\n"
        f"🌅 Восход: {sunrise_time}\n"
        f"🌇 Закат: {sunset_time}\n"
        f"🌡 Давление: {pressure} гПа\n"
        f"👁 Видимость: {visibility:.1f} км\n"
        f"☁️ Облачность: {clouds}%"
    )

def check_weather_alerts(weather_data):
    """Check for dangerous weather conditions."""
    alerts = []
    
    # Check temperature
    temp = weather_data['main']['temp']
    if temp > 30:
        alerts.append("🌡 Сильная жара! Избегайте длительного пребывания на солнце")
    elif temp < -15:
        alerts.append("❄️ Сильный мороз! Тепло оденьтесь")
    
    # Check wind
    wind_speed = weather_data['wind']['speed']
    if wind_speed > 15:
        alerts.append(f"💨 Сильный ветер {wind_speed} м/с! Будьте осторожны на улице")
    
    # Check rain/snow/thunderstorm
    if 'rain' in weather_data:
        rain = weather_data['rain'].get('1h', 0)
        if rain > 10:
            alerts.append("🌧 Сильный дождь! Возьмите зонт")
    if 'snow' in weather_data:
        snow = weather_data['snow'].get('1h', 0)
        if snow > 5:
            alerts.append("🌨 Сильный снег! Возможны заносы на дорогах")
    
    # Check visibility
    visibility = weather_data.get('visibility', 10000) / 1000  # convert to km
    if visibility < 1:
        alerts.append("🌫 Очень плохая видимость! Будьте внимательны")
    
    # Check weather conditions
    weather_id = weather_data['weather'][0]['id']
    if weather_id in range(200, 300):  # Thunderstorm
        alerts.append("⛈ Гроза! Соблюдайте меры предосторожности")
    
    return alerts

@dp.message(CommandStart())
async def start_command(message: Message):
    """Send a message when the command /start is issued."""
    await message.answer(
        'Привет! Я бот прогноза погоды. 🌤\n'
        'Я могу:\n'
        '1. Показать текущую погоду (просто напишите город)\n'
        '2. Прогноз на 5 дней (/forecast город)\n'
        '3. Подробную информацию о погоде (/detailed город)\n'
        '4. Качество воздуха (/air город)\n'
        '5. Определить погоду по геолокации\n'
        '6. Сравнить погоду в разных городах (/compare)\n'
        '7. Показать погодные предупреждения (/alerts город)\n'
        '8. Получить рекомендации по одежде (/wear город)\n'
        '9. Получить карту осадков (/rain город)\n'
        '10. Подписаться на уведомления о погоде (/subscribe город)\n'
        '11. Отписаться от уведомлений (/unsubscribe город)\n'
        '12. Получить статистику погоды (/stats город)\n'
        '13. Найти укрытие от непогоды (/shelter)\n'
        '14. Настроить умные уведомления (/preferences)\n'
        '15. Настроить предпочитаемые активности (/activities)\n'
        '16. Установить время уведомлений (/notifytime)\n\n'
        'Используйте кнопку ниже, чтобы отправить свою геолокацию!',
        reply_markup=create_main_keyboard()
    )

@dp.message(Command('help'))
async def help_command(message: Message):
    """Send a message when the command /help is issued."""
    await message.answer(
        'Доступные команды:\n\n'
        '1. Напишите название города для текущей погоды\n'
        '2. /forecast ГОРОД - прогноз на 5 дней\n'
        '3. /detailed ГОРОД - подробная информация\n'
        '4. /air ГОРОД - качество воздуха\n'
        '5. /compare - сравнить погоду в городах\n'
        '6. /alerts ГОРОД - погодные предупреждения\n'
        '7. /wear ГОРОД - рекомендации по одежде\n'
        '8. /rain ГОРОД - карта осадков\n'
        '9. /subscribe ГОРОД - подписаться на уведомления о погоде\n'
        '10. /unsubscribe ГОРОД - отписаться от уведомлений\n'
        '11. /stats ГОРОД - статистика погоды\n'
        '12. /shelter - найти укрытие от непогоды\n'
        '13. /preferences - настроить умные уведомления\n'
        '14. /activities - настроить предпочитаемые активности\n'
        '15. /notifytime - установить время уведомлений\n'
        '16. Нажмите кнопку "📍 Отправить геолокацию" для погоды в вашем месте\n\n'
        'Примеры:\n'
        '- "Москва" - текущая погода\n'
        '- "/forecast Париж" - прогноз на 5 дней\n'
        '- "/detailed Лондон" - подробная информация\n'
        '- "/alerts Москва" - погодные предупреждения',
        reply_markup=create_main_keyboard()
    )

@dp.message(Command('detailed'))
async def detailed_command(message: Message):
    """Get detailed weather information for the specified city."""
    try:
        city = message.text.split(' ', 1)[1]
    except IndexError:
        await message.answer(
            "Пожалуйста, укажите город после команды.\n"
            "Например: /detailed Москва"
        )
        return

    try:
        # Получаем координаты города
        result = await get_city_coordinates(city)
        if not result:
            await message.answer(
                "Извините, не могу найти такой город. Попробуйте:\n"
                "1. Проверить правильность написания\n"
                "2. Использовать название на русском или английском\n"
                "3. Указать более крупный город поблизости"
            )
            return
            
        lat, lon, normalized_city = result
        
        # Get detailed weather data
        weather_url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=ru"
        async with aiohttp.ClientSession() as session:
            async with session.get(weather_url) as response:
                weather_data = await response.json()
        
        # Check for weather alerts
        alerts = check_weather_alerts(weather_data)
        
        # Format and send detailed weather information
        detailed_message = format_detailed_weather(weather_data, normalized_city)
        if alerts:
            detailed_message += "\n\n" + "\n".join(alerts)
        await message.answer(detailed_message)
        
    except Exception as e:
        logging.error(f"Error getting detailed weather: {e}")
        await message.answer(
            "Извините, произошла ошибка при получении информации о погоде. "
            "Пожалуйста, попробуйте позже."
        )

def get_air_quality_recommendations(aqi, components):
    """Возвращает рекомендации на основе качества воздуха"""
    recommendations = []
    
    # Общие рекомендации на основе AQI
    aqi_recommendations = {
        1: [
            "✅ Отличное качество воздуха - идеально для любой активности на улице",
            "🏃‍♂️ Прекрасное время для спорта на свежем воздухе",
            "🌳 Можно долго гулять и наслаждаться свежим воздухом"
        ],
        2: [
            "✅ Хорошее качество воздуха - подходит для большинства людей",
            "⚠️ Людям с повышенной чувствительностью следует ограничить длительные нагрузки",
            "🏃‍♂️ Можно заниматься спортом на улице"
        ],
        3: [
            "⚠️ Умеренное загрязнение - следует быть осторожным",
            "😷 Чувствительным группам лучше ограничить пребывание на улице",
            "🏃‍♂️ Сократите интенсивные физические нагрузки на открытом воздухе"
        ],
        4: [
            "❗ Плохое качество воздуха - примите меры предосторожности",
            "😷 Рекомендуется носить маску при выходе на улицу",
            "🏠 По возможности оставайтесь в помещении",
            "🚗 Держите окна в машине закрытыми"
        ],
        5: [
            "⛔ Очень плохое качество воздуха - серьезный риск для здоровья",
            "🏠 Настоятельно рекомендуется оставаться в помещении",
            "😷 При необходимости выхода используйте респиратор",
            "❗ Избегайте любой физической активности на улице"
        ]
    }
    
    recommendations.extend(aqi_recommendations.get(aqi, []))
    
    # Специфические рекомендации на основе компонентов
    if components['pm2_5'] > 25:  # WHO guideline value
        recommendations.append("😷 Высокий уровень мелких частиц PM2.5 - используйте маску с хорошей фильтрацией")
    
    if components['pm10'] > 50:  # WHO guideline value
        recommendations.append("😷 Повышенный уровень крупных частиц PM10 - избегайте пыльных мест")
    
    if components['o3'] > 100:  # High ozone level
        recommendations.append("⚠️ Высокий уровень озона - избегайте активности на улице в жаркое время дня")
    
    if components['no2'] > 200:  # High NO2 level
        recommendations.append("🏭 Высокий уровень диоксида азота - держитесь подальше от оживленных дорог")
    
    if components['so2'] > 350:  # High SO2 level
        recommendations.append("⚠️ Высокий уровень диоксида серы - может вызывать респираторные проблемы")
    
    return recommendations

def format_air_quality_message(city, air_data):
    """Форматирует сообщение о качестве воздуха"""
    aqi = air_data['list'][0]['main']['aqi']
    components = air_data['list'][0]['components']
    
    # AQI levels description
    aqi_levels = {
        1: "Отличное 🌟",
        2: "Хорошее 🌿",
        3: "Умеренное 😐",
        4: "Плохое 😷",
        5: "Очень плохое ⚠️"
    }
    
    # Компоненты и их описания
    components_info = {
        'co': ('CO', 'Угарный газ', 'мкг/м³'),
        'no': ('NO', 'Оксид азота', 'мкг/м³'),
        'no2': ('NO₂', 'Диоксид азота', 'мкг/м³'),
        'o3': ('O₃', 'Озон', 'мкг/м³'),
        'so2': ('SO₂', 'Диоксид серы', 'мкг/м³'),
        'pm2_5': ('PM2.5', 'Мелкие частицы', 'мкг/м³'),
        'pm10': ('PM10', 'Крупные частицы', 'мкг/м³'),
        'nh3': ('NH₃', 'Аммиак', 'мкг/м³')
    }
    
    # Формируем основное сообщение
    message = [
        f"🌬 Качество воздуха в {city}:",
        f"\n📊 Общий индекс: {aqi_levels[aqi]}",
        "\n📈 Компоненты воздуха:"
    ]
    
    # Добавляем информацию о компонентах
    for code, (symbol, name, unit) in components_info.items():
        if code in components:
            value = components[code]
            message.append(f"• {symbol} ({name}): {value:.1f} {unit}")
    
    # Получаем и добавляем рекомендации
    recommendations = get_air_quality_recommendations(aqi, components)
    if recommendations:
        message.append("\n💡 Рекомендации:")
        message.extend(recommendations)
    
    return "\n".join(message)

@dp.message(Command('air'))
async def air_quality_command(message: Message):
    """Получает информацию о качестве воздуха для указанного города"""
    try:
        city = message.text.split(' ', 1)[1]
    except IndexError:
        await message.answer(
            "Пожалуйста, укажите город после команды.\n"
            "Например: /air Москва"
        )
        return

    try:
        # Получаем координаты
        geo_url = f"http://api.openweathermap.org/geo/1.0/direct?q={city}&limit=1&appid={OPENWEATHER_API_KEY}"
        async with aiohttp.ClientSession() as session:
            async with session.get(geo_url) as response:
                geo_data = await response.json()
        
        if not geo_data:
            await message.answer("Извините, не могу найти такой город. Попробуйте другой.")
            return
            
        lat = geo_data[0]['lat']
        lon = geo_data[0]['lon']
        
        # Получаем данные о качестве воздуха
        air_url = f"http://api.openweathermap.org/data/2.5/air_pollution?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}"
        async with aiohttp.ClientSession() as session:
            async with session.get(air_url) as response:
                air_data = await response.json()
        
        # Форматируем и отправляем сообщение
        air_message = format_air_quality_message(city, air_data)
        await message.answer(air_message)
        
        # Если качество воздуха плохое, предлагаем посмотреть прогноз
        if air_data['list'][0]['main']['aqi'] >= 4:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(
                        text="Посмотреть прогноз загрязнения",
                        callback_data=f"air_forecast_{lat}_{lon}"
                    )
                ]]
            )
            await message.answer(
                "❗ Обнаружен высокий уровень загрязнения. Хотите посмотреть прогноз?",
                reply_markup=keyboard
            )
        
    except Exception as e:
        logging.error(f"Error getting air quality: {e}")
        await message.answer(
            "Извините, произошла ошибка при получении данных о качестве воздуха. "
            "Пожалуйста, попробуйте позже."
        )

@dp.callback_query(lambda c: c.data.startswith('air_forecast_'))
async def air_forecast(callback_query: types.CallbackQuery):
    """Показывает прогноз качества воздуха"""
    try:
        # Извлекаем координаты
        _, lat, lon = callback_query.data.split('_')[2:]
        lat, lon = float(lat), float(lon)
        
        # Получаем прогноз качества воздуха
        forecast_url = f"http://api.openweathermap.org/data/2.5/air_pollution/forecast?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}"
        async with aiohttp.ClientSession() as session:
            async with session.get(forecast_url) as response:
                forecast_data = await response.json()
        
        # Анализируем прогноз
        forecasts = forecast_data['list'][:8]  # Берем прогноз на ближайшие 24 часа
        aqi_levels = {
            1: "Отличное 🌟",
            2: "Хорошее 🌿",
            3: "Умеренное 😐",
            4: "Плохое 😷",
            5: "Очень плохое ⚠️"
        }
        
        # Форматируем прогноз
        message = ["📊 Прогноз качества воздуха на ближайшие 24 часа:\n"]
        for forecast in forecasts:
            dt = datetime.fromtimestamp(forecast['dt'])
            aqi = forecast['main']['aqi']
            message.append(f"🕐 {dt.strftime('%H:%M')}: {aqi_levels[aqi]}")
        
        await callback_query.message.answer("\n".join(message))
        await callback_query.answer()
        
    except Exception as e:
        logging.error(f"Error getting air forecast: {e}")
        await callback_query.message.answer(
            "Извините, произошла ошибка при получении прогноза качества воздуха."
        )
        await callback_query.answer()

@dp.message(Command('compare'))
async def compare_command(message: Message):
    """Start the weather comparison process."""
    await message.answer(
        "Для сравнения погоды в двух городах, отправьте их названия через запятую.\n"
        "Например: Москва, Санкт-Петербург"
    )

@dp.message(lambda message: ',' in message.text)
async def compare_cities(message: Message):
    """Compare weather in two cities."""
    cities = [city.strip() for city in message.text.split(',')]
    if len(cities) != 2:
        await message.answer("Пожалуйста, укажите ровно два города через запятую.")
        return

    try:
        weather_data = []
        for city in cities:
            # Get coordinates
            geo_url = f"http://api.openweathermap.org/geo/1.0/direct?q={city}&limit=1&appid={OPENWEATHER_API_KEY}"
            geo_response = requests.get(geo_url)
            geo_data = geo_response.json()
            
            if not geo_data:
                await message.answer(f"Извините, не могу найти город {city}.")
                return
                
            lat = geo_data[0]['lat']
            lon = geo_data[0]['lon']
            
            # Get weather data
            weather_url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=ru"
            weather_response = requests.get(weather_url)
            weather_data.append(weather_response.json())
        
        # Compare and format message
        compare_message = f"🔄 Сравнение погоды:\n\n"
        
        for i, data in enumerate(weather_data):
            temp = data['main']['temp']
            feels_like = data['main']['feels_like']
            description = data['weather'][0]['description']
            humidity = data['main']['humidity']
            wind_speed = data['wind']['speed']
            
            compare_message += (
                f"📍 {cities[i]}:\n"
                f"🌡 Температура: {temp:.1f}°C\n"
                f"🤔 Ощущается как: {feels_like:.1f}°C\n"
                f"☁️ Условия: {description}\n"
                f"💧 Влажность: {humidity}%\n"
                f"💨 Скорость ветра: {wind_speed} м/с\n\n"
            )
        
        # Add temperature difference
        temp_diff = abs(weather_data[0]['main']['temp'] - weather_data[1]['main']['temp'])
        compare_message += f"Разница температур: {temp_diff:.1f}°C"
        
        await message.answer(compare_message)
        
    except Exception as e:
        logging.error(f"Error comparing cities: {e}")
        await message.answer(
            "Извините, произошла ошибка при сравнении городов. "
            "Пожалуйста, попробуйте позже."
        )

@dp.message(Command('forecast'))
async def forecast_command(message: Message):
    """Get 5-day weather forecast for the specified city."""
    try:
        city = message.text.split(' ', 1)[1]
    except IndexError:
        await message.answer(
            "Пожалуйста, укажите город после команды.\n"
            "Например: /forecast Москва"
        )
        return

    try:
        # Get coordinates first
        geo_url = f"http://api.openweathermap.org/geo/1.0/direct?q={city}&limit=1&appid={OPENWEATHER_API_KEY}"
        geo_response = requests.get(geo_url)
        geo_data = geo_response.json()
        
        if not geo_data:
            await message.answer("Извините, не могу найти такой город. Попробуйте другой.")
            return
            
        lat = geo_data[0]['lat']
        lon = geo_data[0]['lon']
        
        # Get 5-day forecast data
        forecast_url = f"https://api.openweathermap.org/data/2.5/forecast?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=ru"
        forecast_response = requests.get(forecast_url)
        forecast_data = forecast_response.json()
        
        # Process and format forecast data
        forecast_message = f"🌍 Прогноз погоды в городе {city} на 5 дней:\n\n"
        
        # Keep track of processed dates to avoid duplicates
        processed_dates = set()
        
        for item in forecast_data['list']:
            # Convert timestamp to date
            date = datetime.fromtimestamp(item['dt'])
            date_str = date.strftime('%d.%m.%Y')
            
            # Only process one forecast per day
            if date_str in processed_dates:
                continue
                
            processed_dates.add(date_str)
            
            # Get weather data
            temp = item['main']['temp']
            feels_like = item['main']['feels_like']
            description = item['weather'][0]['description']
            humidity = item['main']['humidity']
            wind_speed = item['wind']['speed']
            
            # Add day forecast to message
            forecast_message += (
                f"📅 {date_str}:\n"
                f"🌡 Температура: {temp:.1f}°C\n"
                f"🤔 Ощущается как: {feels_like:.1f}°C\n"
                f"☁️ Условия: {description}\n"
                f"💧 Влажность: {humidity}%\n"
                f"💨 Скорость ветра: {wind_speed} м/с\n\n"
            )
            
            # Stop after 5 days
            if len(processed_dates) >= 5:
                break
        
        await message.answer(forecast_message)
        
    except Exception as e:
        logging.error(f"Error getting forecast: {e}")
        await message.answer(
            "Извините, произошла ошибка при получении прогноза погоды. "
            "Пожалуйста, попробуйте позже."
        )

@dp.message(Command('alerts'))
async def alerts_command(message: Message):
    """Get weather alerts for the specified city."""
    try:
        city = message.text.split(' ', 1)[1]
    except IndexError:
        await message.answer(
            "Пожалуйста, укажите город после команды.\n"
            "Например: /alerts Москва"
        )
        return

    try:
        # Get coordinates first
        geo_url = f"http://api.openweathermap.org/geo/1.0/direct?q={city}&limit=1&appid={OPENWEATHER_API_KEY}"
        geo_response = requests.get(geo_url)
        geo_data = geo_response.json()
        
        if not geo_data:
            await message.answer("Извините, не могу найти такой город. Попробуйте другой.")
            return
            
        lat = geo_data[0]['lat']
        lon = geo_data[0]['lon']
        
        # Get weather data
        weather_url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=ru"
        weather_response = requests.get(weather_url)
        weather_data = weather_response.json()
        
        # Check for weather alerts
        alerts = check_weather_alerts(weather_data)
        
        if alerts:
            alert_message = f"⚠️ Погодные предупреждения для города {city}:\n\n" + "\n".join(alerts)
        else:
            alert_message = f"✅ Опасных погодных явлений в городе {city} не обнаружено"
        
        await message.answer(alert_message)
        
    except Exception as e:
        logging.error(f"Error getting weather alerts: {e}")
        await message.answer(
            "Извините, произошла ошибка при получении погодных предупреждений. "
            "Пожалуйста, попробуйте позже."
        )

@dp.message(Command('wear'))
async def wear_command(message: Message):
    """Получить рекомендации по одежде для указанного города."""
    try:
        city = message.text.split(' ', 1)[1]
    except IndexError:
        await message.answer(
            "Пожалуйста, укажите город после команды.\n"
            "Например: /wear Москва"
        )
        return

    try:
        # Получаем координаты
        geo_url = f"http://api.openweathermap.org/geo/1.0/direct?q={city}&limit=1&appid={OPENWEATHER_API_KEY}"
        geo_response = requests.get(geo_url)
        geo_data = geo_response.json()
        
        if not geo_data:
            await message.answer("Извините, не могу найти такой город. Попробуйте другой.")
            return
            
        lat = geo_data[0]['lat']
        lon = geo_data[0]['lon']
        
        # Получаем данные о погоде
        weather_url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=ru"
        weather_response = requests.get(weather_url)
        weather_data = weather_response.json()
        
        # Получаем рекомендации
        recommendations = get_clothing_recommendations(weather_data)
        
        # Формируем сообщение
        temp = weather_data['main']['temp']
        feels_like = weather_data['main']['feels_like']
        description = weather_data['weather'][0]['description']
        
        message_text = (
            f"👔 Рекомендации по одежде для {city}:\n\n"
            f"🌡 Температура: {temp:.1f}°C\n"
            f"🤔 Ощущается как: {feels_like:.1f}°C\n"
            f"☁️ Условия: {description}\n\n"
            f"Рекомендуется надеть:\n"
            f"{chr(10).join('- ' + item for item in recommendations)}"
        )
        
        await message.answer(message_text)
        
    except Exception as e:
        logging.error(f"Error getting clothing recommendations: {e}")
        await message.answer(
            "Извините, произошла ошибка при получении рекомендаций. "
            "Пожалуйста, попробуйте позже."
        )

@dp.message(Command('rain'))
async def rain_map_command(message: Message):
    """Отправляет информацию об осадках для указанного города"""
    try:
        city = message.text.split(' ', 1)[1]
    except IndexError:
        await message.answer(
            "Пожалуйста, укажите город после команды.\n"
            "Например: /rain Москва"
        )
        return

    try:
        # Получаем координаты города
        geo_url = f"http://api.openweathermap.org/geo/1.0/direct?q={city}&limit=1&appid={OPENWEATHER_API_KEY}"
        async with aiohttp.ClientSession() as session:
            async with session.get(geo_url) as response:
                geo_data = await response.json()
        
        if not geo_data:
            await message.answer("Извините, не могу найти такой город. Попробуйте другой.")
            return
            
        lat = geo_data[0]['lat']
        lon = geo_data[0]['lon']
        
        # Получаем текущие данные о погоде
        weather_url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=ru"
        async with aiohttp.ClientSession() as session:
            async with session.get(weather_url) as response:
                weather_data = await response.json()

        # Формируем базовое сообщение о погоде
        weather_message = f"🌍 Информация об осадках в городе {city}:\n\n"
        weather_message += f"☁️ {weather_data['weather'][0]['description']}\n"
        
        # Добавляем информацию об осадках
        if 'rain' in weather_data:
            weather_message += f"🌧 Дождь: {weather_data['rain'].get('1h', 0)} мм/ч\n"
        if 'snow' in weather_data:
            weather_message += f"🌨 Снег: {weather_data['snow'].get('1h', 0)} мм/ч\n"
        
        # Добавляем информацию о влажности и облачности
        weather_message += f"💧 Влажность: {weather_data['main']['humidity']}%\n"
        weather_message += f"☁️ Облачность: {weather_data['clouds']['all']}%\n"
        
        # Пытаемся получить карту осадков
        map_url = await get_precipitation_map(lat, lon)
        
        if map_url:
            try:
                await message.answer_photo(
                    map_url,
                    caption=weather_message + "\n🗺 Карта осадков:"
                )
            except Exception as e:
                logging.error(f"Error sending precipitation map: {e}")
                await message.answer(weather_message + "\n\nК сожалению, карта осадков сейчас недоступна.")
        else:
            # Если карта недоступна, отправляем только текстовую информацию
            if 'rain' not in weather_data and 'snow' not in weather_data:
                weather_message += "\n✨ В данный момент осадков нет"
            await message.answer(weather_message)
            
    except Exception as e:
        logging.error(f"Error in rain_map_command: {e}")
        await message.answer(
            "Извините, произошла ошибка при получении информации об осадках. "
            "Пожалуйста, попробуйте позже."
        )

# Добавляем обработчики для кнопок масштабирования
@dp.callback_query(lambda c: c.data.startswith(('zoom_in_', 'zoom_out_')))
async def process_zoom(callback_query: types.CallbackQuery):
    """Обрабатывает нажатия кнопок масштабирования карты"""
    try:
        action, city = callback_query.data.split('_', 1)
        zoom = 10 if action == 'zoom_in' else 6
        
        # Получаем координаты города
        geo_url = f"http://api.openweathermap.org/geo/1.0/direct?q={city}&limit=1&appid={OPENWEATHER_API_KEY}"
        geo_response = requests.get(geo_url)
        geo_data = geo_response.json()
        
        if not geo_data:
            await callback_query.answer("Город не найден")
            return
            
        lat = geo_data[0]['lat']
        lon = geo_data[0]['lon']
        
        # Получаем новую карту с измененным масштабом
        map_url = await get_precipitation_map(lat, lon, zoom)
        
        if map_url:
            # Обновляем изображение
            await callback_query.message.edit_media(
                types.InputMediaPhoto(
                    media=map_url,
                    caption=f"🗺 Карта осадков для города {city}\n"
                            f"🔵 Синий цвет - дождь\n"
                            f"🟣 Фиолетовый цвет - смешанные осадки\n"
                            f"⚪️ Белый цвет - снег"
                ),
                reply_markup=callback_query.message.reply_markup
            )
            await callback_query.answer()
        else:
            await callback_query.answer("Не удалось обновить карту")
            
    except Exception as e:
        logging.error(f"Error in process_zoom: {e}")
        await callback_query.answer("Произошла ошибка при изменении масштаба")

async def get_city_coordinates(city_name: str) -> tuple[float, float, str] | None:
    """
    Получает координаты города с поддержкой разных языков и форматов написания.
    Возвращает кортеж (lat, lon, normalized_city_name) или None если город не найден.
    """
    try:
        # Список API для поиска города (в порядке приоритета)
        search_apis = [
            # OpenWeatherMap Geocoding API
            {
                'url': lambda city: f"http://api.openweathermap.org/geo/1.0/direct?q={quote(city)}&limit=1&appid={OPENWEATHER_API_KEY}&lang=ru",
                'extract': lambda data: (
                    float(data[0]['lat']),
                    float(data[0]['lon']),
                    data[0].get('local_names', {}).get('ru') or data[0]['name']
                ) if data else None
            },
            # Nominatim API
            {
                'url': lambda city: (
                    f"https://nominatim.openstreetmap.org/search"
                    f"?format=json&q={quote(city)}&limit=1&country=ru"
                ),
                'headers': {'User-Agent': 'WeatherBot/1.0'},
                'extract': lambda data: (
                    float(data[0]['lat']),
                    float(data[0]['lon']),
                    data[0]['display_name'].split(',')[0]
                ) if data else None
            },
            # Дополнительный поиск через OpenWeatherMap без языка
            {
                'url': lambda city: f"http://api.openweathermap.org/geo/1.0/direct?q={quote(city)}&limit=1&appid={OPENWEATHER_API_KEY}",
                'extract': lambda data: (
                    float(data[0]['lat']),
                    float(data[0]['lon']),
                    data[0]['name']
                ) if data else None
            }
        ]

        # Пробуем каждый API по очереди
        async with aiohttp.ClientSession() as session:
            for api in search_apis:
                try:
                    headers = api.get('headers', {})
                    url = api['url'](city_name)
                    
                    async with session.get(url, headers=headers) as response:
                        if response.status == 200:
                            data = await response.json()
                            if data:  # Если получили непустой ответ
                                result = api['extract'](data)
                                if result:
                                    return result
                except Exception as e:
                    logging.warning(f"Error with geocoding API: {e}")
                    continue

        # Если город все еще не найден, пробуем прямой поиск по базе городов России
        russian_cities = {
            'москва': (55.7558, 37.6173, 'Москва'),
            'санкт-петербург': (59.9343, 30.3351, 'Санкт-Петербург'),
            'новосибирск': (55.0084, 82.9357, 'Новосибирск'),
            'екатеринбург': (56.8519, 60.6122, 'Екатеринбург'),
            'нижний новгород': (56.2965, 43.9361, 'Нижний Новгород'),
            'казань': (55.7887, 49.1221, 'Казань'),
            'челябинск': (55.1644, 61.4368, 'Челябинск'),
            'омск': (54.9885, 73.3242, 'Омск'),
            'самара': (53.1959, 50.1001, 'Самара'),
            'ростов-на-дону': (47.2313, 39.7233, 'Ростов-на-Дону'),
            'уфа': (54.7348, 55.9578, 'Уфа'),
            'красноярск': (56.0090, 92.8719, 'Красноярск'),
            'воронеж': (51.6720, 39.1843, 'Воронеж'),
            'пермь': (58.0105, 56.2502, 'Пермь'),
            'волгоград': (48.7194, 44.5018, 'Волгоград'),
            'саратов': (51.5406, 46.0086, 'Саратов'),
            'краснодар': (45.0355, 38.9753, 'Краснодар'),
            'тюмень': (57.1529, 65.5343, 'Тюмень'),
            'тольятти': (53.5303, 49.3461, 'Тольятти'),
            'ижевск': (56.8498, 53.2045, 'Ижевск')
        }

        # Проверяем совпадение с базой городов (с учетом разных вариантов написания)
        city_lower = city_name.lower().replace('ё', 'е')
        for known_city, coords in russian_cities.items():
            if city_lower == known_city or city_lower in known_city or known_city in city_lower:
                return coords

        # Если город не найден всеми способами
        return None

    except Exception as e:
        logging.error(f"Error in get_city_coordinates: {e}")
        return None

@dp.message(lambda message: not message.text.startswith('/'))
async def get_weather(message: Message):
    """Get current weather for the specified city."""
    if message.text == "ℹ️ Помощь":
        await help_command(message)
        return

    try:
        # Получаем координаты города
        result = await get_city_coordinates(message.text)
        if not result:
            await message.answer(
                "Извините, не могу найти такой город. Попробуйте:\n"
                "1. Проверить правильность написания\n"
                "2. Использовать название на русском или английском\n"
                "3. Указать более крупный город поблизости"
            )
            return
            
        lat, lon, normalized_city = result
        
        # Get weather data
        weather_url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=ru"
        async with aiohttp.ClientSession() as session:
            async with session.get(weather_url) as response:
                weather_data = await response.json()
        
        # Check for weather alerts
        alerts = check_weather_alerts(weather_data)
        
        # Format and send detailed weather information
        detailed_message = format_detailed_weather(weather_data, normalized_city)
        if alerts:
            detailed_message += "\n\n" + "\n".join(alerts)
        await message.answer(detailed_message)
        
    except Exception as e:
        logging.error(f"Error getting weather: {e}")
        await message.answer(
            "Извините, произошла ошибка при получении прогноза погоды. "
            "Пожалуйста, попробуйте позже."
        )

async def process_update(request):
    """Обработчик входящих обновлений от Telegram"""
    data = await request.json()
    update = types.Update(**data)
    await dp.feed_update(bot=bot, update=update)
    return web.Response()

async def healthcheck(request):
    """Простой обработчик для проверки работоспособности"""
    return web.Response(text="Bot is running")

async def shutdown(dispatcher: Dispatcher):
    """Корректное завершение работы бота"""
    logging.info("Shutting down...")
    
    # Отключаем планировщик
    if scheduler:
        scheduler.shutdown(wait=False)
    
    # Закрываем соединения
    await dispatcher.storage.close()
    await dispatcher.storage.wait_closed()
    
    # Закрываем сессию бота
    session = await bot.get_session()
    if session:
        await session.close()
    
    # Останавливаем веб-приложение
    if runner:
        await runner.cleanup()

async def on_shutdown(app):
    """Действия при завершении работы веб-приложения"""
    logging.info("Stopping web application...")
    await shutdown(dp)

async def on_startup(app):
    """Действия при запуске"""
    webhook_path = f"/webhook/{TELEGRAM_BOT_TOKEN}"
    webhook_url = os.environ.get('RENDER_EXTERNAL_URL', 'http://localhost:8080') + webhook_path
    
    # Устанавливаем вебхук
    await bot.set_webhook(
        url=webhook_url,
        drop_pending_updates=True
    )
    
    # Устанавливаем команды бота
    await bot.set_my_commands(COMMANDS)
    print(f"Webhook set to {webhook_url}")
    print("Bot commands updated successfully")

async def check_weather_conditions(city, lat, lon):
    """Проверяет погодные условия и формирует предупреждения"""
    try:
        weather_url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=ru"
        async with aiohttp.ClientSession() as session:
            async with session.get(weather_url) as response:
                weather_data = await response.json()
        
        warnings = []
        
        # Проверяем температуру
        temp = weather_data['main']['temp']
        if temp > 30:
            warnings.append(f"🌡 Сильная жара в {city}: {temp:.1f}°C")
        elif temp < -15:
            warnings.append(f"❄️ Сильный мороз в {city}: {temp:.1f}°C")
        
        # Проверяем осадки
        if 'rain' in weather_data:
            rain = weather_data['rain'].get('1h', 0)
            if rain > 10:
                warnings.append(f"🌧 Сильный дождь в {city}: {rain} мм/ч")
        if 'snow' in weather_data:
            snow = weather_data['snow'].get('1h', 0)
            if snow > 5:
                warnings.append(f"🌨 Сильный снег в {city}: {snow} мм/ч")
        
        # Проверяем ветер
        wind_speed = weather_data['wind']['speed']
        if wind_speed > 15:
            warnings.append(f"💨 Сильный ветер в {city}: {wind_speed} м/с")
        
        # Обновляем статистику
        for param in ['temp', 'humidity', 'pressure']:
            weather_stats[city][param].append(weather_data['main'][param])
            # Храним только последние 24 значения
            weather_stats[city][param] = weather_stats[city][param][-24:]
        
        return warnings
        
    except Exception as e:
        logging.error(f"Error checking weather conditions: {e}")
        return []

async def send_weather_alerts():
    """Отправляет уведомления о погоде подписчикам"""
    for user_id, subscriptions in weather_subscriptions.items():
        for city, lat, lon in subscriptions:
            warnings = await check_weather_conditions(city, lat, lon)
            if warnings:
                try:
                    await bot.send_message(
                        user_id,
                        "⚠️ Предупреждения о погоде:\n" + "\n".join(warnings)
                    )
                except Exception as e:
                    logging.error(f"Error sending alert to user {user_id}: {e}")

async def find_nearby_shelters(lat, lon):
    """Находит ближайшие места укрытия от непогоды"""
    try:
        # Используем OpenStreetMap Nominatim API для поиска ближайших мест
        search_url = (
            f"https://nominatim.openstreetmap.org/search"
            f"?format=json"
            f"&lat={lat}"
            f"&lon={lon}"
            f"&amenity=shelter,shopping_mall,library,cafe"
            f"&limit=5"
        )
        
        async with aiohttp.ClientSession() as session:
            async with session.get(search_url, headers={'User-Agent': 'WeatherBot/1.0'}) as response:
                places = await response.json()
        
        return places
    except Exception as e:
        logging.error(f"Error finding shelters: {e}")
        return []

@dp.message(Command('subscribe'))
async def subscribe_command(message: Message):
    """Подписка на уведомления о погоде"""
    try:
        city = message.text.split(' ', 1)[1]
    except IndexError:
        await message.answer(
            "Пожалуйста, укажите город после команды.\n"
            "Например: /subscribe Москва"
        )
        return

    try:
        # Получаем координаты города
        result = await get_city_coordinates(city)
        if not result:
            await message.answer(
                "Извините, не могу найти указанный город. Пожалуйста:\n"
                "1. Проверьте правильность написания\n"
                "2. Убедитесь, что название города написано правильно\n"
                "3. Попробуйте указать более крупный город поблизости\n\n"
                "Примеры правильного написания:\n"
                "✅ Саратов\n"
                "✅ Нижний Новгород\n"
                "✅ Санкт-Петербург"
            )
            return
            
        lat, lon, normalized_city = result
        
        # Добавляем подписку
        user_id = message.from_user.id
        if (normalized_city, lat, lon) not in weather_subscriptions[user_id]:
            weather_subscriptions[user_id].append((normalized_city, lat, lon))
            save_subscriptions()
            
            # Сразу получаем текущую погоду для подтверждения
            weather_url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=ru"
            async with aiohttp.ClientSession() as session:
                async with session.get(weather_url) as response:
                    weather_data = await response.json()
            
            await message.answer(
                f"✅ Вы успешно подписались на уведомления о погоде в городе {normalized_city}\n\n"
                f"Текущая погода:\n"
                f"🌡 Температура: {weather_data['main']['temp']:.1f}°C\n"
                f"☁️ {weather_data['weather'][0]['description']}"
            )
        else:
            await message.answer(f"Вы уже подписаны на уведомления о погоде в городе {normalized_city}")
        
    except Exception as e:
        logging.error(f"Error in subscribe_command: {e}")
        await message.answer(
            "Извините, произошла ошибка при подписке на уведомления. "
            "Пожалуйста, попробуйте позже."
        )

@dp.message(Command('unsubscribe'))
async def unsubscribe_command(message: Message):
    """Отписка от уведомлений о погоде"""
    try:
        city = message.text.split(' ', 1)[1]
    except IndexError:
        await message.answer(
            "Пожалуйста, укажите город после команды.\n"
            "Например: /unsubscribe Москва"
        )
        return

    try:
        user_id = message.from_user.id
        # Находим и удаляем подписку
        for sub in weather_subscriptions[user_id][:]:
            if sub[0].lower() == city.lower():
                weather_subscriptions[user_id].remove(sub)
                save_subscriptions()
                await message.answer(f"✅ Вы успешно отписались от уведомлений о погоде в городе {city}")
                return
        
        await message.answer(f"Вы не были подписаны на уведомления о погоде в городе {city}")
        
    except Exception as e:
        logging.error(f"Error in unsubscribe_command: {e}")
        await message.answer(
            "Извините, произошла ошибка при отписке от уведомлений. "
            "Пожалуйста, попробуйте позже."
        )

@dp.message(Command('stats'))
async def stats_command(message: Message):
    """Показывает статистику погоды"""
    try:
        city = message.text.split(' ', 1)[1]
    except IndexError:
        await message.answer(
            "Пожалуйста, укажите город после команды.\n"
            "Например: /stats Москва"
        )
        return

    try:
        if city not in weather_stats or not weather_stats[city]['temp']:
            await message.answer(
                f"Извините, для города {city} пока нет статистики. "
                "Статистика начнет собираться после подписки на уведомления."
            )
            return
        
        # Формируем статистику
        stats = weather_stats[city]
        temp_avg = sum(stats['temp']) / len(stats['temp'])
        temp_min = min(stats['temp'])
        temp_max = max(stats['temp'])
        humidity_avg = sum(stats['humidity']) / len(stats['humidity'])
        
        message_text = (
            f"📊 Статистика погоды в городе {city} (за последние 24 часа):\n\n"
            f"🌡 Температура:\n"
            f"   • Средняя: {temp_avg:.1f}°C\n"
            f"   • Минимальная: {temp_min:.1f}°C\n"
            f"   • Максимальная: {temp_max:.1f}°C\n"
            f"💧 Средняя влажность: {humidity_avg:.1f}%"
        )
        
        await message.answer(message_text)
        
    except Exception as e:
        logging.error(f"Error in stats_command: {e}")
        await message.answer(
            "Извините, произошла ошибка при получении статистики. "
            "Пожалуйста, попробуйте позже."
        )

@dp.message(Command('shelter'))
async def shelter_command(message: Message):
    """Поиск укрытия от непогоды"""
    try:
        # Проверяем, передано ли название города
        try:
            city = message.text.split(' ', 1)[1]
            # Получаем координаты города
            result = await get_city_coordinates(city)
            if not result:
                await message.answer(
                    "Извините, не могу найти такой город. Попробуйте:\n"
                    "1. Проверить правильность написания\n"
                    "2. Использовать название на русском или английском\n"
                    "3. Указать более крупный город поблизости\n\n"
                    "Или отправьте свою геолокацию, нажав на кнопку ниже 📍"
                )
                return
            lat, lon, normalized_city = result
        except IndexError:
            # Если город не указан, запрашиваем геолокацию
            keyboard = ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="📍 Отправить геолокацию", request_location=True)]
                ],
                resize_keyboard=True,
                one_time_keyboard=True
            )
            await message.answer(
                "Пожалуйста, отправьте свою геолокацию, нажав на кнопку ниже, "
                "или укажите название города после команды:\n"
                "Например: /shelter Москва",
                reply_markup=keyboard
            )
            return
        
        # Получаем список ближайших укрытий
        shelters = await find_nearby_shelters(lat, lon)
        
        if not shelters:
            await message.answer(
                "Извините, не удалось найти укрытия поблизости. "
                "Попробуйте искать торговые центры или кафе в этом районе."
            )
            return
        
        # Формируем сообщение с укрытиями
        message_text = f"🏪 Ближайшие места, где можно укрыться от непогоды в районе {normalized_city if 'normalized_city' in locals() else 'вашей геолокации'}:\n\n"
        
        for place in shelters:
            distance = ((float(place['lat']) - lat) ** 2 + (float(place['lon']) - lon) ** 2) ** 0.5 * 111  # примерное расстояние в км
            name = place['display_name'].split(',')[0]
            address = ', '.join(place['display_name'].split(',')[1:]).strip()
            
            message_text += (
                f"📍 {name}\n"
                f"   📏 Расстояние: {distance:.1f} км\n"
                f"   🏠 Адрес: {address}\n\n"
            )
            
            # Добавляем кнопку для открытия карты
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🗺 Открыть на карте",
                            url=f"https://www.openstreetmap.org/?mlat={place['lat']}&mlon={place['lon']}&zoom=17"
                        )
                    ]
                ]
            )
        
        await message.answer(message_text, reply_markup=keyboard)
        
    except Exception as e:
        logging.error(f"Error in shelter_command: {e}")
        await message.answer(
            "Извините, произошла ошибка при поиске укрытий. "
            "Пожалуйста, попробуйте позже."
        )

def save_subscriptions():
    """Сохраняет подписки в файл"""
    with open('weather_subscriptions.json', 'w', encoding='utf-8') as f:
        json.dump({str(k): v for k, v in weather_subscriptions.items()}, f, ensure_ascii=False)

def load_subscriptions():
    """Загружает подписки из файла"""
    global weather_subscriptions
    try:
        if os.path.exists('weather_subscriptions.json'):
            with open('weather_subscriptions.json', 'r', encoding='utf-8') as f:
                data = json.load(f)
                weather_subscriptions = defaultdict(list, {int(k): v for k, v in data.items()})
    except Exception as e:
        logging.error(f"Error loading subscriptions: {e}")

def save_user_preferences():
    """Сохраняет настройки пользователей в файл"""
    with open('user_preferences.json', 'w', encoding='utf-8') as f:
        json.dump({str(k): v for k, v in user_preferences.items()}, f, ensure_ascii=False)

def load_user_preferences():
    """Загружает настройки пользователей из файла"""
    global user_preferences
    try:
        if os.path.exists('user_preferences.json'):
            with open('user_preferences.json', 'r', encoding='utf-8') as f:
                data = json.load(f)
                user_preferences.update({int(k): v for k, v in data.items()})
    except Exception as e:
        logging.error(f"Error loading user preferences: {e}")

async def check_weather_changes(city, lat, lon, prev_temp):
    """Проверяет резкие изменения погоды"""
    try:
        weather_url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=ru"
        async with aiohttp.ClientSession() as session:
            async with session.get(weather_url) as response:
                weather_data = await response.json()
        
        curr_temp = weather_data['main']['temp']
        temp_change = abs(curr_temp - prev_temp)
        
        if temp_change >= 5:  # Изменение на 5°C или более
            return f"🌡 Резкое изменение температуры в {city}: {temp_change:.1f}°C"
        return None
        
    except Exception as e:
        logging.error(f"Error checking weather changes: {e}")
        return None

async def check_activity_conditions(city, lat, lon, activities):
    """Проверяет условия для активностей"""
    try:
        weather_url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=ru"
        async with aiohttp.ClientSession() as session:
            async with session.get(weather_url) as response:
                weather_data = await response.json()
        
        temp = weather_data['main']['temp']
        wind_speed = weather_data['wind']['speed']
        is_rain = 'rain' in weather_data
        
        suitable_activities = []
        
        for activity in activities:
            if activity not in ACTIVITIES:
                continue
                
            conditions = ACTIVITIES[activity]
            temp_min, temp_max = conditions['temp_range']
            
            if (temp_min <= temp <= temp_max and
                wind_speed <= conditions['wind_max'] and
                (not conditions['no_rain'] or not is_rain)):
                suitable_activities.append(conditions['description'])
        
        if suitable_activities:
            return f"🎯 Отличные условия в {city} для: {', '.join(suitable_activities)}!"
        return None
        
    except Exception as e:
        logging.error(f"Error checking activity conditions: {e}")
        return None

async def send_smart_notifications():
    """Отправляет умные уведомления пользователям"""
    current_hour = datetime.now().strftime('%H:00')
    
    for user_id, prefs in user_preferences.items():
        if prefs['notification_time'] != current_hour:
            continue
            
        try:
            for city, lat, lon in weather_subscriptions[user_id]:
                notifications = []
                
                # Проверяем условия для активностей
                if prefs['activities']:
                    activity_notice = await check_activity_conditions(city, lat, lon, prefs['activities'])
                    if activity_notice:
                        notifications.append(activity_notice)
                
                # Получаем текущую погоду
                weather_url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=ru"
                async with aiohttp.ClientSession() as session:
                    async with session.get(weather_url) as response:
                        weather_data = await response.json()
                
                temp = weather_data['main']['temp']
                
                # Проверяем температурный диапазон
                if not (prefs['temp_range']['min'] <= temp <= prefs['temp_range']['max']):
                    notifications.append(
                        f"🌡 Температура в {city} ({temp:.1f}°C) вне вашего комфортного диапазона "
                        f"({prefs['temp_range']['min']}°C - {prefs['temp_range']['max']}°C)"
                    )
                
                # Проверяем ветер
                wind_speed = weather_data['wind']['speed']
                if wind_speed > prefs['wind_threshold']:
                    notifications.append(f"💨 Сильный ветер в {city}: {wind_speed} м/с")
                
                # Проверяем дождь
                if prefs['rain_alerts'] and 'rain' in weather_data:
                    rain = weather_data['rain'].get('1h', 0)
                    if rain > 0:
                        notifications.append(f"🌧 Ожидается дождь в {city}: {rain} мм/ч")
                
                if notifications:
                    await bot.send_message(
                        user_id,
                        "🔔 Умные уведомления:\n\n" + "\n\n".join(notifications)
                    )
                    
        except Exception as e:
            logging.error(f"Error sending smart notifications to user {user_id}: {e}")

@dp.message(Command('preferences'))
async def preferences_command(message: Message):
    """Настройка предпочтений пользователя"""
    user_id = message.from_user.id
    
    # Создаем клавиатуру с настройками
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🌡 Температурный диапазон",
                    callback_data="set_temp_range"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🌧 Уведомления о дожде",
                    callback_data="toggle_rain"
                )
            ],
            [
                InlineKeyboardButton(
                    text="💨 Порог скорости ветра",
                    callback_data="set_wind"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⚡️ Уведомления о резких изменениях",
                    callback_data="toggle_changes"
                )
            ]
        ]
    )
    
    prefs = user_preferences[user_id]
    await message.answer(
        f"⚙️ Текущие настройки уведомлений:\n\n"
        f"🌡 Комфортная температура: {prefs['temp_range']['min']}°C - {prefs['temp_range']['max']}°C\n"
        f"🌧 Уведомления о дожде: {'Включены' if prefs['rain_alerts'] else 'Выключены'}\n"
        f"💨 Порог ветра: {prefs['wind_threshold']} м/с\n"
        f"⚡️ Уведомления об изменениях: {'Включены' if prefs['notify_changes'] else 'Выключены'}\n"
        f"⏰ Время уведомлений: {prefs['notification_time']}\n\n"
        f"Выберите настройку для изменения:",
        reply_markup=keyboard
    )

@dp.message(Command('activities'))
async def activities_command(message: Message):
    """Настройка предпочитаемых активностей"""
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🏃‍♂️ Бег",
                    callback_data="toggle_activity_running"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🚴‍♂️ Велопрогулки",
                    callback_data="toggle_activity_cycling"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🚶‍♂️ Прогулки",
                    callback_data="toggle_activity_walking"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🧺 Пикник",
                    callback_data="toggle_activity_picnic"
                )
            ]
        ]
    )
    
    user_id = message.from_user.id
    current_activities = user_preferences[user_id]['activities']
    
    activities_text = "Нет выбранных активностей"
    if current_activities:
        activities_text = "\n".join(f"• {ACTIVITIES[act]['description']}" for act in current_activities)
    
    await message.answer(
        f"🎯 Выберите интересующие вас активности, и я буду уведомлять "
        f"вас когда погодные условия будут подходящими.\n\n"
        f"Текущие активности:\n{activities_text}",
        reply_markup=keyboard
    )

@dp.message(Command('notifytime'))
async def notifytime_command(message: Message):
    """Установка времени уведомлений"""
    try:
        time_str = message.text.split(' ', 1)[1]
        # Проверяем формат времени
        try:
            hour = int(time_str.split(':')[0])
            if not (0 <= hour <= 23):
                raise ValueError
            new_time = f"{hour:02d}:00"
        except:
            await message.answer(
                "Пожалуйста, укажите время в формате ЧЧ:00\n"
                "Например: /notifytime 09:00"
            )
            return
        
        user_id = message.from_user.id
        user_preferences[user_id]['notification_time'] = new_time
        save_user_preferences()
        
        await message.answer(f"✅ Время уведомлений установлено на {new_time}")
        
    except IndexError:
        await message.answer(
            "Пожалуйста, укажите время после команды.\n"
            "Например: /notifytime 09:00"
        )

@dp.callback_query(lambda c: c.data.startswith('toggle_activity_'))
async def process_activity_toggle(callback_query: types.CallbackQuery):
    """Обработка переключения активностей"""
    activity = callback_query.data.replace('toggle_activity_', '')
    user_id = callback_query.from_user.id
    
    if activity in user_preferences[user_id]['activities']:
        user_preferences[user_id]['activities'].remove(activity)
        status = 'удалена из'
    else:
        user_preferences[user_id]['activities'].append(activity)
        status = 'добавлена в'
    
    save_user_preferences()
    
    await callback_query.answer(
        f"Активность {ACTIVITIES[activity]['description']} {status} список"
    )
    
    # Обновляем сообщение с текущим списком активностей
    current_activities = user_preferences[user_id]['activities']
    activities_text = "Нет выбранных активностей"
    if current_activities:
        activities_text = "\n".join(f"• {ACTIVITIES[act]['description']}" for act in current_activities)
    
    await callback_query.message.edit_text(
        f"🎯 Выберите интересующие вас активности, и я буду уведомлять "
        f"вас когда погодные условия будут подходящими.\n\n"
        f"Текущие активности:\n{activities_text}",
        reply_markup=callback_query.message.reply_markup
    )

async def main():
    """Start the bot."""
    global scheduler, app, runner
    
    try:
        # Настраиваем логирование
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        
        # Загружаем сохраненные данные
        load_subscriptions()
        load_user_preferences()
        
        # Инициализируем планировщик
        scheduler = AsyncIOScheduler()
        scheduler.add_job(send_weather_alerts, 'interval', minutes=30)
        scheduler.add_job(send_smart_notifications, 'interval', minutes=60)
        scheduler.start()
        
        # Создаем веб-приложение
        app = web.Application()
        
        # Добавляем обработчики
        webhook_path = f"/webhook/{TELEGRAM_BOT_TOKEN}"
        app.router.add_post(webhook_path, process_update)
        app.router.add_get("/", healthcheck)
        
        # Добавляем обработчики событий приложения
        app.on_startup.append(on_startup)
        app.on_shutdown.append(on_shutdown)
        
        # Получаем порт из переменных окружения
        port = int(os.environ.get('PORT', 8080))
        
        # Запускаем веб-сервер
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', port)
        
        # Устанавливаем обработчики сигналов
        for signal_name in ('SIGINT', 'SIGTERM'):
            try:
                signal.signal(
                    getattr(signal, signal_name),
                    lambda s, f: asyncio.create_task(shutdown(dp))
                )
            except AttributeError:
                pass
        
        # Запускаем бота
        await site.start()
        logging.info(f"Bot started on port {port}")
        
        # Ждем завершения
        await asyncio.Event().wait()
        
    except Exception as e:
        logging.error(f"Critical error in main: {e}", exc_info=True)
        if runner:
            await runner.cleanup()
        sys.exit(1)

# Обновим точку входа
if __name__ == '__main__':
    try:
        # Запускаем бота в отдельной задаче
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Запускаем основной цикл с обработкой исключений
        with suppress(KeyboardInterrupt, SystemExit):
            loop.run_until_complete(main())
    except Exception as e:
        logging.error(f"Fatal error: {e}", exc_info=True)
    finally:
        # Закрываем loop
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()
        logging.info("Bot stopped")