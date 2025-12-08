import logging
import sqlite3
import random
import threading
from typing import Dict, List, Tuple
from urllib.parse import quote

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# ==================== КОНФИГУРАЦИЯ ====================

# ВАШ ТЕЛЕГРАМ ID (замените на свой)
ADMIN_USER_ID = 123456789  # ← ЗАМЕНИТЕ ЭТО ЧИСЛО НА ВАШ ID!

# Токен основного бота-зеркала (ЗАМЕНИТЕ НА СВОЙ!)
MAIN_BOT_TOKEN = "8517379434:AAGqMYBuEQZ8EMNRf3g4yBN-Q0jpm5u5eZU"

# ==================== НАСТРОЙКА ЛОГИРОВАНИЯ ====================

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Словарь для замены кириллических букв на латинские
REPLACEMENTS = {
    'а': 'a', 'с': 'c', 'о': 'o', 'р': 'p', 'е': 'e', 'х': 'x', 'у': 'y',
    'А': 'A', 'С': 'C', 'О': 'O', 'Р': 'P', 'Е': 'E', 'Х': 'X', 'У': 'Y'
}

# Глобальные переменные
USER_BOTS_DB = "user_bots.db"
mirror_db = None  # Будет инициализирован в основном боте

# ==================== КЛАСС БАЗЫ ДАННЫХ ДЛЯ ЗЕРКАЛА ====================

