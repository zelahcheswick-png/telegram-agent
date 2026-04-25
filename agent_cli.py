"""
TransferStats Agent — CLI управления.

Использование:
    telegram-agent status       — статус агента
    telegram-agent reconfigure  — перенастройка групп (открывает локальный сайт)
    telegram-agent stop         — остановить агента
    telegram-agent start        — запустить агента
"""

import os
import subprocess
import sys

INSTALL_DIR = "/opt/telegram-agent"
SERVICE_NAME = "telegram-agent"


def status():
    """Показать статус агента."""
    result = subprocess.run(
        ["systemctl", "is-active", SERVICE_NAME],
        capture_output=True, text=True,
    )
    state = result.stdout.strip()
    if state == "active":
        print("Агент: работает")
    else:
        print(f"Агент: {state}")

    # Показать логи
    subprocess.run(["journalctl", "-u", SERVICE_NAME, "-n", "10", "--no-pager"])


def reconfigure():
    """Перенастройка групп через локальный сайт."""
    print("Остановка агента...")
    subprocess.run(["systemctl", "stop", SERVICE_NAME])

    print("Запуск локального сайта настройки...")
    os.chdir(INSTALL_DIR)

    # Запустить setup_web.py
    subprocess.Popen(
        [f"{INSTALL_DIR}/venv/bin/python", "setup_web.py"],
        cwd=INSTALL_DIR,
    )

    # Запустить cloudflared
    subprocess.Popen(
        ["cloudflared", "tunnel", "--url", "http://127.0.0.1:8080"],
        stdout=open("/tmp/cf_tunnel.log", "w"),
        stderr=subprocess.STDOUT,
    )

    import time
    time.sleep(8)

    # Извлечь URL
    try:
        with open("/tmp/cf_tunnel.log") as f:
            import re
            match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", f.read())
            if match:
                print(f"\nОткройте на телефоне:\n  {match.group()}\n")
            else:
                print("Ошибка: не удалось получить URL")
    except FileNotFoundError:
        print("Ошибка: лог cloudflared не найден")


def stop():
    """Остановить агента."""
    subprocess.run(["systemctl", "stop", SERVICE_NAME])
    print("Агент остановлен")


def start():
    """Запустить агента."""
    subprocess.run(["systemctl", "start", SERVICE_NAME])
    print("Агент запущен")


COMMANDS = {
    "status": status,
    "reconfigure": reconfigure,
    "stop": stop,
    "start": start,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("Использование: telegram-agent <команда>")
        print("Команды:", ", ".join(COMMANDS.keys()))
        sys.exit(1)
    COMMANDS[sys.argv[1]]()


if __name__ == "__main__":
    main()
