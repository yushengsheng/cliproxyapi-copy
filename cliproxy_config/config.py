"""Read connections, regenerate native paths, and apply recoverable changes."""
from __future__ import annotations

import base64
import datetime as dt
import json
import ntpath
import os
from pathlib import Path
import secrets
import ssl
import stat
import sys
import tempfile
import tomllib
import urllib.error
import urllib.parse
import urllib.request

import tomlkit
import truststore

from .crypto import ConfigError, MAX_FILE_BYTES, decrypt, encrypt

PROVIDER = "linux-manager-cliproxyapi"
FILES = ("config.toml", "linux-manager-cliproxyapi.key", "linux-manager-cliproxyapi-models.json")
MAX_LOCAL_BYTES = 4 * 1024 * 1024
MODEL_SETTINGS = {"model_reasoning_effort", "model_reasoning_summary", "model_verbosity",
                  "model_context_window", "model_auto_compact_token_limit"}
PROVIDER_SETTINGS = {"request_max_retries", "stream_max_retries", "stream_idle_timeout_ms", "supports_websockets"}


def default_directory() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser().absolute()


def safe_path(path: Path) -> None:
    if not path.is_absolute() or any(ord(c) < 32 for c in str(path)):
        raise ConfigError("配置路径必须是有效的绝对路径。")
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ConfigError("配置路径包含符号链接，请选择真实的配置目录。")


def read_bytes(path: Path, limit: int = MAX_LOCAL_BYTES) -> bytes:
    safe_path(path)
    if not path.is_file() or path.stat().st_size > limit:
        raise ConfigError("所选文件不存在、不是普通文件，或超过大小限制。")
    with path.open("rb") as handle:
        content = handle.read(limit + 1)
    if len(content) > limit:
        raise ConfigError("所选文件超过大小限制。")
    return content


def atomic_write(path: Path, content: bytes, mode: int = 0o600) -> None:
    safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".cliproxy-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _text(value, label, limit=1024):
    if not isinstance(value, str) or not value or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ConfigError(label + "格式不正确。")
    return value


def _url(value):
    _text(value, "接口地址", 2048)
    try:
        parsed = urllib.parse.urlsplit(value)
        valid = (parsed.scheme == "https" and parsed.hostname and parsed.port != 0
                 and parsed.path.rstrip("/").endswith("/v1")
                 and not (parsed.username or parsed.password or parsed.query or parsed.fragment))
    except ValueError:
        valid = False
    if not valid:
        raise ConfigError("接口地址必须是 HTTPS 地址，并以 /v1 结尾。")


def _settings(values, allowed):
    if not isinstance(values, dict) or set(values) - allowed:
        raise ConfigError("配置包含不支持的连接选项。")
    for key, value in values.items():
        if key == "supports_websockets":
            valid = type(value) is bool
        elif key in {"model_reasoning_effort", "model_reasoning_summary", "model_verbosity"}:
            valid = isinstance(value, str) and 0 < len(value) <= 64 and not any(ord(c) < 32 for c in value)
        else:
            valid = type(value) is int and 0 <= value <= 2**31 - 1
        if not valid:
            raise ConfigError("连接选项 " + key + " 格式不正确。")


def validate_connection(payload: dict) -> dict:
    expected = {"kind", "version", "base_url", "api_key", "model", "catalog", "model_settings", "provider_settings"}
    if not isinstance(payload, dict) or set(payload) != expected or payload.get("kind") != "connection" or type(payload.get("version")) is not int or payload["version"] != 1:
        raise ConfigError("不是受支持的线路配置。")
    _url(payload["base_url"])
    _text(payload["api_key"], "API 密钥", 8192)
    _text(payload["model"], "默认模型")
    _settings(payload["model_settings"], MODEL_SETTINGS)
    _settings(payload["provider_settings"], PROVIDER_SETTINGS)
    catalog = payload["catalog"]
    if not isinstance(catalog, dict) or not isinstance(catalog.get("models"), list) or not 1 <= len(catalog["models"]) <= 2048:
        raise ConfigError("模型目录格式不正确。")
    slugs = []
    for row in catalog["models"]:
        if not isinstance(row, dict):
            raise ConfigError("模型目录包含无效项目。")
        slugs.append(_text(row.get("slug"), "模型名称"))
    if len(set(slugs)) != len(slugs) or payload["model"] not in slugs:
        raise ConfigError("模型目录重复，或缺少默认模型。")
    try:
        if len(json.dumps(catalog, ensure_ascii=False, allow_nan=False).encode()) > MAX_LOCAL_BYTES:
            raise ConfigError("模型目录过大。")
    except (ValueError, TypeError):
        raise ConfigError("模型目录格式不正确。") from None
    return payload


