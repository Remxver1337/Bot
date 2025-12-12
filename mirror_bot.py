import logging
import asyncio
import urllib.parse
import random
import sys
import os
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)
from aiohttp import web
import argparse

from database import Database

# Словарь для замены
CYRILLIC_TO_LATIN = {
    'а': 'a', 'р': 'p', 'с': 'c', 'е': 'e', 'о': 'o', 'у': 'y', 'х': 'x',
    'А': 'A', 'Р': 'P', 'С': 'C', 'Е': 'E', 'О': 'O', 'У': 'Y', 'Х': 'X'
}

class MirrorBot:
    def __init__(self, token, owner_id, domain, port):
        self.token = token
        self.owner_id = owner_id
        self.domain = domain
        self.port = port
        self.webhook_url = f"https://{domain}:{port}/webhook/{token}"
        self.db = Database()
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        if not self.db.check_bot_access(user_id, self.token):
            await update.message.reply_text("❌ Доступ запрещен!")
            return
        
        keyboard = [
            [InlineKeyboardButton("📝 Создать сообщения", callback_data='create_messages')],
            [InlineKeyboardButton("👥 Мои пользователи", callback_data='my_users')],
            [InlineKeyboardButton("🚀 Начать работу", callback_data='start_work')],
        ]
        
        await update.message.reply_text(
            "👋 Добро пожаловать!",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def create_messages(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        base_text = "Привет, тебе нужна скидка на пойзон? Я в пойзон феникс выиграл в гиве..."
        
        for i in range(500):
            message = ''.join(CYRILLIC_TO_LATIN.get(char, char) for char in base_text)
            self.db.save_message(self.token, message)
        
        await query.edit_message_text("✅ 500 сообщений создано!")
    
    async def setup_webhook(self):
        """Настройка вебхука на указанном домене"""
        application = Application.builder().token(self.token).build()
        
        # Регистрируем обработчики
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CallbackQueryHandler(self.button_callback))
        
        # Настраиваем вебхук
        await application.bot.set_webhook(
            url=self.webhook_url,
            drop_pending_updates=True
        )
        
        return application
    
    async def run_webhook(self):
        """Запуск вебхук сервера"""
        application = await self.setup_webhook()
        
        # Создаем aiohttp приложение
        app = web.Application()
        
        async def handle_webhook(request):
            data = await request.json()
            update = Update.de_json(data, application.bot)
            await application.process_update(update)
            return web.Response(text="OK")
        
        app.router.add_post(f'/webhook/{self.token}', handle_webhook)
        
        # Запускаем сервер
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', self.port)
        await site.start()
        
        print(f"✅ Зеркальный бот запущен на {self.webhook_url}")
        await asyncio.Event().wait()
    
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        data = query.data
        
        if data == 'create_messages':
            await self.create_messages(update, context)
        # ... остальные обработчики ...

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--token', required=True)
    parser.add_argument('--owner', required=True)
    parser.add_argument('--domain', required=True)
    parser.add_argument('--port', type=int, required=True)
    
    args = parser.parse_args()
    
    bot = MirrorBot(args.token, int(args.owner), args.domain, args.port)
    asyncio.run(bot.run_webhook())

if __name__ == '__main__':
    main()