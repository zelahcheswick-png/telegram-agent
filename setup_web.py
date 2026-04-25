"""
TransferStats Agent — Локальный веб-сайт настройки.

Запускается на 127.0.0.1:8080.
Cloudflare Tunnel проксирует его наружу для доступа с телефона.
После завершения настройки сайт автоматически закрывается.
"""

import asyncio
import json
import logging
import sys
import os
from pathlib import Path

from aiohttp import web

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("setup_web")

AGENT_DIR = Path(__file__).parent
TEMPLATE_DIR = AGENT_DIR / "templates"
CONFIG_PATH = AGENT_DIR / "agent.ini"
GROUPS_PATH = AGENT_DIR / "agent_groups.json"
_SETUP_TOKEN = ""  # Устанавливается при запуске

# Telethon клиент (создаётся при вводе API credentials)
_telethon_client = None
_session_string = None
_phone = None
_phone_code_hash = None


# ── Handlers ──────────────────────────────────────────────────────────────────

async def handle_index(request: web.Request) -> web.Response:
    html_path = TEMPLATE_DIR / "setup.html"
    if not html_path.exists():
        return web.Response(text="Template not found", status=500)
    return web.Response(
        text=html_path.read_text(encoding="utf-8"),
        content_type="text/html",
    )


async def handle_send_code(request: web.Request) -> web.Response:
    """Отправить код подтверждения на номер телефона."""
    global _telethon_client, _phone, _phone_code_hash

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    api_id = body.get("api_id")
    api_hash = body.get("api_hash", "").strip()
    phone = body.get("phone", "").strip()

    log.info("send-code: api_id=%s phone=%s***", api_id, phone[:4] if phone else "")

    if not all([api_id, api_hash, phone]):
        return web.json_response({"error": "api_id, api_hash, phone required"}, status=400)

    try:
        api_id_int = int(api_id)
    except (ValueError, TypeError):
        return web.json_response({"error": "API ID должен быть числом"}, status=400)

    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        # Закрыть предыдущий клиент если есть
        if _telethon_client:
            try:
                await _telethon_client.disconnect()
            except Exception:
                pass

        _telethon_client = TelegramClient(StringSession(), api_id_int, api_hash)
        await _telethon_client.connect()
        log.info("send-code: connected to Telegram")

        if not await _telethon_client.is_user_authorized():
            result = await _telethon_client.send_code_request(phone)
            _phone = phone
            _phone_code_hash = result.phone_code_hash
            log.info("send-code: code sent to %s", phone)

        return web.json_response({"ok": True})
    except Exception as e:
        import traceback
        traceback.print_exc()
        # Закрыть клиент при ошибке
        if _telethon_client:
            try:
                await _telethon_client.disconnect()
            except Exception:
                pass
            _telethon_client = None
        return web.json_response({"error": str(e)[:200]}, status=500)


async def handle_verify_code(request: web.Request) -> web.Response:
    """Подтвердить код из Telegram."""
    global _telethon_client, _session_string, _phone, _phone_code_hash

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    code = body.get("code", "").strip()
    if not code:
        return web.json_response({"error": "code required"}, status=400)

    if not _telethon_client or not _phone or not _phone_code_hash:
        return web.json_response({"error": "send_code first"}, status=400)

    try:
        await _telethon_client.sign_in(_phone, code, phone_code_hash=_phone_code_hash)
        _session_string = _telethon_client.session.save()
        return web.json_response({"ok": True, "needs_2fa": False})
    except Exception as e:
        error_str = str(e).lower()
        if "password" in error_str or "2fa" in error_str:
            return web.json_response({"ok": False, "needs_2fa": True})
        return web.json_response({"error": str(e)}, status=500)


async def handle_verify_2fa(request: web.Request) -> web.Response:
    """Подтвердить 2FA пароль."""
    global _telethon_client, _session_string

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    password = body.get("password", "")
    if not password:
        return web.json_response({"error": "password required"}, status=400)

    if not _telethon_client:
        return web.json_response({"error": "no client"}, status=400)

    try:
        await _telethon_client.sign_in(password=password)
        _session_string = _telethon_client.session.save()
        log.info("2FA: authenticated OK, session saved")
        return web.json_response({"ok": True})
    except Exception as e:
        log.error("2FA error: %s", e)
        return web.json_response({"error": str(e)[:200]}, status=500)


