import os
import hashlib
from datetime import datetime
from config import MEMES_PATH
from database import add_meme, get_setting, set_setting

def compute_sha1(file_path):
    sha1 = hashlib.sha1()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            sha1.update(chunk)
    return sha1.hexdigest()

def scan_memes_folder():
    if not os.path.exists(MEMES_PATH):
        os.makedirs(MEMES_PATH, exist_ok=True)
        return 0

    count = 0
    for filename in os.listdir(MEMES_PATH):
        file_path = os.path.join(MEMES_PATH, filename)
        if not os.path.isfile(file_path):
            continue
        ext = filename.split(".")[-1].lower()
        if ext not in ("jpg", "jpeg", "png", "gif", "mp4", "webm"):
            continue

        sha1 = compute_sha1(file_path)
        add_meme(filename, sha1, file_path)
        count += 1

    set_setting("last_index_date", datetime.now().isoformat())
    return count