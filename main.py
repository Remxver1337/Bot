import logging
import asyncio
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)
from config import ADMIN_ID, MAIN_BOT_TOKEN
from database import Database
import os
import threading
from datetime import datetime

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

db = Database()

# Запускаем проверку неактивных ботов в отдельном потоке
def check_inactive_bots_periodically():
    while True:
        try:
            inactive_count = db.check_inactive_bots()
            if inactive_count > 0:
                logger.info(f"Отключено {inactive_count} неактивных ботов")
        except Exception as e:
            logger.error(f"Ошибка при проверке неактивных ботов: {e}")
        
        # Проверяем каждые 6 часов
        threading.Event().wait(6 * 3600)

# Запускаем проверку
check_thread = threading.Thread(target=check_inactive_bots_periodically, daemon=True)
check_thread.start()

# Главное меню
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id == ADMIN_ID:
        keyboard = [
            [InlineKeyboardButton("🪞 Мои зеркала", callback_data='my_mirrors')],
            [InlineKeyboardButton("📢 Админ панель", callback_data='admin_panel')],
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("🪞 Мои зеркала", callback_data='my_mirrors')],
        ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "👋 Добро пожаловать в Mirror Bot Creator!\n\n"
        "Здесь вы можете создать своего бота-зеркала для работы с пользователями.",
        reply_markup=reply_markup
    )

