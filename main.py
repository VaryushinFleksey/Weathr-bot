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
from contextlib import suppress, contextmanager
import traceback
import functools
from aiohttp import ClientTimeout
from asyncio import Lock
import time as time_module
import sqlite3

# Database configuration
DB_FILE = 'weather_bot.db'

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Database functions
@contextmanager
def get_db():
    """Контекстный менеджер для работы с базой данных"""
    conn = sqlite3.connect(DB_FILE)
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    """Инициализация базы данных"""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Создаем таблицу пользователей
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                language_code TEXT,
                is_premium BOOLEAN,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Создаем таблицу подписок
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id INTEGER,
                city TEXT,
                lat REAL,
                lon REAL,
                PRIMARY KEY (user_id, city)
            )
        ''')
        
        # Создаем таблицу настроек пользователей
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id INTEGER PRIMARY KEY,
                notification_time TEXT,
                temp_min INTEGER,
                temp_max INTEGER,
                wind_threshold INTEGER,
                rain_alerts BOOLEAN,
                activities TEXT
            )
        ''')
        
        # Создаем таблицу статистики погоды
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS weather_stats (
                city TEXT,
                timestamp INTEGER,
                temperature REAL,
                humidity INTEGER,
                wind_speed REAL,
                PRIMARY KEY (city, timestamp)
            )
        ''')
        conn.commit()
        logger.info("Database initialized successfully")

def save_user_info(user: types.User):
    """Сохраняет информацию о пользователе в базу данных"""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO users (
                    user_id, username, first_name, last_name,
                    language_code, is_premium, joined_at
                ) VALUES (?, ?, ?, ?, ?, ?, COALESCE(
                    (SELECT joined_at FROM users WHERE user_id = ?),
                    CURRENT_TIMESTAMP
                ))
            ''', (
                user.id,
                user.username,
                user.first_name,
                user.last_name,
                user.language_code,
                user.is_premium,
                user.id
            ))
            conn.commit()
            logger.info(f"User info saved: {user.id} ({user.username or user.first_name})")
    except Exception as e:
        log_error(e, f"Error saving user info for user {user.id}")
        raise

# Rate limiting configuration
RATE_LIMIT = 1  # seconds between requests
rate_limit_dict = defaultdict(lambda: 0)
rate_limit_lock = Lock()

# API timeout settings
API_TIMEOUT = ClientTimeout(total=10)  # 10 seconds timeout for API calls

# Load environment variables
load_dotenv()

# Get and validate environment variables
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
OPENWEATHER_API_KEY = os.getenv('OPENWEATHER_API_KEY')

if not TELEGRAM_BOT_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN not found in environment variables")
    sys.exit(1)

if not OPENWEATHER_API_KEY:
    logger.error("OPENWEATHER_API_KEY not found in environment variables")
    sys.exit(1)

# Rate limiting decorator
def rate_limit(limit: float):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            message = next((arg for arg in args if isinstance(arg, Message)), None)
            if message:
                user_id = message.from_user.id
                async with rate_limit_lock:
                    last_time = rate_limit_dict[user_id]
                    current_time = time_module.time()
                    if current_time - last_time < limit:
                        await message.answer(
                            "Пожалуйста, подождите немного перед следующим запросом."
                        )
                        return
                    rate_limit_dict[user_id] = current_time
            return await func(*args, **kwargs)
        return wrapper
    return decorator

# Добавляем функцию для логирования ошибок с полным трейсбеком
def log_error(error: Exception, message: str = None):
    """Логирует ошибку с полным трейсбеком"""
    if message:
        logger.error(f"{message}: {str(error)}")
    else:
        logger.error(str(error))
    logger.error(traceback.format_exc())

# Декоратор для отслеживания выполнения функций
def log_execution(func):
    """Декоратор для логирования выполнения функций"""
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        logger.info(f"Entering function: {func.__name__}")
        try:
            result = await func(*args, **kwargs)
            logger.info(f"Successfully completed: {func.__name__}")
            return result
        except Exception as e:
            log_error(e, f"Error in {func.__name__}")
            raise
    return wrapper

# Initialize bot and dispatcher
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
scheduler = None
app = None
runner = None

# Initialize database
init_db()

# Database context manager
@contextmanager
def get_db():
    """Контекстный менеджер для работы с базой данных"""
    conn = sqlite3.connect(DB_FILE)
    try:
        yield conn
    finally:
        conn.close()

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

