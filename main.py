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

# Bot commands for menu
COMMANDS = [
    BotCommand(command='start', description='Запустить бота'),
    BotCommand(command='help', description='Показать помощь'),
    BotCommand(command='forecast', description='Прогноз погоды на 5 дней'),
    BotCommand(command='detailed', description='Подробная информация о погоде'),
    BotCommand(command='air', description='Качество воздуха'),
    BotCommand(command='compare', description='Сравнить погоду в двух городах')
]

def create_main_keyboard():
    """Create main keyboard with location button."""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📍 Отправить геолокацию", request_location=True)],
            [KeyboardButton(text="ℹ️ Помощь")]
        ],
        resize_keyboard=True
    )
    return keyboard

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
        '6. Сравнить погоду в разных городах (/compare)\n\n'
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
        '6. Нажмите кнопку "📍 Отправить геолокацию" для погоды в вашем месте\n\n'
        'Примеры:\n'
        '- "Москва" - текущая погода\n'
        '- "/forecast Париж" - прогноз на 5 дней\n'
        '- "/detailed Лондон" - подробная информация',
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
        
        # Format and send detailed weather information
        detailed_message = format_detailed_weather(weather_data, city)
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
            1: "Отличное 😊",
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
        
        # Format and send detailed weather information
        detailed_message = format_detailed_weather(weather_data, city_name)
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
        
        # Format and send detailed weather information
        detailed_message = format_detailed_weather(weather_data, city)
        await message.answer(detailed_message)
        
    except Exception as e:
        logging.error(f"Error getting weather: {e}")
        await message.answer(
            "Извините, произошла ошибка при получении прогноза погоды. "
            "Пожалуйста, попробуйте позже."
        )

async def set_commands():
    """Set bot commands in the menu."""
    await bot.set_my_commands(COMMANDS)

async def main():
    """Start the bot."""
    # Set bot commands
    await set_commands()
    
    # Start polling
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main()) 