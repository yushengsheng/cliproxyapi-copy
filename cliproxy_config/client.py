"""Select a client application; locate per-user settings independently of its install path."""
from __future__ import annotations

import json
import base64
import ntpath
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile

from dataclasses import dataclass

from .config import read_bytes
from .crypto import ConfigError


@dataclass(frozen=True)
class ClientApplication:
    path: Path
    name: str
    icon: bytes | None = None


def _mac_info(path: Path) -> dict:
    try:
        return plistlib.loads(read_bytes(path / "Contents" / "Info.plist", 65536))
    except (ConfigError, OSError, ValueError):
        return {}


def _powershell(script: str) -> str:
    command = ntpath.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    result = subprocess.run([command, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                            capture_output=True, timeout=8, check=True)
    return result.stdout.decode("utf-8-sig").strip()


def application_icon(path: Path, info: dict) -> bytes | None:
    if sys.platform == "win32" and path.suffix.lower() == ".exe":
        literal = str(path).replace("'", "''")
        script = ("Add-Type -AssemblyName System.Drawing;"
                  "$icon=[System.Drawing.Icon]::ExtractAssociatedIcon('" + literal + "');"
                  "if($icon){$original=$icon.ToBitmap();$bitmap=New-Object System.Drawing.Bitmap($original,56,56);"
                  "$stream=New-Object System.IO.MemoryStream;$bitmap.Save($stream,[System.Drawing.Imaging.ImageFormat]::Png);"
                  "[Console]::Write([Convert]::ToBase64String($stream.ToArray()));"
                  "$stream.Dispose();$bitmap.Dispose();$original.Dispose();$icon.Dispose()}")
        try:
            value = _powershell(script)
            return base64.b64decode(value, validate=True) if value else None
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
    if path.suffix.lower() != ".app":
        return None
    name = info.get("CFBundleIconFile")
    if not isinstance(name, str) or not name or Path(name).name != name:
        return None
    icon = path / "Contents" / "Resources" / name
    if not icon.suffix:
        icon = icon.with_suffix(".icns")
    if not icon.is_file():
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="cliproxy-icon-") as temp:
            output = Path(temp) / "icon.png"
            subprocess.run(["/usr/bin/sips", "-s", "format", "png", "-z", "56", "56", str(icon), "--out", str(output)],
                           capture_output=True, timeout=5, check=True)
            return output.read_bytes()
    except (OSError, subprocess.SubprocessError):
        return None


def application_list() -> list[ClientApplication]:
    rows = []
    for path in installed_clients():
        info = _mac_info(path) if path.suffix.lower() == ".app" else {}
        if path.suffix.lower() == ".app" and info.get("CFBundleIdentifier") != "com.openai.codex":
            continue
        name = info.get("CFBundleDisplayName") or info.get("CFBundleName") or path.stem
        rows.append(ClientApplication(path, str(name), application_icon(path, info)))
    return rows


def installed_clients() -> list[Path]:
    if sys.platform == "darwin":
        candidates = []
        for folder in (Path("/Applications"), Path.home() / "Applications"):
            if not folder.is_dir():
                continue
            # Application folders only; do not traverse user files or app bundle contents.
            try:
                for entry in folder.iterdir():
                    if entry.suffix.lower() == ".app":
                        if _mac_info(entry).get("CFBundleIdentifier") == "com.openai.codex":
                            candidates.append(entry)
                    elif entry.is_dir() and not entry.name.startswith("."):
                        for child in entry.glob("*.app"):
                            if _mac_info(child).get("CFBundleIdentifier") == "com.openai.codex":
                                candidates.append(child)
            except OSError:
                continue
    elif sys.platform == "win32":
        candidates = []
        for variable in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
            if os.environ.get(variable):
                base = Path(os.environ[variable])
                for name in ("ChatGPT", "Codex"):
                    for parent in (base, base / "Programs", base / "OpenAI"):
                        candidates.append(parent / name / (name + ".exe"))
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for name in ("ChatGPT.exe", "Codex.exe"):
                try:
                    with winreg.OpenKey(hive, "Software\\Microsoft\\Windows\\CurrentVersion\\App Paths\\" + name) as key:
                        candidates.append(Path(winreg.QueryValueEx(key, "")[0].strip('"')))
                except OSError:
                    pass
        try:
            script = ("[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false);"
                      "@(Get-AppxPackage -Name '*Codex*';Get-AppxPackage -Name '*ChatGPT*')|ForEach-Object {"
                      "$package=$_;$manifest=Get-AppxPackageManifest -Package $package;"
                      "$manifest.Package.Applications.Application|ForEach-Object {"
                      "if($_.Executable){[Console]::WriteLine((Join-Path $package.InstallLocation $_.Executable))}}}")
            candidates.extend(Path(line) for line in _powershell(script).splitlines() if line.lower().endswith(("chatgpt.exe", "codex.exe")))
        except (OSError, UnicodeError, subprocess.SubprocessError):
            pass
    else:
        candidates = []
    return list(dict.fromkeys(path for path in candidates if path.exists()))


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


def resolve_client(application: Path, home: Path | None = None, environment: dict | None = None) -> tuple[Path, Path]:
    try:
        application = application.expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        raise ConfigError("所选客户端不存在或路径不可访问。") from None
    if application.suffix.lower() == ".app" and application.is_dir():
        try:
            info = plistlib.loads(read_bytes(application / "Contents" / "Info.plist", 65536))
        except (OSError, ValueError):
            raise ConfigError("无法识别所选应用，请选择 GPT / Codex 桌面客户端。") from None
        if info.get("CFBundleIdentifier") != "com.openai.codex":
            raise ConfigError("所选应用不使用本工具支持的 GPT / Codex 配置，无法应用这条 API 线路。")
    elif application.is_file() and application.name.lower() in ("chatgpt.exe", "codex.exe"):
        pass
    else:
        raise ConfigError("请选择 GPT / Codex 桌面客户端的 .app 或 .exe 文件。")
    return application, configuration_directory(home, environment)