# Админ панель
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != ADMIN_ID:
        await query.edit_message_text("❌ Доступ запрещен!")
        return
    
    keyboard = [
        [InlineKeyboardButton("📢 Сделать объявление", callback_data='make_announcement')],
        [InlineKeyboardButton("📊 Статистика", callback_data='admin_stats')],
        [InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')],
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "👑 **Админ панель**\n\n"
        "Выберите действие:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

# Создание объявления
async def make_announcement(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != ADMIN_ID:
        await query.edit_message_text("❌ Доступ запрещен!")
        return
    
    await query.edit_message_text(
        "📢 **Создание объявления**\n\n"
        "Отправьте текст объявления, которое будет разослано всем пользователям бота.",
        parse_mode='Markdown'
    )
    
    context.user_data['awaiting_announcement'] = True

# Обработка объявления
async def handle_announcement(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('awaiting_announcement'):
        return
    
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Доступ запрещен!")
        return
    
    message = update.message.text
    subscribers = db.get_all_subscribers()
    
    await update.message.reply_text(f"📢 Рассылка начата для {len(subscribers)} пользователей...")
    
    success = 0
    failed = 0
    
    for user_id in subscribers:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"📢 **Оповещение от администратора:**\n\n{message}",
                parse_mode='Markdown'
            )
            success += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            logger.error(f"Failed to send to {user_id}: {e}")
            failed += 1
    
    await update.message.reply_text(
        f"✅ Рассылка завершена!\n"
        f"Успешно: {success}\n"
        f"Не удалось: {failed}"
    )
    
    context.user_data['awaiting_announcement'] = False

# Статистика админа
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != ADMIN_ID:
        await query.edit_message_text("❌ Доступ запрещен!")
        return
    
    # Получаем статистику
    db.cursor.execute("SELECT COUNT(*) FROM mirror_bots")
    total_bots = db.cursor.fetchone()[0]
    
    db.cursor.execute("SELECT COUNT(*) FROM mirror_bots WHERE is_enabled = 1")
    active_bots = db.cursor.fetchone()[0]
    
    db.cursor.execute("SELECT COUNT(*) FROM subscribers")
    total_users = db.cursor.fetchone()[0]
    
    keyboard = [
        [InlineKeyboardButton("🔙 Назад", callback_data='admin_panel')],
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"📊 **Статистика системы**\n\n"
        f"🤖 Всего ботов: {total_bots}\n"
        f"🟢 Активных ботов: {active_bots}\n"
        f"👥 Пользователей: {total_users}",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

# Мои зеркала
async def my_mirrors(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    bots = db.get_user_bots(user_id)
    
    if not bots:
        keyboard = [
            [InlineKeyboardButton("➕ Создать зеркало", callback_data='create_mirror')],
        ]
        
        if user_id == ADMIN_ID:
            keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "🪞 **Мои зеркала**\n\n"
            "У вас ещё нет созданных зеркал.\n\n"
            "Чтобы создать зеркало:\n"
            "1. Создайте бота через @BotFather\n"
            "2. Получите токен бота\n"
            "3. Добавьте его через кнопку ниже",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    else:
        keyboard = []
        
        for bot in bots:
            bot_id, owner_id, token, username, created_at, last_activity, status, is_enabled = bot
            
            users_count = db.count_bot_users(token)
            
            # Определяем статус
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
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "🪞 **Мои зеркала**\n\n"
            "Выберите бота для управления:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

# Создание зеркала
async def create_mirror(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    # Проверяем лимит ботов
    bots = db.get_user_bots(user_id)
    if len(bots) >= 1:
        await query.edit_message_text(
            "❌ **Лимит ботов достигнут!**\n\n"
            "Вы можете создать только 1 зеркало.\n"
            "Удалите существующее зеркало, чтобы создать новое.",
            parse_mode='Markdown'
        )
        return
    
    await query.edit_message_text(
        "🤖 **Создание зеркала**\n\n"
        "1. Создайте бота через @BotFather\n"
        "2. Получите токен бота (выглядит как: `1234567890:ABCdefGHIjklMnoPQRstuVWXyz`)\n"
        "3. Отправьте токен сюда\n\n"
        "⚠️ **ВАЖНО:**\n"
        "• Убедитесь, что бот не был использован ранее\n"
        "• После создания добавьте бота в свой чат как администратора",
        parse_mode='Markdown'
    )
    
    context.user_data['awaiting_token'] = True

# Обработка токена бота
async def handle_bot_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not context.user_data.get('awaiting_token'):
        return
    
    token = update.message.text.strip()
    
    # Проверка формата токена
    token_pattern = r'^\d+:[A-Za-z0-9_-]+$'
    if not re.match(token_pattern, token):
        await update.message.reply_text(
            "❌ **Неверный формат токена!**\n\n"
            "Токен должен быть в формате:\n"
            "`1234567890:ABCdefGHIjklMnoPQRstuVWXyz`\n\n"
            "Попробуйте еще раз:",
            parse_mode='Markdown'
        )
        return
    
    try:
        # Получаем информацию о боте
        from telegram import Bot
        temp_bot = Bot(token=token)
        bot_info = await temp_bot.get_me()
        bot_username = bot_info.username
        
        # Сохраняем в базу
        success, message = db.add_mirror_bot(user_id, token, bot_username)
        
        if success:
            # Запускаем зеркального бота в отдельном процессе
            import subprocess
            subprocess.Popen([
                'python', 'bot_mirror.py',
                '--token', token,
                '--owner', str(user_id)
            ])
            
            await update.message.reply_text(
                f"✅ **Бот создан успешно!**\n\n"
                f"🤖 Имя: @{bot_username}\n"
                f"🔗 Ссылка: https://t.me/{bot_username}\n\n"
                "📋 **Что дальше?**\n"
                "1. Добавьте бота в чат как администратора\n"
                "2. Перейдите в бота @{bot_username}\n"
                "3. Начните настройку",
                parse_mode='Markdown'
            )
        elif message == "limit_reached":
            await update.message.reply_text(
                "❌ **Лимит ботов достигнут!**\n\n"
                "Вы можете создать только 1 зеркало.\n"
                "Удалите существующее зеркало, чтобы создать новое.",
                parse_mode='Markdown'
            )
        elif message == "already_exists":
            await update.message.reply_text(
                "❌ **Этот бот уже зарегистрирован!**\n\n"
                "Данный токен уже используется в системе.\n"
                "Используйте другой токен.",
                parse_mode='Markdown'
            )
    
    except Exception as e:
        logger.error(f"Error creating bot: {e}")
        await update.message.reply_text(
            "❌ **Ошибка при создании бота!**\n\n"
            "Возможные причины:\n"
            "1. Неверный токен\n"
            "2. Проблемы с сетью\n"
            "3. Бот заблокирован\n\n"
            "Проверьте токен и попробуйте еще раз.",
            parse_mode='Markdown'
        )
    
    context.user_data['awaiting_token'] = False

# Детали бота
async def bot_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    bot_token_short = query.data.replace('bot_detail_', '')
    user_id = query.from_user.id
    
    # Находим полный токен
    user_bots = db.get_user_bots(user_id)
    target_bot = None
    
    for bot in user_bots:
        if bot[2].startswith(bot_token_short):
            target_bot = bot
            break
    
    if not target_bot:
        await query.edit_message_text("❌ Бот не найден или доступ запрещен!")
        return
    
    bot_id, owner_id, token, username, created_at, last_activity, status, is_enabled = target_bot
    
    # Проверяем права доступа
    if user_id != owner_id and not db.check_bot_access(user_id, token):
        await query.edit_message_text("❌ Доступ запрещен!")
        return
    
    users_count = db.count_bot_users(token)
    is_enabled_bool = is_enabled == 1
    
    keyboard = []
    
    # Кнопка включения/выключения
    if user_id == owner_id:  # Только владелец может включать/выключать
        keyboard.append([
            InlineKeyboardButton(
                "🔴 Выключить" if is_enabled_bool else "🟢 Включить",
                callback_data=f'toggle_bot_{token[:10]}'
            )
        ])
    
    # Пользователи с доступом (только для владельца)
    if user_id == owner_id:
        access_users = db.get_bot_access_users(token)
        keyboard.append([
            InlineKeyboardButton(
                f"👥 Пользователи доступа ({len(access_users)-1}/9)",
                callback_data=f'access_users_{token[:10]}'
            )
        ])
    
    keyboard.append([
        InlineKeyboardButton(
            f"📋 Добавленные пользователи ({users_count})",
            callback_data=f'bot_users_{token[:10]}_page_1'
        )
    ])
    
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data='my_mirrors')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    status_text = "🟢 Активен" if is_enabled_bool else "🔴 Выключен"
    if status == 'inactive':
        status_text = "⚫ Неактивен (автоотключение)"
    
    await query.edit_message_text(
        f"🤖 **Информация о боте**\n\n"
        f"Имя: @{username}\n"
        f"Статус: {status_text}\n"
        f"Создан: {created_at[:10]}\n"
        f"Последняя активность: {last_activity[:10] if last_activity else 'Нет данных'}\n\n"
        f"📊 **Статистика:**\n"
        f"• Пользователей в базе: {users_count}\n",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

# Включение/выключение бота
async def toggle_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    bot_token_short = query.data.replace('toggle_bot_', '')
    user_id = query.from_user.id
    
    # Находим бота
    user_bots = db.get_user_bots(user_id)
    target_bot = None
    
    for bot in user_bots:
        if bot[2].startswith(bot_token_short):
            target_bot = bot
            break
    
    if not target_bot or user_id != target_bot[1]:  # Проверяем, что это владелец
        await query.edit_message_text("❌ Только владелец может управлять статусом бота!")
        return
    
    token = target_bot[2]
    is_enabled = target_bot[7] == 1
    
    # Меняем статус
    success = db.toggle_bot_status(user_id, token, not is_enabled)
    
    if success:
        await query.edit_message_text(
            f"✅ Бот {'включен' if not is_enabled else 'выключен'}!",
            parse_mode='Markdown'
        )
    else:
        await query.edit_message_text(
            "❌ Ошибка при изменении статуса!",
            parse_mode='Markdown'
        )
    
    # Возвращаемся к деталям бота
    await bot_detail(update, context)

# Пользователи с доступом
async def access_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    bot_token_short = query.data.replace('access_users_', '')
    user_id = query.from_user.id
    
    # Находим бота
    user_bots = db.get_user_bots(user_id)
    target_bot = None
    
    for bot in user_bots:
        if bot[2].startswith(bot_token_short):
            target_bot = bot
            break
    
    if not target_bot or user_id != target_bot[1]:
        await query.edit_message_text("❌ Доступ запрещен!")
        return
    
    token = target_bot[2]
    access_users = db.get_bot_access_users(token)
    
    keyboard = []
    
    for access_user_id in access_users:
        if access_user_id != user_id:  # Не показываем владельца
            keyboard.append([
                InlineKeyboardButton(
                    f"👤 ID: {access_user_id}",
                    callback_data=f'remove_access_{token[:10]}_{access_user_id}'
                )
            ])
    
    keyboard.append([
        InlineKeyboardButton(
            "➕ Добавить пользователя",
            callback_data=f'add_access_{token[:10]}'
        )
    ])
    
    keyboard.append([
        InlineKeyboardButton("🔙 Назад", callback_data=f'bot_detail_{token[:10]}')
    ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"👥 **Пользователи с доступом**\n\n"
        f"Текущие пользователи: {len(access_users)-1}/9\n\n"
        "Нажмите на пользователя, чтобы удалить его доступ:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

# Добавление пользователя с доступом
async def add_access_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    bot_token_short = query.data.replace('add_access_', '')
    user_id = query.from_user.id
    
    # Находим бота
    user_bots = db.get_user_bots(user_id)
    target_bot = None
    
    for bot in user_bots:
        if bot[2].startswith(bot_token_short):
            target_bot = bot
            break
    
    if not target_bot or user_id != target_bot[1]:
        await query.edit_message_text("❌ Доступ запрещен!")
        return
    
    context.user_data['awaiting_access_user'] = {
        'token': target_bot[2],
        'token_short': bot_token_short
    }
    
    await query.edit_message_text(
        "➕ **Добавление пользователя**\n\n"
        "Отправьте Telegram ID пользователя, которому хотите дать доступ к боту.\n\n"
        "Как узнать ID пользователя?\n"
        "1. Попросите пользователя написать боту @userinfobot\n"
        "2. Или используйте другого бота для получения ID\n\n"
        "Отправьте ID:",
        parse_mode='Markdown'
    )

# Обработка добавления пользователя с доступом
async def handle_access_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'awaiting_access_user' not in context.user_data:
        return
    
    user_id = update.effective_user.id
    access_data = context.user_data['awaiting_access_user']
    
    try:
        access_user_id = int(update.message.text.strip())
        
        # Проверяем, что это не сам владелец
        if access_user_id == user_id:
            await update.message.reply_text("❌ Нельзя добавить себя!")
            del context.user_data['awaiting_access_user']
            return
        
        # Добавляем доступ
        success, message = db.add_bot_access(
            user_id, 
            access_data['token'], 
            access_user_id
        )
        
        if success:
            await update.message.reply_text(
                f"✅ Пользователь {access_user_id} добавлен!\n"
                f"Теперь он имеет доступ к вашему боту."
            )
        elif message == "limit_reached":
            await update.message.reply_text(
                "❌ Достигнут лимит пользователей!\n"
                "Максимум 10 пользователей (включая владельца)."
            )
        elif message == "already_exists":
            await update.message.reply_text(
                "❌ Этот пользователь уже имеет доступ!"
            )
        elif message == "not_owner":
            await update.message.reply_text("❌ Ошибка доступа!")
        
    except ValueError:
        await update.message.reply_text("❌ Неверный формат ID! Отправьте числовой ID.")
    except Exception as e:
        logger.error(f"Error adding access user: {e}")
        await update.message.reply_text("❌ Ошибка при добавлении пользователя!")
    
    del context.user_data['awaiting_access_user']

# Удаление доступа пользователя
async def remove_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data.replace('remove_access_', '')
    parts = data.split('_')
    
    if len(parts) < 2:
        await query.edit_message_text("❌ Ошибка!")
        return
    
    bot_token_short = parts[0]
    access_user_id = int(parts[1])
    user_id = query.from_user.id
    
    # Находим бота
    user_bots = db.get_user_bots(user_id)
    target_bot = None
    
    for bot in user_bots:
        if bot[2].startswith(bot_token_short):
            target_bot = bot
            break
    
    if not target_bot or user_id != target_bot[1]:
        await query.edit_message_text("❌ Доступ запрещен!")
        return
    
    token = target_bot[2]
    
    # Удаляем доступ
    success = db.remove_bot_access(user_id, token, access_user_id)
    
    if success:
        await query.edit_message_text(f"✅ Пользователь {access_user_id} удален!")
    else:
        await query.edit_message_text("❌ Ошибка при удалении!")
    
    # Возвращаемся к списку пользователей
    await access_users(update, context)

# Пользователи бота
async def bot_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data.replace('bot_users_', '')
    parts = data.split('_')
    
    if len(parts) < 3:
        await query.edit_message_text("❌ Ошибка!")
        return
    
    bot_token_short = parts[0]
    page = int(parts[2])
    user_id = query.from_user.id
    
    # Находим бота
    user_bots = db.get_user_bots(user_id)
    target_bot = None
    
    for bot in user_bots:
        if bot[2].startswith(bot_token_short):
            target_bot = bot
            break
    
    if not target_bot:
        await query.edit_message_text("❌ Бот не найден!")
        return
    
    token = target_bot[2]
    users = db.get_bot_users(token, page=page, limit=20)
    total_users = db.count_bot_users(token)
    total_pages = (total_users + 19) // 20  # Округление вверх
    
    if not users:
        keyboard = [
            [InlineKeyboardButton("➕ Добавить пользователей", callback_data='add_bot_users')],
            [InlineKeyboardButton("🔙 Назад", callback_data=f'bot_detail_{bot_token_short}')],
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "👥 **Добавленные пользователи**\n\n"
            "Список пользователей пуст.\n\n"
            "Чтобы добавить пользователей, перейдите в самого бота-зеркала "
            "и используйте функцию 'Мои пользователи'.",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return
    
    keyboard = []
    
    for user in users:
        user_id_db, _, _, username, added_at = user
        keyboard.append([
            InlineKeyboardButton(
                f"👤 {username}",
                callback_data=f'user_detail_{bot_token_short}_{username}'
            )
        ])
    
    # Пагинация
    pagination = []
    
    if page > 1:
        pagination.append(
            InlineKeyboardButton("◀️", callback_data=f'bot_users_{bot_token_short}_page_{page-1}')
        )
    
    pagination.append(
        InlineKeyboardButton(f"{page}/{total_pages}", callback_data='current')
    )
    
    if page < total_pages:
        pagination.append(
            InlineKeyboardButton("▶️", callback_data=f'bot_users_{bot_token_short}_page_{page+1}')
        )
    
    if pagination:
        keyboard.append(pagination)
    
    keyboard.append([
        InlineKeyboardButton("🔙 Назад", callback_data=f'bot_detail_{bot_token_short}')
    ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"👥 **Добавленные пользователи**\n\n"
        f"Страница {page} из {total_pages}\n"
        f"Всего пользователей: {total_users}\n\n"
        "Выберите пользователя для управления:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

# Детали пользователя
async def user_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data.replace('user_detail_', '')
    parts = data.split('_')
    
    if len(parts) < 2:
        await query.edit_message_text("❌ Ошибка!")
        return
    
    bot_token_short = parts[0]
    username = '_'.join(parts[1:])
    user_id = query.from_user.id
    
    # Находим бота
    user_bots = db.get_user_bots(user_id)
    target_bot = None
    
    for bot in user_bots:
        if bot[2].startswith(bot_token_short):
            target_bot = bot
            break
    
    if not target_bot:
        await query.edit_message_text("❌ Бот не найден!")
        return
    
    token = target_bot[2]
    
    keyboard = [
        [
            InlineKeyboardButton("🗑️ Удалить", callback_data=f'delete_user_{bot_token_short}_{username}'),
            InlineKeyboardButton("🔙 Назад", callback_data=f'bot_users_{bot_token_short}_page_1'),
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"👤 **Информация о пользователе**\n\n"
        f"Имя пользователя: {username}\n"
        f"Бот: @{target_bot[3]}\n\n"
        "Выберите действие:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

# Удаление пользователя
async def delete_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data.replace('delete_user_', '')
    parts = data.split('_')
    
    if len(parts) < 2:
        await query.edit_message_text("❌ Ошибка!")
        return
    
    bot_token_short = parts[0]
    username = '_'.join(parts[1:])
    user_id = query.from_user.id
    
    # Находим бота
    user_bots = db.get_user_bots(user_id)
    target_bot = None
    
    for bot in user_bots:
        if bot[2].startswith(bot_token_short):
            target_bot = bot
            break
    
    if not target_bot:
        await query.edit_message_text("❌ Бот не найден!")
        return
    
    token = target_bot[2]
    
    # Удаляем пользователя
    success = db.delete_bot_user(user_id, token, username)
    
    if success:
        await query.edit_message_text(f"✅ Пользователь {username} удален!")
    else:
        await query.edit_message_text("❌ Ошибка при удалении!")
    
    # Возвращаемся к списку пользователей
    await bot_users(update, context)

# Обработка callback
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    
    if data == 'my_mirrors':
        await my_mirrors(update, context)
    elif data == 'admin_panel':
        await admin_panel(update, context)
    elif data == 'make_announcement':
        await make_announcement(update, context)
    elif data == 'admin_stats':
        await admin_stats(update, context)
    elif data == 'create_mirror':
        await create_mirror(update, context)
    elif data == 'back_to_main':
        await start(update, context)
    elif data.startswith('bot_detail_'):
        await bot_detail(update, context)
    elif data.startswith('toggle_bot_'):
        await toggle_bot(update, context)
    elif data.startswith('access_users_'):
        await access_users(update, context)
    elif data.startswith('add_access_'):
        await add_access_user(update, context)
    elif data.startswith('remove_access_'):
        await remove_access(update, context)
    elif data.startswith('bot_users_'):
        await bot_users(update, context)
    elif data.startswith('user_detail_'):
        await user_detail(update, context)
    elif data.startswith('delete_user_'):
        await delete_user(update, context)

# Команда /bc для админа
async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Эта команда только для администратора!")
        return
    
    if not context.args:
        await update.message.reply_text("Использование: /bc Текст рассылки")
        return
    
    message = ' '.join(context.args)
    subscribers = db.get_all_subscribers()
    
    await update.message.reply_text(f"📢 Рассылка начата для {len(subscribers)} пользователей...")
    
    success = 0
    failed = 0
    
    for user_id in subscribers:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"📢 **Оповещение от администратора:**\n\n{message}",
                parse_mode='Markdown'
            )
            success += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            logger.error(f"Failed to send to {user_id}: {e}")
            failed += 1
    
    await update.message.reply_text(
        f"✅ Рассылка завершена!\n"
        f"Успешно: {success}\n"
        f"Не удалось: {failed}"
    )

def main():
    # Создаем Application
    application = Application.builder().token(MAIN_BOT_TOKEN).build()
    
    # Обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("bc", broadcast_command))
    
    # Обработчики callback
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Обработчики сообщений
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, 
        handle_bot_token
    ))
    
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, 
        handle_announcement
    ))
    
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, 
        handle_access_user
    ))
    
    # Запуск бота
    print("✅ Основной бот запущен!")
    application.run_polling(allowed_updates=Update.ALL_UPDATES)

if __name__ == '__main__':
    main()