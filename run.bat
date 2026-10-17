@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist venv (
    echo Создаю виртуальное окружение...
    python -m venv venv || goto :error
    venv\Scripts\python -m pip install -r requirements.txt || goto :error
)
if not exist .env (
    copy .env.example .env >nul
    echo Заполните файл .env ^(BOT_TOKEN и ADMIN_IDS^) и запустите run.bat ещё раз.
    notepad .env
    pause
    exit /b 1
)
venv\Scripts\python -m bot
pause
exit /b 0
:error
echo Не получилось установить зависимости. Проверьте, что установлен Python 3.10+.
pause
exit /b 1
