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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(BASE_DIR, 'bot.log'), encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

from database import (
    init_db, get_setting, set_setting, get_random_unposted_meme,
    create_pending, close_pending, mark_meme_posted,
    mark_meme_skipped, get_meme_path, add_scheduled_post,
    get_pending_scheduled, mark_scheduled_posted, mark_meme_scheduled,
    get_scheduled_for_date, count_all_for_date, expire_pending,
    has_active_pending_for_meme, get_pending_by_id,
    get_pendings_for_meme, close_all_pending,
    get_old_pending_grouped, get_memes_stats, add_meme, get_meme_by_sha1,
    count_posted_today, clear_scheduled, reset_scheduled_to_new,
    now
)
from scanner import scan_memes_folder

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_IDS = [int(x.strip()) for x in os.getenv('ADMIN_IDS', '').split(',') if x.strip()]
CHANNEL_ID = os.getenv('CHANNEL_ID', '')
MEMES_PATH = os.getenv('MEMES_PATH', os.path.join(BASE_DIR, 'memes'))
DEFAULT_LIMIT = int(os.getenv('DAILY_LIMIT', 5))
SCAN_HOUR = int(os.getenv('SCAN_HOUR', 0))
MODERATE_HOUR = int(os.getenv('MODERATE_HOUR', 9))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()


def get_limit():
    return int(get_setting("daily_limit", DEFAULT_LIMIT))


def get_active_hours():
    start = int(get_setting("active_start_hour", 9))
    end = int(get_setting("active_end_hour", 23))
    return start, end


def get_moderation_start_hour():
    return int(get_setting("moderation_start_hour", get_active_hours()[0]))


def get_today():
    return now().date()


def count_scheduled_for_date(d):
    return count_all_for_date(d.isoformat())


def day_label(d):
    today = get_today()
    if d == today:
        return "Сегодня"
    elif d == today + timedelta(days=1):
        return "Завтра"
    else:
        return d.strftime('%d.%m')


def compute_sha1(file_path):
    sha1 = hashlib.sha1()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            sha1.update(chunk)
    return sha1.hexdigest()


def is_moderation_time():
    if get_setting("test_mode", "0") == "1":
        return True
    now_hour = now().hour
    mod_start = get_moderation_start_hour()
    active_start, active_end = get_active_hours()
    return mod_start <= now_hour < active_end


def has_free_slot():
    limit = get_limit()
    today = get_today()
    for day_offset in range(0, 14):
        d = today + timedelta(days=day_offset)
        if count_scheduled_for_date(d) < limit:
            return True
    return False


# ============================================================
# СЛУЖЕБНЫЕ ФУНКЦИИ (для таймера)
# ============================================================

async def do_scan():
    """Сканирует папку memes/, добавляет новые файлы в базу."""
    count = scan_memes_folder()
    logger.info(f"[SCAN] Обработано файлов: {count}")
    return count


async def do_moderate():
    """Предлагает мемы на модерацию."""
    logger.info("[MODERATE] Автозапуск модерации")
    await start_moderation(force=True)


# ============================================================
# КОМАНДЫ
# ============================================================

