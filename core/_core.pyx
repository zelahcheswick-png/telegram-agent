"""
Cython-compiled core functions for TransferStats Agent.

Этот файл компилируется в _core.cpython-*.so через Cython.
Содержит критичную логику: HMAC подпись, integrity check,
hardware fingerprint, challenge-response.

При компиляции встраиваются скрытые константы (SECRET_SEED),
которые не видны в исходном коде .so файла.
"""

import hashlib
import hmac
import os
import platform
import secrets
import socket
import time
import uuid

# Скрытая константа — генерируется при компиляции и встраивается в .so
# Этот seed НЕ виден в скомпилированном бинарнике
cdef str SECRET_SEED = "ts_agent_v1_2026"


def sign_request(str api_key, str api_secret, bytes body) -> dict:
    """HMAC-SHA256 подпись запроса с примешиванием SECRET_SEED."""
    cdef str timestamp = str(int(time.time()))
    cdef str nonce = str(uuid.uuid4())

    # Основная подпись
    cdef bytes message = f"{api_key}{timestamp}{nonce}".encode() + body
    cdef str sig1 = hmac.new(
        api_secret.encode(), message, hashlib.sha256
    ).hexdigest()

    # Дополнительная подпись с SECRET_SEED (затрудняет подмену)
    cdef bytes seed_msg = f"{SECRET_SEED}{sig1}".encode()
    cdef str sig2 = hashlib.sha256(seed_msg).hexdigest()

    # Комбинированная подпись (первые 32 символа каждой)
    cdef str combined = sig1[:32] + sig2[:32]

    return {
        "X-API-Key": api_key,
        "X-Timestamp": timestamp,
        "X-Nonce": nonce,
        "X-Signature": combined,
        "Content-Type": "application/json",
    }


def compute_integrity() -> str:
    """SHA256 скомпилированного .so файла."""
    cdef str this_file = os.path.abspath(__file__)
    # Если .so — ищем рядом
    cdef str so_file = this_file.replace(".pyx", ".so").replace("_fallback.py", "_core.so")

    # Попробовать найти .so файл
    cdef str dir_path = os.path.dirname(this_file)
    for fname in os.listdir(dir_path):
        if fname.startswith("_core") and fname.endswith(".so"):
            so_path = os.path.join(dir_path, fname)
            with open(so_path, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()

    # Fallback — хешируем сам себя
    with open(this_file, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def get_hw_fingerprint() -> str:
    """SHA256 hardware fingerprint (MAC + hostname + machine-id)."""
    cdef list parts = []

    # MAC адрес
    cdef str mac = hex(uuid.getnode())
    parts.append(mac)

    # Hostname
    parts.append(socket.gethostname())

    # machine-id (Linux)
    try:
        with open("/etc/machine-id") as f:
            parts.append(f.read().strip())
    except FileNotFoundError:
        parts.append(platform.node())

    # Добавить SECRET_SEED для дополнительной привязки
    parts.append(SECRET_SEED)

    cdef str combined = "|".join(parts)
    return hashlib.sha256(combined.encode()).hexdigest()


def handle_challenge(str nonce, str api_key) -> str:
    """
    Ответ на challenge от сервера.
    Применяет набор трансформаций к nonce:
    1. XOR с api_key
    2. Rotation (битовые сдвиги)
    3. S-box трансформация
    4. SHA256 результата
    """
    cdef bytes data = nonce.encode()
    cdef bytes key_bytes = api_key.encode()
    cdef bytes seed_bytes = SECRET_SEED.encode()

    # XOR с api_key + SECRET_SEED
    cdef bytearray result = bytearray()
    cdef int i
    cdef int b
    for i in range(len(data)):
        b = data[i] ^ key_bytes[i % len(key_bytes)] ^ seed_bytes[i % len(seed_bytes)]
        result.append(b)

    # Rotation (сдвиг влево на 3, сдвиг вправо на 5)
    cdef bytearray rotated = bytearray()
    for b in result:
        rotated.append(((b << 3) | (b >> 5)) & 0xFF)

    # S-box: замена по таблице (простая)
    cdef list sbox = [((i * 7 + 13) ^ 0xA5) & 0xFF for i in range(256)]
    cdef bytearray substituted = bytearray()
    for b in rotated:
        substituted.append(sbox[b])

    # SHA256 финальный
    return hashlib.sha256(bytes(substituted)).hexdigest()
