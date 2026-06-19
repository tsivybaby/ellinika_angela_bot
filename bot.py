import asyncio
import csv
import os
import random
import sqlite3
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

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


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS words (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            greek TEXT NOT NULL,
            russian TEXT NOT NULL,
            lesson TEXT DEFAULT ''
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS user_progress (
            user_id INTEGER NOT NULL,
            word_id INTEGER NOT NULL,
            correct INTEGER DEFAULT 0,
            wrong INTEGER DEFAULT 0,
            last_seen TEXT,
            PRIMARY KEY (user_id, word_id)
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            user_id INTEGER PRIMARY KEY,
            word_id INTEGER NOT NULL,
            direction TEXT NOT NULL
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            current_lesson TEXT DEFAULT ''
        )
        """)

        count = conn.execute("SELECT COUNT(*) FROM words").fetchone()[0]
        if count == 0:
            if not WORDS_CSV.exists():
                raise FileNotFoundError(f"Cannot find words.csv. Expected: {WORDS_CSV}")
            with open(WORDS_CSV, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    conn.execute(
                        "INSERT INTO words (greek, russian, lesson) VALUES (?, ?, ?)",
                        (
                            row["greek"].strip(),
                            row["russian"].strip(),
                            row.get("lesson", "").strip()
                        )
                    )


def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📚 Выбрать урок"), KeyboardButton(text="🎓 Все уроки")],
            [KeyboardButton(text="📝 Учить слова"), KeyboardButton(text="🔁 Повторить ошибки")],
            [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="➕ Добавить слово")],
        ],
        resize_keyboard=True
    )


def lesson_keyboard():
    rows = []
    for i in range(1, 14, 2):
        row = [InlineKeyboardButton(text=f"Урок {i}", callback_data=f"lesson:{i}")]
        if i + 1 <= 13:
            row.append(InlineKeyboardButton(text=f"Урок {i+1}", callback_data=f"lesson:{i+1}"))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="🎓 Все уроки", callback_data="lesson:all")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def normalize(text: str) -> str:
    return text.lower().replace("ё", "е").strip()


def get_current_lesson(user_id: int) -> str:
    with db() as conn:
        row = conn.execute(
            "SELECT current_lesson FROM user_settings WHERE user_id = ?",
            (user_id,)
        ).fetchone()
    return row["current_lesson"] if row and row["current_lesson"] else ""


def set_current_lesson(user_id: int, lesson: str):
    with db() as conn:
        conn.execute("""
            INSERT INTO user_settings (user_id, current_lesson)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET current_lesson = excluded.current_lesson
        """, (user_id, lesson))


def lesson_where_sql(lesson: str, alias: str = "w"):
    """Фильтр для выбранного урока: 80% текущий урок, 20% предыдущие уроки."""
    if not lesson:
        return "", ()

    try:
        lesson_num = int(lesson)
    except ValueError:
        return "", ()

    # 80% — выбранный урок, 20% — предыдущие уроки.
    use_current = random.random() < 0.8 or lesson_num <= 1
    if use_current:
        return f" AND {alias}.lesson = ? ", (str(lesson_num),)
    else:
        previous = [str(i) for i in range(1, lesson_num)]
        placeholders = ",".join(["?"] * len(previous))
        return f" AND {alias}.lesson IN ({placeholders}) ", tuple(previous)


def pick_word(user_id: int, mistakes_only: bool = False):
    lesson = get_current_lesson(user_id)

    with db() as conn:
        if mistakes_only:
            where, params = lesson_where_sql(lesson, "w")
            rows = conn.execute(f"""
                SELECT w.* FROM words w
                JOIN user_progress p ON p.word_id = w.id
                WHERE p.user_id = ? AND p.wrong > p.correct
                {where}
            """, (user_id, *params)).fetchall()

            if not rows and lesson:
                # Если в выбранном уроке нет ошибок, показываем любые ошибки.
                rows = conn.execute("""
                    SELECT w.* FROM words w
                    JOIN user_progress p ON p.word_id = w.id
                    WHERE p.user_id = ? AND p.wrong > p.correct
                """, (user_id,)).fetchall()

            if not rows:
                rows = conn.execute("SELECT * FROM words ORDER BY RANDOM() LIMIT 20").fetchall()

        else:
            where, params = lesson_where_sql(lesson, "w")
            rows = conn.execute(f"""
                SELECT w.*, COALESCE(p.correct,0) AS correct, COALESCE(p.wrong,0) AS wrong
                FROM words w
                LEFT JOIN user_progress p ON p.word_id = w.id AND p.user_id = ?
                WHERE 1=1
                {where}
                ORDER BY (COALESCE(p.wrong,0) - COALESCE(p.correct,0)) DESC, RANDOM()
                LIMIT 20
            """, (user_id, *params)).fetchall()

            # Если для урока нет слов — fallback на весь словарь.
            if not rows:
                rows = conn.execute("""
                    SELECT w.*, COALESCE(p.correct,0) AS correct, COALESCE(p.wrong,0) AS wrong
                    FROM words w
                    LEFT JOIN user_progress p ON p.word_id = w.id AND p.user_id = ?
                    ORDER BY (COALESCE(p.wrong,0) - COALESCE(p.correct,0)) DESC, RANDOM()
                    LIMIT 20
                """, (user_id,)).fetchall()

        return random.choice(rows) if rows else None


def make_options(word, direction: str):
    field = "russian" if direction == "el_ru" else "greek"
    correct = word[field]

    with db() as conn:
        wrong_rows = conn.execute(
            f"SELECT {field} AS value FROM words WHERE id != ? ORDER BY RANDOM() LIMIT 10",
            (word["id"],)
        ).fetchall()

    options = [(correct, True)]
    used = {normalize(correct)}
    for row in wrong_rows:
        value = row["value"]
        if normalize(value) not in used:
            options.append((value, False))
            used.add(normalize(value))
        if len(options) == 4:
            break

    random.shuffle(options)
    return options


def options_keyboard(word, direction: str):
    buttons = []
    for i, (text, is_correct) in enumerate(make_options(word, direction), start=1):
        callback_data = f"ans:{word['id']}:{direction}:{1 if is_correct else 0}"
        buttons.append([InlineKeyboardButton(text=f"{i}. {text}", callback_data=callback_data)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def lesson_label(user_id: int) -> str:
    lesson = get_current_lesson(user_id)
    return f"Урок {lesson}" if lesson else "Все уроки"


async def ask_word(message: Message, mistakes_only: bool = False):
    user_id = message.from_user.id
    word = pick_word(user_id, mistakes_only)
    if not word:
        await message.answer("Пока нет слов. Добавь их в words.csv или через команду /add.")
        return

    direction = random.choice(["el_ru", "ru_el"])
    with db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sessions (user_id, word_id, direction) VALUES (?, ?, ?)",
            (user_id, word["id"], direction)
        )

    prefix = f"📚 Сейчас: {lesson_label(user_id)}"
    if direction == "el_ru":
        text = f"{prefix}\n\nПереведи на русский:\n\n<b>{word['greek']}</b>\n\nВыбери правильный вариант:"
    else:
        text = f"{prefix}\n\nПереведи на греческий:\n\n<b>{word['russian']}</b>\n\nВыбери правильный вариант:"

    await message.answer(text, parse_mode="HTML", reply_markup=options_keyboard(word, direction))


@dp.message(Command("start"))
async def start(message: Message):
    await message.answer(
        "Привет! Я бот для изучения греческих слов 🇬🇷\n\n"
        "Можно выбрать урок 1–13 или учить все слова сразу.\n"
        "В выбранном уроке я буду давать примерно 80% слов из него и 20% из предыдущих уроков.",
        reply_markup=main_keyboard()
    )


@dp.message(Command("lessons"))
@dp.message(F.text == "📚 Выбрать урок")
async def lessons(message: Message):
    await message.answer("Выбери урок:", reply_markup=lesson_keyboard())


@dp.callback_query(F.data.startswith("lesson:"))
async def choose_lesson(callback: CallbackQuery):
    value = callback.data.split(":", 1)[1]
    if value == "all":
        set_current_lesson(callback.from_user.id, "")
        await callback.message.answer("✅ Режим: все уроки", reply_markup=main_keyboard())
    else:
        set_current_lesson(callback.from_user.id, value)
        await callback.message.answer(
            f"✅ Выбран урок {value}.\n\n"
            f"Теперь в тесте будет примерно 80% слов из урока {value} и 20% из предыдущих уроков.",
            reply_markup=main_keyboard()
        )
    await callback.answer()


@dp.message(Command("all"))
@dp.message(F.text == "🎓 Все уроки")
async def all_lessons(message: Message):
    set_current_lesson(message.from_user.id, "")
    await message.answer("✅ Режим: все уроки", reply_markup=main_keyboard())


@dp.message(Command("quiz"))
@dp.message(F.text == "📝 Учить слова")
@dp.message(F.text == "📚 Учить слова")
async def quiz(message: Message):
    await ask_word(message)


@dp.message(Command("mistakes"))
@dp.message(F.text == "🔁 Повторить ошибки")
async def mistakes(message: Message):
    await ask_word(message, mistakes_only=True)


@dp.message(Command("stats"))
@dp.message(F.text == "📊 Статистика")
async def stats(message: Message):
    user_id = message.from_user.id
    lesson = get_current_lesson(user_id)

    with db() as conn:
        if lesson:
            row = conn.execute("""
                SELECT COALESCE(SUM(p.correct),0) AS correct,
                       COALESCE(SUM(p.wrong),0) AS wrong,
                       COUNT(p.word_id) AS studied
                FROM user_progress p
                JOIN words w ON w.id = p.word_id
                WHERE p.user_id = ? AND w.lesson = ?
            """, (user_id, lesson)).fetchone()
            scope = f"урок {lesson}"
        else:
            row = conn.execute("""
                SELECT COALESCE(SUM(correct),0) AS correct,
                       COALESCE(SUM(wrong),0) AS wrong,
                       COUNT(*) AS studied
                FROM user_progress WHERE user_id = ?
            """, (user_id,)).fetchone()
            scope = "все уроки"

        total_words = conn.execute(
            "SELECT COUNT(*) FROM words WHERE lesson = ?" if lesson else "SELECT COUNT(*) FROM words",
            (lesson,) if lesson else ()
        ).fetchone()[0]

    total = row["correct"] + row["wrong"]
    accuracy = round(row["correct"] / total * 100) if total else 0
    await message.answer(
        f"📊 Статистика ({scope}):\n"
        f"Всего слов в наборе: {total_words}\n"
        f"Изучено слов: {row['studied']}\n"
        f"Правильно: {row['correct']}\n"
        f"Ошибки: {row['wrong']}\n"
        f"Точность: {accuracy}%"
    )


@dp.message(F.text == "➕ Добавить слово")
async def add_hint(message: Message):
    await message.answer(
        "Чтобы добавить слово, напиши так:\n\n"
        "/add греческое | русский перевод | урок\n\n"
        "Пример:\n/add γιορτάζω | праздновать | 10"
    )


@dp.message(Command("add"))
async def add_word(message: Message):
    raw = message.text.replace("/add", "", 1).strip()
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 2:
        await message.answer("Формат: /add γιορτάζω | праздновать | 10")
        return

    greek, russian = parts[0], parts[1]
    lesson = parts[2] if len(parts) > 2 else get_current_lesson(message.from_user.id)

    with db() as conn:
        conn.execute("INSERT INTO words (greek, russian, lesson) VALUES (?, ?, ?)", (greek, russian, lesson))

    lesson_text = f"Урок {lesson}" if lesson else "без урока"
    await message.answer(f"✅ Добавила слово: {greek} — {russian}\n📚 {lesson_text}")


@dp.callback_query(F.data.startswith("ans:"))
async def answer_button(callback: CallbackQuery):
    user_id = callback.from_user.id
    parts = callback.data.split(":")
    word_id = int(parts[1])
    direction = parts[2]
    is_correct = parts[3] == "1"

    with db() as conn:
        word = conn.execute("SELECT * FROM words WHERE id = ?", (word_id,)).fetchone()
        expected = word["russian"] if direction == "el_ru" else word["greek"]

        if is_correct:
            conn.execute("""
                INSERT INTO user_progress (user_id, word_id, correct, wrong, last_seen)
                VALUES (?, ?, 1, 0, datetime('now'))
                ON CONFLICT(user_id, word_id) DO UPDATE SET
                    correct = correct + 1,
                    last_seen = datetime('now')
            """, (user_id, word_id))
            await callback.message.answer("✅ Правильно!")
        else:
            conn.execute("""
                INSERT INTO user_progress (user_id, word_id, correct, wrong, last_seen)
                VALUES (?, ?, 0, 1, datetime('now'))
                ON CONFLICT(user_id, word_id) DO UPDATE SET
                    wrong = wrong + 1,
                    last_seen = datetime('now')
            """, (user_id, word_id))
            await callback.message.answer(f"❌ Не совсем. Правильно: <b>{expected}</b>", parse_mode="HTML")

        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    await callback.answer()
    await ask_word(callback.message)


@dp.message()
async def answer_check(message: Message):
    await message.answer(
        "Теперь отвечай кнопками 1 из 4 😊\n"
        "Нажми «📚 Выбрать урок» или «📝 Учить слова».",
        reply_markup=main_keyboard()
    )


async def main():
    init_db()
    print("Greek Words Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
