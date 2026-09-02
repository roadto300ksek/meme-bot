#!/usr/bin/env python3
import asyncio
from app import start_moderation, process_scheduled_posts
from scanner import scan_memes_folder
from database import init_db, reset_daily_counter

async def main():
    init_db()
    reset_daily_counter()  # на всякий случай
    scan_memes_folder()
    await start_moderation()
    await process_scheduled_posts()

if __name__ == "__main__":
    asyncio.run(main())