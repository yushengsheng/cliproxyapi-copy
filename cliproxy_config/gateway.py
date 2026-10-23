"""Read an authenticated browser's management response, never a client's credentials."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request

import truststore

from .config import MAX_LOCAL_BYTES, effective_model_settings, validate_connection
from .crypto import ConfigError
from .model_support import DISCOVERY_FIELDS, codex_capability_error


@dataclass(frozen=True)
class Gateway:
    base_url: str
    keys: tuple[str, ...] = field(repr=False)
    models: tuple[dict, ...] = ()


def management_url(value: str) -> tuple[str, str]:
    try:
        value = value.strip()
        parsed = urllib.parse.urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.port == 0
                or parsed.username or parsed.password or parsed.query
                or any(ord(c) < 33 for c in value)):
            raise ValueError()
        path = parsed.path.rstrip("/")
        if path.endswith("/management.html"):
            path = path[:-len("/management.html")]
        elif path:
            raise ValueError()
        root = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
        return root + "/management.html#/config", root + "/v1"
    except ValueError:
        raise ConfigError("请输入完整的 HTTPS 后台地址，例如 https://服务器:端口/management.html#/。") from None


def read_browser(url: str, browser: str) -> Gateway:
    page, base_url = management_url(url)
    from .browser import BROWSERS, read_builtin, read_native
    if browser not in BROWSERS:
        raise ConfigError("请选择内置登录窗口、Brave 或 Chrome。")
    parsed = urllib.parse.urlsplit(base_url)
    origin = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    config = read_builtin(page, origin) if browser == BROWSERS[0] else read_native(page, browser, origin)
    keys = config.get("keys")
    if (not isinstance(keys, list) or not keys or len(keys) > 256
            or any(not isinstance(k, str) or not k or len(k) > 8192
                   or any(ord(c) < 33 for c in k) for k in keys)):
        raise ConfigError("后台未配置有效的客户端 API 密钥。请在后台添加后再获取。")
    keys = tuple(dict.fromkeys(keys))
    return Gateway(base_url, keys, fetch_models(base_url, keys[0]))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _request(base_url: str, key: str, path: str, body: dict | None = None, *, allow_not_found=False) -> dict:
    # Validate origin and headers before constructing an authenticated request.
    base_url = base_url.rstrip("/")
    if not base_url.endswith("/v1"):
        raise ConfigError("接口地址必须以 /v1 结尾。")
    management_url(base_url[:-len("/v1")] + "/management.html")
    if not key or len(key) > 8192 or any(ord(c) < 33 for c in key):
        raise ConfigError("客户端 API 密钥无效。")
    headers = {"Authorization": "Bearer " + key, "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    # CLIProxyAPI uses anthropic-version for protocol-specific model naming.
    # Discovery, Responses and Chat Completions must retain native model IDs.
    if path == "/messages":
        headers["anthropic-version"] = "2023-06-01"
    request = urllib.request.Request(base_url + path,
        data=None if body is None else json.dumps(body).encode("utf-8"), headers=headers)
    opener = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(
        context=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)))
    try:
        with opener.open(request, timeout=45) as response:
            content = response.read(MAX_LOCAL_BYTES + 1)
        if len(content) > MAX_LOCAL_BYTES:
            raise ValueError()
        result = json.loads(content)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except urllib.error.HTTPError as error:
        if allow_not_found and error.code == 404:
            return {}
        hints = {401: "客户端密钥无效", 403: "没有访问权限", 404: "接口或所选模型不存在",
                 429: "额度不足或请求过于频繁", 503: "上游授权不可用，可能处于冷却或登录已失效"}
        raise ConfigError(f"HTTP {error.code}：{hints.get(error.code, '服务调用失败')}。后台模型目录不代表实际调用一定可用。") from None
    except (OSError, ValueError, TypeError):
        raise ConfigError("连接失败：请检查网络、系统信任的 HTTPS 证书及后台地址。") from None


def fetch_models(base_url: str, key: str) -> tuple[dict, ...]:
    rows = _request(base_url, key, "/models").get("data")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 2048:
        raise ConfigError("服务器没有返回有效的模型列表。")
    models = {}
    for row in rows:
        name = row.get("id") if isinstance(row, dict) else None
        if not isinstance(name, str) or not name or len(name) > 1024 or any(ord(c) < 32 for c in name):
            raise ConfigError("服务器模型列表包含无效名称。")
        owner = row.get("owned_by", "unknown")
        models[name] = {"id": name, "owned_by": owner if isinstance(owner, str) else "unknown"}
        models[name].update({field: deepcopy(row[field]) for field in DISCOVERY_FIELDS if field in row})
    # CLIProxyAPI exposes native Codex capabilities only with client_version.
    # This schema supports extended reasoning levels; no local client data is read.
    catalog = _request(base_url, key, "/models?client_version=0.153.0", allow_not_found=True).get("models", [])
    if not isinstance(catalog, list) or len(catalog) > 2048:
        raise ConfigError("后台模型能力目录格式不正确。")
    seen = set()
    for row in catalog:
        name = row.get("slug") if isinstance(row, dict) else None
        if not isinstance(name, str) or not name or name in seen:
            raise ConfigError("后台模型能力目录包含无效或重复的模型。")
        seen.add(name)
        if name in models:
            models[name]["codex"] = deepcopy(row)
    return tuple(models[key] for key in sorted(models))


def make_connection(base_url: str, key: str, models: list[dict], default: str, *, require_capabilities=True) -> dict:
    catalog = []
    for row in models:
        native = row.get("codex")
        if not isinstance(native, dict) or native.get("slug") != row.get("id"):
            native = {"slug": row.get("id")}
        error = codex_capability_error(native)
        if require_capabilities and error:
            raise ConfigError(error)
        native = deepcopy(native)
        native.update({field: deepcopy(row[field]) for field in DISCOVERY_FIELDS if field in row and field not in native})
        catalog.append(native)
    payload = validate_connection({"kind": "connection", "version": 1, "base_url": base_url,
        "api_key": key, "model": default, "catalog": {"models": catalog}, "model_settings": {},
        "provider_settings": {"supports_websockets": False}})
    payload["model_settings"] = effective_model_settings(payload)
    return payload


def test_model(payload: dict, protocol: str) -> str:
    validate_connection(payload)
    model = payload["model"]
    if protocol == "messages":
        path, body = "/messages", {"model": model, "max_tokens": 32,
            "messages": [{"role": "user", "content": "Reply only OK."}]}
    elif protocol == "chat":
        path, body = "/chat/completions", {"model": model, "max_tokens": 32,
            "messages": [{"role": "user", "content": "Reply only OK."}]}
    elif protocol == "responses":
        path, body = "/responses", {"model": model, "max_output_tokens": 2048, "input": "Reply only OK."}
        effort = effective_model_settings(payload).get("model_reasoning_effort")
        if effort:
            body["reasoning"] = {"effort": effort}
    else:
        raise ConfigError("不支持的接口协议。")
    result = _request(payload["base_url"], payload["api_key"], path, body)
    if protocol == "responses":
        valid = result.get("status") == "completed" and bool(result.get("output"))
    elif protocol == "messages":
        valid = bool(result.get("content")) and result.get("type") == "message"
    else:
        valid = bool(result.get("choices"))
    if not valid:
        raise ConfigError("服务器返回了未完成或无效的模型响应，尚不能确认该模型可用。")
    return f"{model} · {protocol} 接口调用成功"