def save_user_info(user: types.User):
    """Сохраняет информацию о пользователе в базу данных"""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO users (
                    user_id, username, first_name, last_name,
                    language_code, is_premium, joined_at
                ) VALUES (?, ?, ?, ?, ?, ?, COALESCE(
                    (SELECT joined_at FROM users WHERE user_id = ?),
                    CURRENT_TIMESTAMP
                ))
            ''', (
                user.id,
                user.username,
                user.first_name,
                user.last_name,
                user.language_code,
                user.is_premium,
                user.id
            ))
            conn.commit()
            logger.info(f"User info saved: {user.id} ({user.username or user.first_name})")
    except Exception as e:
        log_error(e, f"Error saving user info for user {user.id}")
        raise

@dp.message(CommandStart())
@log_execution
async def start_command(message: Message):
    """Обработчик команды /start"""
    try:
        user = message.from_user
        user_id = user.id
        user_name = user.first_name
        
        # Сохраняем информацию о пользователе
        save_user_info(user)
        
        # Создаем настройки по умолчанию для нового пользователя
        prefs = get_user_preferences_db(user_id)
        if not prefs:
            default_prefs = {
                'notification_time': '09:00',
                'temp_range': {'min': 15, 'max': 25},
                'wind_threshold': 10,
                'rain_alerts': True,
                'activities': []
            }
            save_user_preferences_db(user_id, default_prefs)
        
        # Получаем информацию о пользователе из базы данных
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT joined_at FROM users WHERE user_id = ?', (user_id,))
            joined_at = cursor.fetchone()[0]
        
        await message.answer(
            f"👋 Привет, {user_name}!\n\n"
            f"Вы с нами с: {joined_at}\n\n"
            "Я бот погоды с умными уведомлениями. Вот что я умею:\n\n"
            "🌤 /weather [город] - текущая погода\n"
            "🔔 /subscribe [город] - подписка на уведомления\n"
            "🚫 /unsubscribe [город] - отписка от уведомлений\n"
            "📋 /list - список ваших подписок\n"
            "⚙️ /preferences - настройка уведомлений\n"
            "🏃‍♂️ /activities - настройка активностей\n"
            "🏘 /shelter [город] - найти укрытие от непогоды\n\n"
            "Используйте эти команды для управления подписками и настройками."
        )
        logger.info(f"User interaction: {user_id} ({user_name}) used /start command")
    except Exception as e:
        log_error(e, f"Error in start command for user {message.from_user.id}")
        await message.answer("😔 Произошла ошибка при обработке команды. Попробуйте позже.")

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

@log_execution
async def get_city_coordinates(city_name: str) -> tuple[float, float, str] | None:
    """Получает координаты города с поддержкой разных языков и форматов написания."""
    try:
        logger.info(f"Searching coordinates for city: {city_name}")
        
        # Сначала проверяем в базе российских городов
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

        # Нормализуем введенный город
        city_lower = city_name.lower().replace('ё', 'е').strip()
        
        # Проверяем точное совпадение
        if city_lower in russian_cities:
            logger.info(f"Found exact match in Russian cities database: {city_lower}")
            return russian_cities[city_lower]
            
        # Проверяем частичное совпадение
        for known_city, coords in russian_cities.items():
            if city_lower in known_city or known_city in city_lower:
                logger.info(f"Found partial match in Russian cities database: {known_city}")
                return coords

        # Если город не найден в базе, используем API
        search_apis = [
            # OpenWeatherMap Geocoding API с русской локализацией
            {
                'url': lambda city: f"http://api.openweathermap.org/geo/1.0/direct?q={quote(city)}&limit=1&appid={OPENWEATHER_API_KEY}&lang=ru",
                'extract': lambda data: (
                    float(data[0]['lat']),
                    float(data[0]['lon']),
                    data[0].get('local_names', {}).get('ru') or data[0]['name']
                ) if data else None
            },
            # Nominatim API с фокусом на Россию
            {
                'url': lambda city: (
                    f"https://nominatim.openstreetmap.org/search"
                    f"?format=json&q={quote(city)}&limit=1&countrycodes=ru"
                ),
                'headers': {'User-Agent': 'WeatherBot/1.0'},
                'extract': lambda data: (
                    float(data[0]['lat']),
                    float(data[0]['lon']),
                    data[0]['display_name'].split(',')[0]
                ) if data else None
            }
        ]

        # Пробуем каждый API по очереди
        async with aiohttp.ClientSession() as session:
            for api in search_apis:
                try:
                    headers = api.get('headers', {})
                    url = api['url'](city_name)
                    logger.info(f"Trying API: {url}")
                    
                    async with session.get(url, headers=headers) as response:
                        if response.status == 200:
                            data = await response.json()
                            if data:  # Если получили непустой ответ
                                result = api['extract'](data)
                                if result:
                                    logger.info(f"Found city via API: {result[2]}")
                                    return result
                except Exception as e:
                    logger.warning(f"Error with geocoding API: {e}")
                    continue

        logger.warning(f"City not found: {city_name}")
        return None

    except Exception as e:
        log_error(e, f"Error in get_city_coordinates for city {city_name}")
        return None

@log_execution
async def find_nearby_shelters(lat, lon):
    """Находит ближайшие места укрытия от непогоды"""
    try:
        logger.info(f"Searching shelters near coordinates: {lat}, {lon}")
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
@log_execution
@rate_limit(RATE_LIMIT)
async def subscribe_command(message: Message):
    """Обработчик команды /subscribe"""
    try:
        user_id = message.from_user.id
        
        try:
            city = message.text.split(' ', 1)[1]
        except IndexError:
            await message.answer(
                "Пожалуйста, укажите город после команды.\n"
                "Например: /subscribe Москва"
            )
            return
        
        # Получаем координаты города
        result = await get_city_coordinates(city)
        if not result:
            await message.answer(
                "Извините, не могу найти такой город. Попробуйте:\n"
                "1. Проверить правильность написания\n"
                "2. Использовать название на русском или английском\n"
                "3. Указать более точное название"
            )
            return
            
        lat, lon, city_name = result
        
        # Проверяем существующие подписки
        subscriptions = get_user_subscriptions(user_id)
        
        # Проверяем, не подписан ли уже пользователь на этот город
        if any(sub[0].lower() == city_name.lower() for sub in subscriptions):
            await message.answer(f"Вы уже подписаны на погоду в городе {city_name}")
            return
        
        # Проверяем количество подписок (ограничение на 5 городов)
        if len(subscriptions) >= 5:
            await message.answer(
                "Вы уже подписаны на максимальное количество городов (5).\n"
                "Чтобы подписаться на новый город, сначала отпишитесь от одного из текущих:\n" +
                "\n".join(f"• {city} (/unsubscribe {city})" for city, _, _ in subscriptions)
            )
            return
        
        # Добавляем подписку
        save_subscription(user_id, city_name, lat, lon)
        
        # Создаем настройки по умолчанию, если их нет
        prefs = get_user_preferences_db(user_id)
        if not prefs:
            default_prefs = {
                'notification_time': '09:00',
                'temp_range': {'min': 15, 'max': 25},
                'wind_threshold': 10,
                'rain_alerts': True,
                'activities': []
            }
            save_user_preferences_db(user_id, default_prefs)
        
        # Получаем текущую погоду для подтверждения
        weather_url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=ru"
        async with aiohttp.ClientSession(timeout=API_TIMEOUT) as session:
            async with session.get(weather_url) as response:
                weather_data = await check_api_response(response, "subscribe_command")
        
        # Сохраняем статистику
        save_weather_stats(
            city_name,
            weather_data['main']['temp'],
            weather_data['main']['humidity'],
            weather_data['wind']['speed']
        )
        
        # Формируем сообщение с подтверждением
        confirmation = (
            f"✅ Вы успешно подписались на уведомления о погоде в городе {city_name}\n\n"
            f"Текущая погода:\n"
            f"🌡 Температура: {weather_data['main']['temp']:.1f}°C\n"
            f"☁️ {weather_data['weather'][0]['description']}\n\n"
            f"Вы будете получать:\n"
            f"• Уведомления о важных изменениях погоды\n"
            f"• Предупреждения о неблагоприятных условиях\n"
            f"• Ежедневный прогноз в выбранное время\n\n"
            f"Чтобы настроить уведомления, используйте:\n"
            f"⚙️ /preferences - общие настройки уведомлений\n"
            f"⏰ /notifytime - время ежедневных уведомлений\n"
            f"🎯 /activities - настройка предпочитаемых активностей"
        )
        
        await message.answer(confirmation)
        logger.info(f"User {user_id} subscribed to {city_name}")
        
    except Exception as e:
        log_error(e, f"Error in subscribe command for user {message.from_user.id}")
        await message.answer("😔 Произошла ошибка при подписке. Попробуйте позже.")

@dp.message(Command('unsubscribe'))
@log_execution
@rate_limit(RATE_LIMIT)
async def unsubscribe_command(message: Message):
    """Обработчик команды /unsubscribe"""
    try:
        user_id = message.from_user.id
        
        try:
            city = message.text.split(' ', 1)[1]
        except IndexError:
            await message.answer(
                "Пожалуйста, укажите город после команды.\n"
                "Например: /unsubscribe Москва"
            )
            return
        
        # Получаем подписки пользователя
        subscriptions = get_user_subscriptions(user_id)
        if not subscriptions:
            await message.answer("У вас нет активных подписок на погоду")
            return
        
        # Ищем город в подписках
        city_lower = city.lower()
        for subscribed_city, _, _ in subscriptions:
            if subscribed_city.lower() == city_lower:
                with get_db() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        'DELETE FROM subscriptions WHERE user_id = ? AND city = ?',
                        (user_id, subscribed_city)
                    )
                await message.answer(f"✅ Вы успешно отписались от погоды в городе {subscribed_city}")
                logger.info(f"User {user_id} unsubscribed from {subscribed_city}")
                return
        
        await message.answer(f"Вы не подписаны на погоду в городе {city}")
        
    except Exception as e:
        log_error(e, f"Error in unsubscribe command for user {message.from_user.id}")
        await message.answer("😔 Произошла ошибка при отписке. Попробуйте позже.")

@dp.message(Command('stats'))
@log_execution
@rate_limit(RATE_LIMIT)
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
        # Получаем статистику из базы данных
        stats = get_weather_stats(city)
        if not stats:
            await message.answer(
                f"Извините, для города {city} пока нет статистики. "
                "Статистика начнет собираться после подписки на уведомления."
            )
            return
        
        # Формируем сообщение со статистикой
        message_text = (
            f"📊 Статистика погоды в городе {city} (за последние 24 часа):\n\n"
            f"🌡 Температура:\n"
            f"   • Средняя: {stats['temp_avg']:.1f}°C\n"
            f"   • Минимальная: {stats['temp_min']:.1f}°C\n"
            f"   • Максимальная: {stats['temp_max']:.1f}°C\n"
            f"💧 Средняя влажность: {stats['humidity_avg']:.1f}%\n"
            f"💨 Средняя скорость ветра: {stats['wind_speed_avg']:.1f} м/с"
        )
        
        await message.answer(message_text)
        
    except Exception as e:
        log_error(e, f"Error in stats command for user {message.from_user.id}")
        await message.answer(
            "Извините, произошла ошибка при получении статистики. "
            "Пожалуйста, попробуйте позже."
        )

@dp.message(Command('shelter'))
@log_execution
async def shelter_command(message: Message):
    """Поиск укрытия от непогоды"""
    try:
        user_id = message.from_user.id
        logger.info(f"Processing shelter command for user {user_id}")
        
        # Проверяем, передано ли название города
        try:
            city = message.text.split(' ', 1)[1]
            logger.info(f"Searching shelters for city: {city}")
            
            # Получаем координаты города
            result = await get_city_coordinates(city)
            if not result:
                await message.answer(
                    "Извините, не могу найти такой город. Попробуйте:\n"
                    "1. Проверить правильность написания\n"
                    "2. Использовать название на русском или английском\n"
                    "3. Указать более крупный город поблизости\n\n"
                    "Или отправьте свою геолокацию, нажав на кнопку ниже 📍",
                    reply_markup=ReplyKeyboardMarkup(
                        keyboard=[[KeyboardButton(text="📍 Отправить геолокацию", request_location=True)]],
                        resize_keyboard=True,
                        one_time_keyboard=True
                    )
                )
                return
                
            lat, lon, normalized_city = result
            logger.info(f"Found coordinates for {normalized_city}: {lat}, {lon}")
            
        except IndexError:
            # Если город не указан, запрашиваем геолокацию
            keyboard = ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="📍 Отправить геолокацию", request_location=True)]],
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
        logger.info(f"Found {len(shelters)} shelters near {normalized_city}")
        
        if not shelters:
            await message.answer(
                "Извините, не удалось найти укрытия поблизости. "
                "Попробуйте искать торговые центры или кафе в этом районе."
            )
            return
        
        # Формируем сообщение с укрытиями
        message_text = f"🏪 Ближайшие места, где можно укрыться от непогоды в районе {normalized_city}:\n\n"
        
        # Создаем список кнопок для каждого укрытия
        keyboard_buttons = []
        
        for place in shelters:
            distance = ((float(place['lat']) - lat) ** 2 + (float(place['lon']) - lon) ** 2) ** 0.5 * 111  # примерное расстояние в км
            name = place['display_name'].split(',')[0]
            address = ', '.join(place['display_name'].split(',')[1:]).strip()
            
            message_text += (
                f"📍 {name}\n"
                f"   📏 Расстояние: {distance:.1f} км\n"
                f"   🏠 Адрес: {address}\n\n"
            )
            
            # Добавляем кнопку для этого места
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text=f"🗺 {name} на карте",
                    url=f"https://www.openstreetmap.org/?mlat={place['lat']}&mlon={place['lon']}&zoom=17"
                )
            ])
        
        # Создаем клавиатуру с кнопками для всех укрытий
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        # Отправляем сообщение с информацией и кнопками
        await message.answer(message_text, reply_markup=keyboard)
        logger.info(f"Successfully sent shelter information to user {user_id}")
        
    except Exception as e:
        log_error(e, f"Error in shelter_command for user {message.from_user.id}")
        await message.answer(
            "Извините, произошла ошибка при поиске укрытий. "
            "Пожалуйста, попробуйте позже."
        )

def save_subscriptions():
    """Сохраняет подписки в файл"""
    try:
        with open('subscriptions.json', 'w', encoding='utf-8') as f:
            json.dump(weather_subscriptions, f, ensure_ascii=False, indent=2)
        logger.info("Subscriptions saved successfully")
    except Exception as e:
        log_error(e, "Error saving subscriptions")

def load_subscriptions():
    """Загружает подписки из файла"""
    global weather_subscriptions
    try:
        with open('subscriptions.json', 'r', encoding='utf-8') as f:
            weather_subscriptions = json.load(f)
        logger.info("Subscriptions loaded successfully")
    except FileNotFoundError:
        weather_subscriptions = defaultdict(list)
        logger.info("No subscriptions file found, starting with empty list")
    except Exception as e:
        log_error(e, "Error loading subscriptions")
        weather_subscriptions = defaultdict(list)

def save_user_preferences():
    """Сохраняет пользовательские настройки в файл"""
    try:
        with open('preferences.json', 'w', encoding='utf-8') as f:
            json.dump(user_preferences, f, ensure_ascii=False, indent=2)
        logger.info("User preferences saved successfully")
    except Exception as e:
        log_error(e, "Error saving user preferences")

def load_user_preferences():
    """Загружает пользовательские настройки из файла"""
    global user_preferences
    try:
        with open('preferences.json', 'r', encoding='utf-8') as f:
            user_preferences = json.load(f)
        logger.info("User preferences loaded successfully")
    except FileNotFoundError:
        user_preferences = {}
        logger.info("No preferences file found, starting with empty dict")
    except Exception as e:
        log_error(e, "Error loading user preferences")
        user_preferences = {}

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
        logger.info(f"Checking activity conditions for {city}")
        weather_url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=ru"
        async with aiohttp.ClientSession() as session:
            async with session.get(weather_url) as response:
                weather_data = await check_api_response(response, "check_activity_conditions")
        
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
        log_error(e, f"Error checking activity conditions for {city}")
        return None

@log_execution
async def send_smart_notifications():
    """Отправляет умные уведомления пользователям"""
    logger.info("Starting smart notifications check")
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
                        weather_data = await check_api_response(response, "send_smart_notifications")
                
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
                    logger.info(f"Sent {len(notifications)} smart notifications to user {user_id} for {city}")
                    
        except Exception as e:
            log_error(e, f"Error sending smart notifications to user {user_id}")

@dp.message(Command('preferences'))
@log_execution
@rate_limit(RATE_LIMIT)
async def preferences_command(message: Message):
    """Обработчик команды /preferences"""
    try:
        user_id = message.from_user.id
        
        # Получаем текущие настройки
        prefs = get_user_preferences_db(user_id)
        if not prefs:
            # Создаем настройки по умолчанию
            prefs = {
                'notification_time': '09:00',
                'temp_range': {'min': 15, 'max': 25},
                'wind_threshold': 10,
                'rain_alerts': True,
                'activities': []
            }
            save_user_preferences_db(user_id, prefs)
        
        # Формируем сообщение с текущими настройками
        settings_text = (
            "⚙️ Ваши текущие настройки:\n\n"
            f"⏰ Время уведомлений: {prefs['notification_time']}\n"
            f"🌡 Диапазон комфортной температуры: {prefs['temp_range']['min']}°C - {prefs['temp_range']['max']}°C\n"
            f"💨 Порог скорости ветра: {prefs['wind_threshold']} м/с\n"
            f"🌧 Уведомления о дожде: {'Включены' if prefs['rain_alerts'] else 'Выключены'}\n"
            f"🎯 Активности: {', '.join(prefs['activities']) if prefs['activities'] else 'Не указаны'}\n\n"
            "Для изменения настроек используйте команды:\n"
            "/notifytime [ЧЧ:ММ] - изменить время уведомлений\n"
            "/temprange [мин] [макс] - изменить диапазон температур\n"
            "/wind [порог] - изменить порог ветра\n"
            "/rainalerts [on/off] - вкл/выкл уведомления о дожде\n"
            "/activities - настроить предпочитаемые активности"
        )
        
        await message.answer(settings_text)
        
    except Exception as e:
        log_error(e, f"Error in preferences command for user {message.from_user.id}")
        await message.answer("😔 Произошла ошибка при получении настроек. Попробуйте позже.")

@dp.message(Command('notifytime'))
@log_execution
@rate_limit(RATE_LIMIT)
async def notifytime_command(message: Message):
    """Обработчик команды /notifytime"""
    try:
        user_id = message.from_user.id
        
        try:
            time_str = message.text.split(' ', 1)[1]
            # Проверяем формат времени
            datetime.strptime(time_str, '%H:%M')
        except (IndexError, ValueError):
            await message.answer(
                "Пожалуйста, укажите время в формате ЧЧ:ММ\n"
                "Например: /notifytime 09:00"
            )
            return
        
        # Получаем текущие настройки
        prefs = get_user_preferences_db(user_id)
        if not prefs:
            prefs = {
                'notification_time': '09:00',
                'temp_range': {'min': 15, 'max': 25},
                'wind_threshold': 10,
                'rain_alerts': True,
                'activities': []
            }
        
        # Обновляем время уведомлений
        prefs['notification_time'] = time_str
        save_user_preferences_db(user_id, prefs)
        
        await message.answer(f"✅ Время ежедневных уведомлений установлено на {time_str}")
        
    except Exception as e:
        log_error(e, f"Error in notifytime command for user {message.from_user.id}")
        await message.answer("😔 Произошла ошибка при изменении времени уведомлений. Попробуйте позже.")

@dp.message(Command('temprange'))
@log_execution
@rate_limit(RATE_LIMIT)
async def temprange_command(message: Message):
    """Обработчик команды /temprange"""
    try:
        user_id = message.from_user.id
        
        try:
            _, min_temp, max_temp = message.text.split()
            min_temp = int(min_temp)
            max_temp = int(max_temp)
            
            if min_temp >= max_temp:
                raise ValueError("Минимальная температура должна быть меньше максимальной")
                
            if min_temp < -50 or max_temp > 50:
                raise ValueError("Температура должна быть в диапазоне от -50°C до +50°C")
                
        except ValueError as e:
            await message.answer(
                "Пожалуйста, укажите минимальную и максимальную температуру через пробел.\n"
                "Например: /temprange 15 25\n\n"
                "Температура должна быть:\n"
                "• В диапазоне от -50°C до +50°C\n"
                "• Минимальная температура меньше максимальной"
            )
            return
        
        # Получаем текущие настройки
        prefs = get_user_preferences_db(user_id)
        if not prefs:
            prefs = {
                'notification_time': '09:00',
                'temp_range': {'min': 15, 'max': 25},
                'wind_threshold': 10,
                'rain_alerts': True,
                'activities': []
            }
        
        # Обновляем диапазон температур
        prefs['temp_range'] = {'min': min_temp, 'max': max_temp}
        save_user_preferences_db(user_id, prefs)
        
        await message.answer(
            f"✅ Установлен новый диапазон комфортной температуры:\n"
            f"От {min_temp}°C до {max_temp}°C"
        )
        
    except Exception as e:
        log_error(e, f"Error in temprange command for user {message.from_user.id}")
        await message.answer("😔 Произошла ошибка при изменении диапазона температур. Попробуйте позже.")

@dp.message(Command('wind'))
@log_execution
@rate_limit(RATE_LIMIT)
async def wind_command(message: Message):
    """Обработчик команды /wind"""
    try:
        user_id = message.from_user.id
        
        try:
            threshold = int(message.text.split(' ', 1)[1])
            if threshold < 0 or threshold > 50:
                raise ValueError("Порог ветра должен быть от 0 до 50 м/с")
        except (IndexError, ValueError):
            await message.answer(
                "Пожалуйста, укажите порог скорости ветра в м/с (от 0 до 50).\n"
                "Например: /wind 10"
            )
            return
        
        # Получаем текущие настройки
        prefs = get_user_preferences_db(user_id)
        if not prefs:
            prefs = {
                'notification_time': '09:00',
                'temp_range': {'min': 15, 'max': 25},
                'wind_threshold': 10,
                'rain_alerts': True,
                'activities': []
            }
        
        # Обновляем порог ветра
        prefs['wind_threshold'] = threshold
        save_user_preferences_db(user_id, prefs)
        
        await message.answer(f"✅ Порог скорости ветра установлен на {threshold} м/с")
        
    except Exception as e:
        log_error(e, f"Error in wind command for user {message.from_user.id}")
        await message.answer("😔 Произошла ошибка при изменении порога ветра. Попробуйте позже.")

@dp.message(Command('rainalerts'))
@log_execution
@rate_limit(RATE_LIMIT)
async def rainalerts_command(message: Message):
    """Обработчик команды /rainalerts"""
    try:
        user_id = message.from_user.id
        
        try:
            state = message.text.split(' ', 1)[1].lower()
            if state not in ['on', 'off']:
                raise ValueError
        except (IndexError, ValueError):
            await message.answer(
                "Пожалуйста, укажите on для включения или off для выключения.\n"
                "Например: /rainalerts on"
            )
            return
        
        # Получаем текущие настройки
        prefs = get_user_preferences_db(user_id)
        if not prefs:
            prefs = {
                'notification_time': '09:00',
                'temp_range': {'min': 15, 'max': 25},
                'wind_threshold': 10,
                'rain_alerts': True,
                'activities': []
            }
        
        # Обновляем настройку уведомлений о дожде
        prefs['rain_alerts'] = (state == 'on')
        save_user_preferences_db(user_id, prefs)
        
        status = "включены" if state == 'on' else "выключены"
        await message.answer(f"✅ Уведомления о дожде {status}")
        
    except Exception as e:
        log_error(e, f"Error in rainalerts command for user {message.from_user.id}")
        await message.answer("😔 Произошла ошибка при изменении настроек уведомлений о дожде. Попробуйте позже.")

@dp.message(Command('activities'))
@log_execution
@rate_limit(RATE_LIMIT)
async def activities_command(message: Message):
    """Обработчик команды /activities"""
    try:
        user_id = message.from_user.id
        
        # Получаем текущие настройки
        prefs = get_user_preferences_db(user_id)
        if not prefs:
            prefs = {
                'notification_time': '09:00',
                'temp_range': {'min': 15, 'max': 25},
                'wind_threshold': 10,
                'rain_alerts': True,
                'activities': []
            }
        
        # Создаем клавиатуру с активностями
        activities = [
            "🏃‍♂️ Бег", "🚶‍♂️ Прогулка", "🚴‍♂️ Велосипед",
            "⛺️ Пикник", "🎣 Рыбалка", "🏊‍♂️ Плавание",
            "🏸 Спорт на улице", "🌳 Садоводство", "🎨 Пленэр"
        ]
        
        keyboard = InlineKeyboardMarkup(row_width=3)
        buttons = []
        for activity in activities:
            is_selected = activity in prefs['activities']
            callback_data = f"activity_{activity}"
            buttons.append(
                InlineKeyboardButton(
                    text=f"{'✅' if is_selected else '❌'} {activity}",
                    callback_data=callback_data
                )
            )
        keyboard.add(*buttons)
        
        await message.answer(
            "Выберите предпочитаемые активности:\n"
            "✅ - активность выбрана\n"
            "❌ - активность не выбрана\n\n"
            "Бот будет учитывать погодные условия для этих активностей "
            "при отправке уведомлений.",
            reply_markup=keyboard
        )
        
    except Exception as e:
        log_error(e, f"Error in activities command for user {message.from_user.id}")
        await message.answer("😔 Произошла ошибка при настройке активностей. Попробуйте позже.")

@dp.callback_query(lambda c: c.data.startswith('activity_'))
@log_execution
async def process_activity_callback(callback_query: types.CallbackQuery):
    """Обработчик нажатий на кнопки активностей"""
    try:
        user_id = callback_query.from_user.id
        activity = callback_query.data.replace('activity_', '')
        
        # Получаем текущие настройки
        prefs = get_user_preferences_db(user_id)
        if not prefs:
            prefs = {
                'notification_time': '09:00',
                'temp_range': {'min': 15, 'max': 25},
                'wind_threshold': 10,
                'rain_alerts': True,
                'activities': []
            }
        
        # Обновляем список активностей
        if activity in prefs['activities']:
            prefs['activities'].remove(activity)
        else:
            prefs['activities'].append(activity)
        
        # Сохраняем обновленные настройки
        save_user_preferences_db(user_id, prefs)
        
        # Обновляем клавиатуру
        activities = [
            "🏃‍♂️ Бег", "🚶‍♂️ Прогулка", "🚴‍♂️ Велосипед",
            "⛺️ Пикник", "🎣 Рыбалка", "🏊‍♂️ Плавание",
            "🏸 Спорт на улице", "🌳 Садоводство", "🎨 Пленэр"
        ]
        
        keyboard = InlineKeyboardMarkup(row_width=3)
        buttons = []
        for act in activities:
            is_selected = act in prefs['activities']
            callback_data = f"activity_{act}"
            buttons.append(
                InlineKeyboardButton(
                    text=f"{'✅' if is_selected else '❌'} {act}",
                    callback_data=callback_data
                )
            )
        keyboard.add(*buttons)
        
        # Обновляем сообщение с новой клавиатурой
        await callback_query.message.edit_reply_markup(reply_markup=keyboard)
        await callback_query.answer()
        
    except Exception as e:
        log_error(e, f"Error in activity callback for user {callback_query.from_user.id}")
        await callback_query.answer("Произошла ошибка. Попробуйте позже.", show_alert=True)

async def main():
    """Start the bot."""
    global scheduler, app, runner
    
    try:
        # Настраиваем логирование
        logger.info("Starting bot initialization...")
        
        # Загружаем сохраненные данные
        load_subscriptions()
        load_user_preferences()
        logger.info("Loaded saved data")
        
        # Инициализируем планировщик
        scheduler = AsyncIOScheduler()
        scheduler.add_job(send_weather_alerts, 'interval', minutes=30)
        scheduler.add_job(send_smart_notifications, 'interval', minutes=60)
        scheduler.start()
        logger.info("Scheduler started")
        
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
        logger.info(f"Bot started on port {port}")
        
        # Ждем завершения
        await asyncio.Event().wait()
        
    except Exception as e:
        log_error(e, "Critical error in main")
        if runner:
            await runner.cleanup()
        if scheduler:
            scheduler.shutdown()
        sys.exit(1)

# Обработчики веб-хуков
@log_execution
async def process_update(request):
    """Обработка входящих обновлений от Telegram"""
    try:
        update = types.Update(**(await request.json()))
        await dp.feed_update(bot=bot, update=update)
        return web.Response()
    except Exception as e:
        log_error(e, "Error processing update")
        return web.Response(status=500)

async def healthcheck(request):
    """Простой обработчик для проверки работоспособности"""
    return web.Response(text="Bot is running")

@log_execution
async def shutdown(dispatcher: Dispatcher):
    """Корректное завершение работы бота"""
    logger.info("Shutting down...")
    
    try:
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
            
        logger.info("Shutdown completed successfully")
    except Exception as e:
        log_error(e, "Error during shutdown")

@log_execution
async def on_shutdown(app):
    """Действия при завершении работы веб-приложения"""
    logger.info("Stopping web application...")
    await shutdown(dp)

@log_execution
async def on_startup(app):
    """Действия при запуске"""
    try:
        webhook_path = f"/webhook/{TELEGRAM_BOT_TOKEN}"
        webhook_url = os.environ.get('RENDER_EXTERNAL_URL')
        if not webhook_url:
            raise ValueError("RENDER_EXTERNAL_URL environment variable is not set")
        webhook_url = webhook_url + webhook_path
        
        # Устанавливаем вебхук
        await bot.set_webhook(
            url=webhook_url,
            drop_pending_updates=True
        )
        
        # Устанавливаем команды бота
        await bot.set_my_commands(COMMANDS)
        logger.info(f"Webhook set to {webhook_url}")
        logger.info("Bot commands updated successfully")
    except Exception as e:
        log_error(e, "Error during startup")
        raise

# Функция для отправки уведомлений о погоде
@log_execution
async def send_weather_alerts():
    """Отправка уведомлений о погодных предупреждениях"""
    try:
        for user_id, cities in weather_subscriptions.items():
            for city, lat, lon in cities:
                # Получаем данные о погоде
                weather_data = await get_weather_data(lat, lon)
                if not weather_data:
                    continue
                
                # Проверяем наличие предупреждений
                alerts = check_weather_alerts(weather_data)
                if alerts:
                    await bot.send_message(
                        user_id,
                        f"⚠️ Погодные предупреждения для {city}:\n\n" + "\n".join(alerts)
                    )
                    logger.info(f"Sent weather alert to user {user_id} for {city}")
    except Exception as e:
        log_error(e, "Error in send_weather_alerts")

async def send_smart_notifications():
    """Отправка умных уведомлений о погоде"""
    try:
        for user_id, cities in weather_subscriptions.items():
            # Получаем настройки пользователя
            prefs = get_user_preferences_db(user_id)
            if not prefs:
                continue
            
            for city, lat, lon in cities:
                # Получаем данные о погоде
                weather_data = await get_weather_data(lat, lon)
                if not weather_data:
                    continue
                
                # Проверяем условия для уведомлений
                notifications = check_smart_notifications(weather_data, prefs)
                if notifications:
                    await bot.send_message(
                        user_id,
                        f"🎯 Умные уведомления для {city}:\n\n" + "\n".join(notifications)
                    )
                    logger.info(f"Sent smart notification to user {user_id} for {city}")
    except Exception as e:
        log_error(e, "Error in send_smart_notifications")

def check_weather_alerts(weather_data):
    """Проверяет наличие погодных предупреждений"""
    alerts = []
    
    # Проверяем экстремальные температуры
    temp = weather_data.get('main', {}).get('temp')
    if temp is not None:
        if temp > 35:
            alerts.append("🌡 Экстремально высокая температура!")
        elif temp < -25:
            alerts.append("❄️ Экстремально низкая температура!")
    
    # Проверяем сильный ветер
    wind_speed = weather_data.get('wind', {}).get('speed')
    if wind_speed and wind_speed > 15:
        alerts.append("💨 Штормовое предупреждение: сильный ветер!")
    
    # Проверяем осадки
    if 'rain' in weather_data:
        rain = weather_data['rain'].get('1h', 0)
        if rain > 10:
            alerts.append("🌧 Сильный дождь!")
    
    if 'snow' in weather_data:
        snow = weather_data['snow'].get('1h', 0)
        if snow > 5:
            alerts.append("🌨 Сильный снегопад!")
    
    return alerts

def check_smart_notifications(weather_data, preferences):
    """Проверяет условия для умных уведомлений"""
    notifications = []
    
    temp = weather_data.get('main', {}).get('temp')
    wind_speed = weather_data.get('wind', {}).get('speed')
    
    # Проверяем температурный диапазон
    temp_range = preferences.get('temp_range', {'min': 15, 'max': 25})
    if temp is not None:
        if temp < temp_range['min']:
            notifications.append("🌡 Температура ниже комфортной")
        elif temp > temp_range['max']:
            notifications.append("🌡 Температура выше комфортной")
    
    # Проверяем ветер
    wind_threshold = preferences.get('wind_threshold', 10)
    if wind_speed and wind_speed > wind_threshold:
        notifications.append("💨 Ветер сильнее предпочитаемого")
    
    # Проверяем дождь
    if preferences.get('rain_alerts', True) and 'rain' in weather_data:
        notifications.append("☔️ Ожидается дождь")
    
    return notifications

async def get_weather_data(lat, lon):
    """Получает данные о погоде по координатам"""
    try:
        url = f"http://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=ru"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=API_TIMEOUT) as response:
                data = await check_api_response(response, "get_weather_data")
                return data
    except Exception as e:
        log_error(e, f"Error getting weather data for coordinates {lat}, {lon}")
        return None

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

# Функция для проверки ответа API
async def check_api_response(response, function_name):
    """Проверяет ответ API на ошибки"""
    if response.status != 200:
        error_msg = f"API error in {function_name}: {response.status}"
        logger.error(error_msg)
        raise Exception(error_msg)
    return await response.json()

# Функция для проверки погодных условий
@log_execution
async def check_weather_conditions(city, lat, lon):
    """Проверяет погодные условия и возвращает предупреждения"""
    try:
        logger.info(f"Checking weather conditions for {city}")
        warnings = []
        
        # Получаем текущую погоду
        weather_url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=ru"
        async with aiohttp.ClientSession() as session:
            async with session.get(weather_url) as response:
                weather_data = await check_api_response(response, "check_weather_conditions")
        
        # Проверяем температуру
        temp = weather_data['main']['temp']
        if temp > 30:
            warnings.append(f"🌡 Высокая температура: {temp:.1f}°C")
        elif temp < 0:
            warnings.append(f"❄️ Низкая температура: {temp:.1f}°C")
        
        # Проверяем ветер
        wind_speed = weather_data['wind']['speed']
        if wind_speed > 10:
            warnings.append(f"💨 Сильный ветер: {wind_speed} м/с")
        
        # Проверяем дождь
        if 'rain' in weather_data:
            rain = weather_data['rain'].get('1h', 0)
            if rain > 0:
                warnings.append(f"🌧 Ожидается дождь: {rain} мм/ч")
        
        # Проверяем снег
        if 'snow' in weather_data:
            snow = weather_data['snow'].get('1h', 0)
            if snow > 0:
                warnings.append(f"🌨 Ожидается снег: {snow} мм/ч")
        
        return warnings
        
    except Exception as e:
        log_error(e, f"Error checking weather conditions for {city}")
        return []

@dp.message(Command('weather'))
@log_execution
async def weather_command(message: Message):
    """Обработчик команды /weather"""
    try:
        # Проверяем, передано ли название города
        try:
            city = message.text.split(' ', 1)[1]
        except IndexError:
            await message.answer(
                "Пожалуйста, укажите город после команды.\n"
                "Например: /weather Москва"
            )
            return
        
        # Получаем координаты города
        result = await get_city_coordinates(city)
        if not result:
            await message.answer(
                "Извините, не могу найти такой город. Попробуйте:\n"
                "1. Проверить правильность написания\n"
                "2. Использовать название на русском или английском\n"
                "3. Указать более точное название"
            )
            return
            
        lat, lon, city_name = result
        
        # Получаем текущую погоду
        weather_url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=ru"
        async with aiohttp.ClientSession() as session:
            async with session.get(weather_url) as response:
                weather_data = await check_api_response(response, "weather_command")
        
        # Формируем ответ
        weather_description = weather_data['weather'][0]['description'].capitalize()
        temp = weather_data['main']['temp']
        feels_like = weather_data['main']['feels_like']
        humidity = weather_data['main']['humidity']
        wind_speed = weather_data['wind']['speed']
        
        message_text = (
            f"🌡 Погода в {city_name}:\n\n"
            f"☁️ {weather_description}\n"
            f"🌡 Температура: {temp:.1f}°C\n"
            f"🤔 Ощущается как: {feels_like:.1f}°C\n"
            f"💧 Влажность: {humidity}%\n"
            f"💨 Ветер: {wind_speed} м/с\n"
        )
        
        # Добавляем информацию об осадках
        if 'rain' in weather_data:
            rain = weather_data['rain'].get('1h', 0)
            message_text += f"🌧 Дождь: {rain} мм/ч\n"
        if 'snow' in weather_data:
            snow = weather_data['snow'].get('1h', 0)
            message_text += f"🌨 Снег: {snow} мм/ч\n"
        
        await message.answer(message_text)
        logger.info(f"Weather info sent for {city_name}")
        
    except Exception as e:
        log_error(e, f"Error in weather command for user {message.from_user.id}")
        await message.answer("😔 Произошла ошибка при получении погоды. Попробуйте позже.")

@dp.message(Command('subscribe'))
@log_execution
async def subscribe_command(message: Message):
    """Обработчик команды /subscribe"""
    try:
        user_id = message.from_user.id
        
        # Проверяем, передано ли название города
        try:
            city = message.text.split(' ', 1)[1]
        except IndexError:
            await message.answer(
                "Пожалуйста, укажите город после команды.\n"
                "Например: /subscribe Москва"
            )
            return
        
        # Получаем координаты города
        result = await get_city_coordinates(city)
        if not result:
            await message.answer(
                "Извините, не могу найти такой город. Попробуйте:\n"
                "1. Проверить правильность написания\n"
                "2. Использовать название на русском или английском\n"
                "3. Указать более точное название"
            )
            return
            
        lat, lon, city_name = result
        
        # Проверяем, не подписан ли уже пользователь на этот город
        if any(city_data[0].lower() == city_name.lower() for city_data in weather_subscriptions[user_id]):
            await message.answer(f"Вы уже подписаны на погоду в городе {city_name}")
            return
        
        # Добавляем подписку
        weather_subscriptions[user_id].append([city_name, lat, lon])
        save_subscriptions()
        
        # Создаем настройки по умолчанию, если их нет
        if user_id not in user_preferences:
            user_preferences[user_id] = {
                'notification_time': '09:00',
                'temp_range': {'min': 15, 'max': 25},
                'wind_threshold': 10,
                'rain_alerts': True,
                'activities': []
            }
            save_user_preferences()
        
        await message.answer(
            f"✅ Вы успешно подписались на погоду в городе {city_name}\n\n"
            "Теперь вы будете получать:\n"
            "🌡 Уведомления об опасных погодных условиях\n"
            "🎯 Рекомендации для активностей (если настроены)\n"
            "📅 Ежедневный прогноз погоды\n\n"
            "Используйте /preferences для настройки уведомлений"
        )
        logger.info(f"User {user_id} subscribed to {city_name}")
        
    except Exception as e:
        log_error(e, f"Error in subscribe command for user {message.from_user.id}")
        await message.answer("😔 Произошла ошибка при подписке. Попробуйте позже.")

@dp.message(Command('unsubscribe'))
@log_execution
async def unsubscribe_command(message: Message):
    """Обработчик команды /unsubscribe"""
    try:
        user_id = message.from_user.id
        
        # Проверяем, передано ли название города
        try:
            city = message.text.split(' ', 1)[1]
        except IndexError:
            await message.answer(
                "Пожалуйста, укажите город после команды.\n"
                "Например: /unsubscribe Москва"
            )
            return
        
        # Проверяем, есть ли подписки у пользователя
        if user_id not in weather_subscriptions or not weather_subscriptions[user_id]:
            await message.answer("У вас нет активных подписок на погоду")
            return
        
        # Ищем город в подписках
        city_lower = city.lower()
        for i, (subscribed_city, _, _) in enumerate(weather_subscriptions[user_id]):
            if subscribed_city.lower() == city_lower:
                weather_subscriptions[user_id].pop(i)
                save_subscriptions()
                await message.answer(f"✅ Вы успешно отписались от погоды в городе {subscribed_city}")
                logger.info(f"User {user_id} unsubscribed from {subscribed_city}")
                return
        
        await message.answer(f"Вы не подписаны на погоду в городе {city}")
        
    except Exception as e:
        log_error(e, f"Error in unsubscribe command for user {message.from_user.id}")
        await message.answer("😔 Произошла ошибка при отписке. Попробуйте позже.")

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
@log_execution
async def shelter_command(message: Message):
    """Поиск укрытия от непогоды"""
    try:
        user_id = message.from_user.id
        logger.info(f"Processing shelter command for user {user_id}")
        
        # Проверяем, передано ли название города
        try:
            city = message.text.split(' ', 1)[1]
            logger.info(f"Searching shelters for city: {city}")
            
            # Получаем координаты города
            result = await get_city_coordinates(city)
            if not result:
                await message.answer(
                    "Извините, не могу найти такой город. Попробуйте:\n"
                    "1. Проверить правильность написания\n"
                    "2. Использовать название на русском или английском\n"
                    "3. Указать более крупный город поблизости\n\n"
                    "Или отправьте свою геолокацию, нажав на кнопку ниже 📍",
                    reply_markup=ReplyKeyboardMarkup(
                        keyboard=[[KeyboardButton(text="📍 Отправить геолокацию", request_location=True)]],
                        resize_keyboard=True,
                        one_time_keyboard=True
                    )
                )
                return
                
            lat, lon, normalized_city = result
            logger.info(f"Found coordinates for {normalized_city}: {lat}, {lon}")
            
        except IndexError:
            # Если город не указан, запрашиваем геолокацию
            keyboard = ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="📍 Отправить геолокацию", request_location=True)]],
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
        logger.info(f"Found {len(shelters)} shelters near {normalized_city}")
        
        if not shelters:
            await message.answer(
                "Извините, не удалось найти укрытия поблизости. "
                "Попробуйте искать торговые центры или кафе в этом районе."
            )
            return
        
        # Формируем сообщение с укрытиями
        message_text = f"🏪 Ближайшие места, где можно укрыться от непогоды в районе {normalized_city}:\n\n"
        
        # Создаем список кнопок для каждого укрытия
        keyboard_buttons = []
        
        for place in shelters:
            distance = ((float(place['lat']) - lat) ** 2 + (float(place['lon']) - lon) ** 2) ** 0.5 * 111  # примерное расстояние в км
            name = place['display_name'].split(',')[0]
            address = ', '.join(place['display_name'].split(',')[1:]).strip()
            
            message_text += (
                f"📍 {name}\n"
                f"   📏 Расстояние: {distance:.1f} км\n"
                f"   🏠 Адрес: {address}\n\n"
            )
            
            # Добавляем кнопку для этого места
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text=f"🗺 {name} на карте",
                    url=f"https://www.openstreetmap.org/?mlat={place['lat']}&mlon={place['lon']}&zoom=17"
                )
            ])
        
        # Создаем клавиатуру с кнопками для всех укрытий
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        # Отправляем сообщение с информацией и кнопками
        await message.answer(message_text, reply_markup=keyboard)
        logger.info(f"Successfully sent shelter information to user {user_id}")
        
    except Exception as e:
        log_error(e, f"Error in shelter_command for user {message.from_user.id}")
        await message.answer(
            "Извините, произошла ошибка при поиске укрытий. "
            "Пожалуйста, попробуйте позже."
        )

@dp.message(Command('list'))
@log_execution
async def list_command(message: Message):
    """Обработчик команды /list"""
    try:
        user_id = message.from_user.id
        
        # Проверяем, есть ли подписки у пользователя
        if user_id not in weather_subscriptions or not weather_subscriptions[user_id]:
            await message.answer("У вас нет активных подписок на погоду")
            return
        
        # Формируем список подписок
        subscriptions = [f"🌍 {city}" for city, _, _ in weather_subscriptions[user_id]]
        
        await message.answer(
            "Ваши подписки на погоду:\n\n" +
            "\n".join(subscriptions) +
            "\n\nИспользуйте /unsubscribe [город] для отписки"
        )
        logger.info(f"Subscriptions list sent to user {user_id}")
        
    except Exception as e:
        log_error(e, f"Error in list command for user {message.from_user.id}")
        await message.answer("😔 Произошла ошибка при получении списка подписок. Попробуйте позже.")

@dp.message(Command('preferences'))
@log_execution
async def preferences_command(message: Message):
    """Обработчик команды /preferences"""
    try:
        user_id = message.from_user.id
        
        # Проверяем, есть ли настройки у пользователя
        if user_id not in user_preferences:
            user_preferences[user_id] = {
                'notification_time': '09:00',
                'temp_range': {'min': 15, 'max': 25},
                'wind_threshold': 10,
                'rain_alerts': True,
                'activities': []
            }
            save_user_preferences()
        
        prefs = user_preferences[user_id]
        
        # Формируем клавиатуру
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏰ Время уведомлений", callback_data="pref_time")],
            [InlineKeyboardButton(text="🌡 Диапазон температур", callback_data="pref_temp")],
            [InlineKeyboardButton(text="💨 Порог ветра", callback_data="pref_wind")],
            [InlineKeyboardButton(text="🌧 Уведомления о дожде", callback_data="pref_rain")],
            [InlineKeyboardButton(text="🎯 Настройка активностей", callback_data="pref_activities")]
        ])
        
        await message.answer(
            "⚙️ Текущие настройки:\n\n"
            f"⏰ Время уведомлений: {prefs['notification_time']}\n"
            f"🌡 Комфортная температура: {prefs['temp_range']['min']}°C - {prefs['temp_range']['max']}°C\n"
            f"💨 Уведомлять о ветре от: {prefs['wind_threshold']} м/с\n"
            f"🌧 Уведомления о дожде: {'Включены' if prefs['rain_alerts'] else 'Выключены'}\n"
            f"🎯 Активности: {', '.join(prefs['activities']) if prefs['activities'] else 'Не настроены'}\n\n"
            "Выберите настройку для изменения:",
            reply_markup=keyboard
        )
        logger.info(f"Preferences menu sent to user {user_id}")
        
    except Exception as e:
        log_error(e, f"Error in preferences command for user {message.from_user.id}")
        await message.answer("😔 Произошла ошибка при получении настроек. Попробуйте позже.")

@dp.message(Command('activities'))
@log_execution
async def activities_command(message: Message):
    """Обработчик команды /activities"""
    try:
        user_id = message.from_user.id
        
        # Проверяем, есть ли настройки у пользователя
        if user_id not in user_preferences:
            user_preferences[user_id] = {
                'notification_time': '09:00',
                'temp_range': {'min': 15, 'max': 25},
                'wind_threshold': 10,
                'rain_alerts': True,
                'activities': []
            }
            save_user_preferences()
        
        # Формируем клавиатуру с доступными активностями
        keyboard = []
        row = []
        
        for activity, info in ACTIVITIES.items():
            is_selected = activity in user_preferences[user_id]['activities']
            button = InlineKeyboardButton(
                text=f"{'✅' if is_selected else '❌'} {info['description']}",
                callback_data=f"activity_{activity}"
            )
            row.append(button)
            
            if len(row) == 2:
                keyboard.append(row)
                row = []
        
        if row:  # Добавляем оставшиеся кнопки
            keyboard.append(row)
        
        keyboard.append([InlineKeyboardButton(text="✅ Готово", callback_data="activities_done")])
        
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await message.answer(
            "🎯 Выберите интересующие вас активности:\n\n"
            "Вы будете получать уведомления, когда погода благоприятна для выбранных занятий.\n"
            "Нажмите на активность, чтобы включить/выключить её.",
            reply_markup=markup
        )
        logger.info(f"Activities menu sent to user {user_id}")
        
    except Exception as e:
        log_error(e, f"Error in activities command for user {message.from_user.id}")
        await message.answer("😔 Произошла ошибка при настройке активностей. Попробуйте позже.")

@dp.message(Command('shelter'))
@log_execution
async def shelter_command(message: Message):
    """Обработчик команды /shelter"""
    try:
        # Проверяем, передано ли название города
        try:
            city = message.text.split(' ', 1)[1]
        except IndexError:
            await message.answer(
                "Пожалуйста, укажите город после команды.\n"
                "Например: /shelter Москва"
            )
            return
        
        # Получаем координаты города
        result = await get_city_coordinates(city)
        if not result:
            await message.answer(
                "Извините, не могу найти такой город. Попробуйте:\n"
                "1. Проверить правильность написания\n"
                "2. Использовать название на русском или английском\n"
                "3. Указать более точное название"
            )
            return
            
        lat, lon, city_name = result
        
        # Ищем ближайшие укрытия
        places_url = f"https://api.openweathermap.org/data/2.5/find?lat={lat}&lon={lon}&cnt=5&appid={OPENWEATHER_API_KEY}"
        async with aiohttp.ClientSession() as session:
            async with session.get(places_url) as response:
                places_data = await check_api_response(response, "shelter_command")
        
        # Формируем список укрытий
        shelters = []
        for place in places_data['list']:
            if place['name'] != city_name:  # Исключаем текущий город
                distance = calculate_distance(lat, lon, place['coord']['lat'], place['coord']['lon'])
                if distance <= 50:  # Только места в радиусе 50 км
                    weather = place['weather'][0]['description']
                    temp = place['main']['temp'] - 273.15  # Конвертируем из Кельвинов в Цельсии
                    shelters.append({
                        'name': place['name'],
                        'distance': distance,
                        'weather': weather,
                        'temp': temp
                    })
        
        if not shelters:
            await message.answer(
                f"🏘 К сожалению, не удалось найти подходящих укрытий рядом с {city_name}.\n"
                "Попробуйте поискать укрытие в другом городе."
            )
            return
        
        # Сортируем укрытия по расстоянию
        shelters.sort(key=lambda x: x['distance'])
        
        # Формируем ответ
        message_text = f"🏘 Ближайшие укрытия от непогоды рядом с {city_name}:\n\n"
        for shelter in shelters:
            message_text += (
                f"🏡 {shelter['name']}\n"
                f"📍 Расстояние: {shelter['distance']:.1f} км\n"
                f"☁️ Погода: {shelter['weather']}\n"
                f"🌡 Температура: {shelter['temp']:.1f}°C\n\n"
            )
        
        await message.answer(message_text)
        logger.info(f"Shelter info sent for {city_name}")
        
    except Exception as e:
        log_error(e, f"Error in shelter command for user {message.from_user.id}")
        await message.answer("😔 Произошла ошибка при поиске укрытий. Попробуйте позже.")

# Database configuration
DB_FILE = 'weather_bot.db'

def init_db():
    """Инициализация базы данных"""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Создаем таблицу пользователей
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                language_code TEXT,
                is_premium BOOLEAN,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Создаем таблицу подписок
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id INTEGER,
                city TEXT,
                lat REAL,
                lon REAL,
                PRIMARY KEY (user_id, city)
            )
        ''')
        
        # Создаем таблицу настроек пользователей
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id INTEGER PRIMARY KEY,
                notification_time TEXT,
                temp_min INTEGER,
                temp_max INTEGER,
                wind_threshold INTEGER,
                rain_alerts BOOLEAN,
                activities TEXT
            )
        ''')
        
        # Создаем таблицу статистики погоды
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS weather_stats (
                city TEXT,
                timestamp INTEGER,
                temperature REAL,
                humidity INTEGER,
                wind_speed REAL,
                PRIMARY KEY (city, timestamp)
            )
        ''')
        
        conn.commit()
        logger.info("Database initialized successfully")

