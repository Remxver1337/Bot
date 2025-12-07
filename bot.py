import logging
import sqlite3
import random
import threading
import time
import os
from typing import Dict, List, Tuple
from urllib.parse import quote

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# Настройка логирования
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

# Глобальные словари
running_bots = {}  # token -> thread
bot_threads = {}   # token -> threading.Thread
bot_applications = {}  # token -> Application

class MirrorManager:
    def __init__(self):
        self.db_name = "mirrors.db"
        self.init_database()
    
    def init_database(self):
        """Инициализация базы данных зеркал"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS mirrors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                bot_token TEXT NOT NULL UNIQUE,
                bot_username TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active INTEGER DEFAULT 1
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def create_mirror(self, user_id: int, bot_token: str) -> Tuple[bool, str, int]:
        """Создание нового зеркала"""
        try:
            import requests
            
            # Проверяем токен
            response = requests.get(
                f"https://api.telegram.org/bot{bot_token}/getMe",
                timeout=10
            )
            
            if response.status_code != 200:
                return False, "❌ Ошибка подключения к Telegram API", 0
            
            data = response.json()
            if not data.get("ok"):
                return False, "❌ Неверный токен бота", 0
            
            bot_info = data["result"]
            bot_username = bot_info["username"]
            
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            
            try:
                cursor.execute('''
                    INSERT INTO mirrors (user_id, bot_token, bot_username, created_at) 
                    VALUES (?, ?, ?, datetime('now'))
                ''', (user_id, bot_token, bot_username))
                
                mirror_id = cursor.lastrowid
                conn.commit()
                
                # Запускаем бота в отдельном потоке
                if self.start_bot_thread(bot_token, mirror_id, bot_username):
                    return True, f"✅ Зеркало создано и запущено! Бот: @{bot_username}", mirror_id
                else:
                    return True, f"✅ Зеркало создано, но не удалось запустить. Бот: @{bot_username}", mirror_id
                
            except sqlite3.IntegrityError:
                cursor.execute('SELECT id, bot_username FROM mirrors WHERE bot_token = ?', (bot_token,))
                existing = cursor.fetchone()
                if existing:
                    return False, f"❌ Этот токен уже используется (Бот: @{existing[1]})", existing[0]
                return False, "❌ Ошибка базы данных", 0
            finally:
                conn.close()
                
        except Exception as e:
            logger.error(f"Ошибка создания зеркала: {e}")
            return False, f"❌ Ошибка: {str(e)}", 0
    
    def start_bot_thread(self, token: str, mirror_id: int, username: str) -> bool:
        """Запуск бота в отдельном потоке"""
        try:
            # Проверяем, не запущен ли уже
            if token in running_bots:
                return True
            
            # Создаем и запускаем поток
            thread = threading.Thread(
                target=self.run_mirror_bot,
                args=(token, mirror_id, username),
                daemon=True
            )
            
            bot_threads[token] = thread
            thread.start()
            
            # Ждем немного для инициализации
            time.sleep(2)
            
            if token in running_bots:
                logger.info(f"Бот {mirror_id} (@{username}) запущен в потоке")
                return True
            else:
                logger.error(f"Не удалось запустить бот {mirror_id}")
                return False
                
        except Exception as e:
            logger.error(f"Ошибка запуска потока бота {mirror_id}: {e}")
            return False
    
    def run_mirror_bot(self, token: str, mirror_id: int, username: str):
        """Функция для запуска в потоке"""
        try:
            logger.info(f"Запуск бота {mirror_id} (@{username})...")
            
            # Создаем application
            application = Application.builder().token(token).build()
            
            # Создаем экземпляр бота
            bot = MirrorBot(application, mirror_id, username)
            bot.setup_handlers()
            
            # Сохраняем в глобальный словарь
            running_bots[token] = application
            bot_applications[token] = application
            
            logger.info(f"Бот {mirror_id} (@{username}) готов к запуску polling")
            
            # Запускаем polling
            application.run_polling(allowed_updates=Update.ALL_TYPES)
            
        except Exception as e:
            logger.error(f"Ошибка в потоке бота {mirror_id}: {e}")
            if token in running_bots:
                del running_bots[token]
            if token in bot_threads:
                del bot_threads[token]
    
    def get_user_mirrors(self, user_id: int) -> List[Tuple]:
        """Получение списка зеркал пользователя"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, bot_token, bot_username, created_at, is_active
            FROM mirrors 
            WHERE user_id = ? 
            ORDER BY created_at DESC
        ''', (user_id,))
        
        mirrors = cursor.fetchall()
        conn.close()
        return mirrors
    
    def delete_mirror(self, user_id: int, mirror_id: int) -> bool:
        """Удаление зеркала"""
        try:
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            
            cursor.execute('SELECT bot_token FROM mirrors WHERE id = ? AND user_id = ?', (mirror_id, user_id))
            result = cursor.fetchone()
            
            if not result:
                conn.close()
                return False
            
            token = result[0]
            
            # Останавливаем бота
            self.stop_bot(token)
            
            # Удаляем из базы
            cursor.execute('DELETE FROM mirrors WHERE id = ? AND user_id = ?', (mirror_id, user_id))
            conn.commit()
            conn.close()
            
            logger.info(f"Зеркало {mirror_id} удалено")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка удаления зеркала: {e}")
            return False
    
    def stop_bot(self, token: str):
        """Остановка бота"""
        try:
            if token in running_bots:
                app = running_bots[token]
                app.stop()
                del running_bots[token]
                logger.info(f"Бот с токеном {token[:10]}... остановлен")
            
            if token in bot_threads:
                del bot_threads[token]
                
        except Exception as e:
            logger.error(f"Ошибка остановки бота: {e}")

mirror_manager = MirrorManager()

class DatabaseManager:
    def __init__(self, mirror_id: int):
        self.mirror_id = mirror_id
        self.db_name = f"mirror_{mirror_id}.db"
        self.init_database()

    def init_database(self):
        """Инициализация базы данных для зеркала"""
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
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('INSERT INTO messages (original_text) VALUES (?)', (original_text,))
        message_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return message_id

    def add_variations(self, message_id: int, variations: List[str]):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.executemany(
            'INSERT INTO variations (message_id, variation_text) VALUES (?, ?)',
            [(message_id, variation) for variation in variations]
        )
        conn.commit()
        conn.close()

    def get_messages(self) -> List[Tuple[int, str]]:
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('SELECT id, original_text FROM messages ORDER BY created_at DESC')
        messages = cursor.fetchall()
        conn.close()
        return messages

    def delete_message(self, message_id: int):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM variations WHERE message_id = ?', (message_id,))
        cursor.execute('DELETE FROM messages WHERE id = ?', (message_id,))
        conn.commit()
        conn.close()

    def add_chat(self, chat_name: str) -> int:
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
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.executemany(
            'INSERT OR IGNORE INTO users (chat_id, username) VALUES (?, ?)',
            [(chat_id, username.strip()) for username in usernames]
        )
        conn.commit()
        conn.close()

    def get_chats(self) -> List[Tuple[int, str]]:
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('SELECT id, name FROM chats ORDER BY name')
        chats = cursor.fetchall()
        conn.close()
        return chats

    def delete_chat(self, chat_id: int):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM users WHERE chat_id = ?', (chat_id,))
        cursor.execute('DELETE FROM chats WHERE id = ?', (chat_id,))
        conn.commit()
        conn.close()

    def get_users_by_chat(self, chat_id: int, offset: int = 0, limit: int = 25) -> List[Tuple[int, str]]:
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute(
            'SELECT id, username FROM users WHERE chat_id = ? LIMIT ? OFFSET ?',
            (chat_id, limit, offset)
        )
        users = cursor.fetchall()
        conn.close()
        return users

    def get_multiple_variations(self, count: int = 5) -> List[str]:
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
        
        while len(variations) < count:
            if variations:
                variations.append(random.choice(variations))
            else:
                break
        
        return variations

class MirrorBot:
    def __init__(self, application: Application, mirror_id: int, username: str):
        self.application = application
        self.mirror_id = mirror_id
        self.username = username
        self.user_states = {}
        self.db = DatabaseManager(mirror_id)
    
    def setup_handlers(self):
        """Настройка обработчиков"""
        self.application.add_handler(CommandHandler("start", self.start_handler))
        self.application.add_handler(CallbackQueryHandler(self.handle_button, pattern="^main_"))
        self.application.add_handler(CallbackQueryHandler(self.handle_messages, pattern="^messages_"))
        self.application.add_handler(CallbackQueryHandler(self.handle_users, pattern="^users_"))
        self.application.add_handler(CallbackQueryHandler(self.handle_spam, pattern="^spam_"))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_input))
    
    def generate_variations(self, text: str, count: int = 500) -> List[str]:
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
    
    async def start_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        welcome_text = (
            f"🌟 Добро пожаловать в Mirror Bot! 🌟\n\n"
            f"🤖 Бот: @{self.username}\n"
            f"🆔 ID зеркала: {self.mirror_id}\n\n"
            f"💬 Доступные функции:\n"
            f"📝 Создание сообщений - создайте и управляйте вариациями сообщений\n"
            f"👥 Мои пользователи - добавьте списки пользователей для рассылки\n"
            f"🚀 Начать спам - запустите рассылку сообщений\n\n"
            f"💡 Бот готов к работе! Выберите раздел:"
        )
        
        keyboard = [
            [InlineKeyboardButton("📝 Создание сообщений", callback_data="main_messages")],
            [InlineKeyboardButton("👥 Мои пользователи", callback_data="main_users")],
            [InlineKeyboardButton("🚀 Начать спам", callback_data="main_spam")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(welcome_text, reply_markup=reply_markup)
    
    async def handle_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик кнопок главного меню"""
        query = update.callback_query
        await query.answer()
        data = query.data
        
        if data == "main_messages":
            await self.show_messages_menu(update, context)
        elif data == "main_users":
            await self.show_users_menu(update, context)
        elif data == "main_spam":
            await self.show_spam_menu(update, context)
        elif data == "main_back":
            await self.show_main_menu(update, context)
    
    async def show_main_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать главное меню"""
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
    
    async def show_messages_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Меню создания сообщений"""
        query = update.callback_query
        await query.answer()
        
        messages = self.db.get_messages()
        messages_count = len(messages)
        
        menu_text = (
            f"📝 Создание сообщений\n\n"
            f"📊 Сообщений: {messages_count}\n\n"
            f"✨ Доступные действия:\n"
            f"• 📄 Создать новое сообщение с вариациями\n"
            f"• 🗑️ Удалить существующее сообщение\n\n"
            f"💡 Выберите действие:"
        )
        
        keyboard = [
            [InlineKeyboardButton("📄 Создать новое сообщение", callback_data="messages_create")],
            [InlineKeyboardButton("🗑️ Удалить сообщение", callback_data="messages_delete")],
            [InlineKeyboardButton("🔙 Назад", callback_data="main_back")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(menu_text, reply_markup=reply_markup)
    
    async def handle_messages(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик кнопок раздела сообщений"""
        query = update.callback_query
        data = query.data
        user_id = query.from_user.id
        
        if data == "messages_create":
            self.user_states[user_id] = "waiting_for_message"
            create_text = (
                "🆕 Создание нового сообщения\n\n"
                "📨 Введите исходное сообщение для создания вариаций:\n\n"
                "💡 Бот автоматически создаст вариации"
            )
            await query.edit_message_text(create_text)
        
        elif data == "messages_delete":
            await self.show_message_list(update, context)
        
        elif data.startswith("messages_delete_"):
            message_id = int(data.split("_")[2])
            self.db.delete_message(message_id)
            await query.answer("✅ Сообщение и все его вариации удалены!")
            await self.show_messages_menu(update, context)
        
        elif data == "messages_back":
            await self.show_messages_menu(update, context)
    
    async def show_message_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать список сообщений для удаления"""
        query = update.callback_query
        user_id = query.from_user.id
        messages = self.db.get_messages()
        
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
    
    async def show_users_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Меню пользователей"""
        query = update.callback_query
        await query.answer()
        
        chats = self.db.get_chats()
        chats_count = len(chats)
        total_users = 0
        for chat_id, _ in chats:
            users = self.db.get_users_by_chat(chat_id, 0, 1000)
            total_users += len(users)
        
        menu_text = (
            f"👥 Мои пользователи\n\n"
            f"📊 Статистика:\n"
            f"├ Чатов: {chats_count}\n"
            f"└ Пользователей: {total_users}\n\n"
            f"✨ Доступные действия:\n"
            f"• ➕ Добавить новых пользователей\n"
            f"• 🗑️ Удалить список пользователей\n\n"
            f"💡 Выберите действие:"
        )
        
        keyboard = [
            [InlineKeyboardButton("➕ Добавить пользователей", callback_data="users_add")],
            [InlineKeyboardButton("🗑️ Удалить список пользователей", callback_data="users_delete")],
            [InlineKeyboardButton("🔙 Назад", callback_data="main_back")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(menu_text, reply_markup=reply_markup)
    
    async def handle_users(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик кнопок раздела пользователей"""
        query = update.callback_query
        data = query.data
        user_id = query.from_user.id
        
        if data == "users_add":
            self.user_states[user_id] = "waiting_for_chat_name"
            add_text = (
                "➕ Добавление пользователей\n\n"
                "🏷️ Напишите название чата из которого взяли пользователей:\n\n"
                "💡 Пример: Основной чат, Резервный список"
            )
            await query.edit_message_text(add_text)
        
        elif data == "users_delete":
            await self.show_chat_list(update, context)
        
        elif data.startswith("users_delete_"):
            chat_id = int(data.split("_")[2])
            self.db.delete_chat(chat_id)
            await query.answer("✅ Чат и все пользователи удалены!")
            await self.show_users_menu(update, context)
        
        elif data == "users_back":
            await self.show_users_menu(update, context)
    
    async def show_chat_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать список чатов для удаления"""
        query = update.callback_query
        user_id = query.from_user.id
        chats = self.db.get_chats()
        
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
            users = self.db.get_users_by_chat(chat_id, 0, 1000)
            users_count = len(users)
            keyboard.append([InlineKeyboardButton(f"👥 {name} ({users_count})", callback_data=f"users_delete_{chat_id}")])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="users_back")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(list_text, reply_markup=reply_markup)
    
    async def show_spam_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Меню рассылки"""
        query = update.callback_query
        user_id = query.from_user.id
        chats = self.db.get_chats()
        
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
            users = self.db.get_users_by_chat(chat_id, 0, 1000)
            users_count = len(users)
            keyboard.append([InlineKeyboardButton(f"👥 {name} ({users_count})", callback_data=f"spam_chat_{chat_id}_0")])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="main_back")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(menu_text, reply_markup=reply_markup)
    
    async def handle_spam(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик кнопок раздела рассылки"""
        query = update.callback_query
        data = query.data
        
        try:
            if data.startswith("spam_chat_"):
                parts = data.split("_")
                chat_id = int(parts[2])
                page = int(parts[3])
                await self.show_users_for_spam(update, context, chat_id, page)
            
            elif data.startswith("spam_page_"):
                parts = data.split("_")
                chat_id = int(parts[2])
                page = int(parts[3])
                await self.show_users_for_spam(update, context, chat_id, page)
            
            elif data == "spam_back":
                await self.show_spam_menu(update, context)
                
        except Exception as e:
            logger.error(f"Ошибка в handle_spam: {e}")
            await query.answer(f"Ошибка: {str(e)}")
    
    async def show_users_for_spam(self, update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, page: int = 0):
        """Показать пользователей для рассылки"""
        query = update.callback_query
        user_id = query.from_user.id
        
        await query.answer()
        
        try:
            users = self.db.get_users_by_chat(chat_id, page * 5, 5)
            
            if not users:
                keyboard = [[InlineKeyboardButton("🔙 Назад к чатам", callback_data="main_spam")]]
                await query.edit_message_text(
                    "✅ Все пользователи обработаны!",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return
            
            chat_name = "Неизвестный чат"
            chats = self.db.get_chats()
            for cid, name in chats:
                if cid == chat_id:
                    chat_name = name
                    break
            
            variations = self.db.get_multiple_variations(5)
            
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
            
            text = f"👥 Чат: {chat_name}\n"
            text += f"📄 Страница: {page + 1}\n\n"
            text += "🔗 Нажмите на имя пользователя для отправки:\n\n"
            
            keyboard = []
            
            for i, (user_id_db, username) in enumerate(users):
                variation_text = variations[i % len(variations)]
                link = f"https://t.me/{username}?text={quote(variation_text)}"
                
                keyboard.append([
                    InlineKeyboardButton(
                        text=f"👤 {username}", 
                        url=link
                    )
                ])
            
            total_users = len(self.db.get_users_by_chat(chat_id, 0, 10000))
            
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
            
            text += f"\n📊 Пользователей: {len(users)} из {total_users}"
            text += f"\n💬 Используются разные вариации текста"
            text += "\n\n💡 Нажимайте на имена для отправки сообщений"
            
            await query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                disable_web_page_preview=True
            )
            
        except Exception as e:
            error_text = f"❌ Ошибка при загрузке: {str(e)}"
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="main_spam")]]
            await query.edit_message_text(error_text, reply_markup=InlineKeyboardMarkup(keyboard))
    
    async def handle_text_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик текстового ввода"""
        user_id = update.message.from_user.id
        text = update.message.text
        
        if user_id not in self.user_states:
            help_text = "💡 Используйте кнопки меню для навигации\n\n🔍 Если вы потерялись, нажмите /start"
            await update.message.reply_text(help_text)
            return
        
        state = self.user_states[user_id]
        
        if state == "waiting_for_message":
            await update.message.reply_text("⏳ Генерирую вариации...")
            
            variations = self.generate_variations(text, 500)
            message_id = self.db.add_message(text)
            self.db.add_variations(message_id, variations)
            
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
                chat_id = self.db.add_chat(chat_name)
                self.db.add_users(chat_id, cleaned_usernames)
                
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

class MainBot:
    def __init__(self, token: str):
        self.token = token
        self.application = Application.builder().token(token).build()
        self.user_states = {}
        self.setup_handlers()
    
    def setup_handlers(self):
        """Настройка обработчиков основного бота"""
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("mirror", self.mirror_command))
        self.application.add_handler(CommandHandler("restart", self.restart_mirrors))
        self.application.add_handler(CallbackQueryHandler(self.handle_button, pattern="^main_"))
        self.application.add_handler(CallbackQueryHandler(self.handle_messages, pattern="^messages_"))
        self.application.add_handler(CallbackQueryHandler(self.handle_users, pattern="^users_"))
        self.application.add_handler(CallbackQueryHandler(self.handle_spam, pattern="^spam_"))
        self.application.add_handler(CallbackQueryHandler(self.handle_mirrors, pattern="^mirror_"))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_input))
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        welcome_text = (
            "🌟 Добро пожаловать в MultiBot System! 🌟\n\n"
            "🤖 **Это главный бот-конструктор**\n\n"
            "💬 **Доступные функции:**\n"
            "📝 Создание сообщений - создайте и управляйте вариациями сообщений\n"
            "👥 Мои пользователи - добавьте списки пользователей для рассылки\n"
            "🚀 Начать спам - запустите рассылку сообщений\n"
            "🔄 Мои зеркала - создавайте свои боты с полным функционалом!\n\n"
            "✨ **Все зеркала работают в отдельных потоках!**\n\n"
            "💡 Выберите раздел:"
        )
        
        keyboard = [
            [InlineKeyboardButton("📝 Создание сообщений", callback_data="main_messages")],
            [InlineKeyboardButton("👥 Мои пользователи", callback_data="main_users")],
            [InlineKeyboardButton("🚀 Начать спам", callback_data="main_spam")],
            [InlineKeyboardButton("🔄 Мои зеркала", callback_data="main_mirrors")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(welcome_text, reply_markup=reply_markup)
    
    async def mirror_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда для быстрого создания зеркала"""
        await self.show_mirrors_menu(update, context)
    
    async def restart_mirrors(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Перезапуск всех зеркал"""
        await update.message.reply_text("🔄 Перезапускаю все зеркала...")
        
        mirrors = mirror_manager.get_user_mirrors(update.effective_user.id)
        count = 0
        
        for mirror in mirrors:
            mirror_id = mirror[0]
            token = mirror[1]
            username = mirror[2]
            
            if token not in running_bots:
                if mirror_manager.start_bot_thread(token, mirror_id, username):
                    count += 1
        
        await update.message.reply_text(f"✅ Перезапущено зеркал: {count}")
    
    async def show_mirrors_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Меню управления зеркалами"""
        query = update.callback_query if hasattr(update, 'callback_query') else None
        
        if query:
            await query.answer()
        
        menu_text = (
            "🔄 **Мои зеркала**\n\n"
            "✨ **Создайте свою копию этого бота!**\n\n"
            "🚀 **Все зеркала работают в отдельных потоках:**\n"
            "• Не нужно отдельное VPS\n"
            "• Автоматический запуск\n"
            "• Полный функционал\n\n"
            "💡 **Как создать зеркало:**\n"
            "1. Создайте бота через @BotFather\n"
            "2. Получите токен\n"
            "3. Отправьте токен сюда\n"
            "4. Готово! Бот запустится автоматически\n\n"
            "✅ **Выберите действие:**"
        )
        
        keyboard = [
            [InlineKeyboardButton("➕ Создать зеркало", callback_data="mirror_create")],
            [InlineKeyboardButton("📋 Мои зеркала", callback_data="mirror_list")],
            [InlineKeyboardButton("🔙 Назад", callback_data="main_back")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if query:
            await query.edit_message_text(menu_text, reply_markup=reply_markup)
        else:
            await update.message.reply_text(menu_text, reply_markup=reply_markup)
    
    async def handle_mirrors(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик кнопок раздела зеркал"""
        query = update.callback_query
        data = query.data
        user_id = query.from_user.id
        
        if data == "mirror_create":
            self.user_states[user_id] = "waiting_for_bot_token"
            create_text = (
                "🆕 **Создание зеркала**\n\n"
                "📝 **Отправьте токен вашего бота:**\n\n"
                "💡 **Как получить токен:**\n"
                "1. Напишите @BotFather\n"
                "2. Создайте нового бота /newbot\n"
                "3. Скопируйте токен\n"
                "4. Отправьте его сюда\n\n"
                "⚠️ **Формат токена:** `1234567890:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw`\n\n"
                "✨ **Бот запустится автоматически в отдельном потоке!**"
            )
            await query.edit_message_text(create_text)
        
        elif data == "mirror_list":
            await self.show_mirror_list(update, context)
        
        elif data.startswith("mirror_delete_"):
            mirror_id = int(data.split("_")[2])
            success = mirror_manager.delete_mirror(user_id, mirror_id)
            
            if success:
                await query.answer("✅ Зеркало удалено!")
            else:
                await query.answer("❌ Ошибка удаления зеркала")
            
            await self.show_mirrors_menu(update, context)
        
        elif data == "mirror_back":
            await self.show_mirrors_menu(update, context)
    
    async def show_mirror_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать список зеркал пользователя"""
        query = update.callback_query
        user_id = query.from_user.id
        
        mirrors = mirror_manager.get_user_mirrors(user_id)
        
        if not mirrors:
            no_mirrors_text = (
                "📭 **У вас нет созданных зеркал**\n\n"
                "💡 Создайте первое зеркало для работы"
            )
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="mirror_back")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(no_mirrors_text, reply_markup=reply_markup)
            return
        
        list_text = "📋 **Мои зеркала:**\n\n"
        
        keyboard = []
        for mirror in mirrors:
            mirror_id = mirror[0]
            token = mirror[1]
            username = mirror[2]
            created_at = mirror[3]
            is_active = mirror[4]
            
            # Маскируем токен
            masked_token = token[:10] + "..." + token[-10:] if len(token) > 20 else token
            
            status = "🟢 Активно" if is_active == 1 else "🔴 Неактивно"
            running_status = "🚀 Запущено" if token in running_bots else "⏸️ Не запущено"
            
            date_str = created_at[:10] if isinstance(created_at, str) else str(created_at)[:10]
            
            list_text += f"🆔 **ID:** `{mirror_id}`\n"
            list_text += f"🤖 **Бот:** @{username}\n"
            list_text += f"🔑 **Токен:** `{masked_token}`\n"
            list_text += f"📅 **Создано:** {date_str}\n"
            list_text += f"📊 **Статус:** {status}\n"
            list_text += f"⚙️ **Запуск:** {running_status}\n"
            list_text += f"🔗 **Ссылка:** https://t.me/{username}\n"
            list_text += "─" * 30 + "\n\n"
            
            keyboard.append([InlineKeyboardButton(f"🗑️ Удалить зеркало {mirror_id}", callback_data=f"mirror_delete_{mirror_id}")])
        
        keyboard.append([InlineKeyboardButton("🔄 Обновить", callback_data="mirror_list")])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="mirror_back")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(list_text, reply_markup=reply_markup)
    
    async def handle_text_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик текстового ввода"""
        user_id = update.message.from_user.id
        text = update.message.text
        
        if user_id not in self.user_states:
            return
        
        state = self.user_states[user_id]
        
        if state == "waiting_for_bot_token":
            # Проверяем формат токена
            if ":" not in text or len(text) < 30:
                error_text = (
                    "❌ **Неверный формат токена!**\n\n"
                    "💡 Токен должен выглядеть так:\n"
                    "`1234567890:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw`\n\n"
                    "📝 Попробуйте еще раз:"
                )
                await update.message.reply_text(error_text)
                return
            
            msg = await update.message.reply_text("⏳ Создаю зеркало...")
            
            success, message, mirror_id = mirror_manager.create_mirror(user_id, text)
            
            if success:
                del self.user_states[user_id]
                await msg.edit_text(f"✅ {message}")
                await self.show_mirrors_menu(update, context)
            else:
                await msg.edit_text(message)
    
    async def handle_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик кнопок главного меню"""
        query = update.callback_query
        data = query.data
        
        if data == "main_messages":
            await self.show_messages_menu(update, context)
        elif data == "main_users":
            await self.show_users_menu(update, context)
        elif data == "main_spam":
            await self.show_spam_menu(update, context)
        elif data == "main_mirrors":
            await self.show_mirrors_menu(update, context)
        elif data == "main_back":
            await self.show_main_menu(update, context)
    
    async def show_main_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать главное меню"""
        query = update.callback_query
        await query.answer()
        
        menu_text = "🎯 **Главное меню**\n\n💡 Выберите нужный раздел:"
        
        keyboard = [
            [InlineKeyboardButton("📝 Создание сообщений", callback_data="main_messages")],
            [InlineKeyboardButton("👥 Мои пользователи", callback_data="main_users")],
            [InlineKeyboardButton("🚀 Начать спам", callback_data="main_spam")],
            [InlineKeyboardButton("🔄 Мои зеркала", callback_data="main_mirrors")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(menu_text, reply_markup=reply_markup)
    
    async def show_messages_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Меню создания сообщений"""
        query = update.callback_query
        await query.answer()
        
        menu_text = (
            "📝 **Создание сообщений**\n\n"
            "✨ **Доступные действия:**\n"
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
    
    async def handle_messages(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик кнопок раздела сообщений"""
        query = update.callback_query
        data = query.data
        user_id = query.from_user.id
        
        if data == "messages_create":
            self.user_states[user_id] = "waiting_for_message"
            create_text = (
                "🆕 Создание нового сообщения\n\n"
                "📨 Введите исходное сообщение для создания вариаций:\n\n"
                "💡 Бот автоматически создаст вариации"
            )
            await query.edit_message_text(create_text)
        
        elif data == "messages_delete":
            await self.show_message_list(update, context)
        
        elif data.startswith("messages_delete_"):
            message_id = int(data.split("_")[2])
            db = DatabaseManager(0)
            db.delete_message(message_id)
            await query.answer("✅ Сообщение и все его вариации удалены!")
            await self.show_messages_menu(update, context)
        
        elif data == "messages_back":
            await self.show_messages_menu(update, context)
    
    def run(self):
        """Запуск основного бота"""
        self.application.run_polling()

# ==================== ЗАПУСК СИСТЕМЫ ====================

def start_all_mirrors():
    """Запуск всех зеркал при старте"""
    print("🚀 Запуск всех сохраненных зеркал...")
    
    # Сначала получаем всех пользователей
    conn = sqlite3.connect("mirrors.db")
    cursor = conn.cursor()
    
    cursor.execute('SELECT DISTINCT user_id FROM mirrors WHERE is_active = 1')
    users = cursor.fetchall()
    
    for user_tuple in users:
        user_id = user_tuple[0]
        mirrors = mirror_manager.get_user_mirrors(user_id)
        
        for mirror in mirrors:
            mirror_id = mirror[0]
            token = mirror[1]
            username = mirror[2]
            
            if token not in running_bots:
                print(f"  Запускаю зеркало {mirror_id} (@{username})...")
                mirror_manager.start_bot_thread(token, mirror_id, username)
                time.sleep(1)  # Небольшая задержка между запусками
    
    conn.close()
    
    print(f"✅ Запущено зеркал: {len(running_bots)}")

def main():
    """Основная функция запуска"""
    print("=" * 60)
    print("🤖 MultiBot System - Запуск системы...")
    print("=" * 60)
    
    # Запускаем все сохраненные зеркала
    start_all_mirrors()
    
    # Даем время для запуска потоков
    time.sleep(3)
    
    # Запускаем основной бот
    BOT_TOKEN = "8517379434:AAGqMYBuEQZ8EMNRf3g4yBN-Q0jpm5u5eZU"  # Ваш токен
    
    print(f"🎯 Основной бот запускается с токеном: {BOT_TOKEN[:10]}...")
    print("💡 Используйте /start в Telegram")
    print("=" * 60)
    
    # Запускаем основной бот
    main_bot = MainBot(BOT_TOKEN)
    main_bot.run()

if __name__ == "__main__":
    # Установите зависимости:
    # pip install python-telegram-bot
    
    main()