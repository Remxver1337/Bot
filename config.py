import os
from dotenv import load_dotenv

load_dotenv()

# Конфигурация
ADMIN_ID = 7404231636  # Ваш Telegram ID
MAIN_BOT_TOKEN = os.getenv('MAIN_BOT_TOKEN')
DOMAIN = os.getenv('DOMAIN', 'your-domain.com')
PORT = int(os.getenv('PORT', 8443))

# Настройки вебхука
WEBHOOK_PATH = f"/webhook/{{token}}"
WEBHOOK_URL = f"https://{DOMAIN}{WEBHOOK_PATH}"

# Ограничения
MAX_MIRRORS_PER_USER = 1
MAX_ACCESS_USERS = 10
INACTIVITY_DAYS = 7  # дней неактивности до отключения