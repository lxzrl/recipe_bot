#!/usr/bin/env bash
# Установка и обновление бота на сервере Ubuntu/Debian.
#   curl -fsSL https://raw.githubusercontent.com/lxzrl/recipe_bot/main/deploy/install.sh -o install.sh
#   sudo bash install.sh
# Повторный запуск обновляет бота до последней версии; база и настройки сохраняются.
set -euo pipefail

REPO_URL="https://github.com/lxzrl/recipe_bot"
APP_DIR="/opt/recipe_bot"
APP_USER="recipebot"
SOCKS_PORT=10808
XRAY_CONFIG="/usr/local/etc/xray/config.json"
ENV_FILE="$APP_DIR/.env"

say() { printf '\n\033[1;32m== %s\033[0m\n' "$*"; }
fail() { printf '\n\033[1;31mОшибка: %s\033[0m\n' "$*" >&2; exit 1; }
ask() { local answer; read -rp "$1" answer </dev/tty; printf '%s' "$answer"; }

[ "$(id -u)" -eq 0 ] || fail "запустите от root: sudo bash install.sh"
command -v apt-get >/dev/null || fail "скрипт рассчитан на Ubuntu или Debian"

say "Устанавливаю системные пакеты"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-venv git curl ca-certificates unzip >/dev/null

python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' \
    || fail "нужен Python 3.10+, выберите на хостинге Ubuntu 22.04 или 24.04"

say "Скачиваю бота в $APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
    git -c safe.directory="$APP_DIR" -C "$APP_DIR" pull --ff-only
else
    git clone -q "$REPO_URL" "$APP_DIR"
fi
id "$APP_USER" >/dev/null 2>&1 || useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"

say "Устанавливаю зависимости Python"
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install -q --upgrade pip
"$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

if [ ! -f "$ENV_FILE" ]; then
    say "Настройка бота"
    token=$(ask "Токен бота от @BotFather: ")
    admins=$(ask "Ваш Telegram ID (от @userinfobot): ")
    [ -n "$token" ] && [ -n "$admins" ] || fail "токен и ID обязательны"
    printf 'BOT_TOKEN=%s\nADMIN_IDS=%s\n' "$token" "$admins" >"$ENV_FILE"
fi

say "Прокси для Telegram"
if grep -q '^TELEGRAM_PROXY=' "$ENV_FILE"; then
    echo "Прокси уже настроен. Чтобы заменить VLESS-ссылку, вставьте новую; Enter — оставить как есть."
else
    echo "Если Telegram с этого сервера недоступен, вставьте ссылку vless://… от вашего VPN."
    echo "Enter — работать без прокси."
fi
vless=$(ask "VLESS-ссылка: ")

if [ -n "$vless" ]; then
    python3 "$APP_DIR/deploy/vless2xray.py" "$vless" --port "$SOCKS_PORT" >/tmp/xray-config.json \
        || fail "не получилось разобрать ссылку"
    if ! command -v xray >/dev/null; then
        say "Устанавливаю Xray"
        bash -c "$(curl -fsSL https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install >/dev/null
    fi
    install -D -m 644 /tmp/xray-config.json "$XRAY_CONFIG"
    rm -f /tmp/xray-config.json
    xray run -test -c "$XRAY_CONFIG" >/dev/null || fail "Xray не принял конфиг"
    systemctl enable -q xray
    systemctl restart xray
    sed -i '/^TELEGRAM_PROXY=/d' "$ENV_FILE"
    echo "TELEGRAM_PROXY=socks5://127.0.0.1:$SOCKS_PORT" >>"$ENV_FILE"
fi

if grep -q '^TELEGRAM_PROXY=' "$ENV_FILE"; then
    say "Проверяю доступ к Telegram через VPN"
    sleep 2
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 \
        --socks5-hostname "127.0.0.1:$SOCKS_PORT" https://api.telegram.org || true)
else
    say "Проверяю доступ к Telegram напрямую"
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 https://api.telegram.org || true)
fi
if [ "$code" = "000" ]; then
    echo "⚠️  Telegram недоступен. Бот будет пытаться подключаться, но, скорее всего, нужна VLESS-ссылка:"
    echo "    запустите sudo bash install.sh ещё раз и вставьте её."
else
    echo "✅ Telegram доступен"
fi

chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 600 "$ENV_FILE"

say "Включаю автозапуск"
cat >/etc/systemd/system/recipe-bot.service <<EOF
[Unit]
Description=Recipe Telegram bot
After=network-online.target xray.service
Wants=network-online.target

[Service]
User=$APP_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python -m bot
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable -q recipe-bot
systemctl restart recipe-bot
sleep 5

if systemctl is-active -q recipe-bot; then
    say "Готово! Бот работает. Откройте его в Telegram и нажмите Start."
    echo "Логи:        journalctl -u recipe-bot -f"
    echo "Перезапуск:  systemctl restart recipe-bot"
    echo "Обновление:  sudo bash $APP_DIR/deploy/install.sh"
else
    journalctl -u recipe-bot -n 30 --no-pager
    fail "бот не запустился, см. журнал выше"
fi
