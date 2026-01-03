#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Исправленная версия с правильным логированием
"""

import telebot
from telebot import types
import sqlite3
import csv
import io
import os
import threading
import logging
from datetime import datetime

# ========== CONFIG ==========
BOT_TOKEN = "8276253982:AAGSBdDaVBHCFOmi6-4PGZGvRGnrU8X4JmM"
OWNER_ID = 7504103313
MAIN_SCAM_CHAT_ID = -1002374406940
STAFF_CHAT_ID = -1003235703843
DB_FILE = "scam_full.db"
AUTO_DELETE = False
DELETE_DELAY = 6
# ===========================

# ========== LOGGING SETUP ==========
# Настройка логирования в файл И консоль
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()  # Вывод в консоль
    ]
)
logger = logging.getLogger(__name__)
# ===================================

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ========== DB Setup ==========
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
cur = conn.cursor()

# Создание таблиц (как было)
cur.execute("""
CREATE TABLE IF NOT EXISTS scam_list (
    user_id INTEGER PRIMARY KEY,
    reason TEXT,
    proof_text TEXT,
    comment TEXT,
    added_by INTEGER,
    added_by_name TEXT,
    added_at TEXT
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS scam_photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scam_user_id INTEGER,
    file_id TEXT
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS staff (
    user_id INTEGER PRIMARY KEY,
    role TEXT
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS staff_stats (
    user_id INTEGER PRIMARY KEY,
    messages INTEGER DEFAULT 0,
    adds INTEGER DEFAULT 0
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS actions_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    actor_id INTEGER,
    actor_name TEXT,
    action TEXT
)
""")

conn.commit()
logger.info("Database initialized")

# ========== Helpers ==========
def now_ts():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def log_action(actor_id, actor_name, action):
    """
    Улучшенная функция логирования:
    - Логирует в БД
    - Логирует в файл/консоль
    - Не скрывает ошибки
    """
    ts = now_ts()
    log_message = f"[{ts}] Actor: {actor_name} (ID: {actor_id}) | Action: {action}"
    
    # 1. Логирование в файл/консоль (всегда работает)
    logger.info(log_message)
    
    # 2. Логирование в БД (может упасть, но мы об этом узнаем)
    try:
        cur.execute(
            "INSERT INTO actions_log (ts, actor_id, actor_name, action) VALUES (?, ?, ?, ?)",
            (ts, actor_id, actor_name or str(actor_id), action)
        )
        conn.commit()
    except Exception as e:
        # ❌ РАНЬШЕ: pass (молча игнорировалось)
        # ✅ ТЕПЕРЬ: логируем ошибку, чтобы знать о проблеме
        logger.error(f"Failed to write log to database: {e}", exc_info=True)
        # Можно также отправить уведомление владельцу
        try:
            bot.send_message(OWNER_ID, f"⚠️ Ошибка записи лога в БД: {e}")
        except:
            pass

# ... остальные функции остаются такими же ...

def get_staff_role(user_id):
    cur.execute("SELECT role FROM staff WHERE user_id = ?", (user_id,))
    r = cur.fetchone()
    return r[0] if r else None

def is_owner(user_id):
    return user_id == OWNER_ID

def is_admin_in_staff_chat(user_id):
    try:
        memb = bot.get_chat_member(STAFF_CHAT_ID, user_id)
        return memb.status in ("administrator", "creator")
    except Exception as e:
        logger.warning(f"Error checking admin status for {user_id}: {e}")
        return False

def is_staff(user_id):
    if is_owner(user_id):
        return True
    return get_staff_role(user_id) is not None

def inc_staff_message(user_id):
    try:
        cur.execute("INSERT OR IGNORE INTO staff_stats (user_id, messages, adds) VALUES (?, 0, 0)", (user_id,))
        cur.execute("UPDATE staff_stats SET messages = messages + 1 WHERE user_id = ?", (user_id,))
        conn.commit()
    except Exception as e:
        logger.error(f"Error updating staff stats: {e}")

def inc_staff_add(user_id):
    try:
        cur.execute("INSERT OR IGNORE INTO staff_stats (user_id, messages, adds) VALUES (?, 0, 0)", (user_id,))
        cur.execute("UPDATE staff_stats SET adds = adds + 1 WHERE user_id = ?", (user_id,))
        conn.commit()
    except Exception as e:
        logger.error(f"Error updating staff add stats: {e}")

# ... остальной код ...

# ========== DB operations ==========
def add_scam_db(user_id, reason, proof_text, comment, added_by, added_by_name):
    if scam_exists(user_id):
        return False
    try:
        cur.execute(
            "INSERT INTO scam_list (user_id, reason, proof_text, comment, added_by, added_by_name, added_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, reason, proof_text, comment, added_by, added_by_name, now_ts())
        )
        conn.commit()
        
        # ✅ ЛОГИРОВАНИЕ ДОБАВЛЕНО
        log_action(added_by, added_by_name or str(added_by), f"ADD_SCAM user={user_id} reason={reason[:50]}")
        inc_staff_add(added_by)
        return True
    except Exception as e:
        logger.error(f"Error adding scam to DB: {e}", exc_info=True)
        return False

def remove_scam_db(user_id):
    if not scam_exists(user_id):
        return False
    try:
        cur.execute("DELETE FROM scam_list WHERE user_id = ?", (user_id,))
        cur.execute("DELETE FROM scam_photos WHERE scam_user_id = ?", (user_id,))
        conn.commit()
        
        # ✅ ЛОГИРОВАНИЕ ДОБАВЛЕНО (нужно передавать actor_id)
        log_action(0, "system", f"REMOVE_SCAM user={user_id}")
        return True
    except Exception as e:
        logger.error(f"Error removing scam from DB: {e}", exc_info=True)
        return False

# ... остальные функции ...

# ========== Command handlers ==========

@bot.message_handler(commands=["start"])
def cmd_start(m):
    logger.info(f"Start command from user {m.from_user.id} (@{m.from_user.username})")
    # ... остальной код ...

@bot.message_handler(regexp=r"^\+скам\b", func=lambda m: m.chat.id == MAIN_SCAM_CHAT_ID)
def cmd_plus_scam(m):
    sender = m.from_user
    
    if not is_staff(sender.id):
        bot.reply_to(m, "⛔ Только сотрудники могут добавлять в скам.")
        return
    
    inc_staff_message(sender.id)
    
    # ... код получения target_id ...
    target_id = None  # ваш код здесь
    
    if scam_exists(target_id):
        bot.reply_to(m, f"⚠ {pretty_user(target_id)} уже в базе.")
        return
    
    ok = add_scam_db(target_id, reason, proof_text, comment, sender.id, sender.username or "")
    
    if ok:
        # ✅ ЛОГИРОВАНИЕ УЖЕ ЕСТЬ В add_scam_db, но можно добавить дополнительное
        logger.info(f"Scam added: user_id={target_id} by {sender.id}")
        bot.reply_to(m, f"🛑 {pretty_user(target_id)} добавлен(а) в скам.\nПричина: {reason or '-'}")
    else:
        logger.error(f"Failed to add scam: user_id={target_id}")
        bot.reply_to(m, "Ошибка добавления.")

@bot.message_handler(regexp=r"^\-скам\b", func=lambda m: m.chat.id == MAIN_SCAM_CHAT_ID)
def cmd_minus_scam(m):
    sender = m.from_user
    
    if not is_staff(sender.id):
        bot.reply_to(m, "⛔ Только сотрудники могут удалять записи.")
        return
    
    inc_staff_message(sender.id)
    
    # ... код получения target_id ...
    target_id = None  # ваш код здесь
    
    ok = remove_scam_db(target_id)
    
    if ok:
        # ✅ ДОБАВЛЕНО ЛОГИРОВАНИЕ с реальным actor_id
        log_action(sender.id, sender.username or str(sender.id), f"REMOVE_SCAM user={target_id}")
        logger.info(f"Scam removed: user_id={target_id} by {sender.id}")
        bot.reply_to(m, f"✅ {pretty_user(target_id)} удалён(а) из скам-базы.")
    else:
        logger.warning(f"Scam removal failed: user_id={target_id} (not found?)")
        bot.reply_to(m, f"⚠ {pretty_user(target_id)} не найден(а).")

# ... остальные команды ...

# ========== Проверка логов ==========
@bot.message_handler(commands=["logs", "логи"])
def cmd_logs(m):
    """Команда для просмотра последних логов (только для владельца)"""
    if m.from_user.id != OWNER_ID:
        bot.reply_to(m, "⛔ Только владелец может просматривать логи.")
        return
    
    try:
        cur.execute("SELECT ts, actor_name, action FROM actions_log ORDER BY id DESC LIMIT 20")
        logs = cur.fetchall()
        
        if not logs:
            bot.reply_to(m, "Логи пусты.")
            return
        
        text = "📋 <b>Последние 20 логов:</b>\n\n"
        for ts, actor, action in reversed(logs):
            text += f"<code>{ts}</code> | {actor} | {action}\n"
        
        bot.reply_to(m, text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error reading logs: {e}", exc_info=True)
        bot.reply_to(m, f"Ошибка чтения логов: {e}")

# Запуск бота
if __name__ == "__main__":
    logger.info("Bot starting...")
    try:
        bot.polling(none_stop=True, interval=0)
    except Exception as e:
        logger.critical(f"Bot crashed: {e}", exc_info=True)


