import logging
import asyncio
import urllib.parse
import random
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)
import sys
import os

# Добавляем путь для импорта
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database import Database

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

db = Database()

# Словарь для замены кириллических символов на латинские аналоги
# Только указанные в задании буквы: a p c e o y x (и их заглавные версии)
CYRILLIC_TO_LATIN = {
    # строчные буквы
    'а': 'a',    # a
    'р': 'p',    # p
    'с': 'c',    # c
    'е': 'e',    # e
    'о': 'o',    # o
    'у': 'y',    # y
    'х': 'x',    # x
    
    # прописные буквы (для тех же букв в начале предложений)
    'А': 'A',    # A
    'Р': 'P',    # P
    'С': 'C',    # C
    'Е': 'E',    # E
    'О': 'O',    # O
    'У': 'Y',    # Y
    'Х': 'X',    # X
}

class MirrorBot:
    def __init__(self, token, owner_id):
        self.token = token
        self.owner_id = owner_id
        self.bot_info = None
    
    async def check_access(self, user_id):
        """Проверка доступа пользователя к боту"""
        return db.check_bot_access(user_id, self.token)
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        # Проверяем доступ
        if not await self.check_access(user_id):
            await update.message.reply_text(
                "❌ **Доступ запрещен!**\n\n"
                "Пожалуйста, выдайте доступ этому аккаунту в основном боте.\n\n"
                "Обратитесь к владельцу бота для получения доступа.",
                parse_mode='Markdown'
            )
            return
        
        # Обновляем активность бота
        db.update_bot_activity(self.token)
        
        # Проверяем статус бота
        bot_status = db.get_bot_status(self.token)
        if bot_status and bot_status[0] == 0:  # is_enabled = 0
            await update.message.reply_text(
                "❌ **Бот отключен!**\n\n"
                "Этот бот-зеркало был отключен владельцем.\n"
                "Обратитесь к владельцу для включения.",
                parse_mode='Markdown'
            )
            return
        
        keyboard = [
            [InlineKeyboardButton("📝 Создать сообщения", callback_data='create_messages')],
            [InlineKeyboardButton("👥 Мои пользователи", callback_data='my_users')],
            [InlineKeyboardButton("🚀 Начать работу", callback_data='start_work')],
            [InlineKeyboardButton("📊 Статистика", callback_data='stats')],
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "👋 **Добро пожаловать в Mirror Bot!**\n\n"
            f"Владелец: {self.owner_id}\n\n"
            "Выберите действие:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def create_messages(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        # Проверяем доступ
        if not await self.check_access(query.from_user.id):
            await query.edit_message_text("❌ Доступ запрещен!")
            return
        
        await query.edit_message_text(
            "📝 **Генерация сообщений**\n\n"
            "Начинаю генерацию 500 сообщений...\n"
            "Это займет до 2 секунд.",
            parse_mode='Markdown'
        )
        
        # Обновляем активность бота
        db.update_bot_activity(self.token)
        
        # Базовый текст для генерации (точно как в примере)
        base_text = (
            "Привет, тебе нужна скидка на пойзон? Я в пойзон феникс выиграл в гиве (бесплатная доставка и скидка 25% на заказ)\n\n"
            "я бесплатно отдаю если что, в чате бейби мало увидел тебя"
        )
        
        generated_count = 0
        start_time = datetime.now()
        
        for i in range(500):
            # Заменяем кириллические символы на латинские аналоги
            message = ''
            for char in base_text:
                if char in CYRILLIC_TO_LATIN:
                    message += CYRILLIC_TO_LATIN[char]
                else:
                    message += char
            
            # Сохраняем сообщение
            db.save_message(self.token, message)
            generated_count += 1
        
        elapsed = (datetime.now() - start_time).total_seconds()
        
        await query.edit_message_text(
            f"✅ **Генерация завершена!**\n\n"
            f"Создано сообщений: {generated_count}\n"
            f"Время выполнения: {elapsed:.2f} сек.\n\n"
            "Сообщения сохранены в базу данных.",
            parse_mode='Markdown'
        )
    
    async def my_users(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        # Проверяем доступ
        if not await self.check_access(query.from_user.id):
            await query.edit_message_text("❌ Доступ запрещен!")
            return
        
        # Обновляем активность бота
        db.update_bot_activity(self.token)
        
        await query.edit_message_text(
            "👥 **Добавление пользователей**\n\n"
            "Отправьте сначала название чата, а затем список пользователей (до 300).\n\n"
            "**Формат:**\n"
            "НазваниеЧата\n"
            "user1\n"
            "user2\n"
            "user3\n\n"
            "**Пример:**\n"
            "МойЧат\n"
            "@username1\n"
            "@username2\n"
            "username3",
            parse_mode='Markdown'
        )
        
        context.user_data['awaiting_users'] = True
    
    async def handle_users_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        # Проверяем доступ
        if not await self.check_access(user_id):
            await update.message.reply_text("❌ Доступ запрещен!")
            return
        
        if not context.user_data.get('awaiting_users'):
            return
        
        text = update.message.text.strip()
        lines = text.split('\n')
        
        if len(lines) < 2:
            await update.message.reply_text("❌ Неверный формат! Нужно минимум 2 строки.")
            return
        
        chat_name = lines[0].strip()
        usernames = lines[1:]
        
        if len(usernames) > 300:
            await update.message.reply_text("❌ Слишком много пользователей! Максимум 300.")
            return
        
        # Очищаем имена пользователей
        cleaned_usernames = []
        for username in usernames:
            username = username.strip()
            if username.startswith('@'):
                username = username[1:]
            cleaned_usernames.append(username)
        
        # Сохраняем пользователей
        db.add_users_to_bot(self.token, chat_name, cleaned_usernames)
        
        # Обновляем активность бота
        db.update_bot_activity(self.token)
        
        await update.message.reply_text(
            f"✅ **Пользователи сохранены!**\n\n"
            f"Чат: {chat_name}\n"
            f"Количество: {len(cleaned_usernames)}\n\n"
            "Теперь вы можете использовать функцию 'Начать работу'.",
            parse_mode='Markdown'
        )
        
        context.user_data['awaiting_users'] = False
    
    async def start_work(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        # Проверяем доступ
        if not await self.check_access(query.from_user.id):
            await query.edit_message_text("❌ Доступ запрещен!")
            return
        
        # Обновляем активность бота
        db.update_bot_activity(self.token)
        
        # Получаем сообщения
        messages = db.get_bot_messages(self.token)
        
        if not messages:
            await query.edit_message_text(
                "❌ **Сначала создайте сообщения!**\n\n"
                "Используйте функцию 'Создать сообщения' для генерации 500 сообщений.",
                parse_mode='Markdown'
            )
            return
        
        # Получаем пользователей
        users = db.get_bot_users(self.token)
        
        if not users:
            await query.edit_message_text(
                "❌ **Сначала добавьте пользователей!**\n\n"
                "Используйте функцию 'Мои пользователи' для добавления списка пользователей.",
                parse_mode='Markdown'
            )
            return
        
        await self.show_links_page(query, context, page=1)
    
    async def show_links_page(self, query, context, page=1):
        # Получаем пользователей для страницы
        users = db.get_bot_users(self.token, page=page, limit=5)
        messages = db.get_bot_messages(self.token)
        
        if not users or not messages:
            await query.edit_message_text("❌ Нет данных для генерации!")
            return
        
        # Берем случайное сообщение
        message = random.choice(messages)[2]  # message_text находится в индексе 2
        message_encoded = urllib.parse.quote(message)
        
        keyboard = []
        
        for user in users:
            username = user[3]  # username находится в индексе 3
            
            link = f"https://t.me/{username}?text={message_encoded}"
            keyboard.append([
                InlineKeyboardButton(f"👤 @{username}", url=link)
            ])
        
        # Кнопки пагинации
        total_users = db.count_bot_users(self.token)
        total_pages = (total_users + 4) // 5  # Округление вверх
        
        pagination_buttons = []
        
        if page > 1:
            pagination_buttons.append(
                InlineKeyboardButton("◀️ Назад", callback_data=f'page_{page-1}')
            )
        
        pagination_buttons.append(
            InlineKeyboardButton(f"{page}/{total_pages}", callback_data='current')
        )
        
        if page < total_pages:
            pagination_buttons.append(
                InlineKeyboardButton("Вперед ▶️", callback_data=f'page_{page+1}')
            )
        
        if pagination_buttons:
            keyboard.append(pagination_buttons)
        
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Обновляем активность бота
        db.update_bot_activity(self.token)
        
        await query.edit_message_text(
            f"🔗 **Сгенерированные ссылки**\n\n"
            f"Страница {page} из {total_pages}\n"
            f"Всего пользователей: {total_users}\n"
            f"Сообщение выбрано случайно из 500 вариантов\n\n"
            "**Нажмите на кнопку для открытия ссылки:**",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        # Проверяем доступ
        if not await self.check_access(query.from_user.id):
            await query.edit_message_text("❌ Доступ запрещен!")
            return
        
        users_count = db.count_bot_users(self.token)
        messages_count = len(db.get_bot_messages(self.token))
        
        # Обновляем активность бота
        db.update_bot_activity(self.token)
        
        keyboard = [
            [InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')],
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"📊 **Статистика бота**\n\n"
            f"👥 Пользователей в базе: {users_count}\n"
            f"📝 Сообщений сгенерировано: {messages_count}\n"
            f"🤖 Владелец бота: {self.owner_id}\n\n"
            f"📈 **Лимиты:**\n"
            f"• Максимум пользователей: 300\n"
            f"• Максимум сообщений: 500",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        data = query.data
        
        # Проверяем доступ
        if not await self.check_access(query.from_user.id):
            await query.edit_message_text("❌ Доступ запрещен!")
            return
        
        if data == 'create_messages':
            await self.create_messages(update, context)
        elif data == 'my_users':
            await self.my_users(update, context)
        elif data == 'start_work':
            await self.start_work(update, context)
        elif data == 'stats':
            await self.stats(update, context)
        elif data == 'back_to_main':
            await self.start(update, context)
        elif data.startswith('page_'):
            page = int(data.split('_')[1])
            await self.show_links_page(query, context, page)
    
    async def run(self):
        # Создаем Application для этого бота
        application = Application.builder().token(self.token).build()
        
        # Получаем информацию о боте
        self.bot_info = await application.bot.get_me()
        
        # Обработчики команд
        application.add_handler(CommandHandler("start", self.start))
        
        # Обработчики callback
        application.add_handler(CallbackQueryHandler(self.button_callback))
        
        # Обработчики сообщений
        application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND, 
            self.handle_users_input
        ))
        
        # Запускаем бота
        logger.info(f"✅ Зеркальный бот @{self.bot_info.username} запущен!")
        
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        
        # Бесконечный цикл
        await asyncio.Event().wait()

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Запуск зеркального бота')
    parser.add_argument('--token', required=True, help='Токен бота')
    parser.add_argument('--owner', required=True, help='ID владельца')
    
    args = parser.parse_args()
    
    # Создаем и запускаем бота
    bot = MirrorBot(args.token, int(args.owner))
    
    asyncio.run(bot.run())

if __name__ == '__main__':
    main()