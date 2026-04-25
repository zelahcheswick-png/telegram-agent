# TransferStats Agent

Прокси-агент для пересылки сообщений из Telegram-групп на сервер TransferStats.

## Что делает

Агент подключается к вашему Telegram-аккаунту через Telethon, читает сообщения из выбранных групп и пересылает их на сервер бота через HTTPS с HMAC-подписью.

**Агент НЕ:**
- Отправляет сообщения в группы
- Получает команды с сервера
- Читает личные сообщения
- Изменяет ваш аккаунт

## Установка

```bash
curl -sSL https://setup.pulsedrive.pro/install | bash -s <YOUR_TOKEN>
```

Токен получите в боте: `/indirect_auth`

## Управление

```bash
telegram-agent status       # Статус агента
telegram-agent reconfigure  # Перенастройка групп
telegram-agent stop         # Остановить
telegram-agent start        # Запустить
```

## Логи

```bash
journalctl -u telegram-agent -f
```

## Безопасность

- Код полностью открыт
- Агент только читает и пересылает сообщения
- Все запросы подписаны HMAC-SHA256
- API Key привязан к вашему аккаунту
- Binary hash проверяется при каждом запуске
- Hardware fingerprint привязан к серверу

## Стек

- Python 3.10+
- Telethon (Telegram MTProto)
- httpx (HTTP клиент)
- aiohttp (локальный сайт настройки)
- Cython (компиляция критичных функций)
- Cloudflare Tunnel (временный доступ к сайту)

## Лицензия

MIT
