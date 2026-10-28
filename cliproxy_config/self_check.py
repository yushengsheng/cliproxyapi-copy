"""Exercise current model-node imports and backups with disposable configuration files."""
from __future__ import annotations

import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import tomllib

from . import VERSION
from .config import FILES, PROVIDER, _snapshot, atomic_write, restore_backup
from .crypto import decode_document, encode_document
from .agents import AGENTS, apply_agent, restore_agent


def run_self_check(app) -> dict:
    import webview
    payload = {"kind": "connection", "version": 1, "base_url": "https://fixture.invalid/v1",
               "api_key": "self-check-fixture-only", "model": "claude-fixture-model",
               "catalog": {"models": [{"slug": "claude-fixture-model", "context_window": 272000,
                   "supported_reasoning_levels": [{"effort": "high"}], "default_reasoning_level": "high",
                   "input_modalities": ["text", "image"]}]},
               "model_settings": {"model_reasoning_effort": "high"},
               "provider_settings": {"supports_websockets": False}}
    password = "self-check-password"
    original = (b'# self-check fixture\nmodel="original"\n'
                b'[plugins."fixture@local"]\nenabled=true\n'
                b'[mcp_servers.fixture]\ncommand="keep-me"\n')
    app.update_idletasks()
    minimum_width, minimum_height = app.minsize()
    if app.winfo_reqheight() > minimum_height or app.winfo_reqwidth() > minimum_width:
        raise RuntimeError("窗口布局超出最小尺寸。")
    with tempfile.TemporaryDirectory(prefix="cliproxy-self-check-") as temp:
        root = Path(temp).resolve()
        destination = root / "目标 配置'中文"
        content = encode_document(payload, password)
        atomic_write(root / "线路.cproxy", content.encode("utf-8"))
        imported = decode_document((root / "线路.cproxy").read_bytes(), password)
        if imported != payload:
            raise RuntimeError("加密导出往返校验失败。")
        atomic_write(destination / FILES[0], original)
        backup = apply_agent("codex", destination, imported, password)
        applied = _snapshot(destination)
        if (json.loads((destination / FILES[2]).read_bytes()) != payload["catalog"]
                or (destination / FILES[1]).read_text(encoding="utf-8") != payload["api_key"]):
            raise RuntimeError("导入配置校验失败。")
        document = tomllib.loads((destination / FILES[0]).read_text(encoding="utf-8"))
        if not document["plugins"]["fixture@local"]["enabled"] or document["mcp_servers"]["fixture"]["command"] != "keep-me":
            raise RuntimeError("原有设置保留校验失败。")
        auth = document["model_providers"][PROVIDER]["auth"]
        result = subprocess.run([auth["command"], *auth["args"]], capture_output=True, timeout=15, check=True)
        if result.stdout.decode("utf-8-sig").strip() != payload["api_key"]:
            raise RuntimeError("本机密钥读取命令校验失败。")
        undo = restore_backup(destination, backup.read_bytes(), password)
        if (destination / FILES[0]).read_bytes() != original or any((destination / name).exists() for name in FILES[1:]):
            raise RuntimeError("备份恢复校验失败。")
        restore_backup(destination, undo.read_bytes(), password)
        if _snapshot(destination) != applied:
            raise RuntimeError("撤销恢复校验失败。")
        for agent in ("claude", "claude-code", "pi", "hermes", "siyuan"):
            directory = root / agent
            backup = apply_agent(agent, directory, payload, password)
            if not all((directory / name).is_file() for name in AGENTS[agent].files):
                raise RuntimeError("Agent 配置生成失败：" + agent)
            restore_agent(agent, directory, backup.read_bytes(), password)
            if any((directory / name).exists() for name in AGENTS[agent].files):
                raise RuntimeError("Agent 备份恢复失败：" + agent)
    return {"version": VERSION, "platform": platform.system(), "architecture": platform.machine(),
            "frozen": bool(getattr(sys, "frozen", False)),
            "tk": app.tk.call("info", "patchlevel"), "drag_drop": app.tk.call("package", "require", "tkdnd"),
            "crypto": "ok", "migration": "ok", "credential_helper": "ok", "agents": "ok", "layout": "ok",
            "browser": "system-webview"}
