"""Locate the Codex configuration directory without scanning installed applications."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .config import read_bytes
from .crypto import ConfigError


def configuration_directory(home: Path | None = None, environment: dict | None = None) -> Path:
    home = Path(home or Path.home()).absolute()
    env = os.environ if environment is None else environment
    if env.get("CODEX_HOME"):
        value = str(env["CODEX_HOME"])
        value = str(home) + value[1:] if value == "~" or value.startswith("~/") else value
        directory = Path(value)
        if not directory.is_absolute():
            raise ConfigError("客户端的 CODEX_HOME 配置不是绝对路径，无法自动定位。")
        return directory
    # Linux Manager's desktop build keeps its chosen client directory in this record.
    records = [home / "Library" / "Application Support" / "linux服务器管理" / "client-config-directories.json"]
    if env.get("APPDATA"):
        records.insert(0, Path(env["APPDATA"]) / "linux服务器管理" / "client-config-directories.json")
    for record in records:
        if record.exists():
            try:
                data = json.loads(read_bytes(record, 65536))
                value = data.get("codex") if isinstance(data, dict) else None
                if value is not None:
                    directory = Path(value)
                    if not isinstance(value, str) or not directory.is_absolute():
                        raise ValueError()
                    return directory
            except (ValueError, TypeError):
                raise ConfigError("Linux Manager 的客户端目录记录无效，无法安全自动定位。") from None
    return home / ".codex"
