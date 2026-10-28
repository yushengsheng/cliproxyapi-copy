"""Small native configuration adapters sharing one encrypted transaction mechanism."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import plistlib
import sys

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from .client import configuration_directory
from .config import (FILES, MAX_LOCAL_BYTES, _backup_states, _commit, _snapshot,
                     read_bytes, render_config, restore_backup, validate_connection)
from .crypto import ConfigError, decode_document
from .model_support import ImportPlan, plan_import, plan_summary

CLAUDE_PROFILE_ID = "f1250c21-45c7-4b32-90fb-131f25cab970"
CLAUDE_PROFILE = "configLibrary/" + CLAUDE_PROFILE_ID + ".json"


@dataclass(frozen=True)
class Agent:
    label: str
    files: tuple[str, ...]
    protocol: str


AGENTS = {
    "codex": Agent("GPT / Codex", FILES, "responses"),
    "claude": Agent("Claude 桌面版 · Chat + Code", (CLAUDE_PROFILE, "configLibrary/_meta.json", "claude_desktop_config.json"), "messages"),
    "pi": Agent("pi", ("models.json", "settings.json", "auth.json"), "responses"),
    "hermes": Agent("Hermes", ("config.yaml",), "chat"),
    "claude-code": Agent("Claude Code · 命令行", ("settings.json",), "messages"),
    # SiYuan stores workspace-wide settings in <workspace>/conf/conf.json.
    # Its custom provider uses the OpenAI Chat Completions protocol and keeps
    # a provider-scoped model registry, so importing every selected model also
    # makes them available from SiYuan's model picker.
    "siyuan": Agent("思源笔记", ("conf/conf.json",), "chat"),
}
PROVIDER = "cliproxyapi"
# The CLIProxyAPI extension replaces its own provider at startup. Keep static
# imports separate so installing that extension cannot hide the selected models.
PI_PROVIDER = "cliproxyapi-config"


def default_directory(agent: str, home: Path | None = None, environment=None) -> Path:
    env = os.environ if environment is None else environment
    home = Path(home or Path.home()).absolute()
    if agent == "codex":
        return configuration_directory(home, env)
    if agent == "claude":
        if sys.platform == "darwin":
            return home / "Library/Application Support/Claude-3p"
        if sys.platform == "win32":
            return Path(env.get("LOCALAPPDATA") or home / "AppData/Local") / "Claude-3p"
        return Path(env.get("XDG_CONFIG_HOME") or home / ".config") / "Claude-3p"
    variable, suffix = {"claude-code": ("CLAUDE_CONFIG_DIR", ".claude"),
                        "pi": ("PI_CODING_AGENT_DIR", ".pi/agent"),
                        "hermes": ("HERMES_HOME", ".hermes"),
                        "siyuan": ("SIYUAN_WORKSPACE", "SiYuan")}[agent]
    value = env.get(variable)
    if value:
        path = Path(value).expanduser()
    elif agent == "siyuan":
        # The macOS desktop build keeps its default workspace under the
        # application-support directory.  Older builds and other platforms
        # commonly use ~/SiYuan, so retain that fallback and honor a live
        # workspace marker when more than one candidate exists.
        candidates = []
        if sys.platform == "darwin":
            candidates.append(home / "Library/Application Support/SiYuan")
        elif sys.platform == "win32":
            candidates.append(Path(env.get("APPDATA") or home / "AppData/Roaming") / "SiYuan")
        candidates.extend((home / suffix, home / "Documents/SiYuan"))
        existing = [candidate for candidate in candidates
                    if (candidate / "conf/conf.json").is_file()]
        locked = [candidate for candidate in existing if (candidate / ".lock").exists()]
        path = (locked[0] if locked else existing[0]) if existing else home / suffix
    else:
        path = home / suffix
    if not path.is_absolute():
        raise ConfigError(variable + " 必须是绝对路径。")
    return path


def _json(content: bytes | None) -> dict:
    value = json.loads(content.decode("utf-8-sig")) if content else {}
    if not isinstance(value, dict):
        raise ValueError()
    return value


def _section(document, name):
    value = document.setdefault(name, {})
    if not isinstance(value, dict):
        raise ValueError()
    return value


def _pi_input(row: dict) -> list[str]:
    modes = row.get("input_modalities")
    modes = [mode for mode in modes if mode in ("text", "image")] if isinstance(modes, list) else []
    return modes or ["text"]


def _encode(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _siyuan_id(seed: str) -> str:
    """Return a stable SiYuan node-shaped ID without embedding credentials."""
    digest = hashlib.sha1(seed.encode("utf-8"), usedforsecurity=False).hexdigest()[:7]
    return "20000101000000-" + digest


def _siyuan_secret(value: str) -> str:
    """Encode a SiYuan API secret with its workspace-compatible obfuscation."""
    # SiYuan's util.AESEncrypt first hex-encodes UTF-8 text, then encrypts it
    # with the fixed AES-CBC key/IV and stores the result as hex. The format is
    # intentionally reversible and matches what the native settings page writes.
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    encoded = value.encode("utf-8").hex().encode("ascii")
    padding = algorithms.AES.block_size // 8 - len(encoded) % (algorithms.AES.block_size // 8)
    padded = encoded + bytes([padding]) * padding
    cipher = Cipher(algorithms.AES(b"696D897C9AA0611B"), modes.CBC(b"RandomInitVector"))
    encryptor = cipher.encryptor()
    return (encryptor.update(padded) + encryptor.finalize()).hex()


def _siyuan_models(rows: list[dict], provider_id: str) -> list[dict]:
    models = []
    for row in rows:
        name = row["slug"]
        model = {"id": _siyuan_id(f"{provider_id}:model:{name}"),
                 "displayName": row.get("display_name") or name,
                 "enabled": True, "name": name}
        context = row.get("context_window")
        if isinstance(context, int) and context > 0:
            model["contextLength"] = context
        models.append(model)
    return models


def prepare_import(agent: str, payload: dict) -> ImportPlan:
    if agent not in AGENTS:
        raise ConfigError("不支持的 Agent。")
    return plan_import(agent, payload, AGENTS[agent].protocol)


def render_agent(agent: str, before: dict, payload: dict, directory: Path) -> dict[str, bytes]:
    plan = prepare_import(agent, payload)
    if plan.payload is None:
        raise ConfigError("没有可导入的模型，未修改任何配置。\n" + plan_summary(plan))
    payload = plan.payload
    rows = payload["catalog"]["models"]
    names = [row["slug"] for row in rows]
    key, base, model = payload["api_key"], payload["base_url"], payload["model"]
    original = lambda name: before.get(name, {}).get("data") if before.get(name) else None
    try:
        if agent == "codex":
            return {FILES[0]: render_config(original(FILES[0]), payload, directory),
                    FILES[1]: key.encode("utf-8"), FILES[2]: _encode(payload["catalog"])}
        if agent == "siyuan":
            document = _json(original("conf/conf.json"))
            ai = _section(document, "ai")
            providers = ai.setdefault("providers", [])
            if not isinstance(providers, list) or any(not isinstance(item, dict) for item in providers):
                raise ValueError()
            provider_id = _siyuan_id("provider:" + PROVIDER)
            models = _siyuan_models(rows, provider_id)
            model_ids = {model["name"]: model["id"] for model in models}
            secret = _siyuan_secret(key)
            # Replace only the source previously created by this tool. Other
            # SiYuan providers and their credentials remain available for
            # manual switching, even if a user gave one the same display name.
            providers = [item for item in providers if item.get("id") != provider_id]
            providers.append({"id": provider_id, "displayName": "CLIProxyAPI", "enabled": True,
                              "apiKey": secret, "baseURL": base, "protocol": "openai",
                              "requestTimeout": 120, "models": models})
            ai["providers"] = providers
            preferred_id = model_ids[model]
            editing = _section(ai, "editing")
            editing["modelId"] = preferred_id
            agent_settings = _section(ai, "agent")
            agent_settings["modelId"] = preferred_id
            # SiYuan versions before the provider registry used openAI fields.
            # Keep those fields in sync when they are present in an existing
            # workspace so an upgrade or downgrade keeps the same route.
            legacy = ai.get("openAI")
            if isinstance(legacy, dict):
                legacy.update({"APIKey": secret, "APIModel": model, "APIBaseURL": base})
            return {"conf/conf.json": _encode(document)}
        if agent == "claude":
            document = _json(original(CLAUDE_PROFILE))
            meta = _json(original("configLibrary/_meta.json"))
            if meta.get("hybridPointer"):
                raise ConfigError("Claude 桌面版正在使用远程托管配置，请先在 Claude 中切换为本地配置后导入。")
            entries = meta.setdefault("entries", [])
            if not isinstance(entries, list) or any(not isinstance(row, dict) for row in entries):
                raise ValueError()
            entries[:] = [row for row in entries if row.get("id") != CLAUDE_PROFILE_ID]
            entries.append({"id": CLAUDE_PROFILE_ID, "name": "CLIProxyAPI", "provider": "gateway"})
            meta["appliedId"] = CLAUDE_PROFILE_ID
            ordered = sorted(rows, key=lambda row: row["slug"] != model)
            document.update({"inferenceProvider": "gateway", "inferenceCredentialKind": "static",
                "inferenceGatewayBaseUrl": base[:-len("/v1")], "inferenceGatewayApiKey": key,
                "inferenceGatewayAuthScheme": "bearer", "modelDiscoveryEnabled": False,
                "inferenceModels": [{"name": row["slug"], "supports1m": row.get("context_window", 0) >= 1000000}
                                    for row in ordered],
                "alwaysStartWithDefaultModel": True, "chatTabEnabled": True,
                "isClaudeCodeForDesktopEnabled": True})
            root = _json(original("claude_desktop_config.json"))
            root["deploymentMode"] = "3p"
            return {CLAUDE_PROFILE: _encode(document), "configLibrary/_meta.json": _encode(meta),
                    "claude_desktop_config.json": _encode(root)}
        if agent == "claude-code":
            document = _json(original("settings.json"))
            env = _section(document, "env")
            if any(str(env.get(k, "")).lower() not in ("", "0", "false") for k in
                   ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")):
                raise ConfigError("Claude Code 已启用云平台专用线路，请先关闭该线路后再导入。")
            env.pop("ANTHROPIC_API_KEY", None)
            env.update({"ANTHROPIC_BASE_URL": base[:-len("/v1")], "ANTHROPIC_AUTH_TOKEN": key,
                        "ANTHROPIC_MODEL": model})
            env.pop("ANTHROPIC_SMALL_FAST_MODEL", None)
            for family in ("OPUS", "SONNET", "HAIKU", "FABLE"):
                matches = [name for name in names if family.lower() in name.lower()]
                env["ANTHROPIC_DEFAULT_" + family + "_MODEL"] = model if model in matches or not matches else matches[0]
            # Discovery adds custom IDs; extend any existing model filter.
            env["CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY"] = "1"
            document["model"] = model
            available = document.get("availableModels", [])
            if not isinstance(available, list) or any(not isinstance(name, str) for name in available):
                raise ValueError()
            document["availableModels"] = list(dict.fromkeys([*available, *names]))
            document.setdefault("availableModelsMatch", "exact")
            return {"settings.json": _encode(document)}
        if agent == "pi":
            document = _json(original("models.json"))
            # Pi interpolates $ and treats ! as a command in apiKey. Use auth.json's
            # literal credential format so arbitrary server keys cannot become commands.
            _section(document, "providers")[PI_PROVIDER] = {"baseUrl": base, "api": "openai-responses",
                "models": [{"id": row["slug"], "name": row["slug"],
                            "reasoning": any(level.get("effort") not in (None, "none") for level in row.get("supported_reasoning_levels", [])),
                            "input": _pi_input(row),
                            "contextWindow": row.get("context_window", 32768), "maxTokens": 4096} for row in rows]}
            auth = _json(original("auth.json"))
            if PI_PROVIDER in auth and (not isinstance(auth[PI_PROVIDER], dict)
                                       or auth[PI_PROVIDER].get("type") != "api_key"):
                raise ConfigError("pi 的导入来源已被其他登录方式占用，未覆盖原凭据。")
            auth[PI_PROVIDER] = {"type": "api_key", "key": key}
            settings = _json(original("settings.json"))
            # Imports add a source; they must not replace an existing account's
            # startup choice, thinking preference, or unrestricted model scope.
            if settings.get("defaultProvider") == PI_PROVIDER or not (settings.get("defaultProvider") or settings.get("defaultModel")):
                settings.update({"defaultProvider": PI_PROVIDER, "defaultModel": model})
            scope = settings.get("enabledModels")
            if scope is not None:
                if not isinstance(scope, list) or any(not isinstance(name, str) for name in scope):
                    raise ValueError()
                if scope:
                    settings["enabledModels"] = list(dict.fromkeys(
                        [name for name in scope if not name.startswith(PI_PROVIDER + "/")]
                        + [PI_PROVIDER + "/" + name for name in names]))
            return {name: original(name) if original(name) is not None and _json(original(name)) == value
                    else _encode(value) for name, value in
                    (("models.json", document), ("settings.json", settings), ("auth.json", auth))}
        if agent == "hermes":
            yaml = YAML()
            yaml.preserve_quotes = True
            content = original("config.yaml")
            document = yaml.load(content.decode("utf-8")) if content else {}
            if document is None:
                document = {}
            if not isinstance(document, dict):
                raise ValueError()
            # Replacing only our own provider also removes stale auth helpers/headers.
            _section(document, "providers")[PROVIDER] = {"api": base, "api_key": key,
                "transport": "chat_completions", "default_model": model,
                "discover_models": False, "models": {name: {} for name in names}}
            if isinstance(document.get("model"), str):
                document["model"] = {}
            current = _section(document, "model")
            for field in ("base_url", "api_key", "key_env", "model", "context_length", "api_mode", "transport"):
                current.pop(field, None)
            current.update({"provider": "custom:" + PROVIDER, "default": model})
            output = StringIO()
            yaml.dump(document, output)
            return {"config.yaml": output.getvalue().encode("utf-8")}
        raise ConfigError("不支持的 Agent。")
    except (ValueError, TypeError, UnicodeError, YAMLError):
        raise ConfigError("目标软件的配置格式错误，未覆盖任何文件。请先修复原配置。") from None


def apply_agent(agent: str, directory: Path, payload: dict, password: str | None = None,
                *, backup=True, backup_path: Path | None = None) -> Path | None:
    directory = directory.expanduser().absolute()
    if agent == "claude" and directory == default_directory("claude"):
        _check_desktop_policy()
    files = AGENTS[agent].files
    before = _snapshot(directory, files)
    rendered = render_agent(agent, before, payload, directory)
    if any(len(value) > MAX_LOCAL_BYTES for value in rendered.values()):
        raise ConfigError("生成的配置文件过大。")
    desired = {name: before[name] if before[name] and before[name]["data"] == value
               else {"data": value, "mode": 0o600} for name, value in rendered.items()}
    return _commit(directory, before, desired, password, files, agent, backup=backup, backup_path=backup_path)


def restore_agent(agent: str, directory: Path, content: bytes, password: str | None = None,
                  *, backup_path: Path | None = None) -> Path:
    directory = directory.expanduser().absolute()
    if agent == "codex":
        return restore_backup(directory, content, password, backup_path=backup_path)
    payload = decode_document(content, password)
    # Before desktop support, the "claude" adapter generated CLI settings backups.
    if (agent == "claude-code" and payload.get("agent") == "claude"
            and isinstance(payload.get("files"), dict) and set(payload["files"]) == {"settings.json"}):
        payload["agent"] = "claude-code"
    if payload.pop("agent", None) != agent or payload.pop("directory", None) != str(directory):
        raise ConfigError("该备份不属于当前软件或配置目录。")
    files = AGENTS[agent].files
    desired = _backup_states(payload, files)
    try:
        for name, row in desired.items():
            if row is None:
                continue
            if name.endswith(".json"):
                _json(row["data"])
            elif name.endswith(".yaml"):
                value = YAML(typ="safe").load(row["data"].decode("utf-8"))
                if value is not None and not isinstance(value, dict):
                    raise ValueError()
    except (ValueError, TypeError, UnicodeError, YAMLError):
        raise ConfigError("备份中的目标软件配置格式错误，未覆盖任何文件。") from None
    before = _snapshot(directory, files)
    if agent == "pi":
        desired = _restore_pi(before, desired)
    return _commit(directory, before, desired, password, files, agent, backup_path=backup_path)


def _restore_pi(before: dict, backup: dict) -> dict:
    """Undo our source only: OAuth tokens may have rotated since the backup."""
    result = {}
    try:
        for name in AGENTS["pi"].files:
            current = _json(before[name]["data"] if before[name] else None)
            saved = _json(backup[name]["data"] if backup[name] else None)
            if name == "models.json":
                providers = _section(current, "providers")
                saved_providers = _section(saved, "providers")
                if PI_PROVIDER in saved_providers:
                    providers[PI_PROVIDER] = saved_providers[PI_PROVIDER]
                else:
                    providers.pop(PI_PROVIDER, None)
                if not providers and not (backup[name] and "providers" in _json(backup[name]["data"])):
                    current.pop("providers", None)
            elif name == "auth.json":
                if PI_PROVIDER in current and (not isinstance(current[PI_PROVIDER], dict)
                                              or current[PI_PROVIDER].get("type") != "api_key"):
                    raise ConfigError("pi 的导入来源已被其他登录方式占用，未覆盖原凭据。")
                if PI_PROVIDER in saved:
                    if not isinstance(saved[PI_PROVIDER], dict) or saved[PI_PROVIDER].get("type") != "api_key":
                        raise ConfigError("备份中的 pi 导入凭据不是 API 密钥，未覆盖现有登录。")
                    current[PI_PROVIDER] = saved[PI_PROVIDER]
                else:
                    current.pop(PI_PROVIDER, None)
            else:
                if current.get("defaultProvider") in (None, PI_PROVIDER):
                    for key in ("defaultProvider", "defaultModel"):
                        if key in saved:
                            current[key] = saved[key]
                        else:
                            current.pop(key, None)
                scope, old_scope = current.get("enabledModels"), saved.get("enabledModels")
                for values in (scope, old_scope):
                    if values is not None and (not isinstance(values, list)
                                              or any(not isinstance(value, str) for value in values)):
                        raise ValueError()
                if scope:
                    remaining = [value for value in scope if not value.startswith(PI_PROVIDER + "/")]
                    restored = [value for value in old_scope or [] if value.startswith(PI_PROVIDER + "/")]
                    if remaining or restored:
                        current["enabledModels"] = list(dict.fromkeys(remaining + restored))
                    elif old_scope == []:
                        current["enabledModels"] = []
                    else:
                        current.pop("enabledModels", None)
            # Preserve original bytes/mode whenever the semantic content matches.
            if current == _json(backup[name]["data"] if backup[name] else None):
                result[name] = backup[name]
            elif current == _json(before[name]["data"] if before[name] else None):
                result[name] = before[name]
            else:
                result[name] = {"data": _encode(current), "mode": 0o600}
    except (ValueError, TypeError, UnicodeError):
        raise ConfigError("pi 的当前配置或备份格式错误，未覆盖任何文件。") from None
    return result


def _check_desktop_policy():
    """Do not report a local import as effective when device policy takes precedence."""
    routing = {"inferenceProvider", "bootstrapUrl", "disableDeploymentModeChooser"}
    if sys.platform == "darwin":
        import getpass
        for folder in (Path("/Library/Managed Preferences"), Path("/Library/Managed Preferences") / getpass.getuser()):
            path = folder / "com.anthropic.claudefordesktop.plist"
            if path.exists():
                try:
                    policy = plistlib.loads(read_bytes(path))
                except (ValueError, OSError):
                    raise ConfigError("Claude 桌面版存在无法读取的组织策略，无法确认本地配置生效。") from None
                if isinstance(policy, dict) and routing.intersection(policy):
                    raise ConfigError("Claude 桌面版的网关由组织策略管理，本地导入无法覆盖该策略。请先在 Claude 中确认配置来源。")
    elif sys.platform == "win32":
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\Policies\Claude") as key:
                    for name in routing:
                        try:
                            winreg.QueryValueEx(key, name)
                        except FileNotFoundError:
                            continue
                        raise ConfigError("Claude 桌面版的网关由组织策略管理，本地导入无法覆盖该策略。")
            except FileNotFoundError:
                continue
            except OSError:
                raise ConfigError("无法读取 Claude 桌面版的组织策略，请检查权限。") from None


def preview_agent(agent: str, directory: Path, payload: dict) -> str:
    plan = prepare_import(agent, payload)
    summary = plan_summary(plan)
    if plan.payload is None:
        return "没有可导入的模型，不会修改任何配置。\n" + summary
    summary += "\n拟导入：" + "、".join(plan.accepted) + "\n仅预览配置，未进行联网调用测试。"
    masked = dict(plan.payload, api_key="<由后台获取的客户端密钥>")
    rendered = render_agent(agent, {}, masked, directory)
    parts = [str(directory / name) + "\n" + value.decode("utf-8") for name, value in rendered.items()]
    return summary + "\n\n" + "\n\n".join(parts)
