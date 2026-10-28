"""Validate model nodes, render Codex settings, and commit recoverable changes."""
from __future__ import annotations

import base64
from contextlib import contextmanager, nullcontext
import datetime as dt
import json
import ntpath
import os
from pathlib import Path
import secrets
import stat
import sys
import tempfile
import tomllib
import urllib.parse

import tomlkit
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from .crypto import ConfigError, decode_document, encode_document, encrypt

PROVIDER = "linux-manager-cliproxyapi"
FILES = ("config.toml", "linux-manager-cliproxyapi.key", "linux-manager-cliproxyapi-models.json")
MAX_LOCAL_BYTES = 4 * 1024 * 1024
MODEL_SETTINGS = {"model_reasoning_effort", "model_reasoning_summary", "model_verbosity",
                  "model_context_window", "model_auto_compact_token_limit"}
PROVIDER_SETTINGS = {"request_max_retries", "stream_max_retries", "stream_idle_timeout_ms", "supports_websockets"}


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
        if "context_window" in row and (type(row["context_window"]) is not int or not 0 < row["context_window"] <= 2**31 - 1):
            raise ConfigError("模型上下文窗口格式不正确。")
        if "supported_reasoning_levels" in row:
            levels = row["supported_reasoning_levels"]
            if (not isinstance(levels, list) or any(not isinstance(level, dict)
                    or not isinstance(level.get("effort"), str)
                    or level["effort"] not in {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
                    for level in levels)):
                raise ConfigError("模型思考等级格式不正确。")
            efforts = [level["effort"] for level in levels]
            if len(set(efforts)) != len(efforts) or (row.get("default_reasoning_level") is not None
                    and row["default_reasoning_level"] not in efforts):
                raise ConfigError("模型默认思考等级不在支持列表中。")
    if len(set(slugs)) != len(slugs) or payload["model"] not in slugs:
        raise ConfigError("模型目录重复，或缺少默认模型。")
    try:
        if len(json.dumps(catalog, ensure_ascii=False, allow_nan=False).encode()) > MAX_LOCAL_BYTES:
            raise ConfigError("模型目录过大。")
    except (ValueError, TypeError):
        raise ConfigError("模型目录格式不正确。") from None
    return payload


def effective_model_settings(payload: dict) -> dict:
    settings = dict(payload["model_settings"])
    model = next(row for row in payload["catalog"]["models"] if row["slug"] == payload["model"])
    levels = [row["effort"] for row in model.get("supported_reasoning_levels", [])]
    if levels and settings.get("model_reasoning_effort") not in levels:
        settings["model_reasoning_effort"] = model.get("default_reasoning_level") or levels[0]
    return settings


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


def _codex_document(original: bytes | None):
    """Load a Codex document, rebuilding only the unusable routing shell."""
    recovered = False
    try:
        document = tomlkit.parse((original or b"").decode("utf-8"))
    except (UnicodeError, tomlkit.exceptions.ParseError):
        # The import is an explicit request to replace the active route. Keep
        # the raw bytes in the transaction snapshot so backup/rollback can
        # still restore the broken file exactly.
        document = tomlkit.document()
        recovered = original is not None and bool(original)

    if "profile" in document:
        # A default profile redirects Codex away from the settings this tool
        # writes. Removing it is the smallest repair for an explicit import.
        del document["profile"]
        recovered = True

    providers = document.get("model_providers")
    if providers is not None and not isinstance(providers, dict):
        document["model_providers"] = tomlkit.table()
        recovered = True

    models_config = document.get("models")
    if models_config is not None and not isinstance(models_config, dict):
        document["models"] = tomlkit.table()
        recovered = True
    elif (isinstance(models_config, dict) and "new_thread" in models_config
          and not isinstance(models_config["new_thread"], dict)):
        del models_config["new_thread"]
        recovered = True
    return document, recovered


def needs_codex_recovery(original: bytes | None) -> bool:
    """Return whether importing will repair the existing Codex document."""
    return _codex_document(original)[1]


def render_config(original: bytes | None, payload: dict, directory: Path, platform=None) -> bytes:
    validate_connection(payload)
    try:
        document, _ = _codex_document(original)
        providers = document.get("model_providers", {})
        if not isinstance(providers, dict):
            raise ConfigError("目标客户端的提供商配置格式错误。")
        for key in sorted(MODEL_SETTINGS):
            if key in document:
                del document[key]
        document["model"] = payload["model"]
        models_config = document.get("models", {})
        if not isinstance(models_config, dict):
            raise ConfigError("目标客户端的新会话配置格式错误。")
        if "new_thread" in models_config:
            new_thread = models_config["new_thread"]
            if not isinstance(new_thread, dict):
                raise ConfigError("目标客户端的新会话配置格式错误。")
            new_thread["model"] = payload["model"]
            new_thread.pop("reasoning_effort", None)
        document["model_provider"] = PROVIDER
        document["model_catalog_json"] = str(directory / FILES[2])
        for key, value in effective_model_settings(payload).items():
            document[key] = value
        if "model_providers" not in document:
            document["model_providers"] = tomlkit.table()
        provider = {"name": "CLIProxyAPI", "base_url": payload["base_url"], "wire_api": "responses",
                    **payload["provider_settings"], "auth": credential_auth(directory, platform)}
        document["model_providers"][PROVIDER] = provider
        output = tomlkit.dumps(document).encode("utf-8")
        checked = tomllib.loads(output.decode("utf-8"))
        if checked["model_provider"] != PROVIDER or checked["model"] != payload["model"]:
            raise ValueError()
        return output
    except (ValueError, UnicodeError, TypeError, KeyError, tomlkit.exceptions.ParseError):
        raise ConfigError("目标配置格式错误，未覆盖任何文件。") from None


def _snapshot(directory: Path, files=FILES) -> dict:
    safe_path(directory)
    result = {}
    for name in files:
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


def _backup_states(payload: dict, files=FILES) -> dict:
    if (set(payload) != {"kind", "version", "files"} or payload.get("kind") != "backup"
            or type(payload.get("version")) is not int or payload["version"] != 1
            or not isinstance(payload.get("files"), dict) or set(payload["files"]) != set(files)):
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
        if states.get(files[0]):
            first_name = files[0].lower()
            first_data = states[files[0]]["data"].decode("utf-8")
            if first_name.endswith(".toml"):
                config = tomllib.loads(first_data)
            elif first_name.endswith(".json"):
                config = json.loads(first_data)
            elif first_name.endswith((".yaml", ".yml")):
                config = YAML(typ="safe").load(first_data)
            else:
                config = None
            # Restores are local snapshots, never a route for importing another machine's paths.
            if config is not None and not isinstance(config, dict):
                raise ValueError()
    except (ValueError, TypeError, UnicodeError, YAMLError):
        raise ConfigError("恢复备份内容损坏。") from None
    return states


def _commit(directory: Path, before: dict, desired: dict, password: str | None, files=FILES,
            agent="codex", *, backup=True, backup_path: Path | None = None) -> Path | None:
    # Bind a restore to its original folder; portable connections are handled separately.
    if backup:
        if backup_path is None:
            identifier = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(4)
            backup_path = directory / "cliproxy-config-backups" / (identifier + ".cproxybackup")
        backup_path = backup_path.expanduser().absolute()
        safe_path(backup_path)
        if backup_path.resolve() in {(directory / name).resolve() for name in (*files, "auth.json")}:
            raise ConfigError("备份不能覆盖目标软件的配置文件。")
        snapshot = _snapshot_payload(before)
        snapshot["directory"] = str(directory)
        if agent != "codex":
            snapshot["agent"] = agent
        content = encrypt(snapshot, password) if password is not None else encode_document(snapshot)
        atomic_write(backup_path, content.encode("utf-8"))
    else:
        backup_path = None
    with _pi_write_locks(directory) if agent == "pi" else nullcontext():
        return _write_states(directory, before, desired, files, backup_path)


@contextmanager
def _pi_write_locks(directory: Path):
    # Pi uses proper-lockfile's atomic .lock directories for both shared files.
    # Cooperate with its login/refresh/settings writers instead of racing them.
    acquired = []
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in ("auth.json", "settings.json"):
            lock = directory / (name + ".lock")
            safe_path(lock)
            try:
                lock.mkdir(mode=0o700)
            except FileExistsError:
                raise ConfigError("pi 正在更新登录或设置，已停止写入。请等操作完成后重试；不要手动删除正在使用的锁。") from None
            acquired.append(lock)
        yield
    finally:
        for lock in reversed(acquired):
            lock.rmdir()


def _write_states(directory: Path, before: dict, desired: dict, files: tuple, backup_path: Path | None):
    if not _same_states(_snapshot(directory, files), before):
        raise ConfigError("备份期间客户端配置被其他程序修改，已停止应用，请重试。")
    changed = []
    try:
        for name, row in desired.items():
            if _same_states({name: before[name]}, {name: row}):
                continue
            path = directory / name
            current = _snapshot(directory, files)
            expected = {key: desired[key] if key in changed else before[key] for key in files}
            if not _same_states(current, expected):
                raise ConfigError("应用期间配置被其他程序修改，已停止应用。")
            changed.append(name)
            if row is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, row["data"], row["mode"])
        if not _same_states(_snapshot(directory, files), desired):
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
        detail = "备份：" + str(backup_path) if backup_path else "本次未生成备份。"
        if failed:
            raise ConfigError("写入失败，部分文件无法撤回或被其他程序修改；未覆盖其他程序的修改。" + detail) from None
        raise ConfigError("写入失败，已自动撤回本次修改。" + detail) from None
    return backup_path


def restore_backup(directory: Path, content: str | bytes, password: str | None = None,
                   *, backup_path: Path | None = None) -> Path:
    directory = directory.expanduser().absolute()
    payload = decode_document(content, password)
    if payload.get("directory") != str(directory):
        raise ConfigError("恢复备份只能应用于生成它的原配置目录。跨电脑请使用导出的线路配置。")
    payload.pop("directory")
    desired = _backup_states(payload)
    return _commit(directory, _snapshot(directory), desired, password, backup_path=backup_path)
