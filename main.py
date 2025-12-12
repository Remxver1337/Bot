import logging
import asyncio
import re
import sys
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)

from config import MAIN_BOT_TOKEN, ADMIN_ID, MIRROR_DOMAIN, MIRROR_PORT

print("\n" + "="*60)
print("🤖 ОСНОВНОЙ БОТ - Mirror Bot Creator")
print("Запущен на bothost.ru")
print("="*60)

# Проверка токена
if not MAIN_BOT_TOKEN:
    print("❌ ОШИБКА: Токен не найден!")
    sys.exit(1)

print(f"✅ Токен: {MAIN_BOT_TOKEN[:15]}...")
print(f"✅ Админ ID: {ADMIN_ID}")
print(f"✅ Домен для зеркал: {MIRROR_DOMAIN}:{MIRROR_PORT}")

# Импортируем базу данных
from database import Database
import threading
from datetime import datetime

db = Database()

# Запускаем проверку неактивных ботов
def check_inactive_bots():
    while True:
        try:
            inactive_count = db.check_inactive_bots()
            if inactive_count > 0:
                logging.info(f"Отключено {inactive_count} неактивных ботов")
        except Exception as e:
            logging.error(f"Ошибка при проверке неактивных ботов: {e}")
        threading.Event().wait(6 * 3600)

threading.Thread(target=check_inactive_bots, daemon=True).start()

# ========== ВСЕ ОБРАБОТЧИКИ КОМАНД (такие же как раньше) ==========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db.add_subscriber(user_id)
    
    keyboard = [[InlineKeyboardButton("🪞 Мои зеркала", callback_data='my_mirrors')]]
    
    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("📢 Админ панель", callback_data='admin_panel')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "👋 Добро пожаловать в Mirror Bot Creator!\n\n"
        "Здесь вы можете создавать и управлять ботами-зеркалами.",
        reply_markup=reply_markup
    )

async def my_mirrors(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    bots = db.get_user_bots(user_id)
    
    if not bots:
        keyboard = [[InlineKeyboardButton("➕ Создать зеркало", callback_data='create_mirror')]]
        if user_id == ADMIN_ID:
            keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')])
        
        await query.edit_message_text(
            "🪞 **Мои зеркала**\n\nУ вас ещё нет созданных зеркал.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    else:
        keyboard = []
        for bot in bots:
            _, _, token, username, _, _, status, is_enabled = bot
            users_count = db.count_bot_users(token)
            status_emoji = "🟢" if is_enabled == 1 else "🔴"
            keyboard.append([
                InlineKeyboardButton(
                    f"@{username} ({status_emoji}, 👥 {users_count})", 
                    callback_data=f'bot_detail_{token[:10]}'
                )
            ])
        
        keyboard.append([InlineKeyboardButton("➕ Создать зеркало", callback_data='create_mirror')])
        if user_id == ADMIN_ID:
            keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')])
        
        await query.edit_message_text(
            "🪞 **Мои зеркала**\n\nВыберите бота:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )

async def create_mirror(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    bots = db.get_user_bots(user_id)
    
    if len(bots) >= 1:
        await query.edit_message_text("❌ Лимит ботов достигнут! Максимум 1 бот на пользователя.")
        return
    
    await query.edit_message_text(
        "🤖 **Создание зеркала**\n\n"
        "Отправьте токен бота от @BotFather:",
        parse_mode='Markdown'
    )
    
    context.user_data['awaiting_token'] = True

async def handle_bot_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('awaiting_token'):
        return
    
    user_id = update.effective_user.id
    token = update.message.text.strip()
    
    token_pattern = r'^\d+:[A-Za-z0-9_-]+$'
    if not re.match(token_pattern, token):
        await update.message.reply_text("❌ Неверный формат токена!")
        return
    
    try:
        from telegram import Bot
        temp_bot = Bot(token=token)
        bot_info = await temp_bot.get_me()
        bot_username = bot_info.username
        
        success, message = db.add_mirror_bot(user_id, token, bot_username)
        
        if success:
            # Запускаем зеркального бота с вебхуком на домене
            import subprocess
            subprocess.Popen([
                'python', 'bot_mirror.py',
                '--token', token,
                '--owner', str(user_id),
                '--domain', MIRROR_DOMAIN,
                '--port', str(MIRROR_PORT)
            ])
            
            await update.message.reply_text(
                f"✅ Бот @{bot_username} создан!\n"
                f"Webhook: https://{MIRROR_DOMAIN}:{MIRROR_PORT}/webhook/{token}"
            )
        elif message == "limit_reached":
            await update.message.reply_text("❌ Лимит ботов достигнут!")
        elif message == "already_exists":
            await update.message.reply_text("❌ Бот уже зарегистрирован!")
            
    except Exception as e:
        logging.error(f"Error creating bot: {e}")
        await update.message.reply_text("❌ Ошибка при создании бота!")
    
    context.user_data['awaiting_token'] = False

# ... ДОБАВЬТЕ ВСЕ ОСТАЛЬНЫЕ ОБРАБОТЧИКИ ИЗ ПРЕДЫДУЩЕЙ ВЕРСИИ ...

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Только для администратора!")
        return
    
    if not context.args:
        await update.message.reply_text("Использование: /bc текст")
        return
    
    message = ' '.join(context.args)
    subscribers = db.get_all_subscribers()
    
    success = 0
    for user_id in subscribers:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"📢 **Оповещение:**\n\n{message}",
                parse_mode='Markdown'
            )
            success += 1
            await asyncio.sleep(0.05)
        except:
            pass
    
    await update.message.reply_text(f"✅ Отправлено {success}/{len(subscribers)}")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    
    if data == 'my_mirrors':
        await my_mirrors(update, context)
    elif data == 'create_mirror':
        await create_mirror(update, context)
    elif data == 'back_to_main':
        await start(update, context)
    elif data.startswith('bot_detail_'):
        # ... обработка деталей бота ...
        pass
    # ... остальные обработчики ...

def main():
    # Настройка логирования
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO
    )
    
    # Создаем Application
    application = Application.builder().token(MAIN_BOT_TOKEN).build()
    
    # Обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("bc", broadcast_command))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, 
        handle_bot_token
    ))
    
    # Запуск бота
    print("✅ Основной бот запущен на bothost.ru")
    print("="*60)
    
    # Bothost сам управляет вебхуками, просто запускаем polling
    application.run_polling()

if __name__ == '__main__':
    main()