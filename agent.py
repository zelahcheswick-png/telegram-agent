"""
TransferStats Forwarder Agent

Пересылает сообщения из выбранных Telegram-групп на сервер бота.
НЕ читает/обрабатывает сообщения, НЕ отправляет ничего в Telegram.
Только forward сообщений в ingest endpoint.

Код полностью открыт: https://github.com/zelahcheswick-png/telegram-agent
"""

import asyncio
import configparser
import json
import logging
import os
import sys
import time
from pathlib import Path

import httpx
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# Импорт критичной логики из .so (Cython) или .py (fallback)
try:
    from core import sign_request, compute_integrity, get_hw_fingerprint, handle_challenge
except ImportError:
    # Fallback для разработки (без компиляции Cython)
    from core._fallback import sign_request, compute_integrity, get_hw_fingerprint, handle_challenge

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("agent")

# ── Конфигурация ─────────────────────────────────────────────────────────────

CONFIG_PATH = Path(__file__).parent / "agent.ini"
GROUPS_PATH = Path(__file__).parent / "agent_groups.json"

if not CONFIG_PATH.exists():
    log.error("Config not found: %s", CONFIG_PATH)
    sys.exit(1)

CFG = configparser.ConfigParser()
CFG.read(CONFIG_PATH)

try:
    TG = CFG["telegram"]
    AG = CFG["agent"]
except KeyError as e:
    log.error("Config missing section %s. Run setup first: telegram-agent reconfigure", e)
    sys.exit(1)

API_ID = int(TG["api_id"])
API_HASH = TG["api_hash"]
SESSION_STRING = TG["session"]
PHONE = TG["phone"]
AGENT_TOKEN = AG["token"]
API_KEY = AG["api_key"]
API_SECRET = AG["api_secret"]
ENDPOINT = AG["endpoint"].rstrip("/")

# Загрузить группы из конфига или отдельного файла
if GROUPS_PATH.exists():
    with open(GROUPS_PATH) as f:
        GROUPS = json.load(f).get("groups", [])
else:
    GROUPS = [int(g.strip()) for g in CFG.get("groups", "ids", fallback="").split(",") if g.strip()]

if not GROUPS:
    log.warning("No groups configured. Agent will not forward any messages.")

log.info("Agent starting: %d groups, endpoint=%s", len(GROUPS), ENDPOINT)

# ── Dedup ─────────────────────────────────────────────────────────────────────

_seen: dict[int, float] = {}
_SEEN_TTL = 300  # 5 минут


def _is_duplicate(message_id: int) -> bool:
    now = time.time()
    # Очистка старых
    expired = [k for k, v in _seen.items() if now - v > _SEEN_TTL]
    for k in expired:
        del _seen[k]
    if message_id in _seen:
        return True
    _seen[message_id] = now
    return False


# ── HTTP клиент ───────────────────────────────────────────────────────────────

_http: httpx.AsyncClient | None = None


async def _get_http() -> httpx.AsyncClient:
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(timeout=10)
    return _http


# ── Telethon ──────────────────────────────────────────────────────────────────

client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)


@client.on(events.NewMessage(chats=GROUPS))
async def on_message(event):
    """Обработчик новых сообщений из выбранных групп."""
    if event.message.reply_to:
        return
    text = event.message.text or ""
    if not text.strip():
        return
    if _is_duplicate(event.message.id):
        return

    body = json.dumps({
        "group_id": event.chat_id,
        "message_id": event.message.id,
        "text": text,
        "sender_id": event.sender_id,
    }).encode()

    headers = sign_request(API_KEY, API_SECRET, body)

    try:
        http = await _get_http()
        resp = await http.post(f"{ENDPOINT}/ingest", content=body, headers=headers)
        if resp.status_code == 200:
            log.debug("Forwarded msg=%d group=%d", event.message.id, event.chat_id)
        else:
            log.warning("Ingest error %d: %s", resp.status_code, resp.text[:100])
    except Exception as e:
        log.error("Ingest failed: %s", e)


# ── Heartbeat ─────────────────────────────────────────────────────────────────

