# OPTIMIZED: единый модуль утилит — SHA-1 и работа со временем
import asyncio
import hashlib
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")
UTC = timezone.utc

ALLOWED_EXT = {"jpg", "jpeg", "png", "gif", "mp4", "webm"}


def now_ts() -> int:
    """Текущий Unix timestamp (UTC, секунды). Для хранения в БД."""
    return int(datetime.now(UTC).timestamp())


def now_msk() -> datetime:
    """Текущее время в MSK (aware). Для логики и отображения."""
    return datetime.now(UTC).astimezone(MSK)


def ts_to_msk(ts: int | None) -> datetime | None:
    """Unix ts (UTC) -> datetime MSK (aware)."""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=UTC).astimezone(MSK)


def ts_to_msk_str(ts: int | None, fmt: str = "%d.%m %H:%M") -> str:
    """Unix ts -> строка в MSK."""
    dt = ts_to_msk(ts)
    return dt.strftime(fmt) if dt else "—"


def day_bounds_ts(d: date) -> tuple[int, int]:
    """(start_ts, end_ts) для дня в MSK. end_ts — исключая."""
    start_msk = datetime.combine(d, time.min).replace(tzinfo=MSK)
    end_msk = start_msk + timedelta(days=1)
    return int(start_msk.timestamp()), int(end_msk.timestamp())


def _compute_sha1_sync(file_path: str) -> str:
    sha1 = hashlib.sha1()
    with open(file_path, "rb") as f:
        while chunk := f.read(1024 * 1024):  # OPTIMIZED: 1 МБ
            sha1.update(chunk)
    return sha1.hexdigest()


async def compute_sha1(file_path: str) -> str:
    """OPTIMIZED: SHA-1 в отдельном потоке — не блокирует event loop."""
    return await asyncio.to_thread(_compute_sha1_sync, file_path)
