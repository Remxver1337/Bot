import sqlite3
import json
import os
from datetime import datetime, timedelta

class Database:
    def __init__(self, db_name='mirror_bots.db'):
        self.conn = sqlite3.connect(db_name, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.init_db()
    
    def init_db(self):
        # Таблица для зеркальных ботов
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS mirror_bots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                bot_token TEXT UNIQUE,
                bot_username TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'active',
                is_enabled BOOLEAN DEFAULT 1
            )
        ''')
        
        # Таблица для доступа к ботам
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS bot_access (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_token TEXT,
                user_id INTEGER,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(bot_token, user_id)
            )
        ''')
        
        # Таблица для пользователей зеркальных ботов
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS bot_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_token TEXT,
                chat_name TEXT,
                username TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица для сообщений
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_token TEXT,
                message_text TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица для рассылки
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS subscribers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE,
                subscribed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        self.conn.commit()
    
    def add_mirror_bot(self, user_id, bot_token, bot_username):
        try:
            self.cursor.execute(
                'SELECT COUNT(*) FROM mirror_bots WHERE user_id = ?',
                (user_id,)
            )
            count = self.cursor.fetchone()[0]
            
            if count >= 1:
                return False, "limit_reached"
            
            self.cursor.execute(
                'INSERT INTO mirror_bots (user_id, bot_token, bot_username) VALUES (?, ?, ?)',
                (user_id, bot_token, bot_username)
            )
            
            self.cursor.execute(
                'INSERT INTO bot_access (bot_token, user_id) VALUES (?, ?)',
                (bot_token, user_id)
            )
            
            self.conn.commit()
            return True, "success"
        except sqlite3.IntegrityError:
            return False, "already_exists"
    
    def get_user_bots(self, user_id):
        self.cursor.execute(
            '''SELECT * FROM mirror_bots 
               WHERE user_id = ? OR id IN (
                   SELECT id FROM mirror_bots WHERE bot_token IN (
                       SELECT bot_token FROM bot_access WHERE user_id = ?
                   )
               )
               ORDER BY created_at DESC''',
            (user_id, user_id)
        )
        return self.cursor.fetchall()
    
    def get_bot_by_token(self, token):
        self.cursor.execute(
            'SELECT * FROM mirror_bots WHERE bot_token = ?',
            (token,)
        )
        return self.cursor.fetchone()
    
    def get_bot_by_username(self, username):
        self.cursor.execute(
            'SELECT * FROM mirror_bots WHERE bot_username = ?',
            (username,)
        )
        return self.cursor.fetchone()
    
    def add_users_to_bot(self, bot_token, chat_name, usernames):
        for username in usernames:
            self.cursor.execute(
                'INSERT INTO bot_users (bot_token, chat_name, username) VALUES (?, ?, ?)',
                (bot_token, chat_name, username.strip())
            )
        self.conn.commit()
    
    def get_bot_users(self, bot_token, page=1, limit=300):
        offset = (page - 1) * limit
        self.cursor.execute(
            'SELECT * FROM bot_users WHERE bot_token = ? LIMIT ? OFFSET ?',
            (bot_token, limit, offset)
        )
        return self.cursor.fetchall()
    
    def count_bot_users(self, bot_token):
        self.cursor.execute(
            'SELECT COUNT(*) FROM bot_users WHERE bot_token = ?',
            (bot_token,)
        )
        return self.cursor.fetchone()[0]
    
    def delete_bot_user(self, user_id, bot_token, username):
        self.cursor.execute(
            'DELETE FROM bot_users WHERE bot_token = ? AND username = ? AND bot_token IN (SELECT bot_token FROM mirror_bots WHERE user_id = ?)',
            (bot_token, username, user_id)
        )
        self.conn.commit()
        return self.cursor.rowcount > 0
    
    def save_message(self, bot_token, message_text):
        self.cursor.execute(
            'INSERT INTO messages (bot_token, message_text) VALUES (?, ?)',
            (bot_token, message_text)
        )
        self.conn.commit()
    
    def get_bot_messages(self, bot_token):
        self.cursor.execute(
            'SELECT * FROM messages WHERE bot_token = ? ORDER BY created_at DESC LIMIT 500',
            (bot_token,)
        )
        return self.cursor.fetchall()
    
    def update_bot_activity(self, bot_token):
        self.cursor.execute(
            'UPDATE mirror_bots SET last_activity = CURRENT_TIMESTAMP WHERE bot_token = ?',
            (bot_token,)
        )
        self.conn.commit()
    
    def check_inactive_bots(self):
        cutoff_date = datetime.now() - timedelta(days=7)
        self.cursor.execute(
            '''UPDATE mirror_bots 
               SET status = 'inactive', is_enabled = 0 
               WHERE last_activity < ? AND status = 'active' AND is_enabled = 1''',
            (cutoff_date,)
        )
        self.conn.commit()
        return self.cursor.rowcount
    
    def toggle_bot_status(self, user_id, bot_token, enable):
        self.cursor.execute(
            '''UPDATE mirror_bots 
               SET is_enabled = ?, status = ?
               WHERE bot_token = ? AND user_id = ?''',
            (1 if enable else 0, 'active' if enable else 'disabled', bot_token, user_id)
        )
        self.conn.commit()
        return self.cursor.rowcount > 0
    
    def get_bot_status(self, bot_token):
        self.cursor.execute(
            'SELECT is_enabled, status FROM mirror_bots WHERE bot_token = ?',
            (bot_token,)
        )
        return self.cursor.fetchone()
    
    def add_bot_access(self, owner_id, bot_token, access_user_id):
        self.cursor.execute(
            'SELECT COUNT(*) FROM mirror_bots WHERE user_id = ? AND bot_token = ?',
            (owner_id, bot_token)
        )
        if self.cursor.fetchone()[0] == 0:
            return False, "not_owner"
        
        self.cursor.execute(
            'SELECT COUNT(*) FROM bot_access WHERE bot_token = ?',
            (bot_token,)
        )
        count = self.cursor.fetchone()[0]
        
        if count >= 10:
            return False, "limit_reached"
        
        try:
            self.cursor.execute(
                'INSERT INTO bot_access (bot_token, user_id) VALUES (?, ?)',
                (bot_token, access_user_id)
            )
            self.conn.commit()
            return True, "success"
        except sqlite3.IntegrityError:
            return False, "already_exists"
    
    def check_bot_access(self, user_id, bot_token):
        self.cursor.execute(
            '''SELECT 1 FROM mirror_bots 
               WHERE bot_token = ? AND (user_id = ? OR bot_token IN (
                   SELECT bot_token FROM bot_access WHERE user_id = ?
               ))''',
            (bot_token, user_id, user_id)
        )
        return self.cursor.fetchone() is not None
    
    def get_bot_access_users(self, bot_token):
        self.cursor.execute(
            '''SELECT user_id FROM bot_access 
               WHERE bot_token = ?''',
            (bot_token,)
        )
        return [row[0] for row in self.cursor.fetchall()]
    
    def remove_bot_access(self, owner_id, bot_token, access_user_id):
        self.cursor.execute(
            '''DELETE FROM bot_access 
               WHERE bot_token = ? AND user_id = ? 
               AND bot_token IN (SELECT bot_token FROM mirror_bots WHERE user_id = ?)''',
            (bot_token, access_user_id, owner_id)
        )
        self.conn.commit()
        return self.cursor.rowcount > 0
    
    def add_subscriber(self, user_id):
        try:
            self.cursor.execute(
                'INSERT INTO subscribers (user_id) VALUES (?)',
                (user_id,)
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
    
    def get_all_subscribers(self):
        self.cursor.execute('SELECT user_id FROM subscribers')
        return [row[0] for row in self.cursor.fetchall()]
    
    def delete_bot(self, user_id, bot_token):
        self.cursor.execute(
            'DELETE FROM mirror_bots WHERE user_id = ? AND bot_token = ?',
            (user_id, bot_token)
        )
        self.cursor.execute(
            'DELETE FROM bot_users WHERE bot_token = ?',
            (bot_token,)
        )
        self.cursor.execute(
            'DELETE FROM messages WHERE bot_token = ?',
            (bot_token,)
        )
        self.cursor.execute(
            'DELETE FROM bot_access WHERE bot_token = ?',
            (bot_token,)
        )
        self.conn.commit()
        return self.cursor.rowcount > 0