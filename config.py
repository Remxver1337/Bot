import os
import sys

print("=" * 50)
print("CONFIG.PY: Настройки для bothost.ru")
print("=" * 50)

# Токен основного бота
MAIN_BOT_TOKEN = "8568866654:AAFfLobjJfnbjwltSdy4IAw_-3yBzw3rGm8"

# Админ
ADMIN_ID = 7404231636

# Домен и порт для зеркальных ботов (вебхуки)
MIRROR_DOMAIN = "bot_1765579907_1589_remxver1337.bothost.ru"
MIRROR_PORT = 443
MIRROR_WEBHOOK_URL = f"https://{MIRROR_DOMAIN}/webhook"

print(f"✅ Токен основного бота: {MAIN_BOT_TOKEN[:15]}...")
print(f"✅ Админ ID: {ADMIN_ID}")
print(f"✅ Домен для зеркал: {MIRROR_DOMAIN}")
print(f"✅ Порт для зеркал: {MIRROR_PORT}")
print(f"✅ Webhook URL для зеркал: {MIRROR_WEBHOOK_URL}/{{token}}")

# Ограничения
MAX_MIRRORS_PER_USER = 1
MAX_ACCESS_USERS = 10
INACTIVITY_DAYS = 7

print("✅ Конфигурация загружена!")
print("=" * 50)