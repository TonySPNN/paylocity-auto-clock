import sqlite3
import os
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any

from supabase_db import (
    get_supabase_connection,
    AsyncpgConnectionWrapper,
    AsyncpgCursorWrapper,
    convert_sqlite_to_postgres,
    Row,
    DATABASE_URL,
    DIRECT_URL
)

logger = logging.getLogger("database")
DB_PATH = os.path.join(os.path.dirname(__file__), "data.db")
USE_SUPABASE = os.getenv("USE_SUPABASE", "1") == "1"

def get_db():
    if USE_SUPABASE:
        try:
            return get_supabase_connection()
        except Exception as e:
            logger.error(f"Failed to connect to Supabase, falling back to SQLite: {e}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def _migrate_from_sqlite(cursor):
    if not os.path.exists(DB_PATH):
        return
    try:
        sq_conn = sqlite3.connect(DB_PATH)
        sq_conn.row_factory = sqlite3.Row
        sq_cur = sq_conn.cursor()

        # Migrate settings
        sq_cur.execute("SELECT key, value FROM settings")
        for r in sq_cur.fetchall():
            cursor.execute("""
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """, (r["key"], r["value"]))

        # Migrate schedules if empty
        row = cursor.execute("SELECT COUNT(*) FROM schedules").fetchone()
        sched_count = row[0] if row else 0
        if sched_count == 0:
            sq_cur.execute("SELECT title, action, schedule_type, target_date, days_of_week, target_time, is_enabled, created_at FROM schedules")
            for r in sq_cur.fetchall():
                cursor.execute("""
                    INSERT INTO schedules (title, action, schedule_type, target_date, days_of_week, target_time, is_enabled, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (r["title"], r["action"], r["schedule_type"], r["target_date"], r["days_of_week"], r["target_time"], r["is_enabled"], r["created_at"]))

        # Migrate history if empty
        row = cursor.execute("SELECT COUNT(*) FROM history").fetchone()
        hist_count = row[0] if row else 0
        if hist_count == 0:
            sq_cur.execute("SELECT action, status, message, screenshot_path, executed_at FROM history")
            for r in sq_cur.fetchall():
                cursor.execute("""
                    INSERT INTO history (action, status, message, screenshot_path, executed_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (r["action"], r["status"], r["message"], r["screenshot_path"], r["executed_at"]))

        sq_conn.close()
    except Exception as e:
        logger.warning(f"Error migrating SQLite data: {e}")

def init_db():
    with get_db() as conn:

        cursor = conn.cursor()
        
        # 1. Schedules Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                action TEXT NOT NULL,           -- 'clock_in' or 'clock_out'
                schedule_type TEXT NOT NULL,    -- 'date' (specific date) or 'recurring' (weekly)
                target_date TEXT,               -- YYYY-MM-DD
                days_of_week TEXT,              -- e.g. '0,1,2,3,4' (0=Mon, 6=Sun)
                target_time TEXT NOT NULL,      -- HH:MM (24-hour format)
                is_enabled INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
            )
        """)
        
        # 2. History Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                status TEXT NOT NULL,           -- 'success', 'failed', 'running'
                message TEXT,
                screenshot_path TEXT,
                executed_at TEXT NOT NULL
            )
        """)
        
        # 3. Settings Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        
        # 4. LINE Chats Table (Rooms / Users)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS line_chats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_type TEXT NOT NULL,       -- 'user', 'group', 'room'
                source_id TEXT UNIQUE NOT NULL,  -- userId, groupId, or roomId
                display_name TEXT,
                picture_url TEXT,
                status_message TEXT,
                last_message TEXT,
                last_message_at TEXT,
                latest_reply_token TEXT,
                latest_reply_time TEXT,
                unread_count INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)

        # Migration: ensure latest_reply_token exists in existing databases
        try:
            cursor.execute("ALTER TABLE line_chats ADD COLUMN latest_reply_token TEXT")
        except (sqlite3.OperationalError, Exception):
            pass
        try:
            cursor.execute("ALTER TABLE line_chats ADD COLUMN latest_reply_time TEXT")
        except (sqlite3.OperationalError, Exception):
            pass

        # 5. LINE Messages History Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS line_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,           -- references source_id
                sender_type TEXT NOT NULL,       -- 'user', 'admin', 'bot'
                sender_id TEXT,
                message_type TEXT NOT NULL,      -- 'text', 'image', 'sticker', 'system'
                content TEXT,
                raw_data TEXT,
                reply_token TEXT,
                timestamp TEXT NOT NULL
            )
        """)

        # 6. LINE Webhook Raw Logs Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS line_webhook_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_types TEXT,
                source_id TEXT,
                payload TEXT NOT NULL,
                status TEXT,                     -- 'success', 'verified', 'error'
                received_at TEXT NOT NULL
            )
        """)
        
        # Default Settings
        default_settings = {
            "paylocity_url": "https://access.paylocity.com/",
            "company_id": "",
            "username": "",
            "password": "",
            "phone_number": "",              # e.g. +66812345678
            "voice_call_enabled": "0",
            "twilio_account_sid": "",
            "twilio_auth_token": "",
            "twilio_from_number": "",        # e.g. +1...
            "line_enabled": "1",
            "line_channel_id": "",
            "line_channel_secret": "",
            "line_channel_access_token": "",
            "line_user_id": "",
            "timezone": "Asia/Bangkok",
            "gemini_api_keys": "",
            "gemini_model": "gemini-1.5-flash",
            "ai_reply_enabled": "1",
            "ai_system_prompt": "คุณคือผู้ช่วย AI บริการลูกค้าของบริษัทที่คอยดูแลตอบแชททาง LINE Official Account ตอบคำถามลูกค้าด้วยภาษาไทยที่สุภาพ เป็นมิตร กระชับ ชัดเจน และเป็นธรรมชาติเหมือนพนักงานตอบเอง",
            "browser_profile": "macos_sequoia"
        }
        
        for k, v in default_settings.items():
            cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
            
        # Migrate paylocity_url if it contains legacy Escher redirect
        cursor.execute("""
            UPDATE settings 
            SET value = 'https://access.paylocity.com/' 
            WHERE key = 'paylocity_url' AND (value LIKE '%Escher%' OR value LIKE '%redirect_uri%')
        """)

        # Migrate existing SQLite data if Supabase tables are empty
        if USE_SUPABASE:
            _migrate_from_sqlite(cursor)

        conn.commit()


