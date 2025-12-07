import logging
import sqlite3
import random
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

class DatabaseManager:
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

    def get_users_by_chat(self, chat_id: int, offset: int = 0, limit: int = 50) -> List[Tuple[int, str]]:
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

    def get_multiple_variations(self, count: int = 50) -> List[str]:
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

    def get_total_users_in_chat(self, chat_id: int) -> int:
        """Получение общего количества пользователей в чате"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM users WHERE chat_id = ?', (chat_id,))
        count = cursor.fetchone()[0]
        conn.close()
        return count

class SpamBot:
    def __init__(self, token: str):
        self.application = Application.builder().token(token).build()
        self.user_states = {}
        self.setup_handlers()

    def setup_handlers(self):
        """Настройка обработчиков"""
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CallbackQueryHandler(self.handle_button, pattern="^main_"))
        self.application.add_handler(CallbackQueryHandler(self.handle_messages, pattern="^messages_"))
        self.application.add_handler(CallbackQueryHandler(self.handle_users, pattern="^users_"))
        self.application.add_handler(CallbackQueryHandler(self.handle_spam, pattern="^spam_"))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_input))

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        user_id = update.effective_user.id
        
        welcome_text = (
            "🌟 Добро пожаловать! 🌟\n\n"
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
        elif data == "main_back":
            await self.show_main_menu(update, context)

    # РАЗДЕЛ СОЗДАНИЯ СООБЩЕНИЙ
    async def show_messages_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Меню создания сообщений"""
        query = update.callback_query
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
            db = DatabaseManager(user_id)
            db.delete_message(message_id)
            await query.answer("✅ Сообщение и все его вариации удалены!")
            await self.show_messages_menu(update, context)
        
        elif data == "messages_back":
            await self.show_messages_menu(update, context)

    async def show_message_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать список сообщений для удаления"""
        query = update.callback_query
        user_id = query.from_user.id
        db = DatabaseManager(user_id)
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

    # РАЗДЕЛ МОИХ ПОЛЬЗОВАТЕЛЕЙ
    async def show_users_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Меню пользователей"""
        query = update.callback_query
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
            db = DatabaseManager(user_id)
            db.delete_chat(chat_id)
            await query.answer("✅ Чат и все пользователи удалены!")
            await self.show_users_menu(update, context)
        
        elif data == "users_back":
            await self.show_users_menu(update, context)

    async def show_chat_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать список чатов для удаления"""
        query = update.callback_query
        user_id = query.from_user.id
        db = DatabaseManager(user_id)
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

    # РАЗДЕЛ НАЧАТЬ СПАМ - ОБНОВЛЕННАЯ ВЕРСИЯ
    async def show_spam_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Меню рассылки"""
        query = update.callback_query
        user_id = query.from_user.id
        db = DatabaseManager(user_id)
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
        
        # ВАЖНОЕ ПРЕДУПРЕЖДЕНИЕ
        warning_text = (
            "⚠️ ВАЖНОЕ ПРЕДУПРЕЖДЕНИЕ ⚠️\n\n"
            "🚫 НЕ СПАМЬТЕ НАПРЯМУЮ ИЗ БОТА!\n\n"
            "📋 Инструкция по использованию:\n"
            "1. Выберите чат из списка ниже\n"
            "2. Откроется страница с 50 пользователями\n"
            "3. Каждый никнейм содержит скрытую ссылку\n"
            "4. Копируйте никнеймы и отправляйте в избранное\n"
            "5. Из избранного пересылайте нужным пользователям\n\n"
            "🔒 Это обеспечит безопасность вашего аккаунта!\n\n"
            "💡 Выберите чат для рассылки:"
        )
        
        keyboard = []
        for chat_id, name in chats:
            keyboard.append([InlineKeyboardButton(f"👥 {name}", callback_data=f"spam_chat_{chat_id}_0")])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="main_back")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(warning_text, reply_markup=reply_markup)

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
            
            elif data == "spam_warning_close":
                await query.edit_message_reply_markup(reply_markup=None)
                await query.answer("✅ Предупреждение скрыто")
                
        except Exception as e:
            logger.error(f"Ошибка в handle_spam: {e}")
            await query.answer(f"Ошибка: {str(e)}")

    async def show_users_for_spam(self, update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, page: int = 0):
        """Показать 50 пользователей с кликабельными ссылками в никах"""
        query = update.callback_query
        user_id = query.from_user.id
        
        await query.answer()
        
        try:
            db = DatabaseManager(user_id)
            users = db.get_users_by_chat(chat_id, page * 50, 50)  # 50 пользователей на страницу
            
            if not users:
                keyboard = [[InlineKeyboardButton("🔙 Назад к чатам", callback_data="main_spam")]]
                await query.edit_message_text(
                    "✅ Все пользователи обработаны!",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return
            
            chat_name = "Неизвестный чат"
            chats = db.get_chats()
            for cid, name in chats:
                if cid == chat_id:
                    chat_name = name
                    break
            
            # Получаем несколько случайных вариаций для разных пользователей
            variations = db.get_multiple_variations(50)
            
            # Если нет вариаций, показываем сообщение
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
            
            # Формируем сообщение с кликабельными ссылками в никах
            text = f"👥 Чат: {chat_name}\n"
            text += f"📄 Страница: {page + 1}\n"
            
            # Предупреждение на каждой странице
            warning_msg = (
                "\n⚠️ ВНИМАНИЕ:\n"
                "🚫 НЕ ОТПРАВЛЯЙТЕ СООБЩЕНИЯ НАПРЯМУЮ ИЗ ЭТИХ ССЫЛОК!\n"
                "📋 Копируйте никнеймы и отправляйте в избранное,\n"
                "а затем пересылайте из избранного.\n"
                "🔒 Это защитит ваш аккаунт от блокировки!\n\n"
                "🔗 Ссылки скрыты в никнеймах (нажмите для копирования):\n"
            )
            text += warning_msg
            
            # Создаем клавиатуру с кнопками-ссылками
            keyboard = []
            
            for i, (user_id_db, username) in enumerate(users):
                # Берем вариацию для этого пользователя (по кругу если вариаций меньше)
                variation_text = variations[i % len(variations)]
                
                # Создаем ссылку для этого пользователя
                link = f"https://t.me/{username}?text={quote(variation_text)}"
                
                # Отображаем просто никнейм, но при нажатии открывается Telegram с сообщением
                button_text = f"👤 {username}"
                
                keyboard.append([
                    InlineKeyboardButton(
                        text=button_text, 
                        url=link
                    )
                ])
            
            # Получаем общее количество пользователей для навигации
            total_users = db.get_total_users_in_chat(chat_id)
            
            # Кнопки навигации
            nav_buttons = []
            if page > 0:
                nav_buttons.append(InlineKeyboardButton("◀️ Предыдущая", callback_data=f"spam_page_{chat_id}_{page-1}"))
            
            # Показываем текущую страницу и общее количество
            total_pages = (total_users + 49) // 50  # Округляем вверх
            page_info = f"{page + 1}/{total_pages}"
            nav_buttons.append(InlineKeyboardButton(f"📄 {page_info}", callback_data="no_action"))
            
            if (page + 1) * 50 < total_users:
                nav_buttons.append(InlineKeyboardButton("Следующая ▶️", callback_data=f"spam_page_{chat_id}_{page+1}"))
            
            if nav_buttons:
                keyboard.append(nav_buttons)
            
            # Кнопка для обновления вариаций
            keyboard.append([InlineKeyboardButton("🔄 Обновить вариации", callback_data=f"spam_chat_{chat_id}_{page}")])
            
            # Кнопка скрытия предупреждения
            keyboard.append([InlineKeyboardButton("❌ Скрыть предупреждение", callback_data="spam_warning_close")])
            
            # Кнопка назад
            keyboard.append([InlineKeyboardButton("🔙 Назад к чатам", callback_data="main_spam")])
            
            # Добавляем статистику
            stats_text = f"\n📊 Пользователей на странице: {len(users)}"
            stats_text += f"\n📈 Всего пользователей: {total_users}"
            stats_text += f"\n💬 Используются разные вариации текста"
            stats_text += "\n\n💡 Нажимайте на никнеймы для копирования ссылок"
            
            text += stats_text
            
            await query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                disable_web_page_preview=True
            )
            
        except Exception as e:
            error_text = f"❌ Ошибка при загрузке: {str(e)}"
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="main_spam")]]
            await query.edit_message_text(error_text, reply_markup=InlineKeyboardMarkup(keyboard))

    # ОБРАБОТЧИК ТЕКСТОВОГО ВВОДА
    async def handle_text_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик текстового ввода"""
        user_id = update.message.from_user.id
        text = update.message.text
        
        if user_id not in self.user_states:
            help_text = "💡 Используйте кнопки меню для навигации\n\n🔍 Если вы потерялись, нажмите /start"
            await update.message.reply_text(help_text)
            return
        
        state = self.user_states[user_id]
        db = DatabaseManager(user_id)
        
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

    def run(self):
        """Запуск бота"""
        self.application.run_polling()

# Запуск бота
if __name__ == "__main__":
    BOT_TOKEN = "8517379434:AAGqMYBuEQZ8EMNRf3g4yBN-Q0jpm5u5eZU"
    
    bot = SpamBot(BOT_TOKEN)
    print("🤖 Бот запущен и готов к работе!")
    print("💡 Используйте /start в Telegram для начала работы")
    bot.run()