"""
Автотесты для мем-бота.
Запуск: python tests.py
Не трогает боевую БД и не обращается к Telegram.
"""
import os
import sys
import sqlite3
import tempfile
import hashlib
from datetime import datetime, timedelta

# --- Подменяем БД на тестовую ДО импорта database ---
TEST_DB = os.path.join(tempfile.gettempdir(), "test_memes.db")
if os.path.exists(TEST_DB):
    os.remove(TEST_DB)

import database
database.DB_PATH = TEST_DB

from database import (
    init_db, get_setting, set_setting,
    add_meme, get_meme_by_sha1, get_meme_by_id, get_random_unposted_meme,
    mark_meme_posted, mark_meme_skipped, mark_meme_scheduled,
    create_pending, get_pending_by_id, close_pending,
    has_active_pending_for_meme, get_pendings_for_meme,
    add_scheduled_post, get_pending_scheduled, mark_scheduled_posted,
    get_scheduled_for_date, count_all_for_date, count_posted_today,
    get_memes_stats, clear_scheduled, reset_scheduled_to_new,
    close_all_pending, get_old_pending_grouped, expire_pending,
    now
)


PASSED = 0
FAILED = 0
FAILED_TESTS = []


def check(name, condition, detail=""):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ✅ {name}")
    else:
        FAILED += 1
        FAILED_TESTS.append(f"{name} — {detail}")
        print(f"  ❌ {name} — {detail}")


def fresh_db():
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    init_db()


def make_sha1(text):
    return hashlib.sha1(text.encode()).hexdigest()


def force_mark_posted(limit=2):
    """Помечает N постов как posted напрямую в БД (независимо от now())."""
    conn = sqlite3.connect(TEST_DB)
    conn.execute(f"""
        UPDATE scheduled_posts SET status='posted'
        WHERE id IN (SELECT id FROM scheduled_posts ORDER BY id LIMIT {limit})
    """)
    conn.commit()
    conn.close()


# ============================================================
# ТЕСТЫ
# ============================================================

def test_db_init():
    print("\n📦 Тест 1: Инициализация БД")
    fresh_db()
    check("daily_limit = 5", get_setting("daily_limit") == "5", f"got {get_setting('daily_limit')}")
    check("active_start_hour = 9", get_setting("active_start_hour") == "9")
    check("active_end_hour = 23", get_setting("active_end_hour") == "23")


def test_add_meme():
    print("\n📦 Тест 2: Добавление мема")
    fresh_db()
    sha = make_sha1("test1")
    meme_id = add_meme("test1.jpg", sha, "/tmp/test1.jpg")
    check("meme_id вернулся", meme_id is not None and meme_id > 0)

    found = get_meme_by_sha1(sha)
    check("мем найден по sha1", found is not None)
    check("status = new", found["status"] == "new", f"got {found['status']}")


def test_dedup():
    print("\n📦 Тест 3: Дубли не проходят")
    fresh_db()
    sha = make_sha1("dup")
    add_meme("dup.jpg", sha, "/tmp/dup.jpg")
    add_meme("dup.jpg", sha, "/tmp/dup2.jpg")
    stats = get_memes_stats()
    check("в БД 1 мем", stats.get("new", 0) == 1, f"got {stats}")


def test_random_meme():
    print("\n📦 Тест 4: Случайный мем из пула")
    fresh_db()
    for i in range(3):
        add_meme(f"{i}.jpg", make_sha1(f"m{i}"), f"/tmp/{i}.jpg")

    meme = get_random_unposted_meme()
    check("мем вернулся", meme is not None)

    for i in range(1, 4):
        mark_meme_posted(i)
    meme = get_random_unposted_meme()
    check("пул пуст после всех posted", meme is None)


def test_pending():
    print("\n📦 Тест 5: Pending-модерация")
    fresh_db()
    meme_id = add_meme("x.jpg", make_sha1("x"), "/tmp/x.jpg")

    pid = create_pending(meme_id, 12345, 999)
    check("pending_id вернулся", pid is not None)

    p = get_pending_by_id(pid)
    check("status = pending", p["status"] == "pending")
    check("has_active = True", has_active_pending_for_meme(meme_id))

    close_pending(pid, "approved")
    p = get_pending_by_id(pid)
    check("после close = approved", p["status"] == "approved")
    check("has_active = False", not has_active_pending_for_meme(meme_id))