@dp.message(Command("scan"))
async def cmd_scan(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    await message.answer("🔄 Сканирую папку memes/...")
    count = await do_scan()
    stats = get_memes_stats()
    stats_text = "\n".join([f"  • {k}: {v}" for k, v in stats.items()])
    await message.answer(
        f"✅ Скан завершён\n"
        f"📂 Обработано файлов: {count}\n\n"
        f"📦 Мемы в базе:\n{stats_text}"
    )


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id

    if user_id not in ADMIN_IDS:
        await message.answer(
            "👋 Привет!\n\n"
            "Это бот мем-канала @meme359daily.\n"
            "Кинь мне мем (фото, GIF или видео) — и я передам его админам на модерацию.\n\n"
            "Если мем одобрят — он появится в канале!"
        )
        return

    limit = get_limit()
    start, end = get_active_hours()
    mod_start = get_moderation_start_hour()
    today = get_today()
    tomorrow = today + timedelta(days=1)
    today_count = count_scheduled_for_date(today)
    tomorrow_count = count_scheduled_for_date(tomorrow)
    posted_today = count_posted_today()
    test_mode = get_setting("test_mode", "0") == "1"
    test_line = "🧪 ТЕСТОВЫЙ РЕЖИМ АКТИВЕН\n" if test_mode else ""
    now_str = now().strftime('%H:%M:%S')
    free_line = "✅ Есть свободные слоты" if has_free_slot() else "🚫 Все слоты на 14 дней забиты"
    await message.answer(
        f"{test_line}"
        f"🤖 Бот запущен!\n"
        f"🕒 Сейчас: {now_str} MSK\n"
        f"📊 Лимит: {limit}/день\n"
        f"🕐 Публикация: {start}:00 – {end}:00\n"
        f"📥 Модерация с: {mod_start}:00\n"
        f"📅 Сегодня ({today.strftime('%d.%m')}): {today_count}/{limit} (опубликовано: {posted_today})\n"
        f"📅 Завтра ({tomorrow.strftime('%d.%m')}): {tomorrow_count}/{limit}\n"
        f"{free_line}\n\n"
        f"Автозадачи:\n"
        f"  • scan в {SCAN_HOUR:02d}:00\n"
        f"  • moderate в {MODERATE_HOUR:02d}:00\n\n"
        f"/scan — просканировать папку memes\n"
        f"/moderate — предложить мем вручную\n"
        f"/status — статус\n"
        f"/set_limit N — лимит\n"
        f"/set_hours X Y — часы публикации\n"
        f"/set_moderation_hour N — час начала модерации\n"
        f"/test_cycle — тест\n"
        f"/test_cycle_end — выход из теста"
    )


@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    limit = get_limit()
    start, end = get_active_hours()
    mod_start = get_moderation_start_hour()
    today = get_today()
    tomorrow = today + timedelta(days=1)
    today_count = count_scheduled_for_date(today)
    tomorrow_count = count_scheduled_for_date(tomorrow)
    posted_today = count_posted_today()
    stats = get_memes_stats()
    stats_text = "\n".join([f"  • {k}: {v}" for k, v in stats.items()]) or "  (пусто)"
    test_mode = get_setting("test_mode", "0") == "1"
    test_line = "\n🧪 ТЕСТОВЫЙ РЕЖИМ АКТИВЕН\n" if test_mode else "\n"
    now_str = now().strftime('%H:%M:%S')
    free_line = "✅ Есть свободные слоты" if has_free_slot() else "🚫 Все слоты на 14 дней забиты"
    await message.answer(
        f"📊 Настройки:{test_line}"
        f"🕒 Сейчас: {now_str} MSK\n"
        f"• Лимит: {limit}/день\n"
        f"• Публикация: {start}:00 – {end}:00\n"
        f"• Модерация с: {mod_start}:00\n"
        f"• Канал: {CHANNEL_ID or '—'}\n"
        f"• {free_line}\n"
        f"• scan в {SCAN_HOUR:02d}:00, moderate в {MODERATE_HOUR:02d}:00\n\n"
        f"📅 Сегодня ({today.strftime('%d.%m')}): {today_count}/{limit} (опубликовано: {posted_today})\n"
        f"📅 Завтра ({tomorrow.strftime('%d.%m')}): {tomorrow_count}/{limit}\n\n"
        f"📦 Мемы:\n{stats_text}"
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
        await message.answer(f"✅ Лимит: {n}/день")
    except:
        await message.answer("❌ /set_limit 5")


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
        await message.answer(f"✅ Публикация: {start}:00 – {end}:00")
    except:
        await message.answer("❌ /set_hours 9 23")


@dp.message(Command("set_moderation_hour"))
async def cmd_set_moderation_hour(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        n = int(message.text.split()[1])
        if not (0 <= n <= 23):
            raise ValueError
        set_setting("moderation_start_hour", n)
        await message.answer(f"✅ Модерация с: {n}:00")
    except:
        await message.answer("❌ /set_moderation_hour 9")


@dp.message(Command("moderate"))
async def cmd_moderate(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    await start_moderation(force=True)


@dp.message(Command("test_cycle"))
async def cmd_test_cycle(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    if get_setting("test_mode", "0") != "1":
        set_setting("orig_start_hour", get_setting("active_start_hour", "9"))
        set_setting("orig_end_hour", get_setting("active_end_hour", "23"))
        set_setting("orig_limit", get_setting("daily_limit", "5"))
        set_setting("orig_mod_start", get_setting("moderation_start_hour", "9"))

    n = now()
    test_start = n.hour
    test_end = min(n.hour + 2, 23)
    if test_end <= test_start:
        test_end = 23

    set_setting("active_start_hour", test_start)
    set_setting("active_end_hour", test_end)
    set_setting("moderation_start_hour", test_start)
    set_setting("daily_limit", 20)
    set_setting("test_mode", "1")

    close_all_pending("expired")
    clear_scheduled()
    reset_scheduled_to_new()

    await message.answer(
        f"🧪 ТЕСТ ВКЛЮЧЁН\n"
        f"🕒 Сейчас: {n.strftime('%H:%M:%S')} MSK\n"
        f"• Тестовое окно: {test_start}:00 – {test_end}:00 (сегодня)\n"
        f"• Лимит: 20\n"
        f"• Слоты идут с интервалом ~2 мин от текущего момента\n\n"
        f"Жми ✅ — мем уйдёт в ближайший слот СЕГОДНЯ.\n"
        f"Выход: /test_cycle_end"
    )
    await asyncio.sleep(1)
    await start_moderation(force=True)


@dp.message(Command("test_cycle_end"))
async def cmd_test_cycle_end(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    if get_setting("test_mode", "0") != "1":
        await message.answer("⚠️ Тестовый режим не активен.")
        return

    orig_start = get_setting("orig_start_hour", "9")
    orig_end = get_setting("orig_end_hour", "23")
    orig_limit = get_setting("orig_limit", "5")
    orig_mod_start = get_setting("orig_mod_start", "9")

    set_setting("active_start_hour", orig_start)
    set_setting("active_end_hour", orig_end)
    set_setting("moderation_start_hour", orig_mod_start)
    set_setting("daily_limit", orig_limit)
    set_setting("test_mode", "0")

    close_all_pending("expired")
    clear_scheduled()
    reset_scheduled_to_new()

    await message.answer(
        f"✅ ТЕСТ ВЫКЛЮЧЕН\n"
        f"• Публикация: {orig_start}:00 – {orig_end}:00\n"
        f"• Модерация с: {orig_mod_start}:00\n"
        f"• Лимит: {orig_limit}/день\n"
        f"• Расписание очищено\n\n"
        f"Запускаю обычную модерацию..."
    )
    await asyncio.sleep(1)
    await start_moderation(force=True)


@dp.message(Command("simulate_new_day"))
async def cmd_simulate_new_day(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    close_all_pending("expired")
    clear_scheduled()
    reset_scheduled_to_new()
    await message.answer("🔄 Сброс. Запускаю модерацию...")
    await asyncio.sleep(1)
    await start_moderation(force=True)


@dp.message(Command("reset_moderation"))
async def cmd_reset_moderation(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    close_all_pending("expired")
    await message.answer("🔄 Сброс модерации...")
    await asyncio.sleep(1)
    await start_moderation(force=True)


# ============================================================
# ПРИЁМ МЕМОВ В ЛИЧКУ
# ============================================================

@dp.message(lambda m: m.chat.type == "private" and (m.photo or m.animation or m.video))
async def handle_suggestion(message: types.Message):
    sender_id = message.from_user.id
    is_admin = sender_id in ADMIN_IDS

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
        filename = f"user_{sender_id}_{uuid.uuid4().hex[:8]}{file_ext}"
        os.makedirs(MEMES_PATH, exist_ok=True)
        file_path = os.path.join(MEMES_PATH, filename)
        await bot.download_file(tg_file.file_path, file_path)
    except Exception as e:
        logger.error(f"Ошибка скачивания: {e}")
        await message.answer("❌ Не смог скачать мем. Попробуй ещё раз.")
        return

    sha1 = compute_sha1(file_path)
    existing = get_meme_by_sha1(sha1)
    if existing:
        await message.answer("⚠️ Такой мем уже есть в базе. Попробуй другой.")
        os.remove(file_path)
        return

    meme_id = add_meme(filename, sha1, file_path, submitted_by=sender_id)
    if not meme_id:
        os.remove(file_path)
        await message.answer("❌ Ошибка сохранения. Попробуй ещё раз.")
        return

    sender_name = message.from_user.full_name or f"id{sender_id}"
    sender_link = f"@{message.from_user.username}" if message.from_user.username else f"id{sender_id}"

    # --- АДМИН: просто в базу ---
    if is_admin:
        stats = get_memes_stats()
        new_count = stats.get("new", 0)
        await message.answer(
            f"✅ Мем добавлен в базу\n"
            f"📦 Всего в пуле: {new_count} новых\n\n"
            f"Он будет предложен на модерацию в обычном порядке."
        )
        return

    # --- ЮЗЕР: на модерацию (только в рабочее время) ---
    if not is_moderation_time():
        await message.answer(
            "✅ Мем сохранён!\n"
            f"🕐 Сейчас не время модерации. Он будет рассмотрен с {get_moderation_start_hour()}:00."
        )
        return

    header = f"📥 Мем от юзера\n👤 {sender_name} ({sender_link})"

    sent_any = False
    for admin_id in ADMIN_IDS:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ В очередь", callback_data=f"sug_approve:{meme_id}"),
                InlineKeyboardButton(text="❌ Пропустить", callback_data=f"sug_reject:{meme_id}")
            ]
        ])
        try:
            if file_ext == ".jpg":
                await bot.send_photo(admin_id, FSInputFile(file_path), caption=header, reply_markup=keyboard)
            elif file_ext == ".gif":
                await bot.send_animation(admin_id, FSInputFile(file_path), caption=header, reply_markup=keyboard)
            else:
                await bot.send_video(admin_id, FSInputFile(file_path), caption=header, reply_markup=keyboard)
            sent_any = True
        except Exception as e:
            logger.error(f"Не отправить админу {admin_id}: {e}")

    if sent_any:
        await message.answer("✅ Мем отправлен на модерацию! Если одобрят — появится в @meme359daily.")
    else:
        await message.answer("⚠️ Не смог отправить мем админам.")


@dp.message(lambda m: m.chat.type == "private" and m.from_user.id not in ADMIN_IDS)
async def handle_user_other(message: types.Message):
    if message.text and message.text.startswith("/"):
        return
    await message.answer(
        "📩 Я принимаю только мемы (фото, GIF, видео).\n"
        "Кинь картинку — и я передам админам."
    )


# ============================================================
# КНОПКИ
# ============================================================

@dp.callback_query()
async def handle_callback(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Не админ!", show_alert=True)
        return

    data = callback.data

    if data.startswith("sug_approve:") or data.startswith("sug_reject:"):
        action, meme_id = data.split(":")
        meme_id = int(meme_id)

        if action == "sug_approve":
            if not CHANNEL_ID:
                await callback.answer("❌ Канал не настроен!", show_alert=True)
                return
            slot_time = get_next_slot_with_gap()
            if not slot_time:
                await callback.answer("❌ Нет слотов на 2 недели вперёд!", show_alert=True)
                return
            add_scheduled_post(meme_id, slot_time.isoformat())
            mark_meme_scheduled(meme_id)
            try:
                await callback.message.edit_caption(
                    caption=f"✅ На {slot_time.strftime('%d.%m %H:%M')} ({callback.from_user.full_name})"
                )
            except:
                pass
            await callback.answer("✅ В очереди!")
        else:
            mark_meme_skipped(meme_id)
            try:
                await callback.message.edit_caption(
                    caption=f"⏭ Пропущено ({callback.from_user.full_name})"
                )
            except:
                pass
            await callback.answer("⏭ Ок")
        return

    action, pending_id = data.split(":")
    pending_id = int(pending_id)

    pending = get_pending_by_id(pending_id)
    if not pending or pending["status"] != "pending":
        await callback.answer("❌ Уже обработан", show_alert=True)
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
            await callback.answer("❌ Нет слотов на 2 недели вперёд!", show_alert=True)
            return

        add_scheduled_post(meme_id, slot_time.isoformat())
        mark_meme_scheduled(meme_id)
        close_pending(pending_id, "approved")

        for op in get_pendings_for_meme(meme_id):
            if op["id"] != pending_id:
                try:
                    await bot.edit_message_caption(
                        chat_id=op["chat_id"],
                        message_id=op["message_id"],
                        caption=f"✅ Уже одобрен ({callback.from_user.full_name})"
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

        slot_date = slot_time.date()
        slot_count = count_scheduled_for_date(slot_date)
        limit = get_limit()

        await bot.send_message(
            callback.from_user.id,
            f"✅ Мем на {slot_time.strftime('%d.%m %H:%M')}\n"
            f"📅 {day_label(slot_date)} ({slot_date.strftime('%d.%m')}): {slot_count}/{limit}"
        )
        await callback.answer("✅ В очереди!")

        await asyncio.sleep(1)
        await start_moderation(force=True)

    elif action == "reject":
        mark_meme_skipped(meme_id)
        close_pending(pending_id, "rejected")

        for op in get_pendings_for_meme(meme_id):
            if op["id"] != pending_id:
                try:
                    await bot.edit_message_caption(
                        chat_id=op["chat_id"],
                        message_id=op["message_id"],
                        caption=f"⏭ Уже пропущен ({callback.from_user.full_name})"
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
    n = now()
    test_mode = get_setting("test_mode", "0") == "1"

    if test_mode:
        today = n.date()
        posts = get_scheduled_for_date(today.isoformat())
        idx = len(posts)
        slot = n + timedelta(minutes=2 * (idx + 1))
        if slot.hour >= end_hour:
            slot = slot.replace(hour=end_hour - 1, minute=59, second=0, microsecond=0)
        if slot < n:
            slot = n + timedelta(minutes=1)
        return slot

    for day_offset in range(0, 14):
        target_date = (n + timedelta(days=day_offset)).date()
        date_str = target_date.isoformat()

        if count_all_for_date(date_str) >= limit:
            continue

        posts = get_scheduled_for_date(date_str)

        day_start = datetime(target_date.year, target_date.month, target_date.day, start_hour, 0)
        day_end = datetime(target_date.year, target_date.month, target_date.day, end_hour, 0)

        if target_date == n.date():
            day_start = max(day_start, n + timedelta(minutes=1))

        if day_start >= day_end:
            continue

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
            mid = points[i] + (points[i+1] - points[i]) / 2
            if mid <= n:
                continue
            if gap > best_gap:
                best_gap = gap
                best_mid = mid

        if best_mid:
            return best_mid

    return None


# ============================================================
# МОДЕРАЦИЯ ИЗ ПАПКИ
# ============================================================

async def start_moderation(force: bool = False):
    if not force and not is_moderation_time():
        return

    if not has_free_slot():
        if force:
            for admin_id in ADMIN_IDS:
                await bot.send_message(admin_id, "📅 Все слоты на 2 недели вперёд забиты. Мемы не предлагаю.")
        return

    scan_memes_folder()
    meme = get_random_unposted_meme()
    if not meme:
        for admin_id in ADMIN_IDS:
            await bot.send_message(admin_id, "📭 Нет новых мемов!")
        return

    if has_active_pending_for_meme(meme["id"]):
        return

    test_mode = get_setting("test_mode", "0") == "1"
    caption = f"📸 {meme['filename']}"
    if test_mode:
        caption = f"🧪 {caption}"

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
            logger.error(f"Ошибка отправки {admin_id}: {e}")


# ============================================================
# ФОНОВЫЕ ЗАДАЧИ
# ============================================================

async def process_scheduled_posts():
    try:
        n = now()

        scheduled = get_pending_scheduled()
        if not scheduled:
            return

        item = scheduled[0]
        try:
            item_time = datetime.fromisoformat(item["scheduled_at"])
        except:
            mark_scheduled_posted(item["id"])
            return

        is_stale = (n - item_time) > timedelta(hours=2)

        if not is_stale:
            start_hour, end_hour = get_active_hours()
            if not (start_hour <= n.hour < end_hour):
                return
            if count_posted_today() >= get_limit():
                return

        if not CHANNEL_ID:
            return

        file_path = get_meme_path(item["meme_id"])
        if not file_path or not os.path.exists(file_path):
            mark_scheduled_posted(item["id"])
            return

        if file_path.endswith(".gif"):
            await bot.send_animation(CHANNEL_ID, FSInputFile(file_path))
        elif file_path.endswith(".mp4"):
            await bot.send_video(CHANNEL_ID, FSInputFile(file_path))
        else:
            await bot.send_photo(CHANNEL_ID, FSInputFile(file_path))
        mark_scheduled_posted(item["id"])
        mark_meme_posted(item["meme_id"])
        logger.info(f"Опубликован мем {item['meme_id']}")
    except Exception as e:
        logger.error(f"process_scheduled_posts ошибка: {e}", exc_info=True)


async def auto_offer():
    try:
        if not is_moderation_time():
            return
        if not has_free_slot():
            return
        for admin_id in ADMIN_IDS:
            from database import get_db
            with get_db() as conn:
                row = conn.execute(
                    "SELECT id FROM pending_moderation WHERE chat_id = ? AND status = 'pending' LIMIT 1",
                    (admin_id,)
                ).fetchone()
                if not row:
                    await start_moderation(force=False)
                    return
    except Exception as e:
        logger.error(f"auto_offer ошибка: {e}", exc_info=True)


async def cleanup_old_pending():
    try:
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
            await asyncio.sleep(1)
            await start_moderation(force=True)
    except Exception as e:
        logger.error(f"cleanup_old_pending ошибка: {e}", exc_info=True)


async def check_permissions():
    try:
        if CHANNEL_ID:
            me = await bot.get_me()
            member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=me.id)
            if member.status in ("administrator", "creator"):
                logger.info(f"✅ Бот админ в {CHANNEL_ID}")
            else:
                logger.error(f"⚠️ Бот НЕ админ в {CHANNEL_ID}")
    except Exception as e:
        logger.error(f"check_permissions ошибка: {e}")


async def main():
    init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    await check_permissions()

    # Фоновые задачи
    scheduler.add_job(process_scheduled_posts, 'interval', minutes=1)
    scheduler.add_job(cleanup_old_pending, 'interval', minutes=5)
    scheduler.add_job(auto_offer, 'interval', minutes=30)

    # Ежедневные задачи (часы — из .env)
    scheduler.add_job(do_scan, 'cron', hour=SCAN_HOUR, minute=0, id='daily_scan')
    scheduler.add_job(do_moderate, 'cron', hour=MODERATE_HOUR, minute=0, id='daily_moderate')

    scheduler.start()

    now_str = now().strftime('%H:%M:%S')
    logger.info("=" * 50)
    logger.info(f"Бот запущен. Время: {now_str} MSK")
    logger.info(f"Расписание: scan в {SCAN_HOUR:02d}:00, moderate в {MODERATE_HOUR:02d}:00")
    logger.info("=" * 50)

    await asyncio.sleep(3)
    if is_moderation_time():
        await start_moderation(force=True)
    else:
        logger.info(f"Вне времени модерации. Ждём {get_moderation_start_hour()}:00")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