class MirrorDatabase:
    """База данных для управления пользовательскими ботами"""
    
    def __init__(self):
        self.db_name = USER_BOTS_DB
        self.init_database()
    
    def init_database(self):
        """Инициализация базы данных"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_bots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                bot_token TEXT NOT NULL UNIQUE,
                bot_username TEXT,
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("База данных зеркала инициализирована")
    
    def add_user_bot(self, user_id: int, bot_token: str, bot_username: str = None):
        """Добавление пользовательского бота"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT INTO user_bots (user_id, bot_token, bot_username)
                VALUES (?, ?, ?)
            ''', (user_id, bot_token, bot_username))
            
            conn.commit()
            logger.info(f"Добавлен новый бот для пользователя {user_id}")
            return True
        except sqlite3.IntegrityError:
            logger.warning(f"Бот с токеном уже существует: {bot_token[:10]}...")
            return False
        finally:
            conn.close()
    
    def get_user_bots(self, user_id: int):
        """Получение ботов пользователя"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, bot_token, bot_username, status, created_at
            FROM user_bots 
            WHERE user_id = ?
            ORDER BY created_at DESC
        ''', (user_id,))
        
        bots = cursor.fetchall()
        conn.close()
        return bots
    
    def get_all_bots(self):
        """Получение всех ботов (для админа)"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT user_id, bot_token, bot_username, status, created_at
            FROM user_bots 
            ORDER BY created_at DESC
        ''')
        
        bots = cursor.fetchall()
        conn.close()
        return bots
    
    def update_bot_status(self, bot_token: str, status: str):
        """Обновление статуса бота"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE user_bots 
            SET status = ?
            WHERE bot_token = ?
        ''', (status, bot_token))
        
        conn.commit()
        conn.close()
        logger.info(f"Обновлен статус бота {bot_token[:10]}... на {status}")
    
    def delete_bot(self, bot_token: str):
        """Удаление бота"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('DELETE FROM user_bots WHERE bot_token = ?', (bot_token,))
        
        conn.commit()
        deleted = cursor.rowcount > 0
        conn.close()
        
        if deleted:
            logger.info(f"Удален бот: {bot_token[:10]}...")
        
        return deleted

# ==================== КЛАСС БАЗЫ ДАННЫХ ПОЛЬЗОВАТЕЛЬСКОГО БОТА ====================

class UserDatabase:
    """База данных для каждого пользовательского бота"""
    
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.db_name = f"user_{user_id}.db"
        self.init_database()
    
    def init_database(self):
        """Инициализация базы данных пользователя"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        # Таблица сообщений
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица вариаций
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS variations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER,
                variation_text TEXT NOT NULL,
                send_count INTEGER DEFAULT 0,
                FOREIGN KEY (message_id) REFERENCES messages (id)
            )
        ''')
        
        # Таблица чатов
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS chats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            )
        ''')
        
        # Таблица пользователей
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
            result = cursor.fetchone()
            chat_id = result[0] if result else None
        conn.commit()
        conn.close()
        return chat_id
    
    def add_users(self, chat_id: int, usernames: List[str]):
        """Добавление пользователей в чат"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        for username in usernames:
            cleaned = username.strip().lstrip('@')
            if cleaned:
                cursor.execute(
                    'INSERT OR IGNORE INTO users (chat_id, username) VALUES (?, ?)',
                    (chat_id, cleaned)
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
    
    def get_total_users_in_chat(self, chat_id: int) -> int:
        """Получение общего количества пользователей в чате"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM users WHERE chat_id = ?', (chat_id,))
        count = cursor.fetchone()[0]
        conn.close()
        return count
    
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

# ==================== ФУНКЦИИ ДЛЯ ПОЛЬЗОВАТЕЛЬСКОГО БОТА ====================

def create_user_bot_handlers(application: Application, owner_id: int):
    """Создание обработчиков для пользовательского бота"""
    
    # Создаем базу данных для этого пользователя
    user_db = UserDatabase(owner_id)
    
    # Сохраняем данные в контексте приложения
    application.bot_data['owner_id'] = owner_id
    application.bot_data['db'] = user_db
    application.bot_data['user_states'] = {}
    
    # Функция генерации вариаций
    def generate_variations(text: str, count: int = 500) -> List[str]:
        """Генерация вариаций сообщения"""
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
    
    # Обработчик команды /start
    async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        owner_id = context.application.bot_data.get('owner_id')
        
        # Проверяем, является ли пользователь владельцем
        if user_id != owner_id:
            await update.message.reply_text(
                "🚫 Этот бот принадлежит другому пользователю.\n"
                "Для создания своего бота обратитесь к основному боту-зеркалу."
            )
            return
        
        welcome_text = (
            "🌟 Добро пожаловать в ваш персональный спам-бот! 🌟\n\n"
            "💬 Для начала работы используйте кнопки ниже:\n\n"
            "📝 Создание сообщений - создайте и управляйте вариациями сообщений\n"
            "👥 Мои пользователи - добавьте списки пользователей для рассылки\n"
            "🚀 Начать спам - запустите рассылку сообщений\n\n"
            "💡 Бот готов к работе! Выберите раздел:"
        )
        
        keyboard = [
            [InlineKeyboardButton("📝 Создание сообщений", callback_data="main_messages")],
            [InlineKeyboardButton("👥 Мои пользователи", callback_data="main_users")],
            [InlineKeyboardButton("🚀 Начать спам", callback_data="main_spam")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(welcome_text, reply_markup=reply_markup)
    
    # Обработчик кнопок главного меню
    async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        data = query.data
        
        if data == "main_messages":
            await show_messages_menu(query, context)
        elif data == "main_users":
            await show_users_menu(query, context)
        elif data == "main_spam":
            await show_spam_menu(query, context)
        elif data == "main_back":
            await show_main_menu(query, context)
    
    async def show_main_menu(query, context: ContextTypes.DEFAULT_TYPE):
        """Показать главное меню"""
        menu_text = "🎯 Главное меню\n\n💡 Выберите нужный раздел:"
        
        keyboard = [
            [InlineKeyboardButton("📝 Создание сообщений", callback_data="main_messages")],
            [InlineKeyboardButton("👥 Мои пользователи", callback_data="main_users")],
            [InlineKeyboardButton("🚀 Начать спам", callback_data="main_spam")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(menu_text, reply_markup=reply_markup)
    
    async def show_messages_menu(query, context: ContextTypes.DEFAULT_TYPE):
        """Меню создания сообщений"""
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
    
    # Обработчик кнопок раздела сообщений
    async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        data = query.data
        owner_id = context.application.bot_data.get('owner_id')
        user_states = context.application.bot_data.get('user_states', {})
        
        if data == "messages_create":
            user_states[owner_id] = "waiting_for_message"
            context.application.bot_data['user_states'] = user_states
            
            create_text = (
                "🆕 Создание нового сообщения\n\n"
                "📨 Введите исходное сообщение для создания вариаций:\n\n"
                "💡 Бот автоматически создаст вариации"
            )
            await query.edit_message_text(create_text)
        
        elif data == "messages_delete":
            await show_message_list(query, context)
        
        elif data.startswith("messages_delete_"):
            message_id = int(data.split("_")[2])
            db = context.application.bot_data.get('db')
            if db:
                db.delete_message(message_id)
                await query.answer("✅ Сообщение и все его вариации удалены!", show_alert=True)
                await show_messages_menu(query, context)
        
        elif data == "messages_back":
            await show_messages_menu(query, context)
    
    async def show_message_list(query, context: ContextTypes.DEFAULT_TYPE):
        """Показать список сообщений для удаления"""
        db = context.application.bot_data.get('db')
        if not db:
            await query.edit_message_text("❌ Ошибка: база данных не найдена")
            return
        
        messages = db.get_messages()
        
        if not messages:
            no_messages_text = (
                "📭 У вас нет созданных сообщений\n\n"
                "💡 Создайте первое сообщение для работы"
            )
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="messages_back")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(no_messages_text, reply_markup=reply_markup)
            return
        
        list_text = (
            "🗑️ Удаление сообщений\n\n"
            "📋 Выберите сообщение для удаления:\n\n"
            "⚠️ Внимание: будут удалены ВСЕ вариации этого сообщения"
        )
        
        keyboard = []
        for msg_id, text in messages:
            display_text = text[:50] + "..." if len(text) > 50 else text
            keyboard.append([InlineKeyboardButton(f"📄 {display_text}", callback_data=f"messages_delete_{msg_id}")])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="messages_back")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(list_text, reply_markup=reply_markup)
    
    async def show_users_menu(query, context: ContextTypes.DEFAULT_TYPE):
        """Меню пользователей"""
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
    
    # Обработчик кнопок раздела пользователей
    async def handle_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        data = query.data
        owner_id = context.application.bot_data.get('owner_id')
        user_states = context.application.bot_data.get('user_states', {})
        
        if data == "users_add":
            user_states[owner_id] = "waiting_for_chat_name"
            context.application.bot_data['user_states'] = user_states
            
            add_text = (
                "➕ Добавление пользователей\n\n"
                "🏷️ Напишите название чата из которого взяли пользователей:\n\n"
                "💡 Пример: Основной чат, Резервный список"
            )
            await query.edit_message_text(add_text)
        
        elif data == "users_delete":
            await show_chat_list(query, context)
        
        elif data.startswith("users_delete_"):
            chat_id = int(data.split("_")[2])
            db = context.application.bot_data.get('db')
            if db:
                db.delete_chat(chat_id)
                await query.answer("✅ Чат и все пользователи удалены!", show_alert=True)
                await show_users_menu(query, context)
        
        elif data == "users_back":
            await show_users_menu(query, context)
    
    async def show_chat_list(query, context: ContextTypes.DEFAULT_TYPE):
        """Показать список чатов для удаления"""
        db = context.application.bot_data.get('db')
        if not db:
            await query.edit_message_text("❌ Ошибка: база данных не найдена")
            return
        
        chats = db.get_chats()
        
        if not chats:
            no_chats_text = (
                "📭 У вас нет добавленных чатов\n\n"
                "💡 Добавьте первый чат с пользователями"
            )
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="users_back")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(no_chats_text, reply_markup=reply_markup)
            return
        
        list_text = (
            "🗑️ Удаление чатов\n\n"
            "📋 Выберите чат для удаления:\n\n"
            "⚠️ Внимание: будут удалены ВСЕ пользователи этого чата"
        )
        
        keyboard = []
        for chat_id, name in chats:
            keyboard.append([InlineKeyboardButton(f"👥 {name}", callback_data=f"users_delete_{chat_id}")])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="users_back")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(list_text, reply_markup=reply_markup)
    
    async def show_spam_menu(query, context: ContextTypes.DEFAULT_TYPE):
        """Меню рассылки"""
        db = context.application.bot_data.get('db')
        if not db:
            await query.edit_message_text("❌ Ошибка: база данных не найдена")
            return
        
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
    
    # Обработчик кнопок раздела рассылки
    async def handle_spam(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        data = query.data
        
        try:
            if data.startswith("spam_chat_"):
                parts = data.split("_")
                chat_id = int(parts[2])
                page = int(parts[3])
                await show_users_for_spam(query, context, chat_id, page)
            
            elif data.startswith("spam_page_"):
                parts = data.split("_")
                chat_id = int(parts[2])
                page = int(parts[3])
                await show_users_for_spam(query, context, chat_id, page)
            
            elif data == "spam_back":
                await show_spam_menu(query, context)
                
        except Exception as e:
            logger.error(f"Ошибка в handle_spam: {e}")
            await query.answer(f"Ошибка: {str(e)}")
    
    async def show_users_for_spam(query, context: ContextTypes.DEFAULT_TYPE, chat_id: int, page: int = 0):
        """Показать пользователей для рассылки"""
        db = context.application.bot_data.get('db')
        if not db:
            await query.edit_message_text("❌ Ошибка: база данных не найдена")
            return
        
        users = db.get_users_by_chat(chat_id, page * 5, 5)
        
        if not users:
            keyboard = [[InlineKeyboardButton("🔙 Назад к чатам", callback_data="main_spam")]]
            await query.edit_message_text(
                "✅ Все пользователи обработаны!",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        
        # Получаем информацию о чате
        chats = db.get_chats()
        chat_name = "Неизвестный чат"
        total_users = 0
        
        for cid, name in chats:
            if cid == chat_id:
                chat_name = name
                total_users = db.get_total_users_in_chat(chat_id)
                break
        
        # Получаем вариации сообщений
        variations = db.get_multiple_variations(5)
        
        if not variations:
            keyboard = [
                [InlineKeyboardButton("📝 Создать сообщение", callback_data="main_messages")],
                [InlineKeyboardButton("🔙 Назад к чатам", callback_data="main_spam")]
            ]
            await query.edit_message_text(
                "❌ Нет созданных сообщений!\n\nСначала создайте сообщения в разделе 'Создание сообщений'",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        
        # Формируем сообщение
        text = f"👥 Чат: {chat_name}\n"
        text += f"📄 Страница: {page + 1}\n"
        text += f"📊 Всего пользователей: {total_users}\n\n"
        text += "🔗 Нажмите на имя пользователя для отправки:\n\n"
        
        # Создаем клавиатуру
        keyboard = []
        
        for i, (user_id_db, username) in enumerate(users):
            # Берем вариацию для этого пользователя
            variation_idx = i % len(variations)
            variation_text = variations[variation_idx]
            
            # Создаем ссылку
            link = f"https://t.me/{username}?text={quote(variation_text)}"
            
            keyboard.append([
                InlineKeyboardButton(
                    text=f"👤 {username}", 
                    url=link
                )
            ])
        
        # Кнопки навигации
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("◀️ Пред", callback_data=f"spam_page_{chat_id}_{page-1}"))
        
        nav_buttons.append(InlineKeyboardButton(f"{page + 1}", callback_data="no_action"))
        
        if (page + 1) * 5 < total_users:
            nav_buttons.append(InlineKeyboardButton("След ▶️", callback_data=f"spam_page_{chat_id}_{page+1}"))
        
        if nav_buttons:
            keyboard.append(nav_buttons)
        
        keyboard.append([InlineKeyboardButton("🔄 Новые вариации", callback_data=f"spam_chat_{chat_id}_{page}")])
        keyboard.append([InlineKeyboardButton("🔙 Назад к чатам", callback_data="main_spam")])
        
        text += f"💬 Используются разные вариации текста"
        text += "\n\n💡 Нажимайте на имена для отправки сообщений"
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True
        )
    
    # Обработчик текстовых сообщений
    async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.message.from_user.id
        owner_id = context.application.bot_data.get('owner_id')
        
        # Проверяем владельца
        if user_id != owner_id:
            await update.message.reply_text("🚫 У вас нет доступа к этому боту.")
            return
        
        text = update.message.text
        user_states = context.application.bot_data.get('user_states', {})
        db = context.application.bot_data.get('db')
        
        if not db:
            await update.message.reply_text("❌ Ошибка: база данных не найдена")
            return
        
        if user_id not in user_states:
            help_text = "💡 Используйте кнопки меню для навигации\n\n🔍 Если вы потерялись, нажмите /start"
            await update.message.reply_text(help_text)
            return
        
        state = user_states[user_id]
        
        if state == "waiting_for_message":
            await update.message.reply_text("⏳ Генерирую вариации...")
            
            variations = generate_variations(text, 500)
            message_id = db.add_message(text)
            db.add_variations(message_id, variations)
            
            # Удаляем состояние
            del user_states[user_id]
            context.application.bot_data['user_states'] = user_states
            
            success_text = (
                f"✅ Успешно создано!\n\n"
                f"📊 Создано вариаций: {len(variations)}\n"
                f"💬 Исходное сообщение: {text}\n\n"
                f"💡 Теперь вы можете начать рассылку"
            )
            
            await update.message.reply_text(success_text)
            
            # Показываем главное меню
            menu_text = "🎯 Главное меню\n\n💡 Выберите нужный раздел:"
            
            keyboard = [
                [InlineKeyboardButton("📝 Создание сообщений", callback_data="main_messages")],
                [InlineKeyboardButton("👥 Мои пользователи", callback_data="main_users")],
                [InlineKeyboardButton("🚀 Начать спам", callback_data="main_spam")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(menu_text, reply_markup=reply_markup)
        
        elif state == "waiting_for_chat_name":
            # Сохраняем название чата в контексте
            context.user_data['current_chat_name'] = text
            user_states[user_id] = "waiting_for_users"
            context.application.bot_data['user_states'] = user_states
            
            users_text = (
                f"🏷️ Название чата сохранено: {text}\n\n"
                f"📝 Отправьте список пользователей в столбик:\n\n"
                f"💡 Каждый username с новой строки"
            )
            
            await update.message.reply_text(users_text)
        
        elif state == "waiting_for_users":
            chat_name = context.user_data.get('current_chat_name', 'Неизвестный чат')
            usernames = text.split('\n')
            
            cleaned_usernames = []
            for username in usernames:
                cleaned = username.strip().lstrip('@')
                if cleaned:
                    cleaned_usernames.append(cleaned)
            
            if cleaned_usernames:
                chat_id = db.add_chat(chat_name)
                if chat_id:
                    db.add_users(chat_id, cleaned_usernames)
                    
                    # Удаляем состояние
                    del user_states[user_id]
                    if 'current_chat_name' in context.user_data:
                        del context.user_data['current_chat_name']
                    context.application.bot_data['user_states'] = user_states
                    
                    success_text = (
                        f"✅ Пользователи добавлены!\n\n"
                        f"🏷️ Чат: {chat_name}\n"
                        f"👥 Добавлено пользователей: {len(cleaned_usernames)}\n\n"
                        f"💡 Теперь вы можете начать рассылку"
                    )
                    
                    await update.message.reply_text(success_text)
                    
                    # Показываем главное меню
                    menu_text = "🎯 Главное меню\n\n💡 Выберите нужный раздел:"
                    
                    keyboard = [
                        [InlineKeyboardButton("📝 Создание сообщений", callback_data="main_messages")],
                        [InlineKeyboardButton("👥 Мои пользователи", callback_data="main_users")],
                        [InlineKeyboardButton("🚀 Начать спам", callback_data="main_spam")]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    await update.message.reply_text(menu_text, reply_markup=reply_markup)
    
    # Добавляем обработчики в приложение
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CallbackQueryHandler(handle_button, pattern="^main_"))
    application.add_handler(CallbackQueryHandler(handle_messages, pattern="^messages_"))
    application.add_handler(CallbackQueryHandler(handle_users, pattern="^users_"))
    application.add_handler(CallbackQueryHandler(handle_spam, pattern="^spam_"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_input))

def start_user_bot(token: str, owner_id: int):
    """Запуск пользовательского бота в отдельном потоке"""
    try:
        # Создаем новое приложение для бота
        application = Application.builder().token(token).build()
        
        # Создаем обработчики для этого бота
        create_user_bot_handlers(application, owner_id)
        
        # Запускаем бота в отдельном потоке
        def run_bot():
            try:
                logger.info(f"Запускаем пользовательский бот для владельца {owner_id}")
                application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
            except Exception as e:
                logger.error(f"Ошибка в пользовательском боте: {e}")
        
        # Запускаем в отдельном потоке
        thread = threading.Thread(target=run_bot, daemon=True)
        thread.start()
        
        logger.info(f"Пользовательский бот для {owner_id} запущен в отдельном потоке")
        return thread
        
    except Exception as e:
        logger.error(f"Ошибка запуска пользовательского бота: {e}")
        return None

# ==================== ОСНОВНОЙ БОТ-ЗЕРКАЛО ====================

def main():
    """Главная функция запуска основного бота-зеркала"""
    
    # Инициализируем базу данных
    global mirror_db
    mirror_db = MirrorDatabase()
    
    # Создаем приложение для основного бота
    application = Application.builder().token(MAIN_BOT_TOKEN).build()
    
    # Обработчик команды /start для основного бота
    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        welcome_text = (
            "🤖 **Добро пожаловать в Mirror Bot Creator!** 🤖\n\n"
            "✨ **Создайте своего собственного спам-бота за 3 шага:**\n\n"
            "1️⃣ Создайте бота через @BotFather\n"
            "2️⃣ Получите токен бота (формат: 1234567890:ABCdefGHIjkl...)\n"
            "3️⃣ Используйте команду /create с токеном\n\n"
            "📋 **Доступные команды:**\n"
            "• /create [token] - Создать нового бота\n"
            "• /mybots - Показать моих ботов\n"
            "• /stop [номер] - Остановить бота\n"
            "• /admin - Панель администратора\n\n"
            "💡 **Ваш бот получит все функции оригинального спам-бота:**\n"
            "• 📝 Создание сообщений с вариациями\n"
            "• 👥 Управление списками пользователей\n"
            "• 🚀 Массовая рассылка\n"
            "• 📊 Статистика\n\n"
            "👇 **Чтобы начать, создайте бота в @BotFather и используйте /create**"
        )
        
        await update.message.reply_text(welcome_text, parse_mode='Markdown')
    
    # Обработчик команды /create
    async def create_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        if len(context.args) < 1:
            instruction_text = (
                "📝 **Как создать своего бота:**\n\n"
                "1. Откройте @BotFather в Telegram\n"
                "2. Отправьте команду /newbot\n"
                "3. Выберите имя для бота\n"
                "4. Выберите username для бота (должен заканчиваться на 'bot')\n"
                "5. Получите токен (выглядит примерно так):\n"
                "   1234567890:ABCdefGHIjklMNOpqrsTUVwxyz\n"
                "6. Отправьте команду:\n"
                "   /create 1234567890:ABCdefGHIjklMNOpqrsTUVwxyz\n\n"
                "⚠️ **Внимание:** Никому не передавайте токен вашего бота!"
            )
            await update.message.reply_text(instruction_text, parse_mode='Markdown')
            return
        
        bot_token = context.args[0].strip()
        
        try:
            # Проверяем токен
            test_app = Application.builder().token(bot_token).build()
            await test_app.initialize()
            bot_info = await test_app.bot.get_me()
            bot_username = bot_info.username
            
            # Сохраняем бота в базу данных
            if mirror_db.add_user_bot(user_id, bot_token, bot_username):
                # Запускаем пользовательского бота
                thread = start_user_bot(bot_token, user_id)
                
                if thread:
                    # Сохраняем поток в глобальный словарь
                    user_bot_threads[bot_token] = thread
                    
                    success_text = (
                        f"✅ **Бот успешно создан!**\n\n"
                        f"🤖 **Имя бота:** @{bot_username}\n"
                        f"👤 **Владелец:** Вы\n"
                        f"🔄 **Статус:** Активен\n\n"
                        f"💡 **Что делать дальше:**\n"
                        f"1. Перейдите в @{bot_username}\n"
                        f"2. Нажмите /start\n"
                        f"3. Создайте сообщения\n"
                        f"4. Добавьте пользователей\n"
                        f"5. Начните рассылку!\n\n"
                        f"⚠️ **Не забудьте:**\n"
                        f"• Добавить сообщения в вашем боте\n"
                        f"• Добавить пользователей для рассылки\n"
                        f"• Начать рассылку через раздел 'Начать спам'"
                    )
                    
                    await update.message.reply_text(success_text, parse_mode='Markdown')
                else:
                    await update.message.reply_text("❌ Не удалось запустить бота. Попробуйте позже.")
            else:
                await update.message.reply_text("❌ Этот бот уже зарегистрирован в системе!")
                
            await test_app.shutdown()
                
        except Exception as e:
            logger.error(f"Ошибка создания бота: {e}")
            error_msg = str(e)
            if "Unauthorized" in error_msg:
                await update.message.reply_text("❌ Неверный токен бота. Проверьте токен и попробуйте снова.")
            else:
                await update.message.reply_text(f"❌ Ошибка: {error_msg}")
    
    # Обработчик команды /mybots
    async def my_bots(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        bots = mirror_db.get_user_bots(user_id)
        
        if not bots:
            await update.message.reply_text(
                "📭 **У вас нет созданных ботов.**\n\n"
                "Используйте /create [токен] для создания первого бота.",
                parse_mode='Markdown'
            )
            return
        
        bot_list_text = "🤖 **Мои боты:**\n\n"
        
        for i, (bot_id, token, username, status, created_at) in enumerate(bots, 1):
            bot_status = "🟢 Активен" if status == 'active' else "🔴 Остановлен"
            created_date = created_at.split()[0] if created_at else "Неизвестно"
            
            bot_list_text += f"**{i}. @{username}**\n"
            bot_list_text += f"   Статус: {bot_status}\n"
            bot_list_text += f"   Создан: {created_date}\n"
            bot_list_text += f"   Токен: `{token[:10]}...`\n\n"
        
        bot_list_text += (
            "💡 **Команды для управления:**\n"
            "• /stop [номер] - остановить бота\n"
            "• /create [токен] - добавить нового бота\n\n"
            "⚠️ **Важно:** Токен показан не полностью для безопасности."
        )
        
        await update.message.reply_text(bot_list_text, parse_mode='Markdown')
    
    # Обработчик команды /stop
    async def stop_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        if len(context.args) < 1:
            await update.message.reply_text(
                "❌ Укажите номер бота для остановки.\n\n"
                "**Пример:** /stop 1\n\n"
                "Используйте /mybots чтобы увидеть список ваших ботов.",
                parse_mode='Markdown'
            )
            return
        
        try:
            bot_num = int(context.args[0]) - 1
            
            bots = mirror_db.get_user_bots(user_id)
            
            if bot_num < 0 or bot_num >= len(bots):
                await update.message.reply_text("❌ Неверный номер бота.")
                return
            
            bot_id, bot_token, bot_username, status, created_at = bots[bot_num]
            
            # Обновляем статус в базе данных
            mirror_db.update_bot_status(bot_token, 'stopped')
            
            # Удаляем поток из глобального словаря
            if bot_token in user_bot_threads:
                # Останавливаем поток (в реальности нужно корректно остановить бота)
                del user_bot_threads[bot_token]
            
            await update.message.reply_text(
                f"✅ **Бот @{bot_username} остановлен.**\n\n"
                f"Для повторного запуска используйте /create с тем же токеном.",
                parse_mode='Markdown'
            )
            
        except ValueError:
            await update.message.reply_text("❌ Введите корректный номер.")
        except Exception as e:
            logger.error(f"Ошибка остановки бота: {e}")
            await update.message.reply_text(f"❌ Ошибка: {str(e)}")
    
    # Обработчик команды /admin
    async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        # Проверяем права администратора
        if user_id != ADMIN_USER_ID:
            await update.message.reply_text("🚫 У вас нет доступа к панели администратора.")
            return
        
        # Получаем статистику
        all_bots = mirror_db.get_all_bots()
        
        admin_text = (
            "👑 **Панель администратора**\n\n"
            f"📊 **Статистика:**\n"
            f"• Всего ботов в системе: {len(all_bots)}\n"
            f"• Активных потоков: {len(user_bot_threads)}\n\n"
            "📋 **Последние 10 ботов:**\n"
        )
        
        for i, (owner_id, token, username, status, created_at) in enumerate(all_bots[:10], 1):
            admin_text += f"{i}. @{username} (ID владельца: {owner_id}) - {status}\n"
        
        if len(all_bots) > 10:
            admin_text += f"\n... и еще {len(all_bots) - 10} ботов\n"
        
        await update.message.reply_text(admin_text, parse_mode='Markdown')
    
    # Обработчик текстовых сообщений
    async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text
        
        # Если текст похож на токен бота
        if ':' in text and len(text) > 30:
            # Предлагаем создать бота
            await update.message.reply_text(
                f"🔑 **Обнаружен токен бота!**\n\n"
                f"Хотите создать бота с этим токеном?\n\n"
                f"Используйте команду:\n"
                f"`/create {text}`",
                parse_mode='Markdown'
            )
        else:
            # Показываем помощь
            await update.message.reply_text(
                "💡 **Используйте команды:**\n\n"
                "/start - Начало работы\n"
                "/create [токен] - Создать бота\n"
                "/mybots - Мои боты\n"
                "/stop [номер] - Остановить бота\n"
                "/admin - Панель администратора\n\n"
                "🔗 **Как получить токен:**\n"
                "1. Откройте @BotFather\n"
                "2. Создайте нового бота\n"
                "3. Скопируйте токен\n"
                "4. Используйте /create с токеном",
                parse_mode='Markdown'
            )
    
    # Загружаем и запускаем существующих ботов при старте
    def load_existing_bots():
        all_bots = mirror_db.get_all_bots()
        
        for owner_id, bot_token, bot_username, status, created_at in all_bots:
            if status == 'active':
                logger.info(f"Загружаем бота @{bot_username} для пользователя {owner_id}")
                thread = start_user_bot(bot_token, owner_id)
                if thread:
                    user_bot_threads[bot_token] = thread
                    logger.info(f"Бот @{bot_username} успешно загружен")
                else:
                    logger.error(f"Не удалось загрузить бота @{bot_username}")
    
    # Настраиваем обработчики для основного бота
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("create", create_bot))
    application.add_handler(CommandHandler("mybots", my_bots))
    application.add_handler(CommandHandler("stop", stop_bot))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    # Загружаем существующих ботов
    logger.info("Загружаем существующих ботов...")
    load_existing_bots()
    
    # Запускаем основной бот
    print("=" * 50)
    print("🤖 MIRROR BOT CREATOR ЗАПУЩЕН!")
    print("=" * 50)
    print(f"✅ Загружено ботов: {len(user_bot_threads)}")
    print("💡 Основной бот запущен и готов к работе!")
    print("🔗 Используйте /start в Telegram для начала работы")
    print("=" * 50)
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

# ==================== ЗАПУСК ПРОГРАММЫ ====================

if __name__ == "__main__":
    main()