async def handle_groups(request: web.Request) -> web.Response:
    """Получить список групп пользователя."""
    if not _telethon_client:
        return web.json_response({"error": "authenticate first"}, status=400)

    try:
        groups = []
        async for dialog in _telethon_client.iter_dialogs():
            if dialog.is_group or dialog.is_channel:
                groups.append({
                    "id": dialog.id,
                    "title": dialog.title,
                })
        return web.json_response({"ok": True, "groups": groups})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def handle_save(request: web.Request) -> web.Response:
    """Сохранить конфигурацию и запустить агента."""
    global _telethon_client

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    api_id = body.get("api_id")
    api_hash = body.get("api_hash", "").strip()
    phone = body.get("phone", "").strip()
    token = body.get("token", "").strip()
    endpoint = body.get("endpoint", "").strip()
    selected_groups = body.get("groups", [])

    log.info("save: api_id=%s groups=%d", api_id, len(selected_groups))

    if not all([api_id, api_hash, phone, token, endpoint]):
        log.error("save: missing fields: api_id=%s api_hash=%s phone=%s token=%s endpoint=%s",
                  bool(api_id), bool(api_hash), bool(phone), bool(token), bool(endpoint))
        return web.json_response({"error": "missing fields"}, status=400)

    if not _session_string:
        return web.json_response({"error": "authenticate first"}, status=400)

    # Получить api_key и api_secret с сервера по токену
    api_key = ""
    api_secret = ""
    try:
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=10) as _c:
            resp = await _c.get(f"{endpoint}/api/agent-credentials?token={token}")
            if resp.status_code == 200:
                creds = resp.json()
                api_key = creds.get("api_key", "")
                api_secret = creds.get("api_secret", "")
                log.info("save: got credentials from server")
            else:
                log.warning("save: server returned %d for credentials", resp.status_code)
    except Exception as e:
        log.warning("save: failed to get credentials: %s", e)

    if not api_key or not api_secret:
        return web.json_response({"error": "Failed to get agent credentials from server. Check token."}, status=400)

    # Сохранить agent.ini
    config_content = f"""[telegram]
api_id = {api_id}
api_hash = {api_hash}
phone = {phone}
session = {_session_string}

[agent]
token = {token}
api_key = {api_key}
api_secret = {api_secret}
endpoint = {endpoint}

[groups]
ids = {','.join(str(g) for g in selected_groups)}
"""
    CONFIG_PATH.write_text(config_content)
    os.chmod(CONFIG_PATH, 0o600)

    # Сохранить группы в JSON (для динамического обновления)
    GROUPS_PATH.write_text(json.dumps({"groups": selected_groups}))

    # Запустить systemd сервис (async, не блокирует event loop)
    proc = await asyncio.create_subprocess_exec("systemctl", "enable", "telegram-agent")
    await proc.wait()
    proc = await asyncio.create_subprocess_exec("systemctl", "start", "telegram-agent")
    await proc.wait()

    # Отключить Telethon
    if _telethon_client:
        await _telethon_client.disconnect()

    return web.json_response({"ok": True})


async def handle_finish(request: web.Request) -> web.Response:
    """Корректно завершить работу веб-сервера (только POST)."""
    app = request.app
    asyncio.get_event_loop().call_later(2, lambda: app.loop.stop())
    return web.json_response({"ok": True, "message": "Shutting down..."})


# ── App ───────────────────────────────────────────────────────────────────────

def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_post("/api/send-code", handle_send_code)
    app.router.add_post("/api/verify-code", handle_verify_code)
    app.router.add_post("/api/verify-2fa", handle_verify_2fa)
    app.router.add_get("/api/groups", handle_groups)
    app.router.add_post("/api/save", handle_save)
    app.router.add_post("/api/finish", handle_finish)
    return app


if __name__ == "__main__":
    log.info("Starting setup web server on http://127.0.0.1:8080")
    web.run_app(create_app(), host="127.0.0.1", port=8080, print=None)
