import logging
import sqlite3
import random
from typing import Dict, List, Tuple, Optional
from urllib.parse import quote
from datetime import datetime, timedelta
import asyncio
import json

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ID администратора
ADMIN_ID = 7404231636

# Словарь для замены кириллических букв на латинские
REPLACEMENTS = {
    'а': 'a', 'с': 'c', 'о': 'o', 'р': 'p', 'е': 'e', 'х': 'x', 'у': 'y',
    'А': 'A', 'С': 'C', 'О': 'O', 'Р': 'P', 'Е': 'E', 'Х': 'X', 'У': 'Y'
}

class MirrorDatabase:
    """База данных для управления зеркалами"""
    
    def __init__(self):
        self.db_name = "mirrors.db"
        self.init_database()
    
    def init_database(self):
        """Инициализация базы данных зеркал"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        # Таблица зеркал
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS mirrors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                bot_token TEXT NOT NULL UNIQUE,
                bot_username TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active INTEGER DEFAULT 1,
                UNIQUE(user_id, bot_token)
            )
        ''')
        
        # Таблица доступа к зеркалам
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS mirror_access (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mirror_id INTEGER NOT NULL,
                allowed_user_id INTEGER NOT NULL,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (mirror_id) REFERENCES mirrors (id),
                UNIQUE(mirror_id, allowed_user_id)
            )
        ''')
        
        # Таблица объявлений
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS announcements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER NOT NULL,
                message_text TEXT NOT NULL,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def add_mirror(self, user_id: int, bot_token: str, bot_username: str = None) -> bool:
        """Добавление нового зеркала"""
        try:
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            
            # Проверяем, не создал ли уже пользователь зеркало
            cursor.execute('SELECT id FROM mirrors WHERE user_id = ?', (user_id,))
            existing = cursor.fetchone()
            if existing:
                conn.close()
                return False
            
            cursor.execute('''
                INSERT INTO mirrors (user_id, bot_token, bot_username, created_at, last_activity)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, bot_token, bot_username, datetime.now(), datetime.now()))
            
            mirror_id = cursor.lastrowid
            
            # Добавляем создателя как первого пользователя с доступом
            cursor.execute('''
                INSERT INTO mirror_access (mirror_id, allowed_user_id)
                VALUES (?, ?)
            ''', (mirror_id, user_id))
            
            conn.commit()
            conn.close()
            return True
        except sqlite3.IntegrityError:
            return False
    
    def get_user_mirror(self, user_id: int) -> Optional[Tuple]:
        """Получение зеркала пользователя"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, bot_token, bot_username, created_at, last_activity, is_active
            FROM mirrors WHERE user_id = ?
        ''', (user_id,))
        result = cursor.fetchone()
        conn.close()
        return result
    
    def update_mirror_activity(self, mirror_id: int):
        """Обновление времени последней активности"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE mirrors SET last_activity = ? WHERE id = ?
        ''', (datetime.now(), mirror_id))
        conn.commit()
        conn.close()
    
    def deactivate_inactive_mirrors(self):
        """Деактивация зеркал без активности больше недели"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        week_ago = datetime.now() - timedelta(days=7)
        cursor.execute('''
            UPDATE mirrors SET is_active = 0 
            WHERE last_activity < ? AND is_active = 1
        ''', (week_ago,))
        conn.commit()
        conn.close()
    
    def add_user_to_mirror(self, mirror_id: int, user_id: int) -> bool:
        """Добавление пользователя к зеркалу"""
        try:
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            
            # Проверяем, сколько уже пользователей
            cursor.execute('''
                SELECT COUNT(*) FROM mirror_access WHERE mirror_id = ?
            ''', (mirror_id,))
            count = cursor.fetchone()[0]
            
            if count >= 10:
                conn.close()
                return False
            
            cursor.execute('''
                INSERT INTO mirror_access (mirror_id, allowed_user_id)
                VALUES (?, ?)
            ''', (mirror_id, user_id))
            
            conn.commit()
            conn.close()
            return True
        except sqlite3.IntegrityError:
            return False
    
    def check_user_access(self, mirror_id: int, user_id: int) -> bool:
        """Проверка доступа пользователя к зеркалу"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT 1 FROM mirror_access 
            WHERE mirror_id = ? AND allowed_user_id = ?
        ''', (mirror_id, user_id))
        result = cursor.fetchone() is not None
        conn.close()
        return result
    
    def get_mirror_users(self, mirror_id: int) -> List[int]:
        """Получение списка пользователей с доступом к зеркалу"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT allowed_user_id FROM mirror_access WHERE mirror_id = ?
        ''', (mirror_id,))
        users = [row[0] for row in cursor.fetchall()]
        conn.close()
        return users
    
    def remove_user_from_mirror(self, mirror_id: int, user_id: int):
        """Удаление пользователя из зеркала (кроме создателя)"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        # Получаем создателя зеркала
        cursor.execute('SELECT user_id FROM mirrors WHERE id = ?', (mirror_id,))
        creator_id = cursor.fetchone()[0]
        
        if user_id != creator_id:
            cursor.execute('''
                DELETE FROM mirror_access 
                WHERE mirror_id = ? AND allowed_user_id = ?
            ''', (mirror_id, user_id))
        
        conn.commit()
        conn.close()
    
    def get_all_mirrors(self) -> List[Tuple]:
        """Получение всех зеркал (для админа)"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, user_id, bot_username, created_at, last_activity, is_active
            FROM mirrors ORDER BY created_at DESC
        ''')
        mirrors = cursor.fetchall()
        conn.close()
        return mirrors
    
    def add_announcement(self, admin_id: int, message_text: str):
        """Добавление объявления"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO announcements (admin_id, message_text)
            VALUES (?, ?)
        ''', (admin_id, message_text))
        conn.commit()
        conn.close()
    
    def get_recent_announcements(self, limit: int = 5) -> List[Tuple]:
        """Получение последних объявлений"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT message_text, sent_at FROM announcements 
            ORDER BY sent_at DESC LIMIT ?
        ''', (limit,))
        announcements = cursor.fetchall()
        conn.close()
        return announcements

class UserDatabase:
    """База данных пользователя (общая для всех зеркал)"""
    
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.db_name = f"user_{user_id}.db"
        self.init_database()
    
    def init_database(self):
        """Инициализация базы данных для пользователя"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS variations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER,
                variation_text TEXT NOT NULL,
                send_count INTEGER DEFAULT 0,
                FOREIGN KEY (message_id) REFERENCES messages (id)
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS chats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                username TEXT NOT NULL,
                FOREIGN KEY (chat_id) REFERENCES chats (id)
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def add_message(self, original_text: str) -> int:
        """Добавление исходного сообщения"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('INSERT INTO messages (original_text) VALUES (?)', (original_text,))
        message_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return message_id
    
    def add_variations(self, message_id: int, variations: List[str]):
        """Добавление вариаций сообщения"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.executemany(
            'INSERT INTO variations (message_id, variation_text) VALUES (?, ?)',
            [(message_id, variation) for variation in variations]
        )
        conn.commit()
        conn.close()
    
    def get_messages(self) -> List[Tuple[int, str]]:
        """Получение списка исходных сообщений"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('SELECT id, original_text FROM messages ORDER BY created_at DESC')
        messages = cursor.fetchall()
        conn.close()
        return messages
    
    def delete_message(self, message_id: int):
        """Удаление сообщения и всех его вариаций"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM variations WHERE message_id = ?', (message_id,))
        cursor.execute('DELETE FROM messages WHERE id = ?', (message_id,))
        conn.commit()
        conn.close()
    
    def add_chat(self, chat_name: str) -> int:
        """Добавление чата"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        try:
            cursor.execute('INSERT INTO chats (name) VALUES (?)', (chat_name,))
            chat_id = cursor.lastrowid
        except sqlite3.IntegrityError:
            cursor.execute('SELECT id FROM chats WHERE name = ?', (chat_name,))
            chat_id = cursor.fetchone()[0]
        conn.commit()
        conn.close()
        return chat_id
    
    def add_users(self, chat_id: int, usernames: List[str]):
        """Добавление пользователей в чат"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.executemany(
            'INSERT OR IGNORE INTO users (chat_id, username) VALUES (?, ?)',
            [(chat_id, username.strip()) for username in usernames]
        )
        conn.commit()
        conn.close()
    
    def get_chats(self) -> List[Tuple[int, str]]:
        """Получение списка чатов"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('SELECT id, name FROM chats ORDER BY name')
        chats = cursor.fetchall()
        conn.close()
        return chats
    
    def delete_chat(self, chat_id: int):
        """Удаление чата и всех его пользователей"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM users WHERE chat_id = ?', (chat_id,))
        cursor.execute('DELETE FROM chats WHERE id = ?', (chat_id,))
        conn.commit()
        conn.close()
    
    def get_users_by_chat(self, chat_id: int, offset: int = 0, limit: int = 25) -> List[Tuple[int, str]]:
        """Получение пользователей чата с пагинацией"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute(
            'SELECT id, username FROM users WHERE chat_id = ? LIMIT ? OFFSET ?',
            (chat_id, limit, offset)
        )
        users = cursor.fetchall()
        conn.close()
        return users
    
    def get_random_variation(self) -> Tuple[int, str]:
        """Получение случайной вариации сообщения"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, variation_text FROM variations 
            WHERE send_count < 5 
            ORDER BY RANDOM() 
            LIMIT 1
        ''')
        result = cursor.fetchone()
        
        if result:
            variation_id, variation_text = result
            cursor.execute(
                'UPDATE variations SET send_count = send_count + 1 WHERE id = ?',
                (variation_id,)
            )
            cursor.execute('DELETE FROM variations WHERE send_count >= 5')
            conn.commit()
            conn.close()
            return variation_id, variation_text
        
        conn.close()
        return None, None
    
    def get_multiple_variations(self, count: int = 5) -> List[str]:
        """Получение нескольких случайных вариаций"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT variation_text FROM variations 
            WHERE send_count < 5 
            ORDER BY RANDOM() 
            LIMIT ?
        ''', (count,))
        results = cursor.fetchall()
        conn.close()
        
        variations = [result[0] for result in results]
        
        # Если не хватает вариаций, дублируем существующие
        while len(variations) < count:
            if variations:
                variations.append(random.choice(variations))
            else:
                break
        
        return variations

