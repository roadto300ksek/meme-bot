import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
CHANNEL_ID = os.getenv("CHANNEL_ID", "")
MEMES_PATH = os.getenv("MEMES_PATH", "./memes")
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", 5))
RENDER_HOST = os.getenv("RENDER_EXTERNAL_HOSTNAME", "localhost")