import asyncio
import hashlib
import logging
import os
import uuid
from datetime import datetime, timedelta
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from apscheduler.schedulers.asyncio import AsyncIOScheduler

load_dotenv('/home/linus/meme-bot/.env')

from database import (
    init_db, get_setting, set_setting, get_random_unposted_meme,
    create_pending, close_pending, mark_meme_posted,
    mark_meme_skipped, get_meme_path, add_scheduled_post,
    get_pending_scheduled, mark_scheduled_posted, mark_meme_scheduled,
    get_scheduled_for_date, expire_pending,
    has_active_pending_for_chat, has_any_active_pending,
    has_active_pending_for_meme, get_pending_by_id,
    get_pendings_for_meme, close_pendings_for_meme, close_all_pending,
    get_old_pending_grouped, get_memes_stats, add_meme, get_meme_by_sha1,
    count_posted_today, clear_scheduled, reset_scheduled_to_new
)
from scanner import scan_memes_folder

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_IDS = [int(x.strip()) for x in os.getenv('ADMIN_IDS', '').split(',') if x.strip()]
CHANNEL_ID = os.getenv('CHANNEL_ID', '')
SUGGESTION_CHAT_ID = os.getenv('SUGGESTION_CHAT_ID', '')
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


def get_today():
    return datetime.now().date()


def count_scheduled_for_date(d=None):
    if d is None:
        d = get_today()
    return len(get_scheduled_for_date(d.isoformat()))


def compute_sha1(file_path):
    sha1 = hashlib.sha1()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            sha1.update(chunk)
    return sha1.hexdigest()


# ============================================================
# КОМАНДЫ
# ============================================================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ Ты не админ!")
        return
    limit = get_limit()
    start, end = get_active_hours()
    today = get_today()
    today_count = count_scheduled_for_date(today)
    status_line = (
        f"📅 Сегодня ({today.strftime('%d.%m')}): {today_count} / {limit}"
        if today_count < limit
        else f"🎉 Лимит на сегодня набран ({today_count} / {limit})"
    )
    await message.answer(
        f"🤖 Бот запущен!\n"
        f"📊 Лимит: {limit} постов в день\n"
        f"🕐 Часы: {start}:00 – {end}:00\n"
        f"{status_line}\n"
        f"📥 Предложка: {SUGGESTION_CHAT_ID or 'не задана'}\n\n"
        f"Команды:\n"
        f"/moderate — запросить мем вручную\n"
        f"/status — статус\n"
        f"/set_limit N — лимит\n"
        f"/set_hours X Y — часы активности\n"
        f"/simulate_new_day — сброс для теста\n"
        f"/reset_moderation — сбросить модерацию"
    )


