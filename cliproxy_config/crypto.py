"""A fixed, bounded envelope: scrypt and authenticated AES-256-GCM."""
from __future__ import annotations

import base64
import binascii
import json
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


class ConfigError(Exception):
    """A message suitable for display without credentials or raw exceptions."""


MAX_FILE_BYTES = 12 * 1024 * 1024
MAX_PLAIN_BYTES = 8 * 1024 * 1024
_HEADER = {"format": "cliproxy-config", "version": 1,
           "cipher": "aes-256-gcm", "kdf": "scrypt-n32768-r8-p1"}
_AAD = json.dumps(_HEADER, sort_keys=True, separators=(",", ":")).encode("ascii")


def parse_document(content: str | bytes) -> dict:
    """Read the common JSON container without trusting a filename extension."""
    if not isinstance(content, (str, bytes)):
        raise ConfigError("配置内容必须是文本。")
    try:
        size = len(content.encode("utf-8")) if isinstance(content, str) else len(content)
        if size > MAX_FILE_BYTES:
            raise ConfigError("配置文件过大。")
        value = json.loads(content.lstrip("\ufeff") if isinstance(content, str) else content.decode("utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise ConfigError("配置不是有效的 JSON 文件或文本。") from None


def is_encrypted(content: str | bytes) -> bool:
    return parse_document(content).get("format") == _HEADER["format"]


def encode_document(payload: dict, password: str | None = None) -> str:
    if password is not None:
        return encrypt(payload, password)
    content = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if len(content.encode("utf-8")) > MAX_PLAIN_BYTES:
        raise ConfigError("配置内容过大，无法导出。")
    return content


def decode_document(content: str | bytes, password: str | None = None) -> dict:
    value = parse_document(content)
    if value.get("format") == _HEADER["format"]:
        if password is None:
            raise ConfigError("该配置已加密，请输入解密密码。")
        return decrypt(json.dumps(value), password)
    if value.get("kind") not in ("connection", "backup"):
        raise ConfigError("不是本程序导出的模型节点或配置备份。")
    return value


def _key(password: str, salt: bytes) -> bytes:
    if not isinstance(password, str) or not password or len(password.encode("utf-8")) > 1024:
        raise ConfigError("请输入密码，密码长度不能超过 1024 字节。")
    return Scrypt(salt=salt, length=32, n=32768, r=8, p=1).derive(password.encode("utf-8"))


def encrypt(payload: dict, password: str) -> str:
    if len(password) < 8:
        raise ConfigError("加密密码至少需要 8 个字符，建议使用 12 个以上字符。")
    plain = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(plain) > MAX_PLAIN_BYTES:
        raise ConfigError("配置内容过大，无法导出。")
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    cipher = AESGCM(_key(password, salt)).encrypt(nonce, plain, _AAD)
    envelope = dict(_HEADER)
    envelope.update(salt=base64.b64encode(salt).decode("ascii"),
                    nonce=base64.b64encode(nonce).decode("ascii"),
                    data=base64.b64encode(cipher).decode("ascii"))
    return json.dumps(envelope, ensure_ascii=True, separators=(",", ":")) + "\n"


def decrypt(content: str | bytes, password: str) -> dict:
    if not isinstance(content, (str, bytes)) or len(content) > MAX_FILE_BYTES:
        raise ConfigError("配置文件过大或格式不正确。")
    try:
        envelope = json.loads(content)
        if not isinstance(envelope, dict) or set(envelope) != set(_HEADER) | {"salt", "nonce", "data"}:
            raise ValueError()
        if any(envelope[k] != v or type(envelope[k]) is not type(v) for k, v in _HEADER.items()):
            raise ValueError()
        salt, nonce, cipher = (base64.b64decode(envelope[k], validate=True) for k in ("salt", "nonce", "data"))
        if len(salt) != 16 or len(nonce) != 12 or len(cipher) < 16:
            raise ValueError()
    except (ValueError, TypeError, KeyError, UnicodeError, binascii.Error, RecursionError):
        raise ConfigError("不是受支持的加密配置，或文件已经损坏。") from None
    try:
        plain = AESGCM(_key(password, salt)).decrypt(nonce, cipher, _AAD)
    except InvalidTag:
        raise ConfigError("密码不正确，或配置文件已经损坏。") from None
    if len(plain) > MAX_PLAIN_BYTES:
        raise ConfigError("解密后的配置内容过大。")
    try:
        payload = json.loads(plain)
        if not isinstance(payload, dict):
            raise ValueError()
        return payload
    except (ValueError, UnicodeError, RecursionError):
        raise ConfigError("解密后的配置格式不正确。") from None