def credential_auth(directory: Path, platform: str | None = None) -> dict:
    key_path = directory / FILES[1]
    if (platform or sys.platform) == "win32":
        # EncodedCommand passes the path as a literal, even with quotes or shell metacharacters.
        literal = str(key_path).replace("'", "''")
        script = ("[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false);"
                  "[Console]::Out.Write([System.IO.File]::ReadAllText('" + literal + "',[System.Text.Encoding]::UTF8))")
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        windows_root = os.environ.get("SystemRoot", r"C:\Windows")
        command = ntpath.join(windows_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        return {"command": command, "args": ["-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]}
    return {"command": "/bin/cat", "args": [str(key_path)]}


def _read_connection(directory: Path) -> tuple[dict, tuple[Path, ...]]:
    directory = directory.expanduser().absolute()
    try:
        document = tomllib.loads(read_bytes(directory / FILES[0]).decode("utf-8"))
        if document.get("profile"):
            raise ConfigError("客户端使用了独立 profile，暂不支持自动读取；请先取消默认 profile。")
        if document.get("model_provider") != PROVIDER:
            raise ConfigError("当前线路不是 Linux Manager 配置的 CLIProxyAPI，请选择正确的配置目录。")
        provider = document["model_providers"][PROVIDER]
        if provider.get("wire_api") != "responses" or provider.get("requires_openai_auth"):
            raise ConfigError("当前提供商使用了不支持的协议或账号授权方式。")
        allowed = {"name", "base_url", "wire_api", "auth", "requires_openai_auth"} | PROVIDER_SETTINGS
        if set(provider) - allowed:
            raise ConfigError("当前提供商包含额外的认证或连接设置，无法完整迁移。")
        auth = provider.get("auth", {})
        # Do not execute an arbitrary credential helper from a configuration file.
        if auth.get("command") == "/bin/cat" and isinstance(auth.get("args"), list) and len(auth["args"]) == 1:
            key_path = Path(auth["args"][0])
        elif auth == credential_auth(directory):
            key_path = directory / FILES[1]
        else:
            raise ConfigError("无法识别当前密钥读取方式；只支持 Linux Manager 或本工具生成的配置。")
        if not key_path.is_absolute():
            raise ConfigError("密钥路径必须是绝对路径。")
        catalog_path = Path(document["model_catalog_json"])
        payload = {"kind": "connection", "version": 1,
                   "base_url": provider["base_url"].rstrip("/"),
                   "api_key": read_bytes(key_path, 8192).decode("utf-8").strip(),
                   "model": document["model"],
                   "catalog": json.loads(read_bytes(catalog_path)),
                   "model_settings": {k: document[k] for k in MODEL_SETTINGS if k in document},
                   "provider_settings": {k: provider[k] for k in PROVIDER_SETTINGS if k in provider}}
    except (ValueError, UnicodeError, KeyError, TypeError):
        raise ConfigError("本机客户端配置不完整或格式错误，请检查配置目录。") from None
    return validate_connection(payload), (directory / FILES[0], key_path, catalog_path)


def read_connection(directory: Path) -> dict:
    return _read_connection(directory)[0]


def export_connection(directory: Path, password: str, destination: Path | None = None) -> tuple[dict, str]:
    directory = directory.expanduser().absolute()
    payload, source_paths = _read_connection(directory)
    if destination is not None:
        destination = destination.expanduser().absolute()
        protected = (*source_paths, *(directory / name for name in (*FILES, "auth.json")))
        if destination.resolve() in {path.resolve() for path in protected}:
            raise ConfigError("不能覆盖客户端正在使用的配置文件，请选择其他文件名。")
    content = encrypt(payload, password)
    if destination is not None:
        atomic_write(destination, content.encode("utf-8"))
    return payload, content


def render_config(original: bytes | None, payload: dict, directory: Path, platform=None) -> bytes:
    validate_connection(payload)
    try:
        document = tomlkit.parse((original or b"").decode("utf-8"))
        if document.get("profile"):
            raise ConfigError("目标客户端正在使用独立 profile，请先取消默认 profile。")
        providers = document.get("model_providers", {})
        if not isinstance(providers, dict):
            raise ConfigError("目标客户端的提供商配置格式错误。")
        for key in sorted(MODEL_SETTINGS):
            if key in document:
                del document[key]
        document["model"] = payload["model"]
        document["model_provider"] = PROVIDER
        document["model_catalog_json"] = str(directory / FILES[2])
        for key, value in payload["model_settings"].items():
            document[key] = value
        if "model_providers" not in document:
            document["model_providers"] = tomlkit.table()
        provider = {"name": "Linux CLIProxyAPI", "base_url": payload["base_url"], "wire_api": "responses",
                    **payload["provider_settings"], "auth": credential_auth(directory, platform)}
        document["model_providers"][PROVIDER] = provider
        output = tomlkit.dumps(document).encode("utf-8")
        checked = tomllib.loads(output.decode("utf-8"))
        if checked["model_provider"] != PROVIDER or checked["model"] != payload["model"]:
            raise ValueError()
        return output
    except (ValueError, UnicodeError, TypeError, KeyError, tomlkit.exceptions.ParseError):
        raise ConfigError("目标配置格式错误，未覆盖任何文件。") from None


def _snapshot(directory: Path) -> dict:
    safe_path(directory)
    result = {}
    for name in FILES:
        path = directory / name
        safe_path(path)
        result[name] = {"data": read_bytes(path), "mode": stat.S_IMODE(path.stat().st_mode)} if path.exists() else None
    return result


def _same_states(left: dict, right: dict) -> bool:
    if sys.platform != "win32":
        return left == right
    # chmod on Windows exposes only the read-only bit, not Unix permission modes.
    def contents(states):
        return {name: row["data"] if row is not None else None for name, row in states.items()}
    return contents(left) == contents(right)


def _snapshot_payload(states: dict) -> dict:
    return {"kind": "backup", "version": 1, "files": {
        name: {"data": base64.b64encode(row["data"]).decode("ascii"), "mode": row["mode"]} if row else None
        for name, row in states.items()}}


def _backup_states(payload: dict) -> dict:
    if (set(payload) != {"kind", "version", "files"} or payload.get("kind") != "backup"
            or type(payload.get("version")) is not int or payload["version"] != 1
            or not isinstance(payload.get("files"), dict) or set(payload["files"]) != set(FILES)):
        raise ConfigError("不是本工具生成的恢复备份。")
    states = {}
    try:
        for name, row in payload["files"].items():
            if row is None:
                states[name] = None
                continue
            if not isinstance(row, dict) or set(row) != {"data", "mode"}:
                raise ValueError()
            if type(row["mode"]) is not int or row["mode"] & ~0o777:
                raise ValueError()
            content = base64.b64decode(row["data"], validate=True)
            if len(content) > MAX_LOCAL_BYTES:
                raise ValueError()
            states[name] = {"data": content, "mode": row["mode"]}
        if states[FILES[0]]:
            config = tomllib.loads(states[FILES[0]]["data"].decode("utf-8"))
            # Restores are local snapshots, never a route for importing another machine's paths.
            if not isinstance(config, dict):
                raise ValueError()
    except (ValueError, TypeError, UnicodeError):
        raise ConfigError("恢复备份内容损坏。") from None
    return states


def _commit(directory: Path, before: dict, desired: dict, password: str) -> Path:
    backup_dir = directory / "cliproxy-config-backups"
    safe_path(backup_dir)
    identifier = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(4)
    backup_path = backup_dir / (identifier + ".cproxybackup")
    # Bind a restore to its original folder; portable connections are handled separately.
    backup = _snapshot_payload(before)
    backup["directory"] = str(directory)
    atomic_write(backup_path, encrypt(backup, password).encode("utf-8"))
    if not _same_states(_snapshot(directory), before):
        raise ConfigError("备份期间客户端配置被其他程序修改，已停止应用，请重试。")
    changed = []
    try:
        for name, row in desired.items():
            path = directory / name
            current = _snapshot(directory)
            expected = {key: desired[key] if key in changed else before[key] for key in FILES}
            if not _same_states(current, expected):
                raise ConfigError("应用期间配置被其他程序修改，已停止应用。")
            changed.append(name)
            if row is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, row["data"], row["mode"])
        if not _same_states(_snapshot(directory), desired):
            raise ConfigError("应用期间配置被其他程序修改，已停止应用。")
    except Exception:
        failed = []
        for name in reversed(changed):
            try:
                row = before[name]
                path = directory / name
                current = {name: {"data": read_bytes(path), "mode": stat.S_IMODE(path.stat().st_mode)} if path.exists() else None}
                if _same_states(current, {name: row}):
                    continue
                if not _same_states(current, {name: desired[name]}):
                    # Preserve a concurrent edit rather than overwrite it during rollback.
                    failed.append(name)
                    continue
                if row is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write(path, row["data"], row["mode"])
            except Exception:
                failed.append(name)
        if failed:
            raise ConfigError("写入失败，部分文件无法撤回或被其他程序修改；未覆盖其他程序的修改。请检查并按需用加密备份恢复：" + str(backup_path)) from None
        raise ConfigError("写入失败，已自动撤回本次修改。加密备份：" + str(backup_path)) from None
    return backup_path


def apply_connection(directory: Path, payload: dict, password: str, platform=None) -> Path:
    directory = directory.expanduser().absolute()
    before = _snapshot(directory)
    output = render_config(before[FILES[0]]["data"] if before[FILES[0]] else None, payload, directory, platform)
    catalog = (json.dumps(payload["catalog"], ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    desired = {FILES[0]: {"data": output, "mode": 0o600},
               FILES[1]: {"data": payload["api_key"].encode("utf-8"), "mode": 0o600},
               FILES[2]: {"data": catalog, "mode": 0o600}}
    if any(len(row["data"]) > MAX_LOCAL_BYTES for row in desired.values()):
        raise ConfigError("生成的客户端配置过大。")
    return _commit(directory, before, desired, password)


def restore_backup(directory: Path, content: str | bytes, password: str) -> Path:
    directory = directory.expanduser().absolute()
    payload = decrypt(content, password)
    if payload.get("directory") != str(directory):
        raise ConfigError("恢复备份只能应用于生成它的原配置目录。跨电脑请使用导出的线路配置。")
    payload.pop("directory")
    desired = _backup_states(payload)
    return _commit(directory, _snapshot(directory), desired, password)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def check_connection(payload: dict) -> int:
    validate_connection(payload)
    request = urllib.request.Request(payload["base_url"].rstrip("/") + "/models",
        headers={"Authorization": "Bearer " + payload["api_key"], "Accept": "application/json"})
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    opener = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=context))
    try:
        with opener.open(request, timeout=15) as response:
            content = response.read(MAX_LOCAL_BYTES + 1)
        if len(content) > MAX_LOCAL_BYTES:
            raise ValueError()
        rows = json.loads(content)["data"]
        if not isinstance(rows, list):
            raise ValueError()
        models = {r["id"] for r in rows if isinstance(r, dict) and isinstance(r.get("id"), str)}
        if payload["model"] not in models:
            raise ConfigError("接口可访问，但默认模型已不在服务器列表中，请在源电脑重新配置后导出。")
        return len(models)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise ConfigError("服务器拒绝了 API 密钥，请检查密钥是否仍有效。") from None
        raise ConfigError("服务器返回 HTTP " + str(error.code) + "，请检查接口地址和服务状态。") from None
    except (OSError, ValueError, TypeError, KeyError):
        raise ConfigError("连接失败，请检查网络、HTTPS 证书和服务器地址；本机配置未改动。") from None

