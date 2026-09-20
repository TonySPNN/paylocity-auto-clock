import sqlite3
import os
from datetime import datetime
from typing import Dict, List, Optional, Any

DB_PATH = os.path.join(os.path.dirname(__file__), "data.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

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
        
        # Default Settings
        default_settings = {
            "paylocity_url": "https://access.paylocity.com/?client_id=56400b1e4bab4790b909ace559dadbc1&redirect_uri=https%3a%2f%2flogin.paylocity.com%2fEscher%2fEscher_WebUI%2fMembership.IdentityManager%2fReturn&response_mode=form_post&response_type=code&scope=openid+profile+offline_access+security%3acredential%3acreate+security%3acredential%3aupdate+security%3acredential%3adelete+security%3acompanysecuritysettings%3acreate+security%3acompanysecuritysettings%3adelete",
            "company_id": "",
            "username": "",
            "password": "",
            "phone_number": "",              # e.g. +66812345678
            "voice_call_enabled": "1",
            "twilio_account_sid": "",
            "twilio_auth_token": "",
            "twilio_from_number": "",        # e.g. +1...
            "line_enabled": "1",
            "line_channel_access_token": "",
            "line_user_id": "",
            "timezone": "Asia/Bangkok"
        }
        
        for k, v in default_settings.items():
            cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
            
        conn.commit()

# Settings Helpers
def get_setting(key: str, default: str = "") -> str:
    env_val = os.getenv(key.upper())
    if env_val:
        return env_val
    with get_db() as conn:
        row = conn.cursor().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row and row["value"]:
            return row["value"]
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
