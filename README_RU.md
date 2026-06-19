# Telegram-бот для изучения греческих слов

## Что умеет

- спрашивает перевод греческий → русский;
- спрашивает русский → греческий;
- запоминает ошибки;
- показывает статистику;
- позволяет добавлять слова командой `/add`.

## Как создать бота в Telegram

1. Открой Telegram.
2. Найди `@BotFather`.
3. Напиши `/newbot`.
4. Придумай имя и username бота.
5. BotFather даст токен. Его нужно сохранить как `BOT_TOKEN`.

## Как запустить локально

```bash
pip install -r requirements.txt
export BOT_TOKEN="твой_токен"
python bot.py
```

На Windows вместо `export`:

```bash
set BOT_TOKEN=твой_токен
python bot.py
```

## Как загрузить на Railway

1. Создай GitHub-репозиторий и загрузи туда файлы из этой папки.
2. Открой Railway.
3. New Project → Deploy from GitHub repo.
4. Выбери репозиторий.
5. В Variables добавь:
   - `BOT_TOKEN` = токен от BotFather.
6. Start Command: `python bot.py`.
7. После деплоя бот будет работать 24/7.

Railway поддерживает переменные окружения и start command для запуска сервиса.

## Как загрузить на Render

1. Создай GitHub-репозиторий и загрузи туда файлы.
2. Открой Render.
3. New → Background Worker.
4. Подключи репозиторий.
5. Build Command:

```bash
pip install -r requirements.txt
```

6. Start Command:

```bash
python bot.py
```

7. В Environment Variables добавь:
   - `BOT_TOKEN` = токен от BotFather.

Render использует Background Worker для долгих процессов без сайта.

## Как добавить свои слова

В файл `data/words.csv` добавляй строки:

```csv
greek,russian,lesson
γιορτάζω,праздновать,10
στολίζω,украшать,10
```

Или прямо в Telegram:

```text
/add γιορτάζω | праздновать | 10
```

## Важно про базу данных

Сейчас прогресс хранится в файле `bot.db`. Для долгого использования в облаке лучше подключить volume/disk или PostgreSQL, иначе при некоторых redeploy прогресс может сброситься. Сами слова из `data/words.csv` сохранятся в коде.
