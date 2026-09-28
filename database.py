# OPTIMIZED: полностью на aiosqlite. Даты — INTEGER (UTC Unix ts).
import logging
from datetime import date

import aiosqlite

from utils import now_ts, day_bounds_ts

logger = logging.getLogger(__name__)

DB_PATH = "memes.db"
PENDING_TTL_SECONDS = 60 * 60


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS memes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT,
                sha1 TEXT UNIQUE,
                file_path TEXT,
                status TEXT DEFAULT 'new',
                submitted_by INTEGER,
                posted_at INTEGER
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_moderation (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meme_id INTEGER,
                chat_id INTEGER,
                created_at INTEGER,
                status TEXT DEFAULT 'pending',
                message_id INTEGER
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meme_id INTEGER,
                scheduled_at INTEGER,
                status TEXT DEFAULT 'pending'
            )
        """)

        for migration in (
            "ALTER TABLE memes ADD COLUMN submitted_by INTEGER",
            "ALTER TABLE pending_moderation ADD COLUMN message_id INTEGER",
        ):
            try:
                await conn.execute(migration)
            except aiosqlite.OperationalError:
                pass

        for key, val in (
            ("daily_limit", "5"),
            ("active_start_hour", "9"),
            ("active_end_hour", "23"),
        ):
            await conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, val)
            )

        await _migrate_dates_to_unix(conn)
        await conn.commit()


async def _migrate_dates_to_unix(conn: aiosqlite.Connection) -> None:
    async with conn.execute(
        "SELECT value FROM settings WHERE key = 'dates_migrated_v2'"
    ) as cur:
        if await cur.fetchone():
            return

    logger.info("🔧 Миграция дат ISO → Unix ts (UTC)...")

    for table, col in (
        ("memes", "posted_at"),
        ("pending_moderation", "created_at"),
        ("scheduled_posts", "scheduled_at"),
    ):
        await conn.execute(
            f"""UPDATE {table}
                SET {col} = CAST(strftime('%s', {col} || '+03:00') AS INTEGER)
                WHERE {col} IS NOT NULL AND typeof({col}) = 'text'"""
        )

    await conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('dates_migrated_v2', '1')"
    )
    await conn.commit()
    logger.info("🔧 Миграция завершена")


async def get_setting(key: str, default: str | None = None) -> str | None:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ) as cur:
            row = await cur.fetchone()
            return row["value"] if row else default


async def set_setting(key: str, value: str) -> None:
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, str(value)),
        )
        await conn.commit()


async def add_meme(filename: str, sha1: str, file_path: str, submitted_by: int | None = None) -> int | None:
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "INSERT OR IGNORE INTO memes (filename, sha1, file_path, submitted_by) VALUES (?, ?, ?, ?)",
            (filename, sha1, file_path, submitted_by),
        )
        await conn.commit()
        return cur.lastrowid


async def get_meme_by_sha1(sha1: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT * FROM memes WHERE sha1 = ?", (sha1,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_random_unposted_meme() -> dict | None:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM memes WHERE status IN ('new','skipped') ORDER BY RANDOM() LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def mark_meme_posted(meme_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "UPDATE memes SET status='posted', posted_at=? WHERE id=?",
            (now_ts(), meme_id),
        )
        await conn.commit()


async def mark_meme_skipped(meme_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("UPDATE memes SET status='skipped' WHERE id=?", (meme_id,))
        await conn.commit()


async def mark_meme_scheduled(meme_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("UPDATE memes SET status='scheduled' WHERE id=?", (meme_id,))
        await conn.commit()


async def get_meme_path(meme_id: int) -> str | None:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT file_path FROM memes WHERE id = ?", (meme_id,)) as cur:
            row = await cur.fetchone()
            return row["file_path"] if row else None


async def has_active_pending_for_meme(meme_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            "SELECT 1 FROM pending_moderation WHERE meme_id=? AND status='pending' LIMIT 1",
            (meme_id,),
        ) as cur:
            return await cur.fetchone() is not None


async def has_active_pending_for_chat(chat_id: int) -> bool:
    cutoff = now_ts() - PENDING_TTL_SECONDS
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            "SELECT 1 FROM pending_moderation WHERE chat_id=? AND status='pending' AND created_at >= ? LIMIT 1",
            (chat_id, cutoff),
        ) as cur:
            return await cur.fetchone() is not None


async def has_any_active_pending() -> bool:
    cutoff = now_ts() - PENDING_TTL_SECONDS
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            "SELECT 1 FROM pending_moderation WHERE status='pending' AND created_at >= ? LIMIT 1",
            (cutoff,),
        ) as cur:
            return await cur.fetchone() is not None


async def create_pending(meme_id: int, chat_id: int, message_id: int | None = None) -> int:
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "INSERT INTO pending_moderation (meme_id, chat_id, message_id, created_at) VALUES (?, ?, ?, ?)",
            (meme_id, chat_id, message_id, now_ts()),
        )
        await conn.commit()
        return cur.lastrowid


async def get_pending_by_id(pending_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM pending_moderation WHERE id=?", (pending_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_pendings_for_meme(meme_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM pending_moderation WHERE meme_id=? AND status='pending'",
            (meme_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def close_pending(pending_id: int, status: str) -> None:
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "UPDATE pending_moderation SET status=? WHERE id=?", (status, pending_id)
        )
        await conn.commit()


async def close_all_pending(status: str = "expired") -> int:
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "UPDATE pending_moderation SET status=? WHERE status='pending'", (status,)
        )
        await conn.commit()
        return conn.total_changes


async def get_old_pending_grouped(hours: int = 1) -> dict[int, list[dict]]:
    cutoff = now_ts() - hours * 3600
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM pending_moderation WHERE status='pending' AND created_at <= ?",
            (cutoff,),
        ) as cur:
            rows = await cur.fetchall()
    grouped: dict[int, list[dict]] = {}
    for row in rows:
        r = dict(row)
        grouped.setdefault(r["chat_id"], []).append(r)
    return grouped


async def expire_pending(pending_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "UPDATE pending_moderation SET status='expired' WHERE id=?", (pending_id,)
        )
        await conn.commit()


async def auto_cleanup_stale_pendings() -> int:
    cutoff = now_ts() - PENDING_TTL_SECONDS
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "UPDATE pending_moderation SET status='expired' WHERE status='pending' AND created_at < ?",
            (cutoff,),
        )
        await conn.commit()
        return cur.rowcount


async def add_scheduled_post(meme_id: int, scheduled_ts: int) -> None:
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO scheduled_posts (meme_id, scheduled_at) VALUES (?, ?)",
            (meme_id, scheduled_ts),
        )
        await conn.commit()


async def get_pending_scheduled() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM scheduled_posts WHERE status='pending' AND scheduled_at <= ? ORDER BY scheduled_at ASC",
            (now_ts(),),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def mark_scheduled_posted(scheduled_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "UPDATE scheduled_posts SET status='posted' WHERE id=?", (scheduled_id,)
        )
        await conn.commit()


async def get_scheduled_for_date(d: date) -> list[dict]:
    start_ts, end_ts = day_bounds_ts(d)
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM scheduled_posts WHERE status='pending' AND scheduled_at >= ? AND scheduled_at < ? ORDER BY scheduled_at ASC",
            (start_ts, end_ts),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def count_all_for_date(d: date) -> int:
    start_ts, end_ts = day_bounds_ts(d)
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            "SELECT COUNT(*) FROM scheduled_posts WHERE status IN ('pending','posted') AND scheduled_at >= ? AND scheduled_at < ?",
            (start_ts, end_ts),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def count_posted_today() -> int:
    from utils import ts_to_msk
    d = ts_to_msk(now_ts()).date()
    start_ts, end_ts = day_bounds_ts(d)
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            "SELECT COUNT(*) FROM scheduled_posts WHERE status='posted' AND scheduled_at >= ? AND scheduled_at < ?",
            (start_ts, end_ts),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def clear_scheduled() -> None:
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("DELETE FROM scheduled_posts")
        await conn.commit()


async def reset_scheduled_to_new() -> None:
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("UPDATE memes SET status='new' WHERE status='scheduled'")
        await conn.commit()


async def get_memes_stats() -> dict[str, int]:
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute("SELECT status, COUNT(*) FROM memes GROUP BY status") as cur:
            rows = await cur.fetchall()
    return {r[0]: r[1] for r in rows}
