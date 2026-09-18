import asyncio
import logging
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from apscheduler.schedulers.asyncio import AsyncIOScheduler

load_dotenv('/home/linus/meme-bot/.env')

from database import (
    init_db, get_setting, set_setting, get_random_unposted_meme,
    create_pending, get_pending, close_pending, mark_meme_posted,
    mark_meme_skipped, get_meme_path, add_scheduled_post,
    get_pending_scheduled, mark_scheduled_posted, mark_meme_scheduled,
    get_scheduled_for_date, get_old_pending, expire_pending
)
from scanner import scan_memes_folder

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_IDS = [int(x.strip()) for x in os.getenv('ADMIN_IDS', '').split(',') if x.strip()]
CHANNEL_ID = os.getenv('CHANNEL_ID', '')
MEMES_PATH = os.getenv('MEMES_PATH', './memes')
DEFAULT_LIMIT = int(os.getenv('DAILY_LIMIT', 5))

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()


def get_limit():
    return int(get_setting("daily_limit", DEFAULT_LIMIT))


def get_active_hours():
    start = int(get_setting("active_start_hour", 9))
    end = int(get_setting("active_end_hour", 23))
    return start, end


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ Ты не админ!")
        return
    limit = get_limit()
    start, end = get_active_hours()
    await message.answer(
        f"🤖 Бот запущен!\n"
        f"📊 Лимит: {limit} постов в день\n"
        f"🕐 Активные часы: {start}:00 – {end}:00\n\n"
        f"Команды:\n"
        f"/moderate — предложить мем\n"
        f"/set_limit N — изменить лимит\n"
        f"/set_hours X Y — изменить часы активности\n"
        f"/status — текущие настройки"
    )


@dp.message(Command("moderate"))
async def cmd_moderate(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    await start_moderation(message.from_user.id)


@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    limit = get_limit()
    start, end = get_active_hours()
    pending = get_pending()
    await message.answer(
        f"📊 Текущие настройки:\n"
        f"• Лимит: {limit} постов в день\n"
        f"• Часы: {start}:00 – {end}:00\n"
        f"• Канал: {CHANNEL_ID or 'не задан'}\n"
        f"• Модерация: {'есть активная' if pending else 'нет'}"
    )


@dp.message(Command("set_limit"))
async def cmd_set_limit(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        n = int(message.text.split()[1])
        if n < 1 or n > 50:
            raise ValueError
        set_setting("daily_limit", n)
        await message.answer(f"✅ Лимит установлен: {n} постов в день")
    except:
        await message.answer("❌ Используй: /set_limit 5 (от 1 до 50)")


@dp.message(Command("set_hours"))
async def cmd_set_hours(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        parts = message.text.split()
        start = int(parts[1])
        end = int(parts[2])
        if not (0 <= start < end <= 24):
            raise ValueError
        set_setting("active_start_hour", start)
        set_setting("active_end_hour", end)
        await message.answer(f"✅ Часы активности: {start}:00 – {end}:00")
    except:
        await message.answer("❌ Используй: /set_hours 9 23")


def get_next_slot_with_gap():
    """Ищет ближайший день, где есть место, и ставит мем в середину самого длинного промежутка"""
    limit = get_limit()
    start_hour, end_hour = get_active_hours()

    now = datetime.now()

    for day_offset in range(0, 14):
        target_date = (now + timedelta(days=day_offset)).date()
        date_str = target_date.isoformat()

        posts = get_scheduled_for_date(date_str)
        if len(posts) >= limit:
            continue

        day_start = datetime(target_date.year, target_date.month, target_date.day, start_hour, 0)
        day_end = datetime(target_date.year, target_date.month, target_date.day, end_hour, 0)

        points = [day_start, day_end]
        for p in posts:
            dt = datetime.fromisoformat(p["scheduled_at"])
            if day_start < dt < day_end:
                points.append(dt)

        points.sort()

        best_gap = 0
        best_mid = None
        for i in range(len(points) - 1):
            gap = (points[i+1] - points[i]).total_seconds()
            if gap > best_gap:
                best_gap = gap
                best_mid = points[i] + (points[i+1] - points[i]) / 2

        if best_mid and best_mid > now:
            return best_mid

    return None


@dp.callback_query()
async def handle_callback(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Не админ!", show_alert=True)
        return

    action, pending_id = callback.data.split(":")
    pending_id = int(pending_id)

    pending = get_pending()
    if not pending or pending["id"] != pending_id:
        await callback.answer("❌ Устарело", show_alert=True)
        return

    meme_id = pending["meme_id"]

    if action == "approve":
        if not CHANNEL_ID:
            await callback.answer("❌ Канал не настроен в .env!", show_alert=True)
            return

        slot_time = get_next_slot_with_gap()
        if not slot_time:
            await callback.answer("❌ Нет свободных слотов на 2 недели вперёд!", show_alert=True)
            return

        add_scheduled_post(meme_id, slot_time.isoformat())
        mark_meme_scheduled(meme_id)
        close_pending(pending_id, "approved")

        try:
            await callback.message.delete()
        except:
            pass

        await bot.send_message(
            callback.from_user.id,
            f"✅ Мем добавлен в очередь на {slot_time.strftime('%d.%m %H:%M')}"
        )
        await callback.answer("✅ В очереди!")
        await start_moderation(callback.from_user.id)

    elif action == "reject":
        mark_meme_skipped(meme_id)
        close_pending(pending_id, "rejected")

        try:
            await callback.message.delete()
        except:
            pass

        await bot.send_message(callback.from_user.id, "⏭ Пропущено")
        await callback.answer("⏭ Ок")
        await start_moderation(callback.from_user.id)


async def start_moderation(chat_id: int):
    scan_memes_folder()
    meme = get_random_unposted_meme()
    if not meme:
        await bot.send_message(chat_id, "📭 Нет новых мемов!")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ В очередь", callback_data="placeholder:0"),
            InlineKeyboardButton(text="❌ Пропустить", callback_data="placeholder:0")
        ]
    ])

    photo = FSInputFile(meme["file_path"])
    sent = await bot.send_photo(
        chat_id,
        photo,
        caption=f"📸 {meme['filename']}",
        reply_markup=keyboard
    )

    pending_id = create_pending(meme["id"], chat_id, sent.message_id)

    # Обновляем кнопки с реальным pending_id
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ В очередь", callback_data=f"approve:{pending_id}"),
            InlineKeyboardButton(text="❌ Пропустить", callback_data=f"reject:{pending_id}")
        ]
    ])
    try:
        await bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=sent.message_id,
            reply_markup=keyboard
        )
    except Exception as e:
        logging.error(f"Не удалось обновить кнопки: {e}")