# Settings Helpers
def get_setting(key: str, default: str = "") -> str:
    env_val = os.getenv(key.upper())
    if env_val:
        return env_val
    with get_db() as conn:
        row = conn.cursor().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row and row["value"]:
            val = row["value"]
            if key == "paylocity_url" and ("escher" in val.lower() or "redirect_uri" in val.lower()):
                return "https://access.paylocity.com/"
            return val
        return default

def get_all_settings() -> Dict[str, str]:
    with get_db() as conn:
        rows = conn.cursor().execute("SELECT key, value FROM settings").fetchall()
        settings = {r["key"]: r["value"] for r in rows}
    # Merge with environment variables
    for k in settings:
        env_val = os.getenv(k.upper())
        if env_val:
            settings[k] = env_val
    return settings

def update_settings(settings: Dict[str, str]):
    with get_db() as conn:
        cursor = conn.cursor()
        for k, v in settings.items():
            cursor.execute("""
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """, (k, v))
        conn.commit()

# Schedule Helpers
def get_schedules() -> List[Dict[str, Any]]:
    with get_db() as conn:
        rows = conn.cursor().execute("SELECT * FROM schedules ORDER BY target_time ASC").fetchall()
        return [dict(r) for r in rows]

def get_schedule(schedule_id: int) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        row = conn.cursor().execute("SELECT * FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
        return dict(row) if row else None

def add_schedule(title: str, action: str, schedule_type: str, target_time: str,
                 target_date: Optional[str] = None, days_of_week: Optional[str] = None) -> int:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO schedules (title, action, schedule_type, target_date, days_of_week, target_time, is_enabled, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?)
        """, (title, action, schedule_type, target_date, days_of_week, target_time, datetime.now().isoformat()))
        conn.commit()
        return cursor.lastrowid

def update_schedule(schedule_id: int, title: str, action: str, schedule_type: str, target_time: str,
                    target_date: Optional[str] = None, days_of_week: Optional[str] = None, is_enabled: int = 1):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE schedules
            SET title = ?, action = ?, schedule_type = ?, target_date = ?, days_of_week = ?, target_time = ?, is_enabled = ?
            WHERE id = ?
        """, (title, action, schedule_type, target_date, days_of_week, target_time, is_enabled, schedule_id))
        conn.commit()

def toggle_schedule(schedule_id: int, is_enabled: int):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE schedules SET is_enabled = ? WHERE id = ?", (is_enabled, schedule_id))
        conn.commit()

def delete_schedule(schedule_id: int):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
        conn.commit()

# History Helpers
def add_history(action: str, status: str, message: str, screenshot_path: Optional[str] = None) -> int:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO history (action, status, message, screenshot_path, executed_at)
            VALUES (?, ?, ?, ?, ?)
        """, (action, status, message, screenshot_path, datetime.now().isoformat()))
        conn.commit()
        return cursor.lastrowid

def update_history(history_id: int, status: str, message: str, screenshot_path: Optional[str] = None):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE history
            SET status = ?, message = ?, screenshot_path = COALESCE(?, screenshot_path)
            WHERE id = ?
        """, (status, message, screenshot_path, history_id))
        conn.commit()