@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    limit = get_limit()
    start, end = get_active_hours()
    today = get_today()
    today_count = count_scheduled_for_date(today)
    stats = get_memes_stats()
    stats_text = "\n".join([f"  • {k}: {v}" for k, v in stats.items()]) or "  (пусто)"
    await message.answer(
        f"📊 Текущие настройки:\n"
        f"• Лимит: {limit} постов в день\n"
        f"• Часы: {start}:00 – {end}:00\n"
        f"• Канал: {CHANNEL_ID or 'не задан'}\n"
        f"• Предложка: {SUGGESTION_CHAT_ID or 'не задана'}\n"
        f"• Сегодня: {today.strftime('%d.%m')}\n"
        f"• Одобрено: {today_count} / {limit}\n\n"
        f"📦 Мемы в базе:\n{stats_text}"
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


@dp.message(Command("moderate"))
async def cmd_moderate(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    limit = get_limit()
    today = get_today()
    today_count = count_scheduled_for_date(today)
    if today_count >= limit:
        await message.answer(
            f"⚠️ На сегодня ({today.strftime('%d.%m')}) лимит набран ({today_count}/{limit}).\n"
            f"Мем уйдёт на следующий свободный день."
        )
    await start_moderation(force=True)


@dp.message(Command("simulate_new_day"))
async def cmd_simulate_new_day(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    close_all_pending("expired")
    clear_scheduled()
    reset_scheduled_to_new()
    await message.answer(
        "🔄 Симуляция нового дня:\n"
        "• Расписание очищено\n"
        "• Мемы возвращены в пул\n"
        "• Модерация сброшена\n\n"
        "Запускаю модерацию..."
    )
    await asyncio.sleep(1)
    await start_moderation(force=True)


@dp.message(Command("reset_moderation"))
async def cmd_reset_moderation(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    close_all_pending("expired")
    await message.answer("🔄 Модерация сброшена. Запускаю заново...")
    await asyncio.sleep(1)
    await start_moderation(force=True)


# ============================================================
# ПРЕДЛОЖКА
# ============================================================

@dp.message(lambda m: SUGGESTION_CHAT_ID and (
    str(m.chat.id) == str(SUGGESTION_CHAT_ID) or
    (m.chat.username and f"@{m.chat.username}" == SUGGESTION_CHAT_ID)
))
async def handle_suggestion(message: types.Message):
    file_id = None
    file_ext = ".jpg"

    if message.photo:
        file_id = message.photo[-1].file_id
        file_ext = ".jpg"
    elif message.animation:
        file_id = message.animation.file_id
        file_ext = ".gif"
    elif message.video:
        file_id = message.video.file_id
        file_ext = ".mp4"
    else:
        return

    try:
        tg_file = await bot.get_file(file_id)
        filename = f"user_{message.from_user.id}_{uuid.uuid4().hex[:8]}{file_ext}"
        os.makedirs(MEMES_PATH, exist_ok=True)
        file_path = os.path.join(MEMES_PATH, filename)
        await bot.download_file(tg_file.file_path, file_path)
    except Exception as e:
        logging.error(f"Ошибка скачивания из предложки: {e}")
        return

    sha1 = compute_sha1(file_path)
    existing = get_meme_by_sha1(sha1)
    if existing:
        try:
            await message.reply("⚠️ Такой мем уже есть в базе.")
        except:
            pass
        os.remove(file_path)
        return

    meme_id = add_meme(filename, sha1, file_path, submitted_by=message.from_user.id)
    if not meme_id:
        os.remove(file_path)
        return

    user_name = message.from_user.full_name or f"id{message.from_user.id}"

    # Отправляем всем админам СВОЙ pending на этот мем
    for admin_id in ADMIN_IDS:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ В очередь", callback_data=f"sug_approve:{meme_id}:{admin_id}"),
                InlineKeyboardButton(text="❌ Пропустить", callback_data=f"sug_reject:{meme_id}:{admin_id}")
            ]
        ])
        caption = f"📥 Мем из предложки\nОт: {user_name}"
        try:
            if file_ext == ".jpg":
                await bot.send_photo(admin_id, FSInputFile(file_path), caption=caption, reply_markup=keyboard)
            elif file_ext == ".gif":
                await bot.send_animation(admin_id, FSInputFile(file_path), caption=caption, reply_markup=keyboard)
            else:
                await bot.send_video(admin_id, FSInputFile(file_path), caption=caption, reply_markup=keyboard)
        except Exception as e:
            logging.error(f"Не отправить админу {admin_id}: {e}")

    try:
        await message.reply("✅ Мем отправлен на модерацию!")
    except:
        pass


# ============================================================
# КНОПКИ
# ============================================================

@dp.callback_query()
async def handle_callback(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Не админ!", show_alert=True)
        return

    data = callback.data

    # --- Кнопки из предложки ---
    if data.startswith("sug_approve:") or data.startswith("sug_reject:"):
        parts = data.split(":")
        action = parts[0]
        meme_id = int(parts[1])

        meme = get_meme_by_id(meme_id)
        if not meme or meme["status"] != "new":
            await callback.answer("❌ Мем уже обработан", show_alert=True)
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except:
                pass
            return

        if action == "sug_approve":
            if not CHANNEL_ID:
                await callback.answer("❌ Канал не настроен!", show_alert=True)
                return
            slot_time = get_next_slot_with_gap()
            if not slot_time:
                await callback.answer("❌ Нет свободных слотов!", show_alert=True)
                return
            add_scheduled_post(meme_id, slot_time.isoformat())
            mark_meme_scheduled(meme_id)
            try:
                await callback.message.edit_caption(
                    caption=f"✅ Мем из предложки на {slot_time.strftime('%d.%m %H:%M')} (взял {callback.from_user.full_name})"
                )
            except:
                pass
            await callback.answer("✅ В очереди!")
        else:
            mark_meme_skipped(meme_id)
            try:
                await callback.message.edit_caption(caption=f"⏭ Пропущено (взял {callback.from_user.full_name})")
            except:
                pass
            await callback.answer("⏭ Ок")
        return

    # --- Обычные кнопки ---
    action, pending_id = data.split(":")
    pending_id = int(pending_id)

    pending = get_pending_by_id(pending_id)
    if not pending or pending["status"] != "pending":
        await callback.answer("❌ Мем уже обработан", show_alert=True)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except:
            pass
        return

    meme_id = pending["meme_id"]

    if action == "approve":
        if not CHANNEL_ID:
            await callback.answer("❌ Канал не настроен!", show_alert=True)
            return
        slot_time = get_next_slot_with_gap()
        if not slot_time:
            await callback.answer("❌ Нет свободных слотов!", show_alert=True)
            return

        # Кто первый — тот и одобрил
        add_scheduled_post(meme_id, slot_time.isoformat())
        mark_meme_scheduled(meme_id)
        close_pending(pending_id, "approved")

        # Закрываем pending на этот же мем у других админов
        other_pendings = get_pendings_for_meme(meme_id)
        for op in other_pendings:
            if op["id"] != pending_id:
                try:
                    await bot.edit_message_caption(
                        chat_id=op["chat_id"],
                        message_id=op["message_id"],
                        caption=f"✅ Мем уже одобрен ({callback.from_user.full_name})"
                    )
                except:
                    pass
                try:
                    await bot.edit_message_reply_markup(
                        chat_id=op["chat_id"],
                        message_id=op["message_id"],
                        reply_markup=None
                    )
                except:
                    pass
                close_pending(op["id"], "expired")

        try:
            await callback.message.delete()
        except:
            pass

        today = get_today()
        today_count = count_scheduled_for_date(today)
        limit = get_limit()
        await bot.send_message(
            callback.from_user.id,
            f"✅ Мем на {slot_time.strftime('%d.%m %H:%M')}\n"
            f"📅 Сегодня ({today.strftime('%d.%m')}): {today_count} / {limit}"
        )
        await callback.answer("✅ В очереди!")

        await asyncio.sleep(1)
        if today_count < limit:
            await start_moderation(force=True)

    elif action == "reject":
        mark_meme_skipped(meme_id)
        close_pending(pending_id, "rejected")

        # Закрываем у других
        other_pendings = get_pendings_for_meme(meme_id)
        for op in other_pendings:
            if op["id"] != pending_id:
                try:
                    await bot.edit_message_caption(
                        chat_id=op["chat_id"],
                        message_id=op["message_id"],
                        caption=f"⏭ Мем пропущен ({callback.from_user.full_name})"
                    )
                except:
                    pass
                try:
                    await bot.edit_message_reply_markup(
                        chat_id=op["chat_id"],
                        message_id=op["message_id"],
                        reply_markup=None
                    )
                except:
                    pass
                close_pending(op["id"], "expired")

        try:
            await callback.message.delete()
        except:
            pass
        await bot.send_message(callback.from_user.id, "⏭ Пропущено")
        await callback.answer("⏭ Ок")
        await asyncio.sleep(1)
        await start_moderation(force=True)


# ============================================================
# АЛГОРИТМ СЛОТОВ
# ============================================================

def get_next_slot_with_gap():
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


# ============================================================
# МОДЕРАЦИЯ ИЗ ПАПКИ
# ============================================================

async def start_moderation(force: bool = False):
    """Отправляет мем ВСЕМ админам. Каждый получает свой pending."""
    limit = get_limit()
    today = get_today()
    today_count = count_scheduled_for_date(today)
    if not force and today_count >= limit:
        return

    # Не плодим мемы, если на этот мем уже есть активные pending
    scan_memes_folder()
    meme = get_random_unposted_meme()
    if not meme:
        for admin_id in ADMIN_IDS:
            await bot.send_message(admin_id, "📭 Нет новых мемов!")
        return

    # Если на этот мем уже есть pending — не дублируем
    if has_active_pending_for_meme(meme["id"]):
        return

    caption = f"📸 {meme['filename']}\n📅 Сегодня ({today.strftime('%d.%m')}): {today_count} / {limit}"
    if today_count >= limit:
        caption += "\n⚠️ Сверх лимита — уйдёт на завтра"

    # Отправляем всем админам, каждому свой pending
    for admin_id in ADMIN_IDS:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ В очередь", callback_data="placeholder:0"),
                InlineKeyboardButton(text="❌ Пропустить", callback_data="placeholder:0")
            ]
        ])
        try:
            sent = await bot.send_photo(
                admin_id,
                FSInputFile(meme["file_path"]),
                caption=caption,
                reply_markup=keyboard
            )
            pending_id = create_pending(meme["id"], admin_id, sent.message_id)

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ В очередь", callback_data=f"approve:{pending_id}"),
                    InlineKeyboardButton(text="❌ Пропустить", callback_data=f"reject:{pending_id}")
                ]
            ])
            await bot.edit_message_reply_markup(
                chat_id=admin_id,
                message_id=sent.message_id,
                reply_markup=keyboard
            )
        except Exception as e:
            logging.error(f"Ошибка отправки админу {admin_id}: {e}")


