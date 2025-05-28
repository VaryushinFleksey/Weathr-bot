import os
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, BotCommand, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
import requests
from dotenv import load_dotenv
import asyncio
from datetime import datetime
import pytz
import signal
import sys
from aiohttp import web  # Добавляем импорт aiohttp
import aiohttp
import math  # Добавляем импорт math

# Опционально импортируем googlemaps
try:
    import googlemaps
    GOOGLE_MAPS_AVAILABLE = True
except ImportError:
    GOOGLE_MAPS_AVAILABLE = False

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
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')

# Initialize bot and dispatcher
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# Initialize Google Maps client only if API key is available
gmaps = None
if GOOGLE_MAPS_AVAILABLE and GOOGLE_API_KEY:
    try:
        gmaps = googlemaps.Client(key=GOOGLE_API_KEY)
        logging.info("Google Maps API initialized successfully")
    except Exception as e:
        logging.warning(f"Failed to initialize Google Maps API: {e}")

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
    BotCommand(command='rain', description='Карта осадков')
]

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
        
        # Проверяем доступность тайла и его содержимое
        async with aiohttp.ClientSession() as session:
            async with session.get(map_url) as response:
                if response.status == 200:
                    # Проверяем размер ответа
                    content = await response.read()
                    if len(content) < 100:  # Если картинка слишком маленькая, вероятно это пустой тайл
                        logging.warning(f"Tile response too small: {len(content)} bytes")
                        # Попробуем уменьшить зум для получения более общей картины
                        if zoom > 4:
                            return await get_precipitation_map(lat, lon, zoom - 2)
                        return None
                    return map_url
                else:
                    logging.error(f"Failed to fetch tile: {response.status}, {await response.text()}")
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
        '9. Получить карту осадков (/rain город)\n\n'
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
        '9. Нажмите кнопку "📍 Отправить геолокацию" для погоды в вашем месте\n\n'
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
        # Get coordinates first
        geo_url = f"http://api.openweathermap.org/geo/1.0/direct?q={city}&limit=1&appid={OPENWEATHER_API_KEY}"
        geo_response = requests.get(geo_url)
        geo_data = geo_response.json()
        
        if not geo_data:
            await message.answer("Извините, не могу найти такой город. Попробуйте другой.")
            return
            
        lat = geo_data[0]['lat']
        lon = geo_data[0]['lon']
        
        # Get detailed weather data
        weather_url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=ru"
        weather_response = requests.get(weather_url)
        weather_data = weather_response.json()
        
        # Check for weather alerts
        alerts = check_weather_alerts(weather_data)
        
        # Format and send detailed weather information
        detailed_message = format_detailed_weather(weather_data, city)
        if alerts:
            detailed_message += "\n\n" + "\n".join(alerts)
        await message.answer(detailed_message)
        
    except Exception as e:
        logging.error(f"Error getting detailed weather: {e}")
        await message.answer(
            "Извините, произошла ошибка при получении информации о погоде. "
            "Пожалуйста, попробуйте позже."
        )