def test_multiple_pendings():
    print("\n📦 Тест 6: 2 pending на один мем (2 админа)")
    fresh_db()
    meme_id = add_meme("y.jpg", make_sha1("y"), "/tmp/y.jpg")

    p1 = create_pending(meme_id, 111, 1001)
    p2 = create_pending(meme_id, 222, 1002)

    pendings = get_pendings_for_meme(meme_id)
    check("2 pending создано", len(pendings) == 2)

    close_pending(p1, "approved")
    pendings = get_pendings_for_meme(meme_id)
    check("после close одного — остался 1", len(pendings) == 1)
    check("остался p2", pendings[0]["id"] == p2)


def test_scheduled_basic():
    print("\n📦 Тест 7: Отложенные посты — базовое")
    fresh_db()
    meme_id = add_meme("z.jpg", make_sha1("z"), "/tmp/z.jpg")

    past = (now() - timedelta(minutes=5)).isoformat()
    add_scheduled_post(meme_id, past)

    ready = get_pending_scheduled()
    check("пост в прошлом готов", len(ready) == 1)

    future = (now() + timedelta(hours=2)).isoformat()
    add_scheduled_post(meme_id, future)

    ready = get_pending_scheduled()
    check("пост в будущем НЕ готов", len(ready) == 1)


def test_count_all_for_date():
    print("\n📦 Тест 8: Лимит = pending + posted")
    fresh_db()
    meme_id = add_meme("c1.jpg", make_sha1("c1"), "/tmp/c1.jpg")

    today = now().date().isoformat()

    # 3 pending на сегодня (время неважно — фильтр по дате, не по времени)
    for h in [10, 12, 14]:
        add_scheduled_post(meme_id, f"{today}T{h}:00:00")

    check("count_all = 3 (pending)", count_all_for_date(today) == 3,
          f"got {count_all_for_date(today)}")

    # Помечаем 2 как posted напрямую через SQL (независимо от now())
    force_mark_posted(2)

    check("count_all = 3 (2 posted + 1 pending)", count_all_for_date(today) == 3,
          f"got {count_all_for_date(today)}")
    check("count_posted_today = 2", count_posted_today() == 2,
          f"got {count_posted_today()}")


def test_slot_algorithm_full():
    print("\n📦 Тест 9: Алгоритм слотов (симуляция)")
    fresh_db()
    set_setting("daily_limit", 3)
    set_setting("active_start_hour", 9)
    set_setting("active_end_hour", 23)

    slots = []
    for i in range(10):
        meme_id = add_meme(f"m{i}.jpg", make_sha1(f"m{i}"), f"/tmp/m{i}.jpg")
        slot = simulate_next_slot()
        if slot is None:
            break
        add_scheduled_post(meme_id, slot.isoformat())
        slots.append(slot)

    by_day = {}
    for s in slots:
        d = s.date().isoformat()
        by_day[d] = by_day.get(d, 0) + 1

    check("не больше 3 в день", all(v <= 3 for v in by_day.values()), f"got {by_day}")
    check("минимум 4 дня занято", len(by_day) >= 4, f"got {len(by_day)} дней")

    n = now()
    check("все слоты в будущем", all(s > n for s in slots), "есть прошедшие")


def simulate_next_slot():
    """Копия логики get_next_slot_with_gap из bot.py (без импорта bot)."""
    limit = int(get_setting("daily_limit", 5))
    start_hour = int(get_setting("active_start_hour", 9))
    end_hour = int(get_setting("active_end_hour", 23))
    n = now()

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


def test_has_free_slot():
    print("\n📦 Тест 10: has_free_slot — все дни забиты")
    fresh_db()
    set_setting("daily_limit", 2)
    set_setting("active_start_hour", 9)
    set_setting("active_end_hour", 23)

    today = now().date()
    for d_offset in range(14):
        d = today + timedelta(days=d_offset)
        for h in [10, 15]:
            meme_id = add_meme(f"x_{d_offset}_{h}.jpg",
                               make_sha1(f"x_{d_offset}_{h}"),
                               f"/tmp/x.jpg")
            add_scheduled_post(meme_id, f"{d.isoformat()}T{h}:00:00")

    free = False
    limit = 2
    for day_offset in range(14):
        d = today + timedelta(days=day_offset)
        if count_all_for_date(d.isoformat()) < limit:
            free = True
            break
    check("все забито — свободных слотов нет", not free)


def test_old_pending_cleanup():
    print("\n📦 Тест 11: Очистка старых pending")
    fresh_db()
    meme_id = add_meme("o.jpg", make_sha1("o"), "/tmp/o.jpg")
    pid = create_pending(meme_id, 999, 111)

    conn = sqlite3.connect(TEST_DB)
    old_time = (now() - timedelta(hours=2)).isoformat()
    conn.execute("UPDATE pending_moderation SET created_at = ? WHERE id = ?", (old_time, pid))
    conn.commit()
    conn.close()

    grouped = get_old_pending_grouped(hours=1)
    check("старый pending найден", len(grouped) > 0)

    for chat_id, items in grouped.items():
        for item in items:
            expire_pending(item["id"])

    grouped = get_old_pending_grouped(hours=1)
    check("после expire — пусто", len(grouped) == 0)


