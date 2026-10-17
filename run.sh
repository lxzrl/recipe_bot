#!/usr/bin/env sh
set -e
cd "$(dirname "$0")"
if [ ! -d venv ]; then
    echo "Создаю виртуальное окружение..."
    python3 -m venv venv
    venv/bin/python -m pip install -r requirements.txt
fi
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Заполните файл .env (BOT_TOKEN и ADMIN_IDS) и запустите ./run.sh ещё раз."
    exit 1
fi
exec venv/bin/python -m bot
