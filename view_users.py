import sqlite3
import json
from datetime import datetime

DB_FILE = 'weather_bot.db'

def format_user_preferences(row):
    if not row:
        return "Нет настроек"
    
    notification_time = row[1] or "Не установлено"
    temp_min = row[2] if row[2] is not None else "Не установлено"
    temp_max = row[3] if row[3] is not None else "Не установлено"
    wind_threshold = row[4] if row[4] is not None else "Не установлено"
    rain_alerts = "Включены" if row[5] else "Выключены"
    activities = json.loads(row[6]) if row[6] else []
    
    return f"""
    🕒 Время уведомлений: {notification_time}
    🌡 Диапазон температур: {temp_min}°C - {temp_max}°C
    💨 Порог ветра: {wind_threshold} м/с
    🌧 Уведомления о дожде: {rain_alerts}
    🎯 Активности: {', '.join(activities) if activities else 'Не указаны'}
    """

try:
    # Подключаемся к базе данных
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Получаем список всех пользователей с их данными
    cursor.execute("""
        SELECT 
            u.*,
            GROUP_CONCAT(DISTINCT s.city) as subscribed_cities,
            p.notification_time,
            p.temp_min,
            p.temp_max,
            p.wind_threshold,
            p.rain_alerts,
            p.activities
        FROM users u
        LEFT JOIN subscriptions s ON u.user_id = s.user_id
        LEFT JOIN user_preferences p ON u.user_id = p.user_id
        GROUP BY u.user_id
    """)
    users = cursor.fetchall()
    
    if not users:
        print("В базе данных нет пользователей.")
    else:
        print(f"Найдено пользователей: {len(users)}\n")
        for user in users:
            user_id = user[0]
            username = user[1] or "Не указан"
            first_name = user[2] or "Не указано"
            last_name = user[3] or "Не указано"
            language_code = user[4] or "Не указан"
            is_premium = "Да" if user[5] else "Нет"
            joined_at = user[6]
            cities = user[7].split(',') if user[7] else []
            preferences = user[8:]
            
            print(f"👤 Пользователь ID: {user_id}")
            print(f"📝 Имя пользователя: @{username}")
            print(f"👨 Имя: {first_name}")
            print(f"👨 Фамилия: {last_name}")
            print(f"🌐 Язык: {language_code}")
            print(f"⭐ Premium: {is_premium}")
            print(f"📅 Дата регистрации: {joined_at}")
            print(f"🌆 Подписки на города: {', '.join(cities) if cities else 'Нет подписок'}")
            print("⚙️ Настройки:", format_user_preferences(preferences))
            print("-" * 50)
    
    # Показываем общую статистику
    print("\n📊 Общая статистика:")
    
    # Количество активных пользователей (с подписками)
    cursor.execute("""
        SELECT COUNT(DISTINCT user_id) 
        FROM subscriptions
    """)
    active_users = cursor.fetchone()[0]
    print(f"👥 Активных пользователей (с подписками): {active_users}")
    
    # Количество подписок
    cursor.execute("""
        SELECT COUNT(*) 
        FROM subscriptions
    """)
    total_subscriptions = cursor.fetchone()[0]
    print(f"🔔 Всего подписок: {total_subscriptions}")
    
    # Популярные города
    cursor.execute("""
        SELECT city, COUNT(*) as count 
        FROM subscriptions 
        GROUP BY city 
        ORDER BY count DESC 
        LIMIT 5
    """)
    popular_cities = cursor.fetchall()
    if popular_cities:
        print("\n🏆 Топ-5 популярных городов:")
        for city, count in popular_cities:
            print(f"  • {city}: {count} подписчиков")
    
except sqlite3.OperationalError as e:
    if "no such table" in str(e):
        print("База данных пуста или не существует. Возможно, бот еще не запускался.")
    else:
        print(f"Ошибка при работе с базой данных: {e}")
except Exception as e:
    print(f"Произошла ошибка: {e}")
finally:
    if 'conn' in locals():
        conn.close() 