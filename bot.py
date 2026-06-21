import asyncio
import csv
import os
import random
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup

try:
    from gtts import gTTS
except Exception:
    gTTS = None

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "bot.db"
WORDS_CSV = BASE_DIR / "words.csv"
if not WORDS_CSV.exists():
    WORDS_CSV = BASE_DIR / "data" / "words.csv"

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN is not set. Add it in Railway/Render environment variables.")

bot = Bot(TOKEN)
dp = Dispatcher()
SRS_DAYS = [0, 1, 3, 7, 14, 30, 60]


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_column(conn, table, column, definition):
    existing = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db():
    with db() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS words (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            greek TEXT NOT NULL,
            russian TEXT NOT NULL,
            lesson TEXT DEFAULT '',
            lesson_order INTEGER DEFAULT 0,
            lesson_title TEXT DEFAULT '',
            level TEXT DEFAULT 'A2',
            example TEXT DEFAULT ''
        )
        """)
        ensure_column(conn, "words", "lesson", "TEXT DEFAULT ''")
        ensure_column(conn, "words", "lesson_order", "INTEGER DEFAULT 0")
        ensure_column(conn, "words", "lesson_title", "TEXT DEFAULT ''")
        ensure_column(conn, "words", "level", "TEXT DEFAULT 'A2'")
        ensure_column(conn, "words", "example", "TEXT DEFAULT ''")
        conn.execute("UPDATE words SET level = 'A2' WHERE level IS NULL OR level = ''")

        conn.execute("""
        CREATE TABLE IF NOT EXISTS user_progress (
            user_id INTEGER NOT NULL,
            word_id INTEGER NOT NULL,
            correct INTEGER DEFAULT 0,
            wrong INTEGER DEFAULT 0,
            favorite INTEGER DEFAULT 0,
            srs_level INTEGER DEFAULT 0,
            due_at TEXT,
            last_seen TEXT,
            PRIMARY KEY (user_id, word_id)
        )
        """)
        ensure_column(conn, "user_progress", "favorite", "INTEGER DEFAULT 0")
        ensure_column(conn, "user_progress", "srs_level", "INTEGER DEFAULT 0")
        ensure_column(conn, "user_progress", "due_at", "TEXT")
        ensure_column(conn, "user_progress", "last_seen", "TEXT")

        conn.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            current_lesson_order INTEGER DEFAULT 0,
            current_level TEXT DEFAULT '',
            current_lesson_level TEXT DEFAULT '',
            reminder_time TEXT DEFAULT '',
            reminder_enabled INTEGER DEFAULT 0
        )
        """)
        ensure_column(conn, "user_settings", "current_lesson_order", "INTEGER DEFAULT 0")
        ensure_column(conn, "user_settings", "current_level", "TEXT DEFAULT ''")
        ensure_column(conn, "user_settings", "current_lesson_level", "TEXT DEFAULT ''")
        ensure_column(conn, "user_settings", "reminder_time", "TEXT DEFAULT ''")
        ensure_column(conn, "user_settings", "reminder_enabled", "INTEGER DEFAULT 0")

        conn.execute("""CREATE TABLE IF NOT EXISTS sessions (
            user_id INTEGER PRIMARY KEY,
            word_id INTEGER NOT NULL,
            direction TEXT NOT NULL
        )""")
        import_words_from_csv(conn)