# ============================================================
# ФОНОВЫЕ ЗАДАЧИ
# ============================================================

async def process_scheduled_posts():
    now = datetime.now()
    start_hour, end_hour = get_active_hours()

    if not (start_hour <= now.hour < end_hour):
        return

    if count_posted_today() >= get_limit():
        return

    scheduled = get_pending_scheduled()
    if not scheduled:
        return

    item = scheduled[0]
    if not CHANNEL_ID:
        return

    file_path = get_meme_path(item["meme_id"])
    if not file_path or not os.path.exists(file_path):
        mark_scheduled_posted(item["id"])
        return

    try:
        if file_path.endswith(".gif"):
            await bot.send_animation(CHANNEL_ID, FSInputFile(file_path))
        elif file_path.endswith(".mp4"):
            await bot.send_video(CHANNEL_ID, FSInputFile(file_path))
        else:
            await bot.send_photo(CHANNEL_ID, FSInputFile(file_path))
        mark_scheduled_posted(item["id"])
        mark_meme_posted(item["meme_id"])
        logging.info(f"Опубликован мем {item['meme_id']}")
    except Exception as e:
        logging.error(f"Ошибка публикации: {e}")


async def cleanup_old_pending():
    grouped = get_old_pending_grouped(hours=1)
    for chat_id, items in grouped.items():
        for item in items:
            if item.get("message_id"):
                try:
                    await bot.delete_message(chat_id=item["chat_id"], message_id=item["message_id"])
                except:
                    pass
            expire_pending(item["id"])
    if grouped:
        limit = get_limit()
        today = get_today()
        today_count = count_scheduled_for_date(today)
        if today_count < limit:
            await asyncio.sleep(1)
            await start_moderation(force=True)


