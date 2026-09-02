import sqlite3
from datetime import datetime

DB_PATH = "memes.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT,
                sha1 TEXT UNIQUE,
                file_path TEXT,
                status TEXT DEFAULT 'new',
                posted_at TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_moderation (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meme_id INTEGER,
                chat_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'pending'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meme_id INTEGER,
                scheduled_at TIMESTAMP,
                status TEXT DEFAULT 'pending'
            )
        """)
        # Настройки по умолчанию
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", ("daily_limit", str(5)))
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", ("current_day_posts", "0"))
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", ("channel_id", ""))
        conn.commit()

def get_setting(key, default=None):
    with get_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

def set_setting(key, value):
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()

def add_meme(filename, sha1, file_path):
    with get_db() as conn:
        conn.execute("INSERT OR IGNORE INTO memes (filename, sha1, file_path) VALUES (?, ?, ?)", (filename, sha1, file_path))
        conn.commit()

def get_random_unposted_meme():
    with get_db() as conn:
        row = conn.execute("""
            SELECT * FROM memes
            WHERE status IN ('new', 'skipped')
            ORDER BY RANDOM()
            LIMIT 1
        """).fetchone()
        return dict(row) if row else None

def mark_meme_posted(meme_id):
    with get_db() as conn:
        conn.execute("UPDATE memes SET status = 'posted', posted_at = CURRENT_TIMESTAMP WHERE id = ?", (meme_id,))
        conn.commit()

def mark_meme_skipped(meme_id):
    with get_db() as conn:
        conn.execute("UPDATE memes SET status = 'skipped' WHERE id = ?", (meme_id,))
        conn.commit()

def create_pending(meme_id, chat_id):
    with get_db() as conn:
        cursor = conn.execute("INSERT INTO pending_moderation (meme_id, chat_id) VALUES (?, ?)", (meme_id, chat_id))
        conn.commit()
        return cursor.lastrowid

def get_pending():
    with get_db() as conn:
        row = conn.execute("SELECT * FROM pending_moderation WHERE status = 'pending' ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

def close_pending(pending_id, status):
    with get_db() as conn:
        conn.execute("UPDATE pending_moderation SET status = ? WHERE id = ?", (status, pending_id))
        conn.commit()

def add_scheduled_post(meme_id, scheduled_at):
    with get_db() as conn:
        conn.execute("INSERT INTO scheduled_posts (meme_id, scheduled_at) VALUES (?, ?)", (meme_id, scheduled_at))
        conn.commit()

def get_pending_scheduled():
    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM scheduled_posts
            WHERE status = 'pending' AND scheduled_at <= CURRENT_TIMESTAMP
        """).fetchall()
        return [dict(row) for row in rows]

def mark_scheduled_posted(scheduled_id):
    with get_db() as conn:
        conn.execute("UPDATE scheduled_posts SET status = 'posted' WHERE id = ?", (scheduled_id,))
        conn.commit()

def get_meme_path(meme_id):
    with get_db() as conn:
        row = conn.execute("SELECT file_path FROM memes WHERE id = ?", (meme_id,)).fetchone()
        return row["file_path"] if row else None

def reset_daily_counter():
    set_setting("current_day_posts", 0)