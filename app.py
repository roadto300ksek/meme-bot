import asyncio
import logging
import os
from flask import Flask, request
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Твои файлы
from config import BOT_TOKEN, ADMIN_ID
from database import init_db, get_setting, set_setting, get_random_unposted_meme, create_pending, get_pending, close_pending, mark_meme_posted, mark_meme_skipped
from scanner import scan_memes_folder

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ---------- ХЕНДЛЕРЫ КОМАНД ----------
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Ты не админ!")
        return
    await message.answer("🤖 Бот запущен! Используй /moderate для начала.")

@dp.message(Command("moderate"))
async def cmd_moderate(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    await start_moderation()

# ---------- КНОПКИ ----------
@dp.callback_query()
async def handle_callback(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
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
        await callback.message.edit_caption("✅ Запощено!")
        await callback.answer("✅ Готово!")
        await start_moderation()

    elif action == "reject":
        mark_meme_skipped(meme_id)
        close_pending(pending_id, "rejected")
        await callback.message.edit_caption("⏭ Пропущено")
        await callback.answer("⏭ Ок")
        await start_moderation()

# ---------- ЛОГИКА МОДЕРАЦИИ ----------
async def start_moderation():
    scan_memes_folder()
    meme = get_random_unposted_meme()
    if not meme:
        await bot.send_message(ADMIN_ID, "📭 Нет новых мемов!")
        return

    pending_id = create_pending(meme["id"], ADMIN_ID)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("✅ Запостить", callback_data=f"approve:{pending_id}")],
        [InlineKeyboardButton("❌ Пропустить", callback_data=f"reject:{pending_id}")]
    ])

    with open(meme["file_path"], "rb") as f:
        await bot.send_photo(ADMIN_ID, f, caption=f"📸 {meme['filename']}", reply_markup=keyboard)

# ---------- ВЕБХУК ----------
@app.route("/webhook", methods=["POST"])
async def webhook():
    update = types.Update(**request.json)
    await dp.process_update(update)
    return "OK", 200

@app.route("/")
def index():
    return "Бот работает!", 200

# ---------- ЗАПУСК ----------
if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000)