async def daily_index():
    count = scan_memes_folder()
    logging.info(f"Индексация: {count}")


async def check_permissions():
    if CHANNEL_ID:
        try:
            me = await bot.get_me()
            member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=me.id)
            if member.status in ("administrator", "creator"):
                logging.info(f"✅ Бот админ в канале {CHANNEL_ID}")
            else:
                logging.error(f"⚠️ Бот НЕ админ в канале {CHANNEL_ID}")
        except Exception as e:
            logging.error(f"Ошибка проверки канала: {e}")

    if SUGGESTION_CHAT_ID:
        try:
            chat = await bot.get_chat(SUGGESTION_CHAT_ID)
            logging.info(f"✅ Предложка: {chat.title}")
        except Exception as e:
            logging.error(f"⚠️ Не могу достучаться до предложки: {e}")


async def main():
    init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    await check_permissions()

    scheduler.add_job(process_scheduled_posts, 'interval', minutes=1)
    scheduler.add_job(cleanup_old_pending, 'interval', minutes=5)
    scheduler.add_job(daily_index, 'cron', hour=9, minute=0)
    scheduler.start()

    logging.info("Бот запущен.")

    await asyncio.sleep(3)
    limit = get_limit()
    today = get_today()
    today_count = count_scheduled_for_date(today)
    if today_count < limit:
        await start_moderation(force=True)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