@dp.message(Command('air'))
async def air_quality_command(message: Message):
    """Get air quality information for the specified city."""
    try:
        city = message.text.split(' ', 1)[1]
    except IndexError:
        await message.answer(
            "Пожалуйста, укажите город после команды.\n"
            "Например: /air Москва"
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
        
        # Get air quality data
        air_url = f"http://api.openweathermap.org/data/2.5/air_pollution?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}"
        air_response = requests.get(air_url)
        air_data = air_response.json()
        
        # AQI levels description
        aqi_levels = {
            1: "Отличное ",
            2: "Хорошее 🙂",
            3: "Умеренное 😐",
            4: "Плохое 😷",
            5: "Очень плохое 🤢"
        }
        
        aqi = air_data['list'][0]['main']['aqi']
        components = air_data['list'][0]['components']
        
        air_message = (
            f"🌬 Качество воздуха в городе {city}:\n\n"
            f"Общий индекс: {aqi_levels[aqi]}\n\n"
            f"Компоненты:\n"
            f"CO (Угарный газ): {components['co']:.1f} мкг/м³\n"
            f"NO (Оксид азота): {components['no']:.1f} мкг/м³\n"
            f"NO₂ (Диоксид азота): {components['no2']:.1f} мкг/м³\n"
            f"O₃ (Озон): {components['o3']:.1f} мкг/м³\n"
            f"SO₂ (Диоксид серы): {components['so2']:.1f} мкг/м³\n"
            f"PM2.5 (Мелкие частицы): {components['pm2_5']:.1f} мкг/м³\n"
            f"PM10 (Крупные частицы): {components['pm10']:.1f} мкг/м³"
        )
        
        await message.answer(air_message)
        
    except Exception as e:
        logging.error(f"Error getting air quality: {e}")
        await message.answer(
            "Извините, произошла ошибка при получении данных о качестве воздуха. "
            "Пожалуйста, попробуйте позже."
        )

@dp.message(lambda message: message.location is not None)
async def handle_location(message: Message):
    """Handle received location."""
    try:
        lat = message.location.latitude
        lon = message.location.longitude
        
        # Get weather data
        weather_url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=ru"
        weather_response = requests.get(weather_url)
        weather_data = weather_response.json()
        
        # Get city name from coordinates
        city_name = weather_data['name']
        
        # Check for weather alerts
        alerts = check_weather_alerts(weather_data)
        
        # Format and send detailed weather information
        detailed_message = format_detailed_weather(weather_data, city_name)
        if alerts:
            detailed_message += "\n\n" + "\n".join(alerts)
        await message.answer(detailed_message)
        
    except Exception as e:
        logging.error(f"Error handling location: {e}")
        await message.answer(
            "Извините, произошла ошибка при определении погоды по вашей геолокации. "
            "Пожалуйста, попробуйте позже."
        )

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
    """Отправляет карту осадков для указанного города"""
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
        geo_response = requests.get(geo_url)
        geo_data = geo_response.json()
        
        if not geo_data:
            await message.answer("Извините, не могу найти такой город. Попробуйте другой.")
            return
            
        lat = geo_data[0]['lat']
        lon = geo_data[0]['lon']
        
        # Сначала проверим наличие осадков в регионе
        weather_url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=ru"
        async with aiohttp.ClientSession() as session:
            async with session.get(weather_url) as response:
                weather_data = await response.json()
        
        # Получаем URL карты осадков
        map_url = await get_precipitation_map(lat, lon)
        
        if map_url:
            try:
                # Пробуем отправить фото
                await message.answer_photo(
                    map_url,
                    caption=f"🗺 Карта осадков для города {city}\n"
                            f"🔵 Синий цвет - дождь\n"
                            f"🟣 Фиолетовый цвет - смешанные осадки\n"
                            f"⚪️ Белый цвет - снег"
                )
            except Exception as photo_error:
                logging.error(f"Error sending photo: {photo_error}")
                # Если не получилось отправить фото, отправим хотя бы текущие данные об осадках
                if 'rain' in weather_data or 'snow' in weather_data:
                    precipitation_info = "Текущие осадки:\n"
                    if 'rain' in weather_data:
                        precipitation_info += f"🌧 Дождь: {weather_data['rain'].get('1h', 0)} мм/ч\n"
                    if 'snow' in weather_data:
                        precipitation_info += f"🌨 Снег: {weather_data['snow'].get('1h', 0)} мм/ч\n"
                    await message.answer(precipitation_info)
                else:
                    await message.answer("В данный момент осадков нет.")
        else:
            # Если карта недоступна, отправим информацию о текущей погоде
            weather_info = f"Текущая погода в {city}:\n"
            weather_info += f"☁️ {weather_data['weather'][0]['description']}\n"
            if 'rain' in weather_data:
                weather_info += f"🌧 Дождь: {weather_data['rain'].get('1h', 0)} мм/ч\n"
            if 'snow' in weather_data:
                weather_info += f"🌨 Снег: {weather_data['snow'].get('1h', 0)} мм/ч\n"
            if 'rain' not in weather_data and 'snow' not in weather_data:
                weather_info += "Осадков нет"
            await message.answer(weather_info)
            
    except Exception as e:
        logging.error(f"Error in rain_map_command: {e}")
        await message.answer(
            "Извините, произошла ошибка при получении карты осадков. "
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

@dp.message()
async def get_weather(message: Message):
    """Get current weather for the specified city."""
    if message.text == "ℹ️ Помощь":
        await help_command(message)
        return

    city = message.text
    
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
        
        # Format and send detailed weather information
        detailed_message = format_detailed_weather(weather_data, city)
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

async def on_shutdown(app):
    """Корректное завершение работы бота"""
    print("Shutting down...")
    try:
        await bot.session.close()
        await dp.storage.close()
    except Exception as e:
        print(f"Error during shutdown: {e}")

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

async def main():
    """Start the bot."""
    try:
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
        await site.start()
        print(f"Web server is running on port {port}")
        
        # Ждем завершения
        await asyncio.Event().wait()
        
    except Exception as e:
        print(f"Error: {e}")
        raise
    finally:
        await on_shutdown(app)

if __name__ == '__main__':
    try:
        # Добавляем обработчики сигналов
        for signal_name in ('SIGINT', 'SIGTERM'):
            if hasattr(signal, signal_name):
                signal.signal(getattr(signal, signal_name), lambda s, f: asyncio.get_event_loop().stop())
        
        # Запускаем бота
        print("Starting bot...")
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print('Bot stopped')
    except Exception as e:
        print(f"Critical error: {e}")
        sys.exit(1) 