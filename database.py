import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")
DB_PATH = "memes.db"


def now():
    return datetime.now(MSK).replace(tzinfo=None)


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
                submitted_by INTEGER,
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
                created_at TIMESTAMP,
                status TEXT DEFAULT 'pending',
                message_id INTEGER
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
        try:
            conn.execute("ALTER TABLE memes ADD COLUMN submitted_by INTEGER")
        except:
            pass
        try:
            conn.execute("ALTER TABLE pending_moderation ADD COLUMN message_id INTEGER")
        except:
            pass

        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", ("daily_limit", "5"))
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", ("active_start_hour", "9"))
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", ("active_end_hour", "23"))
        conn.commit()


def get_setting(key, default=None):
    with get_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()


def add_meme(filename, sha1, file_path, submitted_by=None):
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO memes (filename, sha1, file_path, submitted_by) VALUES (?, ?, ?, ?)",
            (filename, sha1, file_path, submitted_by)
        )
        conn.commit()
        return cursor.lastrowid


def get_meme_by_sha1(sha1):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM memes WHERE sha1 = ?", (sha1,)).fetchone()
        return dict(row) if row else None


def get_meme_by_id(meme_id):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM memes WHERE id = ?", (meme_id,)).fetchone()
        return dict(row) if row else None


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
        conn.execute("UPDATE memes SET status = 'posted', posted_at = ? WHERE id = ?", (now().isoformat(), meme_id))
        conn.commit()


def mark_meme_skipped(meme_id):
    with get_db() as conn:
        conn.execute("UPDATE memes SET status = 'skipped' WHERE id = ?", (meme_id,))
        conn.commit()


def mark_meme_scheduled(meme_id):
    with get_db() as conn:
        conn.execute("UPDATE memes SET status = 'scheduled' WHERE id = ?", (meme_id,))
        conn.commit()


def has_active_pending_for_meme(meme_id):
    with get_db() as conn:
        row = conn.execute("""
            SELECT id FROM pending_moderation
            WHERE meme_id = ? AND status = 'pending'
            LIMIT 1
        """, (meme_id,)).fetchone()
        return row is not None


def has_active_pending_for_chat(chat_id):
    """Есть ли у этого чата активный pending."""
    with get_db() as conn:
        row = conn.execute("""
            SELECT id FROM pending_moderation
            WHERE chat_id = ? AND status = 'pending'
            LIMIT 1
        """, (chat_id,)).fetchone()
        return row is not None


def create_pending(meme_id, chat_id, message_id=None):
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO pending_moderation (meme_id, chat_id, message_id, created_at) VALUES (?, ?, ?, ?)",
            (meme_id, chat_id, message_id, now().isoformat())
        )
        conn.commit()
        return cursor.lastrowid


def get_pending_by_id(pending_id):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM pending_moderation WHERE id = ?", (pending_id,)).fetchone()
        return dict(row) if row else None


def get_pendings_for_meme(meme_id):
    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM pending_moderation
            WHERE meme_id = ? AND status = 'pending'
        """, (meme_id,)).fetchall()
        return [dict(row) for row in rows]


def close_pending(pending_id, status):
    with get_db() as conn:
        conn.execute("UPDATE pending_moderation SET status = ? WHERE id = ?", (status, pending_id))
        conn.commit()


def close_all_pending(status="expired"):
    with get_db() as conn:
        conn.execute("UPDATE pending_moderation SET status = ? WHERE status = 'pending'", (status,))
        conn.commit()


def get_old_pending_grouped(hours=1):
    cutoff = (now() - timedelta(hours=hours)).isoformat()
    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM pending_moderation
            WHERE status = 'pending'
              AND created_at <= ?
        """, (cutoff,)).fetchall()
        grouped = {}
        for row in rows:
            r = dict(row)
            grouped.setdefault(r["chat_id"], []).append(r)
        return grouped


def expire_pending(pending_id):
    with get_db() as conn:
        conn.execute("UPDATE pending_moderation SET status = 'expired' WHERE id = ?", (pending_id,))
        conn.commit()


def get_meme_path(meme_id):
    with get_db() as conn:
        row = conn.execute("SELECT file_path FROM memes WHERE id = ?", (meme_id,)).fetchone()
        return row["file_path"] if row else None


def add_scheduled_post(meme_id, scheduled_at):
    with get_db() as conn:
        conn.execute("INSERT INTO scheduled_posts (meme_id, scheduled_at) VALUES (?, ?)", (meme_id, scheduled_at))
        conn.commit()


def get_pending_scheduled():
    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM scheduled_posts
            WHERE status = 'pending' AND scheduled_at <= ?
            ORDER BY scheduled_at ASC
        """, (now().isoformat(),)).fetchall()
        return [dict(row) for row in rows]


def mark_scheduled_posted(scheduled_id):
    with get_db() as conn:
        conn.execute("UPDATE scheduled_posts SET status = 'posted' WHERE id = ?", (scheduled_id,))
        conn.commit()


def get_scheduled_for_date(date_str):
    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM scheduled_posts
            WHERE status = 'pending' AND scheduled_at LIKE ?
            ORDER BY scheduled_at ASC
        """, (f"{date_str}%",)).fetchall()
        return [dict(row) for row in rows]


def count_all_for_date(date_str):
    with get_db() as conn:
        row = conn.execute("""
            SELECT COUNT(*) FROM scheduled_posts
            WHERE status IN ('pending', 'posted') AND scheduled_at LIKE ?
        """, (f"{date_str}%",)).fetchone()
        return row[0] if row else 0


def count_posted_today():
    today = now().date().isoformat()
    with get_db() as conn:
        row = conn.execute("""
            SELECT COUNT(*) FROM scheduled_posts
            WHERE status = 'posted' AND scheduled_at LIKE ?
        """, (f"{today}%",)).fetchone()
        return row[0] if row else 0


def clear_scheduled():
    with get_db() as conn:
        conn.execute("DELETE FROM scheduled_posts")
        conn.commit()


def reset_scheduled_to_new():
    with get_db() as conn:
        conn.execute("UPDATE memes SET status = 'new' WHERE status = 'scheduled'")
        conn.commit()


def get_memes_stats():
    with get_db() as conn:
        rows = conn.execute("SELECT status, COUNT(*) as cnt FROM memes GROUP BY status").fetchall()
        return {row["status"]: row["cnt"] for row in rows}
