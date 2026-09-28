# OPTIMIZED: async SHA-1 через utils, await на БД, убран дубликат compute_sha1
import logging
import os

from config import MEMES_PATH
from database import add_meme, set_setting
from utils import ALLOWED_EXT, compute_sha1, now_ts

logger = logging.getLogger(__name__)


async def scan_memes_folder() -> int:
    if not os.path.exists(MEMES_PATH):
        os.makedirs(MEMES_PATH, exist_ok=True)
        return 0

    count = 0
    for filename in os.listdir(MEMES_PATH):
        file_path = os.path.join(MEMES_PATH, filename)
        if not os.path.isfile(file_path):
            continue
        ext = filename.rsplit(".", 1)[-1].lower()
        if ext not in ALLOWED_EXT:
            continue

        try:
            sha1 = await compute_sha1(file_path)
        except OSError as e:
            logger.warning(f"Не удалось прочитать {file_path}: {e}")
            continue

        await add_meme(filename, sha1, file_path)
        count += 1

    await set_setting("last_index_date", str(now_ts()))
    return count
