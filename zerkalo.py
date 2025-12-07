import sqlite3
import sys

def show_all_bots():
    """Показать всех ботов в системе"""
    conn = sqlite3.connect('user_bots.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT u.user_id, u.bot_token, u.bot_username, u.status, u.created_at, 
               COUNT(DISTINCT m.id) as message_count
        FROM user_bots u
        LEFT JOIN messages m ON u.user_id = m.user_id
        GROUP BY u.user_id, u.bot_token
        ORDER BY u.created_at DESC
    ''')
    
    bots = cursor.fetchall()
    conn.close()
    
    if not bots:
        print("📭 Нет зарегистрированных ботов")
        return
    
    print(f"\n🤖 Всего ботов в системе: {len(bots)}\n")
    print("=" * 80)
    
    for user_id, token, username, status, created_at, msg_count in bots:
        print(f"👤 Пользователь ID: {user_id}")
        print(f"🤖 Бот: @{username}")
        print(f"📊 Сообщений: {msg_count}")
        print(f"🔄 Статус: {'🟢 Активен' if status == 'active' else '🔴 Остановлен'}")
        print(f"📅 Создан: {created_at}")
        print(f"🔑 Токен (первые 20 символов): {token[:20]}...")
        print("-" * 80)

def cleanup_old_bots(days_old=30):
    """Очистка старых неактивных ботов"""
    import datetime
    
    conn = sqlite3.connect('user_bots.db')
    cursor = conn.cursor()
    
    cutoff_date = (datetime.datetime.now() - datetime.timedelta(days=days_old)).strftime('%Y-%m-%d')
    
    cursor.execute('''
        SELECT bot_token, bot_username, created_at 
        FROM user_bots 
        WHERE status = 'stopped' AND date(created_at) < date(?)
    ''', (cutoff_date,))
    
    old_bots = cursor.fetchall()
    
    if not old_bots:
        print("📭 Нет старых неактивных ботов для удаления")
        conn.close()
        return
    
    print(f"\n🗑️ Найдено старых ботов для удаления: {len(old_bots)}\n")
    
    for token, username, created_at in old_bots:
        print(f"Удаляем: @{username} (создан {created_at})")
        cursor.execute('DELETE FROM user_bots WHERE bot_token = ?', (token,))
    
    conn.commit()
    deleted_count = cursor.rowcount
    conn.close()
    
    print(f"\n✅ Удалено ботов: {deleted_count}")

def backup_database():
    """Создание резервной копии базы данных"""
    import shutil
    import datetime
    
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_file = f'backup_user_bots_{timestamp}.db'
    
    try:
        shutil.copy2('user_bots.db', backup_file)
        print(f"✅ Резервная копия создана: {backup_file}")
    except Exception as e:
        print(f"❌ Ошибка создания резервной копии: {e}")

def main():
    """Главное меню управления"""
    while True:
        print("\n" + "=" * 50)
        print("🤖 Управление ботами-зеркалами")
        print("=" * 50)
        print("1. Показать всех ботов")
        print("2. Очистить старых ботов (старше 30 дней)")
        print("3. Создать резервную копию")
        print("4. Выйти")
        
        choice = input("\nВыберите действие (1-4): ").strip()
        
        if choice == "1":
            show_all_bots()
        elif choice == "2":
            days = input("Удалить ботов старше (дней) [30]: ").strip()
            days = int(days) if days.isdigit() else 30
            cleanup_old_bots(days)
        elif choice == "3":
            backup_database()
        elif choice == "4":
            print("👋 До свидания!")
            sys.exit(0)
        else:
            print("❌ Неверный выбор. Попробуйте снова.")

if __name__ == "__main__":
    main()