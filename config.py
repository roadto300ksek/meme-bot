# FIXED: строгая валидация типов при старте
import os
from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    val = os.getenv(name)
    if not val or not val.strip():
        raise RuntimeError(f"Environment variable {name} is required")
    return val.strip()


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as e:
        raise RuntimeError(f"Env {name} must be int, got {raw!r}") from e


def _ids(name: str) -> list[int]:
    raw = os.getenv(name, "")
    result: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            result.append(int(part))
        except ValueError as e:
            raise RuntimeError(f"{name} contains non-int: {part!r}") from e
    return result


BOT_TOKEN: str = _require("BOT_TOKEN")
ADMIN_IDS: list[int] = _ids("ADMIN_IDS")
CHANNEL_ID: str = os.getenv("CHANNEL_ID", "")
MEMES_PATH: str = os.getenv("MEMES_PATH", "./memes")
DAILY_LIMIT: int = _int("DAILY_LIMIT", 5)
SCAN_HOUR: int = _int("SCAN_HOUR", 0)
MODERATE_HOUR: int = _int("MODERATE_HOUR", 9)
RENDER_HOST: str = os.getenv("RENDER_EXTERNAL_HOSTNAME", "localhost")
