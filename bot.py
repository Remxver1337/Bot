import logging
import sqlite3
import random
from typing import Dict, List, Tuple, Optional
from urllib.parse import quote
from datetime import datetime, timedelta
import asyncio
import json
import threading
import requests

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

# НАСТРОЙКИ ВАШЕГО ХОСТА - ЗАМЕНИТЕ НА СВОИ
YOUR_HOST = "your-domain.com"  # Ваш домен
YOUR_PORT = 8443               # Порт для вебхуков
YOUR_SSL_CERT = "/path/to/cert.pem"    # Путь к SSL сертификату
YOUR_SSL_KEY = "/path/to/key.pem"      # Путь к SSL ключу

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
                is_running INTEGER DEFAULT 1,
                webhook_url TEXT,
                host_domain TEXT,
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
        
        # Таблица настроек хоста
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS host_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                host_domain TEXT NOT NULL,
                webhook_port INTEGER DEFAULT 8443,
                ssl_cert_path TEXT,
                ssl_key_path TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Добавляем настройки вашего хоста по умолчанию
        cursor.execute('''
            INSERT OR REPLACE INTO host_settings (id, host_domain, webhook_port, ssl_cert_path, ssl_key_path) 
            VALUES (1, ?, ?, ?, ?)
        ''', (YOUR_HOST, YOUR_PORT, YOUR_SSL_CERT, YOUR_SSL_KEY))
        
        conn.commit()
        conn.close()
    
    def add_mirror(self, user_id: int, bot_token: str, bot_username: str = None) -> Tuple[bool, int, str]:
        """Добавление нового зеркала с автоматической регистрацией на хосте"""
        try:
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            
            # Проверяем, не создал ли уже пользователь зеркало
            cursor.execute('SELECT id FROM mirrors WHERE user_id = ?', (user_id,))
            existing = cursor.fetchone()
            if existing:
                conn.close()
                return False, 0, "Вы уже создали зеркало"
            
            # Получаем настройки хоста
            cursor.execute('SELECT host_domain, webhook_port FROM host_settings WHERE id = 1')
            host_settings = cursor.fetchone()
            
            if host_settings:
                host_domain, port = host_settings
                webhook_url = f"https://{host_domain}:{port}/{bot_token}"
            else:
                host_domain, port = YOUR_HOST, YOUR_PORT
                webhook_url = f"https://{host_domain}:{port}/{bot_token}"
            
            cursor.execute('''
                INSERT INTO mirrors (user_id, bot_token, bot_username, created_at, last_activity, 
                                   webhook_url, host_domain, is_running)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, bot_token, bot_username, datetime.now(), datetime.now(), 
                  webhook_url, host_domain, 1))
            
            mirror_id = cursor.lastrowid
            
            # Добавляем создателя как первого пользователя с доступом
            cursor.execute('''
                INSERT INTO mirror_access (mirror_id, allowed_user_id)
                VALUES (?, ?)
            ''', (mirror_id, user_id))
            
            conn.commit()
            conn.close()
            
            # Возвращаем информацию для регистрации на хосте
            return True, mirror_id, webhook_url
            
        except sqlite3.IntegrityError as e:
            return False, 0, f"Ошибка базы данных: {str(e)}"
    
    def get_user_mirror(self, user_id: int) -> Optional[Tuple]:
        """Получение зеркала пользователя"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, bot_token, bot_username, created_at, last_activity, 
                   is_active, is_running, webhook_url, host_domain
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
            UPDATE mirrors SET is_active = 0, is_running = 0
            WHERE last_activity < ? AND is_active = 1
        ''', (week_ago,))
        conn.commit()
        conn.close()
    
    def toggle_mirror_running(self, mirror_id: int, running: bool = None) -> Tuple[bool, Tuple]:
        """Включение/выключение работы зеркала"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        if running is None:
            # Получаем текущее состояние
            cursor.execute('SELECT is_running FROM mirrors WHERE id = ?', (mirror_id,))
            current = cursor.fetchone()
            if current:
                new_state = 0 if current[0] == 1 else 1
            else:
                conn.close()
                return False, ()
        else:
            new_state = 1 if running else 0
        
        cursor.execute('''
            UPDATE mirrors SET is_running = ?, last_activity = ? WHERE id = ?
        ''', (new_state, datetime.now(), mirror_id))
        
        conn.commit()
        
        # Получаем информацию о зеркале
        cursor.execute('''
            SELECT bot_token, user_id, bot_username, webhook_url FROM mirrors WHERE id = ?
        ''', (mirror_id,))
        mirror_info = cursor.fetchone()
        
        conn.close()
        
        return new_state == 1, mirror_info
    
    def get_host_settings(self) -> Tuple:
        """Получение настроек хоста"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('SELECT host_domain, webhook_port, ssl_cert_path, ssl_key_path FROM host_settings WHERE id = 1')
        settings = cursor.fetchone()
        conn.close()
        return settings
    
    def update_host_settings(self, host_domain: str, port: int = None, 
                           ssl_cert: str = None, ssl_key: str = None):
        """Обновление настроек хоста"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        if port is None:
            cursor.execute('SELECT webhook_port FROM host_settings WHERE id = 1')
            port_result = cursor.fetchone()
            port = port_result[0] if port_result else YOUR_PORT
        
        cursor.execute('''
            UPDATE host_settings 
            SET host_domain = ?, webhook_port = ?, 
                ssl_cert_path = ?, ssl_key_path = ?,
                updated_at = ?
            WHERE id = 1
        ''', (host_domain, port, ssl_cert, ssl_key, datetime.now()))
        
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
            SELECT id, user_id, bot_username, created_at, last_activity, 
                   is_active, is_running, host_domain
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
        self.running_mirrors = {}
        self.setup_handlers()
        
        # Запускаем все активные зеркала при старте
        self.start_all_mirrors()
    
    def setup_handlers(self):
        """Настройка обработчиков"""
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("admin", self.admin_command))
        self.application.add_handler(CommandHandler("announce", self.announce_command))
        self.application.add_handler(CommandHandler("host", self.host_command))
        self.application.add_handler(CommandHandler("restart_mirrors", self.restart_mirrors_command))
        self.application.add_handler(CallbackQueryHandler(self.handle_button, pattern="^main_"))
        self.application.add_handler(CallbackQueryHandler(self.handle_mirrors, pattern="^mirrors_"))
        self.application.add_handler(CallbackQueryHandler(self.handle_admin, pattern="^admin_"))
        self.application.add_handler(CallbackQueryHandler(self.handle_messages, pattern="^messages_"))
        self.application.add_handler(CallbackQueryHandler(self.handle_users, pattern="^users_"))
        self.application.add_handler(CallbackQueryHandler(self.handle_spam, pattern="^spam_"))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_input))
    
    def start_all_mirrors(self):
        """Запуск всех активных зеркалов при старте бота"""
        mirrors = self.mirror_db.get_all_mirrors()
        for mirror in mirrors:
            mirror_id, user_id, _, _, _, is_active, is_running, host_domain = mirror
            
            if is_active and is_running:
                # Получаем токен зеркала
                user_mirror = self.mirror_db.get_user_mirror(user_id)
                if user_mirror:
                    _, bot_token, _, _, _, _, _, webhook_url, _ = user_mirror
                    
                    # Запускаем зеркало
                    asyncio.create_task(self.start_mirror_bot(bot_token, user_id, mirror_id))
                    logger.info(f"Автозапуск зеркала {mirror_id} для пользователя {user_id}")
    
    async def restart_mirrors_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда для перезапуска всех зеркал (только админ)"""
        user_id = update.effective_user.id
        
        if user_id != ADMIN_ID:
            await update.message.reply_text("⛔ У вас нет прав доступа к этой команде")
            return
        
        # Останавливаем все запущенные зеркала
        for mirror_id in list(self.running_mirrors.keys()):
            self.stop_mirror_bot(mirror_id)
        
        # Запускаем все активные зеркала заново
        self.start_all_mirrors()
        
        await update.message.reply_text("✅ Все зеркала перезапущены")
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        user_id = update.effective_user.id
        
        # Получаем настройки хоста
        host_settings = self.mirror_db.get_host_settings()
        if host_settings:
            host_domain, port, _, _ = host_settings
        else:
            host_domain, port = YOUR_HOST, YOUR_PORT
        
        # Проверяем, есть ли у пользователя зеркало
        user_mirror = self.mirror_db.get_user_mirror(user_id)
        
        welcome_text = (
            "🌟 Добро пожаловать в основной бот! 🌟\n\n"
            "📱 Этот бот предназначен для создания и управления зеркалами\n\n"
            f"🌐 Все зеркала регистрируются автоматически на хосте:\n"
            f"📍 {host_domain}:{port}\n\n"
        )
        
        if user_mirror:
            mirror_id, bot_token, bot_username, created_at, last_activity, is_active, is_running, webhook_url, host_domain = user_mirror
            status = "✅ Запущено" if is_running else "⏸️ Остановлено"
            welcome_text += (
                "✅ У вас уже есть зеркало!\n"
                f"🤖 Имя бота: @{bot_username if bot_username else 'неизвестно'}\n"
                f"📅 Создан: {created_at.split()[0]}\n"
                f"🔄 Статус: {status}\n"
                f"🌐 Хост: {host_domain}\n\n"
            )
        
        welcome_text += (
            "✨ Доступные функции:\n"
            "• 🔄 Создать новое зеркало (авторегистрация на хосте)\n"
            "• ⚙️ Управление зеркалом (остановка/запуск)\n"
            "• 👥 Управление доступом\n"
            "• 📋 Посмотреть моё зеркало\n"
            "• 📝 Создание сообщений (для ознакомления)\n"
            "• 👥 Добавление пользователей (для ознакомления)\n"
            "• 🚀 Начать спам (для ознакомления)\n\n"
            "💡 Основной бот предназначен для ознакомления с функционалом. "
            "Пожалуйста, создайте зеркало и рассылайте из него"
        )
        
        keyboard = []
        
        if not user_mirror:
            keyboard.append([InlineKeyboardButton("🔄 Создать зеркало", callback_data="mirrors_create")])
        else:
            keyboard.append([InlineKeyboardButton("📋 Моё зеркало", callback_data="mirrors_view")])
            keyboard.append([InlineKeyboardButton("⚙️ Управление зеркалом", callback_data="mirrors_manage")])
            keyboard.append([InlineKeyboardButton("👥 Управление доступом", callback_data="mirrors_access")])
        
        # Кнопки для ознакомления с функционалом
        keyboard.append([InlineKeyboardButton("📝 Создание сообщений", callback_data="main_messages")])
        keyboard.append([InlineKeyboardButton("👥 Мои пользователи", callback_data="main_users")])
        keyboard.append([InlineKeyboardButton("🚀 Начать спам", callback_data="main_spam")])
        
        if user_id == ADMIN_ID:
            keyboard.append([InlineKeyboardButton("⚙️ Админ панель", callback_data="admin_panel")])
            keyboard.append([InlineKeyboardButton("🌐 Настройки хоста", callback_data="admin_host")])
        
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
    
    async def host_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /host (только для админа)"""
        user_id = update.effective_user.id
        
        if user_id != ADMIN_ID:
            await update.message.reply_text("⛔ У вас нет прав доступа к этой команде")
            return
        
        if not context.args:
            # Показать текущие настройки хоста
            settings = self.mirror_db.get_host_settings()
            if settings:
                host_domain, port, ssl_cert, ssl_key = settings
                text = (
                    "🌐 Текущие настройки хоста:\n\n"
                    f"📍 Домен: {host_domain}\n"
                    f"🔌 Порт: {port}\n"
                    f"🔐 SSL сертификат: {'Установлен' if ssl_cert else 'Не установлен'}\n"
                    f"🔑 SSL ключ: {'Установлен' if ssl_key else 'Не установлен'}\n\n"
                    "Все новые зеркала будут регистрироваться на этом хосте\n\n"
                    "Использование: /host <домен> [порт]\n"
                    "Пример: /host myserver.com 8443"
                )
            else:
                text = "Настройки хоста не найдены"
            
            await update.message.reply_text(text)
        else:
            # Обновить настройки хоста
            host_domain = context.args[0]
            port = int(context.args[1]) if len(context.args) > 1 else None
            
            self.mirror_db.update_host_settings(host_domain, port)
            
            await update.message.reply_text(
                f"✅ Настройки хоста обновлены!\n\n"
                f"📍 Новый домен: {host_domain}\n"
                f"🔌 Порт: {port or 'по умолчанию'}\n\n"
                f"Все новые зеркала будут регистрироваться на новом хосте."
            )
    
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
        sent_count = 0
        
        for mirror in mirrors:
            try:
                await context.bot.send_message(
                    chat_id=mirror[1],  # user_id создателя
                    text=f"📢 Объявление от администратора:\n\n{announcement_text}"
                )
                sent_count += 1
            except Exception as e:
                logger.error(f"Ошибка отправки объявления пользователю {mirror[1]}: {e}")
        
        await update.message.reply_text(f"✅ Объявление отправлено {sent_count} пользователям")
    
    async def show_admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать админ панель"""
        query = update.callback_query
        if query:
            await query.answer()
            message = query.message
        else:
            message = update.message
        
        # Получаем настройки хоста
        settings = self.mirror_db.get_host_settings()
        if settings:
            host_domain, port, ssl_cert, ssl_key = settings
        else:
            host_domain, port = YOUR_HOST, YOUR_PORT
        
        # Получаем статистику
        mirrors = self.mirror_db.get_all_mirrors()
        active_mirrors = sum(1 for m in mirrors if m[5] == 1)
        running_mirrors = sum(1 for m in mirrors if m[6] == 1)
        total_users = sum(len(self.mirror_db.get_mirror_users(m[0])) for m in mirrors)
        
        announcements = self.mirror_db.get_recent_announcements(3)
        
        admin_text = (
            "⚙️ Админ панель\n\n"
            f"🌐 Хост: {host_domain}:{port}\n"
            f"📊 Статистика:\n"
            f"• Всего зеркал: {len(mirrors)}\n"
            f"• Активных зеркал: {active_mirrors}\n"
            f"• Запущенных зеркал: {running_mirrors}\n"
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
            [InlineKeyboardButton("🌐 Настройки хоста", callback_data="admin_host")],
            [InlineKeyboardButton("🔄 Перезапустить все зеркала", callback_data="admin_restart_mirrors")],
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
        
        elif data == "admin_host":
            await self.show_host_settings(update, context)
        
        elif data == "admin_restart_mirrors":
            # Перезапускаем все зеркала
            for mirror_id in list(self.running_mirrors.keys()):
                self.stop_mirror_bot(mirror_id)
            
            self.start_all_mirrors()
            await query.answer("✅ Все зеркала перезапущены")
            await self.show_admin_panel(update, context)
        
        elif data == "admin_deactivate":
            self.mirror_db.deactivate_inactive_mirrors()
            await query.answer("✅ Неактивные зеркала деактивированы")
            await self.show_admin_panel(update, context)
        
        elif data == "admin_back":
            await self.show_admin_panel(update, context)
    
    async def show_host_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать настройки хоста"""
        query = update.callback_query
        settings = self.mirror_db.get_host_settings()
        
        if settings:
            host_domain, port, ssl_cert, ssl_key = settings
            
            text = (
                "🌐 Настройки хоста\n\n"
                f"📍 Текущий домен: {host_domain}\n"
                f"🔌 Порт: {port}\n"
                f"🔐 SSL сертификат: {'✅ Установлен' if ssl_cert else '❌ Не установлен'}\n"
                f"🔑 SSL ключ: {'✅ Установлен' if ssl_key else '❌ Не установлен'}\n\n"
                "⚠️ Все новые зеркала будут автоматически регистрироваться на этом хосте\n\n"
                "✨ Доступные действия:"
            )
            
            keyboard = [
                [InlineKeyboardButton("✏️ Изменить домен", callback_data="admin_host_change")],
                [InlineKeyboardButton("🔧 Изменить порт", callback_data="admin_host_port")],
                [InlineKeyboardButton("🔙 Назад в админку", callback_data="admin_back")]
            ]
        else:
            text = "❌ Настройки хоста не найдены"
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="admin_back")]]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text, reply_markup=reply_markup)
    
    async def show_all_mirrors(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать все зеркала"""
        query = update.callback_query
        mirrors = self.mirror_db.get_all_mirrors()
        
        if not mirrors:
            await query.edit_message_text("📭 Нет созданных зеркал")
            return
        
        # Получаем настройки хоста
        settings = self.mirror_db.get_host_settings()
        host_domain = settings[0] if settings else YOUR_HOST
        
        text = f"📋 Все зеркала (хост: {host_domain}):\n\n"
        
        for mirror in mirrors:
            mirror_id, user_id, bot_username, created_at, last_activity, is_active, is_running, mirror_host = mirror
            users = self.mirror_db.get_mirror_users(mirror_id)
            
            created_date = created_at.split()[0] if isinstance(created_at, str) else created_at.strftime('%Y-%m-%d')
            last_activity_date = last_activity.split()[0] if isinstance(last_activity, str) else last_activity.strftime('%Y-%m-%d')
            
            status = "✅" if is_running else "⏸️"
            active_status = "🟢" if is_active else "🔴"
            
            text += (
                f"{status} {active_status} ID: {mirror_id}\n"
                f"👤 Создатель: {user_id}\n"
                f"🤖 Бот: @{bot_username if bot_username else 'неизвестно'}\n"
                f"👥 Пользователей: {len(users)}\n"
                f"🌐 Хост: {mirror_host}\n"
                f"📅 Создан: {created_date}\n"
                f"🔄 Активность: {last_activity_date}\n"
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
        
        elif data == "mirrors_manage":
            await self.manage_user_mirror(update, context)
        
        elif data == "mirrors_access":
            await self.manage_mirror_access(update, context)
        
        elif data == "mirrors_back":
            await self.start(update, context)
        
        elif data.startswith("mirrors_toggle_"):
            mirror_id = int(data.split("_")[2])
            await self.toggle_mirror_running(update, context, mirror_id)
    
    async def ask_for_bot_token(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Запросить токен бота для создания зеркала"""
        query = update.callback_query
        user_id = query.from_user.id
        
        # Получаем настройки хоста
        settings = self.mirror_db.get_host_settings()
        if settings:
            host_domain, port, _, _ = settings
        else:
            host_domain, port = YOUR_HOST, YOUR_PORT
        
        self.user_states[user_id] = "waiting_for_bot_token"
        
        text = (
            f"🔄 Создание зеркала\n\n"
            f"🌐 Зеркало будет автоматически зарегистрировано на хосте:\n"
            f"📍 {host_domain}:{port}\n\n"
            f"🔑 Для создания зеркала, пожалуйста, создайте бота через @BotFather и отправьте его токен:\n\n"
            f"💡 Инструкция:\n"
            f"1. Откройте @BotFather в Telegram\n"
            f"2. Создайте нового бота с помощью /newbot\n"
            f"3. Скопируйте токен (выглядит как: 1234567890:ABCdefGHIjklMNOpqrsTUVwxyz)\n"
            f"4. Отправьте токен сюда\n\n"
            f"✅ После отправки токена:\n"
            f"• Зеркало автоматически запустится на вашем хосте\n"
            f"• Вы получите ссылку на бота\n"
            f"• Можете добавлять пользователей\n\n"
            f"⚠️ Внимание:\n"
            f"• 1 пользователь может создать только 1 зеркало\n"
            f"• Для спама используйте зеркало, не основной бот\n"
            f"• В зеркале не будет кнопки «мои зеркала»"
        )
        
        keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="mirrors_back")]]
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
                
                # Добавляем зеркало в базу с автоматической регистрацией на хосте
                success, mirror_id, webhook_url = self.mirror_db.add_mirror(user_id, text, bot_username)
                
                if success:
                    del self.user_states[user_id]
                    
                    # Получаем настройки хоста
                    settings = self.mirror_db.get_host_settings()
                    host_domain = settings[0] if settings else YOUR_HOST
                    
                    # Запускаем зеркало на хосте
                    await self.start_mirror_bot(text, user_id, mirror_id)
                    
                    success_text = (
                        f"✅ Зеркало успешно создано и зарегистрировано!\n\n"
                        f"🤖 Имя бота: @{bot_username}\n"
                        f"🔗 Ссылка на бота: https://t.me/{bot_username}\n"
                        f"🌐 Хост регистрации: {host_domain}\n"
                        f"🔗 Webhook URL: {webhook_url}\n\n"
                        f"✨ Зеркало автоматически запущено на вашем сервере!\n\n"
                        f"💡 Теперь вы можете:\n"
                        f"1. Использовать зеркало для рассылки\n"
                        f"2. Добавить до 10 пользователей\n"
                        f"3. Остановить/запустить зеркало при необходимости\n\n"
                        f"⚠️ Основной бот предназначен для ознакомления. Используйте зеркало для спама!"
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
            # Обработчик для ознакомительного функционала
            await self.handle_demo_text(update, context, text, state)
    
    # Остальные методы (show_user_mirror, manage_user_mirror, toggle_mirror_running, 
    # handle_demo_text, generate_variations, и т.д.) остаются такими же как в предыдущей версии
    
    async def start_mirror_bot(self, bot_token: str, creator_id: int, mirror_id: int):
        """Запуск зеркального бота на вашем хосте"""
        try:
            # Получаем настройки хоста
            settings = self.mirror_db.get_host_settings()
            if settings:
                host_domain, port, ssl_cert, ssl_key = settings
            else:
                host_domain, port, ssl_cert, ssl_key = YOUR_HOST, YOUR_PORT, YOUR_SSL_CERT, YOUR_SSL_KEY
            
            # Импортируем здесь, чтобы избежать циклических импортов
            from mirror_bot import MirrorSpamBot
            
            # Создаем зеркального бота
            mirror_bot = MirrorSpamBot(
                bot_token=bot_token,
                creator_id=creator_id,
                mirror_id=mirror_id,
                mirror_db=self.mirror_db,
                host_domain=host_domain,
                webhook_port=port
            )
            
            # Сохраняем ссылку на запущенное зеркало
            self.running_mirrors[mirror_id] = mirror_bot
            
            # Запускаем бота в отдельном потоке
            import threading
            
            def run_mirror():
                try:
                    # Запускаем с вебхуком
                    mirror_bot.run_webhook(
                        host=host_domain,
                        port=port,
                        ssl_cert=ssl_cert,
                        ssl_key=ssl_key
                    )
                except Exception as e:
                    logger.error(f"Ошибка запуска зеркала {mirror_id}: {e}")
                    # Если вебхук не работает, запускаем polling
                    mirror_bot.run_polling()
            
            thread = threading.Thread(target=run_mirror, daemon=True)
            thread.start()
            
            logger.info(f"Зеркало {mirror_id} запущено на хосте {host_domain}:{port}")
            
        except Exception as e:
            logger.error(f"Ошибка запуска зеркала {mirror_id}: {e}")
    
    def stop_mirror_bot(self, mirror_id: int):
        """Остановка зеркального бота"""
        if mirror_id in self.running_mirrors:
            try:
                mirror_bot = self.running_mirrors[mirror_id]
                # Здесь должен быть метод остановки
                # В текущей реализации просто удаляем из словаря
                del self.running_mirrors[mirror_id]
                logger.info(f"Зеркало {mirror_id} остановлено")
            except Exception as e:
                logger.error(f"Ошибка остановки зеркала {mirror_id}: {e}")
    
    def run(self):
        """Запуск основного бота"""
        # Запускаем проверку неактивных зеркал каждые 24 часа
        async def check_inactive_mirrors():
            while True:
                await asyncio.sleep(24 * 60 * 60)  # 24 часа
                self.mirror_db.deactivate_inactive_mirrors()
                logger.info("Проверка неактивных зеркал выполнена")
        
        # Запускаем в фоне
        asyncio.create_task(check_inactive_mirrors())
        
        # Получаем настройки хоста
        settings = self.mirror_db.get_host_settings()
        if settings:
            host_domain, port, _, _ = settings
        else:
            host_domain, port = YOUR_HOST, YOUR_PORT
        
        print("=" * 50)
        print("🤖 Основной бот запущен!")
        print(f"👑 Админ ID: {ADMIN_ID}")
        print(f"🌐 Хост для зеркал: {host_domain}:{port}")
        print(f"🔌 Все зеркала регистрируются автоматически на этом хосте")
        print("=" * 50)
        print("💡 Команды:")
        print("  /start - начать работу")
        print("  /admin - админ панель (только для админа)")
        print("  /announce <текст> - отправить объявление всем")
        print("  /host <домен> [порт] - изменить хост для новых зеркал")
        print("  /restart_mirrors - перезапустить все зеркала")
        print("=" * 50)
        
        self.application.run_polling()

# ЗАПУСК ОСНОВНОГО БОТА
if __name__ == "__main__":
    # Токен вашего основного бота
    MAIN_BOT_TOKEN = "8517379434:AAGqMYBuEQZ8EMNRf3g4yBN-Q0jpm5u5eZU"
    
    # Создаем и запускаем основной бот
    main_bot = MirrorManagerBot(MAIN_BOT_TOKEN)
    main_bot.run()