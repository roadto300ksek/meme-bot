import asyncio
import logging
from flask import Flask, request, jsonify
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime, timedelta
import os

from config import BOT_TOKEN, ADMIN_ID, CHANNEL_ID, DAILY_LIMIT, RENDER_HOST
from database import (
    init_db, get_setting, set_setting, get_random_unposted_meme,
    create_pending, get_pending, close_pending, mark_meme_posted,
    mark_meme_skipped, add_scheduled_post, get_pending_scheduled,
    mark_scheduled_posted, get_meme_path, reset_daily_counter
)
from scanner import scan_memes_folder

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ------------------ ХЕНДЛЕРЫ КОМАНД ------------------
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Ты не админ!")
        return
    limit = get_setting("daily_limit", DAILY_LIMIT)
    posted = int(get_setting("current_day_posts", 0))
    channel = get_setting("channel_id") or "не настроен"
    await message.answer(
        f"🤖 **Мем-менеджер**\n"
        f"📢 Канал: {channel}\n"
        f"📦 Лимит: {limit} постов/день\n"
        f"📤 Сегодня: {posted}\n\n"
        f"Команды:\n"
        f"/status — статус\n"
        f"/scan — проиндексировать папку\n"
        f"/moderate — запустить модерацию вручную\n"
        f"/set_limit N — установить лимит\n"
        f"/set_channel @канал — установить канал\n"
        f"/skip_all — пропустить всё на сегодня"
    )

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    limit = get_setting("daily_limit", DAILY_LIMIT)
    posted = int(get_setting("current_day_posts", 0))
    channel = get_setting("channel_id") or "не настроен"
    pending = get_pending()
    await message.answer(
        f"📊 **Статус**\n"
        f"📢 Канал: {channel}\n"
        f"📦 Лимит: {limit}\n"
        f"📤 Сегодня: {posted}\n"
        f"⏳ Модерация: {'активна' if pending else 'нет'}"
    )