def save_subscription(user_id: int, city: str, lat: float, lon: float):
    """Сохраняет подписку в базу данных"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'INSERT OR REPLACE INTO subscriptions (user_id, city, lat, lon) VALUES (?, ?, ?, ?)',
            (user_id, city, lat, lon)
        )

def get_user_subscriptions(user_id: int) -> list:
    """Получает список подписок пользователя"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT city, lat, lon FROM subscriptions WHERE user_id = ?', (user_id,))
        return cursor.fetchall()

def save_user_preferences_db(user_id: int, preferences: dict):
    """Сохраняет настройки пользователя в базу данных"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''INSERT OR REPLACE INTO user_preferences 
               (user_id, notification_time, temp_min, temp_max, wind_threshold, rain_alerts, activities)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (
                user_id,
                preferences['notification_time'],
                preferences['temp_range']['min'],
                preferences['temp_range']['max'],
                preferences['wind_threshold'],
                preferences['rain_alerts'],
                json.dumps(preferences['activities'])
            )
        )

def get_user_preferences_db(user_id: int) -> dict:
    """Получает настройки пользователя из базы данных"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM user_preferences WHERE user_id = ?', (user_id,))
        row = cursor.fetchone()
        
        if row:
            return {
                'notification_time': row[1],
                'temp_range': {'min': row[2], 'max': row[3]},
                'wind_threshold': row[4],
                'rain_alerts': bool(row[5]),
                'activities': json.loads(row[6])
            }
        return None

def save_weather_stats(city: str, temp: float, humidity: int, wind_speed: float):
    """Сохраняет статистику погоды"""
    with get_db() as conn:
        cursor = conn.cursor()
        timestamp = int(time_module.time())
        cursor.execute(
            'INSERT INTO weather_stats (city, timestamp, temperature, humidity, wind_speed) VALUES (?, ?, ?, ?, ?)',
            (city, timestamp, temp, humidity, wind_speed)
        )

def get_weather_stats(city: str, hours: int = 24) -> dict:
    """Получает статистику погоды за указанный период"""
    with get_db() as conn:
        cursor = conn.cursor()
        timestamp = int(time_module.time() - hours * 3600)
        cursor.execute(
            '''SELECT AVG(temperature), MIN(temperature), MAX(temperature), AVG(humidity), AVG(wind_speed)
               FROM weather_stats 
               WHERE city = ? AND timestamp > ?''',
            (city, timestamp)
        )
        row = cursor.fetchone()
        
        if row and row[0] is not None:
            return {
                'temp_avg': row[0],
                'temp_min': row[1],
                'temp_max': row[2],
                'humidity_avg': row[3],
                'wind_speed_avg': row[4]
            }
        return None

# Инициализируем базу данных при запуске
init_db()