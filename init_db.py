import sqlite3

DB_FILE = 'weather_bot.db'

def init_db():
    """Инициализация базы данных"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    try:
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
        print("База данных успешно инициализирована!")
        
        # Проверяем созданные таблицы
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        print("\nСозданные таблицы:")
        for table in tables:
            print(f"- {table[0]}")
            
    except Exception as e:
        print(f"Ошибка при инициализации базы данных: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == '__main__':
    init_db() 