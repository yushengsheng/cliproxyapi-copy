"""Exercise the packaged runtime against disposable configuration files."""
from __future__ import annotations

from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import tomllib

from . import VERSION
from .config import (FILES, PROVIDER, apply_connection, atomic_write,
                     export_connection, read_connection, restore_backup)
from .crypto import decrypt


def run_self_check(app) -> dict:
    payload = {"kind": "connection", "version": 1, "base_url": "https://fixture.invalid/v1",
               "api_key": "self-check-fixture-only", "model": "fixture-model",
               "catalog": {"models": [{"slug": "fixture-model"}]},
               "model_settings": {"model_reasoning_effort": "high"},
               "provider_settings": {"supports_websockets": False}}
    password = "self-check-password"
    original = (b'# self-check fixture\nmodel="original"\n'
                b'[plugins."fixture@local"]\nenabled=true\n'
                b'[mcp_servers.fixture]\ncommand="keep-me"\n')
    app.update_idletasks()
    if app.winfo_reqheight() > app.minsize()[1]:
        raise RuntimeError("窗口布局超出最小高度。")
    with tempfile.TemporaryDirectory(prefix="cliproxy-self-check-") as temp:
        root = Path(temp).resolve()
        source, destination = root / "源 配置'中文", root / "目标 配置'中文"
        apply_connection(source, payload, password)
        _, content = export_connection(source, password, root / "线路.cproxy")
        imported = decrypt((root / "线路.cproxy").read_bytes(), password)
        if imported != payload or decrypt(content, password) != payload:
            raise RuntimeError("加密导出往返校验失败。")
        atomic_write(destination / FILES[0], original)
        backup = apply_connection(destination, imported, password)
        if read_connection(destination) != payload:
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
        if read_connection(destination) != payload:
            raise RuntimeError("撤销恢复校验失败。")
    return {"version": VERSION, "platform": platform.system(), "architecture": platform.machine(),
            "frozen": bool(getattr(sys, "frozen", False)),
            "tk": app.tk.call("info", "patchlevel"), "drag_drop": app.tk.call("package", "require", "tkdnd"),
            "crypto": "ok", "migration": "ok", "credential_helper": "ok", "layout": "ok"}
