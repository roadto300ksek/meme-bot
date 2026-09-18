import asyncio
import logging
import os
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile

load_dotenv('/home/linus/meme-bot/.env')

from database import (
    init_db, get_setting, set_setting, get_random_unposted_meme,
    create_pending, get_pending, close_pending, mark_meme_posted,
    mark_meme_skipped, get_meme_path
)
from scanner import scan_memes_folder

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_IDS = [int(x.strip()) for x in os.getenv('ADMIN_IDS', '').split(',') if x.strip()]
CHANNEL_ID = os.getenv('CHANNEL_ID', '')
MEMES_PATH = os.getenv('MEMES_PATH', './memes')
DAILY_LIMIT = int(os.getenv('DAILY_LIMIT', 5))

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ Ты не админ!")
        return
    await message.answer("🤖 Бот запущен! Используй /moderate для начала.")


@dp.message(Command("moderate"))
async def cmd_moderate(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    await start_moderation(message.from_user.id)


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
        file_path = get_meme_path(meme_id)
        if not file_path or not os.path.exists(file_path):
            await callback.answer("❌ Файл не найден", show_alert=True)
            return
        try:
            photo = FSInputFile(file_path)
            await bot.send_photo(chat_id=CHANNEL_ID, photo=photo)
        except Exception as e:
            await callback.answer(f"❌ Ошибка: {e}", show_alert=True)
            return

        mark_meme_posted(meme_id)
        close_pending(pending_id, "approved")

        # Удаляем сообщение с кнопками и шлём подтверждение
        try:
            await callback.message.delete()
        except:
            pass
        await bot.send_message(callback.from_user.id, "✅ Запощено в канал!")
        await callback.answer("✅ Готово!")

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

    pending_id = create_pending(meme["id"], chat_id)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Запостить", callback_data=f"approve:{pending_id}")],
        [InlineKeyboardButton(text="❌ Пропустить", callback_data=f"reject:{pending_id}")]
    ])

    photo = FSInputFile(meme["file_path"])
    await bot.send_photo(
        chat_id,
        photo,
        caption=f"📸 {meme['filename']}",
        reply_markup=keyboard
    )


async def main():
    init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