class MirrorManagerBot:
    """Основной бот для создания и управления зеркалами"""
    
    def __init__(self, token: str):
        self.application = Application.builder().token(token).build()
        self.mirror_db = MirrorDatabase()
        self.user_states = {}
        self.setup_handlers()
    
    def setup_handlers(self):
        """Настройка обработчиков"""
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("admin", self.admin_command))
        self.application.add_handler(CommandHandler("announce", self.announce_command))
        self.application.add_handler(CallbackQueryHandler(self.handle_button, pattern="^main_"))
        self.application.add_handler(CallbackQueryHandler(self.handle_mirrors, pattern="^mirrors_"))
        self.application.add_handler(CallbackQueryHandler(self.handle_admin, pattern="^admin_"))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_input))
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        user_id = update.effective_user.id
        
        # Проверяем, есть ли у пользователя зеркало
        user_mirror = self.mirror_db.get_user_mirror(user_id)
        
        welcome_text = (
            "🌟 Добро пожаловать в основной бот! 🌟\n\n"
            "📱 Этот бот предназначен для создания и управления зеркалами\n\n"
        )
        
        if user_mirror:
            mirror_id, bot_token, bot_username, created_at, last_activity, is_active = user_mirror
            welcome_text += (
                "✅ У вас уже есть зеркало!\n"
                f"👤 Имя бота: @{bot_username if bot_username else 'неизвестно'}\n"
                f"📅 Создан: {created_at.split()[0]}\n"
                f"🔄 Статус: {'Активно' if is_active else 'Неактивно'}\n\n"
            )
        
        welcome_text += (
            "✨ Доступные функции:\n"
            "• 🔄 Создать новое зеркало\n"
            "• 👥 Управление доступом к зеркалу\n"
            "• 📋 Посмотреть моё зеркало\n\n"
            "💡 Основной бот предназначен для ознакомления с функционалом. "
            "Пожалуйста, создайте зеркало и рассылайте из него"
        )
        
        keyboard = []
        
        if not user_mirror:
            keyboard.append([InlineKeyboardButton("🔄 Создать зеркало", callback_data="mirrors_create")])
        else:
            keyboard.append([InlineKeyboardButton("📋 Моё зеркало", callback_data="mirrors_view")])
            keyboard.append([InlineKeyboardButton("👥 Управление доступом", callback_data="mirrors_access")])
        
        keyboard.append([InlineKeyboardButton("🚀 Начать спам", callback_data="main_spam")])
        
        if user_id == ADMIN_ID:
            keyboard.append([InlineKeyboardButton("⚙️ Админ панель", callback_data="admin_panel")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if update.message:
            await update.message.reply_text(welcome_text, reply_markup=reply_markup)
        else:
            await update.callback_query.edit_message_text(welcome_text, reply_markup=reply_markup)
    
    async def admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /admin (только для админа)"""
        user_id = update.effective_user.id
        
        if user_id != ADMIN_ID:
            await update.message.reply_text("⛔ У вас нет прав доступа к этой команде")
            return
        
        await self.show_admin_panel(update, context)
    
    async def announce_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /announce (только для админа)"""
        user_id = update.effective_user.id
        
        if user_id != ADMIN_ID:
            await update.message.reply_text("⛔ У вас нет прав доступа к этой команде")
            return
        
        if not context.args:
            await update.message.reply_text("📝 Использование: /announce <текст объявления>")
            return
        
        announcement_text = ' '.join(context.args)
        self.mirror_db.add_announcement(user_id, announcement_text)
        
        # Отправляем объявление всем пользователям с зеркалами
        mirrors = self.mirror_db.get_all_mirrors()
        for mirror in mirrors:
            try:
                await context.bot.send_message(
                    chat_id=mirror[1],  # user_id создателя
                    text=f"📢 Объявление от администратора:\n\n{announcement_text}"
                )
            except Exception as e:
                logger.error(f"Ошибка отправки объявления пользователю {mirror[1]}: {e}")
        
        await update.message.reply_text(f"✅ Объявление отправлено {len(mirrors)} пользователям")
    
    async def show_admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать админ панель"""
        query = update.callback_query
        if query:
            await query.answer()
            message = query.message
        else:
            message = update.message
        
        # Получаем статистику
        mirrors = self.mirror_db.get_all_mirrors()
        active_mirrors = sum(1 for m in mirrors if m[5] == 1)
        total_users = sum(len(self.mirror_db.get_mirror_users(m[0])) for m in mirrors)
        
        announcements = self.mirror_db.get_recent_announcements(3)
        
        admin_text = (
            "⚙️ Админ панель\n\n"
            f"📊 Статистика:\n"
            f"• Всего зеркал: {len(mirrors)}\n"
            f"• Активных зеркал: {active_mirrors}\n"
            f"• Всего пользователей: {total_users}\n\n"
            f"📢 Последние объявления:\n"
        )
        
        if announcements:
            for i, (text, sent_at) in enumerate(announcements, 1):
                date_str = sent_at.split()[0] if isinstance(sent_at, str) else sent_at.strftime('%Y-%m-%d')
                admin_text += f"{i}. {date_str}: {text[:50]}...\n"
        else:
            admin_text += "Нет объявлений\n"
        
        admin_text += "\n✨ Доступные действия:"
        
        keyboard = [
            [InlineKeyboardButton("📋 Все зеркала", callback_data="admin_mirrors")],
            [InlineKeyboardButton("📢 Создать объявление", callback_data="admin_announce")],
            [InlineKeyboardButton("🔄 Деактивировать неактивные", callback_data="admin_deactivate")],
            [InlineKeyboardButton("🔙 Назад", callback_data="main_back")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if query:
            await query.edit_message_text(admin_text, reply_markup=reply_markup)
        else:
            await message.reply_text(admin_text, reply_markup=reply_markup)
    
    async def handle_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик админ кнопок"""
        query = update.callback_query
        data = query.data
        
        if data == "admin_panel":
            await self.show_admin_panel(update, context)
        
        elif data == "admin_mirrors":
            await self.show_all_mirrors(update, context)
        
        elif data == "admin_announce":
            await self.ask_for_announcement(update, context)
        
        elif data == "admin_deactivate":
            self.mirror_db.deactivate_inactive_mirrors()
            await query.answer("✅ Неактивные зеркала деактивированы")
            await self.show_admin_panel(update, context)
        
        elif data == "admin_back":
            await self.show_admin_panel(update, context)
    
    async def show_all_mirrors(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать все зеркала"""
        query = update.callback_query
        mirrors = self.mirror_db.get_all_mirrors()
        
        if not mirrors:
            await query.edit_message_text("📭 Нет созданных зеркал")
            return
        
        text = "📋 Все зеркала:\n\n"
        
        for mirror in mirrors:
            mirror_id, user_id, bot_username, created_at, last_activity, is_active = mirror
            users = self.mirror_db.get_mirror_users(mirror_id)
            
            created_date = created_at.split()[0] if isinstance(created_at, str) else created_at.strftime('%Y-%m-%d')
            last_activity_date = last_activity.split()[0] if isinstance(last_activity, str) else last_activity.strftime('%Y-%m-%d')
            
            text += (
                f"🆔 ID: {mirror_id}\n"
                f"👤 Создатель: {user_id}\n"
                f"🤖 Бот: @{bot_username if bot_username else 'неизвестно'}\n"
                f"👥 Пользователей: {len(users)}\n"
                f"📅 Создан: {created_date}\n"
                f"🔄 Активность: {last_activity_date}\n"
                f"📊 Статус: {'✅ Активно' if is_active else '❌ Неактивно'}\n"
                f"――――――――――――――――――――\n"
            )
        
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="admin_back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup)
    
    async def ask_for_announcement(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Запросить текст объявления"""
        query = update.callback_query
        user_id = query.from_user.id
        
        self.user_states[user_id] = "waiting_for_announcement"
        
        text = (
            "📢 Создание объявления\n\n"
            "✍️ Введите текст объявления, которое будет отправлено всем пользователям с зеркалами:\n\n"
            "💡 Объявление будет отправлено немедленно"
        )
        
        keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="admin_back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup)
    
    async def handle_mirrors(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик кнопок управления зеркалами"""
        query = update.callback_query
        data = query.data
        user_id = query.from_user.id
        
        if data == "mirrors_create":
            await self.ask_for_bot_token(update, context)
        
        elif data == "mirrors_view":
            await self.show_user_mirror(update, context)
        
        elif data == "mirrors_access":
            await self.manage_mirror_access(update, context)
        
        elif data == "mirrors_back":
            await self.start(update, context)
    
    async def ask_for_bot_token(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Запросить токен бота для создания зеркала"""
        query = update.callback_query
        user_id = query.from_user.id
        
        self.user_states[user_id] = "waiting_for_bot_token"
        
        text = (
            "🔄 Создание зеркала\n\n"
            "🔑 Для создания зеркала, пожалуйста, создайте бота через @BotFather и отправьте его токен:\n\n"
            "💡 Инструкция:\n"
            "1. Откройте @BotFather в Telegram\n"
            "2. Создайте нового бота с помощью /newbot\n"
            "3. Скопируйте токен (выглядит как: 1234567890:ABCdefGHIjklMNOpqrsTUVwxyz)\n"
            "4. Отправьте токен сюда\n\n"
            "⚠️ Внимание:\n"
            "• 1 пользователь может создать только 1 зеркало\n"
            "• Для спама используйте зеркало, не основной бот\n"
            "• В зеркале не будет кнопки «мои зеркала»"
        )
        
        keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="mirrors_back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup)
    
    async def show_user_mirror(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать информацию о зеркале пользователя"""
        query = update.callback_query
        user_id = query.from_user.id
        
        user_mirror = self.mirror_db.get_user_mirror(user_id)
        
        if not user_mirror:
            text = "❌ У вас нет созданного зеркала"
            keyboard = [[InlineKeyboardButton("🔄 Создать зеркало", callback_data="mirrors_create")]]
        else:
            mirror_id, bot_token, bot_username, created_at, last_activity, is_active = user_mirror
            users = self.mirror_db.get_mirror_users(mirror_id)
            
            text = (
                "📋 Информация о вашем зеркале:\n\n"
                f"🆔 ID зеркала: {mirror_id}\n"
                f"🤖 Имя бота: @{bot_username if bot_username else 'неизвестно'}\n"
                f"📅 Создан: {created_at.split()[0]}\n"
                f"🔄 Последняя активность: {last_activity.split()[0]}\n"
                f"📊 Статус: {'✅ Активно' if is_active else '❌ Неактивно'}\n"
                f"👥 Пользователей с доступом: {len(users)}\n\n"
                "💡 Ссылка на бота:\n"
                f"👉 https://t.me/{bot_username}\n\n"
                "⚠️ Основной бот предназначен для ознакомления. Используйте зеркало для спама!"
            )
            
            keyboard = [
                [InlineKeyboardButton("👥 Управление доступом", callback_data="mirrors_access")],
                [InlineKeyboardButton("🔙 Назад", callback_data="mirrors_back")]
            ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text, reply_markup=reply_markup)
    
    async def manage_mirror_access(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Управление доступом к зеркалу"""
        query = update.callback_query
        user_id = query.from_user.id
        
        user_mirror = self.mirror_db.get_user_mirror(user_id)
        
        if not user_mirror:
            await query.answer("❌ У вас нет зеркала")
            return
        
        mirror_id, _, _, _, _, _ = user_mirror
        users = self.mirror_db.get_mirror_users(mirror_id)
        
        text = "👥 Управление доступом к зеркалу\n\n"
        text += f"📊 Пользователей с доступом: {len(users)}/10\n\n"
        text += "👤 Список пользователей:\n"
        
        for i, user in enumerate(users, 1):
            text += f"{i}. ID: {user}\n"
        
        text += "\n✨ Доступные действия:"
        
        keyboard = []
        
        if len(users) < 10:
            keyboard.append([InlineKeyboardButton("➕ Добавить пользователя", callback_data=f"mirrors_adduser_{mirror_id}")])
        
        if len(users) > 1:  # Есть кто-то кроме создателя
            keyboard.append([InlineKeyboardButton("➖ Удалить пользователя", callback_data=f"mirrors_removeuser_{mirror_id}")])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="mirrors_back")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text, reply_markup=reply_markup)
    
    async def handle_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик кнопок главного меню"""
        query = update.callback_query
        data = query.data
        
        if data == "main_spam":
            await self.show_spam_menu(update, context)
        elif data == "main_back":
            await self.start(update, context)
    
    # Остальные методы (show_spam_menu, handle_spam, etc.) такие же как в оригинальном коде
    # Но с удалением кнопки "Мои зеркала" из интерфейса
    
    async def show_spam_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Меню рассылки"""
        query = update.callback_query
        user_id = query.from_user.id
        
        # Проверяем, есть ли у пользователя зеркало
        user_mirror = self.mirror_db.get_user_mirror(user_id)
        
        if user_mirror:
            # Если есть зеркало, показываем предупреждение
            text = (
                "🚀 Начать спам\n\n"
                "⚠️ Внимание!\n\n"
                "Основной бот предназначен для ознакомления с функционалом. "
                "Пожалуйста, создайте зеркало и рассылайте из него.\n\n"
                "✅ У вас уже есть зеркало. Используйте его для рассылки!"
            )
            
            keyboard = [
                [InlineKeyboardButton("📋 Моё зеркало", callback_data="mirrors_view")],
                [InlineKeyboardButton("🔙 Назад", callback_data="main_back")]
            ]
        else:
            # Используем оригинальный функционал
            db = UserDatabase(user_id)
            chats = db.get_chats()
            
            if not chats:
                text = (
                    "📭 У вас нет добавленных чатов\n\n"
                    "💡 Сначала добавьте пользователей"
                )
                keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="main_back")]]
            else:
                text = (
                    "🚀 Начать рассылку\n\n"
                    "📋 Выберите чат для рассылки:\n\n"
                    "💡 После выбора чата откроется список пользователей с кликабельными ссылками"
                )
                
                keyboard = []
                for chat_id, name in chats:
                    keyboard.append([InlineKeyboardButton(f"👥 {name}", callback_data=f"spam_chat_{chat_id}_0")])
                
                keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="main_back")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text, reply_markup=reply_markup)
    
    async def handle_text_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик текстового ввода"""
        user_id = update.message.from_user.id
        text = update.message.text
        
        if user_id not in self.user_states:
            help_text = "💡 Используйте кнопки меню для навигации\n\n🔍 Если вы потерялись, нажмите /start"
            await update.message.reply_text(help_text)
            return
        
        state = self.user_states[user_id]
        
        if state == "waiting_for_bot_token":
            # Пытаемся создать зеркало с этим токеном
            try:
                # Проверяем токен, создавая временное приложение
                temp_app = Application.builder().token(text).build()
                bot_info = await temp_app.bot.get_me()
                bot_username = bot_info.username
                
                # Добавляем зеркало в базу
                success = self.mirror_db.add_mirror(user_id, text, bot_username)
                
                if success:
                    del self.user_states[user_id]
                    
                    # Запускаем зеркало в отдельном процессе
                    await self.start_mirror_bot(text, user_id)
                    
                    success_text = (
                        f"✅ Зеркало успешно создано!\n\n"
                        f"🤖 Имя бота: @{bot_username}\n"
                        f"🔗 Ссылка: https://t.me/{bot_username}\n\n"
                        f"✨ Теперь вы можете использовать зеркало для рассылки!\n\n"
                        f"💡 Особенности зеркала:\n"
                        f"• Только вы и добавленные пользователи могут им пользоваться\n"
                        f"• Нет кнопки «мои зеркала»\n"
                        f"• Общая база данных с основным ботом\n"
                        f"• Автоматическая деактивация при неактивности"
                    )
                    
                    await update.message.reply_text(success_text)
                    await self.start(update, context)
                else:
                    error_text = (
                        "❌ Не удалось создать зеркало\n\n"
                        "Возможные причины:\n"
                        "• Вы уже создали зеркало ранее\n"
                        "• Этот токен уже используется\n"
                        "• Произошла ошибка в базе данных\n\n"
                        "💡 1 пользователь может создать только 1 зеркало"
                    )
                    await update.message.reply_text(error_text)
                    
            except Exception as e:
                error_text = (
                    "❌ Неверный токен бота\n\n"
                    "Пожалуйста, убедитесь что:\n"
                    "1. Токен скопирован правильно\n"
                    "2. Бот создан через @BotFather\n"
                    "3. Токен имеет правильный формат\n\n"
                    "💡 Пример токена: 1234567890:ABCdefGHIjklMNOpqrsTUVwxyz"
                )
                await update.message.reply_text(error_text)
        
        elif state == "waiting_for_announcement":
            if user_id != ADMIN_ID:
                await update.message.reply_text("⛔ У вас нет прав")
                return
            
            self.mirror_db.add_announcement(user_id, text)
            
            # Отправляем объявление всем пользователям с зеркалами
            mirrors = self.mirror_db.get_all_mirrors()
            sent_count = 0
            
            for mirror in mirrors:
                try:
                    await context.bot.send_message(
                        chat_id=mirror[1],  # user_id создателя
                        text=f"📢 Объявление от администратора:\n\n{text}"
                    )
                    sent_count += 1
                except Exception as e:
                    logger.error(f"Ошибка отправки объявления пользователю {mirror[1]}: {e}")
            
            del self.user_states[user_id]
            
            await update.message.reply_text(f"✅ Объявление отправлено {sent_count} пользователям")
            await self.show_admin_panel(update, context)
        
        else:
            # Оригинальный обработчик текста для спама
            await self.handle_spam_text(update, context, text)
    
    async def handle_spam_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
        """Обработчик текста для спама (упрощенная версия)"""
        user_id = update.message.from_user.id
        
        # Используем оригинальный функционал
        from original_spam_bot import SpamBot  # Импортируем оригинальный класс
        
        # Создаем временный экземпляр для обработки
        # В реальной реализации нужно интегрировать код оригинального бота
        
        help_text = "💡 Используйте кнопки меню для навигации\n\nДля создания сообщений используйте кнопку '📝 Создание сообщений'"
        await update.message.reply_text(help_text)
    
    async def start_mirror_bot(self, bot_token: str, creator_id: int):
        """Запуск зеркального бота в отдельном потоке"""
        import threading
        
        def run_mirror():
            # Импортируем здесь, чтобы избежать циклических импортов
            from mirror_bot import MirrorSpamBot
            mirror_bot = MirrorSpamBot(bot_token, creator_id, self.mirror_db)
            mirror_bot.run()
        
        # Запускаем в отдельном потоке
        thread = threading.Thread(target=run_mirror, daemon=True)
        thread.start()
    
    def run(self):
        """Запуск основного бота"""
        self.application.run_polling()

# МОДУЛЬ ДЛЯ ЗЕРКАЛЬНОГО БОТА (mirror_bot.py)
class MirrorSpamBot:
    """Зеркальный бот для рассылки"""
    
    def __init__(self, token: str, creator_id: int, mirror_db: MirrorDatabase):
        self.token = token
        self.creator_id = creator_id
        self.mirror_db = mirror_db
        self.application = Application.builder().token(token).build()
        self.user_states = {}
        self.setup_handlers()
        
        # Получаем ID зеркала
        self.mirror_info = mirror_db.get_user_mirror(creator_id)
        if self.mirror_info:
            self.mirror_id = self.mirror_info[0]
        else:
            self.mirror_id = None
    
    def setup_handlers(self):
        """Настройка обработчиков для зеркала"""
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CallbackQueryHandler(self.handle_button, pattern="^main_"))
        self.application.add_handler(CallbackQueryHandler(self.handle_messages, pattern="^messages_"))
        self.application.add_handler(CallbackQueryHandler(self.handle_users, pattern="^users_"))
        self.application.add_handler(CallbackQueryHandler(self.handle_spam, pattern="^spam_"))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_input))
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start для зеркала"""
        user_id = update.effective_user.id
        
        # Проверяем доступ пользователя
        if not self.mirror_id or not self.mirror_db.check_user_access(self.mirror_id, user_id):
            await update.message.reply_text(
                "⛔ Пожалуйста, выдайте доступ этому аккаунту в основном боте\n\n"
                "💡 Обратитесь к создателю зеркала для получения доступа"
            )
            return
        
        # Обновляем активность зеркала
        self.mirror_db.update_mirror_activity(self.mirror_id)
        
        welcome_text = (
            f"🪞 Зеркало бота\n\n"
            f"🌟 Добро пожаловать!\n\n"
            f"✨ Этот бот является зеркалом основного бота\n"
            f"💬 Все данные синхронизированы с основным аккаунтом\n\n"
            f"💡 Используйте кнопки ниже для работы:"
        )
        
        keyboard = [
            [InlineKeyboardButton("📝 Создание сообщений", callback_data="main_messages")],
            [InlineKeyboardButton("👥 Мои пользователи", callback_data="main_users")],
            [InlineKeyboardButton("🚀 Начать спам", callback_data="main_spam")]
        ]
        
        # НЕТ КНОПКИ "МОИ ЗЕРКАЛА" - это зеркало!
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(welcome_text, reply_markup=reply_markup)
    
    # Все остальные методы такие же как в оригинальном SpamBot,
    # но используют UserDatabase(self.creator_id) для доступа к данным
    
    async def show_main_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать главное меню (без кнопки зеркал)"""
        query = update.callback_query
        await query.answer()
        
        menu_text = "🎯 Главное меню\n\n💡 Выберите нужный раздел:"
        
        keyboard = [
            [InlineKeyboardButton("📝 Создание сообщений", callback_data="main_messages")],
            [InlineKeyboardButton("👥 Мои пользователи", callback_data="main_users")],
            [InlineKeyboardButton("🚀 Начать спам", callback_data="main_spam")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(menu_text, reply_markup=reply_markup)
    
    # Остальные методы копируются из оригинального SpamBot,
    # но с использованием UserDatabase(self.creator_id)
    
    async def show_messages_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Меню создания сообщений (использует данные создателя)"""
        query = update.callback_query
        user_id = query.from_user.id
        
        # Проверяем доступ
        if not self.mirror_db.check_user_access(self.mirror_id, user_id):
            await query.answer("⛔ Нет доступа")
            return
        
        await query.answer()
        
        menu_text = (
            "📝 Создание сообщений\n\n"
            "✨ Доступные действия:\n"
            "• 📄 Создать новое сообщение с вариациями\n"
            "• 🗑️ Удалить существующее сообщение\n\n"
            "💡 Выберите действие:"
        )
        
        keyboard = [
            [InlineKeyboardButton("📄 Создать новое сообщение", callback_data="messages_create")],
            [InlineKeyboardButton("🗑️ Удалить сообщение", callback_data="messages_delete")],
            [InlineKeyboardButton("🔙 Назад", callback_data="main_back")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(menu_text, reply_markup=reply_markup)
    
    def generate_variations(self, text: str, count: int = 500) -> List[str]:
        """Генерация вариаций сообщения (копия из оригинального кода)"""
        variations = set()
        chars_to_replace = list(REPLACEMENTS.keys())
        
        variations.add(text)
        
        while len(variations) < count:
            variation = list(text)
            changes_made = False
            
            for i, char in enumerate(variation):
                if char in REPLACEMENTS and random.random() < 0.3:
                    variation[i] = REPLACEMENTS[char]
                    changes_made = True
            
            variation_str = ''.join(variation)
            if changes_made and variation_str != text:
                variations.add(variation_str)
            
            if len(variations) >= min(count, 2 ** len([c for c in text if c in chars_to_replace])):
                break
        
        return list(variations)
    
    async def handle_text_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик текстового ввода для зеркала"""
        user_id = update.message.from_user.id
        
        # Проверяем доступ
        if not self.mirror_id or not self.mirror_db.check_user_access(self.mirror_id, user_id):
            await update.message.reply_text(
                "⛔ Пожалуйста, выдайте доступ этому аккаунту в основном боте"
            )
            return
        
        # Обновляем активность
        self.mirror_db.update_mirror_activity(self.mirror_id)
        
        text = update.message.text
        
        if user_id not in self.user_states:
            help_text = "💡 Используйте кнопки меню для навигации\n\n🔍 Если вы потерялись, нажмите /start"
            await update.message.reply_text(help_text)
            return
        
        state = self.user_states[user_id]
        db = UserDatabase(self.creator_id)  # Используем базу создателя!
        
        if state == "waiting_for_message":
            await update.message.reply_text("⏳ Генерирую вариации...")
            
            variations = self.generate_variations(text, 500)
            message_id = db.add_message(text)
            db.add_variations(message_id, variations)
            
            del self.user_states[user_id]
            
            success_text = (
                f"✅ Успешно создано!\n\n"
                f"📊 Создано вариаций: {len(variations)}\n"
                f"💬 Исходное сообщение: {text}\n\n"
                f"💡 Теперь вы можете начать рассылку"
            )
            
            await update.message.reply_text(success_text)
            await self.show_main_menu_from_message(update, context)
        
        elif state == "waiting_for_chat_name":
            context.user_data['current_chat_name'] = text
            self.user_states[user_id] = "waiting_for_users"
            
            users_text = (
                f"🏷️ Название чата сохранено: {text}\n\n"
                f"📝 Отправьте список пользователей в столбик:\n\n"
                f"💡 Каждый username с новой строки"
            )
            
            await update.message.reply_text(users_text)
        
        elif state == "waiting_for_users":
            chat_name = context.user_data.get('current_chat_name')
            usernames = text.split('\n')
            
            cleaned_usernames = []
            for username in usernames:
                cleaned = username.strip().lstrip('@')
                if cleaned:
                    cleaned_usernames.append(cleaned)
            
            if cleaned_usernames:
                chat_id = db.add_chat(chat_name)
                db.add_users(chat_id, cleaned_usernames)
                
                del self.user_states[user_id]
                if 'current_chat_name' in context.user_data:
                    del context.user_data['current_chat_name']
                
                success_text = (
                    f"✅ Пользователи добавлены!\n\n"
                    f"🏷️ Чат: {chat_name}\n"
                    f"👥 Добавлено пользователей: {len(cleaned_usernames)}\n\n"
                    f"💡 Теперь вы можете начать рассылку"
                )
                
                await update.message.reply_text(success_text)
                await self.show_main_menu_from_message(update, context)
            else:
                error_text = (
                    "❌ Список пользователей пуст\n\n"
                    "💡 Отправьте список username'ов в столбик"
                )
                await update.message.reply_text(error_text)
    
    async def show_main_menu_from_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать главное меню из текстового сообщения"""
        menu_text = "🎯 Главное меню\n\n💡 Выберите нужный раздел:"
        
        keyboard = [
            [InlineKeyboardButton("📝 Создание сообщений", callback_data="main_messages")],
            [InlineKeyboardButton("👥 Мои пользователи", callback_data="main_users")],
            [InlineKeyboardButton("🚀 Начать спам", callback_data="main_spam")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(menu_text, reply_markup=reply_markup)
    
    async def handle_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик кнопок главного меню для зеркала"""
        query = update.callback_query
        user_id = query.from_user.id
        
        # Проверяем доступ
        if not self.mirror_db.check_user_access(self.mirror_id, user_id):
            await query.answer("⛔ Нет доступа")
            return
        
        data = query.data
        
        if data == "main_messages":
            await self.show_messages_menu(update, context)
        elif data == "main_users":
            await self.show_users_menu(update, context)
        elif data == "main_spam":
            await self.show_spam_menu(update, context)
        elif data == "main_back":
            await self.show_main_menu(update, context)
    
    # Остальные методы (show_users_menu, show_spam_menu и т.д.)
    # должны быть скопированы из оригинального SpamBot
    # с заменой DatabaseManager(user_id) на UserDatabase(self.creator_id)
    
    async def show_users_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Меню пользователей (использует данные создателя)"""
        query = update.callback_query
        user_id = query.from_user.id
        
        # Проверяем доступ
        if not self.mirror_db.check_user_access(self.mirror_id, user_id):
            await query.answer("⛔ Нет доступа")
            return
        
        await query.answer()
        
        menu_text = (
            "👥 Мои пользователи\n\n"
            "✨ Доступные действия:\n"
            "• ➕ Добавить новых пользователей\n"
            "• 🗑️ Удалить список пользователей\n\n"
            "💡 Выберите действие:"
        )
        
        keyboard = [
            [InlineKeyboardButton("➕ Добавить пользователей", callback_data="users_add")],
            [InlineKeyboardButton("🗑️ Удалить список пользователей", callback_data="users_delete")],
            [InlineKeyboardButton("🔙 Назад", callback_data="main_back")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(menu_text, reply_markup=reply_markup)
    
    async def show_spam_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Меню рассылки (использует данные создателя)"""
        query = update.callback_query
        user_id = query.from_user.id
        
        # Проверяем доступ
        if not self.mirror_db.check_user_access(self.mirror_id, user_id):
            await query.answer("⛔ Нет доступа")
            return
        
        db = UserDatabase(self.creator_id)
        chats = db.get_chats()
        
        if not chats:
            no_chats_text = (
                "📭 У вас нет добавленных чатов\n\n"
                "💡 Сначала добавьте пользователей в разделе \"👥 Мои пользователи\""
            )
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="main_back")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(no_chats_text, reply_markup=reply_markup)
            return
        
        menu_text = (
            "🚀 Начать рассылку\n\n"
            "📋 Выберите чат для рассылки:\n\n"
            "💡 После выбора чата откроется список пользователей с кликабельными ссылками"
        )
        
        keyboard = []
        for chat_id, name in chats:
            keyboard.append([InlineKeyboardButton(f"👥 {name}", callback_data=f"spam_chat_{chat_id}_0")])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="main_back")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(menu_text, reply_markup=reply_markup)
    
    def run(self):
        """Запуск зеркального бота"""
        self.application.run_polling()

# Запуск основного бота
if __name__ == "__main__":
    # Токен вашего основного бота
    MAIN_BOT_TOKEN = "8517379434:AAGqMYBuEQZ8EMNRf3g4yBN-Q0jpm5u5eZU"
    
    # Создаем и запускаем основной бот
    main_bot = MirrorManagerBot(MAIN_BOT_TOKEN)
    print("🤖 Основной бот запущен!")
    print("👑 Админ ID:", ADMIN_ID)
    print("💡 Используйте /start для начала работы")
    main_bot.run()