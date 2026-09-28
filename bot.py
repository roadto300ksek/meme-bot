# OPTIMIZED: все вызовы БД — async. Даты хранятся как Unix ts UTC, отображение — MSK.
import asyncio
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
load_dotenv(os.path.join(BASE_DIR, ".env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(BASE_DIR, "bot.log"), encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

from config import (
    BOT_TOKEN, ADMIN_IDS, CHANNEL_ID, MEMES_PATH,
    DAILY_LIMIT, SCAN_HOUR, MODERATE_HOUR,
)
from database import (
    init_db, get_setting, set_setting, get_random_unposted_meme,
    create_pending, close_pending, mark_meme_posted, mark_meme_skipped,
    mark_meme_scheduled, get_meme_path, add_scheduled_post,
    get_pending_scheduled, mark_scheduled_posted,
    get_scheduled_for_date, count_all_for_date, expire_pending,
    has_active_pending_for_meme, has_any_active_pending,
    auto_cleanup_stale_pendings, get_pending_by_id, get_pendings_for_meme,
    close_all_pending, get_old_pending_grouped, get_memes_stats,
    add_meme, get_meme_by_sha1, count_posted_today,
    clear_scheduled, reset_scheduled_to_new,
)
from scanner import scan_memes_folder
from utils import (
    MSK, now_ts, now_msk, ts_to_msk, ts_to_msk_str,
    compute_sha1,
)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()


# ============================================================
# Helpers
# ============================================================

async def get_limit() -> int:
    return int(await get_setting("daily_limit", str(DAILY_LIMIT)))


async def get_active_hours() -> tuple[int, int]:
    start = int(await get_setting("active_start_hour", "9"))
    end = int(await get_setting("active_end_hour", "23"))
    return start, end


async def get_moderation_start_hour() -> int:
    start, _ = await get_active_hours()
    return int(await get_setting("moderation_start_hour", str(start)))


async def count_scheduled_for_date(d) -> int:
    return await count_all_for_date(d)


def day_label(d) -> str:
    today = now_msk().date()
    if d == today:
        return "Сегодня"
    if d == today + timedelta(days=1):
        return "Завтра"
    return d.strftime("%d.%m")


async def is_moderation_time() -> bool:
    if await get_setting("test_mode", "0") == "1":
        return True
    hour = now_msk().hour
    mod_start = await get_moderation_start_hour()
    _, active_end = await get_active_hours()
    return mod_start <= hour < active_end


async def has_free_slot() -> bool:
    limit = await get_limit()
    today = now_msk().date()
    for offset in range(14):
        if await count_scheduled_for_date(today + timedelta(days=offset)) < limit:
            return True
    return False


async def today_is_full() -> bool:
    limit = await get_limit()
    return await count_scheduled_for_date(now_msk().date()) >= limit


# ============================================================
# Команды
# ============================================================

@dp.message(Command("scan"))
async def cmd_scan(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    await message.answer("🔄 Сканирую папку memes/...")
    count = await do_scan()
    stats = await get_memes_stats()
    stats_text = "\n".join(f"  • {k}: {v}" for k, v in stats.items())
    await message.answer(
        f"✅ Скан завершён\n📂 Обработано файлов: {count}\n\n📦 Мемы в базе:\n{stats_text}"
    )


@dp.message(Command("force"))
async def cmd_force(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    n = await auto_cleanup_stale_pendings()
    await close_all_pending("expired")
    await message.answer(f"🔄 Сброшено pending: {n}\nЗапускаю модерацию заново...")
    await asyncio.sleep(1)
    await start_moderation(force=True)


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    uid = message.from_user.id
    if uid not in ADMIN_IDS:
        await message.answer(
            "👋 Привет!\n\nЭто бот мем-канала @meme359daily.\n"
            "Кинь мне мем (фото, GIF или видео) — передам админам на модерацию."
        )
        return

    limit = await get_limit()
    start, end = await get_active_hours()
    mod_start = await get_moderation_start_hour()
    today = now_msk().date()
    tomorrow = today + timedelta(days=1)
    tc = await count_scheduled_for_date(today)
    tmc = await count_scheduled_for_date(tomorrow)
    posted = await count_posted_today()
    test_mode = await get_setting("test_mode", "0") == "1"
    test_line = "🧪 ТЕСТОВЫЙ РЕЖИМ АКТИВЕН\n" if test_mode else ""
    free_line = "✅ Есть свободные слоты" if await has_free_slot() else "🚫 Все слоты на 14 дней забиты"
    pending_line = "⏳ Есть активный мем на модерации" if await has_any_active_pending() else "✅ Модерация свободна"
    full_line = "🎉 Лимит на сегодня набран" if await today_is_full() else f"⏳ Свободно сегодня: {limit - tc}"

    await message.answer(
        f"{test_line}🤖 Бот запущен!\n"
        f"🕒 Сейчас: {now_msk().strftime('%H:%M:%S')} MSK\n"
        f"📊 Лимит: {limit}/день\n"
        f"🕐 Публикация: {start}:00 – {end}:00\n"
        f"📥 Модерация с: {mod_start}:00\n"
        f"📅 Сегодня ({today.strftime('%d.%m')}): {tc}/{limit} (опубликовано: {posted})\n"
        f"📅 Завтра ({tomorrow.strftime('%d.%m')}): {tmc}/{limit}\n"
        f"{free_line}\n{pending_line}\n{full_line}\n\n"
        f"Автозадачи: scan {SCAN_HOUR:02d}:00, moderate {MODERATE_HOUR:02d}:00\n\n"
        f"/scan /moderate /force /status /set_limit N /set_hours X Y "
        f"/set_moderation_hour N /test_cycle /test_cycle_end"
    )


@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    limit = await get_limit()
    start, end = await get_active_hours()
    mod_start = await get_moderation_start_hour()
    today = now_msk().date()
    tomorrow = today + timedelta(days=1)
    tc = await count_scheduled_for_date(today)
    tmc = await count_scheduled_for_date(tomorrow)
    posted = await count_posted_today()
    stats = await get_memes_stats()
    stats_text = "\n".join(f"  • {k}: {v}" for k, v in stats.items()) or "  (пусто)"
    test_line = "\n🧪 ТЕСТОВЫЙ РЕЖИМ АКТИВЕН\n" if await get_setting("test_mode", "0") == "1" else "\n"
    free_line = "✅ Есть свободные слоты" if await has_free_slot() else "🚫 Все слоты на 14 дней забиты"
    pending_line = "⏳ Есть активный мем на модерации" if await has_any_active_pending() else "✅ Модерация свободна"
    full_line = "🎉 Лимит на сегодня набран" if await today_is_full() else f"⏳ Свободно сегодня: {limit - tc}"

    await message.answer(
        f"📊 Настройки:{test_line}"
        f"🕒 Сейчас: {now_msk().strftime('%H:%M:%S')} MSK\n"
        f"• Лимит: {limit}/день\n"
        f"• Публикация: {start}:00 – {end}:00\n"
        f"• Модерация с: {mod_start}:00\n"
        f"• Канал: {CHANNEL_ID or '—'}\n"
        f"• {free_line}\n• {pending_line}\n• {full_line}\n"
        f"• scan {SCAN_HOUR:02d}:00, moderate {MODERATE_HOUR:02d}:00\n\n"
        f"📅 Сегодня ({today.strftime('%d.%m')}): {tc}/{limit} (опубликовано: {posted})\n"
        f"📅 Завтра ({tomorrow.strftime('%d.%m')}): {tmc}/{limit}\n\n"
        f"📦 Мемы:\n{stats_text}"
    )


@dp.message(Command("set_limit"))
async def cmd_set_limit(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        n = int(message.text.split()[1])
        assert 1 <= n <= 50
        await set_setting("daily_limit", str(n))
        await message.answer(f"✅ Лимит: {n}/день")
    except (IndexError, ValueError, AssertionError):
        await message.answer("❌ /set_limit 5")


@dp.message(Command("set_hours"))
async def cmd_set_hours(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        parts = message.text.split()
        s, e = int(parts[1]), int(parts[2])
        assert 0 <= s < e <= 24
        await set_setting("active_start_hour", str(s))
        await set_setting("active_end_hour", str(e))
        await message.answer(f"✅ Публикация: {s}:00 – {e}:00")
    except (IndexError, ValueError, AssertionError):
        await message.answer("❌ /set_hours 9 23")


@dp.message(Command("set_moderation_hour"))
async def cmd_set_moderation_hour(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        n = int(message.text.split()[1])
        assert 0 <= n <= 23
        await set_setting("moderation_start_hour", str(n))
        await message.answer(f"✅ Модерация с: {n}:00")
    except (IndexError, ValueError, AssertionError):
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

    if await get_setting("test_mode", "0") != "1":
        await set_setting("orig_start_hour", await get_setting("active_start_hour", "9"))
        await set_setting("orig_end_hour", await get_setting("active_end_hour", "23"))
        await set_setting("orig_limit", await get_setting("daily_limit", "5"))
        await set_setting("orig_mod_start", await get_setting("moderation_start_hour", "9"))

    hour = now_msk().hour
    test_end = min(hour + 2, 23) or 23
    await set_setting("active_start_hour", str(hour))
    await set_setting("active_end_hour", str(test_end))
    await set_setting("moderation_start_hour", str(hour))
    await set_setting("daily_limit", "20")
    await set_setting("test_mode", "1")

    await close_all_pending("expired")
    await clear_scheduled()
    await reset_scheduled_to_new()

    await message.answer(
        f"🧪 ТЕСТ ВКЛЮЧЁН\n🕒 Сейчас: {now_msk().strftime('%H:%M:%S')} MSK\n"
        f"• Окно: {hour}:00 – {test_end}:00, лимит 20\nВыход: /test_cycle_end"
    )
    await asyncio.sleep(1)
    await start_moderation(force=True)


@dp.message(Command("test_cycle_end"))
async def cmd_test_cycle_end(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    if await get_setting("test_mode", "0") != "1":
        await message.answer("⚠️ Тестовый режим не активен.")
        return

    for k, orig_key, default in (
        ("active_start_hour", "orig_start_hour", "9"),
        ("active_end_hour", "orig_end_hour", "23"),
        ("moderation_start_hour", "orig_mod_start", "9"),
        ("daily_limit", "orig_limit", "5"),
    ):
        val = await get_setting(orig_key, default)
        await set_setting(k, val)
    await set_setting("test_mode", "0")

    await close_all_pending("expired")
    await clear_scheduled()
    await reset_scheduled_to_new()
    await message.answer("✅ ТЕСТ ВЫКЛЮЧЕН. Запускаю модерацию...")
    await asyncio.sleep(1)
    await start_moderation(force=True)


@dp.message(Command("simulate_new_day"))
async def cmd_simulate_new_day(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    await close_all_pending("expired")
    await clear_scheduled()
    await reset_scheduled_to_new()
    await message.answer("🔄 Сброс. Запускаю модерацию...")
    await asyncio.sleep(1)
    await start_moderation(force=True)


@dp.message(Command("reset_moderation"))
async def cmd_reset_moderation(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    await close_all_pending("expired")
    await message.answer("🔄 Сброс модерации...")
    await asyncio.sleep(1)
    await start_moderation(force=True)


# ============================================================
# Приём мемов в личку
# ============================================================

@dp.message(lambda m: m.chat.type == "private" and (m.photo or m.animation or m.video))
async def handle_suggestion(message: types.Message):
    sender_id = message.from_user.id
    is_admin = sender_id in ADMIN_IDS

    if message.photo:
        file_id, ext = message.photo[-1].file_id, ".jpg"
    elif message.animation:
        file_id, ext = message.animation.file_id, ".gif"
    else:
        file_id, ext = message.video.file_id, ".mp4"

    try:
        tg_file = await bot.get_file(file_id)
        filename = f"user_{sender_id}_{uuid.uuid4().hex[:8]}{ext}"
        os.makedirs(MEMES_PATH, exist_ok=True)
        file_path = os.path.join(MEMES_PATH, filename)
        await bot.download_file(tg_file.file_path, file_path)
    except Exception as e:
        logger.error(f"Ошибка скачивания: {e}")
        await message.answer("❌ Не смог скачать мем. Попробуй ещё раз.")
        return

    try:
        sha1 = await compute_sha1(file_path)
    except OSError as e:
        logger.error(f"SHA1 error: {e}")
        os.remove(file_path)
        await message.answer("❌ Не смог прочитать файл.")
        return

    if await get_meme_by_sha1(sha1):
        await message.answer("⚠️ Такой мем уже есть в базе.")
        os.remove(file_path)
        return

    meme_id = await add_meme(filename, sha1, file_path, submitted_by=sender_id)
    if not meme_id:
        os.remove(file_path)
        await message.answer("❌ Ошибка сохранения.")
        return

    sender_name = message.from_user.full_name or f"id{sender_id}"
    sender_link = f"@{message.from_user.username}" if message.from_user.username else f"id{sender_id}"

    if is_admin:
        stats = await get_memes_stats()
        await message.answer(
            f"✅ Мем добавлен в базу\n📦 Всего в пуле: {stats.get('new', 0)} новых"
        )
        return

    if not await is_moderation_time():
        mod_hour = await get_moderation_start_hour()
        await message.answer(f"✅ Мем сохранён! Рассмотрим с {mod_hour}:00.")
        return

    if await has_any_active_pending():
        await message.answer("✅ Мем сохранён! Сейчас уже есть мем на модерации.")
        return

    header = f"📥 Мем от юзера\n👤 {sender_name} ({sender_link})"
    sent = False
    for admin_id in ADMIN_IDS:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ В очередь", callback_data=f"sug_approve:{meme_id}"),
            InlineKeyboardButton(text="❌ Пропустить", callback_data=f"sug_reject:{meme_id}"),
        ]])
        try:
            if ext == ".jpg":
                await bot.send_photo(admin_id, FSInputFile(file_path), caption=header, reply_markup=kb)
            elif ext == ".gif":
                await bot.send_animation(admin_id, FSInputFile(file_path), caption=header, reply_markup=kb)
            else:
                await bot.send_video(admin_id, FSInputFile(file_path), caption=header, reply_markup=kb)
            sent = True
        except Exception as e:
            logger.error(f"Не отправить админу {admin_id}: {e}")

    await message.answer(
        "✅ Мем отправлен на модерацию!" if sent else "⚠️ Не смог отправить мем админам."
    )


@dp.message(lambda m: m.chat.type == "private" and m.from_user.id not in ADMIN_IDS)
async def handle_user_other(message: types.Message):
    if message.text and message.text.startswith("/"):
        return
    await message.answer("📩 Принимаю только мемы (фото, GIF, видео).")


# ============================================================
# Кнопки
# ============================================================

@dp.callback_query()
async def handle_callback(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Не админ!", show_alert=True)
        return

    data = callback.data

    # --- Юзерская предложка ---
    if data.startswith(("sug_approve:", "sug_reject:")):
        action, meme_id_s = data.split(":")
        meme_id = int(meme_id_s)

        if action == "sug_approve":
            if not CHANNEL_ID:
                await callback.answer("❌ Канал не настроен!", show_alert=True)
                return
            slot_ts = await get_next_slot_ts()
            if not slot_ts:
                await callback.answer("❌ Нет слотов!", show_alert=True)
                return
            await add_scheduled_post(meme_id, slot_ts)
            await mark_meme_scheduled(meme_id)
            try:
                await callback.message.edit_caption(
                    caption=f"✅ На {ts_to_msk_str(slot_ts)} ({callback.from_user.full_name})"
                )
            except Exception:
                pass
            await callback.answer("✅ В очереди!")
        else:
            await mark_meme_skipped(meme_id)
            try:
                await callback.message.edit_caption(
                    caption=f"⏭ Пропущено ({callback.from_user.full_name})"
                )
            except Exception:
                pass
            await callback.answer("⏭ Ок")

        await asyncio.sleep(1)
        if not await has_any_active_pending():
            await start_moderation(force=True)
        return

    # --- Модерация папки ---
    action, pending_id_s = data.split(":")
    pending_id = int(pending_id_s)

    pending = await get_pending_by_id(pending_id)
    if not pending or pending["status"] != "pending":
        await callback.answer("❌ Уже обработан", show_alert=True)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    meme_id = pending["meme_id"]

    if action == "approve":
        if not CHANNEL_ID:
            await callback.answer("❌ Канал не настроен!", show_alert=True)
            return
        slot_ts = await get_next_slot_ts()
        if not slot_ts:
            await callback.answer("❌ Нет слотов!", show_alert=True)
            return

        await add_scheduled_post(meme_id, slot_ts)
        await mark_meme_scheduled(meme_id)
        await close_pending(pending_id, "approved")

        for op in await get_pendings_for_meme(meme_id):
            if op["id"] != pending_id:
                try:
                    await bot.edit_message_caption(
                        chat_id=op["chat_id"], message_id=op["message_id"],
                        caption=f"✅ Уже одобрен ({callback.from_user.full_name})",
                    )
                    await bot.edit_message_reply_markup(
                        chat_id=op["chat_id"], message_id=op["message_id"], reply_markup=None
                    )
                except Exception:
                    pass
                await close_pending(op["id"], "expired")

        try:
            await callback.message.delete()
        except Exception:
            pass

        slot_dt = ts_to_msk(slot_ts)
        slot_count = await count_scheduled_for_date(slot_dt.date())
        limit = await get_limit()
        await bot.send_message(
            callback.from_user.id,
            f"✅ Мем на {slot_dt.strftime('%d.%m %H:%M')}\n"
            f"📅 {day_label(slot_dt.date())} ({slot_dt.strftime('%d.%m')}): {slot_count}/{limit}",
        )
        await callback.answer("✅ В очереди!")

        await asyncio.sleep(1)
        if not await has_any_active_pending():
            await start_moderation(force=True)

    elif action == "reject":
        await mark_meme_skipped(meme_id)
        await close_pending(pending_id, "rejected")

        for op in await get_pendings_for_meme(meme_id):
            if op["id"] != pending_id:
                try:
                    await bot.edit_message_caption(
                        chat_id=op["chat_id"], message_id=op["message_id"],
                        caption=f"⏭ Уже пропущен ({callback.from_user.full_name})",
                    )
                    await bot.edit_message_reply_markup(
                        chat_id=op["chat_id"], message_id=op["message_id"], reply_markup=None
                    )
                except Exception:
                    pass
                await close_pending(op["id"], "expired")

        try:
            await callback.message.delete()
        except Exception:
            pass
        await bot.send_message(callback.from_user.id, "⏭ Пропущено")
        await callback.answer("⏭ Ок")
        await asyncio.sleep(1)
        if not await has_any_active_pending():
            await start_moderation(force=True)


# ============================================================
# Слоты
# ============================================================

async def get_next_slot_ts() -> int | None:
    """Ближайший свободный слот как Unix ts. Или None."""
    limit = await get_limit()
    start_hour, end_hour = await get_active_hours()
    test_mode = await get_setting("test_mode", "0") == "1"
    now = now_msk()

    if test_mode:
        today = now.date()
        posts = await get_scheduled_for_date(today)
        idx = len(posts)
        slot = now + timedelta(minutes=2 * (idx + 1))
        if slot.hour >= end_hour:
            slot = slot.replace(hour=end_hour - 1, minute=59, second=0, microsecond=0)
        if slot <= now:
            slot = now + timedelta(minutes=1)
        return int(slot.timestamp())

    today = now.date()
    for day_offset in range(14):
        target = today + timedelta(days=day_offset)
        if await count_scheduled_for_date(target) >= limit:
            continue

        posts = await get_scheduled_for_date(target)

        start_msk = datetime.combine(target, datetime.min.time()).replace(
            tzinfo=MSK, hour=start_hour
        )
        end_msk = datetime.combine(target, datetime.min.time()).replace(
            tzinfo=MSK, hour=end_hour
        )

        if target == today:
            start_msk = max(start_msk, now + timedelta(minutes=1))
        if start_msk >= end_msk:
            continue

        points = [start_msk, end_msk] + [
            ts_to_msk(p["scheduled_at"]) for p in posts
            if start_msk < ts_to_msk(p["scheduled_at"]) < end_msk
        ]
        points.sort()

        best_gap, best_mid = 0, None
        for i in range(len(points) - 1):
            gap = (points[i + 1] - points[i]).total_seconds()
            mid = points[i] + (points[i + 1] - points[i]) / 2
            if mid <= now:
                continue
            if gap > best_gap:
                best_gap, best_mid = gap, mid

        if best_mid:
            return int(best_mid.timestamp())

    return None


# ============================================================
# Модерация
# ============================================================

async def start_moderation(force: bool = False):
    if not force and not await is_moderation_time():
        return

    if await has_any_active_pending():
        if force:
            for admin_id in ADMIN_IDS:
                await bot.send_message(admin_id, "⏳ Уже есть активный мем на модерации.")
        return

    if await today_is_full():
        if force:
            tc = await count_scheduled_for_date(now_msk().date())
            limit = await get_limit()
            for admin_id in ADMIN_IDS:
                await bot.send_message(
                    admin_id,
                    f"🎉 Лимит на сегодня набран ({tc}/{limit}). "
                    f"Завтра продолжу или /simulate_new_day для сброса.",
                )
        return

    if not await has_free_slot():
        if force:
            for admin_id in ADMIN_IDS:
                await bot.send_message(admin_id, "📅 Все слоты на 14 дней забиты.")
        return

    await scan_memes_folder()
    meme = await get_random_unposted_meme()
    if not meme:
        for admin_id in ADMIN_IDS:
            await bot.send_message(admin_id, "📭 Нет новых мемов!")
        return

    if await has_active_pending_for_meme(meme["id"]):
        return

    test_mode = await get_setting("test_mode", "0") == "1"
    caption = f"🧪 📸 {meme['filename']}" if test_mode else f"📸 {meme['filename']}"

    for admin_id in ADMIN_IDS:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ В очередь", callback_data="placeholder:0"),
            InlineKeyboardButton(text="❌ Пропустить", callback_data="placeholder:0"),
        ]])
        try:
            sent = await bot.send_photo(
                admin_id, FSInputFile(meme["file_path"]),
                caption=caption, reply_markup=kb,
            )
            pending_id = await create_pending(meme["id"], admin_id, sent.message_id)
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ В очередь", callback_data=f"approve:{pending_id}"),
                InlineKeyboardButton(text="❌ Пропустить", callback_data=f"reject:{pending_id}"),
            ]])
            await bot.edit_message_reply_markup(
                chat_id=admin_id, message_id=sent.message_id, reply_markup=kb
            )
        except Exception as e:
            logger.error(f"Ошибка отправки {admin_id}: {e}")


# ============================================================
# Фоновые задачи
# ============================================================

async def process_scheduled_posts():
    try:
        now = now_msk()
        scheduled = await get_pending_scheduled()
        if not scheduled:
            return

        item = scheduled[0]
        item_time = ts_to_msk(item["scheduled_at"])
        is_stale = (now - item_time) > timedelta(hours=2)

        if not is_stale:
            start_hour, end_hour = await get_active_hours()
            if not (start_hour <= now.hour < end_hour):
                return
            if await count_posted_today() >= await get_limit():
                return

        if not CHANNEL_ID:
            return

        file_path = await get_meme_path(item["meme_id"])
        if not file_path or not os.path.exists(file_path):
            await mark_scheduled_posted(item["id"])
            return

        if file_path.endswith(".gif"):
            await bot.send_animation(CHANNEL_ID, FSInputFile(file_path))
        elif file_path.endswith(".mp4"):
            await bot.send_video(CHANNEL_ID, FSInputFile(file_path))
        else:
            await bot.send_photo(CHANNEL_ID, FSInputFile(file_path))
        await mark_scheduled_posted(item["id"])
        await mark_meme_posted(item["meme_id"])
        logger.info(f"Опубликован мем {item['meme_id']}")
    except Exception as e:
        logger.error(f"process_scheduled_posts ошибка: {e}", exc_info=True)


async def cleanup_old_pending():
    try:
        grouped = await get_old_pending_grouped(hours=1)
        for chat_id, items in grouped.items():
            for item in items:
                if item.get("message_id"):
                    try:
                        await bot.delete_message(chat_id=item["chat_id"], message_id=item["message_id"])
                    except Exception:
                        pass
                await expire_pending(item["id"])
        if grouped:
            logger.info(f"Очищено старых pending: {sum(len(v) for v in grouped.values())}")
    except Exception as e:
        logger.error(f"cleanup_old_pending ошибка: {e}", exc_info=True)


async def do_scan():
    count = await scan_memes_folder()
    logger.info(f"[SCAN] Обработано файлов: {count}")
    return count


async def do_moderate():
    logger.info("[MODERATE] Автозапуск модерации (раз в сутки)")
    await start_moderation(force=True)


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
    await init_db()

    cleaned = await auto_cleanup_stale_pendings()
    if cleaned:
        logger.info(f"🧹 Закрыто старых pending: {cleaned}")

    left = await close_all_pending("expired")
    if left:
        logger.info(f"🧹 Закрыто активных pending при старте: {left}")

    await bot.delete_webhook(drop_pending_updates=True)
    await check_permissions()

    scheduler.add_job(process_scheduled_posts, "interval", minutes=1)
    scheduler.add_job(cleanup_old_pending, "interval", minutes=5)
    scheduler.add_job(do_scan, "cron", hour=SCAN_HOUR, minute=0, id="daily_scan")
    scheduler.add_job(do_moderate, "cron", hour=MODERATE_HOUR, minute=0, id="daily_moderate")
    scheduler.start()

    logger.info("=" * 50)
    logger.info(f"Бот запущен. Время: {now_msk().strftime('%H:%M:%S')} MSK")
    logger.info(f"Расписание: scan {SCAN_HOUR:02d}:00, moderate {MODERATE_HOUR:02d}:00")
    logger.info("=" * 50)

    await asyncio.sleep(3)
    if await is_moderation_time() and not await today_is_full():
        await start_moderation(force=True)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
