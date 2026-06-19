import asyncio
import csv
import os
import random
import sqlite3
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "bot.db"
WORDS_CSV = BASE_DIR / "words.csv"

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
        count = conn.execute("SELECT COUNT(*) FROM words").fetchone()[0]
        if count == 0:
            with open(WORDS_CSV, newline='', encoding='utf-8') as f:
                for row in csv.DictReader(f):
                    conn.execute(
                        "INSERT INTO words (greek, russian, lesson) VALUES (?, ?, ?)",
                        (row["greek"].strip(), row["russian"].strip(), row.get("lesson", "").strip())
                    )


def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📚 Учить слова"), KeyboardButton(text="🔁 Повторить ошибки")],
            [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="➕ Добавить слово")],
        ],
        resize_keyboard=True
    )


def normalize(text: str) -> str:
    return text.lower().replace("ё", "е").strip()


def pick_word(user_id: int, mistakes_only: bool = False):
    with db() as conn:
        if mistakes_only:
            rows = conn.execute("""
                SELECT w.* FROM words w
                JOIN user_progress p ON p.word_id = w.id
                WHERE p.user_id = ? AND p.wrong > p.correct
            """, (user_id,)).fetchall()
            if not rows:
                rows = conn.execute("SELECT * FROM words ORDER BY RANDOM() LIMIT 20").fetchall()
        else:
            rows = conn.execute("""
                SELECT w.*, COALESCE(p.correct,0) AS correct, COALESCE(p.wrong,0) AS wrong
                FROM words w
                LEFT JOIN user_progress p ON p.word_id = w.id AND p.user_id = ?
                ORDER BY (COALESCE(p.wrong,0) - COALESCE(p.correct,0)) DESC, RANDOM()
                LIMIT 20
            """, (user_id,)).fetchall()
        return random.choice(rows) if rows else None


async def ask_word(message: Message, mistakes_only: bool = False):
    user_id = message.from_user.id
    word = pick_word(user_id, mistakes_only)
    if not word:
        await message.answer("Пока нет слов. Добавь их в data/words.csv и перезапусти бота.")
        return

    direction = random.choice(["el_ru", "ru_el"])
    with db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sessions (user_id, word_id, direction) VALUES (?, ?, ?)",
            (user_id, word["id"], direction)
        )

    if direction == "el_ru":
        await message.answer(f"Переведи на русский:\n\n<b>{word['greek']}</b>", parse_mode="HTML")
    else:
        await message.answer(f"Переведи на греческий:\n\n<b>{word['russian']}</b>", parse_mode="HTML")


@dp.message(Command("start"))
async def start(message: Message):
    await message.answer(
        "Привет! Я бот для изучения греческих слов 🇬🇷\n\n"
        "Нажми «📚 Учить слова», и я начну спрашивать карточки.",
        reply_markup=main_keyboard()
    )


@dp.message(Command("quiz"))
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
    with db() as conn:
        row = conn.execute("""
            SELECT COALESCE(SUM(correct),0) AS correct,
                   COALESCE(SUM(wrong),0) AS wrong,
                   COUNT(*) AS studied
            FROM user_progress WHERE user_id = ?
        """, (user_id,)).fetchone()
    total = row["correct"] + row["wrong"]
    accuracy = round(row["correct"] / total * 100) if total else 0
    await message.answer(
        f"📊 Твоя статистика:\n"
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
    lesson = parts[2] if len(parts) > 2 else ""
    with db() as conn:
        conn.execute("INSERT INTO words (greek, russian, lesson) VALUES (?, ?, ?)", (greek, russian, lesson))
    await message.answer(f"Добавила слово: {greek} — {russian}")


@dp.message()
async def answer_check(message: Message):
    user_id = message.from_user.id
    with db() as conn:
        session = conn.execute("SELECT * FROM sessions WHERE user_id = ?", (user_id,)).fetchone()
        if not session:
            await message.answer("Нажми «📚 Учить слова», чтобы начать.", reply_markup=main_keyboard())
            return
        word = conn.execute("SELECT * FROM words WHERE id = ?", (session["word_id"],)).fetchone()
        expected = word["russian"] if session["direction"] == "el_ru" else word["greek"]
        user_answer = normalize(message.text)
        ok = user_answer in normalize(expected) or normalize(expected) in user_answer
        if ok:
            conn.execute("""
                INSERT INTO user_progress (user_id, word_id, correct, wrong, last_seen)
                VALUES (?, ?, 1, 0, datetime('now'))
                ON CONFLICT(user_id, word_id) DO UPDATE SET
                    correct = correct + 1,
                    last_seen = datetime('now')
            """, (user_id, word["id"]))
            await message.answer("✅ Правильно!")
        else:
            conn.execute("""
                INSERT INTO user_progress (user_id, word_id, correct, wrong, last_seen)
                VALUES (?, ?, 0, 1, datetime('now'))
                ON CONFLICT(user_id, word_id) DO UPDATE SET
                    wrong = wrong + 1,
                    last_seen = datetime('now')
            """, (user_id, word["id"]))
            await message.answer(f"❌ Не совсем. Правильно: <b>{expected}</b>", parse_mode="HTML")
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    await ask_word(message)


async def main():
    init_db()
    print("Greek Words Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