async def process_scheduled_posts():
    scheduled = get_pending_scheduled()
    for item in scheduled:
        if not CHANNEL_ID:
            continue
        file_path = get_meme_path(item["meme_id"])
        if not file_path or not os.path.exists(file_path):
            mark_scheduled_posted(item["id"])
            continue
        try:
            photo = FSInputFile(file_path)
            await bot.send_photo(chat_id=CHANNEL_ID, photo=photo)
            mark_scheduled_posted(item["id"])
            mark_meme_posted(item["meme_id"])
            logging.info(f"Опубликован отложенный мем {item['meme_id']}")
        except Exception as e:
            logging.error(f"Ошибка публикации отложенного мема: {e}")


async def cleanup_old_pending():
    """Удаляет устаревшие pending-записи и сообщения с кнопками"""
    old = get_old_pending(hours=1)
    for item in old:
        if item.get("message_id"):
            try:
                await bot.delete_message(chat_id=item["chat_id"], message_id=item["message_id"])
            except:
                pass
        expire_pending(item["id"])
        try:
            await bot.send_message(
                item["chat_id"],
                "⏰ Предыдущее предложение устарело (прошёл час). Напиши /moderate для нового."
            )
        except:
            pass
    if old:
        logging.info(f"Очищено {len(old)} устаревших предложений")


async def daily_index():
    logging.info("Запущена ежедневная индексация папки")
    count = scan_memes_folder()
    logging.info(f"Индексация завершена. Обработано файлов: {count}")


async def check_channel_permissions():
    if not CHANNEL_ID:
        logging.warning("CHANNEL_ID не задан в .env")
        return
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=me.id)
        if member.status not in ("administrator", "creator"):
            logging.error(f"⚠️ Бот НЕ админ в канале {CHANNEL_ID}! Статус: {member.status}")
            for admin in ADMIN_IDS:
                try:
                    await bot.send_message(
                        admin,
                        f"⚠️ Бот не админ в канале {CHANNEL_ID}. Добавь его как админа с правом публикации."
                    )
                except:
                    pass
        else:
            logging.info(f"✅ Бот админ в канале {CHANNEL_ID}")
    except Exception as e:
        logging.error(f"Ошибка проверки прав в канале: {e}")


async def main():
    init_db()
    await bot.delete_webhook(drop_pending_updates=True)

    await check_channel_permissions()

    scheduler.add_job(process_scheduled_posts, 'interval', minutes=1)
    scheduler.add_job(cleanup_old_pending, 'interval', minutes=5)
    scheduler.add_job(daily_index, 'cron', hour=9, minute=0)

    scheduler.start()
    logging.info("Планировщик запущен: посты каждую минуту, очистка каждые 5 минут, индексация в 9:00")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