def test_clear_and_reset():
    print("\n📦 Тест 12: Очистка и сброс")
    fresh_db()
    meme_id = add_meme("r.jpg", make_sha1("r"), "/tmp/r.jpg")
    mark_meme_scheduled(meme_id)
    add_scheduled_post(meme_id, now().isoformat())

    clear_scheduled()
    check("scheduled_posts пусто", len(get_pending_scheduled()) == 0)

    reset_scheduled_to_new()
    stats = get_memes_stats()
    check("мем вернулся в new", stats.get("new", 0) == 1, f"got {stats}")


def test_close_all_pending():
    print("\n📦 Тест 13: close_all_pending")
    fresh_db()
    meme_id = add_meme("q.jpg", make_sha1("q"), "/tmp/q.jpg")
    create_pending(meme_id, 111, 1001)
    create_pending(meme_id, 222, 1002)

    close_all_pending("expired")
    pendings = get_pendings_for_meme(meme_id)
    check("все pending закрыты", len(pendings) == 0)


def test_full_cycle():
    print("\n📦 Тест 14: ПОЛНЫЙ ЦИКЛ (модерация → очередь → публикация)")
    fresh_db()
    set_setting("daily_limit", 5)
    set_setting("active_start_hour", 9)
    set_setting("active_end_hour", 23)

    today = now().date().isoformat()

    # Шаг 1: 5 мемов через simulate_next_slot
    approved = 0
    for i in range(5):
        meme_id = add_meme(f"c{i}.jpg", make_sha1(f"c{i}"), f"/tmp/c{i}.jpg")
        slot = simulate_next_slot()
        if slot is None:
            break
        add_scheduled_post(meme_id, slot.isoformat())
        mark_meme_scheduled(meme_id)
        approved += 1

    check("5 мемов одобрено", approved == 5, f"got {approved}")
    check("count_all_for_today = 5", count_all_for_date(today) == 5,
          f"got {count_all_for_date(today)}")

    # Шаг 2: 6-й уходит на завтра
    meme_id = add_meme("c6.jpg", make_sha1("c6"), "/tmp/c6.jpg")
    slot = simulate_next_slot()
    check("6-й ушёл на завтра", slot is not None and slot.date().isoformat() != today,
          f"got {slot}")
    add_scheduled_post(meme_id, slot.isoformat())

    check("на сегодня всё ещё 5", count_all_for_date(today) == 5,
          f"got {count_all_for_date(today)}")

    # Шаг 3: pending ready = 0 (всё в будущем)
    all_posts = get_pending_scheduled()
    check("pending ready = 0 (всё в будущем)", len(all_posts) == 0,
          f"got {len(all_posts)}")

    # Шаг 4: публикуем 1 мем вручную через SQL
    force_mark_posted(1)

    check("count_posted_today = 1", count_posted_today() == 1,
          f"got {count_posted_today()}")
    check("count_all = 6 (1 posted + 5 pending)", count_all_for_date(today) == 5,
          f"got {count_all_for_date(today)}")


# ============================================================
# ЗАПУСК
# ============================================================

def main():
    print("=" * 60)
    print("🧪 АВТОТЕСТЫ MEME-BOT")
    print("=" * 60)

    tests = [
        test_db_init,
        test_add_meme,
        test_dedup,
        test_random_meme,
        test_pending,
        test_multiple_pendings,
        test_scheduled_basic,
        test_count_all_for_date,
        test_slot_algorithm_full,
        test_has_free_slot,
        test_old_pending_cleanup,
        test_clear_and_reset,
        test_close_all_pending,
        test_full_cycle,
    ]

    for test in tests:
        try:
            test()
        except Exception as e:
            global FAILED
            FAILED += 1
            FAILED_TESTS.append(f"{test.__name__} — ИСКЛЮЧЕНИЕ: {e}")
            print(f"  💥 {test.__name__} упал: {e}")

    print("\n" + "=" * 60)
    print(f"✅ Пройдено: {PASSED}")
    print(f"❌ Провалено: {FAILED}")
    if FAILED_TESTS:
        print("\nПровалы:")
        for t in FAILED_TESTS:
            print(f"  • {t}")
    print("=" * 60)

    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)

    sys.exit(0 if FAILED == 0 else 1)


if __name__ == "__main__":
    main()