def import_words_from_csv(conn):
    if not WORDS_CSV.exists():
        raise FileNotFoundError(f"Cannot find words.csv. Expected: {WORDS_CSV}")

    with open(WORDS_CSV, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            greek = (row.get("greek") or "").strip()
            russian = (row.get("russian") or "").strip()
            if not greek or not russian:
                continue
            raw_order = (row.get("lesson_order") or row.get("lesson") or "").strip()
            try:
                lesson_order = int(raw_order)
            except Exception:
                lesson_order = 0
            lesson_title = (row.get("lesson_title") or "").strip() or (f"Урок {lesson_order}" if lesson_order else "Без урока")
            level = (row.get("level") or "A2").strip().upper()
            example = (row.get("example") or "").strip()

            existing = conn.execute("""
                SELECT id FROM words
                WHERE greek = ? AND russian = ? AND lesson_order = ? AND lesson_title = ?
                LIMIT 1
            """, (greek, russian, lesson_order, lesson_title)).fetchone()
            if existing:
                conn.execute("""
                    UPDATE words SET level = ?, lesson = ?, example = COALESCE(NULLIF(?, ''), example)
                    WHERE id = ?
                """, (level, str(lesson_order), example, existing["id"]))
            else:
                conn.execute("""
                    INSERT INTO words (greek, russian, lesson, lesson_order, lesson_title, level, example)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (greek, russian, str(lesson_order), lesson_order, lesson_title, level, example))


def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🟢 A2"), KeyboardButton(text="🔵 B1"), KeyboardButton(text="🌍 Все уровни")],
            [KeyboardButton(text="📚 Выбрать урок"), KeyboardButton(text="🎓 Все уроки")],
            [KeyboardButton(text="📝 Учить слова"), KeyboardButton(text="🔁 Повторить ошибки")],
            [KeyboardButton(text="⭐ Избранное"), KeyboardButton(text="📊 Статистика")],
            [KeyboardButton(text="➕ Добавить слово"), KeyboardButton(text="⏰ Напоминание")],
        ],
        resize_keyboard=True
    )


def after_answer_keyboard(word_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⭐ В избранное", callback_data=f"fav:{word_id}"),
            InlineKeyboardButton(text="🔊 Озвучить", callback_data=f"voice:{word_id}")
        ],
        [InlineKeyboardButton(text="➡️ Следующее слово", callback_data="next_word")]
    ])


def get_current_level(user_id):
    with db() as conn:
        row = conn.execute("SELECT current_level FROM user_settings WHERE user_id = ?", (user_id,)).fetchone()
    return row["current_level"] if row and row["current_level"] else ""


def get_current_lesson_order(user_id):
    with db() as conn:
        row = conn.execute("SELECT current_lesson_order FROM user_settings WHERE user_id = ?", (user_id,)).fetchone()
    return int(row["current_lesson_order"]) if row and row["current_lesson_order"] else 0


def get_current_lesson_level(user_id):
    with db() as conn:
        row = conn.execute("SELECT current_lesson_level FROM user_settings WHERE user_id = ?", (user_id,)).fetchone()
    return row["current_lesson_level"] if row and row["current_lesson_level"] else ""


def set_current_level(user_id, level):
    with db() as conn:
        conn.execute("""
            INSERT INTO user_settings (user_id, current_level, current_lesson_order, current_lesson_level)
            VALUES (?, ?, 0, '')
            ON CONFLICT(user_id) DO UPDATE SET
                current_level = excluded.current_level,
                current_lesson_order = 0,
                current_lesson_level = ''
        """, (user_id, level))


def set_current_lesson(user_id, level, lesson_order):
    with db() as conn:
        conn.execute("""
            INSERT INTO user_settings (user_id, current_level, current_lesson_level, current_lesson_order)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                current_level = excluded.current_level,
                current_lesson_level = excluded.current_lesson_level,
                current_lesson_order = excluded.current_lesson_order
        """, (user_id, level, level, lesson_order))


def get_lessons(level_filter=""):
    with db() as conn:
        if level_filter:
            return conn.execute("""
                SELECT level, lesson_order, lesson_title, COUNT(*) AS count_words
                FROM words
                WHERE lesson_order > 0 AND level = ?
                GROUP BY level, lesson_order, lesson_title
                ORDER BY level, lesson_order
            """, (level_filter,)).fetchall()
        return conn.execute("""
            SELECT level, lesson_order, lesson_title, COUNT(*) AS count_words
            FROM words
            WHERE lesson_order > 0
            GROUP BY level, lesson_order, lesson_title
            ORDER BY CASE level WHEN 'A2' THEN 1 WHEN 'B1' THEN 2 ELSE 3 END, lesson_order
        """).fetchall()


def lesson_keyboard(user_id=None):
    level_filter = get_current_level(user_id) if user_id else ""
    rows = []
    for lesson in get_lessons(level_filter):
        text = f"{lesson['level']} · {lesson['lesson_title']} ({lesson['count_words']})"
        rows.append([InlineKeyboardButton(
            text=text,
            callback_data=f"lesson:{lesson['level']}:{lesson['lesson_order']}"
        )])
    rows.append([InlineKeyboardButton(text="🎓 Все уроки выбранного уровня", callback_data="lesson:all")])
    rows.append([InlineKeyboardButton(text="🌍 Все уровни", callback_data="level:ALL")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def normalize(text):
    return (text or "").lower().replace("ё", "е").strip()


def get_lesson_title(level, lesson_order):
    if not lesson_order:
        return f"{level}" if level else "Все уровни"
    with db() as conn:
        row = conn.execute("SELECT lesson_title FROM words WHERE level = ? AND lesson_order = ? LIMIT 1", (level, lesson_order)).fetchone()
    return f"{level} · {row['lesson_title']}" if row else f"{level} · Урок {lesson_order}"


def build_where(user_id, alias="w"):
    level = get_current_level(user_id)
    lesson_order = get_current_lesson_order(user_id)
    lesson_level = get_current_lesson_level(user_id) or level
    clauses, params = [], []

    if lesson_order and lesson_level:
        # 80% текущий урок, 20% предыдущие уроки в том же уровне
        use_current = random.random() < 0.8 or lesson_order <= 1
        clauses.append(f"{alias}.level = ?")
        params.append(lesson_level)
        if use_current:
            clauses.append(f"{alias}.lesson_order = ?")
            params.append(lesson_order)
        else:
            previous = list(range(1, lesson_order))
            placeholders = ",".join(["?"] * len(previous))
            clauses.append(f"{alias}.lesson_order IN ({placeholders})")
            params.extend(previous)
    elif level:
        clauses.append(f"{alias}.level = ?")
        params.append(level)

    if not clauses:
        return "", ()
    return " AND " + " AND ".join(clauses) + " ", tuple(params)


def pick_word(user_id, mistakes_only=False, favorites_only=False):
    now = datetime.utcnow().isoformat(timespec="seconds")
    where, params = build_where(user_id, "w")

    with db() as conn:
        if favorites_only:
            rows = conn.execute(f"""
                SELECT w.* FROM words w
                JOIN user_progress p ON p.word_id = w.id
                WHERE p.user_id = ? AND p.favorite = 1 {where}
                ORDER BY RANDOM() LIMIT 50
            """, (user_id, *params)).fetchall()
            return random.choice(rows) if rows else None

        if mistakes_only:
            rows = conn.execute(f"""
                SELECT w.* FROM words w
                JOIN user_progress p ON p.word_id = w.id
                WHERE p.user_id = ? AND p.wrong > p.correct {where}
                ORDER BY RANDOM() LIMIT 50
            """, (user_id, *params)).fetchall()
            return random.choice(rows) if rows else None

        rows = conn.execute(f"""
            SELECT w.*, COALESCE(p.correct,0) AS correct, COALESCE(p.wrong,0) AS wrong
            FROM words w
            LEFT JOIN user_progress p ON p.word_id = w.id AND p.user_id = ?
            WHERE 1=1 {where}
              AND (p.due_at IS NULL OR p.due_at <= ?)
            ORDER BY CASE WHEN p.due_at IS NOT NULL THEN 0 ELSE 1 END,
                     (COALESCE(p.wrong,0) - COALESCE(p.correct,0)) DESC,
                     RANDOM()
            LIMIT 50
        """, (user_id, *params, now)).fetchall()

        if not rows:
            rows = conn.execute(f"SELECT w.* FROM words w WHERE 1=1 {where} ORDER BY RANDOM() LIMIT 50", params).fetchall()
        if not rows:
            rows = conn.execute("SELECT * FROM words ORDER BY RANDOM() LIMIT 50").fetchall()
    return random.choice(rows) if rows else None


def make_options(word, direction):
    field = "russian" if direction == "el_ru" else "greek"
    correct = word[field]
    with db() as conn:
        wrong_rows = conn.execute(f"SELECT {field} AS value FROM words WHERE id != ? ORDER BY RANDOM() LIMIT 30", (word["id"],)).fetchall()
    options = [(correct, True)]
    used = {normalize(correct)}
    for row in wrong_rows:
        value = row["value"]
        if normalize(value) and normalize(value) not in used:
            options.append((value, False)); used.add(normalize(value))
        if len(options) == 4:
            break
    random.shuffle(options)
    return options


def options_keyboard(word, direction):
    buttons = []
    for i, (text, is_correct) in enumerate(make_options(word, direction), start=1):
        buttons.append([InlineKeyboardButton(
            text=f"{i}. {text}",
            callback_data=f"ans:{word['id']}:{direction}:{1 if is_correct else 0}"
        )])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def current_scope_label(user_id):
    level = get_current_level(user_id)
    lesson_order = get_current_lesson_order(user_id)
    lesson_level = get_current_lesson_level(user_id) or level
    if lesson_order and lesson_level:
        return get_lesson_title(lesson_level, lesson_order)
    if level:
        return f"Все уроки {level}"
    return "Все уровни"


async def ask_word(message, mistakes_only=False, favorites_only=False):
    user_id = message.chat.id
    word = pick_word(user_id, mistakes_only=mistakes_only, favorites_only=favorites_only)
    if not word:
        await message.answer("Пока нет слов для этого режима 😊", reply_markup=main_keyboard())
        return
    direction = random.choice(["el_ru", "ru_el"])
    prefix = f"📚 Сейчас: {current_scope_label(user_id)}"
    if direction == "el_ru":
        text = f"{prefix}\n\nПереведи на русский:\n\n<b>{word['greek']}</b>\n\nВыбери правильный вариант:"
    else:
        text = f"{prefix}\n\nПереведи на греческий:\n\n<b>{word['russian']}</b>\n\nВыбери правильный вариант:"
    await message.answer(text, parse_mode="HTML", reply_markup=options_keyboard(word, direction))


def update_srs(conn, user_id, word_id, correct):
    row = conn.execute("SELECT srs_level FROM user_progress WHERE user_id = ? AND word_id = ?", (user_id, word_id)).fetchone()
    old_level = int(row["srs_level"]) if row else 0
    new_level = min(old_level + 1, len(SRS_DAYS) - 1) if correct else 0
    due = datetime.utcnow() + timedelta(days=SRS_DAYS[new_level])
    return new_level, due.isoformat(timespec="seconds")


@dp.message(Command("start"))
async def start(message: Message):
    await message.answer(
        "Привет! Я бот для греческих слов 🇬🇷\n\n"
        "Теперь слова разделены по уровням: A2 и B1.\n"
        "Выбери 🟢 A2, 🔵 B1 или 🌍 Все уровни.",
        reply_markup=main_keyboard()
    )


@dp.message(F.text == "🟢 A2")
async def level_a2(message: Message):
    set_current_level(message.from_user.id, "A2")
    await message.answer("✅ Выбран уровень A2. Теперь выбери урок или нажми «📝 Учить слова».", reply_markup=main_keyboard())


@dp.message(F.text == "🔵 B1")
async def level_b1(message: Message):
    set_current_level(message.from_user.id, "B1")
    await message.answer("✅ Выбран уровень B1. Теперь выбери урок или нажми «📝 Учить слова».", reply_markup=main_keyboard())


@dp.message(F.text == "🌍 Все уровни")
async def level_all(message: Message):
    set_current_level(message.from_user.id, "")
    await message.answer("✅ Режим: все уровни. Начинаем тест.", reply_markup=main_keyboard())
    await ask_word(message)


@dp.callback_query(F.data.startswith("level:"))
async def level_callback(callback: CallbackQuery):
    value = callback.data.split(":", 1)[1]
    set_current_level(callback.from_user.id, "" if value == "ALL" else value)
    await callback.answer()
    await callback.message.answer("✅ Режим: все уровни." if value == "ALL" else f"✅ Выбран уровень {value}.", reply_markup=main_keyboard())
    await ask_word(callback.message)


@dp.message(Command("lessons"))
@dp.message(F.text == "📚 Выбрать урок")
async def lessons(message: Message):
    await message.answer("Выбери урок:", reply_markup=lesson_keyboard(message.from_user.id))


@dp.callback_query(F.data.startswith("lesson:"))
async def choose_lesson(callback: CallbackQuery):
    parts = callback.data.split(":")
    if parts[1] == "all":
        level = get_current_level(callback.from_user.id)
        set_current_level(callback.from_user.id, level)
        await callback.message.answer(f"✅ Режим: все уроки {level or 'всех уровней'}. Начинаем тест.", reply_markup=main_keyboard())
    else:
        level, lesson_order = parts[1], int(parts[2])
        set_current_lesson(callback.from_user.id, level, lesson_order)
        await callback.message.answer(f"✅ Выбран: {get_lesson_title(level, lesson_order)}. Начинаем тест.", reply_markup=main_keyboard())
    await callback.answer()
    await ask_word(callback.message)


@dp.message(Command("all"))
@dp.message(F.text == "🎓 Все уроки")
async def all_lessons(message: Message):
    level = get_current_level(message.from_user.id)
    set_current_level(message.from_user.id, level)
    await message.answer(f"✅ Режим: все уроки {level or 'всех уровней'}. Начинаем тест.", reply_markup=main_keyboard())
    await ask_word(message)


@dp.message(Command("quiz"))
@dp.message(F.text == "📝 Учить слова")
async def quiz(message: Message):
    await ask_word(message)


@dp.message(Command("mistakes"))
@dp.message(F.text == "🔁 Повторить ошибки")
async def mistakes(message: Message):
    await ask_word(message, mistakes_only=True)


@dp.message(Command("favorites"))
@dp.message(F.text == "⭐ Избранное")
async def favorites(message: Message):
    await ask_word(message, favorites_only=True)


@dp.message(Command("stats"))
@dp.message(F.text == "📊 Статистика")
async def stats(message: Message):
    user_id = message.from_user.id
    with db() as conn:
        rows = conn.execute("""
            SELECT w.level, w.lesson_order, w.lesson_title,
                   COUNT(DISTINCT w.id) AS total_words,
                   COUNT(DISTINCT p.word_id) AS studied,
                   COALESCE(SUM(p.correct),0) AS correct,
                   COALESCE(SUM(p.wrong),0) AS wrong,
                   COALESCE(SUM(p.favorite),0) AS favorites
            FROM words w
            LEFT JOIN user_progress p ON p.word_id = w.id AND p.user_id = ?
            WHERE w.lesson_order > 0
            GROUP BY w.level, w.lesson_order, w.lesson_title
            ORDER BY CASE w.level WHEN 'A2' THEN 1 WHEN 'B1' THEN 2 ELSE 3 END, w.lesson_order
        """, (user_id,)).fetchall()
    lines = ["📊 Статистика по урокам:"]
    totals = {"correct":0, "wrong":0, "words":0, "studied":0, "fav":0}
    level_totals = {}
    for r in rows:
        attempts = r["correct"] + r["wrong"]
        acc = round(r["correct"] / attempts * 100) if attempts else 0
        lines.append(f"{r['level']} · {r['lesson_title']}: {r['studied']}/{r['total_words']} слов, {acc}%")
        lt = level_totals.setdefault(r['level'], [0,0])
        lt[0] += r['correct']; lt[1] += r['wrong']
        totals["correct"] += r["correct"]; totals["wrong"] += r["wrong"]
        totals["words"] += r["total_words"]; totals["studied"] += r["studied"]; totals["fav"] += r["favorites"]
    lines.append("")
    lines.append("По уровням:")
    for lvl, (cor, wr) in level_totals.items():
        att = cor + wr
        acc = round(cor / att * 100) if att else 0
        lines.append(f"{lvl}: {acc}% ({cor}✅ / {wr}❌)")
    att = totals["correct"] + totals["wrong"]
    acc = round(totals["correct"] / att * 100) if att else 0
    lines += ["", f"Всего: {totals['studied']}/{totals['words']} слов", f"Точность: {acc}%", f"⭐ Избранное: {totals['fav']}"]
    await message.answer("\n".join(lines))


@dp.message(Command("remind"))
async def remind_command(message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Напиши время так: /remind 20:00")
        return
    time_text = parts[1].strip()
    try:
        datetime.strptime(time_text, "%H:%M")
    except ValueError:
        await message.answer("Формат времени должен быть HH:MM, например /remind 20:00")
        return
    with db() as conn:
        conn.execute("""
            INSERT INTO user_settings (user_id, reminder_time, reminder_enabled)
            VALUES (?, ?, 1)
            ON CONFLICT(user_id) DO UPDATE SET reminder_time = excluded.reminder_time, reminder_enabled = 1
        """, (message.from_user.id, time_text))
    await message.answer(f"✅ Напоминание включено каждый день в {time_text}. Важно: это UTC-время Railway.")


@dp.message(F.text == "⏰ Напоминание")
async def reminder_hint(message: Message):
    await message.answer("Включить: /remind 20:00\nВыключить: /remindoff\n\nВажно: Railway использует UTC-время.")


@dp.message(Command("remindoff"))
async def remind_off(message: Message):
    with db() as conn:
        conn.execute("""
            INSERT INTO user_settings (user_id, reminder_enabled)
            VALUES (?, 0)
            ON CONFLICT(user_id) DO UPDATE SET reminder_enabled = 0
        """, (message.from_user.id,))
    await message.answer("✅ Напоминание выключено.")


@dp.message(F.text == "➕ Добавить слово")
async def add_hint(message: Message):
    await message.answer("Формат:\n/add греческое | русский перевод | уровень | номер урока | пример\n\nПример:\n/add η συνεργασία | сотрудничество | B1 | 1 | Η συνεργασία είναι σημαντική.")


@dp.message(Command("add"))
async def add_word(message: Message):
    raw = message.text.replace("/add", "", 1).strip()
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 2:
        await message.answer("Формат: /add греческое | русский перевод | B1 | 1 | пример")
        return
    greek, russian = parts[0], parts[1]
    current_level = get_current_level(message.from_user.id) or "A2"
    level = parts[2].upper() if len(parts) > 2 and parts[2] else current_level
    try:
        lesson_order = int(parts[3]) if len(parts) > 3 else get_current_lesson_order(message.from_user.id)
    except Exception:
        lesson_order = 0
    example = parts[4] if len(parts) > 4 else ""
    lesson_title = get_lesson_title(level, lesson_order).replace(f"{level} · ", "") if lesson_order else "Без урока"
    with db() as conn:
        conn.execute("""
            INSERT INTO words (greek, russian, lesson, lesson_order, lesson_title, level, example)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (greek, russian, str(lesson_order), lesson_order, lesson_title, level, example))
    await message.answer(f"✅ Добавила слово: {greek} — {russian}\n📚 {level} · {lesson_title}")


@dp.callback_query(F.data.startswith("ans:"))
async def answer_button(callback: CallbackQuery):
    user_id = callback.from_user.id
    _, word_id_raw, direction, correct_raw = callback.data.split(":")
    word_id = int(word_id_raw)
    is_correct = correct_raw == "1"
    with db() as conn:
        word = conn.execute("SELECT * FROM words WHERE id = ?", (word_id,)).fetchone()
        expected = word["russian"] if direction == "el_ru" else word["greek"]
        srs_level, due_at = update_srs(conn, user_id, word_id, is_correct)
        if is_correct:
            conn.execute("""
                INSERT INTO user_progress (user_id, word_id, correct, wrong, srs_level, due_at, last_seen)
                VALUES (?, ?, 1, 0, ?, ?, datetime('now'))
                ON CONFLICT(user_id, word_id) DO UPDATE SET correct = correct + 1, srs_level = excluded.srs_level, due_at = excluded.due_at, last_seen = datetime('now')
            """, (user_id, word_id, srs_level, due_at))
            text = "✅ Правильно!"
        else:
            conn.execute("""
                INSERT INTO user_progress (user_id, word_id, correct, wrong, srs_level, due_at, last_seen)
                VALUES (?, ?, 0, 1, 0, ?, datetime('now'))
                ON CONFLICT(user_id, word_id) DO UPDATE SET wrong = wrong + 1, srs_level = 0, due_at = excluded.due_at, last_seen = datetime('now')
            """, (user_id, word_id, due_at))
            text = f"❌ Не совсем. Правильно: <b>{expected}</b>"
        if word["example"]:
            text += f"\n\nПример:\n<i>{word['example']}</i>"
    await callback.message.answer(text, parse_mode="HTML", reply_markup=after_answer_keyboard(word_id))
    await callback.answer()


@dp.callback_query(F.data == "next_word")
async def next_word_callback(callback: CallbackQuery):
    await callback.answer()
    await ask_word(callback.message)


@dp.callback_query(F.data.startswith("fav:"))
async def favorite_word(callback: CallbackQuery):
    word_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    with db() as conn:
        row = conn.execute("SELECT favorite FROM user_progress WHERE user_id = ? AND word_id = ?", (user_id, word_id)).fetchone()
        new_fav = 0 if row and row["favorite"] else 1
        conn.execute("""
            INSERT INTO user_progress (user_id, word_id, favorite)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, word_id) DO UPDATE SET favorite = excluded.favorite
        """, (user_id, word_id, new_fav))
    await callback.answer("⭐ Добавлено" if new_fav else "Убрано")


@dp.callback_query(F.data.startswith("voice:"))
async def voice_word(callback: CallbackQuery):
    word_id = int(callback.data.split(":")[1])
    with db() as conn:
        word = conn.execute("SELECT greek FROM words WHERE id = ?", (word_id,)).fetchone()
    if not word:
        await callback.answer("Слово не найдено")
        return
    if gTTS is None:
        await callback.message.answer("Озвучка недоступна: gTTS не установлена.")
        return
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
            tmp_path = tmp.name
        gTTS(text=word["greek"], lang="el").save(tmp_path)
        await callback.message.answer_audio(FSInputFile(tmp_path), caption=f"🔊 {word['greek']}")
        os.remove(tmp_path)
    except Exception as e:
        await callback.message.answer(f"Не получилось озвучить слово: {e}")
    await callback.answer()


async def reminder_loop():
    sent_today = set()
    while True:
        now = datetime.utcnow()
        hhmm = now.strftime("%H:%M")
        day = now.strftime("%Y-%m-%d")
        with db() as conn:
            rows = conn.execute("SELECT user_id FROM user_settings WHERE reminder_enabled = 1 AND reminder_time = ?", (hhmm,)).fetchall()
        for row in rows:
            key = (row["user_id"], day, hhmm)
            if key in sent_today:
                continue
            try:
                await bot.send_message(row["user_id"], "⏰ Пора повторить греческие слова 🇬🇷\nНажми «📝 Учить слова».")
                sent_today.add(key)
            except Exception:
                pass
        sent_today = {k for k in sent_today if k[1] == day}
        await asyncio.sleep(60)


@dp.message()
async def fallback(message: Message):
    await message.answer("Выбери действие кнопками 😊", reply_markup=main_keyboard())


async def main():
    init_db()
    print("Greek Words Bot started")
    asyncio.create_task(reminder_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