def get_histories(limit: int = 50) -> List[Dict[str, Any]]:
    with get_db() as conn:
        rows = conn.cursor().execute("SELECT * FROM history ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

def get_history_by_id(history_id: int) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        row = conn.cursor().execute("SELECT * FROM history WHERE id = ?", (history_id,)).fetchone()
        return dict(row) if row else None

def get_running_history() -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        row = conn.cursor().execute("SELECT * FROM history WHERE status = 'running' ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

# LINE Chat & Message Helpers
def upsert_line_chat(source_type: str, source_id: str, display_name: Optional[str] = None,
                     picture_url: Optional[str] = None, status_message: Optional[str] = None,
                     reply_token: Optional[str] = None):
    now = datetime.now().isoformat()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO line_chats (source_type, source_id, display_name, picture_url, status_message, created_at, last_message_at, latest_reply_token, latest_reply_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                display_name = COALESCE(excluded.display_name, line_chats.display_name),
                picture_url = COALESCE(excluded.picture_url, line_chats.picture_url),
                status_message = COALESCE(excluded.status_message, line_chats.status_message),
                latest_reply_token = COALESCE(excluded.latest_reply_token, line_chats.latest_reply_token),
                latest_reply_time = COALESCE(excluded.latest_reply_time, line_chats.latest_reply_time)
        """, (source_type, source_id, display_name, picture_url, status_message, now, now, reply_token, now if reply_token else None))
        conn.commit()

def update_chat_profile(source_id: str, display_name: str, picture_url: Optional[str] = None, status_message: Optional[str] = None):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE line_chats
            SET display_name = ?, picture_url = COALESCE(?, picture_url), status_message = COALESCE(?, status_message)
            WHERE source_id = ?
        """, (display_name, picture_url, status_message, source_id))
        conn.commit()

def clear_chat_reply_token(source_id: str):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE line_chats
            SET latest_reply_token = NULL, latest_reply_time = NULL
            WHERE source_id = ?
        """, (source_id,))
        conn.commit()

def get_line_chats() -> List[Dict[str, Any]]:
    with get_db() as conn:
        rows = conn.cursor().execute("""
            SELECT * FROM line_chats
            ORDER BY COALESCE(last_message_at, created_at) DESC, id DESC
        """).fetchall()
        return [dict(r) for r in rows]

def get_line_chat(source_id: str) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        row = conn.cursor().execute("SELECT * FROM line_chats WHERE source_id = ?", (source_id,)).fetchone()
        return dict(row) if row else None

def add_line_message(chat_id: str, sender_type: str, sender_id: Optional[str],
                     message_type: str, content: str, raw_data: Optional[str] = None,
                     reply_token: Optional[str] = None, timestamp: Optional[str] = None) -> int:
    ts = timestamp or datetime.now().isoformat()
    with get_db() as conn:
        cursor = conn.cursor()
        # Insert message
        cursor.execute("""
            INSERT INTO line_messages (chat_id, sender_type, sender_id, message_type, content, raw_data, reply_token, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (chat_id, sender_type, sender_id, message_type, content, raw_data, reply_token, ts))
        msg_id = cursor.lastrowid

        # Update last_message and last_message_at in line_chats
        snippet = content if message_type == "text" else f"[{message_type}]"
        if reply_token:
            cursor.execute("""
                UPDATE line_chats
                SET last_message = ?, last_message_at = ?, latest_reply_token = ?, latest_reply_time = ?
                WHERE source_id = ?
            """, (snippet, ts, reply_token, ts, chat_id))
        else:
            cursor.execute("""
                UPDATE line_chats
                SET last_message = ?, last_message_at = ?
                WHERE source_id = ?
            """, (snippet, ts, chat_id))

        conn.commit()
        return msg_id

def get_line_messages(chat_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    with get_db() as conn:
        rows = conn.cursor().execute("""
            SELECT * FROM (
                SELECT * FROM line_messages
                WHERE chat_id = ?
                ORDER BY id DESC
                LIMIT ?
            ) ORDER BY id ASC
        """, (chat_id, limit)).fetchall()
        return [dict(r) for r in rows]

# LINE Webhook Log Helpers
def add_webhook_log(event_types: str, source_id: Optional[str], payload: str, status: str = "success") -> int:
    now = datetime.now().isoformat()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO line_webhook_logs (event_types, source_id, payload, status, received_at)
            VALUES (?, ?, ?, ?, ?)
        """, (event_types, source_id, payload, status, now))
        conn.commit()
        return cursor.lastrowid

def get_webhook_logs(page: int = 1, limit: int = 10) -> Dict[str, Any]:
    offset = max(0, (page - 1) * limit)
    with get_db() as conn:
        cursor = conn.cursor()
        total = cursor.execute("SELECT COUNT(*) FROM line_webhook_logs").fetchone()[0]
        rows = cursor.execute("""
            SELECT id, event_types, source_id, status, received_at,
                   substr(payload, 1, 120) as preview
            FROM line_webhook_logs
            ORDER BY id DESC
            LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()
        
        total_pages = max(1, (total + limit - 1) // limit)
        return {
            "logs": [dict(r) for r in rows],
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": total_pages
        }

def get_webhook_log(log_id: int) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        row = conn.cursor().execute("SELECT * FROM line_webhook_logs WHERE id = ?", (log_id,)).fetchone()
        return dict(row) if row else None