async def _handle_reconfigure():
    """Запустить setup_web + cloudflared для перенастройки групп."""
    import subprocess
    import re

    log.info("Launching reconfigure setup...")

    # Запустить setup_web в reconfigure mode
    try:
        subprocess.Popen(
            [sys.executable, "setup_web.py", "--reconfigure", f"--token={AGENT_TOKEN}"],
            cwd=str(AGENT_DIR),
        )
    except Exception as e:
        log.error("Failed to launch setup_web: %s", e)
        return

    # Запустить cloudflared
    try:
        cf_log = open("/tmp/cf_tunnel.log", "w")
        subprocess.Popen(
            ["cloudflared", "tunnel", "--url", "http://127.0.0.1:8080"],
            stdout=cf_log,
            stderr=subprocess.STDOUT,
        )
    except Exception as e:
        log.error("Failed to launch cloudflared: %s", e)
        return

    # Ждать URL (до 20 сек)
    url = None
    for _ in range(20):
        await asyncio.sleep(1)
        try:
            with open("/tmp/cf_tunnel.log") as f:
                match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", f.read())
                if match:
                    url = match.group()
                    break
        except FileNotFoundError:
            pass

    if url:
        # Отправить URL на сервер
        try:
            body = json.dumps({"url": url}).encode()
            req_headers = sign_request(API_KEY, API_SECRET, body)
            http = await _get_http()
            resp = await http.post(f"{ENDPOINT}/api/reconfigure-url", content=body, headers=req_headers)
            if resp.status_code == 200:
                log.info("Reconfigure URL sent: %s", url)
            else:
                log.warning("Reconfigure URL send failed: %d", resp.status_code)
        except Exception as e:
            log.error("Failed to send reconfigure URL: %s", e)
    else:
        log.error("Failed to get cloudflare tunnel URL")


async def heartbeat():
    """Отправка heartbeat каждые 60 секунд."""
    while True:
        try:
            body = json.dumps({"groups_count": len(GROUPS)}).encode()
            headers = sign_request(API_KEY, API_SECRET, body)
            http = await _get_http()
            resp = await http.post(f"{ENDPOINT}/heartbeat", content=body, headers=headers)

            if resp.status_code == 200:
                data = resp.json()
                challenge = data.get("challenge")
                if challenge:
                    log.info("Challenge received, responding...")
                    challenge_body = json.dumps({
                        "challenge_nonce": challenge,
                        "response": handle_challenge(challenge, API_KEY),
                    }).encode()
                    challenge_headers = sign_request(API_KEY, API_SECRET, challenge_body)
                    await http.post(
                        f"{ENDPOINT}/challenge/respond",
                        content=challenge_body,
                        headers=challenge_headers,
                    )

                # Reconfigure requested by server
                if data.get("reconfigure"):
                    log.info("Reconfigure requested by server, launching setup...")
                    asyncio.create_task(_handle_reconfigure())
        except Exception as e:
            log.warning("Heartbeat failed: %s", e)

        await asyncio.sleep(60)


# ── Fingerprint verification ─────────────────────────────────────────────────

async def verify_install():
    """Отправить binary_hash и hw_fingerprint на сервер при первом запуске."""
    try:
        binary_hash = compute_integrity()
        hw_fp = get_hw_fingerprint()
        body = json.dumps({
            "token": AGENT_TOKEN,
            "binary_hash": binary_hash,
            "hw_fingerprint": hw_fp,
        }).encode()
        http = await _get_http()
        resp = await http.post(f"{ENDPOINT}/install/verify", content=body)
        if resp.status_code == 200:
            log.info("Install verified OK")
        elif resp.status_code == 403:
            log.error("Install verification FAILED — binary or hardware changed!")
            log.error("Run: telegram-agent reconfigure")
            sys.exit(1)
        else:
            log.warning("Install verify: %d %s", resp.status_code, resp.text[:100])
    except Exception as e:
        log.warning("Install verify failed: %s", e)


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    await verify_install()
    await client.start(phone=PHONE)
    log.info("Telethon connected, listening on %d groups", len(GROUPS))
    try:
        await asyncio.gather(
            client.run_until_disconnected(),
            heartbeat(),
        )
    except Exception as e:
        log.error("Agent crashed: %s", e)
        raise


if __name__ == "__main__":
    asyncio.run(main())
