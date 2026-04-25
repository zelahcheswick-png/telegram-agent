#!/bin/bash
set -e

# TransferStats Agent — Установщик
# Использование: curl -sSL https://setup.pulsedrive.pro/install | bash -s <TOKEN>

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

TOKEN="$1"
ENDPOINT="https://bot.pulsedrive.pro"
REPO="https://github.com/zelahcheswick-png/telegram-agent.git"
INSTALL_DIR="/opt/telegram-agent"

echo -e "${GREEN}TransferStats Agent — Установщик${NC}"
echo ""

# Проверка root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Ошибка: запустите от root (sudo)${NC}"
    exit 1
fi

# 1. Проверка токена
if [ -z "$TOKEN" ]; then
    echo -e "${RED}Ошибка: укажите токен.${NC}"
    echo "Использование: curl -sSL https://setup.pulsedrive.pro/install | bash -s <TOKEN>"
    exit 1
fi

echo -e "${YELLOW}[1/7] Проверка токена...${NC}"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${ENDPOINT}/health")
if [ "$HTTP_CODE" != "200" ]; then
    echo -e "${RED}Ошибка: сервер недоступен (HTTP $HTTP_CODE)${NC}"
    exit 1
fi
echo -e "${GREEN}  OK${NC}"

# 2. Установка зависимостей
echo -e "${YELLOW}[2/7] Установка зависимостей...${NC}"
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv git curl jq 2>/dev/null
echo -e "${GREEN}  OK${NC}"

# 3. Клонирование репозитория
echo -e "${YELLOW}[3/7] Загрузка кода...${NC}"
if [ -d "$INSTALL_DIR" ]; then
    cd "$INSTALL_DIR"
    git pull --quiet 2>/dev/null || true
else
    git clone --depth=1 --quiet "$REPO" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi
echo -e "${GREEN}  OK${NC}"

# 4. Python venv + зависимости
echo -e "${YELLOW}[4/7] Настройка Python...${NC}"
python3 -m venv venv
source venv/bin/activate
pip install --quiet -r requirements.txt
echo -e "${GREEN}  OK${NC}"

# 5. Компиляция Cython (если возможно)
echo -e "${YELLOW}[5/7] Компиляция Cython...${NC}"
pip install --quiet cython 2>/dev/null
if python core/setup.py build_ext --inplace 2>/dev/null; then
    echo -e "${GREEN}  Скомпилировано${NC}"
else
    echo -e "${YELLOW}  Предупреждение: Cython не скомпилирован, используется fallback${NC}"
fi

# 6. cloudflared
echo -e "${YELLOW}[6/7] Установка cloudflared...${NC}"
if ! command -v cloudflared &> /dev/null; then
    curl -L -s -o /usr/local/bin/cloudflared \
        "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
    chmod +x /usr/local/bin/cloudflared
fi
echo -e "${GREEN}  OK${NC}"

# 7. Systemd service
echo -e "${YELLOW}[7/7] Настройка systemd...${NC}"
cat > /etc/systemd/system/telegram-agent.service << EOF
[Unit]
Description=TransferStats Agent
After=network.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/venv/bin/python agent.py
Restart=always
RestartSec=10
User=root
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
echo -e "${GREEN}  OK${NC}"

# Создать временный agent.ini с токеном
cat > "$INSTALL_DIR/agent.ini" << EOF
[telegram]
api_id =
api_hash =
phone =
session =

[agent]
token = ${TOKEN}
api_key =
api_secret =
endpoint = ${ENDPOINT}

[groups]
ids =
EOF
chmod 600 "$INSTALL_DIR/agent.ini"

echo ""
echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}Установка завершена!${NC}"
echo ""
echo -e "${YELLOW}Запуск локального сайта настройки...${NC}"

# Запустить setup_web.py + cloudflared
cd "$INSTALL_DIR"
source venv/bin/activate

# Запуск setup_web.py в фоне
python setup_web.py &
WEB_PID=$!
sleep 3

# Запуск cloudflared tunnel
cloudflared tunnel --url http://127.0.0.1:8080 > /tmp/cf_tunnel.log 2>&1 &
CF_PID=$!
sleep 8

# Извлечь публичный URL
URL=$(grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" /tmp/cf_tunnel.log | head -1)

if [ -z "$URL" ]; then
    echo -e "${RED}Ошибка: не удалось получить публичный URL${NC}"
    echo "Попробуйте запустить вручную:"
    echo "  cd $INSTALL_DIR"
    echo "  source venv/bin/activate"
    echo "  python setup_web.py &"
    echo "  cloudflared tunnel --url http://127.0.0.1:8080"
    kill $WEB_PID $CF_PID 2>/dev/null
    exit 1
fi

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  Откройте на телефоне или компьютере:       ║${NC}"
echo -e "${GREEN}║                                              ║${NC}"
echo -e "${GREEN}║  ${URL}  ║${NC}"
echo -e "${GREEN}║                                              ║${NC}"
echo -e "${GREEN}║  Ссылка действует 15 минут.                  ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo ""
echo "После завершения настройки сайт закроется автоматически."
echo ""

# Ждать завершения setup_web.py
wait $WEB_PID 2>/dev/null

# Остановить cloudflared
kill $CF_PID 2>/dev/null
echo -e "${GREEN}Настройка завершена. Агент запущен.${NC}"