@dp.message(Command("scan"))
async def cmd_scan(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("🔄 Индексация папки...")
    count = scan_memes_folder()
    await message.answer(f"✅ Проиндексировано {count} новых файлов.")

@dp.message(Command("moderate"))
async def cmd_moderate(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("🚀 Запускаю модерацию...")
    await start_moderation()

@dp.message(Command("set_limit"))
async def cmd_set_limit(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        limit = int(message.text.split()[1])
        set_setting("daily_limit", limit)
        await message.answer(f"✅ Лимит установлен: {limit}")
    except:
        await message.answer("❌ Используй: /set_limit 7")

@dp.message(Command("set_channel"))
async def cmd_set_channel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("❌ Используй: /set_channel @channel")
        return
    channel = parts[1]
    set_setting("channel_id", channel)
    await message.answer(f"✅ Канал установлен: {channel}")

@dp.message(Command("skip_all"))
async def cmd_skip_all(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    pending = get_pending()
    if pending:
        close_pending(pending["id"], "rejected")
        mark_meme_skipped(pending["meme_id"])
        await message.answer("⏭ Всё пропущено на сегодня!")
    else:
        await message.answer("❌ Нет активной модерации.")

# ------------------ ОБРАБОТЧИК КНОПОК ------------------
@dp.callback_query()
async def handle_callback(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Ты не админ!", show_alert=True)
        return

    action, pending_id = callback.data.split(":")
    pending_id = int(pending_id)

    pending = get_pending()
    if not pending or pending["id"] != pending_id:
        await callback.answer("❌ Запись устарела", show_alert=True)
        return

    meme_id = pending["meme_id"]

    if action == "approve":
        channel = get_setting("channel_id")
        if not channel:
            await callback.answer("❌ Канал не настроен!", show_alert=True)
            return
        file_path = get_meme_path(meme_id)
        if not file_path or not os.path.exists(file_path):
            await callback.answer("❌ Файл не найден", show_alert=True)
            return
        try:
            with open(file_path, "rb") as f:
                await bot.send_photo(chat_id=channel, photo=f)
        except Exception as e:
            await callback.answer(f"❌ Ошибка: {e}", show_alert=True)
            return

        mark_meme_posted(meme_id)
        close_pending(pending_id, "approved")
        posted = int(get_setting("current_day_posts", 0))
        set_setting("current_day_posts", posted + 1)
        await callback.message.edit_caption("✅ Запощено!")
        await callback.answer("✅ Мем отправлен в канал!")

        # Проверяем лимит
        limit = int(get_setting("daily_limit", DAILY_LIMIT))
        if posted + 1 >= limit:
            await bot.send_message(ADMIN_ID, "🎉 Дневной лимит достигнут! Завтра продолжу.")
        else:
            await start_moderation()

    elif action == "reject":
        mark_meme_skipped(meme_id)
        close_pending(pending_id, "rejected")
        await callback.message.edit_caption("⏭ Пропущено")
        await callback.answer("⏭ Мем пропущен")
        await start_moderation()

    elif action == "schedule":
        scheduled_at = datetime.now() + timedelta(hours=3)
        add_scheduled_post(meme_id, scheduled_at.isoformat())
        close_pending(pending_id, "scheduled")
        await callback.message.edit_caption(f"⏰ Отложено до {scheduled_at.strftime('%H:%M')}")
        await callback.answer("⏰ Мем отложен")
        await start_moderation()

# ------------------ ОСНОВНАЯ ЛОГИКА МОДЕРАЦИИ ------------------
async def start_moderation():
    """Выбрать случайный мем и отправить на модерацию"""
    # Сначала индексируем папку (подхватываем новые файлы)
    scan_memes_folder()

    # Проверяем лимит
    limit = int(get_setting("daily_limit", DAILY_LIMIT))
    posted = int(get_setting("current_day_posts", 0))
    if posted >= limit:
        await bot.send_message(ADMIN_ID, "🎉 Дневной лимит достигнут!")
        return

    meme = get_random_unposted_meme()
    if not meme:
        await bot.send_message(ADMIN_ID, "📭 Новых мемов нет! Закинь картинки в папку.")
        return

    pending_id = create_pending(meme["id"], ADMIN_ID)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Запостить", callback_data=f"approve:{pending_id}"),
            InlineKeyboardButton(text="❌ Пропустить", callback_data=f"reject:{pending_id}")
        ],
        [
            InlineKeyboardButton(text="⏰ Отложить на 3ч", callback_data=f"schedule:{pending_id}")
        ]
    ])

    try:
        with open(meme["file_path"], "rb") as f:
            await bot.send_photo(
                chat_id=ADMIN_ID,
                photo=f,
                caption=f"📸 {meme['filename']}\nОсталось: {limit - posted} из {limit}",
                reply_markup=keyboard
            )
    except Exception as e:
        await bot.send_message(ADMIN_ID, f"❌ Ошибка отправки: {e}")

# ------------------ ОТЛОЖЕННЫЙ ПОСТИНГ ------------------
async def process_scheduled_posts():
    """Отправляет все отложенные посты, у которых наступило время"""
    scheduled = get_pending_scheduled()
    for item in scheduled:
        channel = get_setting("channel_id")
        if not channel:
            break
        file_path = get_meme_path(item["meme_id"])
        if not file_path or not os.path.exists(file_path):
            mark_scheduled_posted(item["id"])
            continue
        try:
            with open(file_path, "rb") as f:
                await bot.send_photo(chat_id=channel, photo=f)
            mark_scheduled_posted(item["id"])
            mark_meme_posted(item["meme_id"])
        except Exception as e:
            logging.error(f"Ошибка отправки отложенного поста: {e}")

# ------------------ FLASK РОУТЫ ------------------
@app.route("/webhook", methods=["POST"])
async def webhook():
    update = types.Update(**request.json)
    await dp.process_update(update)
    return "OK", 200

@app.route("/", methods=["GET"])
def index():
    return "Meme Bot is running!", 200

@app.route("/health", methods=["GET"])
def health():
    return "OK", 200

@app.route("/trigger_moderation", methods=["GET"])
def trigger_moderation():
    """Роут для вызова извне (например, cron-job.org)"""
    # Сброс счётчика на новый день
    last_date = get_setting("last_index_date")
    if last_date:
        try:
            last = datetime.fromisoformat(last_date)
            if last.date() != datetime.now().date():
                reset_daily_counter()
        except:
            pass

    # Запускаем модерацию и отложенные посты в фоне
    asyncio.create_task(start_moderation())
    asyncio.create_task(process_scheduled_posts())
    return "OK", 200

# ------------------ ЗАПУСК ------------------
if __name__ == "__main__":
    init_db()
    # Устанавливаем вебхук
    webhook_url = f"https://{RENDER_HOST}/webhook"
    asyncio.run(bot.set_webhook(webhook_url, drop_pending_updates=True))
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)